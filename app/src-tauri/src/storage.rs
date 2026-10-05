// Где хранить движок и модели (0.3.3): выбранная папка вместо системного
// диска — на C: у многих тесно, на Mac — внешний диск.
//
// Выбор лежит в `<data_dir>\storage.json` (`{"root": "E:\\Meet"}`): оболочке
// надо знать, где движок, до запуска резидента, а резидент (Python,
// `meet.paths.storage_root`) читает тот же файл — одна правда на оба
// процесса. Нет файла — всё как до 0.3.3: движок и GigaAM в папке данных,
// модели Hugging Face — в общем кэше. Раскладка выбранной папки:
//
//     <папка>\engine\<версия>   окружение движка (ставится заново: venv на
//                               Windows не переносится — в скриптах пути)
//     <папка>\models\hf         свой кэш HF: только модели каталога Meet
//     <папка>\models\gigaam     веса GigaAM
//     <папка>\models\xet        кэш кусков загрузчика Xet
//
// Перенос (`storage_move`) идёт по шагам журнала `storage-move.json`:
//
//   engine     движок этой версии ставится в новую папку (тот же установщик,
//              что у мастера; резидент работает из прежней);
//   models     модели Meet копируются и сверяются (`python -m meet.storage
//              copy` — Python уже нового движка);
//   switching  ждём, пока не идёт запись, ассистент, загрузки и задачи;
//              пишем `storage.json` и перезапускаем резидент из новой папки;
//   cleanup    резидент из новой папки ответил — прежние движок и модели Meet
//              удаляются (общий кэш HF — только с согласия, вопрос в окне).
//
// Правило восстановления после сбоя (оболочку убили, компьютер выключился):
// до `cleanup` — ОТКАТ, после — ДОВЕДЕНИЕ. Пока резидент из новой папки не
// ответил, прежняя папка цела и рабочая: вернуть на неё `storage.json` и
// удалить недоделанное в новой — дёшево и безопасно, а «довести» значило бы
// продолжать многоминутную установку при старте, без окна и без согласия.
// После ответа нового резидента прежняя папка больше не нужна; осталось только
// удалить её — это и доводится при следующем запуске (идемпотентно).
//
// Удаляется только своё: в выбранной папке — `engine` и наши папки в
// `models`; чужая непустая папка не выбирается (кладём в её подпапку `Meet`),
// своя помечена файлом `.meet-storage`.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

use crate::api;
use crate::engine;
use crate::logs::shell_log;
use crate::resident::{self, ResidentStatus, Supervisor};

/// Выбранная папка движка и моделей (читает и Python, `meet.paths`).
pub const POINTER: &str = "storage.json";
/// Журнал идущего переноса (Python по нему не качает модели посреди переноса).
pub const JOURNAL: &str = "storage-move.json";
/// После переезда из общего кэша HF: окно спросит, удалить ли модели Meet из
/// него (ответ и удаление — резидент, `meet.storage.answer_leftovers`).
pub const LEFTOVERS: &str = "storage-leftovers.json";
/// Метка папки движка и моделей: в помеченной папке всё наше.
pub const MARK: &str = ".meet-storage";
/// Подпапка в выбранной непустой чужой папке.
const NEST: &str = "Meet";
const ENGINE: &str = "engine";
const MODELS: &str = "models";
/// Наши папки в `models`.
const OWN_MODELS: [&str; 3] = ["gigaam", "hf", "xet"];

/// Событие хода переноса: `{phase, text, done, total}`.
pub const PROGRESS_EVENT: &str = "storage-progress";
/// Заголовок уведомления «папки нет» (резидент не запускается).
pub const MISSING_TITLE: &str = "Папка движка и моделей недоступна";
pub const CANCELLED: &str = "Перенос отменён — движок и модели остались на прежнем месте";
const NOT_RUNNING: &str = "Служба записи не запущена — перенос возможен, когда она работает";
/// Запас места сверх движка и моделей, ГБ.
const MARGIN_GB: f64 = 0.5;
/// Сколько ждём, что резидент поднимется из новой папки (первый импорт torch
/// с холодного диска бывает долгим).
const START_TIMEOUT: Duration = Duration::from_secs(180);
const IDLE_POLL: Duration = Duration::from_secs(2);
const POLL: Duration = Duration::from_millis(250);

// --- выбранная папка ------------------------------------------------------------

/// Выбранная папка из `storage.json`; нет файла, он битый, путь пустой или
/// относительный — `None` (как `meet.paths.storage_root`).
pub fn root(data_dir: &Path) -> Option<PathBuf> {
    let raw = fs::read_to_string(data_dir.join(POINTER)).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let text = value.get("root")?.as_str()?.trim();
    let path = PathBuf::from(text);
    (!text.is_empty() && path.is_absolute()).then_some(path)
}

/// Папка, в которой лежат `engine` и `models`.
pub fn home(data_dir: &Path) -> PathBuf {
    root(data_dir).unwrap_or_else(|| data_dir.to_path_buf())
}

/// Выбранная папка, которой нет (внешний диск отключён), или `None`.
pub fn missing(data_dir: &Path) -> Option<PathBuf> {
    root(data_dir).filter(|root| !root.is_dir())
}

fn write_atomic(path: &Path, text: &str) -> io::Result<()> {
    let staged = path.with_extension("json.tmp");
    fs::write(&staged, text)?;
    fs::rename(&staged, path)
}

fn remove_file(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error),
        _ => Ok(()),
    }
}

