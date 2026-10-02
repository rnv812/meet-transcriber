// Движок расшифровки: приватное окружение Python, которое оболочка ставит
// через uv при первом запуске (мастер вызывает команды внизу файла).
//
// Установщик (NSIS) несёт только uv.exe, ffmpeg.exe и колесо meet — тяжёлый
// стек (torch, faster-whisper, pyannote) скачивается на машине пользователя
// под её железо: CUDA-сборка для карты NVIDIA, CPU — иначе. Окружение живёт в
// `<data_dir>\engine\<версия приложения>`: новая версия приложения ставит
// своё окружение рядом, старое удаляется, когда резидент новой версии
// ответил (`resident.rs`). Python от uv — общий, в `engine\python`.
//
// Шаги установки — те же, что `meet.engine.uv_steps` в Python: тест сверяет
// их с фикстурами tests/fixtures/uv_steps_*.json.
//
// Маркер установки хранит хэш колеса meet: установщик, пересобранный под той
// же версией (rc → финальная), при старте переставляет только пакет meet
// (`Upkeep`), резидент ждёт этого и поднимается уже из нового кода.
//
// Повторный запуск установки после сбоя просто повторяет все шаги: скачанное
// лежит в кэше uv (`UV_CACHE_DIR` по умолчанию), второй раз из сети идёт
// только недокачанное.

use std::collections::VecDeque;
use std::ffi::{OsStr, OsString};
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

use crate::logs::{self, shell_log};
use crate::resident::{self, Supervisor};
use crate::tray;

/// Индексы колёс torch — как `TORCH_CUDA_INDEX`/`TORCH_CPU_INDEX` в
/// `meet/engine.py` (cu128: без неё не работают карты RTX 50xx).
pub const TORCH_CUDA_INDEX: &str = "https://download.pytorch.org/whl/cu128";
pub const TORCH_CPU_INDEX: &str = "https://download.pytorch.org/whl/cpu";
/// torch — одна минорная версия на оба профиля, как `TORCH_SPECS` в
/// `meet/engine.py` (без пина CPU-индекс отдавал 2.14, CUDA — 2.11).
pub const TORCH_SPECS: [&str; 2] = ["torch==2.11.*", "torchaudio==2.11.*"];

/// Событие прогресса: `{step, of, line}` — номер шага с 1, всего шагов,
/// строка вывода (первое событие шага — его название).
pub const PROGRESS_EVENT: &str = "engine-progress";
/// Событие сбоя шага: `{step, tail}` — номер шага и последние строки вывода.
pub const FAILED_EVENT: &str = "engine-failed";

const ENGINE: &str = "engine";
/// Python от uv — общий для всех версий окружения, `stale_envs` его не трогает.
const PYTHON: &str = "python";
/// Маркер законченной установки: пишется последним, после всех шагов.
const MARKER: &str = "installed.json";
/// Межпроцессный замок установки в `engine` (pid держателя).
const INSTALL_LOCK: &str = "install.lock";
const LAUNCHER: &str = "meet-tray.exe";
const WHEEL_PREFIX: &str = "meet_transcriber-";
const UV: &str = "uv.exe";
const FFMPEG: &str = "ffmpeg.exe";
/// Места на диске с данными, ГБ. Замер сухого прогона 01.10.2026 (uv 0.11.23,
/// torch 2.11 cu128): CUDA-окружение — 6,8 ГБ (torch 4,1 ГБ + nvidia-cublas/
/// cudnn 2 ГБ; кэш uv на том же диске — жёсткие ссылки, места не удваивает),
/// CPU — 1,3 ГБ; сверху запас на распаковку.
const NEEDS_CUDA_GB: f64 = 8.0;
const NEEDS_CPU_GB: f64 = 3.0;
const TAIL_LINES: usize = 30;
/// Не чаще 10 строк в секунду в окно: uv сыплет сотнями строк, журнал
/// получает все, интерфейсу хватает «что сейчас происходит».
const LINE_GAP: Duration = Duration::from_millis(100);
const GPU_TIMEOUT: Duration = Duration::from_secs(5);
/// Названия шагов `uv_steps` для окна установки — по порядку.
const STEP_TITLES: [&str; 4] = [
    "Загрузка Python 3.12",
    "Создание окружения движка",
    "Установка PyTorch",
    "Установка движка Meet",
];

pub const NO_UV: &str = "Установщик собран без движка: нет uv.exe";
pub const NO_WHEEL: &str = "Установщик собран без движка: нет колеса meet_transcriber";
pub const BUSY: &str = "Установка уже идёт";

pub fn engine_root(data_dir: &Path) -> PathBuf {
    data_dir.join(ENGINE)
}

pub fn env_dir(data_dir: &Path, version: &str) -> PathBuf {
    engine_root(data_dir).join(version)
}

pub fn python_dir(data_dir: &Path) -> PathBuf {
    engine_root(data_dir).join(PYTHON)
}

/// gui-script резидента в окружении движка.
pub fn launcher(env_dir: &Path) -> PathBuf {
    env_dir.join("Scripts").join(LAUNCHER)
}

/// Команды установки колеса в приватное окружение — порт
/// `meet.engine.uv_steps`. Путь к python склеивается строкой с «\», как в
/// Python, чтобы шаги совпадали байт в байт. `constraints` — файл точных
/// версий из ресурсов (`constraints-<профиль>.txt`, собирает
/// `build_release.ps1`): шаги 3 и 4 ставят ровно то, с чем собран релиз.
pub fn uv_steps(
    uv: &str,
    env_dir: &str,
    wheel: &str,
    profile: &str,
    constraints: Option<&str>,
) -> Vec<Vec<String>> {
    let python = format!(r"{env_dir}\Scripts\python.exe");
    let index = if profile == "cuda" {
        TORCH_CUDA_INDEX
    } else {
        TORCH_CPU_INDEX
    };
    let owned = |items: &[&str]| {
        items
            .iter()
            .map(|item| item.to_string())
            .collect::<Vec<_>>()
    };
    let pip = owned(&[uv, "pip", "install", "--python", &python]);
    let pinned = constraints
        .map(|file| owned(&["--constraint", file]))
        .unwrap_or_default();
    vec![
        owned(&[uv, "python", "install", "3.12"]),
        owned(&[uv, "venv", "--python", "3.12", env_dir]),
        [
            pip.clone(),
            owned(&TORCH_SPECS),
            owned(&["--index-url", index]),
            pinned.clone(),
        ]
        .concat(),
        [pip, vec![format!("{wheel}[engine-{profile}]")], pinned].concat(),
    ]
}

/// Файл точных версий профиля в ресурсах; нет (dev, старая сборка) — `None`.
pub fn constraints_file(resources: &Path, profile: &str) -> Option<PathBuf> {
    Some(resources.join(format!("constraints-{profile}.txt"))).filter(|file| file.is_file())
}

/// Окружение uv: всё своё — внутри папки приложения. Python ставится в
/// `engine\python` без ярлыков в `~\.local\bin` и без записи в реестр;
/// окружение строится только на этом Python, а не на системном (удалят
/// системный — движок не сломается). `UV_VENV_CLEAR`: uv 0.8+ отказывается
/// создавать venv поверх существующего, а повторная установка после сбоя
/// должна просто пройти все шаги заново. Он же делает правильной смену
/// профиля cpu↔cuda на той же версии: без очистки `uv pip install torch` с
/// другим индексом счёл бы стоящий torch подходящим и оставил чужую сборку.
/// Кэш uv — по умолчанию: на нём держится докачка, так что очистка стоит
/// только перекладки файлов из кэша.
pub fn uv_env(data_dir: &Path) -> Vec<(&'static str, OsString)> {
    vec![
        (
            "UV_PYTHON_INSTALL_DIR",
            python_dir(data_dir).into_os_string(),
        ),
        ("UV_PYTHON_INSTALL_BIN", "0".into()),
        ("UV_PYTHON_INSTALL_REGISTRY", "0".into()),
        ("UV_MANAGED_PYTHON", "1".into()),
        ("UV_VENV_CLEAR", "1".into()),
        // Чужой uv.toml (в профиле пользователя) не должен подменить индекс.
        ("UV_NO_CONFIG", "1".into()),
        // .pyc — при установке, а не при первом запуске: иначе первый
        // импорт torch и pyannote у резидента шёл ~50 с.
        ("UV_COMPILE_BYTECODE", "1".into()),
        ("UV_NO_PROGRESS", "1".into()),
        ("NO_COLOR", "1".into()),
    ]
}

/// Окружения других версий в `engine` — всё, кроме текущей и общего Python.
pub fn stale_envs(engine_root: &Path, current: &str) -> Vec<PathBuf> {
    let Ok(entries) = fs::read_dir(engine_root) else {
        return Vec::new();
    };
    let mut stale: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
        .filter(|entry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            !name.eq_ignore_ascii_case(current) && !name.eq_ignore_ascii_case(PYTHON)
        })
        .map(|entry| entry.path())
        .collect();
    stale.sort();
    stale
}

pub fn needs_gb(profile: &str) -> f64 {
    if profile == "cuda" {
        NEEDS_CUDA_GB
    } else {
        NEEDS_CPU_GB
    }
}

