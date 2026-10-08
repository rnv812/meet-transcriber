// Главное окно приложения: создаётся по требованию, закрытие уничтожает его
// (WebView2 возвращает память), и команды, которые веб-странице недоступны.
//
// IMPORTANT: `open_main` вызывается только из обработчиков трея/меню (главный
// поток) или через `app.run_on_main_thread`: создание окна из async-команды
// роняло приложение на Windows.

use std::path::{Path, PathBuf};
#[cfg(not(windows))]
use std::process::Command;

use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

use crate::api::Client;
use crate::engine;
use crate::logs::{self, shell_log};
use crate::platform;
use crate::resident::{self, Endpoint, ResidentStatus, Supervisor};
use crate::tray;

/// Расширения, которые резидент принимает при импорте (`IMPORT_EXTS`).
pub const MEDIA_EXTS: &[&str] = &[
    "mp3", "mp4", "m4a", "wav", "ogg", "opus", "webm", "mkv", "flac",
];

/// Адрес страницы окна; `recording` — запись, которую нужно открыть сразу,
/// `section` — раздел (`assistant` — настройки ассистента).
pub fn main_url(recording: Option<&str>, section: Option<&str>) -> String {
    let mut query = Vec::new();
    if let Some(id) = recording {
        query.push(format!("recording={}", encode_component(id)));
    }
    if let Some(section) = section {
        query.push(format!("section={}", encode_component(section)));
    }
    if query.is_empty() {
        "index.html".to_string()
    } else {
        format!("index.html?{}", query.join("&"))
    }
}