/// Записать выбор (`None` — по умолчанию: файла нет).
fn write_pointer(data_dir: &Path, root: Option<&Path>) -> io::Result<()> {
    let path = data_dir.join(POINTER);
    match root {
        None => remove_file(&path),
        Some(root) => {
            let text = serde_json::json!({ "root": root.to_string_lossy() }).to_string();
            write_atomic(&path, &text)
        }
    }
}

// --- сравнение путей ------------------------------------------------------------

/// Путь для сравнения: настоящий путь ближайшей существующей папки плюс
/// остаток; на Windows — без учёта регистра.
fn norm(path: &Path) -> PathBuf {
    let mut rest = Vec::new();
    let mut current = path;
    loop {
        if let Ok(real) = fs::canonicalize(current) {
            let mut out = real;
            for part in rest.iter().rev() {
                out.push(part);
            }
            return fold(out);
        }
        match (current.parent(), current.file_name()) {
            (Some(parent), Some(name)) => {
                rest.push(name.to_os_string());
                current = parent;
            }
            _ => return fold(path.to_path_buf()),
        }
    }
}

#[cfg(windows)]
fn fold(path: PathBuf) -> PathBuf {
    PathBuf::from(path.to_string_lossy().to_lowercase())
}

#[cfg(not(windows))]
fn fold(path: PathBuf) -> PathBuf {
    path
}

pub fn same_path(a: &Path, b: &Path) -> bool {
    norm(a) == norm(b)
}

/// `child` лежит внутри `parent` (и не совпадает с ним).
pub fn inside(child: &Path, parent: &Path) -> bool {
    let (child, parent) = (norm(child), norm(parent));
    child != parent && child.starts_with(&parent)
}

// --- куда переносить ------------------------------------------------------------

fn empty_or_absent(dir: &Path) -> bool {
    match fs::read_dir(dir) {
        Ok(mut entries) => entries.next().is_none(),
        Err(error) => error.kind() == io::ErrorKind::NotFound,
    }
}

/// Папка движка и моделей для выбранной человеком: пустая, несуществующая или
/// уже наша (метка) — она сама; папка данных — она сама; иначе — подпапка
/// `Meet`: чужие файлы не перемешиваются с нашими, и откат не заденет их.
pub fn resolve_target(picked: &Path, data_dir: &Path) -> PathBuf {
    if same_path(picked, data_dir) {
        return data_dir.to_path_buf();
    }
    if picked.join(MARK).is_file() || empty_or_absent(picked) {
        return picked.to_path_buf();
    }
    let nested = picked.join(NEST);
    if same_path(&nested, data_dir) {
        data_dir.to_path_buf()
    } else {
        nested
    }
}

/// Годится ли `target` (после `resolve_target`); нет — текст для человека.
pub fn check_target(target: &Path, current: &Path, data_dir: &Path) -> Result<(), String> {
    if !target.is_absolute() {
        return Err("Укажите полный путь к папке".into());
    }
    if same_path(target, current) {
        return Err("Движок и модели уже в этой папке".into());
    }
    if inside(target, current) || inside(current, target) {
        return Err("Выберите папку вне нынешней папки движка и моделей".into());
    }
    if inside(target, data_dir) {
        return Err("Выберите папку вне папки данных Meet".into());
    }
    if target.exists() && !target.is_dir() {
        return Err("Это не папка".into());
    }
    if !target.exists() && !target.parent().is_some_and(Path::is_dir) {
        return Err("Папка недоступна — диск отключён?".into());
    }
    if !same_path(target, data_dir) && !target.join(MARK).is_file() && !empty_or_absent(target) {
        return Err(format!(
            "Папка {} не пуста — выберите пустую папку",
            target.display()
        ));
    }
    Ok(())
}

/// Места нужно, ГБ: движок, модели и запас.
pub fn needs_gb(engine_gb: f64, models_bytes: u64) -> f64 {
    engine_gb + models_bytes as f64 / f64::from(1u32 << 30) + MARGIN_GB
}

// --- журнал и восстановление ----------------------------------------------------

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Engine,
    Models,
    Switching,
    Cleanup,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Journal {
    /// Прежняя выбранная папка; `None` — по умолчанию (папка данных и общий кэш).
    pub from: Option<PathBuf>,
    pub to: PathBuf,
    pub phase: Phase,
}

pub fn read_journal(data_dir: &Path) -> Option<Journal> {
    serde_json::from_str(&fs::read_to_string(data_dir.join(JOURNAL)).ok()?).ok()
}

fn write_journal(data_dir: &Path, journal: &Journal) -> Result<(), String> {
    let text = serde_json::to_string_pretty(journal).unwrap_or_default();
    write_atomic(&data_dir.join(JOURNAL), &text)
        .map_err(|error| format!("Не удалось записать журнал переноса: {error}"))
}

/// Что делать с прерванным переносом при старте.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Recovery {
    Nothing,
    /// До ответа нового резидента: вернуть прежнюю папку, удалить недоделанное.
    RollBack,
    /// Новый резидент отвечал: удалить прежнее.
    Finish,
}

pub fn recovery(phase: Option<Phase>) -> Recovery {
    match phase {
        None => Recovery::Nothing,
        Some(Phase::Cleanup) => Recovery::Finish,
        Some(_) => Recovery::RollBack,
    }
}

fn remove_tree(path: &Path, errors: &mut Vec<String>) {
    match fs::remove_dir_all(path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => errors.push(format!("{}: {error}", path.display())),
    }
}