/// Места, когда тяжёлое уже лежит в кэше uv на том же диске: окружение
/// собирается жёсткими ссылками на кэш, новое на диске — мелочь. Без этого
/// повтор после сбоя, переустановка и обновление на тесном диске упирались
/// бы в те же 8 ГБ, хотя гигабайты уже скачаны и заняты кэшем.
const WARM_NEEDS_GB: f64 = 1.0;

pub fn needs_for(profile: &str, warm: bool) -> f64 {
    if warm {
        WARM_NEEDS_GB
    } else {
        needs_gb(profile)
    }
}

/// Кэш uv, которым пользуется установка: `UV_CACHE_DIR`, иначе умолчание uv
/// на Windows — `%LOCALAPPDATA%\uv\cache` (uv.toml установка не читает:
/// `UV_NO_CONFIG`).
pub fn uv_cache_dir(
    env_override: Option<&OsStr>,
    local_app_data: Option<&OsStr>,
) -> Option<PathBuf> {
    match env_override.filter(|dir| !dir.is_empty()) {
        Some(dir) => Some(PathBuf::from(dir)),
        None => local_app_data
            .filter(|dir| !dir.is_empty())
            .map(|dir| Path::new(dir).join("uv").join("cache")),
    }
}

/// В кэше uv — распакованный torch сборки профиля (`+cu…` или `+cpu`):
/// `wheels-v*\index\*\torch\2.11.0+cu128-cp312-…`. Этот файл-указатель uv
/// пишет после распаковки колеса; `.http`, `.msgpack`, `.lock` рядом —
/// метаданные и замки, они бывают и у недокачанного.
pub fn cache_has_torch(cache: &Path, profile: &str) -> bool {
    let tag = if profile == "cuda" { "+cu" } else { "+cpu" };
    let dirs = |dir: &Path| -> Vec<PathBuf> {
        fs::read_dir(dir)
            .map(|entries| {
                entries
                    .filter_map(Result::ok)
                    .map(|entry| entry.path())
                    .collect()
            })
            .unwrap_or_default()
    };
    dirs(cache)
        .into_iter()
        .filter(|dir| {
            dir.file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with("wheels-v"))
        })
        .flat_map(|wheels| dirs(&wheels.join("index")))
        .flat_map(|index| dirs(&index.join("torch")))
        .any(|entry| {
            let name = entry
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default();
            entry.is_file()
                && name.contains(tag)
                && ![".http", ".msgpack", ".lock"]
                    .iter()
                    .any(|suffix| name.ends_with(suffix))
        })
}

/// Один ли том у двух путей (жёсткие ссылки — только в пределах тома):
/// сравниваются буквы дисков или корни UNC.
pub fn same_volume(a: &Path, b: &Path) -> bool {
    use std::path::Component;
    match (a.components().next(), b.components().next()) {
        (Some(Component::Prefix(x)), Some(Component::Prefix(y))) => {
            x.as_os_str().eq_ignore_ascii_case(y.as_os_str())
        }
        _ => false,
    }
}

/// Законченный движок профиля (любой версии) в `engine`: его файлы —
/// жёсткие ссылки на кэш uv, новый движок того же профиля встанет почти
/// без нового места.
fn profile_env_present(engine_root: &Path, profile: &str) -> bool {
    stale_envs(engine_root, "")
        .iter()
        .filter(|env| launcher(env).is_file())
        .any(|env| read_marker(env).is_some_and(|marker| marker.profile == profile))
}

/// Сколько места нужно под движок профиля с учётом уже скачанного.
pub fn space_needed(data_dir: &Path, profile: &str, cache: Option<&Path>) -> f64 {
    let warm = profile_env_present(&engine_root(data_dir), profile)
        || cache
            .is_some_and(|cache| same_volume(cache, data_dir) && cache_has_torch(cache, profile));
    needs_for(profile, warm)
}

fn current_uv_cache() -> Option<PathBuf> {
    uv_cache_dir(
        std::env::var_os("UV_CACHE_DIR").as_deref(),
        std::env::var_os("LOCALAPPDATA").as_deref(),
    )
}

/// Профиль по видеокарте: NVIDIA видна — cuda, иначе cpu.
pub fn profile_for(gpu: Option<&str>) -> &'static str {
    if gpu.is_some() {
        "cuda"
    } else {
        "cpu"
    }
}

#[derive(Serialize, Deserialize)]
struct Marker {
    version: String,
    profile: String,
    installed_at: String,
    /// SHA-256 колеса meet, из которого собран движок. Пересобранный
    /// установщик той же версии (rc1 и финальная 0.1.0 делят `engine\0.1.0`)
    /// несёт другое колесо — по этому полю оболочка видит, что код движка
    /// устарел. У маркеров до него поля нет (`None`) — тоже «устарел».
    #[serde(default, skip_serializing_if = "Option::is_none")]
    wheel_sha256: Option<String>,
}

pub fn marker_json(
    version: &str,
    profile: &str,
    installed_at: &str,
    wheel_sha256: Option<&str>,
) -> String {
    let marker = Marker {
        version: version.into(),
        profile: profile.into(),
        installed_at: installed_at.into(),
        wheel_sha256: wheel_sha256.map(String::from),
    };
    serde_json::to_string_pretty(&marker).unwrap_or_default()
}

/// SHA-256 файла строкой из 64 шестнадцатеричных цифр.
pub fn file_sha256(path: &Path) -> io::Result<String> {
    use sha2::{Digest, Sha256};
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    io::copy(&mut file, &mut hasher)?;
    Ok(hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn read_marker(env_dir: &Path) -> Option<Marker> {
    serde_json::from_str(&fs::read_to_string(env_dir.join(MARKER)).ok()?).ok()
}

/// Движок этой версии установлен: есть резидент и маркер с версией
/// приложения. Без маркера окружение недостроено (установка прервалась).
pub fn is_installed(env_dir: &Path, version: &str) -> bool {
    launcher(env_dir).is_file()
        && read_marker(env_dir).is_some_and(|marker| marker.version == version)
}

fn known_profile(profile: &str) -> bool {
    matches!(profile, "cuda" | "cpu")
}

/// Обслуживание движка при старте оболочки — без мастера и без окна.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Upkeep {
    /// Делать нечего: движок свежий, либо его нет и ставить его будет мастер.
    Nothing,
    /// Движок этой версии стоит, но собран из другого колеса (пересобранный
    /// установщик той же версии): переставить только пакет meet — секунды,
    /// всё остальное уже на месте.
    RefreshWheel { profile: String },
    /// Движка этой версии нет, а движок прежней версии стоял (обновление
    /// приложения): поставить новый тем же профилем, в фоне и без мастера —
    /// пакеты почти все в кэше uv. Прежний удалится, когда новый заработает.
    Upgrade { profile: String },
}

impl Upkeep {
    /// Резидент ждёт конца обслуживания: иначе он поднялся бы из старого
    /// кода, чтобы через секунды быть погашенным, или встал бы в «движок не
    /// установлен», пока тот ставится.
    pub fn holds_resident(&self) -> bool {
        !matches!(self, Upkeep::Nothing)
    }
}

/// Профиль движка прежней версии из `engine\<версия>`: окружение с
/// резидентом и маркером своей версии (законченная установка). Из
/// нескольких — установленный последним.
pub fn previous_profile(engine_root: &Path, current: &str) -> Option<String> {
    stale_envs(engine_root, current)
        .into_iter()
        .filter(|env| launcher(env).is_file())
        .filter_map(|env| {
            let marker = read_marker(&env)?;
            let name = env.file_name()?.to_string_lossy().into_owned();
            (marker.version.eq_ignore_ascii_case(&name) && known_profile(&marker.profile))
                .then_some(marker)
        })
        .max_by(|a, b| a.installed_at.cmp(&b.installed_at))
        .map(|marker| marker.profile)
}

/// Что делать с движком при старте. `installed` — профиль и хэш колеса из
/// маркера установленного движка этой версии (`None` — не установлен),
/// `bundled_wheel` — хэш колеса в ресурсах (`None` — ресурсов нет, dev),
/// `previous` — профиль движка прежней версии (`previous_profile`).
/// Только в релизе: dev-сборка движок не трогает.
pub fn upkeep_decision(
    release: bool,
    installed: Option<(&str, Option<&str>)>,
    bundled_wheel: Option<&str>,
    previous: Option<&str>,
) -> Upkeep {
    if !release {
        return Upkeep::Nothing;
    }
    match (installed, bundled_wheel) {
        (Some((profile, recorded)), Some(bundled))
            if recorded != Some(bundled) && known_profile(profile) =>
        {
            Upkeep::RefreshWheel {
                profile: profile.to_string(),
            }
        }
        (None, Some(_)) => match previous.filter(|profile| known_profile(profile)) {
            Some(profile) => Upkeep::Upgrade {
                profile: profile.to_string(),
            },
            None => Upkeep::Nothing,
        },
        _ => Upkeep::Nothing,
    }
}

/// Как ставить.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InstallMode {
    /// Все шаги; `fresh` — сперва удалить окружение.
    Full { fresh: bool },
    /// Только последний шаг (колесо meet) с `--reinstall-package`: та же
    /// версия, другое колесо — uv иначе счёл бы пакет уже стоящим.
    RefreshWheel,
}

