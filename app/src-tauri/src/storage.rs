// Где хранить движок и модели (0.3.3): выбранная папка вместо системного
// диска — на C: у многих тесно, на Mac — внешний диск.
//
// Выбор лежит в `<data_dir>\storage.json` (`{"version": 1, "root": "E:\\Meet"}`):
// оболочке надо знать, где движок, до запуска резидента, а резидент (Python,
// `meet.paths.storage_root`) читает тот же файл — одна правда на оба
// процесса. Нет файла — всё как до 0.3.3: движок и GigaAM в папке данных,
// модели Hugging Face — в общем кэше. Файл есть, но не прочитан (оборванная
// запись, чужая версия) — не «по умолчанию», а отдельное состояние: движок на
// системном диске перенос уже удалил, решает человек. Раскладка папки:
//
//     <папка>\engine\<версия>   окружение движка (ставится заново: venv на
//                               Windows не переносится — в скриптах пути)
//     <папка>\uv-cache          кэш uv установок в эту папку
//     <папка>\models\hf         свой кэш HF: только модели каталога Meet
//     <папка>\models\gigaam     веса GigaAM
//     <папка>\models\xet        кэш кусков загрузчика Xet
//
// Перенос (`execute`) идёт по шагам журнала `storage-move.json`:
//
//   engine     движок этой версии ставится в новую папку (резидент работает
//              из прежней);
//   models     модели Meet копируются и сверяются (`python -m meet.storage
//              copy` — Python уже нового движка; повтор докопирует только
//              недостающее);
//   switching  резидент удерживается (`POST /storage/hold`: ничего не идёт, и
//              новое не начнётся), пишется `storage.json`, резидент
//              перезапускается из новой папки;
//   cleanup    новый резидент ответил — прежние движок и модели Meet
//              удаляются (общий кэш HF — только с согласия, вопрос в окне).
//
// Сбой и восстановление. До `cleanup` перенос ПРЕРЫВАЕТСЯ: `storage.json`
// возвращается на прежнюю папку (она цела и рабочая), журнал становится
// `interrupted`, из новой папки убирается только недостроенный движок, а
// проверенные копии моделей и кэш uv остаются. Окно предлагает «Продолжить»
// (копирование докопирует недостающее, движок соберётся из кэша uv) или
// «Отменить» (тогда новая папка очищается). Так же — при сбое шага в работе
// (кроме отмены человеком: она очищает сразу). После `cleanup` — ДОВЕДЕНИЕ:
// осталось только удалить прежнее, это идемпотентно.
//
// Удаление идёт через очередь `storage-discard.json` с повторами: занятые
// файлы (антивирус, проводник, недобитый резидент) не держат перенос
// «идущим» бесконечно. Перед удалением папки резидент, запущенный из неё,
// гасится. Удаляется только своё: `engine`, `uv-cache` и наши папки в
// `models`; чужая непустая папка (и корень диска) не выбирается — кладём в
// подпапку `Meet`, своя помечена файлом `.meet-storage`.

use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::engine;
use crate::logs::shell_log;

/// Выбранная папка движка и моделей (читает и Python, `meet.paths`).
pub const POINTER: &str = "storage.json";
/// Журнал переноса (Python по нему не качает модели, пока перенос идёт).
pub const JOURNAL: &str = "storage-move.json";
/// Очередь удаления недоделанного (с повторами).
pub const DISCARDS: &str = "storage-discard.json";
/// После переезда из общего кэша HF: окно спросит, удалить ли модели Meet из
/// него (ответ и удаление — резидент, `meet.storage.answer_leftovers`).
pub const LEFTOVERS: &str = "storage-leftovers.json";
/// Метка папки движка и моделей: в помеченной папке всё наше.
pub const MARK: &str = ".meet-storage";
/// Кэш uv в выбранной папке (`engine::uv_env`).
pub const UV_CACHE: &str = "uv-cache";
/// Версия формата файлов выбора, журнала и очереди.
const VERSION: u32 = 1;
/// Подпапка в выбранной непустой чужой папке и в корне диска.
const NEST: &str = "Meet";
const ENGINE: &str = "engine";
const MODELS: &str = "models";
/// Наши папки в `models`.
const OWN_MODELS: [&str; 3] = ["gigaam", "hf", "xet"];

/// Заголовок уведомления «папки нет» (резидент не запускается).
pub const MISSING_TITLE: &str = "Папка движка и моделей недоступна";
pub const UNREADABLE_TITLE: &str = "Файл выбора папки движка повреждён";
pub const CANCELLED: &str = "Перенос отменён — движок и модели остались на прежнем месте";
pub const START_FAILED: &str = "Служба записи не запустилась из новой папки — движок и модели \
                                остались на прежнем месте. Подробности — в журнале";
