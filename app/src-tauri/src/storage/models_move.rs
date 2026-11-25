// Отдельная папка моделей (0.6.3): модели Meet — свой кэш Hugging Face,
// GigaAM, кэш Xet — на любом диске, независимо от движка.
//
// Выбор лежит в `<data_dir>\storage-models.json` (формат как у `storage.json`:
// `{"version": 1, "root": "F:\\Models", "folder": "<id>"}`); его читает и
// Python (`meet.paths.models_root`). Нет файла — модели рядом с движком
// (`<папка движка>\models`; без выбранной папки движка модели HF — в общем
// кэше), как до 0.6.3: старые установки ничего не замечают, а прежняя версия
// после отката файла просто не видит (модели скачаются заново). Раскладка
// выбранной папки:
//
//     <папка>\hf       свой кэш HF: только модели каталога Meet
//     <папка>\gigaam   веса GigaAM
//     <папка>\xet      кэш кусков загрузчика Xet
//
// Перенос (`execute`) — по шагам своего журнала `storage-models-move.json`
// (те же `Phase`, что у переноса движка, без установки):
//
//   models     модели Meet копируются и сверяются (`python -m meet.storage
//              copy-models` — Python нынешнего движка; повтор докопирует
//              только недостающее);
//   switching  резидент удерживается, пишется `storage-models.json`, резидент
//              перезапускается и должен назвать новую папку моделей;
//   cleanup    прежние модели Meet удаляются (общий кэш HF — никогда: о нём
//              спросит окно, как после переноса движка).
//
// До `cleanup` перенос ПРЕРЫВАЕТСЯ (выбор — прежний, скопированное ждёт
// «Продолжить»), отмена человеком — очищает новую папку (очередь удаления,
// `Scope::Models`). После `cleanup` — доведение при следующем запуске.
//
// Переносы — по одному: перенос моделей не начинается, пока прерван перенос
// движка, и наоборот. Общий перенос при отдельной папке моделей двигает
// только движок (`storage::steps`), и его папка не может совпасть с папкой
// моделей или лежать в ней (`check_target`).

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::{
    cancel_check, check_place, drain_lock, finish, hold_and_latch, home, inside, journal_state,
    owner, parse_pointer, pointer_file_folder, quarantine_journal, queue_discard_locked,
    remove_file, remove_tree, resolve_target_in, root, same_path, write_atomic, write_pointer_file,
    Blocked, JournalState, MoveControl, Owner, Phase, Pointer, Scope, SwitchEnv, CANCELLED,
    LEFTOVERS, MARK, MODELS, OWN_MODELS, VERSION,
};
use crate::logs::shell_log;

/// Отдельная папка моделей (читает и Python, `meet.paths.models_root`).
pub const MODELS_POINTER: &str = "storage-models.json";
/// Журнал переноса моделей (Python по нему не качает модели, пока перенос идёт).
pub const MODELS_JOURNAL: &str = "storage-models-move.json";
/// Подпапка в выбранной непустой чужой папке и в корне диска.
const NEST_MODELS: &str = "Meet Models";

pub const MODELS_HERE: &str = "Здесь папка моделей Meet (или она лежит внутри) — для движка \
                               выберите другую папку";
pub const CANCELLED_MODELS: &str = "Перенос отменён — модели остались на прежнем месте";
pub const START_FAILED_MODELS: &str = "Служба записи не заработала с новой папкой моделей — \
                                       модели остались на прежнем месте. Подробности — в журнале";
pub const ENGINE_INTERRUPTED: &str = "Перенос движка прерван — сначала продолжите или отмените \
                                      его («Движок и модели» в разделе «Распознавание»)";
pub const MODELS_INTERRUPTED: &str = "Перенос моделей прерван — сначала продолжите или отмените \
                                      его (раздел «Модели»)";
pub const MODELS_MISSING_TITLE: &str = "Папка моделей недоступна";
pub const MODELS_UNREADABLE_TITLE: &str = "Файл выбора папки моделей повреждён";

