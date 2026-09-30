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
use crate::logs::shell_log;
use crate::resident::{self, Endpoint, ResidentStatus, Supervisor};

/// Расширения, которые резидент принимает при импорте (`IMPORT_EXTS`).
pub const MEDIA_EXTS: &[&str] = &[
    "mp3", "mp4", "m4a", "wav", "ogg", "opus", "webm", "mkv", "flac",
];

/// Адрес страницы окна; `recording` — запись, которую нужно открыть сразу.
pub fn main_url(recording: Option<&str>) -> String {
    match recording {
        None => "index.html".to_string(),
        Some(id) => format!("index.html?recording={}", encode_component(id)),
    }
}

fn encode_component(text: &str) -> String {
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
pub fn open_main(app: &AppHandle, recording: Option<String>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        if let Some(id) = recording {
            let _ = app.emit_to("main", "open-recording", id);
        }
        return;
    }
    let built = WebviewWindowBuilder::new(
        app,
        "main",
        WebviewUrl::App(main_url(recording.as_deref()).into()),
    )
    .title("meet")
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

/// Корни для `open_folder`: данные приложения всегда, папка записей — если
/// резидент отвечает. Не отвечает — только данные приложения.
fn allowed_roots() -> Vec<PathBuf> {
    let mut roots = vec![resident::data_dir()];
    let state =
        resident::read_endpoint().and_then(|endpoint| Client::new(&endpoint).get_state().ok());
    if let Some(dir) = state.as_ref().and_then(recordings_root) {
        roots.push(dir);
    }
    roots
}

/// Папка записей из `/state`: `recordings_dir` — это уже `recording.out_dir`
/// или умолчание, разрешённое самим резидентом (в dev — `recordings\`
/// репозитория, которого оболочка сама не вычислит).
pub fn recordings_root(state: &serde_json::Value) -> Option<PathBuf> {
    state
        .get("recordings_dir")
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

#[tauri::command]
pub fn resident_status(app: AppHandle) -> String {
    match app.state::<Supervisor>().status() {
        ResidentStatus::Starting => "starting",
        ResidentStatus::Running => "running",
        ResidentStatus::External => "external",
        ResidentStatus::ExternalNoApi => "external-no-api",
        ResidentStatus::Failed { .. } => "failed",
    }
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn main_url_carries_recording() {
        assert_eq!(main_url(None), "index.html");
        assert_eq!(
            main_url(Some("2026-09-30_16-04_import")),
            "index.html?recording=2026-09-30_16-04_import"
        );
        assert_eq!(main_url(Some("a b")), "index.html?recording=a%20b");
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
}