/// Убрать из папки `home` наше: движок и модели; `whole` — ещё папки свои
/// (`hf`, `xet`, метку, пустые папки). В папке данных — только движок и
/// модели: журналы, настройки и записи там же.
fn remove_ours(home: &Path, whole: bool, data_dir: &Path) -> Vec<String> {
    let mut errors = Vec::new();
    remove_tree(&home.join(ENGINE), &mut errors);
    let models = home.join(MODELS);
    for name in OWN_MODELS {
        if whole || name == "gigaam" {
            remove_tree(&models.join(name), &mut errors);
        }
    }
    let _ = fs::remove_dir(&models); // только пустую
    if whole && !same_path(home, data_dir) {
        let _ = remove_file(&home.join(MARK));
        let _ = fs::remove_dir(home); // только пустую: чужие файлы — его
    }
    errors
}

/// Откатить перенос: прежняя папка — снова выбранная, недоделанное в новой
/// удаляется. Идемпотентно.
pub fn roll_back(data_dir: &Path, journal: &Journal) -> Result<(), String> {
    write_pointer(data_dir, journal.from.as_deref())
        .map_err(|error| format!("Не удалось вернуть прежнюю папку: {error}"))?;
    if journal.from.is_none() {
        let _ = remove_file(&data_dir.join(LEFTOVERS));
    }
    discard(data_dir, journal)
}

/// Удалить недоделанное в новой папке и журнал (выбор уже возвращён).
fn discard(data_dir: &Path, journal: &Journal) -> Result<(), String> {
    let from = journal
        .from
        .clone()
        .unwrap_or_else(|| data_dir.to_path_buf());
    // Новая папка — не та, из которой всё работает (иначе удалили бы живое).
    if !same_path(&journal.to, &from) && !same_path(&journal.to, &home(data_dir)) {
        let errors = remove_ours(&journal.to, true, data_dir);
        if !errors.is_empty() {
            return Err(errors.join("; "));
        }
    }
    remove_file(&data_dir.join(JOURNAL)).map_err(|error| error.to_string())
}

/// Довести перенос: удалить прежние движок и модели Meet (общий кэш HF не
/// трогаем — о нём спросит окно). Не удалилось (файлы заняты) — журнал
/// остаётся, следующий запуск попробует снова.
pub fn finish(data_dir: &Path, journal: &Journal) -> Result<(), String> {
    let from = journal
        .from
        .clone()
        .unwrap_or_else(|| data_dir.to_path_buf());
    if !same_path(&from, &journal.to) && !same_path(&from, &home(data_dir)) {
        // Прежней папки нет (диск отключён) — удалять нечего и некуда.
        if from.is_dir() {
            let errors = remove_ours(&from, journal.from.is_some(), data_dir);
            if !errors.is_empty() {
                return Err(errors.join("; "));
            }
        } else {
            shell_log!(
                "прежняя папка движка и моделей недоступна ({}) — удалите её вручную",
                from.display()
            );
        }
    }
    remove_file(&data_dir.join(JOURNAL)).map_err(|error| error.to_string())
}

/// При старте оболочки, до надзора и обслуживания движка: прерванный перенос
/// откатить или довести (правило — в шапке файла). Выбор возвращается сразу,
/// удаление гигабайт — в своём потоке.
pub fn recover_at_startup() {
    let data = resident::data_dir();
    let Some(journal) = read_journal(&data) else {
        return;
    };
    match recovery(Some(journal.phase)) {
        Recovery::Nothing => {}
        Recovery::RollBack => {
            shell_log!(
                "перенос движка и моделей прерван на шаге {:?} — откатываю, всё остаётся на прежнем месте",
                journal.phase
            );
            if let Err(error) = write_pointer(&data, journal.from.as_deref()) {
                shell_log!("не удалось вернуть прежнюю папку движка: {error}");
                return;
            }
            if journal.from.is_none() {
                let _ = remove_file(&data.join(LEFTOVERS));
            }
            in_background(move || match discard(&data, &journal) {
                Ok(()) => shell_log!("откат переноса закончен"),
                Err(error) => shell_log!("откат переноса: не всё удалилось: {error}"),
            });
        }
        Recovery::Finish => {
            shell_log!("перенос движка и моделей закончен — удаляю прежнюю папку");
            in_background(move || match finish(&data, &journal) {
                Ok(()) => shell_log!("прежние движок и модели удалены"),
                Err(error) => shell_log!("прежние движок и модели удалены не все: {error}"),
            });
        }
    }
}

fn in_background(work: impl FnOnce() + Send + 'static) {
    if let Err(error) = thread::Builder::new()
        .name("meet-storage-cleanup".into())
        .spawn(work)
    {
        shell_log!("поток уборки после переноса не запустился: {error}");
    }
}

// --- строки копирования моделей -------------------------------------------------

#[derive(Debug, PartialEq)]
pub enum CopyLine {
    Progress { done: u64, total: u64 },
    Done,
    Error(String),
    Other,
}

/// Строка `python -m meet.storage copy`: JSON с ходом, итогом или ошибкой.
pub fn parse_copy_line(line: &str) -> CopyLine {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(line.trim()) else {
        return CopyLine::Other;
    };
    if let Some(error) = value.get("error").and_then(serde_json::Value::as_str) {
        return CopyLine::Error(error.to_string());
    }
    if value.get("ok").and_then(serde_json::Value::as_bool) == Some(true) {
        return CopyLine::Done;
    }
    match (
        value.get("done").and_then(serde_json::Value::as_u64),
        value.get("total").and_then(serde_json::Value::as_u64),
    ) {
        (Some(done), Some(total)) => CopyLine::Progress { done, total },
        _ => CopyLine::Other,
    }
}

