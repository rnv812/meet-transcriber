// Перенос движка и моделей в приложении: настоящее окружение переноса
// (`AppEnv`: установщик, копирование, резидент, события окна), проверка перед
// переносом и команды окна. Сама логика шагов, журнала, отката и уборки —
// в `storage.rs` (там же тесты на подделке).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::api;
use crate::engine;
use crate::logs::shell_log;
use crate::resident::{self, ResidentStatus, Supervisor};
use crate::storage::{
    self, abandon, check_target, dir_bytes, execute, home, needs_gb, parse_copy_line, pointer,
    read_journal, resolve_target, same_path, Blocked, CopyLine, Journal, MoveControl, MoveEnv,
    Phase, Pointer, Recover,
};

/// Событие хода переноса: `{phase, text, done, total}`.
pub const PROGRESS_EVENT: &str = "storage-progress";
const NOT_RUNNING: &str = "Служба записи не запущена — перенос возможен, когда она работает";
/// Сколько ждём, что резидент поднимется из новой папки (первый импорт torch
/// с холодного диска бывает долгим).
const START_TIMEOUT: Duration = Duration::from_secs(180);
/// Сколько ждём выхода резидента, которого попросили выйти перед удалением
/// его папки (он сохраняет идущую запись).
const EXIT_TIMEOUT: Duration = Duration::from_secs(75);
const POLL: Duration = Duration::from_millis(250);

static MOVING: AtomicBool = AtomicBool::new(false);
static CONTROL: MoveControl = MoveControl::new();

struct Moving;

impl Moving {
    fn begin() -> Result<Moving, String> {
        MOVING
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .map_err(|_| "Перенос уже идёт".to_string())?;
        CONTROL.reset();
        Ok(Moving)
    }
}

impl Drop for Moving {
    fn drop(&mut self) {
        MOVING.store(false, Ordering::SeqCst);
    }
}

// --- резидент ---------------------------------------------------------------------

fn client() -> Option<(resident::Endpoint, api::Client)> {
    let endpoint = resident::read_endpoint()?;
    resident::answers(&endpoint).then(|| {
        let client = api::Client::new(&endpoint);
        (endpoint, client)
    })
}

/// Окружение резидента, который отвечает по `daemon.json` (`GET /storage`).
fn resident_prefix(client: &api::Client) -> Option<PathBuf> {
    client
        .get("/storage")
        .ok()?
        .get("prefix")?
        .as_str()
        .map(PathBuf::from)
}

/// Погасить резидент, запущенный из `dir`, и дождаться выхода: удалять папку
/// из-под живого резидента нельзя (его ленивые импорты, запись, загрузки).
/// `false` — не удалось убедиться (резидент отвечает, но окружение не назвал,
/// или не вышел): удалять нельзя, повторим позже.
fn stop_resident_in(dir: &Path) -> bool {
    let Some((endpoint, client)) = client() else {
        return true;
    };
    let Some(prefix) = resident_prefix(&client) else {
        shell_log!(
            "резидент не назвал своё окружение — {} удалю позже",
            dir.display()
        );
        return false;
    };
    if !(same_path(&prefix, dir) || storage::inside(&prefix, dir)) {
        return true;
    }
    shell_log!(
        "резидент работает из {} — прошу его выйти до удаления папки",
        dir.display()
    );
    if let Err(error) = client.post("/shutdown", serde_json::Value::Null) {
        shell_log!("/shutdown не прошёл: {error}");
    }
    let deadline = Instant::now() + EXIT_TIMEOUT;
    while Instant::now() < deadline && resident::answers(&endpoint) {
        thread::sleep(POLL);
    }
    !resident::answers(&endpoint)
}

/// Восстановление при старте: оболочки с надзором ещё нет — только резидент
/// по `daemon.json`.
pub struct StartupEnv;

impl Recover for StartupEnv {
    fn stop_resident_in(&self, dir: &Path) -> bool {
        stop_resident_in(dir)
    }
}