pub fn encode_component(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Запись из командной строки: `meet.exe --recording <id>` (или
/// `--recording=<id>`). `args` — полный argv, `args[0]` — сам exe. Флаг без
/// значения или со следующим флагом вместо id — `None`: окно откроется без
/// записи.
pub fn recording_arg(args: &[String]) -> Option<String> {
    let mut rest = args.iter().skip(1);
    while let Some(arg) = rest.next() {
        let value = if arg == "--recording" {
            rest.next().map(String::as_str)
        } else if let Some(value) = arg.strip_prefix("--recording=") {
            Some(value)
        } else {
            continue;
        };
        return value
            .filter(|id| !id.is_empty() && !id.starts_with("--"))
            .map(str::to_string);
    }
    None
}

/// Показать окно, создав при необходимости. Только с главного потока.
///
/// `section` новому окну уходит в адрес (`?section=…`), открытому — событием
/// `open-section` (как запись — `open-recording`).
pub fn open_main(app: &AppHandle, recording: Option<String>, section: Option<&str>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        if let Some(id) = recording {
            let _ = app.emit_to("main", "open-recording", id);
        }
        if let Some(section) = section {
            let _ = app.emit_to("main", "open-section", section);
        }
        return;
    }
    let theme = crate::appearance::startup_theme();
    let built = WebviewWindowBuilder::new(
        app,
        "main",
        WebviewUrl::App(main_url(recording.as_deref(), section).into()),
    )
    .title("Meet")
    // Рамка — в тему окна («Оформление»): у «Системной» — как в Windows.
    .theme(theme)
    .background_color(crate::appearance::canvas(theme))
    // Решение о теме — странице до её скриптов: кеша оформления может ещё не быть.
    .initialization_script(crate::appearance::theme_hint_script(theme))
    .inner_size(1180.0, 760.0)
    .min_inner_size(820.0, 520.0)
    .center()
    .build();
    match built {
        Ok(window) => {
            // «Системная»: холст — по теме ОС, а не тёмный по умолчанию.
            if theme.is_none() {
                crate::appearance::paint_canvas(&window, None);
            }
            // Значок окна — кадр icon.ico под размер, а не растянутый 16 px.
            crate::app_icon::apply(&window);
        }
        Err(error) => shell_log!("окно не открылось: {error}"),
    }
}

#[tauri::command]
pub fn endpoint() -> Option<Endpoint> {
    resident::read_endpoint()
}

/// Показать папку записи в проводнике. Приложение не заменяет файловый
/// менеджер: иногда быстрее открыть папку, чем искать её в библиотеке.
///
/// IMPORTANT: `explorer <файл>` файл запускает, поэтому только папки и только
/// свои: данные приложения и папка записей. Async — чтобы вопрос резиденту о
/// папке записей не держал главный поток.
#[tauri::command]
pub async fn open_folder(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let target = PathBuf::from(&path);
        if !target.is_dir() {
            return Err(format!("папки нет: {path}"));
        }
        if !folder_allowed(&target, &allowed_roots()) {
            shell_log!("open_folder: отказ, папка вне данных и записей: {path}");
            return Err(format!("эту папку приложение не открывает: {path}"));
        }
        // explorer (macOS — open) возвращает ненулевой код даже при успехе,
        // поэтому ждать завершения нельзя — нас интересует только сам запуск.
        platform::open_folder(&target).map_err(|error| format!("не удалось открыть папку: {error}"))
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Папка, и внутри одного из корней. Пути сравниваются после canonicalize:
/// `..`, короткие имена и регистр не выводят за корень, а сравнение по
/// компонентам не путает `root2` с `root`. Несуществующий путь — отказ.
pub fn folder_allowed(path: &Path, roots: &[PathBuf]) -> bool {
    let Ok(target) = path.canonicalize() else {
        return false;
    };
    target.is_dir()
        && roots
            .iter()
            .filter_map(|root| root.canonicalize().ok())
            .any(|root| target.starts_with(root))
}

/// Корни для `open_folder`: данные приложения всегда, папка записей и папка
/// для встреч в базе знаний (куда выгружаются встречи) — если резидент
/// отвечает. Не отвечает — только данные приложения.
fn allowed_roots() -> Vec<PathBuf> {
    let mut roots = vec![resident::data_dir()];
    let state =
        resident::read_endpoint().and_then(|endpoint| Client::new(&endpoint).get_state().ok());
    if let Some(state) = state.as_ref() {
        roots.extend(recordings_root(state));
        roots.extend(meetings_root(state));
    }
    roots
}

/// Папка записей из `/state`: `recordings_dir` — это уже `recording.out_dir`
/// или умолчание, разрешённое самим резидентом (в dev — `recordings\`
/// репозитория, которого оболочка сама не вычислит).
pub fn recordings_root(state: &serde_json::Value) -> Option<PathBuf> {
    dir_at(state, "recordings_dir")
}

/// Папка для встреч из `/state` (`export.meetings_dir`): выгруженную встречу
/// карточка открывает кнопкой «Открыть папку». Не задана — `None`.
pub fn meetings_root(state: &serde_json::Value) -> Option<PathBuf> {
    dir_at(state, "meetings_dir")
}

fn dir_at(state: &serde_json::Value, key: &str) -> Option<PathBuf> {
    state
        .get(key)
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|dir| !dir.is_empty())
        .map(PathBuf::from)
}

/// «Сохранить как» и запись UTF-8. `None` — пользователь отказался.
#[tauri::command]
pub async fn save_text(
    app: AppHandle,
    default_name: String,
    content: String,
) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(chosen) = app
            .dialog()
            .file()
            .set_file_name(&default_name)
            .blocking_save_file()
        else {
            return Ok(None);
        };
        let path = chosen.into_path().map_err(|error| error.to_string())?;
        std::fs::write(&path, content)
            .map_err(|error| format!("не удалось сохранить файл: {error}"))?;
        Ok(Some(path.to_string_lossy().into_owned()))
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Диалог выбора аудио/видео для импорта. `None` — отказ.
#[tauri::command]
pub async fn pick_media(app: AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(chosen) = app
            .dialog()
            .file()
            .add_filter("Аудио и видео", MEDIA_EXTS)
            .blocking_pick_file()
        else {
            return Ok(None);
        };
        let path = chosen.into_path().map_err(|error| error.to_string())?;
        Ok(Some(path.to_string_lossy().into_owned()))
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Что можно приложить к сообщению ассистенту кнопкой «📎»: документы, которые
/// разбирает резидент (`materials.DOC_SUFFIXES`), и картинки
/// (`participant.IMAGE_SUFFIXES`). Перетаскивание и папки идут мимо этого
/// списка — их проверяет сам резидент.
pub const CHAT_ATTACH_EXTS: &[&str] = &[
    "md", "txt", "docx", "pptx", "xlsx", "csv", "pdf", "png", "jpg", "jpeg", "gif", "webp", "bmp",
    "tif", "tiff",
];

/// Путь из диалога можно отдать резиденту как вложение чата: абсолютный, не
/// сетевой (UNC-путь `//сервер/…`) и с расширением из `CHAT_ATTACH_EXTS`.
pub fn chat_attachable(path: &Path) -> bool {
    let text = path.to_string_lossy();
    if !path.is_absolute()
        || text.starts_with("\\\\")
        || text.starts_with("//")
        || text.contains('\0')
    {
        return false;
    }
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| CHAT_ATTACH_EXTS.contains(&ext.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// Диалог «📎 Приложить» строки ввода чата ассистента: несколько файлов;
/// отказ — пустой список. Пути — только из `chat_attachable`.
#[tauri::command]
pub async fn pick_chat_files(app: AppHandle) -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(chosen) = app
            .dialog()
            .file()
            .add_filter("Документы и картинки", CHAT_ATTACH_EXTS)
            .blocking_pick_files()
        else {
            return Ok(Vec::new());
        };
        Ok(chosen
            .into_iter()
            .filter_map(|file| file.into_path().ok())
            .filter(|path| chat_attachable(path))
            .map(|path| path.to_string_lossy().into_owned())
            .collect())
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Диалог выбора папки (настройки ассистента: база знаний, заметки). `start`
/// — откуда начать, если такая папка есть. `None` — отказ.
#[tauri::command]
pub async fn pick_folder(app: AppHandle, start: Option<String>) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut dialog = app.dialog().file();
        if let Some(dir) = start.filter(|dir| Path::new(dir).is_dir()) {
            dialog = dialog.set_directory(dir);
        }
        let Some(chosen) = dialog.blocking_pick_folder() else {
            return Ok(None);
        };
        let path = chosen.into_path().map_err(|error| error.to_string())?;
        Ok(Some(path.to_string_lossy().into_owned()))
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Что `open_material` открывает приложением по умолчанию: документы и
/// картинки (материалы встречи, документы базы знаний, расшифровки).
pub const MATERIAL_EXTS: &[&str] = &[
    "md", "txt", "pdf", "docx", "doc", "pptx", "ppt", "xlsx", "xls", "csv", "odt", "ods", "odp",
    "rtf", "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff",
];

/// Никогда — даже если список выше расширят: исполняемое, скрипты, ярлыки и
/// всё, что ОС «запускает», а не показывает.
pub const REFUSED_EXTS: &[&str] = &[
    "exe",
    "com",
    "bat",
    "cmd",
    "ps1",
    "psm1",
    "psd1",
    "vbs",
    "vbe",
    "js",
    "jse",
    "wsf",
    "wsh",
    "msi",
    "msp",
    "mst",
    "scr",
    "pif",
    "lnk",
    "url",
    "hta",
    "cpl",
    "jar",
    "reg",
    "inf",
    "py",
    "pyw",
    "sh",
    "app",
    "dll",
    "sys",
    "html",
    "htm",
    "mht",
    "svg",
    "xml",
    "xsl",
    "msc",
    "scf",
    "chm",
    "iso",
    "img",
    "vhd",
    "vhdx",
    "appref-ms",
    "application",
    "gadget",
    "library-ms",
    "settingcontent-ms",
    "command",
    "tool",
    "workflow",
    "applescript",
    "scpt",
];

/// Можно ли открыть файл `path` чипом-источника чата: существующий файл
/// (не папка) с расширением из `MATERIAL_EXTS` и не из `REFUSED_EXTS`, и
/// внутри одного из корней. Сравнение — после canonicalize (`..`, ссылки,
/// регистр и короткие имена не выводят за корень; `root2` не путается с
/// `root`). → путь на диске, который открыть.
pub fn material_allowed(path: &Path, roots: &[PathBuf]) -> Option<PathBuf> {
    let text = path.to_string_lossy();
    if !path.is_absolute() || text.contains('\0') || has_stream(&text) {
        return None;
    }
    // До canonicalize — только по тексту: путь вне корней (`\\сервер\…`,
    // `\\?\GLOBALROOT\…`) не трогает ни сеть, ни устройства (ревью M2).
    if !roots
        .iter()
        .any(|root| lexically_inside(&text, &root.to_string_lossy()))
    {
        return None;
    }
    let target = path.canonicalize().ok()?;
    // Поток NTFS (`evil.exe:x.pdf`) canonicalize сохраняет — отказ (ревью M1).
    if !target.is_file() || has_stream(&plain_path(&target)) {
        return None;
    }
    // Расширение — у пути на диске: ссылка `План.pdf` на `run.bat` — это bat.
    let ext = target
        .extension()
        .and_then(|ext| ext.to_str())?
        .to_ascii_lowercase();
    if REFUSED_EXTS.contains(&ext.as_str()) || !MATERIAL_EXTS.contains(&ext.as_str()) {
        return None;
    }
    let inside = roots
        .iter()
        .filter_map(|root| root.canonicalize().ok())
        .filter(|root| root.is_dir())
        .any(|root| target.starts_with(root));
    inside.then_some(target)
}

/// В пути есть «:» после буквы диска — альтернативный поток NTFS
/// (`файл:поток`), а не обычный файл.
pub fn has_stream(path: &str) -> bool {
    let rest = path
        .strip_prefix(r"\\?\")
        .or_else(|| path.strip_prefix(r"\\.\"))
        .unwrap_or(path);
    let bytes = rest.as_bytes();
    let rest = if bytes.len() >= 2 && bytes[1] == b':' && bytes[0].is_ascii_alphabetic() {
        &rest[2..]
    } else {
        rest
    };
    rest.contains(':')
}

/// `path` по тексту внутри `root`: разделители — любые, регистр не важен,
/// сравнение по целым частям (`root2` — не `root`). Без обращения к диску.
pub fn lexically_inside(path: &str, root: &str) -> bool {
    let norm = |text: &str| text.replace('\\', "/").trim_end_matches('/').to_lowercase();
    let path = norm(path);
    let root = norm(root);
    !root.is_empty() && (path == root || path.starts_with(&format!("{root}/")))
}

/// Корни `open_material` по `/state`: база знаний (`knowledge_dir`),
/// библиотека встреч (`recordings_dir` — там и `assistant/materials`,
/// `assistant/files` каждой встречи) и папка для встреч в базе знаний
/// (`meetings_dir`).
pub fn material_roots(state: &serde_json::Value) -> Vec<PathBuf> {
    ["knowledge_dir", "recordings_dir", "meetings_dir"]
        .iter()
        .filter_map(|key| dir_at(state, key))
        .collect()
}

/// `\\?\C:\…` после canonicalize → `C:\…`: так путь понимают все программы.
fn plain_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => rest.to_string(),
        _ => text.into_owned(),
    }
}

/// Чип-источник в чате ассистента: открыть материал встречи или документ
/// базы знаний приложением по умолчанию. Только файлы из `material_allowed`
/// (корни — из `/state` резидента; не отвечает — ничего не открывается).
#[tauri::command]
pub async fn open_material(path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let roots = resident::read_endpoint()
            .and_then(|endpoint| Client::new(&endpoint).get_state().ok())
            .map(|state| material_roots(&state))
            .unwrap_or_default();
        let Some(target) = material_allowed(Path::new(&path), &roots) else {
            shell_log!("open_material: отказ: {path}");
            return Err("этот файл приложение не открывает".to_string());
        };
        shell_execute(&plain_path(&target)).map_err(|code| match code {
            // SE_ERR_NOASSOC: у типа файла нет программы (часто у `.md`).
            31 => "нет программы для этого типа файла — откройте его из папки".to_string(),
            code => format!("файл не открылся (код {code})"),
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Страницы, которые окно открывает в браузере: мастер (Hugging Face),
/// подсказки «не найден — установите» в настройках ассистента, «Скачать
/// новую версию» и «Что нового» в «О программе» — выпуски публичного
/// репозитория обновлений (`updater::UPDATE_REPO`) и прежнего форка.
/// Префикс кончается на «/»: хост дальше не продолжить (`huggingface.co.evil`).
const URL_PREFIXES: &[&str] = &[
    "https://huggingface.co/",
    "https://claude.ai/",
    "https://github.com/openai/codex/",
    "https://opencode.ai/",
    "https://github.com/rnv812/ai_transcriber/releases/",
    "https://github.com/rnv812/meet-transcriber/releases/",
];
const URL_EXACT: &[&str] = &[
    "https://github.com/openai/codex",
    "https://github.com/rnv812/ai_transcriber/releases",
    "https://github.com/rnv812/meet-transcriber/releases",
];

/// Адрес из списка и без символов, которые что-то значат для оболочки
/// Windows (`&`, `|`, `"`, `^`, пробелы, `\`, управляющие).
pub fn url_allowed(url: &str) -> bool {
    plain_url(url)
        && (URL_EXACT.contains(&url)
            || URL_PREFIXES
                .iter()
                .any(|prefix| url.len() > prefix.len() && url.starts_with(prefix)))
}

fn plain_url(url: &str) -> bool {
    url.chars()
        .all(|c| c.is_ascii_alphanumeric() || "-._~/:?=#%+".contains(c))
}

/// Ссылки на задачи Jira (M3): из адреса Jira в настройках
/// (`integrations.jira_base_url`) — префикс «https://хост[:порт]/». Только
/// https, хост из букв, цифр, точек и дефисов (не с точки или дефиса и без
/// «..»), порт — цифры; логин и пароль («@»), «?», «#», «\» — нет. Путь
/// адреса не важен: пускаем ровно этот хост. Негодный адрес — None.
pub fn jira_prefix(base: &str) -> Option<String> {
    let rest = base.trim().strip_prefix("https://")?;
    let authority = rest.split('/').next()?;
    let (host, port) = match authority.split_once(':') {
        Some((host, port)) => (host, Some(port)),
        None => (authority, None),
    };
    let host_ok = !host.is_empty()
        && host.len() <= 253
        && host
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
        && !host.starts_with(['.', '-'])
        && !host.ends_with(['.', '-'])
        && !host.contains("..");
    let port_ok = port.map_or(true, |p| {
        !p.is_empty() && p.len() <= 5 && p.chars().all(|c| c.is_ascii_digit())
    });
    (host_ok && port_ok).then(|| format!("https://{}/", authority.to_ascii_lowercase()))
}

/// Адрес на хосте Jira из настроек (`prefix` — из `jira_prefix`): хост — без
/// учёта регистра, после префикса — что-то есть, символы — как у `url_allowed`.
pub fn jira_url_allowed(url: &str, prefix: &str) -> bool {
    plain_url(url)
        && url.len() > prefix.len()
        && url
            .get(..prefix.len())
            .is_some_and(|head| head.eq_ignore_ascii_case(prefix))
}

/// Префикс Jira из `config.json` резидента; адреса нет или он негодный — None.
fn configured_jira_prefix() -> Option<String> {
    let raw = std::fs::read_to_string(resident::data_dir().join("config.json")).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    jira_prefix(value.get("integrations")?.get("jira_base_url")?.as_str()?)
}

/// Открыть страницу в браузере по умолчанию. Только адреса из списка и
/// задачи на хосте Jira из настроек (адрес читается из `config.json` при
/// каждом вызове — окно не может подсунуть свой).
///
/// Модель угроз (осознанное решение M3): адрес Jira в `config.json` пишет
/// резидент по PATCH настроек, а PATCH доступен окну. Значит, скомпрометированное
/// окно может прописать любой https-хост и затем открывать страницы на нём.
/// Это остаётся возможностью «открыть https-страницу в браузере»: ShellExecute
/// без cmd, только https, ровно один хост, без «@», «\», пробелов и метасимволов.
#[tauri::command]
pub async fn open_url(url: String) -> Result<(), String> {
    let jira = || configured_jira_prefix().is_some_and(|prefix| jira_url_allowed(&url, &prefix));
    if !url_allowed(&url) && !jira() {
        shell_log!("open_url: отказ, адрес не из списка: {url}");
        return Err("эту ссылку приложение не открывает".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || shell_open(&url))
        .await
        .map_err(|error| error.to_string())?
}

/// ShellExecuteW, а не `cmd /C start`: адрес не проходит через разбор cmd.
/// ShellExecute может отдать работу расширениям оболочки — им нужен COM в
/// однопоточном режиме, как велит документация; поток пула blocking-задач
/// инициализирует его на время вызова и освобождает после.
fn shell_open(url: &str) -> Result<(), String> {
    shell_execute(url).map_err(|code| format!("браузер не открылся (код {code})"))
}

/// ShellExecuteW «open»: страница — в браузере, exe — запуск (установщик
/// обновления, `updater`). `Err` — код ShellExecute (32 и меньше).
#[cfg(windows)]
pub(crate) fn shell_execute(target: &str) -> Result<(), isize> {
    use windows_sys::Win32::System::Com::{
        CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE,
    };
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

    let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let verb = wide("open");
    let file = wide(target);
    // SAFETY: CoInitializeEx без зарезервированного указателя. Успех (S_OK,
    // S_FALSE — уже инициализирован) парный CoUninitialize ниже; отказ
    // (RPC_E_CHANGED_MODE — поток уже в другом режиме) — не освобождаем.
    let com = unsafe {
        CoInitializeEx(
            std::ptr::null(),
            (COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE) as u32,
        )
    };
    // SAFETY: обе строки заканчиваются нулём и живут до конца вызова;
    // окно-владелец и параметры не нужны (null).
    let code = unsafe {
        ShellExecuteW(
            std::ptr::null_mut(),
            verb.as_ptr(),
            file.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            SW_SHOWNORMAL,
        )
    } as isize;
    if com >= 0 {
        // SAFETY: парный успешному CoInitializeEx на этом же потоке.
        unsafe { CoUninitialize() };
    }
    // Больше 32 — успех (так устроен ответ ShellExecute).
    if code > 32 {
        Ok(())
    } else {
        Err(code)
    }
}

/// macOS: `open` без шелла — адрес не разбирается интерпретатором команд;
/// страница — в браузере, образ диска обновления — в Finder.
#[cfg(not(windows))]
pub(crate) fn shell_execute(target: &str) -> Result<(), isize> {
    match Command::new("open").arg(target).status() {
        Ok(status) if status.success() => Ok(()),
        Ok(status) => Err(status.code().map_or(-1, |code| code as isize)),
        Err(_) => Err(0),
    }
}

/// Отметка «мастер первого запуска пройден или пропущен» в папке данных.
/// Окно хранит то же в localStorage и в настройках резидента, но ни то, ни
/// другое оболочке при старте не прочитать (резидента без движка нет).
pub const WIZARD_DONE_MARKER: &str = "wizard_done";

pub fn wizard_done(data_dir: &Path) -> bool {
    data_dir.join(WIZARD_DONE_MARKER).is_file()
}

pub fn write_wizard_done(data_dir: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(data_dir)?;
    std::fs::write(data_dir.join(WIZARD_DONE_MARKER), b"")
}

#[tauri::command]
pub fn mark_wizard_done() -> Result<(), String> {
    write_wizard_done(&resident::data_dir())
        .map_err(|error| format!("отметка мастера не записалась: {error}"))
}

/// Открыть ли окно с мастером при старте: только в установленном приложении
/// (в dev резидент из .venv репозитория), пока движка нет и «Пропустить» не
/// нажимали. Окно само решит показать мастер по тем же правилам. Движок,
/// который оболочка ставит в фоне сама (обновление приложения,
/// `engine::Upkeep`), — не повод для мастера.
pub fn wizard_at_startup(
    release: bool,
    engine_installed: bool,
    wizard_done: bool,
    engine_upkeep: bool,
) -> bool {
    release && !engine_installed && !wizard_done && !engine_upkeep
}

/// Первый запуск: сразу окно, а не молчаливая иконка в трее без движка.
/// Из `setup` (главный поток).
pub fn open_wizard_on_first_run(app: &AppHandle, engine_upkeep: bool) {
    let data = resident::data_dir();
    // Папка движка и моделей недоступна: окно с ошибкой и «Вернуть на
    // системный диск», а не мастер, который поставил бы движок заново.
    if crate::storage::blocked(&data).is_some() {
        open_main(app, None, None);
        return;
    }
    let version = app.package_info().version.to_string();
    let home = crate::storage::home(&data);
    let installed = engine::is_installed(&engine::env_dir(&home, &version), &version);
    if wizard_at_startup(
        !cfg!(debug_assertions),
        installed,
        wizard_done(&data),
        engine_upkeep,
    ) {
        shell_log!("движок {version} не установлен — открываю мастер первого запуска");
        open_main(app, None, None);
    }
}

/// Раздел «Запись экрана» в настройках конфиденциальности macOS — адрес
/// зашит: окно открывает только его, не произвольную ссылку.
pub const SCREEN_RECORDING_PANE: &str =
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";

/// «Открыть настройки» у плашки «Звук собеседников не записывается» (macOS).
#[tauri::command]
pub async fn open_screen_recording_settings() -> Result<(), String> {
    if !cfg!(target_os = "macos") {
        return Err("Только для macOS".to_string());
    }
    tauri::async_runtime::spawn_blocking(|| {
        shell_execute(SCREEN_RECORDING_PANE)
            .map_err(|code| format!("Системные настройки не открылись (код {code})"))
    })
    .await
    .map_err(|error| error.to_string())?
}

/// «Открыть журнал» из окна (мастер ждал сервис, а тот не запустился): та
/// же папка, что у пункта трея, — журналы, а без них папка данных.
#[tauri::command]
pub async fn open_logs() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| {
        let data = resident::data_dir();
        let target = tray::log_folder(&logs::resident_log(&data), &data);
        platform::open_folder(&target)
            .map_err(|error| format!("не удалось открыть журнал: {error}"))
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
pub fn resident_status(app: AppHandle) -> String {
    match app.state::<Supervisor>().status() {
        ResidentStatus::Starting => "starting",
        ResidentStatus::Running => "running",
        ResidentStatus::External => "external",
        ResidentStatus::ExternalNoApi => "external-no-api",
        ResidentStatus::Failed { .. } => "failed",
        ResidentStatus::EngineMissing => "engine-missing",
        ResidentStatus::EngineUpdating { .. } => "engine-updating",
        ResidentStatus::StorageMissing { .. } => "storage-missing",
        ResidentStatus::StorageUnreadable => "storage-unreadable",
        ResidentStatus::Quitting => "quitting",
    }
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn main_url_carries_recording() {
        assert_eq!(main_url(None, None), "index.html");
        assert_eq!(
            main_url(Some("2026-09-30_16-04_import"), None),
            "index.html?recording=2026-09-30_16-04_import"
        );
        assert_eq!(main_url(Some("a b"), None), "index.html?recording=a%20b");
    }

    #[test]
    fn main_url_carries_section() {
        assert_eq!(
            main_url(None, Some("assistant")),
            "index.html?section=assistant"
        );
        assert_eq!(
            main_url(Some("rec"), Some("assistant")),
            "index.html?recording=rec&section=assistant"
        );
    }

    #[test]
    fn chat_attachments_keep_only_known_local_files() {
        let root = if cfg!(windows) {
            r"C:\Users\me\"
        } else {
            "/home/me/"
        };
        for name in [
            "План.pptx",
            "notes.MD",
            "скрин.PNG",
            "photo.jpeg",
            "table.xlsx",
            "doc.pdf",
        ] {
            let path = PathBuf::from(format!("{root}{name}"));
            assert!(chat_attachable(&path), "{name}");
        }
        for name in [
            "setup.exe",
            "script.ps1",
            "archive.zip",
            "noext",
            "page.html",
        ] {
            let path = PathBuf::from(format!("{root}{name}"));
            assert!(!chat_attachable(&path), "{name}");
        }
        assert!(!chat_attachable(Path::new("relative/План.pptx")));
        assert!(!chat_attachable(Path::new(r"\\server\share\План.pptx")));
        assert!(!chat_attachable(Path::new("//server/share/План.pptx")));
    }

    #[test]
    fn open_material_keeps_to_documents_inside_the_roots() {
        let tree = Tree::new("material");
        let root = tree.0.join("root");
        let meeting = root
            .join("2026-09-30_16-04")
            .join("assistant")
            .join("materials");
        std::fs::create_dir_all(&meeting).unwrap();
        std::fs::write(meeting.join("a1.txt"), b"x").unwrap();
        std::fs::write(root.join("План.pdf"), b"%PDF").unwrap();
        std::fs::write(root.join("notes.MD"), b"x").unwrap();
        std::fs::write(root.join("page.html"), b"x").unwrap();
        std::fs::write(root.join("noext"), b"x").unwrap();
        std::fs::write(root.join("tool.ps1"), b"x").unwrap();
        std::fs::write(tree.0.join("outside").join("secret.pdf"), b"x").unwrap();
        let sibling = tree.0.join("root2");
        std::fs::create_dir_all(&sibling).unwrap();
        std::fs::write(sibling.join("near.pdf"), b"x").unwrap();
        let roots = [root.clone()];

        for ok in [
            root.join("План.pdf"),
            root.join("notes.MD"),
            meeting.join("a1.txt"),
        ] {
            assert!(material_allowed(&ok, &roots).is_some(), "{}", ok.display());
        }
        // Исполняемое и скрипты — никогда; без расширения и не из списка — нет.
        for name in ["run.bat", "tool.ps1", "page.html", "noext"] {
            assert!(
                material_allowed(&root.join(name), &roots).is_none(),
                "{name}"
            );
        }
        // Вне корней, через «..», соседний корень с тем же началом имени.
        assert!(material_allowed(&tree.0.join("outside").join("secret.pdf"), &roots).is_none());
        let escape = root.join("..").join("outside").join("secret.pdf");
        assert!(escape.is_file(), "файл есть — отказ только из-за корня");
        assert!(material_allowed(&escape, &roots).is_none());
        assert!(material_allowed(&sibling.join("near.pdf"), &roots).is_none());
        // Папка, несуществующий файл, относительный путь, нет корней.
        assert!(material_allowed(&root.join("2026-09-30_16-04"), &roots).is_none());
        assert!(material_allowed(&root.join("нет.pdf"), &roots).is_none());
        assert!(material_allowed(Path::new("relative/План.pdf"), &roots).is_none());
        assert!(material_allowed(&root.join("План.pdf"), &[]).is_none());
        for ext in REFUSED_EXTS {
            assert!(!MATERIAL_EXTS.contains(ext), "{ext} и разрешён, и запрещён");
        }
        // Двойное расширение и верхний регистр: решает последнее расширение.
        std::fs::write(root.join("x.pdf.exe"), b"x").unwrap();
        std::fs::write(root.join("X.PDF"), b"x").unwrap();
        assert!(material_allowed(&root.join("x.pdf.exe"), &roots).is_none());
        assert!(material_allowed(&root.join("X.PDF"), &roots).is_some());
    }

    #[test]
    fn open_material_refuses_ntfs_streams() {
        assert!(has_stream(r"C:\docs\evil.exe:x.pdf"));
        assert!(has_stream(r"\\?\C:\docs\a.txt:evil.exe"));
        assert!(!has_stream(r"C:\docs\План.pdf"));
        assert!(!has_stream(r"\\?\C:\docs\План.pdf"));
        let tree = Tree::new("stream");
        let root = tree.0.join("root");
        let roots = [root.clone()];
        std::fs::write(root.join("evil.exe"), b"x").unwrap();
        let stream = PathBuf::from(format!("{}:x.pdf", root.join("evil.exe").display()));
        // На NTFS поток создаётся записью; где потоков нет — путь просто не файл.
        let _ = std::fs::write(&stream, b"%PDF");
        assert!(material_allowed(&stream, &roots).is_none());
        let stream = PathBuf::from(format!("{}:evil.exe", root.join("a.txt").display()));
        std::fs::write(root.join("a.txt"), b"x").unwrap();
        let _ = std::fs::write(&stream, b"MZ");
        assert!(material_allowed(&stream, &roots).is_none());
    }

    #[test]
    fn open_material_checks_roots_before_touching_the_path() {
        assert!(lexically_inside(r"C:\KB\Проекты\a.pdf", "c:/kb"));
        assert!(lexically_inside("C:/KB", r"C:\KB\"));
        assert!(!lexically_inside(r"C:\KB2\a.pdf", r"C:\KB"));
        assert!(!lexically_inside(r"\\host\share\a.pdf", r"C:\KB"));
        assert!(!lexically_inside(r"C:\KB\a.pdf", ""));
        let tree = Tree::new("lexical");
        let roots = [tree.0.join("root")];
        // Сетевой путь и пути устройств вне корней — отказ без обращения к ним.
        for path in [
            r"\\host\share\x.pdf",
            r"\\?\GLOBALROOT\Device\HarddiskVolume1\x.pdf",
            r"\\.\C:\x.pdf",
            r"\\?\UNC\host\share\x.pdf",
        ] {
            assert!(
                material_allowed(Path::new(path), &roots).is_none(),
                "{path}"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn open_material_refuses_a_link_out_of_the_root() {
        let tree = Tree::new("link");
        let root = tree.0.join("root");
        std::fs::write(tree.0.join("outside").join("secret.pdf"), b"x").unwrap();
        std::os::unix::fs::symlink(
            tree.0.join("outside").join("secret.pdf"),
            root.join("link.pdf"),
        )
        .unwrap();
        assert!(material_allowed(&root.join("link.pdf"), &[root]).is_none());
    }

    #[cfg(windows)]
    #[test]
    fn open_material_refuses_a_link_out_of_the_root() {
        let tree = Tree::new("link");
        let root = tree.0.join("root");
        std::fs::write(tree.0.join("outside").join("secret.pdf"), b"x").unwrap();
        // Ссылку на файл без прав разработчика Windows не даст — тогда пропуск.
        if std::os::windows::fs::symlink_file(
            tree.0.join("outside").join("secret.pdf"),
            root.join("link.pdf"),
        )
        .is_ok()
        {
            assert!(
                material_allowed(&root.join("link.pdf"), std::slice::from_ref(&root)).is_none()
            );
        }
        // Junction на папку снаружи создаётся без прав — путь через неё тоже вне корня.
        let junction = root.join("jn");
        let made = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&junction)
            .arg(tree.0.join("outside"))
            .output()
            .map(|out| out.status.success())
            .unwrap_or(false);
        if made {
            assert!(material_allowed(&junction.join("secret.pdf"), &[root]).is_none());
        }
    }

    #[test]
    fn open_material_roots_come_from_the_state() {
        let state = serde_json::json!({
            "knowledge_dir": "C:/kb", "recordings_dir": "D:/rec", "meetings_dir": null,
        });
        assert_eq!(
            material_roots(&state),
            vec![PathBuf::from("C:/kb"), PathBuf::from("D:/rec")]
        );
        assert!(material_roots(&serde_json::json!({})).is_empty());
        assert_eq!(plain_path(Path::new(r"\\?\C:\kb\a.pdf")), r"C:\kb\a.pdf");
        assert_eq!(
            plain_path(Path::new(r"\\?\UNC\srv\a.pdf")),
            r"\\?\UNC\srv\a.pdf"
        );
    }

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    #[test]
    fn recording_arg_reads_the_id_after_the_flag() {
        assert_eq!(
            recording_arg(&argv(&["meet.exe", "--recording", "2026-09-30_16-04"])),
            Some("2026-09-30_16-04".to_string())
        );
        assert_eq!(
            recording_arg(&argv(&["meet.exe", "--recording=2026-09-30_16-04"])),
            Some("2026-09-30_16-04".to_string())
        );
    }

    /// Временное дерево: `root` (разрешённый корень) и `outside` рядом.
    struct Tree(PathBuf);

    impl Tree {
        fn new(name: &str) -> Self {
            let base = std::env::temp_dir()
                .join(format!("meet-open-folder-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&base);
            std::fs::create_dir_all(base.join("root").join("2026-09-30_16-04")).unwrap();
            std::fs::create_dir_all(base.join("outside")).unwrap();
            std::fs::write(base.join("root").join("run.bat"), b"calc").unwrap();
            Tree(base)
        }
    }

    impl Drop for Tree {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn folder_under_a_root_is_allowed() {
        let tree = Tree::new("inside");
        let roots = [tree.0.join("root")];
        assert!(folder_allowed(
            &tree.0.join("root").join("2026-09-30_16-04"),
            &roots
        ));
        assert!(folder_allowed(&tree.0.join("root"), &roots));
    }

    #[test]
    fn file_is_never_opened() {
        // explorer.exe <файл> — это запуск файла, а не показ папки.
        let tree = Tree::new("file");
        let roots = [tree.0.join("root")];
        assert!(!folder_allowed(
            &tree.0.join("root").join("run.bat"),
            &roots
        ));
    }

    #[test]
    fn folder_outside_the_roots_is_refused() {
        let tree = Tree::new("outside");
        let roots = [tree.0.join("root")];
        assert!(!folder_allowed(&tree.0.join("outside"), &roots));
        assert!(!folder_allowed(&tree.0.join("missing"), &roots));
        assert!(!folder_allowed(&tree.0.join("outside"), &[]));
    }

    #[test]
    fn dot_dot_cannot_escape_a_root() {
        let tree = Tree::new("dotdot");
        let roots = [tree.0.join("root")];
        let escape = tree.0.join("root").join("..").join("outside");
        assert!(
            escape.is_dir(),
            "путь существует — отказ только из-за корня"
        );
        assert!(!folder_allowed(&escape, &roots));
    }

    #[test]
    fn root_name_prefix_is_not_a_root() {
        // «root2» начинается с «root», но в корень не входит.
        let tree = Tree::new("prefix");
        std::fs::create_dir_all(tree.0.join("root2")).unwrap();
        assert!(!folder_allowed(
            &tree.0.join("root2"),
            &[tree.0.join("root")]
        ));
    }

    #[test]
    fn meetings_root_comes_from_the_state_snapshot() {
        let state =
            serde_json::json!({"recordings_dir": r"D:\rec", "meetings_dir": r"E:\kb\Встречи"});
        assert_eq!(meetings_root(&state), Some(PathBuf::from(r"E:\kb\Встречи")));
        assert_eq!(
            meetings_root(&serde_json::json!({"meetings_dir": null})),
            None
        );
        assert_eq!(meetings_root(&serde_json::json!({})), None);
    }

    #[test]
    fn recordings_root_comes_from_the_state_snapshot() {
        let state = serde_json::json!({"recordings_dir": r"D:\rec"});
        assert_eq!(recordings_root(&state), Some(PathBuf::from(r"D:\rec")));
        assert_eq!(recordings_root(&serde_json::json!({})), None);
        assert_eq!(
            recordings_root(&serde_json::json!({"recordings_dir": " "})),
            None
        );
    }

    #[test]
    fn recording_arg_is_none_without_a_usable_id() {
        assert_eq!(recording_arg(&argv(&["meet.exe"])), None);
        assert_eq!(recording_arg(&argv(&[])), None);
        // Флаг без значения — просто окно, без записи.
        assert_eq!(recording_arg(&argv(&["meet.exe", "--recording"])), None);
        assert_eq!(recording_arg(&argv(&["meet.exe", "--recording="])), None);
        // Следующий флаг — не id записи.
        assert_eq!(
            recording_arg(&argv(&["meet.exe", "--recording", "--other"])),
            None
        );
        // Сам exe (argv[0]) не разбирается как флаг.
        assert_eq!(recording_arg(&argv(&["--recording", "x"])), None);
    }

    #[test]
    fn url_allowed_only_for_known_pages() {
        for url in [
            "https://huggingface.co/pyannote/speaker-diarization-community-1",
            "https://huggingface.co/settings/tokens",
            "https://claude.ai/code",
            "https://github.com/openai/codex",
            "https://github.com/openai/codex/releases",
            "https://opencode.ai/docs/",
            "https://github.com/rnv812/ai_transcriber/releases",
            "https://github.com/rnv812/ai_transcriber/releases/tag/v0.1.0",
            "https://github.com/rnv812/meet-transcriber/releases",
            "https://github.com/rnv812/meet-transcriber/releases/tag/v0.2.0",
        ] {
            assert!(url_allowed(url), "{url}");
        }
        for url in [
            // Чужой хост, похожий на свой.
            "https://huggingface.co.evil.example/x",
            "https://huggingface.com/x",
            "http://huggingface.co/x",
            "https://huggingface.co",
            "https://github.com/openai/codexx",
            "https://github.com/openai/other",
            "https://github.com/evil/codex",
            "https://opencode.ai.evil.example/docs/",
            "http://opencode.ai/docs/",
            // Только Releases форка: не сам репозиторий и не чужой форк.
            "https://github.com/rnv812/ai_transcriber",
            "https://github.com/rnv812/ai_transcriber/issues",
            "https://github.com/rnv812/ai_transcriber/releasesx",
            "https://github.com/rnv812/ai_transcriber_evil/releases",
            "https://github.com/rnv812/meet-transcriber",
            "https://github.com/rnv812/meet-transcriber/releasesx",
            "https://github.com/rnv812/meet-transcriber-evil/releases",
            "https://github.com/evil/ai_transcriber/releases",
            "file:///C:/Windows/System32/calc.exe",
            "C:\\Windows\\System32\\calc.exe",
            "",
            // Метасимволы cmd и пробелы: адрес передаётся как есть, без них.
            "https://huggingface.co/x&calc",
            "https://huggingface.co/x|calc",
            "https://huggingface.co/x\"calc",
            "https://huggingface.co/x calc",
            "https://huggingface.co/x^calc",
            "https://huggingface.co/x\ncalc",
            "https://huggingface.co/x\\..\\calc",
        ] {
            assert!(!url_allowed(url), "{url:?}");
        }
    }

    #[test]
    fn jira_prefix_takes_only_a_plain_https_host() {
        assert_eq!(
            jira_prefix("https://jira.example.com").as_deref(),
            Some("https://jira.example.com/")
        );
        assert_eq!(
            jira_prefix(" https://Jira.Example.com/path/ ").as_deref(),
            Some("https://jira.example.com/")
        );
        assert_eq!(
            jira_prefix("https://jira.example.com:8443").as_deref(),
            Some("https://jira.example.com:8443/")
        );
        for base in [
            "",
            "http://jira.example.com",
            "jira.example.com",
            "https://",
            "https://user:pass@jira.example.com",
            "https://user@jira.example.com",
            "https://.example.com",
            "https://jira.example.com.",
            "https://jira..example.com",
            "https://jira.example.com:",
            "https://jira.example.com:80a",
            "https://jira.example.com:123456",
            "https://jira.example.com\\@evil.com",
            "https://jira.example.com?x=1",
            "https://jira.example.com#x",
            "https://jira example.com",
            "javascript:alert(1)",
        ] {
            assert_eq!(jira_prefix(base), None, "{base:?}");
        }
    }

    #[test]
    fn jira_links_only_on_the_configured_host() {
        let prefix = jira_prefix("https://jira.example.com").unwrap();
        for url in [
            "https://jira.example.com/browse/SPR-131",
            "https://JIRA.example.com/browse/SPR-1",
            "https://jira.example.com/jira/browse/OPS-7",
        ] {
            assert!(jira_url_allowed(url, &prefix), "{url}");
        }
        for url in [
            "https://jira.example.com",
            "https://jira.example.com/",
            "http://jira.example.com/browse/SPR-1",
            "https://jira.example.com.evil.com/browse/SPR-1",
            "https://jira.example.com@evil.com/browse/SPR-1",
            "https://evil.com/jira.example.com/browse/SPR-1",
            "https://jira.example.co/browse/SPR-1",
            "https://jira.example.com:8443/browse/SPR-1",
            "https://jira.example.com/browse/SPR-1&calc",
            "https://jira.example.com/browse/SPR-1 calc",
            "https://jira.example.com/browse/..\\..\\calc",
        ] {
            assert!(!jira_url_allowed(url, &prefix), "{url:?}");
        }
        // Обычный список это не расширяет: без настроек Jira не открывается.
        assert!(!url_allowed("https://jira.example.com/browse/SPR-131"));
    }

    #[test]
    fn wizard_opens_at_startup_only_in_release_without_engine_and_without_skip() {
        assert!(wizard_at_startup(true, false, false, false));
        // Движок есть — обычный запуск в трей.
        assert!(!wizard_at_startup(true, true, false, false));
        // «Пропустить» уже нажимали — мастер сам не открывается никогда.
        assert!(!wizard_at_startup(true, false, true, false));
        // dev: резидент из .venv репозитория, мастер не мешает.
        assert!(!wizard_at_startup(false, false, false, false));
        // Обновление приложения: движок ставится в фоне сам — без мастера.
        assert!(!wizard_at_startup(true, false, false, true));
    }

    #[test]
    fn wizard_done_marker_lives_in_the_data_dir() {
        let base = std::env::temp_dir().join(format!("meet-wizard-marker-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        assert!(!wizard_done(&base));
        write_wizard_done(&base).unwrap();
        assert!(wizard_done(&base));
        assert!(base.join(WIZARD_DONE_MARKER).is_file());
        // Повторная отметка не ломается.
        write_wizard_done(&base).unwrap();
        let _ = std::fs::remove_dir_all(&base);
    }
}