// --- перенос --------------------------------------------------------------------

static MOVING: AtomicBool = AtomicBool::new(false);
/// «Отменить» ещё можно (до переключения).
static CANCELLABLE: AtomicBool = AtomicBool::new(false);
static CANCEL: AtomicBool = AtomicBool::new(false);

struct Moving;

impl Moving {
    fn begin() -> Result<Moving, String> {
        MOVING
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .map_err(|_| "Перенос уже идёт".to_string())?;
        CANCEL.store(false, Ordering::SeqCst);
        CANCELLABLE.store(true, Ordering::SeqCst);
        Ok(Moving)
    }
}

impl Drop for Moving {
    fn drop(&mut self) {
        CANCELLABLE.store(false, Ordering::SeqCst);
        MOVING.store(false, Ordering::SeqCst);
    }
}

fn cancelled() -> Result<(), String> {
    if CANCEL.load(Ordering::SeqCst) {
        Err(CANCELLED.into())
    } else {
        Ok(())
    }
}

#[derive(Serialize, Clone)]
struct Progress {
    phase: &'static str,
    text: String,
    done: u64,
    total: u64,
}

fn emit(app: &AppHandle, phase: &'static str, text: impl Into<String>, done: u64, total: u64) {
    let _ = app.emit(
        PROGRESS_EVENT,
        Progress {
            phase,
            text: text.into(),
            done,
            total,
        },
    );
}

/// Что резидент говорит о переносе (`GET /storage`).
struct ResidentInfo {
    busy: Option<String>,
    models_bytes: u64,
}

fn resident_info(app: &AppHandle) -> Result<ResidentInfo, String> {
    if app.state::<Supervisor>().status() != ResidentStatus::Running {
        return Err(NOT_RUNNING.into());
    }
    let endpoint = resident::read_endpoint().ok_or(NOT_RUNNING)?;
    let value = api::Client::new(&endpoint)
        .get("/storage")
        .map_err(|error| format!("Служба записи не ответила: {error}"))?;
    Ok(ResidentInfo {
        busy: value
            .get("busy")
            .and_then(serde_json::Value::as_str)
            .map(String::from),
        models_bytes: value
            .get("models_bytes")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0),
    })
}

/// Проверка перед переносом — для окна.
#[derive(Serialize, Clone, Debug)]
pub struct StorageCheck {
    /// Куда на самом деле (выбранная папка или её подпапка `Meet`).
    pub target: String,
    pub free_gb: Option<f64>,
    pub needs_gb: f64,
    pub engine_gb: f64,
    pub models_gb: f64,
    /// Почему сейчас нельзя (идёт запись…); `None` — можно.
    pub busy: Option<String>,
    /// Места не хватает; `None` — хватает или узнать нельзя.
    pub error: Option<String>,
}

struct Plan {
    target: PathBuf,
    profile: String,
    check: StorageCheck,
}

fn plan(app: &AppHandle, picked: &Path) -> Result<Plan, String> {
    let data = resident::data_dir();
    let current = home(&data);
    let target = resolve_target(picked, &data);
    check_target(&target, &current, &data)?;
    let version = app.package_info().version.to_string();
    let profile = engine::installed_profile(&engine::env_dir(&current, &version), &version)
        .ok_or("Движок не установлен — перенос возможен после установки")?;
    let info = resident_info(app)?;
    let engine_gb = engine::space_for(&target, &profile);
    let models_gb = info.models_bytes as f64 / f64::from(1u32 << 30);
    let needs = needs_gb(engine_gb, info.models_bytes);
    let free = engine::free_gb(&target);
    let error = free.and_then(|free| engine::space_error(needs, free));
    Ok(Plan {
        check: StorageCheck {
            target: target.to_string_lossy().into_owned(),
            free_gb: free.map(|gb| (gb * 10.0).floor() / 10.0),
            needs_gb: (needs * 10.0).ceil() / 10.0,
            engine_gb,
            models_gb: (models_gb * 10.0).ceil() / 10.0,
            busy: info.busy,
            error,
        },
        target,
        profile,
    })
}