// --- выбор ----------------------------------------------------------------------

pub fn models_pointer(data_dir: &Path) -> Pointer {
    parse_pointer(&data_dir.join(MODELS_POINTER))
}

/// Отдельно выбранная папка моделей; нет выбора или он не прочитан — `None`.
pub fn models_root(data_dir: &Path) -> Option<PathBuf> {
    match models_pointer(data_dir) {
        Pointer::Root(root) => Some(root),
        _ => None,
    }
}

/// Id папки моделей, записанный в выбор.
pub fn models_pointer_folder(data_dir: &Path) -> Option<String> {
    pointer_file_folder(&data_dir.join(MODELS_POINTER))
}

/// Где модели без отдельной папки: рядом с движком.
pub fn default_dir(data_dir: &Path) -> PathBuf {
    home(data_dir).join(MODELS)
}

/// Где модели сейчас.
pub fn models_dir(data_dir: &Path) -> PathBuf {
    models_root(data_dir).unwrap_or_else(|| default_dir(data_dir))
}

/// Почему папка моделей сейчас недоступна (резидент работает, но модели не
/// качаются и не грузятся — окно предлагает подключить диск или вернуть
/// модели в папку по умолчанию).
pub fn models_blocked(data_dir: &Path) -> Option<Blocked> {
    match models_pointer(data_dir) {
        Pointer::Absent => None,
        Pointer::Unreadable => Some(Blocked::Unreadable),
        Pointer::Root(root) => (!root.is_dir()).then_some(Blocked::Missing(root)),
    }
}

/// Записать выбор (`None` — модели рядом с движком: файла нет).
pub fn write_models_pointer(data_dir: &Path, root: Option<&Path>) -> io::Result<()> {
    write_pointer_file(&data_dir.join(MODELS_POINTER), root)
}

/// Модели по умолчанию — в общем кэше HF (папки движка нет): после переезда
/// оттуда окно спросит об остатках.
fn default_is_shared(data_dir: &Path) -> bool {
    root(data_dir).is_none()
}

// --- куда переносить --------------------------------------------------------------

/// Папка моделей для выбранной человеком: как у движка (`resolve_target`),
/// но подпапка — `Meet Models`.
pub fn resolve_models_target(picked: &Path, data_dir: &Path) -> PathBuf {
    resolve_target_in(picked, data_dir, NEST_MODELS)
}

/// Годится ли `target` (после `resolve_models_target`) для моделей.
pub fn check_models_target(target: &Path, data_dir: &Path) -> Result<(), String> {
    if !target.is_absolute() {
        return Err("Укажите полный путь к папке".into());
    }
    let current = models_dir(data_dir);
    if same_path(target, &current) {
        return Err("Модели уже в этой папке".into());
    }
    if inside(target, &current) || inside(&current, target) {
        return Err("Выберите папку вне нынешней папки моделей".into());
    }
    let engine = home(data_dir);
    if same_path(target, &engine) || inside(target, &engine) || inside(&engine, target) {
        return Err(
            "Это папка движка Meet — для моделей выберите другую (вернуть модели к \
                    движку — «Вернуть в папку по умолчанию»)"
                .into(),
        );
    }
    if same_path(target, data_dir) || inside(target, data_dir) {
        return Err("Выберите папку вне папки данных Meet".into());
    }
    check_place(target, data_dir, false)
}

/// Обратно рядом с движком: можно, только если модели сейчас отдельно.
pub fn check_models_default(data_dir: &Path) -> Result<PathBuf, String> {
    if models_root(data_dir).is_none() {
        return Err("Модели уже в папке по умолчанию".into());
    }
    let target = default_dir(data_dir);
    if !home(data_dir).is_dir() {
        return Err("Папка движка недоступна — диск отключён?".into());
    }
    Ok(target)
}

// --- журнал -----------------------------------------------------------------------