/// Запас места сверх движка и моделей, ГБ.
const MARGIN_GB: f64 = 0.5;
/// Как часто спрашивать удержание, пока резидент занят.
pub const IDLE_POLL: Duration = Duration::from_secs(2);
/// Длиннее этого путь к папке без длинных путей Windows не берём: torch и
/// пакеты nvidia добавляют к нему ~150 символов, предел — 260.
#[cfg_attr(not(windows), allow(dead_code))]
const LONG_PATH_LIMIT: usize = 100;

// --- выбранная папка ------------------------------------------------------------

/// Что лежит в `storage.json`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pointer {
    /// Файла нет — по умолчанию.
    Absent,
    Root(PathBuf),
    /// Файл есть, но не прочитан (оборванная запись, чужая версия, плохой путь).
    Unreadable,
}

pub fn pointer(data_dir: &Path) -> Pointer {
    let raw = match fs::read_to_string(data_dir.join(POINTER)) {
        Ok(raw) => raw,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Pointer::Absent,
        Err(_) => return Pointer::Unreadable,
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&raw) else {
        return Pointer::Unreadable;
    };
    let version = value
        .get("version")
        .map_or(Some(u64::from(VERSION)), serde_json::Value::as_u64);
    if version != Some(u64::from(VERSION)) {
        return Pointer::Unreadable;
    }
    let text = value
        .get("root")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .trim();
    let path = PathBuf::from(text);
    if text.is_empty() || !path.is_absolute() {
        return Pointer::Unreadable;
    }
    Pointer::Root(path)
}

/// Выбранная папка; нет выбора или он не прочитан — `None`.
pub fn root(data_dir: &Path) -> Option<PathBuf> {
    match pointer(data_dir) {
        Pointer::Root(root) => Some(root),
        _ => None,
    }
}

/// Папка, в которой лежат `engine` и `models`.
pub fn home(data_dir: &Path) -> PathBuf {
    root(data_dir).unwrap_or_else(|| data_dir.to_path_buf())
}

/// Почему движок и модели сейчас недоступны.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Blocked {
    /// Выбранной папки нет (внешний диск отключён, папку переименовали).
    Missing(PathBuf),
    /// `storage.json` не прочитан.
    Unreadable,
}

pub fn blocked(data_dir: &Path) -> Option<Blocked> {
    match pointer(data_dir) {
        Pointer::Absent => None,
        Pointer::Unreadable => Some(Blocked::Unreadable),
        Pointer::Root(root) => (!root.is_dir()).then_some(Blocked::Missing(root)),
    }
}

/// Ставить и запускать движок можно: папка на месте и выбор прочитан.
pub fn ready(data_dir: &Path) -> Result<(), String> {
    match blocked(data_dir) {
        None => Ok(()),
        Some(Blocked::Missing(path)) => Err(format!("{MISSING_TITLE}: {}", path.display())),
        Some(Blocked::Unreadable) => Err(UNREADABLE_TITLE.to_string()),
    }
}

/// Записать надёжно: во временный файл, на диск (`sync_all`), переименовать;
/// на unix — ещё и запись о переименовании в папке. Выключение питания
/// посреди не оставит пустой `storage.json`.
pub fn write_atomic(path: &Path, text: &str) -> io::Result<()> {
    let staged = path.with_extension("json.tmp");
    {
        let mut file = File::create(&staged)?;
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
    }
    fs::rename(&staged, path)?;
    #[cfg(unix)]
    if let Some(parent) = path.parent() {
        if let Ok(dir) = File::open(parent) {
            let _ = dir.sync_all();
        }
    }
    Ok(())
}

pub fn remove_file(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error),
        _ => Ok(()),
    }
}