fn run_move(app: &AppHandle, picked: &Path) -> Result<String, String> {
    let _install = engine::begin_install()?;
    let _moving = Moving::begin()?;
    let data = resident::data_dir();
    if let Some(journal) = read_journal(&data) {
        if journal.phase != Phase::Cleanup {
            return Err("Прерванный перенос ещё убирается — повторите через минуту".into());
        }
        finish(&data, &journal).map_err(|error| {
            format!("Прежняя папка движка ещё не удалена ({error}) — перезапустите компьютер и повторите")
        })?;
    }
    let plan = plan(app, picked)?;
    if let Some(busy) = plan.check.busy {
        return Err(format!(
            "Перенести сейчас нельзя: {busy}. Повторите, когда закончится"
        ));
    }
    if let Some(error) = plan.check.error {
        return Err(error);
    }
    let target = plan.target;
    fs::create_dir_all(&target)
        .map_err(|error| format!("Не удалось создать папку {}: {error}", target.display()))?;
    if !same_path(&target, &data) {
        // Метка — она же проверка, что в папку можно писать.
        fs::write(target.join(MARK), "Meet: движок и модели\n")
            .map_err(|error| format!("В папку {} нельзя записать: {error}", target.display()))?;
    }
    let mut journal = Journal {
        from: root(&data),
        to: target.clone(),
        phase: Phase::Engine,
    };
    write_journal(&data, &journal)?;
    shell_log!(
        "перенос движка и моделей: {} -> {}",
        home(&data).display(),
        target.display()
    );
    match steps(app, &data, &mut journal, &plan.profile) {
        Ok(()) => {
            shell_log!("перенос движка и моделей закончен: {}", target.display());
            Ok(target.to_string_lossy().into_owned())
        }
        Err(error) => {
            let error = if CANCEL.load(Ordering::SeqCst) && journal.phase != Phase::Cleanup {
                CANCELLED.to_string()
            } else {
                error
            };
            shell_log!("перенос движка и моделей не удался: {error}");
            if journal.phase != Phase::Cleanup {
                emit(app, "rollback", "Возвращаю всё на прежнее место", 0, 0);
                if let Err(rollback) = roll_back(&data, &journal) {
                    shell_log!("откат переноса: {rollback} — доделаю при следующем запуске");
                }
            }
            Err(error)
        }
    }
}

fn steps(app: &AppHandle, data: &Path, journal: &mut Journal, profile: &str) -> Result<(), String> {
    let version = app.package_info().version.to_string();
    emit(app, "engine", "Установка движка в новую папку", 0, 0);
    engine::install_into(app, &journal.to, profile)?;
    cancelled()?;

    journal.phase = Phase::Models;
    write_journal(data, journal)?;
    emit(app, "models", "Копирование моделей", 0, 0);
    copy_models(app, data, &journal.to, &version)?;
    cancelled()?;

    journal.phase = Phase::Switching;
    write_journal(data, journal)?;
    wait_idle(app)?;
    CANCELLABLE.store(false, Ordering::SeqCst);
    emit(
        app,
        "switching",
        "Перезапуск службы записи из новой папки",
        0,
        0,
    );
    switch(app, data, journal, &version)?;

    journal.phase = Phase::Cleanup;
    write_journal(data, journal)?;
    emit(
        app,
        "cleanup",
        "Удаление движка и моделей из прежней папки",
        0,
        0,
    );
    if let Err(error) = finish(data, journal) {
        // Не сбой переноса: всё уже работает из новой папки.
        shell_log!(
            "прежние движок и модели удалены не все: {error} — повторю при следующем запуске"
        );
    }
    Ok(())
}

fn copy_models(app: &AppHandle, data: &Path, to: &Path, version: &str) -> Result<(), String> {
    let python = engine::python_of(&engine::env_dir(to, version));
    let argv: Vec<String> = vec![
        python.to_string_lossy().into_owned(),
        "-m".into(),
        "meet.storage".into(),
        "copy".into(),
        "--to".into(),
        to.to_string_lossy().into_owned(),
    ];
    let envs: [(&str, std::ffi::OsString); 3] = [
        ("PYTHONIOENCODING", "utf-8".into()),
        ("PYTHONUTF8", "1".into()),
        ("PYTHONUNBUFFERED", "1".into()),
    ];
    let mut finished = false;
    let mut failure: Option<String> = None;
    let code = engine::run_streamed(&argv, &envs, data, |line| match parse_copy_line(&line) {
        CopyLine::Progress { done, total } => {
            emit(app, "models", "Копирование моделей", done, total)
        }
        CopyLine::Done => finished = true,
        CopyLine::Error(text) => failure = Some(text),
        CopyLine::Other => shell_log!("копирование моделей: {line}"),
    })
    .map_err(|error| format!("Копирование моделей не запустилось: {error}"))?;
    match (code, finished) {
        (Some(0), true) => Ok(()),
        _ => Err(failure.unwrap_or_else(|| "Копирование моделей прервалось".into())),
    }
}

/// Ждать, пока резидент не занят (запись, ассистент, загрузки, задачи).
fn wait_idle(app: &AppHandle) -> Result<(), String> {
    loop {
        cancelled()?;
        match resident_info(app)?.busy {
            None => return Ok(()),
            Some(reason) => {
                emit(
                    app,
                    "waiting",
                    format!("Жду, пока закончится: {reason}"),
                    0,
                    0,
                );
                thread::sleep(IDLE_POLL);
            }
        }
    }
}

/// Переключить выбор и поднять резидент из новой папки. Не поднялся — выбор
/// возвращается, резидент — из прежней папки, ошибка (новую папку удалит
/// откат).
fn switch(app: &AppHandle, data: &Path, journal: &Journal, version: &str) -> Result<(), String> {
    write_pointer(data, Some(&journal.to))
        .map_err(|error| format!("Не удалось записать выбор папки: {error}"))?;
    if journal.from.is_none() {
        // Из общего кэша HF переехали — окно спросит, удалить ли там модели Meet.
        let _ = write_atomic(&data.join(LEFTOVERS), "{}");
    }
    let supervisor = app.state::<Supervisor>();
    supervisor.respawn(app);
    let env = engine::env_dir(&journal.to, version);
    if started_from(&supervisor, &env) {
        return Ok(());
    }
    shell_log!("резидент из новой папки не поднялся — возвращаю прежнюю");
    let _ = write_pointer(data, journal.from.as_deref());
    if journal.from.is_none() {
        let _ = remove_file(&data.join(LEFTOVERS));
    }
    supervisor.respawn(app);
    Err(
        "Служба записи не запустилась из новой папки — движок и модели остались на прежнем \
         месте. Подробности — в журнале"
            .into(),
    )
}