/// Уборка очереди в фоне, с повторами (`storage::DRAIN_RETRIES`).
fn drain_in_background(data: PathBuf, cleanup: Option<Journal>) {
    let spawned = thread::Builder::new()
        .name("meet-storage-cleanup".into())
        .spawn(move || {
            let mut left = storage::recover_slow(&data, cleanup, &StartupEnv);
            for delay in storage::DRAIN_RETRIES.iter().skip(1) {
                if left == 0 {
                    return;
                }
                thread::sleep(*delay);
                left = storage::drain_once(&data, &StartupEnv);
            }
            if left > 0 {
                shell_log!(
                    "не удалилось недоделанное ({left} папок) — повторю при следующем запуске"
                );
            }
        });
    if let Err(error) = spawned {
        shell_log!("поток уборки после переноса не запустился: {error}");
    }
}

/// При старте оболочки, до надзора и обслуживания движка: прерванный перенос
/// отметить (выбор — прежний), доведение уборки и очередь удаления — в фоне.
pub fn recover_at_startup() {
    let data = resident::data_dir();
    let cleanup = storage::recover_journal(&data);
    if cleanup.is_some() || storage::discards_pending(&data) {
        drain_in_background(data, cleanup);
    }
}

/// Настоящее окружение переноса.
struct AppEnv<'a> {
    app: &'a AppHandle,
    data: PathBuf,
    version: String,
    /// Id удержания резидента этого переноса (повтор — идемпотентен).
    hold_id: String,
}

impl Recover for AppEnv<'_> {
    fn stop_resident_in(&self, dir: &Path) -> bool {
        stop_resident_in(dir)
    }
}

impl MoveEnv for AppEnv<'_> {
    fn install(&self, home: &Path, profile: &str) -> Result<(), String> {
        engine::install_into(self.app, home, profile)
    }

    fn copy_models(&self, to: &Path) -> Result<(), String> {
        copy_models(self.app, &self.data, to, &self.version)
    }

    fn smoke(&self, env: &Path) -> Result<(), String> {
        smoke(&self.data, env)
    }

    fn hold(&self) -> Result<Option<String>, String> {
        if self.app.state::<Supervisor>().status() != ResidentStatus::Running {
            return Err(NOT_RUNNING.into());
        }
        let (_, client) = client().ok_or(NOT_RUNNING)?;
        let answer = client
            .post("/storage/hold", serde_json::json!({ "id": self.hold_id }))
            .map_err(|error| format!("Служба записи не ответила: {error}"))?;
        if answer.get("held").and_then(serde_json::Value::as_bool) == Some(true) {
            Ok(None)
        } else {
            Ok(Some(
                answer
                    .get("busy")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("служба записи занята")
                    .to_string(),
            ))
        }
    }

    fn release(&self) {
        if let Some((_, client)) = client() {
            let _ = client.delete(&format!("/storage/hold?id={}", self.hold_id));
        }
    }

    fn restart_from(&self, env: &Path) -> bool {
        let supervisor = self.app.state::<Supervisor>();
        supervisor.respawn(self.app);
        started_from(&supervisor, env)
    }

    fn respawn(&self) {
        self.app.state::<Supervisor>().respawn(self.app);
    }

    fn emit(&self, phase: &'static str, text: &str, done: u64, total: u64) {
        emit(self.app, phase, text, done, total);
    }

    fn pause(&self, duration: Duration) {
        thread::sleep(duration);
    }

    fn drain(&self, data_dir: &Path) {
        drain_in_background(data_dir.to_path_buf(), None);
    }
}

#[derive(Serialize, Clone)]
struct Progress<'a> {
    phase: &'static str,
    text: &'a str,
    done: u64,
    total: u64,
}

fn emit(app: &AppHandle, phase: &'static str, text: &str, done: u64, total: u64) {
    let _ = app.emit(
        PROGRESS_EVENT,
        Progress {
            phase,
            text,
            done,
            total,
        },
    );
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

/// Новый движок запускается: torch и ctranslate2 импортируются его Python.
fn smoke(data: &Path, env: &Path) -> Result<(), String> {
    let python = engine::python_of(env);
    let argv: Vec<String> = vec![
        python.to_string_lossy().into_owned(),
        "-c".into(),
        "import torch, ctranslate2".into(),
    ];
    let envs: [(&str, std::ffi::OsString); 1] = [("PYTHONIOENCODING", "utf-8".into())];
    let mut tail: Vec<String> = Vec::new();
    let code = engine::run_streamed(&argv, &envs, data, |line| {
        shell_log!("проверка движка: {line}");
        tail.push(line);
        if tail.len() > 3 {
            tail.remove(0);
        }
    })
    .map_err(|error| format!("не запустился ({error})"))?;
    match code {
        Some(0) => Ok(()),
        _ => Err(tail
            .last()
            .cloned()
            .unwrap_or_else(|| "импорт torch не прошёл".into())),
    }
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
            | ResidentStatus::StorageUnreadable
            | ResidentStatus::Quitting => return false,
            _ => thread::sleep(POLL),
        }
    }
    false
}

