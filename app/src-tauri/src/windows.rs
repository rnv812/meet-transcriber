// Главное окно приложения: создаётся по требованию, закрытие уничтожает его
// (WebView2 возвращает память), и команды, которые веб-странице недоступны.
//
// IMPORTANT: `open_main` вызывается только из обработчиков трея/меню (главный
// поток) или через `app.run_on_main_thread`: создание окна из async-команды
// роняло приложение на Windows.

use std::path::{Path, PathBuf};
use std::process::Command;

use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

use crate::api::Client;
use crate::engine;
use crate::logs::{self, shell_log};
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
    let built = WebviewWindowBuilder::new(
        app,
        "main",
        WebviewUrl::App(main_url(recording.as_deref(), section).into()),
    )
    .title("Meet")
    .inner_size(1180.0, 760.0)
    .min_inner_size(820.0, 520.0)
    .center()
    .build();
    if let Err(error) = built {
        shell_log!("окно не открылось: {error}");
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
        Command::new("explorer")
            .arg(&target)
            .spawn()
            .map(|_| ())
            // explorer возвращает ненулевой код даже при успехе, поэтому ждать
            // завершения нельзя — нас интересует только сам запуск.
            .map_err(|error| format!("не удалось открыть папку: {error}"))
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

/// Страницы, которые окно открывает в браузере: мастер (Hugging Face),
/// подсказки «не найден — установите» в настройках ассистента, «Скачать
/// новую версию» и «Что нового» в «О программе» — выпуски публичного
/// репозитория обновлений (`updater::UPDATE_REPO`) и прежнего форка.
/// Префикс кончается на «/»: хост дальше не продолжить (`huggingface.co.evil`).
const URL_PREFIXES: &[&str] = &[
    "https://huggingface.co/",
    "https://claude.ai/",
    "https://github.com/openai/codex/",
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
    let plain = url
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "-._~/:?=#%+".contains(c));
    plain
        && (URL_EXACT.contains(&url)
            || URL_PREFIXES
                .iter()
                .any(|prefix| url.len() > prefix.len() && url.starts_with(prefix)))
}

/// Открыть страницу в браузере по умолчанию. Только адреса из списка.
#[tauri::command]
pub async fn open_url(url: String) -> Result<(), String> {
    if !url_allowed(&url) {
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

#[cfg(not(windows))]
pub(crate) fn shell_execute(_target: &str) -> Result<(), isize> {
    Err(0)
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
    let version = app.package_info().version.to_string();
    let installed = engine::is_installed(&engine::env_dir(&data, &version), &version);
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

/// «Открыть журнал» из окна (мастер ждал сервис, а тот не запустился): та
/// же папка, что у пункта трея, — журналы, а без них папка данных.
#[tauri::command]
pub async fn open_logs() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| {
        let data = resident::data_dir();
        let target = tray::log_folder(&logs::resident_log(&data), &data);
        Command::new("explorer")
            .arg(&target)
            .spawn()
            .map(|_| ())
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