fn started_from(supervisor: &Supervisor, env: &Path) -> bool {
    let deadline = Instant::now() + START_TIMEOUT;
    while Instant::now() < deadline {
        match supervisor.status() {
            ResidentStatus::Running => {
                return supervisor
                    .running_from()
                    .is_some_and(|from| from.starts_with(env));
            }
            ResidentStatus::Failed { .. }
            | ResidentStatus::EngineMissing
            | ResidentStatus::StorageMissing { .. }
            | ResidentStatus::Quitting => return false,
            _ => thread::sleep(POLL),
        }
    }
    false
}

// --- команды окна ---------------------------------------------------------------

/// Где сейчас движок и модели.
#[derive(Serialize, Clone, Debug)]
pub struct StorageStatus {
    /// Выбранная папка; `None` — по умолчанию.
    pub root: Option<String>,
    pub home: String,
    /// Папка по умолчанию (системный диск): папка данных.
    pub default_home: String,
    /// Выбранной папки нет (диск отключён).
    pub missing: Option<String>,
    /// Перенос идёт (или его журнал ещё не убран).
    pub moving: bool,
    /// Отменить ещё можно.
    pub cancellable: bool,
}

#[tauri::command]
pub fn storage_status() -> StorageStatus {
    let data = resident::data_dir();
    StorageStatus {
        root: root(&data).map(|root| root.to_string_lossy().into_owned()),
        home: home(&data).to_string_lossy().into_owned(),
        default_home: data.to_string_lossy().into_owned(),
        missing: missing(&data).map(|path| path.to_string_lossy().into_owned()),
        moving: MOVING.load(Ordering::SeqCst)
            || read_journal(&data).is_some_and(|journal| journal.phase != Phase::Cleanup),
        cancellable: CANCELLABLE.load(Ordering::SeqCst),
    }
}

/// Сколько места нужно и можно ли переносить в `target` прямо сейчас.
#[tauri::command]
pub async fn storage_check(app: AppHandle, target: String) -> Result<StorageCheck, String> {
    tauri::async_runtime::spawn_blocking(move || {
        plan(&app, Path::new(target.trim())).map(|plan| plan.check)
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Перенести движок и модели в `target`. Ответ — по окончании (минуты):
/// путь, куда перенесено; ход — событиями `storage-progress`.
#[tauri::command]
pub async fn storage_move(app: AppHandle, target: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_move(&app, Path::new(target.trim())))
        .await
        .map_err(|error| error.to_string())?
}

/// «Отменить» перенос (до переключения): гасит установку или копирование;
/// `storage_move` ответит `CANCELLED` после отката.
#[tauri::command]
pub fn storage_cancel() {
    if MOVING.load(Ordering::SeqCst) && CANCELLABLE.load(Ordering::SeqCst) {
        shell_log!("перенос движка и моделей отменён пользователем");
        CANCEL.store(true, Ordering::SeqCst);
        engine::cancel_installs();
    }
}

/// «Вернуть на системный диск», когда выбранной папки нет: выбор снимается
/// (движок и модели — снова в папке данных и общем кэше HF), резидент
/// перезапускается; движка там нет — окно предложит установить.
#[tauri::command]
pub async fn storage_reset(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let data = resident::data_dir();
        let Some(path) = missing(&data) else {
            return Err("Папка движка и моделей на месте — возвращать нечего".to_string());
        };
        write_pointer(&data, None)
            .map_err(|error| format!("Не удалось снять выбор папки: {error}"))?;
        let _ = remove_file(&data.join(LEFTOVERS));
        shell_log!(
            "папка движка и моделей {} недоступна — возвращаю на системный диск",
            path.display()
        );
        app.state::<Supervisor>().respawn(&app);
        Ok(())
    })
    .await
    .map_err(|error| error.to_string())?
}