// --- проверка перед переносом -----------------------------------------------------

/// Что резидент говорит о переносе (`GET /storage`).
struct ResidentInfo {
    busy: Option<String>,
    models_bytes: u64,
}

fn resident_info(app: &AppHandle) -> Result<ResidentInfo, String> {
    if app.state::<Supervisor>().status() != ResidentStatus::Running {
        return Err(NOT_RUNNING.into());
    }
    let (_, client) = client().ok_or(NOT_RUNNING)?;
    let value = client
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
    /// Это продолжение прерванного переноса в ту же папку.
    pub resume: bool,
}

struct Plan {
    target: PathBuf,
    profile: String,
    check: StorageCheck,
}

fn plan(app: &AppHandle, picked: &Path) -> Result<Plan, String> {
    let data = resident::data_dir();
    storage::ready(&data)?;
    let current = home(&data);
    let target = resolve_target(picked, &data);
    check_target(&target, &current, &data)?;
    let version = app.package_info().version.to_string();
    let profile = engine::installed_profile(&engine::env_dir(&current, &version), &version)
        .ok_or("Движок не установлен — перенос возможен после установки")?;
    let info = resident_info(app)?;
    let engine_gb = engine::space_for(&target, &profile);
    // Продолжение: скопированное уже на месте и место не займёт снова.
    let present = dir_bytes(&target.join("models"));
    let models = info.models_bytes.saturating_sub(present);
    let models_gb = models as f64 / f64::from(1u32 << 30);
    let needs = needs_gb(engine_gb, models);
    let free = engine::free_gb(&target);
    let error = free.and_then(|free| engine::space_error(needs, free));
    let resume = read_journal(&data).is_some_and(|journal| {
        journal.phase == Phase::Interrupted && same_path(&journal.to, &target)
    });
    Ok(Plan {
        check: StorageCheck {
            target: target.to_string_lossy().into_owned(),
            free_gb: free.map(|gb| (gb * 10.0).floor() / 10.0),
            needs_gb: (needs * 10.0).ceil() / 10.0,
            engine_gb,
            models_gb: (models_gb * 10.0).ceil() / 10.0,
            busy: info.busy,
            error,
            resume,
        },
        target,
        profile,
    })
}

fn run_move(app: &AppHandle, picked: &Path) -> Result<String, String> {
    let _install = engine::begin_install()?;
    let _moving = Moving::begin()?;
    let data = resident::data_dir();
    let env = AppEnv {
        app,
        data: data.clone(),
        version: app.package_info().version.to_string(),
        hold_id: uuid::Uuid::new_v4().to_string(),
    };
    let wanted = resolve_target(picked, &data);
    storage::preamble(&data, &wanted, &env)?;
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
    // Только после проверки: отказ (занято, места нет) не снимает папку с
    // очереди удаления.
    storage::claim(&data, &target)?;
    shell_log!(
        "перенос движка и моделей: {} -> {}",
        home(&data).display(),
        target.display()
    );
    let journal = Journal::new(storage::root(&data), target.clone(), Phase::Engine);
    execute(&env, &CONTROL, &data, journal, &plan.profile, &env.version)?;
    shell_log!("перенос движка и моделей закончен: {}", target.display());
    Ok(target.to_string_lossy().into_owned())
}

// --- команды окна -----------------------------------------------------------------

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
    /// `storage.json` не прочитан.
    pub unreadable: bool,
    /// Перенос идёт (до переключения).
    pub moving: bool,
    /// Отменить ещё можно.
    pub cancellable: bool,
    /// Перенос в эту папку прерван — «Продолжить» / «Отменить».
    pub interrupted: Option<String>,
    /// Недоделанное ещё удаляется (файлы были заняты).
    pub discarding: bool,
}