fn version() -> u32 {
    VERSION
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct ModelsJournal {
    #[serde(default = "version")]
    pub version: u32,
    /// Прежний выбор; `None` — модели были рядом с движком.
    pub from: Option<PathBuf>,
    /// Новый выбор; `None` — обратно к движку.
    pub to: Option<PathBuf>,
    /// Где модели были (настоящая папка: выбор или папка рядом с движком).
    pub from_dir: PathBuf,
    /// Куда переносятся.
    pub to_dir: PathBuf,
    pub phase: Phase,
    /// Id новой папки (из метки, записанной `claim`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub folder: Option<String>,
}

impl ModelsJournal {
    /// Перенос в `to` (`None` — обратно к движку) из нынешнего места.
    pub fn new(data_dir: &Path, to: Option<PathBuf>) -> Self {
        let to_dir = to.clone().unwrap_or_else(|| default_dir(data_dir));
        ModelsJournal {
            version: VERSION,
            from: models_root(data_dir),
            from_dir: models_dir(data_dir),
            to,
            to_dir,
            phase: Phase::Models,
            folder: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum ModelsJournalState {
    Absent,
    Ok(ModelsJournal),
    Bad,
}

pub fn models_journal_state(data_dir: &Path) -> ModelsJournalState {
    match fs::read_to_string(data_dir.join(MODELS_JOURNAL)) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => ModelsJournalState::Absent,
        Err(_) => ModelsJournalState::Bad,
        Ok(raw) => match serde_json::from_str::<ModelsJournal>(&raw) {
            Ok(journal) if journal.version == VERSION => ModelsJournalState::Ok(journal),
            _ => ModelsJournalState::Bad,
        },
    }
}

pub fn read_models_journal(data_dir: &Path) -> Option<ModelsJournal> {
    match models_journal_state(data_dir) {
        ModelsJournalState::Ok(journal) => Some(journal),
        _ => None,
    }
}

pub fn write_models_journal(data_dir: &Path, journal: &ModelsJournal) -> Result<(), String> {
    let text = serde_json::to_string_pretty(journal).unwrap_or_default();
    write_atomic(&data_dir.join(MODELS_JOURNAL), &text)
        .map_err(|error| format!("Не удалось записать журнал переноса моделей: {error}"))
}

fn quarantine_models_journal(data_dir: &Path) {
    let path = data_dir.join(MODELS_JOURNAL);
    let bad = data_dir.join(format!("{MODELS_JOURNAL}.bad"));
    let _ = remove_file(&bad);
    match fs::rename(&path, &bad) {
        Ok(()) => shell_log!(
            "журнал переноса моделей не прочитан — отложен в {}",
            bad.display()
        ),
        Err(error) => shell_log!("журнал переноса моделей не прочитан и не отложен: {error}"),
    }
}

// --- удаление ---------------------------------------------------------------------

/// Убрать из папки моделей наше: `hf`, `gigaam`, `xet`; `separate` (отдельная
/// папка) — ещё метку и саму папку, если опустела. Папка рядом с движком
/// (`<папка движка>\models`) удаляется, только если опустела.
pub fn remove_models(dir: &Path, separate: bool) -> Vec<String> {
    let mut errors = Vec::new();
    for name in OWN_MODELS {
        remove_tree(&dir.join(name), &mut errors);
    }
    if errors.is_empty() {
        if separate {
            // Метка — только когда всё наше ушло: иначе повтор не узнал бы папку.
            let _ = remove_file(&dir.join(MARK));
        }
        let _ = fs::remove_dir(dir); // только пустую: чужие файлы — его
    }
    errors
}

/// Прежняя папка моделей наша: отдельная — по метке; рядом с движком — её
/// папка движка наша (папка данных или метка с нашим id).
fn ours(dir: &Path, separate: bool, data_dir: &Path) -> bool {
    if separate {
        return owner(dir, data_dir) == Owner::Ours;
    }
    dir.file_name().is_some_and(|name| name == MODELS)
        && dir
            .parent()
            .is_some_and(|parent| owner(parent, data_dir) == Owner::Ours)
}

// --- восстановление и откат -------------------------------------------------------

/// Прервать перенос моделей: выбор — прежний, журнал — `interrupted`,
/// скопированное остаётся для «Продолжить». Идемпотентно.
pub fn interrupt_models(data_dir: &Path, journal: &ModelsJournal) -> Result<(), String> {
    write_models_pointer(data_dir, journal.from.as_deref())
        .map_err(|error| format!("Не удалось вернуть прежнюю папку моделей: {error}"))?;
    if journal.from.is_none() && default_is_shared(data_dir) {
        let _ = remove_file(&data_dir.join(LEFTOVERS));
    }
    let mut stopped = journal.clone();
    stopped.phase = Phase::Interrupted;
    write_models_journal(data_dir, &stopped)
}

/// Отменить перенос моделей: скопированное в новую папку — в очередь на
/// удаление (папка рядом с движком — сразу: в ней ничего живого), журнала
/// нет. Живую папку моделей не трогаем никогда; общий кэш HF — тоже.
pub fn abandon_models(data_dir: &Path, journal: &ModelsJournal) -> Result<(), String> {
    let _lock = drain_lock();
    abandon_models_locked(data_dir, journal)
}

fn abandon_models_locked(data_dir: &Path, journal: &ModelsJournal) -> Result<(), String> {
    let live = models_dir(data_dir);
    let target = &journal.to_dir;
    if !same_path(target, &live) && !inside(&live, target) {
        if journal.to.is_some() {
            queue_discard_locked(data_dir, target, Scope::Models, journal.folder.clone());
        } else if ours(target, false, data_dir) {
            let errors = remove_models(target, false);
            if !errors.is_empty() {
                shell_log!(
                    "скопированное к движку убрано не всё: {}",
                    errors.join("; ")
                );
            }
        }
    }
    remove_file(&data_dir.join(MODELS_JOURNAL)).map_err(|error| error.to_string())
}

/// «Вернуть в папку по умолчанию», когда отдельной папки моделей нет (диск
/// отключён) или выбор не прочитан: выбор снимается без копирования — модели
/// скачаются заново рядом с движком. Прерванный перенос теряет смысл.
pub fn reset_models(data_dir: &Path) -> Result<(), String> {
    let _lock = drain_lock();
    write_models_pointer(data_dir, None)
        .map_err(|error| format!("Не удалось записать выбор папки моделей: {error}"))?;
    if let ModelsJournalState::Ok(journal) = models_journal_state(data_dir) {
        if journal.phase != Phase::Cleanup {
            abandon_models_locked(data_dir, &journal)?;
        }
    }
    Ok(())
}

/// Довести перенос моделей: удалить прежние модели Meet (общий кэш HF не
/// трогаем — о нём спросит окно). Не удалилось (файлы заняты) — журнал
/// остаётся, следующий запуск попробует снова.
pub fn finish_models(data_dir: &Path, journal: &ModelsJournal) -> Result<(), String> {
    let _lock = drain_lock();
    match models_journal_state(data_dir) {
        ModelsJournalState::Ok(current)
            if current == *journal && current.phase == Phase::Cleanup => {}
        _ => return Ok(()),
    }
    if models_blocked(data_dir).is_some() || super::blocked(data_dir).is_some() {
        return Err("папка моделей недоступна — прежние уберу, когда она вернётся".into());
    }
    let from = &journal.from_dir;
    let live = models_dir(data_dir);
    let separate = journal.from.is_some();
    let renamed_live = separate
        && super::folder_id(from).is_some()
        && super::folder_id(from) == models_pointer_folder(data_dir);
    if !same_path(from, &live) && !inside(&live, from) && !renamed_live {
        if from.is_dir() && !ours(from, separate, data_dir) {
            shell_log!(
                "прежняя папка моделей {} — не наша, не удаляю",
                from.display()
            );
        } else if from.is_dir() {
            let errors = remove_models(from, separate);
            if !errors.is_empty() {
                return Err(errors.join("; "));
            }
        } else if separate {
            shell_log!(
                "прежняя папка моделей недоступна ({}) — удалите её вручную",
                from.display()
            );
        }
    }
    remove_file(&data_dir.join(MODELS_JOURNAL)).map_err(|error| error.to_string())
}

/// Быстрая часть восстановления при старте: прерванный до переключения
/// перенос моделей становится `interrupted`. → журнал `cleanup`, который
/// осталось довести (`finish_models`).
pub fn recover_models_journal(data_dir: &Path) -> Option<ModelsJournal> {
    match models_journal_state(data_dir) {
        ModelsJournalState::Absent => None,
        ModelsJournalState::Bad => {
            quarantine_models_journal(data_dir);
            None
        }
        ModelsJournalState::Ok(journal) => match journal.phase {
            Phase::Cleanup => Some(journal),
            Phase::Interrupted => None,
            _ => {
                shell_log!(
                    "перенос моделей прерван на шаге {:?} — прежняя папка снова главная, \
                     скопированное ждёт «Продолжить»",
                    journal.phase
                );
                if let Err(error) = interrupt_models(data_dir, &journal) {
                    shell_log!("прерванный перенос моделей не отмечен: {error}");
                }
                None
            }
        },
    }
}

/// Перед переносом движка: перенос моделей не должен висеть. Уборка после
/// переключения — доводится; прерванный — ждёт человека (отказ).
pub fn settle_models(data_dir: &Path) -> Result<(), String> {
    match models_journal_state(data_dir) {
        ModelsJournalState::Absent => Ok(()),
        ModelsJournalState::Bad => {
            quarantine_models_journal(data_dir);
            Ok(())
        }
        ModelsJournalState::Ok(journal) if journal.phase == Phase::Cleanup => {
            finish_models(data_dir, &journal).map_err(|error| {
                format!("Прежняя папка моделей ещё не удалена ({error}) — повторите позже")
            })
        }
        ModelsJournalState::Ok(_) => Err(MODELS_INTERRUPTED.into()),
    }
}

/// Перед переносом моделей: перенос движка не должен висеть (уборка —
/// доводится, прерванный — отказ), а прерванный перенос моделей в другую
/// папку отменяется (та же — «Продолжить»).
fn preamble_models(data_dir: &Path, wanted: &Path, env: &impl SwitchEnv) -> Result<(), String> {
    match journal_state(data_dir) {
        JournalState::Absent => {}
        JournalState::Bad => quarantine_journal(data_dir),
        JournalState::Ok(journal) if journal.phase == Phase::Cleanup => {
            finish(data_dir, &journal, env).map_err(|error| {
                format!(
                    "Прежняя папка движка ещё не удалена ({error}) — перезапустите компьютер и повторите"
                )
            })?;
        }
        JournalState::Ok(_) => return Err(ENGINE_INTERRUPTED.into()),
    }
    match models_journal_state(data_dir) {
        ModelsJournalState::Absent => Ok(()),
        ModelsJournalState::Bad => {
            quarantine_models_journal(data_dir);
            Ok(())
        }
        ModelsJournalState::Ok(journal) if journal.phase == Phase::Cleanup => {
            finish_models(data_dir, &journal).map_err(|error| {
                format!("Прежняя папка моделей ещё не удалена ({error}) — повторите позже")
            })
        }
        ModelsJournalState::Ok(journal) => {
            if !same_path(&journal.to_dir, wanted) {
                abandon_models(data_dir, &journal)?;
                env.drain(data_dir);
            }
            Ok(())
        }
    }
}

/// Начало переноса моделей: сначала проверка (только чтение — отказ не
/// трогает прерванный перенос), потом — что осталось от прошлого.
pub fn begin_models_move<T>(
    data_dir: &Path,
    wanted: &Path,
    env: &impl SwitchEnv,
    check: impl FnOnce() -> Result<T, String>,
) -> Result<T, String> {
    let checked = check()?;
    preamble_models(data_dir, wanted, env)?;
    Ok(checked)
}

// --- перенос ----------------------------------------------------------------------

/// Чем переносу моделей нужен мир вокруг, сверх переключения.
pub trait ModelsEnv: SwitchEnv {
    /// Скопировать и сверить модели Meet в `to` (`None` — обратно к движку).
    fn copy_models_to(&self, to: Option<&Path>) -> Result<(), String>;
    /// Перезапустить резидент и дождаться, что он поднялся и берёт модели из
    /// `models_dir`.
    fn restart_with_models(&self, models_dir: &Path) -> bool;
}

fn advance_models(
    env: &impl ModelsEnv,
    data_dir: &Path,
    journal: &mut ModelsJournal,
    phase: Phase,
    what: &'static str,
) -> Result<(), String> {
    journal.phase = phase;
    write_models_journal(data_dir, journal)?;
    env.checkpoint(what);
    Ok(())
}

/// Перенести модели по `journal`. Сбой до переключения — перенос прерван
/// (`interrupt_models`), отмена — очищен (`abandon_models`); выбор в обоих
/// случаях прежний.
pub fn execute_models(
    env: &impl ModelsEnv,
    control: &MoveControl,
    data_dir: &Path,
    mut journal: ModelsJournal,
) -> Result<(), String> {
    journal.phase = Phase::Models;
    if journal.folder.is_none() && journal.to.is_some() {
        journal.folder = super::folder_id(&journal.to_dir);
    }
    write_models_journal(data_dir, &journal)?;
    env.checkpoint("models");
    let Err(error) = models_steps(env, control, data_dir, &mut journal) else {
        return Ok(());
    };
    if error == CANCELLED || control.cancelled() {
        env.emit("rollback", "Отменяю перенос", 0, 0);
        if let Err(problem) = abandon_models(data_dir, &journal) {
            shell_log!("отмена переноса моделей: {problem}");
        }
        env.drain(data_dir);
        return Err(CANCELLED_MODELS.into());
    }
    shell_log!("перенос моделей прерван: {error}");
    env.emit("rollback", "Возвращаю прежнюю папку моделей", 0, 0);
    if let Err(problem) = interrupt_models(data_dir, &journal) {
        shell_log!("прерванный перенос моделей не отмечен: {problem}");
    }
    Err(error)
}

fn models_steps(
    env: &impl ModelsEnv,
    control: &MoveControl,
    data_dir: &Path,
    journal: &mut ModelsJournal,
) -> Result<(), String> {
    env.emit("models", "Копирование моделей", 0, 0);
    env.copy_models_to(journal.to.as_deref())?;
    cancel_check(control)?;

    advance_models(env, data_dir, journal, Phase::Switching, "switching")?;
    hold_and_latch(env, control)?;
    env.emit(
        "switching",
        "Перезапуск службы записи с новой папкой моделей",
        0,
        0,
    );
    if let Err(error) = write_models_pointer(data_dir, journal.to.as_deref()) {
        env.release();
        return Err(format!("Не удалось записать выбор папки моделей: {error}"));
    }
    // Из общего кэша HF переехали — окно спросит, удалить ли там модели Meet.
    let leftovers = journal.from.is_none() && default_is_shared(data_dir);
    if leftovers {
        let _ = write_atomic(&data_dir.join(LEFTOVERS), "{}");
    }
    env.checkpoint("pointer");
    if !env.restart_with_models(&journal.to_dir) {
        shell_log!("резидент с новой папкой моделей не поднялся — возвращаю прежнюю");
        let _ = write_models_pointer(data_dir, journal.from.as_deref());
        if leftovers {
            let _ = remove_file(&data_dir.join(LEFTOVERS));
        }
        env.respawn();
        return Err(START_FAILED_MODELS.into());
    }

    advance_models(env, data_dir, journal, Phase::Cleanup, "cleanup")?;
    env.emit("cleanup", "Удаление моделей из прежней папки", 0, 0);
    if let Err(error) = finish_models(data_dir, journal) {
        // Не сбой переноса: всё уже работает с новой папкой.
        shell_log!("прежние модели удалены не все: {error} — повторю при следующем запуске");
    }
    Ok(())
}