/// «Повторить» после подключения диска: надзор заново проверит папку.
#[tauri::command]
pub async fn storage_retry(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || app.state::<Supervisor>().respawn(&app))
        .await
        .map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Временная папка теста; удаляется в конце.
    struct Temp(PathBuf);

    impl Temp {
        fn new(name: &str) -> Self {
            let dir = std::env::temp_dir()
                .join(format!("meet-storage-test-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            Temp(dir)
        }

        fn file(&self, relative: &str) -> PathBuf {
            let path = self.0.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, "x").unwrap();
            path
        }
    }

    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn without_choice_home_is_the_data_dir() {
        let t = Temp::new("default");
        assert_eq!(root(&t.0), None);
        assert_eq!(home(&t.0), t.0);
        assert_eq!(missing(&t.0), None);
    }

    #[test]
    fn choice_is_written_and_read_back() {
        let t = Temp::new("pointer");
        let chosen = t.0.join("E").join("Meet");
        fs::create_dir_all(&chosen).unwrap();
        write_pointer(&t.0, Some(&chosen)).unwrap();
        assert_eq!(root(&t.0), Some(chosen.clone()));
        assert_eq!(home(&t.0), chosen);
        assert_eq!(
            engine::env_dir(&home(&t.0), "0.3.3"),
            chosen.join("engine").join("0.3.3")
        );
        write_pointer(&t.0, None).unwrap();
        assert_eq!(root(&t.0), None);
        write_pointer(&t.0, None).unwrap(); // повтор — не ошибка
    }

    #[test]
    fn broken_choice_means_default() {
        let t = Temp::new("broken");
        for raw in [
            "",
            "{",
            "[]",
            r#"{"root": ""}"#,
            r#"{"root": 5}"#,
            r#"{"root": "rel/path"}"#,
        ] {
            fs::write(t.0.join(POINTER), raw).unwrap();
            assert_eq!(root(&t.0), None, "{raw}");
        }
    }

    #[test]
    fn unplugged_folder_is_missing_and_not_created() {
        let t = Temp::new("missing");
        let gone = t.0.join("unplugged").join("Meet");
        write_pointer(&t.0, Some(&gone)).unwrap();
        assert_eq!(missing(&t.0), Some(gone.clone()));
        assert_eq!(home(&t.0), gone);
        assert!(!gone.exists());
    }

    #[test]
    fn python_reads_the_same_file_format() {
        let t = Temp::new("format");
        let chosen = t.0.join("Meet");
        write_pointer(&t.0, Some(&chosen)).unwrap();
        let raw: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(t.0.join(POINTER)).unwrap()).unwrap();
        assert_eq!(raw["root"], chosen.to_string_lossy().as_ref());
    }

    #[test]
    fn crash_before_the_new_resident_answered_rolls_back_after_it_finishes() {
        assert_eq!(recovery(None), Recovery::Nothing);
        assert_eq!(recovery(Some(Phase::Engine)), Recovery::RollBack);
        assert_eq!(recovery(Some(Phase::Models)), Recovery::RollBack);
        assert_eq!(recovery(Some(Phase::Switching)), Recovery::RollBack);
        assert_eq!(recovery(Some(Phase::Cleanup)), Recovery::Finish);
    }

    #[test]
    fn journal_round_trips() {
        let t = Temp::new("journal");
        let journal = Journal {
            from: None,
            to: t.0.join("Meet"),
            phase: Phase::Models,
        };
        write_journal(&t.0, &journal).unwrap();
        assert_eq!(read_journal(&t.0), Some(journal));
        let raw = fs::read_to_string(t.0.join(JOURNAL)).unwrap();
        assert!(raw.contains("\"models\""), "{raw}");
    }

    #[test]
    fn target_is_the_folder_itself_when_empty_or_ours() {
        let t = Temp::new("target");
        let data = t.0.join("data");
        fs::create_dir_all(&data).unwrap();
        let empty = t.0.join("empty");
        fs::create_dir_all(&empty).unwrap();
        assert_eq!(resolve_target(&empty, &data), empty);
        let absent = t.0.join("absent");
        assert_eq!(resolve_target(&absent, &data), absent);
        let ours = t.0.join("ours");
        t.file("ours/.meet-storage");
        t.file("ours/engine/0.3.2/x");
        assert_eq!(resolve_target(&ours, &data), ours);
        assert_eq!(resolve_target(&data, &data), data);
    }

    #[test]
    fn busy_foreign_folder_gets_a_meet_subfolder() {
        let t = Temp::new("nest");
        let data = t.0.join("data");
        fs::create_dir_all(&data).unwrap();
        t.file("drive/photos/cat.jpg");
        assert_eq!(
            resolve_target(&t.0.join("drive"), &data),
            t.0.join("drive").join("Meet")
        );
    }

    #[test]
    fn target_checks_explain_refusals() {
        let t = Temp::new("check");
        let data = t.0.join("data");
        t.file("data/config.json");
        let current = data.clone();
        let fresh = t.0.join("E").join("Meet");
        fs::create_dir_all(t.0.join("E")).unwrap();
        assert_eq!(check_target(&fresh, &current, &data), Ok(()));
        assert!(check_target(&data, &current, &data)
            .unwrap_err()
            .contains("уже в этой папке"));
        assert!(check_target(&data.join("sub"), &current, &data)
            .unwrap_err()
            .contains("вне"));
        assert!(check_target(Path::new("relative"), &current, &data).is_err());
        let gone = t.0.join("unplugged").join("Meet");
        assert!(check_target(&gone, &current, &data)
            .unwrap_err()
            .contains("недоступна"));
        t.file("foreign/Meet/notes.txt");
        assert!(
            check_target(&t.0.join("foreign").join("Meet"), &current, &data)
                .unwrap_err()
                .contains("не пуста")
        );
        let file = t.file("plain.txt");
        assert!(check_target(&file, &current, &data).is_err());
    }

    #[test]
    fn back_to_the_system_disk_is_allowed_from_a_chosen_folder() {
        let t = Temp::new("back");
        let data = t.0.join("data");
        t.file("data/config.json");
        let current = t.0.join("E").join("Meet");
        t.file("E/Meet/.meet-storage");
        assert_eq!(check_target(&data, &current, &data), Ok(()));
    }

    #[test]
    fn space_need_adds_models_and_margin() {
        let gb = 1u64 << 30;
        assert!((needs_gb(8.0, 2 * gb) - 10.5).abs() < 1e-9);
        assert!((needs_gb(1.0, 0) - 1.5).abs() < 1e-9);
    }

    fn fake_layout(t: &Temp, home: &str) {
        for file in [
            "engine/0.3.3/Scripts/python.exe",
            "engine/python/cpython/python.exe",
            "models/hf/models--a--b/snapshots/r/model.bin",
            "models/gigaam/v3.ckpt",
            "models/xet/chunk",
        ] {
            t.file(&format!("{home}/{file}"));
        }
    }

    #[test]
    fn roll_back_restores_the_choice_and_removes_only_our_files() {
        let t = Temp::new("rollback");
        let data = t.0.join("data");
        t.file("data/config.json");
        let to = t.0.join("E").join("Meet");
        t.file("E/Meet/.meet-storage");
        fake_layout(&t, "E/Meet");
        t.file("E/Meet/notes.txt"); // чужое в нашей папке — не наше
        write_pointer(&data, Some(&to)).unwrap(); // упали посреди переключения
        fs::write(data.join(LEFTOVERS), "{}").unwrap();
        let journal = Journal {
            from: None,
            to: to.clone(),
            phase: Phase::Switching,
        };
        write_journal(&data, &journal).unwrap();
        roll_back(&data, &journal).unwrap();
        assert_eq!(root(&data), None);
        assert!(!data.join(LEFTOVERS).exists());
        assert!(!data.join(JOURNAL).exists());
        assert!(!to.join("engine").exists());
        assert!(!to.join("models").exists());
        assert!(to.join("notes.txt").is_file());
        assert!(data.join("config.json").is_file());
        roll_back(&data, &journal).unwrap(); // идемпотентно
    }

    #[test]
    fn roll_back_never_deletes_the_live_folder() {
        let t = Temp::new("live");
        let data = t.0.join("data");
        let live = t.0.join("Live");
        fake_layout(&t, "Live");
        t.file("data/config.json");
        write_pointer(&data, Some(&live)).unwrap();
        // Журнал сломан так, что «новая» папка — нынешняя.
        let journal = Journal {
            from: Some(live.clone()),
            to: live.clone(),
            phase: Phase::Engine,
        };
        roll_back(&data, &journal).unwrap();
        assert!(live.join("engine").join("0.3.3").is_dir());
        assert!(live.join("models").join("hf").is_dir());
    }

    #[test]
    fn finish_from_default_keeps_settings_records_and_the_shared_cache() {
        let t = Temp::new("finish-default");
        let data = t.0.join("data");
        fake_layout(&t, "data");
        t.file("data/config.json");
        t.file("data/recordings/r1/sys.opus");
        let to = t.0.join("Meet");
        fake_layout(&t, "Meet");
        write_pointer(&data, Some(&to)).unwrap();
        let journal = Journal {
            from: None,
            to: to.clone(),
            phase: Phase::Cleanup,
        };
        write_journal(&data, &journal).unwrap();
        finish(&data, &journal).unwrap();
        assert!(!data.join("engine").exists());
        assert!(!data.join("models").join("gigaam").exists());
        // В папке данных `models/hf` без выбора не бывает своим кэшем — не трогаем.
        assert!(data.join("models").join("hf").exists());
        assert!(data.join("config.json").is_file());
        assert!(data
            .join("recordings")
            .join("r1")
            .join("sys.opus")
            .is_file());
        assert!(!data.join(JOURNAL).exists());
        assert!(to.join("engine").join("0.3.3").is_dir(), "новая папка цела");
    }

    #[test]
    fn finish_from_a_chosen_folder_removes_it_whole() {
        let t = Temp::new("finish-chosen");
        let data = t.0.join("data");
        t.file("data/config.json");
        let from = t.0.join("Old");
        t.file("Old/.meet-storage");
        fake_layout(&t, "Old");
        let to = t.0.join("New");
        fake_layout(&t, "New");
        write_pointer(&data, Some(&to)).unwrap();
        let journal = Journal {
            from: Some(from.clone()),
            to,
            phase: Phase::Cleanup,
        };
        finish(&data, &journal).unwrap();
        assert!(!from.exists());
    }

    #[test]
    fn finish_with_an_unplugged_old_folder_just_closes_the_journal() {
        let t = Temp::new("finish-gone");
        let data = t.0.join("data");
        t.file("data/config.json");
        let to = t.0.join("New");
        fake_layout(&t, "New");
        write_pointer(&data, Some(&to)).unwrap();
        let journal = Journal {
            from: Some(t.0.join("unplugged")),
            to,
            phase: Phase::Cleanup,
        };
        write_journal(&data, &journal).unwrap();
        finish(&data, &journal).unwrap();
        assert!(!data.join(JOURNAL).exists());
    }

    #[test]
    fn copy_lines_are_progress_result_or_error() {
        assert_eq!(
            parse_copy_line(r#"{"done": 5, "total": 10}"#),
            CopyLine::Progress { done: 5, total: 10 }
        );
        assert_eq!(
            parse_copy_line(r#"{"ok": true, "bytes": 10, "copied": 3}"#),
            CopyLine::Done
        );
        assert_eq!(
            parse_copy_line(r#"{"error": "Недостаточно места"}"#),
            CopyLine::Error("Недостаточно места".into())
        );
        assert_eq!(
            parse_copy_line("Traceback (most recent call last):"),
            CopyLine::Other
        );
    }

    #[test]
    fn paths_compare_case_insensitively_on_windows() {
        let t = Temp::new("case");
        let a = t.0.join("Folder");
        fs::create_dir_all(&a).unwrap();
        assert!(same_path(&a, &a.join(".").join("..").join("Folder")));
        assert!(inside(&a.join("x"), &a));
        assert!(!inside(&a, &a));
        if cfg!(windows) {
            assert!(same_path(&a, &t.0.join("FOLDER")));
        }
    }
}