#[tauri::command]
pub fn storage_status() -> StorageStatus {
    let data = resident::data_dir();
    let journal = read_journal(&data);
    let moving = MOVING.load(Ordering::SeqCst);
    StorageStatus {
        root: storage::root(&data).map(|root| root.to_string_lossy().into_owned()),
        home: home(&data).to_string_lossy().into_owned(),
        default_home: data.to_string_lossy().into_owned(),
        missing: match storage::blocked(&data) {
            Some(Blocked::Missing(path)) => Some(path.to_string_lossy().into_owned()),
            _ => None,
        },
        unreadable: pointer(&data) == Pointer::Unreadable,
        moving: moving
            || journal
                .as_ref()
                .is_some_and(|journal| journal.phase.moving()),
        cancellable: moving && CONTROL.cancellable(),
        interrupted: journal
            .filter(|journal| !moving && journal.phase == Phase::Interrupted)
            .map(|journal| journal.to.to_string_lossy().into_owned()),
        discarding: storage::discards_pending(&data),
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

/// Перенести движок и модели в `target` (или продолжить прерванный перенос
/// туда же). Ответ — по окончании (минуты): путь, куда перенесено; ход —
/// событиями `storage-progress`.
#[tauri::command]
pub async fn storage_move(app: AppHandle, target: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || run_move(&app, Path::new(target.trim())))
        .await
        .map_err(|error| error.to_string())?
}

/// «Отменить» идущий перенос (до переключения): гасит установку или
/// копирование; `storage_move` ответит `CANCELLED` после очистки.
#[tauri::command]
pub fn storage_cancel() {
    if MOVING.load(Ordering::SeqCst) && CONTROL.cancel() {
        shell_log!("перенос движка и моделей отменён пользователем");
        engine::cancel_installs();
    }
}

/// «Отменить» прерванный перенос: новая папка очищается (в фоне, с повторами).
#[tauri::command]
pub async fn storage_abandon() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        if MOVING.load(Ordering::SeqCst) {
            return Err("Перенос идёт — отмените его кнопкой «Отменить»".to_string());
        }
        let data = resident::data_dir();
        let Some(journal) = read_journal(&data).filter(|j| j.phase == Phase::Interrupted) else {
            return Ok(());
        };
        abandon(&data, &journal)?;
        shell_log!("прерванный перенос в {} отменён", journal.to.display());
        drain_in_background(data, None);
        Ok(())
    })
    .await
    .map_err(|error| error.to_string())?
}

/// «Вернуть на системный диск», когда выбранной папки нет или выбор не
/// прочитан: выбор снимается (движок и модели — снова в папке данных и общем
/// кэше HF), резидент перезапускается; движка там нет — окно предложит
/// установить.
#[tauri::command]
pub async fn storage_reset(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        if MOVING.load(Ordering::SeqCst) {
            return Err("Идёт перенос — дождитесь его конца".to_string());
        }
        let data = resident::data_dir();
        if storage::blocked(&data).is_none() {
            return Err("Папка движка и моделей на месте — возвращать нечего".to_string());
        }
        storage::reset_choice(&data, None)?;
        shell_log!("папка движка и моделей недоступна — возвращаю на системный диск");
        app.state::<Supervisor>().respawn(&app);
        Ok(())
    })
    .await
    .map_err(|error| error.to_string())?
}

/// «Указать папку», когда выбранной папки нет (буква диска сменилась) или
/// выбор не прочитан: годится только папка с движком Meet этой версии.
#[tauri::command]
pub async fn storage_repoint(app: AppHandle, folder: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        if MOVING.load(Ordering::SeqCst) {
            return Err("Идёт перенос — дождитесь его конца".to_string());
        }
        let data = resident::data_dir();
        let folder = PathBuf::from(folder.trim());
        let version = app.package_info().version.to_string();
        storage::repoint(&data, &folder, &version)?;
        shell_log!(
            "папка движка и моделей указана заново: {}",
            folder.display()
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