/// Записать выбор (`None` — по умолчанию: файла нет).
pub fn write_pointer(data_dir: &Path, root: Option<&Path>) -> io::Result<()> {
    let path = data_dir.join(POINTER);
    match root {
        None => remove_file(&path),
        Some(root) => {
            let text = serde_json::json!({ "version": VERSION, "root": root.to_string_lossy() })
                .to_string();
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

/// Корень диска (`E:\`, `/`, `/Volumes/Stick`): в нём всегда подпапка `Meet`.
pub fn volume_root(path: &Path) -> bool {
    let real = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    real.parent().is_none() || real.parent() == Some(Path::new("/Volumes"))
}

/// Папка движка и моделей для выбранной человеком: пустая, несуществующая или
/// уже наша (метка) — она сама; папка данных — она сама; корень диска и чужая
/// непустая — подпапка `Meet`: чужие файлы не перемешиваются с нашими, и
/// очистка не заденет их.
pub fn resolve_target(picked: &Path, data_dir: &Path) -> PathBuf {
    if same_path(picked, data_dir) {
        return data_dir.to_path_buf();
    }
    if !volume_root(picked) && (picked.join(MARK).is_file() || empty_or_absent(picked)) {
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
    vet::volume(target)
}

/// Места нужно, ГБ: движок, модели и запас.
pub fn needs_gb(engine_gb: f64, models_bytes: u64) -> f64 {
    engine_gb + models_bytes as f64 / f64::from(1u32 << 30) + MARGIN_GB
}

/// Сколько байт уже лежит под папкой (докопированное при «Продолжить»).
pub fn dir_bytes(dir: &Path) -> u64 {
    let Ok(entries) = fs::read_dir(dir) else {
        return 0;
    };
    entries
        .filter_map(Result::ok)
        .map(|entry| match entry.file_type() {
            Ok(kind) if kind.is_dir() => dir_bytes(&entry.path()),
            Ok(kind) if kind.is_file() => entry.metadata().map(|m| m.len()).unwrap_or(0),
            _ => 0,
        })
        .sum()
}

/// Проверка тома папки: облако, сеть, файловая система, длина пути.
pub mod vet {
    use std::path::{Path, PathBuf};

    pub const CLOUD: &str = "Папка в облачном хранилище (OneDrive, iCloud и т. п.): облако \
                             выгружает и блокирует файлы движка и моделей. Выберите папку вне \
                             облака";
    pub const NETWORK: &str = "Сетевая папка не подходит: после перезагрузки она бывает \
                               недоступна, а движок и модели по сети грузятся медленно. Выберите \
                               локальный или внешний диск";
    #[cfg_attr(not(windows), allow(dead_code))]
    pub const FAT: &str = "Диск в FAT32: файлы больше 4 ГБ на нём не помещаются. Выберите диск \
                           NTFS или exFAT";
    #[cfg_attr(windows, allow(dead_code))]
    pub const NO_SYMLINKS: &str = "На этом диске нельзя создать символическую ссылку (exFAT или \
                                   FAT?) — окружению движка они нужны. Выберите диск APFS или Mac \
                                   OS Extended";
    #[cfg_attr(not(windows), allow(dead_code))]
    pub const TOO_LONG: &str = "Путь к папке слишком длинный: файлы движка в нём превысят предел \
                                Windows в 260 символов. Выберите папку ближе к корню диска (до \
                                100 символов в пути) или включите длинные пути Windows";

    /// Путь внутри одной из облачных папок (или совпадает с ней).
    pub fn under_cloud(path: &Path, roots: &[PathBuf]) -> bool {
        roots
            .iter()
            .any(|root| super::same_path(path, root) || super::inside(path, root))
    }

    /// FAT12/16/32 по имени файловой системы (`GetVolumeInformationW`).
    #[cfg_attr(not(windows), allow(dead_code))]
    pub fn fat(name: &str) -> bool {
        matches!(name.trim().to_ascii_uppercase().as_str(), "FAT" | "FAT32")
    }

    #[cfg_attr(not(windows), allow(dead_code))]
    pub fn too_long(path: &Path, long_paths: bool) -> bool {
        !long_paths && path.to_string_lossy().chars().count() > super::LONG_PATH_LIMIT
    }

    fn existing(path: &Path) -> PathBuf {
        path.ancestors()
            .find(|dir| dir.exists())
            .unwrap_or(path)
            .to_path_buf()
    }

    #[cfg(windows)]
    pub fn volume(target: &Path) -> Result<(), String> {
        use std::path::{Component, Prefix};

        let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
        let text = target.to_string_lossy();
        if text.starts_with(r"\\") && !text.starts_with(r"\\?\") {
            return Err(NETWORK.into());
        }
        let real = std::fs::canonicalize(existing(target)).unwrap_or_else(|_| existing(target));
        let letter = match real.components().next() {
            Some(Component::Prefix(prefix)) => match prefix.kind() {
                Prefix::Disk(letter) | Prefix::VerbatimDisk(letter) => Some(letter as char),
                Prefix::UNC(..) | Prefix::VerbatimUNC(..) => return Err(NETWORK.into()),
                _ => None,
            },
            _ => None,
        };
        let cloud: Vec<PathBuf> = ["OneDrive", "OneDriveConsumer", "OneDriveCommercial"]
            .iter()
            .filter_map(|name| std::env::var_os(name).filter(|value| !value.is_empty()))
            .map(PathBuf::from)
            .collect();
        if under_cloud(target, &cloud) || cloud_attributes(target) {
            return Err(CLOUD.into());
        }
        if let Some(letter) = letter {
            let root = wide(&format!("{letter}:\\"));
            // SAFETY: строка с нулём на конце живёт до конца вызова.
            let kind =
                unsafe { windows_sys::Win32::Storage::FileSystem::GetDriveTypeW(root.as_ptr()) };
            // DRIVE_REMOTE (WindowsProgramming) — без лишней фичи windows-sys.
            if kind == 4 {
                return Err(NETWORK.into());
            }
            if filesystem(&root).is_some_and(|name| fat(&name)) {
                return Err(FAT.into());
            }
        }
        if too_long(target, long_paths_enabled()) {
            return Err(TOO_LONG.into());
        }
        Ok(())
    }

    /// Файлы по требованию облака (OneDrive, Dropbox через Cloud Files API):
    /// у папки или её родителей — атрибуты «выгружается/закреплено».
    #[cfg(windows)]
    fn cloud_attributes(target: &Path) -> bool {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::{
            GetFileAttributesW, FILE_ATTRIBUTE_PINNED, FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
            FILE_ATTRIBUTE_UNPINNED,
        };
        let cloud =
            FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | FILE_ATTRIBUTE_PINNED | FILE_ATTRIBUTE_UNPINNED;
        target.ancestors().filter(|dir| dir.exists()).any(|dir| {
            let wide: Vec<u16> = dir.as_os_str().encode_wide().chain(Some(0)).collect();
            // SAFETY: строка с нулём на конце живёт до конца вызова.
            let attributes = unsafe { GetFileAttributesW(wide.as_ptr()) };
            attributes != u32::MAX && attributes & cloud != 0
        })
    }

    #[cfg(windows)]
    fn filesystem(root: &[u16]) -> Option<String> {
        use windows_sys::Win32::Storage::FileSystem::GetVolumeInformationW;
        let mut name = [0u16; 64];
        // SAFETY: корень — строка с нулём; буфер имени ФС — на 64 символа,
        // остальные выходы не нужны (null).
        let ok = unsafe {
            GetVolumeInformationW(
                root.as_ptr(),
                std::ptr::null_mut(),
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                name.as_mut_ptr(),
                name.len() as u32,
            )
        };
        let end = name.iter().position(|c| *c == 0).unwrap_or(name.len());
        (ok != 0).then(|| String::from_utf16_lossy(&name[..end]))
    }

    /// `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled` = 1.
    #[cfg(windows)]
    fn long_paths_enabled() -> bool {
        use windows_sys::Win32::Foundation::ERROR_SUCCESS;
        use windows_sys::Win32::System::Registry::{
            RegGetValueW, HKEY_LOCAL_MACHINE, RRF_RT_REG_DWORD,
        };
        let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
        let key = wide(r"SYSTEM\CurrentControlSet\Control\FileSystem");
        let name = wide("LongPathsEnabled");
        let mut value: u32 = 0;
        let mut size = std::mem::size_of::<u32>() as u32;
        // SAFETY: строки с нулём на конце и буфер под DWORD живут до конца вызова.
        let status = unsafe {
            RegGetValueW(
                HKEY_LOCAL_MACHINE,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_DWORD,
                std::ptr::null_mut(),
                (&mut value as *mut u32).cast(),
                &mut size,
            )
        };
        status == ERROR_SUCCESS && value == 1
    }

    #[cfg(unix)]
    pub fn volume(target: &Path) -> Result<(), String> {
        let place = existing(target);
        if let Some(home) = std::env::var_os("HOME").filter(|home| !home.is_empty()) {
            let home = PathBuf::from(home);
            let cloud = [
                home.join("Library").join("CloudStorage"),
                home.join("Library").join("Mobile Documents"),
            ];
            if under_cloud(target, &cloud) {
                return Err(CLOUD.into());
            }
        }
        if !local(&place) {
            return Err(NETWORK.into());
        }
        if !symlinks_work(&place) {
            return Err(NO_SYMLINKS.into());
        }
        Ok(())
    }

    /// Том локальный (statfs: MNT_LOCAL); не узнать — не мешаем.
    #[cfg(target_os = "macos")]
    fn local(place: &Path) -> bool {
        use std::os::unix::ffi::OsStrExt;
        let mut bytes = place.as_os_str().as_bytes().to_vec();
        bytes.push(0);
        // SAFETY: строка с нулём на конце живёт до конца вызова; statfs пишет
        // в локальную структуру.
        let mut stat: libc::statfs = unsafe { std::mem::zeroed() };
        if unsafe { libc::statfs(bytes.as_ptr().cast(), &mut stat) } != 0 {
            return true;
        }
        stat.f_flags & (libc::MNT_LOCAL as u32) != 0
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    fn local(_place: &Path) -> bool {
        true
    }

    /// Создать и убрать символическую ссылку: проверка делом, а не по имени
    /// файловой системы (ловит и exFAT, и SMB, и NTFS-3G).
    #[cfg(unix)]
    pub fn symlinks_work(place: &Path) -> bool {
        let probe = place.join(format!(".meet-symlink-probe-{}", std::process::id()));
        let _ = std::fs::remove_file(&probe);
        let ok = std::os::unix::fs::symlink("meet-probe-target", &probe).is_ok();
        let _ = std::fs::remove_file(&probe);
        ok
    }
}

// --- журнал ---------------------------------------------------------------------

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Engine,
    Models,
    Switching,
    Cleanup,
    /// Прерван до переключения: выбор — прежний, ждём «Продолжить»/«Отменить».
    Interrupted,
}

impl Phase {
    /// Перенос идёт (до переключения): модели не качаются.
    pub fn moving(self) -> bool {
        matches!(self, Phase::Engine | Phase::Models | Phase::Switching)
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Journal {
    #[serde(default = "version")]
    pub version: u32,
    /// Прежняя выбранная папка; `None` — по умолчанию (папка данных и общий кэш).
    pub from: Option<PathBuf>,
    pub to: PathBuf,
    pub phase: Phase,
}

fn version() -> u32 {
    VERSION
}

impl Journal {
    pub fn new(from: Option<PathBuf>, to: PathBuf, phase: Phase) -> Self {
        Journal {
            version: VERSION,
            from,
            to,
            phase,
        }
    }

    fn previous_home(&self, data_dir: &Path) -> PathBuf {
        self.from.clone().unwrap_or_else(|| data_dir.to_path_buf())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum JournalState {
    Absent,
    Ok(Journal),
    /// Файл есть, но не прочитан.
    Bad,
}

pub fn journal_state(data_dir: &Path) -> JournalState {
    match fs::read_to_string(data_dir.join(JOURNAL)) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => JournalState::Absent,
        Err(_) => JournalState::Bad,
        Ok(raw) => match serde_json::from_str::<Journal>(&raw) {
            Ok(journal) if journal.version == VERSION => JournalState::Ok(journal),
            _ => JournalState::Bad,
        },
    }
}

pub fn read_journal(data_dir: &Path) -> Option<Journal> {
    match journal_state(data_dir) {
        JournalState::Ok(journal) => Some(journal),
        _ => None,
    }
}

pub fn write_journal(data_dir: &Path, journal: &Journal) -> Result<(), String> {
    let text = serde_json::to_string_pretty(journal).unwrap_or_default();
    write_atomic(&data_dir.join(JOURNAL), &text)
        .map_err(|error| format!("Не удалось записать журнал переноса: {error}"))
}

/// Нечитаемый журнал — в `storage-move.json.bad`: правда — `storage.json`, а
/// резидент иначе вечно считал бы перенос идущим и не качал модели.
pub fn quarantine_journal(data_dir: &Path) {
    let path = data_dir.join(JOURNAL);
    let bad = data_dir.join(format!("{JOURNAL}.bad"));
    let _ = remove_file(&bad);
    match fs::rename(&path, &bad) {
        Ok(()) => shell_log!("журнал переноса не прочитан — отложен в {}", bad.display()),
        Err(error) => shell_log!("журнал переноса не прочитан и не отложен: {error}"),
    }
}

// --- очередь удаления -----------------------------------------------------------

/// Что удалить в папке.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Scope {
    /// Только недостроенный движок (прерванный перенос: модели и кэш uv
    /// остаются для «Продолжить»).
    Engine,
    /// Всё наше в папке (отмена переноса).
    All,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Discard {
    pub path: PathBuf,
    pub scope: Scope,
}

#[derive(Serialize, Deserialize, Default)]
struct Discards {
    #[serde(default = "version")]
    version: u32,
    #[serde(default)]
    items: Vec<Discard>,
}

/// Очередь и уборка — по одной: «Продолжить» не начнёт ставить движок в
/// папку, из которой его сейчас удаляют.
static DRAIN: Mutex<()> = Mutex::new(());

fn drain_lock() -> std::sync::MutexGuard<'static, ()> {
    DRAIN
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn read_discards(data_dir: &Path) -> Vec<Discard> {
    fs::read_to_string(data_dir.join(DISCARDS))
        .ok()
        .and_then(|raw| serde_json::from_str::<Discards>(&raw).ok())
        .map(|discards| discards.items)
        .unwrap_or_default()
}

fn write_discards(data_dir: &Path, items: &[Discard]) {
    let path = data_dir.join(DISCARDS);
    let result = if items.is_empty() {
        remove_file(&path)
    } else {
        let text = serde_json::to_string_pretty(&Discards {
            version: VERSION,
            items: items.to_vec(),
        })
        .unwrap_or_default();
        write_atomic(&path, &text)
    };
    if let Err(error) = result {
        shell_log!("очередь удаления не записалась: {error}");
    }
}

/// Поставить папку в очередь удаления (`All` поглощает `Engine`).
pub fn queue_discard(data_dir: &Path, path: &Path, scope: Scope) {
    let _lock = drain_lock();
    let mut items = read_discards(data_dir);
    if let Some(item) = items.iter_mut().find(|item| same_path(&item.path, path)) {
        if scope == Scope::All {
            item.scope = Scope::All;
        }
    } else {
        items.push(Discard {
            path: path.to_path_buf(),
            scope,
        });
    }
    write_discards(data_dir, &items);
}

/// Папку снова берут под перенос («Продолжить», та же папка) — не удалять.
pub fn unqueue_discard(data_dir: &Path, path: &Path) {
    let _lock = drain_lock();
    let items: Vec<Discard> = read_discards(data_dir)
        .into_iter()
        .filter(|item| !same_path(&item.path, path))
        .collect();
    write_discards(data_dir, &items);
}

pub fn discards_pending(data_dir: &Path) -> bool {
    !read_discards(data_dir).is_empty()
}

fn remove_tree(path: &Path, errors: &mut Vec<String>) {
    match fs::remove_dir_all(path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => errors.push(format!("{}: {error}", path.display())),
    }
}

/// Убрать из папки `home` наше: движок и GigaAM; `whole` — ещё свой кэш HF,
/// Xet, кэш uv, метку и пустые папки. В папке данных — без кэша uv и самой
/// папки: журналы, настройки и записи там же.
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
        remove_tree(&home.join(UV_CACHE), &mut errors);
        let _ = remove_file(&home.join(MARK));
        let _ = fs::remove_dir(home); // только пустую: чужие файлы — его
    }
    errors
}

/// Один проход очереди удаления; → сколько осталось (файлы заняты). Живую
/// папку (`home`) и папку, в которой она лежит, не трогает никогда; перед
/// удалением гасит резидент, запущенный из удаляемой папки.
pub fn drain_once(data_dir: &Path, env: &impl Recover) -> usize {
    let _lock = drain_lock();
    let live = home(data_dir);
    let mut left = Vec::new();
    for item in read_discards(data_dir) {
        if same_path(&item.path, &live) || inside(&live, &item.path) {
            shell_log!("{} — живая папка движка, не удаляю", item.path.display());
            continue;
        }
        if !item.path.exists() {
            continue;
        }
        env.stop_resident_in(&item.path);
        let errors = match item.scope {
            Scope::Engine => {
                let mut errors = Vec::new();
                remove_tree(&item.path.join(ENGINE), &mut errors);
                errors
            }
            Scope::All => remove_ours(&item.path, true, data_dir),
        };
        if errors.is_empty() {
            shell_log!("убрано недоделанное в {}", item.path.display());
        } else {
            shell_log!("убрано не всё ({}), повторю позже", errors.join("; "));
            left.push(item);
        }
    }
    write_discards(data_dir, &left);
    left.len()
}

// --- восстановление и откат -----------------------------------------------------

/// Чем восстановлению нужен мир вокруг (в тестах — подделка).
pub trait Recover {
    /// Погасить резидент, запущенный из `dir` (его движок там), и дождаться
    /// выхода — до удаления папки.
    fn stop_resident_in(&self, dir: &Path);
}

/// Прервать перенос: выбор — прежний, журнал — `interrupted`, из новой папки
/// уходит только движок (модели и кэш uv ждут «Продолжить»). Идемпотентно.
pub fn interrupt(data_dir: &Path, journal: &Journal) -> Result<(), String> {
    write_pointer(data_dir, journal.from.as_deref())
        .map_err(|error| format!("Не удалось вернуть прежнюю папку: {error}"))?;
    if journal.from.is_none() {
        let _ = remove_file(&data_dir.join(LEFTOVERS));
    }
    let mut stopped = journal.clone();
    stopped.phase = Phase::Interrupted;
    write_journal(data_dir, &stopped)?;
    if !same_path(&journal.to, &journal.previous_home(data_dir)) {
        queue_discard(data_dir, &journal.to, Scope::Engine);
    }
    Ok(())
}

/// Отменить перенос: выбор — прежний, новая папка — в очередь на очистку,
/// журнала нет.
pub fn abandon(data_dir: &Path, journal: &Journal) -> Result<(), String> {
    write_pointer(data_dir, journal.from.as_deref())
        .map_err(|error| format!("Не удалось вернуть прежнюю папку: {error}"))?;
    if journal.from.is_none() {
        let _ = remove_file(&data_dir.join(LEFTOVERS));
    }
    if !same_path(&journal.to, &journal.previous_home(data_dir)) {
        queue_discard(data_dir, &journal.to, Scope::All);
    }
    remove_file(&data_dir.join(JOURNAL)).map_err(|error| error.to_string())
}

/// Довести перенос: удалить прежние движок и модели Meet (общий кэш HF не
/// трогаем — о нём спросит окно). Не удалилось (файлы заняты) — журнал
/// остаётся, следующий запуск попробует снова.
pub fn finish(data_dir: &Path, journal: &Journal, env: &impl Recover) -> Result<(), String> {
    let from = journal.previous_home(data_dir);
    if !same_path(&from, &journal.to) && !same_path(&from, &home(data_dir)) {
        if from.is_dir() {
            env.stop_resident_in(&from);
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

/// Быстрая часть восстановления при старте оболочки (до надзора): журнал
/// откладывается, если не прочитан; перенос, прерванный до переключения,
/// становится `interrupted` (выбор — прежний). → журнал `cleanup`, который
/// осталось довести (`recover_slow`).
pub fn recover_journal(data_dir: &Path) -> Option<Journal> {
    match journal_state(data_dir) {
        JournalState::Absent => None,
        JournalState::Bad => {
            quarantine_journal(data_dir);
            None
        }
        JournalState::Ok(journal) => match journal.phase {
            Phase::Engine | Phase::Models | Phase::Switching => {
                shell_log!(
                    "перенос движка и моделей прерван на шаге {:?} — прежняя папка снова главная, \
                     скопированное ждёт «Продолжить»",
                    journal.phase
                );
                if let Err(error) = interrupt(data_dir, &journal) {
                    shell_log!("прерванный перенос не отмечен: {error}");
                }
                None
            }
            Phase::Cleanup => Some(journal),
            Phase::Interrupted => None,
        },
    }
}

/// Медленная часть: довести уборку после переключения и пройти очередь
/// удаления. → сколько осталось в очереди.
pub fn recover_slow(data_dir: &Path, cleanup: Option<Journal>, env: &impl Recover) -> usize {
    if let Some(journal) = cleanup {
        match finish(data_dir, &journal, env) {
            Ok(()) => shell_log!("прежние движок и модели удалены"),
            Err(error) => shell_log!("прежние движок и модели удалены не все: {error}"),
        }
    }
    drain_once(data_dir, env)
}

/// Внешний резидент можно подхватить: его окружения не узнать (старая версия
/// без `/storage`), это не движок Meet (разработка из `.venv`) или движок в
/// нынешней папке. Резидент из движка другой папки (недобитый после
/// прерванного переноса) — нельзя: его папку удаляют.
pub fn adoptable(prefix: Option<&Path>, home: &Path) -> bool {
    let Some(prefix) = prefix else {
        return true;
    };
    let meet_engine = prefix
        .parent()
        .and_then(Path::file_name)
        .is_some_and(|name| name == ENGINE)
        && prefix.join("installed.json").is_file();
    !meet_engine || inside(prefix, &engine::engine_root(home))
}

// --- перенос --------------------------------------------------------------------

/// Отмена переноса: до «защёлки» перед переключением её можно нажать, после —
/// нет. Одно атомарное состояние: нажатие и защёлка не разойдутся.
pub struct MoveControl(AtomicU8);

const OPEN: u8 = 0;
const CANCEL_ASKED: u8 = 1;
const LATCHED: u8 = 2;

impl MoveControl {
    pub const fn new() -> Self {
        MoveControl(AtomicU8::new(OPEN))
    }

    pub fn reset(&self) {
        self.0.store(OPEN, Ordering::SeqCst);
    }

    /// «Отменить»; `false` — уже поздно (переключение началось) или нажато.
    pub fn cancel(&self) -> bool {
        self.0
            .compare_exchange(OPEN, CANCEL_ASKED, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
    }

    pub fn cancelled(&self) -> bool {
        self.0.load(Ordering::SeqCst) == CANCEL_ASKED
    }

    pub fn cancellable(&self) -> bool {
        self.0.load(Ordering::SeqCst) == OPEN
    }

    /// Перед переключением: дальше отмены нет. `true` — её успели нажать.
    fn latch(&self) -> bool {
        self.0.swap(LATCHED, Ordering::SeqCst) == CANCEL_ASKED
    }
}

impl Default for MoveControl {
    fn default() -> Self {
        Self::new()
    }
}

/// Чем переносу нужен мир вокруг: установщик, копирование, резидент, окно.
/// Настоящий — `storage_app::AppEnv`, в тестах — подделка.
pub trait MoveEnv: Recover {
    fn install(&self, home: &Path, profile: &str) -> Result<(), String>;
    fn copy_models(&self, to: &Path) -> Result<(), String>;
    /// Удержать резидент перед переключением: `Ok(None)` — удержан, `Ok(Some)`
    /// — занят (чем), `Err` — не ответил.
    fn hold(&self) -> Result<Option<String>, String>;
    /// Снять удержание (переключение сорвалось до перезапуска).
    fn release(&self);
    /// Перезапустить резидент и дождаться, что он поднялся из окружения `env`.
    fn restart_from(&self, env: &Path) -> bool;
    /// Перезапустить резидент (возврат на прежнюю папку), не дожидаясь.
    fn respawn(&self);
    fn emit(&self, phase: &'static str, text: &str, done: u64, total: u64);
    fn pause(&self, duration: Duration);
    /// Убрать очередь удаления (настоящий — в фоне с повторами).
    fn drain(&self, data_dir: &Path);
    /// Точка «процесс убит» для тестов: после каждой записи журнала и выбора.
    fn checkpoint(&self, _what: &'static str) {}
}

fn advance(
    env: &impl MoveEnv,
    data_dir: &Path,
    journal: &mut Journal,
    phase: Phase,
    what: &'static str,
) -> Result<(), String> {
    journal.phase = phase;
    write_journal(data_dir, journal)?;
    env.checkpoint(what);
    Ok(())
}

/// Перенести движок и модели по `journal` (`from` → `to`, шаг `engine`).
/// Сбой до переключения — перенос прерван (`interrupt`), отмена — очищен
/// (`abandon`); выбор в обоих случаях прежний.
pub fn execute(
    env: &impl MoveEnv,
    control: &MoveControl,
    data_dir: &Path,
    mut journal: Journal,
    profile: &str,
    version: &str,
) -> Result<(), String> {
    journal.phase = Phase::Engine;
    write_journal(data_dir, &journal)?;
    env.checkpoint("engine");
    let Err(error) = steps(env, control, data_dir, &mut journal, profile, version) else {
        return Ok(());
    };
    if error == CANCELLED || control.cancelled() {
        env.emit("rollback", "Отменяю перенос", 0, 0);
        if let Err(problem) = abandon(data_dir, &journal) {
            shell_log!("отмена переноса: {problem}");
        }
        env.drain(data_dir);
        return Err(CANCELLED.into());
    }
    shell_log!("перенос движка и моделей прерван: {error}");
    env.emit("rollback", "Возвращаю прежнюю папку", 0, 0);
    if let Err(problem) = interrupt(data_dir, &journal) {
        shell_log!("прерванный перенос не отмечен: {problem}");
    }
    env.drain(data_dir);
    Err(error)
}

fn cancel_check(control: &MoveControl) -> Result<(), String> {
    if control.cancelled() {
        Err(CANCELLED.into())
    } else {
        Ok(())
    }
}

fn steps(
    env: &impl MoveEnv,
    control: &MoveControl,
    data_dir: &Path,
    journal: &mut Journal,
    profile: &str,
    version: &str,
) -> Result<(), String> {
    env.emit("engine", "Установка движка в новую папку", 0, 0);
    env.install(&journal.to, profile)?;
    cancel_check(control)?;

    advance(env, data_dir, journal, Phase::Models, "models")?;
    env.emit("models", "Копирование моделей", 0, 0);
    env.copy_models(&journal.to)?;
    cancel_check(control)?;

    advance(env, data_dir, journal, Phase::Switching, "switching")?;
    // Удержание: под замком резидента проверено, что ничего не идёт, и новое
    // (запись, автозапись, ассистент, задачи, загрузки) не начнётся до
    // перезапуска.
    loop {
        cancel_check(control)?;
        match env.hold()? {
            None => break,
            Some(reason) => {
                env.emit("waiting", &format!("Жду, пока закончится: {reason}"), 0, 0);
                env.pause(IDLE_POLL);
            }
        }
    }
    if control.latch() {
        env.release();
        return Err(CANCELLED.into());
    }
    env.emit("switching", "Перезапуск службы записи из новой папки", 0, 0);
    if let Err(error) = write_pointer(data_dir, Some(&journal.to)) {
        env.release();
        return Err(format!("Не удалось записать выбор папки: {error}"));
    }
    if journal.from.is_none() {
        // Из общего кэша HF переехали — окно спросит, удалить ли там модели Meet.
        let _ = write_atomic(&data_dir.join(LEFTOVERS), "{}");
    }
    env.checkpoint("pointer");
    if !env.restart_from(&engine::env_dir(&journal.to, version)) {
        shell_log!("резидент из новой папки не поднялся — возвращаю прежнюю");
        let _ = write_pointer(data_dir, journal.from.as_deref());
        if journal.from.is_none() {
            let _ = remove_file(&data_dir.join(LEFTOVERS));
        }
        env.respawn();
        return Err(START_FAILED.into());
    }

    advance(env, data_dir, journal, Phase::Cleanup, "cleanup")?;
    env.emit(
        "cleanup",
        "Удаление движка и моделей из прежней папки",
        0,
        0,
    );
    if let Err(error) = finish(data_dir, journal, env) {
        // Не сбой переноса: всё уже работает из новой папки.
        shell_log!(
            "прежние движок и модели удалены не все: {error} — повторю при следующем запуске"
        );
    }
    Ok(())
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

/// Задержки повторов уборки: сразу, через 5 с, 30 с, 2 мин; дальше — при
/// следующем запуске.
pub const DRAIN_RETRIES: [Duration; 4] = [
    Duration::from_secs(0),
    Duration::from_secs(5),
    Duration::from_secs(30),
    Duration::from_secs(120),
];

#[cfg(test)]
mod tests;