/// Имя пакета meet для `--reinstall-package`.
const PACKAGE: &str = "meet-transcriber";

/// Шаги установки с их номерами из `uv_steps` (окну — «шаг N из 4»).
pub fn install_plan(steps: Vec<Vec<String>>, mode: InstallMode) -> Vec<(usize, Vec<String>)> {
    let mut numbered = steps
        .into_iter()
        .enumerate()
        .map(|(index, argv)| (index + 1, argv));
    match mode {
        InstallMode::Full { .. } => numbered.collect(),
        InstallMode::RefreshWheel => numbered
            .next_back()
            .map(|(step, mut argv)| {
                argv.extend(["--reinstall-package".to_string(), PACKAGE.to_string()]);
                vec![(step, argv)]
            })
            .unwrap_or_default(),
    }
}

/// ГБ для человека: с точностью до десятой, вниз (4,96 — «4,9», не «5»),
/// запятая — по-русски, целое — без «,0».
fn gb_text(gb: f64) -> String {
    let tenths = (gb * 10.0 + 1e-9).floor().max(0.0) as u64;
    if tenths % 10 == 0 {
        format!("{}", tenths / 10)
    } else {
        format!("{},{}", tenths / 10, tenths % 10)
    }
}

/// Хватит ли места; нет — текст отказа.
pub fn space_error(needs_gb: f64, free_gb: f64) -> Option<String> {
    (free_gb + 1e-9 < needs_gb).then(|| {
        format!(
            "Недостаточно места: нужно {} ГБ, свободно {} ГБ",
            gb_text(needs_gb),
            gb_text(free_gb)
        )
    })
}

/// Имя карты из вывода `nvidia-smi --query-gpu=name --format=csv,noheader`.
pub fn parse_gpu(stdout: &str) -> Option<String> {
    stdout
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .map(String::from)
}

/// Колесо meet в ресурсах: своей версии, а если такого нет — самое свежее
/// по времени изменения `meet_transcriber-*.whl`. Сравнивать версии из имён
/// не берёмся: PEP 440 (`0.2.0rc1`, `0.2.0.post1`) не сводится к сортировке
/// строк, а сборка кладёт ровно одно колесо — запасной путь лишь на случай
/// расхождения версий колеса и приложения.
pub fn find_wheel(dir: &Path, version: &str) -> Option<PathBuf> {
    let exact = dir.join(format!("{WHEEL_PREFIX}{version}-py3-none-any.whl"));
    if exact.is_file() {
        return Some(exact);
    }
    fs::read_dir(dir)
        .ok()?
        .filter_map(Result::ok)
        .filter(|entry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            name.starts_with(WHEEL_PREFIX) && name.ends_with(".whl")
        })
        .filter_map(|entry| {
            let meta = entry.metadata().ok().filter(|meta| meta.is_file())?;
            Some((meta.modified().ok()?, entry.path()))
        })
        .max()
        .map(|(_, path)| path)
}

/// Где лежит `file` из ресурсов. Релизный конфиг (`tauri.release.conf.json`,
/// `resources/*`) кладёт файлы в `<resource_dir>\resources`; плоскую
/// раскладку тоже понимаем. В dev ресурсов нет — `None`.
pub fn find_resource_dir(base: &Path, file: &str) -> Option<PathBuf> {
    [base.join("resources"), base.to_path_buf()]
        .into_iter()
        .find(|dir| dir.join(file).is_file())
}

fn resource_dir_with(app: &AppHandle, file: &str) -> Option<PathBuf> {
    find_resource_dir(&app.path().resource_dir().ok()?, file)
}

/// Папка с `ffmpeg.exe` из ресурсов — в PATH резидента из окружения движка.
pub fn ffmpeg_dir(app: &AppHandle) -> Option<PathBuf> {
    resource_dir_with(app, FFMPEG)
}

/// PATH с `dir` в начале.
pub fn path_with(dir: &Path, current: Option<&OsStr>) -> OsString {
    let mut parts = vec![dir.to_path_buf()];
    if let Some(current) = current {
        parts.extend(std::env::split_paths(current).filter(|part| !part.as_os_str().is_empty()));
    }
    std::env::join_paths(parts).unwrap_or_else(|_| dir.as_os_str().to_owned())
}

/// Хвост вывода для окна ошибки — последние `TAIL_LINES` строк.
pub fn push_tail(tail: &mut VecDeque<String>, line: String) {
    tail.push_back(line);
    while tail.len() > TAIL_LINES {
        tail.pop_front();
    }
}

/// Строки в окно — не чаще одной за `gap`. Придержанная последней строка
/// не теряется: `flush` в конце шага отдаёт её, чтобы окно показывало, чем
/// шаг на самом деле закончился.
pub struct Throttle {
    gap: Duration,
    last: Option<Instant>,
    pending: Option<String>,
}

impl Throttle {
    pub fn new(gap: Duration) -> Self {
        Throttle {
            gap,
            last: None,
            pending: None,
        }
    }

    /// Строку пора показать — `Some`; рано — она придержана до следующей
    /// или до `flush`.
    pub fn offer(&mut self, now: Instant, line: String) -> Option<String> {
        if self
            .last
            .is_some_and(|last| now.saturating_duration_since(last) < self.gap)
        {
            self.pending = Some(line);
            return None;
        }
        self.last = Some(now);
        self.pending = None;
        Some(line)
    }

    /// Придержанная строка, если последней показана не она.
    pub fn flush(&mut self) -> Option<String> {
        self.pending.take()
    }
}

/// Что делать с `install.lock`, оставленным установкой (`raw` — его
/// содержимое, `None` — файла нет).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockDecision {
    /// Файла нет — создаём свой.
    Create,
    /// Держатель умер (или файл не читается) — забираем.
    TakeOver,
    /// Держатель жив — установка уже идёт в другом процессе.
    Busy,
}

pub fn lock_decision(raw: Option<&str>, alive: impl Fn(u32) -> bool) -> LockDecision {
    let Some(raw) = raw else {
        return LockDecision::Create;
    };
    match resident::lock_holder(raw) {
        Some(pid) if pid != std::process::id() && alive(pid) => LockDecision::Busy,
        _ => LockDecision::TakeOver,
    }
}

/// Межпроцессный замок установки `engine\install.lock` с pid держателя:
/// вторая оболочка (другой `MEET_DATA_DIR` не в счёт — у неё своя папка)
/// или оболочка, перезапущенная, пока uv прежней ещё дорабатывал, не
/// запускает второй uv в то же окружение. Снимается при выходе из `install`.
struct InstallLock(PathBuf);

impl InstallLock {
    fn acquire(engine_root: &Path) -> Result<InstallLock, String> {
        let path = engine_root.join(INSTALL_LOCK);
        fs::create_dir_all(engine_root)
            .map_err(|error| format!("Не удалось создать папку движка: {error}"))?;
        let raw = fs::read_to_string(&path).ok();
        match lock_decision(raw.as_deref(), resident::pid_alive) {
            LockDecision::Busy => return Err(BUSY.to_string()),
            LockDecision::TakeOver => {
                shell_log!("забираю брошенный замок установки {}", path.display());
                let _ = fs::remove_file(&path);
            }
            LockDecision::Create => {}
        }
        let body = format!("{{\"pid\": {}}}", std::process::id());
        // create_new: из двух процессов, одновременно забравших брошенный
        // замок, файл создаст только один.
        File::options()
            .write(true)
            .create_new(true)
            .open(&path)
            .and_then(|mut file| file.write_all(body.as_bytes()))
            .map_err(|error| match error.kind() {
                io::ErrorKind::AlreadyExists => BUSY.to_string(),
                _ => format!("Не удалось создать замок установки: {error}"),
            })?;
        Ok(InstallLock(path))
    }
}

impl Drop for InstallLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

/// Job object с KILL_ON_JOB_CLOSE: uv и всё, что он запустил, умирают
/// вместе с оболочкой — при «Выходе» и при падении (хэндл закрывает
/// Windows). Иначе скрытый uv остался бы сиротой и дописывал окружение
/// наперегонки со следующей установкой.
#[cfg(windows)]
pub struct Job(windows_sys::Win32::Foundation::HANDLE);

// SAFETY: хэндл job object — просто число ядра; вызовы с ним потокобезопасны.
#[cfg(windows)]
unsafe impl Send for Job {}
#[cfg(windows)]
unsafe impl Sync for Job {}

#[cfg(windows)]
impl Job {
    pub fn kill_on_close() -> Option<Job> {
        use windows_sys::Win32::System::JobObjects::{
            CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };
        // SAFETY: безымянный job без атрибутов; структура лимитов — локальная,
        // нулевая инициализация для неё допустима (POD), размер передаётся.
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return None;
            }
            let job = Job(handle);
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            (ok != 0).then_some(job)
        }
    }

    pub fn assign(&self, child: &std::process::Child) -> bool {
        use std::os::windows::io::AsRawHandle;
        self.assign_handle(child.as_raw_handle())
    }

    /// Процесс по хэндлу — для детей, запущенных не через `std::process`
    /// (агент во встроенном терминале, `pty.rs`). Хэндл должен быть жив.
    pub fn assign_handle(&self, process: std::os::windows::io::RawHandle) -> bool {
        use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
        // SAFETY: оба хэндла живы на время вызова (вызывающий держит процесс).
        unsafe { AssignProcessToJobObject(self.0, process) != 0 }
    }
}

#[cfg(windows)]
impl Drop for Job {
    fn drop(&mut self) {
        // SAFETY: хэндл наш и закрывается ровно один раз.
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

/// Job оболочки для процессов установки: живёт до конца процесса (static
/// не освобождается), закрывает его Windows при выходе.
#[cfg(windows)]
fn install_job() -> Option<&'static Job> {
    static JOB: std::sync::OnceLock<Option<Job>> = std::sync::OnceLock::new();
    JOB.get_or_init(Job::kill_on_close).as_ref()
}

/// Привязать процесс установки к job оболочки. Не вышло — установка идёт
/// дальше: без job хуже только уборка после аварийного выхода.
fn bind_to_shell(child: &std::process::Child) {
    #[cfg(windows)]
    if !install_job().is_some_and(|job| job.assign(child)) {
        shell_log!("процесс установки (pid {}) не привязан к job", child.id());
    }
    #[cfg(not(windows))]
    let _ = child;
}

static INSTALLING: AtomicBool = AtomicBool::new(false);

/// Идёт установка; снимается при выходе из `install`, как бы он ни
/// закончился. Второй вызов (двойной клик, второе окно) получает отказ, а не
/// вторую копию uv в той же папке.
pub struct Busy(());

impl Drop for Busy {
    fn drop(&mut self) {
        INSTALLING.store(false, Ordering::SeqCst);
    }
}

pub fn begin_install() -> Result<Busy, String> {
    INSTALLING
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .map(|_| Busy(()))
        .map_err(|_| BUSY.to_string())
}

/// Свободно на диске пути (ближайшая существующая папка), ГБ.
#[cfg(windows)]
pub fn free_gb(path: &Path) -> Option<f64> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
    let existing = path.ancestors().find(|dir| dir.exists())?;
    let wide: Vec<u16> = existing.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut free = 0u64;
    // SAFETY: строка с нулём на конце живёт до конца вызова; счётчик пишется
    // в локальную переменную, необязательные выходы — null.
    let ok = unsafe {
        GetDiskFreeSpaceExW(
            wide.as_ptr(),
            &mut free,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    (ok != 0).then(|| free as f64 / f64::from(1u32 << 30))
}

#[cfg(not(windows))]
pub fn free_gb(_path: &Path) -> Option<f64> {
    None
}

/// Видеокарта NVIDIA по `nvidia-smi` (без окна, не дольше 5 с); нет
/// nvidia-smi или карты — `None`. Это штатно для ноутбука, не сбой.
pub fn detect_gpu() -> Option<String> {
    let mut command = Command::new("nvidia-smi");
    command
        .args(["--query-gpu=name", "--format=csv,noheader"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    resident::hide_console(&mut command);
    let mut child = command.spawn().ok()?;
    let deadline = Instant::now() + GPU_TIMEOUT;
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => break,
            Ok(Some(_)) => return None,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    let mut out = String::new();
    child.stdout.take()?.read_to_string(&mut out).ok()?;
    parse_gpu(&out)
}

/// Запустить программу без окна и отдавать её stdout и stderr построчно, по
/// мере появления. Код выхода (`None` — процесс убит).
pub fn run_streamed(
    argv: &[String],
    envs: &[(&str, OsString)],
    cwd: &Path,
    mut on_line: impl FnMut(String),
) -> io::Result<Option<i32>> {
    let (program, rest) = argv
        .split_first()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "пустая команда"))?;
    let mut command = Command::new(program);
    command
        .args(rest)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (key, value) in envs {
        command.env(key, value);
    }
    resident::hide_console(&mut command);
    let mut child = command.spawn()?;
    // Сразу после запуска: дочерние процессы uv, созданные после привязки,
    // попадают в тот же job сами.
    bind_to_shell(&child);
    let (sender, receiver) = mpsc::channel();
    let streams: [Option<Box<dyn Read + Send>>; 2] = [
        child
            .stdout
            .take()
            .map(|out| Box::new(out) as Box<dyn Read + Send>),
        child
            .stderr
            .take()
            .map(|err| Box::new(err) as Box<dyn Read + Send>),
    ];
    let readers: Vec<_> = streams
        .into_iter()
        .flatten()
        .map(|stream| {
            let sender = sender.clone();
            thread::spawn(move || forward_lines(stream, &sender))
        })
        .collect();
    drop(sender);
    for line in receiver {
        on_line(line);
    }
    for reader in readers {
        let _ = reader.join();
    }
    Ok(child.wait()?.code())
}

/// Строки потока — в канал. `\r` внутри строки — перерисовка прогресса:
/// берём последнюю версию.
fn forward_lines(stream: impl Read, sender: &mpsc::Sender<String>) {
    let mut reader = BufReader::new(stream);
    let mut buffer = Vec::new();
    loop {
        buffer.clear();
        match reader.read_until(b'\n', &mut buffer) {
            Ok(0) | Err(_) => return,
            Ok(_) => {
                let text = String::from_utf8_lossy(&buffer);
                let line = text
                    .trim_end_matches(['\r', '\n'])
                    .rsplit('\r')
                    .next()
                    .unwrap_or_default()
                    .trim_end();
                if !line.is_empty() && sender.send(line.to_string()).is_err() {
                    return;
                }
            }
        }
    }
}

/// `logs\engine-install.log`: весь вывод uv со временем. Открывается тем же
/// `logs::open_append`, что `resident.log`: больше 1 МБ — сначала уезжает в
/// `.1` (`logs::rotate`). Журнал не открылся — установка идёт без него.
struct InstallLog(Option<File>);

impl InstallLog {
    fn open(data_dir: &Path) -> Self {
        let path = logs::engine_install_log(data_dir);
        match logs::open_append(&path) {
            Ok(file) => InstallLog(Some(file)),
            Err(error) => {
                shell_log!("журнал установки {} не открылся: {error}", path.display());
                InstallLog(None)
            }
        }
    }

    fn write(&mut self, text: &str) {
        if let Some(file) = self.0.as_mut() {
            let _ = file.write_all(logs::line(SystemTime::now(), text).as_bytes());
        }
    }
}

#[derive(Serialize, Clone)]
struct Progress {
    step: usize,
    of: usize,
    line: String,
}

#[derive(Serialize, Clone)]
struct Failed {
    step: usize,
    tail: String,
}

fn app_version(app: &AppHandle) -> String {
    app.package_info().version.to_string()
}

/// Поставить движок версии приложения (`fresh` — с нуля, удалив прежнее
/// окружение). Блокирует на минуты: только из рабочего потока.
pub fn install(app: &AppHandle, profile: &str, fresh: bool) -> Result<(), String> {
    install_with(app, profile, InstallMode::Full { fresh })
}

pub fn install_with(app: &AppHandle, profile: &str, mode: InstallMode) -> Result<(), String> {
    let _busy = begin_install()?;
    if !known_profile(profile) {
        return Err(format!("Неизвестный профиль движка: {profile}"));
    }
    let data = resident::data_dir();
    let version = app_version(app);
    let env = env_dir(&data, &version);
    let resources = resource_dir_with(app, UV).ok_or(NO_UV)?;
    let wheel = find_wheel(&resources, &version).ok_or(NO_WHEEL)?;
    let fresh = mode == InstallMode::Full { fresh: true };
    // Повторная установка: места не хватает — отказ раньше, чем гасить
    // работающий резидент. Переустановка проверяет место после удаления
    // окружения (`prepare`): оно само его и освобождает. Замена колеса
    // meet занимает мегабайты — её не проверяем.
    if mode == (InstallMode::Full { fresh: false }) {
        check_space(&data, profile)?;
    }
    let _lock = InstallLock::acquire(&engine_root(&data))?;
    let supervisor = app.state::<Supervisor>();
    // Резидент из этого окружения держит его файлы: uv не пересоздаст venv
    // под работающим python.exe, а удалить папку не даст Windows.
    let stopped = supervisor.stop_if_from(&env);
    // Хэш колеса — в маркер: по нему следующий старт узнает пересборку той
    // же версии. Не посчитался — маркер без хэша, колесо переставится при
    // следующем старте (лишние секунды, не поломка).
    let wheel_sha256 = file_sha256(&wheel)
        .map_err(|error| shell_log!("хэш колеса {} не посчитался: {error}", wheel.display()))
        .ok();
    let result = prepare(&data, &env, profile, fresh).and_then(|()| {
        run_steps(
            app,
            &Target {
                data_dir: &data,
                env: &env,
                version: &version,
                profile,
            },
            &resources.join(UV),
            (&wheel, wheel_sha256.as_deref()),
            mode,
        )
    });
    match &result {
        Ok(()) => {
            shell_log!("движок {version} ({profile}) установлен: {}", env.display());
            // Новый надзор заново соберёт кандидатов и возьмёт движок.
            supervisor.respawn(app);
        }
        Err(error) => {
            shell_log!("установка движка не удалась: {error}");
            if stopped {
                supervisor.respawn(app);
            }
        }
    }
    result
}

/// Хватит ли места на диске с данными под профиль (с учётом кэша uv); не
/// узнать — не мешаем. Ошибся в меньшую сторону — uv упадёт с «нет места»,
/// и это будет в хвосте лога.
fn check_space(data_dir: &Path, profile: &str) -> Result<(), String> {
    let needs = space_needed(data_dir, profile, current_uv_cache().as_deref());
    match free_gb(data_dir).and_then(|free| space_error(needs, free)) {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

/// До шагов: снимается маркер (прерванная установка не должна выглядеть
/// законченной — в том числе когда удаление ниже споткнулось на занятом
/// файле и окружение осталось наполовину), переустановка удаляет окружение
/// и проверяет место.
fn prepare(data_dir: &Path, env: &Path, profile: &str, fresh: bool) -> Result<(), String> {
    match fs::remove_file(env.join(MARKER)) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(format!("Не удалось снять отметку установки: {error}")),
    }
    if fresh {
        if env.exists() {
            fs::remove_dir_all(env).map_err(|error| {
                format!(
                    "Не удалось удалить прежний движок ({}): {error}. Закройте программы, \
                     запущенные из движка, и повторите",
                    env.display()
                )
            })?;
        }
        check_space(data_dir, profile)?;
    }
    fs::create_dir_all(engine_root(data_dir))
        .map_err(|error| format!("Не удалось создать папку движка: {error}"))
}

/// Куда и что ставится.
struct Target<'a> {
    data_dir: &'a Path,
    env: &'a Path,
    version: &'a str,
    profile: &'a str,
}

fn run_steps(
    app: &AppHandle,
    target: &Target,
    uv: &Path,
    (wheel, wheel_sha256): (&Path, Option<&str>),
    mode: InstallMode,
) -> Result<(), String> {
    let Target {
        data_dir,
        env,
        version,
        profile,
    } = *target;
    // Ограничения версий лежат рядом с uv в ресурсах установщика.
    let constraints = uv
        .parent()
        .and_then(|resources| constraints_file(resources, profile))
        .map(|file| file.to_string_lossy().into_owned());
    let steps = uv_steps(
        &uv.to_string_lossy(),
        &env.to_string_lossy(),
        &wheel.to_string_lossy(),
        profile,
        constraints.as_deref(),
    );
    let of = steps.len();
    let plan = install_plan(steps, mode);
    let mut envs = uv_env(data_dir);
    // Прокси из настроек Windows: uv их сам не читает (см. netproxy.rs).
    envs.extend(crate::netproxy::system_proxy_env());
    let cwd = engine_root(data_dir);
    let mut log = InstallLog::open(data_dir);
    log.write(&format!(
        "--- {} движка {version} ({profile}) в {}",
        if mode == InstallMode::RefreshWheel {
            "замена колеса meet"
        } else {
            "установка"
        },
        env.display()
    ));
    let progress = |step: usize, line: String| {
        let _ = app.emit(PROGRESS_EVENT, Progress { step, of, line });
    };
    let supervisor = app.state::<Supervisor>();
    for (step, argv) in &plan {
        let step = *step;
        let title = STEP_TITLES.get(step - 1).copied().unwrap_or("Установка");
        log.write(&format!("шаг {step} из {of}: {}", argv.join(" ")));
        progress(step, title.to_string());
        // Фоновое обслуживание движка показывает шаг в подсказке трея.
        supervisor.engine_step(step, of);
        let mut tail = VecDeque::new();
        let mut throttle = Throttle::new(LINE_GAP);
        let outcome = run_streamed(argv, &envs, &cwd, |line| {
            log.write(&line);
            if let Some(shown) = throttle.offer(Instant::now(), line.clone()) {
                progress(step, shown);
            }
            push_tail(&mut tail, line);
        });
        if let Some(last) = throttle.flush() {
            progress(step, last);
        }
        let failure = match outcome {
            Ok(Some(0)) => None,
            Ok(Some(code)) => Some(format!("шаг {step} из {of} ({title}): код выхода {code}")),
            Ok(None) => Some(format!("шаг {step} из {of} ({title}): процесс прерван")),
            Err(error) => Some(format!(
                "шаг {step} из {of} ({title}) не запустился: {error}"
            )),
        };
        if let Some(message) = failure {
            log.write(&message);
            push_tail(&mut tail, message.clone());
            let tail = Vec::from(tail).join("\n");
            let _ = app.emit(FAILED_EVENT, Failed { step, tail });
            return Err(format!("Установка движка прервалась: {message}"));
        }
    }
    let marker = marker_json(version, profile, &logs::utc_now(), wheel_sha256);
    let staged = env.join(format!("{MARKER}.tmp"));
    fs::write(&staged, marker)
        .and_then(|()| fs::rename(&staged, env.join(MARKER)))
        .map_err(|error| format!("Не удалось записать отметку установки: {error}"))?;
    log.write("движок установлен");
    Ok(())
}

/// Обслуживание движка для этого запуска — из `setup`, до надзора: от него
/// зависит, ждать ли резиденту (`Upkeep::holds_resident`).
pub fn plan_upkeep(app: &AppHandle) -> Upkeep {
    let data = resident::data_dir();
    let version = app_version(app);
    let env = env_dir(&data, &version);
    let marker = is_installed(&env, &version)
        .then(|| read_marker(&env))
        .flatten();
    let bundled = resource_dir_with(app, UV)
        .and_then(|resources| find_wheel(&resources, &version))
        .and_then(|wheel| file_sha256(&wheel).ok());
    let previous = marker
        .is_none()
        .then(|| previous_profile(&engine_root(&data), &version))
        .flatten();
    upkeep_decision(
        !cfg!(debug_assertions),
        marker
            .as_ref()
            .map(|marker| (marker.profile.as_str(), marker.wheel_sha256.as_deref())),
        bundled.as_deref(),
        previous.as_deref(),
    )
}

/// Обслужить движок в своём потоке. Резидент, придержанный до конца
/// обслуживания (`Supervisor::start(.., held)`), поднимается после него —
/// удачного или нет.
pub fn run_upkeep_in_background(app: &AppHandle, upkeep: Upkeep) {
    let (profile, mode) = match upkeep {
        Upkeep::Nothing => return,
        Upkeep::RefreshWheel { profile } => {
            shell_log!("движок собран из другого колеса meet — переставляю пакет meet");
            (profile, InstallMode::RefreshWheel)
        }
        Upkeep::Upgrade { profile } => {
            shell_log!("движка этой версии нет, у прежней был ({profile}) — ставлю новый в фоне");
            (profile, InstallMode::Full { fresh: false })
        }
    };
    let handle = app.clone();
    let spawned = thread::Builder::new()
        .name("meet-engine-upkeep".into())
        .spawn(move || {
            let result = install_with(&handle, &profile, mode);
            upkeep_finished(&handle, result);
        });
    if let Err(error) = spawned {
        shell_log!("поток обслуживания движка не запустился: {error}");
        app.state::<Supervisor>().respawn(app);
    }
}

/// Удача резидент уже подняла (`install_with` → `respawn`). Сбой: поднять
/// надзор здесь (без движка он встанет в «движок не установлен», с прежним —
/// запустит его) и сказать одним важным уведомлением: окно с мастером
/// человек иначе не откроет — флаг мастера стоит, а автозапуск окон не
/// показывает.
fn upkeep_finished(app: &AppHandle, result: Result<(), String>) {
    let Err(error) = result else {
        return;
    };
    shell_log!("обслуживание движка не удалось: {error}");
    app.state::<Supervisor>().respawn(app);
    tray::notify(
        app,
        vec![tray::Notice {
            title: tray::ENGINE_UPDATE_FAILED.into(),
            body: "Откройте окно из трея".into(),
            recording: None,
        }],
    );
}

static CLEANED: AtomicBool = AtomicBool::new(false);

/// Удалить окружения прежних версий — один раз за жизнь оболочки, в своём
/// потоке (гигабайты). Старые окружения резидентом не используются никогда
/// (кандидат — только окружение своей версии); ждём ответа резидента
/// текущей версии лишь затем, чтобы не удалять, пока новый движок не
/// доказал, что работает: откат на прежнюю версию приложения найдёт своё
/// окружение на месте. Не удалилось (файлы заняты) — попробуем при
/// следующем запуске.
pub fn remove_stale_in_background(data_dir: PathBuf, current: String) {
    if CLEANED.swap(true, Ordering::SeqCst) {
        return;
    }
    let spawned = thread::Builder::new()
        .name("meet-engine-cleanup".into())
        .spawn(move || {
            for dir in stale_envs(&engine_root(&data_dir), &current) {
                match fs::remove_dir_all(&dir) {
                    Ok(()) => shell_log!("удалён движок прежней версии: {}", dir.display()),
                    Err(error) => shell_log!("движок {} не удалён: {error}", dir.display()),
                }
            }
        });
    if let Err(error) = spawned {
        shell_log!("поток очистки движков не запустился: {error}");
    }
}

/// Состояние движка для мастера первого запуска.
#[derive(Serialize, Clone, Debug)]
pub struct EngineStatus {
    pub installed: bool,
    pub version: String,
    pub env_dir: String,
    /// Профиль установленного движка (`cuda`/`cpu`); не установлен — `None`.
    pub profile: Option<String>,
    pub gpu: Option<String>,
    /// Свободно на диске с данными, ГБ (вниз до десятой); `None` — узнать
    /// не удалось (тогда установку не блокируем).
    pub free_gb: Option<f64>,
    /// Нужно места под профиль, который подсказывает видеокарта (меньше,
    /// если пакеты уже в кэше uv — `space_needed`).
    pub needs_gb: f64,
    /// То же для CPU-версии (запасной путь мастера для владельцев NVIDIA).
    pub needs_cpu_gb: f64,
    /// Установка идёт прямо сейчас (из мастера в закрытом с тех пор окне или
    /// фоновое обновление): окно подключается к её ходу, а не предлагает
    /// «Установить» второй раз.
    pub installing: bool,
}

/// Идёт ли установка в этой оболочке.
pub fn installing() -> bool {
    INSTALLING.load(Ordering::SeqCst)
}

pub fn status(app: &AppHandle) -> EngineStatus {
    let data = resident::data_dir();
    let version = app_version(app);
    let env = env_dir(&data, &version);
    let installed = is_installed(&env, &version);
    let profile = installed
        .then(|| read_marker(&env))
        .flatten()
        .map(|marker| marker.profile);
    let gpu = detect_gpu();
    let cache = current_uv_cache();
    EngineStatus {
        installed,
        version,
        env_dir: env.to_string_lossy().into_owned(),
        profile,
        needs_gb: space_needed(&data, profile_for(gpu.as_deref()), cache.as_deref()),
        needs_cpu_gb: space_needed(&data, "cpu", cache.as_deref()),
        gpu,
        free_gb: free_gb(&data).map(|gb| (gb * 10.0).floor() / 10.0),
        installing: installing(),
    }
}

#[tauri::command]
pub async fn engine_status(app: AppHandle) -> Result<EngineStatus, String> {
    tauri::async_runtime::spawn_blocking(move || status(&app))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn gpu_info() -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(detect_gpu)
        .await
        .map_err(|error| error.to_string())
}

/// Поставить движок (или достроить после сбоя). Ответ — по окончании;
/// прогресс — событиями `engine-progress`, сбой шага — `engine-failed`.
#[tauri::command]
pub async fn install_engine(app: AppHandle, profile: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || install(&app, &profile, false))
        .await
        .map_err(|error| error.to_string())?
}

/// Удалить окружение текущей версии и поставить заново.
#[tauri::command]
pub async fn reinstall_engine(app: AppHandle, profile: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || install(&app, &profile, true))
        .await
        .map_err(|error| error.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// Временная папка теста; удаляется в конце теста.
    pub(crate) struct TempDir(pub PathBuf);

    impl TempDir {
        pub fn new(name: &str) -> Self {
            let dir = std::env::temp_dir()
                .join(format!("meet-engine-test-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            TempDir(dir)
        }

        pub fn file(&self, relative: &str, content: &str) -> PathBuf {
            let path = self.0.join(relative);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, content).unwrap();
            path
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn env_dir_is_versioned_under_engine() {
        let data = Path::new(r"C:\Users\u\AppData\Local\meet");
        assert_eq!(engine_root(data), data.join("engine"));
        assert_eq!(env_dir(data, "0.2.0"), data.join("engine").join("0.2.0"));
    }

    fn fixture(name: &str) -> Vec<Vec<String>> {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures")
            .join(name);
        let raw =
            fs::read_to_string(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()));
        serde_json::from_str(&raw).unwrap()
    }

    const WHEEL: &str = r"C:\w\meet_transcriber-0.1.0-py3-none-any.whl";

    #[test]
    fn uv_steps_match_python_for_cuda() {
        assert_eq!(
            uv_steps("uv.exe", r"C:\env", WHEEL, "cuda", None),
            fixture("uv_steps_cuda.json")
        );
        assert_eq!(
            uv_steps(
                "uv.exe",
                r"C:\env",
                WHEEL,
                "cuda",
                Some(r"C:\r\constraints-cuda.txt")
            ),
            fixture("uv_steps_cuda_constrained.json")
        );
    }

    #[test]
    fn uv_steps_match_python_for_cpu() {
        assert_eq!(
            uv_steps("uv.exe", r"C:\env", WHEEL, "cpu", None),
            fixture("uv_steps_cpu.json")
        );
        assert_eq!(
            uv_steps(
                "uv.exe",
                r"C:\env",
                WHEEL,
                "cpu",
                Some(r"C:\r\constraints-cpu.txt")
            ),
            fixture("uv_steps_cpu_constrained.json")
        );
    }

    #[test]
    fn constraints_file_is_used_only_when_shipped() {
        let tree = TempDir::new("constraints");
        assert_eq!(constraints_file(&tree.0, "cuda"), None, "dev: файла нет");
        let file = tree.file("constraints-cuda.txt", "torch==2.11.0+cu128\n");
        assert_eq!(constraints_file(&tree.0, "cuda"), Some(file));
        assert_eq!(constraints_file(&tree.0, "cpu"), None);
    }

    #[test]
    fn stale_envs_are_other_versions_but_not_python() {
        let root = TempDir::new("stale");
        for dir in ["0.1.0", "0.2.0", "0.3.0", "python"] {
            fs::create_dir_all(root.0.join(dir)).unwrap();
        }
        root.file("notes.txt", "не папка");
        assert_eq!(
            stale_envs(&root.0, "0.2.0"),
            vec![root.0.join("0.1.0"), root.0.join("0.3.0")]
        );
        // Нет папки engine — нечего удалять.
        assert!(stale_envs(&root.0.join("missing"), "0.2.0").is_empty());
    }

    #[test]
    fn cuda_needs_more_space_than_cpu() {
        assert_eq!(needs_gb("cuda"), 8.0);
        assert_eq!(needs_gb("cpu"), 3.0);
    }

    #[test]
    fn installed_means_launcher_and_marker_of_this_version() {
        let tree = TempDir::new("installed");
        let env = tree.0.join("0.2.0");
        assert!(!is_installed(&env, "0.2.0"), "пустая папка");
        tree.file(r"0.2.0\Scripts\meet-tray.exe", "");
        assert!(!is_installed(&env, "0.2.0"), "без маркера — недостроено");
        tree.file(
            r"0.2.0\installed.json",
            &marker_json("0.1.0", "cuda", "2026-10-01 03:00:00Z", None),
        );
        assert!(!is_installed(&env, "0.2.0"), "маркер другой версии");
        tree.file(
            r"0.2.0\installed.json",
            &marker_json("0.2.0", "cuda", "2026-10-01 03:00:00Z", None),
        );
        assert!(is_installed(&env, "0.2.0"));
        tree.file(r"0.2.0\installed.json", "мусор");
        assert!(!is_installed(&env, "0.2.0"), "битый маркер");
    }

    #[test]
    fn marker_carries_version_profile_and_time() {
        let raw = marker_json("0.2.0", "cpu", "2026-10-01 03:00:00Z", Some("ab12"));
        let value: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(value["version"], "0.2.0");
        assert_eq!(value["profile"], "cpu");
        assert_eq!(value["installed_at"], "2026-10-01 03:00:00Z");
        assert_eq!(value["wheel_sha256"], "ab12");
    }

    #[test]
    fn marker_without_wheel_hash_still_reads_as_installed() {
        // Маркер rc1 — без хэша колеса: движок установлен, хэш неизвестен.
        let tree = TempDir::new("old-marker");
        tree.file(r"0.1.0\Scripts\meet-tray.exe", "");
        tree.file(
            r"0.1.0\installed.json",
            r#"{"version": "0.1.0", "profile": "cuda", "installed_at": "x"}"#,
        );
        let env = tree.0.join("0.1.0");
        assert!(is_installed(&env, "0.1.0"));
        assert_eq!(read_marker(&env).unwrap().wheel_sha256, None);
    }

    #[test]
    fn file_hash_is_sha256_hex() {
        let tree = TempDir::new("sha");
        let file = tree.file("a.txt", "abc");
        assert_eq!(
            file_sha256(&file).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert!(file_sha256(&tree.0.join("missing")).is_err());
    }

    #[test]
    fn same_version_rebuild_refreshes_only_the_meet_wheel() {
        let refresh = |profile: &str| Upkeep::RefreshWheel {
            profile: profile.into(),
        };
        // rc1 → финальная 0.1.0: маркер без хэша, колесо в ресурсах другое.
        assert_eq!(
            upkeep_decision(true, Some(("cuda", None)), Some("new"), None),
            refresh("cuda")
        );
        assert_eq!(
            upkeep_decision(true, Some(("cpu", Some("old"))), Some("new"), None),
            refresh("cpu")
        );
        // То же колесо — делать нечего.
        assert_eq!(
            upkeep_decision(true, Some(("cuda", Some("new"))), Some("new"), None),
            Upkeep::Nothing
        );
        // Движка нет и не было — его ставит мастер.
        assert_eq!(
            upkeep_decision(true, None, Some("new"), None),
            Upkeep::Nothing
        );
        // Колесо в ресурсах не нашлось/не прочлось — не трогаем.
        assert_eq!(
            upkeep_decision(true, Some(("cuda", Some("old"))), None, None),
            Upkeep::Nothing
        );
        // Неизвестный профиль в маркере — не угадываем.
        assert_eq!(
            upkeep_decision(true, Some(("rocm", None)), Some("new"), None),
            Upkeep::Nothing
        );
        // Dev-сборка движок не обслуживает.
        assert_eq!(
            upkeep_decision(false, Some(("cuda", None)), Some("new"), None),
            Upkeep::Nothing
        );
        assert!(refresh("cuda").holds_resident());
        assert!(!Upkeep::Nothing.holds_resident());
    }

    #[test]
    fn upgrade_installs_the_new_engine_with_the_previous_profile() {
        let upgrade = |profile: &str| Upkeep::Upgrade {
            profile: profile.into(),
        };
        // 0.1.0 → 0.2.0: движка 0.2.0 нет, у 0.1.0 был CPU-движок.
        assert_eq!(
            upkeep_decision(true, None, Some("w"), Some("cpu")),
            upgrade("cpu")
        );
        assert!(upgrade("cuda").holds_resident());
        // Свой движок стоит — прежний не важен.
        assert_eq!(
            upkeep_decision(true, Some(("cuda", Some("w"))), Some("w"), Some("cpu")),
            Upkeep::Nothing
        );
        // Без колеса в ресурсах ставить нечем; dev не обслуживает.
        assert_eq!(
            upkeep_decision(true, None, None, Some("cuda")),
            Upkeep::Nothing
        );
        assert_eq!(
            upkeep_decision(false, None, Some("w"), Some("cuda")),
            Upkeep::Nothing
        );
        assert_eq!(
            upkeep_decision(true, None, Some("w"), Some("rocm")),
            Upkeep::Nothing
        );
    }

    #[test]
    fn previous_profile_comes_from_a_finished_install_of_another_version() {
        let tree = TempDir::new("previous");
        let marker =
            |version: &str, profile: &str, at: &str| marker_json(version, profile, at, Some("h"));
        assert_eq!(previous_profile(&tree.0, "0.3.0"), None, "папки engine нет");
        // Недостроенный 0.1.0 (без маркера) и 0.2.0 без резидента — не в счёт.
        tree.file(r"0.1.0\Scripts\meet-tray.exe", "");
        tree.file(
            r"0.2.0\installed.json",
            &marker("0.2.0", "cuda", "2026-10-02"),
        );
        tree.file(
            r"python\installed.json",
            &marker("python", "cuda", "2026-10-03"),
        );
        assert_eq!(previous_profile(&tree.0, "0.3.0"), None);
        // Законченный 0.1.0 — его профиль.
        tree.file(
            r"0.1.0\installed.json",
            &marker("0.1.0", "cpu", "2026-10-01"),
        );
        assert_eq!(previous_profile(&tree.0, "0.3.0").as_deref(), Some("cpu"));
        // Два законченных — последний установленный.
        tree.file(r"0.2.0\Scripts\meet-tray.exe", "");
        assert_eq!(previous_profile(&tree.0, "0.3.0").as_deref(), Some("cuda"));
        // Текущая версия — не «прежняя»; маркер чужой версии в папке — тоже.
        assert_eq!(previous_profile(&tree.0, "0.2.0").as_deref(), Some("cpu"));
        tree.file(
            r"0.1.0\installed.json",
            &marker("0.0.9", "cpu", "2026-10-09"),
        );
        assert_eq!(previous_profile(&tree.0, "0.2.0"), None);
    }

    #[test]
    fn uv_cache_is_the_override_or_the_local_app_data_default() {
        let local = Some(OsStr::new(r"C:\Users\u\AppData\Local"));
        assert_eq!(
            uv_cache_dir(None, local),
            Some(PathBuf::from(r"C:\Users\u\AppData\Local\uv\cache"))
        );
        assert_eq!(
            uv_cache_dir(Some(OsStr::new(r"D:\uvc")), local),
            Some(PathBuf::from(r"D:\uvc"))
        );
        assert_eq!(
            uv_cache_dir(Some(OsStr::new("")), local),
            uv_cache_dir(None, local)
        );
        assert_eq!(uv_cache_dir(None, None), None);
    }

    #[test]
    fn cached_torch_is_recognised_by_its_unpacked_pointer_of_the_profile_build() {
        let cache = TempDir::new("uvcache");
        let torch = r"wheels-v6\index\d2bd0b84f216183d\torch";
        assert!(!cache_has_torch(&cache.0, "cuda"), "пустой кэш");
        // Скачивание начато, но не закончено: только замок и метаданные.
        cache.file(
            &format!(r"{torch}\torch-2.11.0+cu128-cp312-cp312-win_amd64.lock"),
            "",
        );
        cache.file(
            &format!(r"{torch}\2.11.0+cu128-cp312-cp312-win_amd64.msgpack"),
            "",
        );
        assert!(!cache_has_torch(&cache.0, "cuda"));
        cache.file(&format!(r"{torch}\2.11.0+cu128-cp312-cp312-win_amd64"), "x");
        assert!(cache_has_torch(&cache.0, "cuda"));
        assert!(!cache_has_torch(&cache.0, "cpu"), "CUDA-сборка — не CPU");
        cache.file(
            r"wheels-v5\index\09e0bc338403d139\torch\2.11.0+cpu-cp312-cp312-win_amd64",
            "x",
        );
        assert!(cache_has_torch(&cache.0, "cpu"));
        assert!(!cache_has_torch(&cache.0.join("missing"), "cuda"));
    }

    #[test]
    fn hard_links_need_the_same_volume() {
        assert!(same_volume(
            Path::new(r"C:\Users\u\AppData\Local\uv\cache"),
            Path::new(r"c:\Users\u\AppData\Local\meet")
        ));
        assert!(!same_volume(Path::new(r"D:\uvc"), Path::new(r"C:\meet")));
        assert!(!same_volume(Path::new("relative"), Path::new(r"C:\meet")));
    }

    #[test]
    fn space_need_drops_when_the_heavy_part_is_already_on_disk() {
        let data = TempDir::new("space-data");
        let root = data.0.join("engine");
        let cache = data.0.join("uvcache");
        // Ничего не скачано — полный объём.
        assert_eq!(space_needed(&data.0, "cuda", Some(&cache)), 8.0);
        assert_eq!(space_needed(&data.0, "cpu", None), 3.0);
        // torch профиля в кэше на том же диске (повтор после сбоя,
        // переустановка) — немного.
        data.file(
            r"uvcache\wheels-v6\index\x\torch\2.11.0+cu128-cp312-cp312-win_amd64",
            "x",
        );
        assert_eq!(space_needed(&data.0, "cuda", Some(&cache)), 1.0);
        assert_eq!(space_needed(&data.0, "cpu", Some(&cache)), 3.0);
        // Кэш на другом диске — ссылками не обойтись.
        assert_eq!(
            space_needed(&data.0, "cuda", Some(Path::new(r"Q:\uvcache"))),
            8.0
        );
        // Законченный движок того же профиля (прежняя версия) — немного.
        data.file(r"engine\0.1.0\Scripts\meet-tray.exe", "");
        data.file(
            r"engine\0.1.0\installed.json",
            &marker_json("0.1.0", "cpu", "t", None),
        );
        assert_eq!(space_needed(&data.0, "cpu", None), 1.0);
        assert_eq!(space_needed(&data.0, "cuda", None), 8.0);
        assert!(root.is_dir());
    }

    #[test]
    fn wheel_refresh_runs_only_the_last_step_with_reinstall() {
        let steps = uv_steps("uv.exe", r"C:\env", WHEEL, "cuda", Some(r"C:\r\c.txt"));
        let full = install_plan(steps.clone(), InstallMode::Full { fresh: false });
        assert_eq!(full.len(), 4);
        assert_eq!(full[2], (3, steps[2].clone()));
        let refresh = install_plan(steps.clone(), InstallMode::RefreshWheel);
        let mut expected = steps[3].clone();
        expected.extend(["--reinstall-package".into(), "meet-transcriber".into()]);
        assert_eq!(refresh, vec![(4, expected)]);
    }

    #[test]
    fn not_enough_space_is_reported_in_gigabytes() {
        assert_eq!(
            space_error(5.0, 2.34).as_deref(),
            Some("Недостаточно места: нужно 5 ГБ, свободно 2,3 ГБ")
        );
        // 4,96 не округляется до «5»: «нужно 5, свободно 5» читалось бы как
        // ошибка самой проверки.
        assert_eq!(
            space_error(5.0, 4.96).as_deref(),
            Some("Недостаточно места: нужно 5 ГБ, свободно 4,9 ГБ")
        );
        assert_eq!(space_error(3.0, 3.0), None);
        assert_eq!(space_error(5.0, 120.5), None);
    }

    #[test]
    fn gpu_name_is_the_first_line_of_nvidia_smi() {
        assert_eq!(
            parse_gpu("NVIDIA GeForce RTX 5070 Ti\r\n").as_deref(),
            Some("NVIDIA GeForce RTX 5070 Ti")
        );
        assert_eq!(
            parse_gpu("\n  NVIDIA A\nNVIDIA B\n").as_deref(),
            Some("NVIDIA A")
        );
        assert_eq!(parse_gpu(""), None);
        assert_eq!(parse_gpu("  \r\n"), None);
    }

    #[test]
    fn wheel_of_this_version_is_preferred() {
        let tree = TempDir::new("wheel");
        assert_eq!(find_wheel(&tree.0, "0.2.0"), None);
        let other = tree.file("meet_transcriber-0.1.9-py3-none-any.whl", "");
        tree.file("uv.exe", "");
        assert_eq!(find_wheel(&tree.0, "0.2.0"), Some(other));
        let exact = tree.file("meet_transcriber-0.2.0-py3-none-any.whl", "");
        assert_eq!(find_wheel(&tree.0, "0.2.0"), Some(exact));
        assert_eq!(find_wheel(&tree.0.join("missing"), "0.2.0"), None);
    }

    #[test]
    fn without_exact_wheel_the_newest_file_wins_not_the_name_order() {
        // По строкам «0.1.9» > «0.1.10»; решает время файла, а не имя.
        let tree = TempDir::new("wheel-mtime");
        let older = tree.file("meet_transcriber-0.1.9-py3-none-any.whl", "");
        let newer = tree.file("meet_transcriber-0.1.10-py3-none-any.whl", "");
        let hour_ago = SystemTime::now() - Duration::from_secs(3600);
        File::options()
            .write(true)
            .open(&older)
            .unwrap()
            .set_modified(hour_ago)
            .unwrap();
        assert_eq!(find_wheel(&tree.0, "0.2.0"), Some(newer));
    }

    #[test]
    fn ffmpeg_dir_goes_first_in_path() {
        let joined = path_with(
            Path::new(r"C:\app\resources"),
            Some(OsStr::new(r"C:\Windows;C:\tools")),
        );
        assert_eq!(
            joined,
            OsString::from(r"C:\app\resources;C:\Windows;C:\tools")
        );
        assert_eq!(
            path_with(Path::new(r"C:\app\resources"), None),
            OsString::from(r"C:\app\resources")
        );
    }

    #[test]
    fn tail_keeps_the_last_30_lines() {
        let mut tail = VecDeque::new();
        for n in 0..45 {
            push_tail(&mut tail, format!("строка {n}"));
        }
        assert_eq!(tail.len(), 30);
        assert_eq!(tail.front().unwrap(), "строка 15");
        assert_eq!(tail.back().unwrap(), "строка 44");
    }

    #[test]
    fn throttle_lets_through_at_most_one_line_per_gap() {
        let start = Instant::now();
        let at = |ms: u64| start + Duration::from_millis(ms);
        let mut throttle = Throttle::new(Duration::from_millis(100));
        let mut offer = |ms: u64, line: &str| throttle.offer(at(ms), line.to_string());
        assert_eq!(offer(0, "a").as_deref(), Some("a"), "первая строка — сразу");
        assert_eq!(offer(30, "b"), None);
        assert_eq!(offer(99, "c"), None);
        assert_eq!(offer(100, "d").as_deref(), Some("d"));
        assert_eq!(offer(150, "e"), None);
        assert_eq!(offer(260, "f").as_deref(), Some("f"));
    }

    #[test]
    fn throttle_flushes_the_last_held_line_at_step_end() {
        let start = Instant::now();
        let mut throttle = Throttle::new(Duration::from_millis(100));
        assert!(throttle.offer(start, "скачиваю".into()).is_some());
        assert_eq!(
            throttle.offer(start + Duration::from_millis(10), "a".into()),
            None
        );
        assert_eq!(
            throttle.offer(start + Duration::from_millis(20), "готово".into()),
            None
        );
        assert_eq!(throttle.flush().as_deref(), Some("готово"));
        assert_eq!(throttle.flush(), None, "второй раз — нечего");
        // Последняя строка уже показана — досылать нечего.
        assert!(throttle
            .offer(start + Duration::from_millis(500), "x".into())
            .is_some());
        assert_eq!(throttle.flush(), None);
    }

    #[test]
    fn install_lock_belongs_to_a_living_other_process() {
        let me = std::process::id();
        assert_eq!(lock_decision(None, |_| true), LockDecision::Create);
        assert_eq!(
            lock_decision(Some(r#"{"pid": 4242}"#), |pid| pid == 4242),
            LockDecision::Busy
        );
        assert_eq!(
            lock_decision(Some(r#"{"pid": 4242}"#), |_| false),
            LockDecision::TakeOver,
            "держатель умер — замок брошен"
        );
        assert_eq!(
            lock_decision(Some("мусор"), |_| true),
            LockDecision::TakeOver
        );
        // Свой pid в файле — остаток этого же процесса, а не чужая установка.
        assert_eq!(
            lock_decision(Some(&format!(r#"{{"pid": {me}}}"#)), |_| true),
            LockDecision::TakeOver
        );
    }

    #[test]
    fn install_lock_file_is_created_refused_taken_over_and_removed() {
        let root = TempDir::new("install-lock");
        let path = root.0.join("install.lock");
        let lock = InstallLock::acquire(&root.0).unwrap();
        let raw = fs::read_to_string(&path).unwrap();
        assert_eq!(resident::lock_holder(&raw), Some(std::process::id()));
        drop(lock);
        assert!(!path.exists(), "снимается по окончании");
        // Живой чужой держатель — отдельный процесс на время проверки.
        let mut parent = Command::new("cmd")
            .args(["/C", "ping -n 30 127.0.0.1 >nul"])
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        fs::write(&path, format!(r#"{{"pid": {}}}"#, parent.id())).unwrap();
        assert_eq!(
            InstallLock::acquire(&root.0).err().as_deref(),
            Some("Установка уже идёт")
        );
        parent.kill().unwrap();
        parent.wait().unwrap();
        drop(parent);
        // Держатель умер — замок забирается.
        let lock = InstallLock::acquire(&root.0).unwrap();
        let raw = fs::read_to_string(&path).unwrap();
        assert_eq!(resident::lock_holder(&raw), Some(std::process::id()));
        drop(lock);
    }

    #[test]
    fn closing_the_job_kills_its_processes() {
        let job = Job::kill_on_close().unwrap();
        let mut child = Command::new("cmd")
            .args(["/C", "ping -n 30 127.0.0.1 >nul"])
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        assert!(job.assign(&child));
        assert!(
            child.try_wait().unwrap().is_none(),
            "пока job жив — работает"
        );
        drop(job); // как выход оболочки: Windows закрывает хэндл
        let deadline = Instant::now() + Duration::from_secs(5);
        while child.try_wait().unwrap().is_none() {
            assert!(Instant::now() < deadline, "процесс пережил закрытие job");
            thread::sleep(Duration::from_millis(50));
        }
    }

    #[test]
    fn second_install_is_refused_while_the_first_runs() {
        let first = begin_install().unwrap();
        assert!(installing(), "окно видит идущую установку");
        assert_eq!(begin_install().err().as_deref(), Some("Установка уже идёт"));
        drop(first);
        assert!(!installing());
        assert!(begin_install().is_ok(), "после окончания — снова можно");
    }

    #[test]
    fn uv_keeps_python_inside_the_app_folder() {
        let data = Path::new(r"C:\Users\u\AppData\Local\meet");
        let env = uv_env(data);
        let get = |key: &str| env.iter().find(|(k, _)| *k == key).map(|(_, v)| v.clone());
        assert_eq!(
            get("UV_PYTHON_INSTALL_DIR"),
            Some(data.join("engine").join("python").into_os_string())
        );
        assert_eq!(get("UV_PYTHON_INSTALL_BIN"), Some("0".into()));
        assert_eq!(get("UV_PYTHON_INSTALL_REGISTRY"), Some("0".into()));
        assert_eq!(get("UV_COMPILE_BYTECODE"), Some("1".into()));
        // Кэш uv — по умолчанию: на нём держится докачка после сбоя.
        assert_eq!(get("UV_CACHE_DIR"), None);
    }

    #[test]
    fn resources_are_found_in_resources_subfolder_or_flat() {
        let tree = TempDir::new("resources");
        assert_eq!(
            find_resource_dir(&tree.0, "uv.exe"),
            None,
            "dev: ресурсов нет"
        );
        tree.file("uv.exe", "");
        assert_eq!(find_resource_dir(&tree.0, "uv.exe"), Some(tree.0.clone()));
        tree.file(r"resources\uv.exe", "");
        assert_eq!(
            find_resource_dir(&tree.0, "uv.exe"),
            Some(tree.0.join("resources"))
        );
    }

    #[test]
    fn free_space_is_known_for_an_existing_drive() {
        let free = free_gb(&std::env::temp_dir().join("нет-такой-папки")).unwrap();
        assert!(free > 0.0);
    }

    #[test]
    fn streamed_run_gives_stdout_and_stderr_lines_and_exit_code() {
        let argv: Vec<String> = ["cmd", "/C", "echo one& echo two 1>&2& exit /b 3"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let mut lines = Vec::new();
        let code = run_streamed(
            &argv,
            &[("MEET_TEST", "1".into())],
            &std::env::temp_dir(),
            |line| lines.push(line),
        )
        .unwrap();
        lines.sort();
        assert_eq!(lines, vec!["one", "two"]);
        assert_eq!(code, Some(3));
        let missing = vec!["meet-no-such-program.exe".to_string()];
        assert!(run_streamed(&missing, &[], &std::env::temp_dir(), |_| {}).is_err());
    }
}
