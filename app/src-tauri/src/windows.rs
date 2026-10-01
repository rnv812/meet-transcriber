// Главное окно приложения: создаётся по требованию, закрытие уничтожает его
// (WebView2 возвращает память), и команды, которые веб-странице недоступны.
//
// IMPORTANT: `open_main` вызывается только из обработчиков трея/меню (главный
// поток) или через `app.run_on_main_thread`: создание окна из async-команды
// роняло приложение на Windows.

use std::path::{Path, PathBuf};
use std::process::Command;

use tauri::{
    AppHandle, Emitter, Manager, Monitor, PhysicalPosition, PhysicalSize, WebviewUrl,
    WebviewWindowBuilder,
};
use tauri_plugin_dialog::DialogExt;

use crate::api::Client;
use crate::engine;
use crate::logs::shell_log;
use crate::resident::{self, Endpoint, ResidentStatus, Supervisor};

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
    .title("meet")
    .inner_size(1180.0, 760.0)
    .min_inner_size(820.0, 520.0)
    .center()
    .build();
    if let Err(error) = built {
        shell_log!("окно не открылось: {error}");
    }
}

/// Плавающая панель ассистента (`live.html`).
pub const LIVE_LABEL: &str = "live";
const LIVE_WIDTH: f64 = 360.0;
const LIVE_MIN_HEIGHT: f64 = 120.0;
const LIVE_MAX_HEIGHT: f64 = 520.0;
/// Отступ от краёв рабочей области, логические пиксели.
const LIVE_MARGIN: f64 = 16.0;

/// Рабочая область монитора (без панели задач) в физических пикселях и его
/// масштаб — как их отдаёт `Monitor`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Screen {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale: f64,
}

impl Screen {
    fn of(monitor: &Monitor) -> Screen {
        let area = monitor.work_area();
        Screen {
            x: area.position.x,
            y: area.position.y,
            width: area.size.width,
            height: area.size.height,
            scale: monitor.scale_factor(),
        }
    }
}

/// Где встаёт панель: правый нижний угол рабочей области (над панелью задач)
/// с отступом 16 px. (x, y, ширина, высота) — логические пиксели, как их
/// берёт `WebviewWindowBuilder`.
pub fn live_window_rect(screen: Screen) -> (f64, f64, f64, f64) {
    let scale = if screen.scale.is_finite() && screen.scale > 0.0 {
        screen.scale
    } else {
        1.0
    };
    let right = (f64::from(screen.x) + f64::from(screen.width)) / scale;
    let bottom = (f64::from(screen.y) + f64::from(screen.height)) / scale;
    (
        right - LIVE_MARGIN - LIVE_WIDTH,
        bottom - LIVE_MARGIN - LIVE_MIN_HEIGHT,
        LIVE_WIDTH,
        LIVE_MIN_HEIGHT,
    )
}

/// Высота, которую просит панель, в пределах 120..520 (логические пиксели).
pub fn live_height(requested: f64) -> f64 {
    if requested.is_nan() {
        return LIVE_MIN_HEIGHT;
    }
    requested.clamp(LIVE_MIN_HEIGHT, LIVE_MAX_HEIGHT)
}

/// Новый верх окна, чтобы при смене высоты нижний край остался на месте:
/// панель растёт вверх, от панели задач.
pub fn anchored_top(top: f64, height: f64, new_height: f64) -> f64 {
    top + height - new_height
}

/// Порядок двух вызовов при смене высоты панели.
#[derive(Debug, PartialEq, Eq)]
pub enum ResizeOrder {
    /// Растёт: сначала поднять верх, потом вытянуть.
    MoveThenSize,
    /// Сжимается: сначала укоротить, потом опустить.
    SizeThenMove,
}

/// Между двумя вызовами окно на кадр видно: при обратном порядке растущая
/// панель свисала бы ниже нижнего края (за панель задач), а сжимающаяся —
/// на кадр уезжала бы вниз целиком.
pub fn resize_order(height: f64, new_height: f64) -> ResizeOrder {
    if new_height > height {
        ResizeOrder::MoveThenSize
    } else {
        ResizeOrder::SizeThenMove
    }
}

/// Открыть панель ассистента. Только с главного потока (`run_on_main_thread`
/// из опроса трея). Уже открыта — не трогаем: фокус она не забирает.
pub fn open_live(app: &AppHandle) {
    if app.get_webview_window(LIVE_LABEL).is_some() {
        return;
    }
    let mut builder =
        WebviewWindowBuilder::new(app, LIVE_LABEL, WebviewUrl::App("live.html".into()))
            .title("meet — ассистент")
            .inner_size(LIVE_WIDTH, LIVE_MIN_HEIGHT)
            .resizable(false)
            .decorations(false)
            .transparent(true)
            // Тень Windows у окна без рамки — светлая каёмка вокруг
            // скруглённой панели.
            .shadow(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .focused(false);
    match app.primary_monitor() {
        Ok(Some(monitor)) => {
            let (x, y, _, _) = live_window_rect(Screen::of(&monitor));
            builder = builder.position(x, y);
        }
        Ok(None) => shell_log!("панель ассистента: монитор не найден, позиция по умолчанию"),
        Err(error) => shell_log!("панель ассистента: монитор не прочитан: {error}"),
    }
    if let Err(error) = builder.build() {
        shell_log!("панель ассистента не открылась: {error}");
    }
}

/// Закрыть панель ассистента (ассистент остановился). Закрыта человеком —
/// ничего не делаем.
pub fn close_live(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(LIVE_LABEL) {
        if let Err(error) = window.destroy() {
            shell_log!("панель ассистента не закрылась: {error}");
        }
    }
}

/// Панель меняет высоту под содержимое (120..520), нижний край на месте.
#[tauri::command]
pub fn live_resize(app: AppHandle, height: f64) -> Result<(), String> {
    let window = app
        .get_webview_window(LIVE_LABEL)
        .ok_or("панели ассистента нет")?;
    let scale = window.scale_factor().map_err(|error| error.to_string())?;
    let position = window.outer_position().map_err(|error| error.to_string())?;
    // Без рамки и тени внешний размер совпадает с внутренним; для якоря
    // берём внешний (это край окна на экране), задаём — внутренний.
    let outer = window.outer_size().map_err(|error| error.to_string())?;
    let inner = window.inner_size().map_err(|error| error.to_string())?;
    let new_height = (live_height(height) * scale).round();
    let frame = f64::from(outer.height) - f64::from(inner.height);
    let top = anchored_top(
        f64::from(position.y),
        f64::from(outer.height),
        new_height + frame,
    );
    let size = || {
        window
            .set_size(PhysicalSize::new(inner.width, new_height as u32))
            .map_err(|error| error.to_string())
    };
    let place = || {
        window
            .set_position(PhysicalPosition::new(position.x, top.round() as i32))
            .map_err(|error| error.to_string())
    };
    match resize_order(f64::from(outer.height), new_height + frame) {
        ResizeOrder::MoveThenSize => {
            place()?;
            size()
        }
        ResizeOrder::SizeThenMove => {
            size()?;
            place()
        }
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
/// подсказки «не найден — установите» в настройках ассистента и «Скачать
/// новую версию» (Releases форка) в «О программе». Префикс кончается на
/// «/»: хост дальше не продолжить (`huggingface.co.evil`).
const URL_PREFIXES: &[&str] = &[
    "https://huggingface.co/",
    "https://claude.ai/",
    "https://github.com/openai/codex/",
    "https://github.com/rnv812/ai_transcriber/releases/",
];
const URL_EXACT: &[&str] = &[
    "https://github.com/openai/codex",
    "https://github.com/rnv812/ai_transcriber/releases",
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
#[cfg(windows)]
fn shell_open(url: &str) -> Result<(), String> {
    use windows_sys::Win32::System::Com::{
        CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE,
    };
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

    let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let verb = wide("open");
    let file = wide(url);
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
        Err(format!("браузер не открылся (код {code})"))
    }
}

#[cfg(not(windows))]
fn shell_open(_url: &str) -> Result<(), String> {
    Err("открыть страницу можно только в Windows".to_string())
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
/// нажимали. Окно само решит показать мастер по тем же правилам.
pub fn wizard_at_startup(release: bool, engine_installed: bool, wizard_done: bool) -> bool {
    release && !engine_installed && !wizard_done
}

/// Первый запуск: сразу окно, а не молчаливая иконка в трее без движка.
/// Из `setup` (главный поток).
pub fn open_wizard_on_first_run(app: &AppHandle) {
    let data = resident::data_dir();
    let version = app.package_info().version.to_string();
    let installed = engine::is_installed(&engine::env_dir(&data, &version), &version);
    if wizard_at_startup(!cfg!(debug_assertions), installed, wizard_done(&data)) {
        shell_log!("движок {version} не установлен — открываю мастер первого запуска");
        open_main(app, None, None);
    }
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

    fn screen(width: u32, height: u32, scale: f64) -> Screen {
        Screen {
            x: 0,
            y: 0,
            width,
            height,
            scale,
        }
    }

    #[test]
    fn live_window_sits_bottom_right_above_the_taskbar() {
        // 1920×1080, панель задач 40 px снизу: рабочая область 1920×1040.
        assert_eq!(
            live_window_rect(screen(1920, 1040, 1.0)),
            (1544.0, 904.0, 360.0, 120.0)
        );
        // 150 %: физические пиксели → логические, отступ тот же в логических.
        assert_eq!(
            live_window_rect(screen(2880, 1560, 1.5)),
            (1544.0, 904.0, 360.0, 120.0)
        );
        // Рабочая область не с нуля (панель задач слева/сверху).
        let shifted = Screen {
            x: 60,
            y: 40,
            ..screen(1860, 1040, 1.0)
        };
        assert_eq!(live_window_rect(shifted), (1544.0, 944.0, 360.0, 120.0));
        // Масштаб не известен — как 100 %.
        assert_eq!(
            live_window_rect(screen(1920, 1040, 0.0)),
            (1544.0, 904.0, 360.0, 120.0)
        );
    }

    #[test]
    fn live_height_is_clamped() {
        assert_eq!(live_height(300.0), 300.0);
        assert_eq!(live_height(10.0), 120.0);
        assert_eq!(live_height(900.0), 520.0);
        assert_eq!(live_height(f64::NAN), 120.0);
        assert_eq!(live_height(f64::INFINITY), 520.0);
    }

    #[test]
    fn resize_keeps_the_bottom_edge() {
        // Было 120 высотой с верхом на 904 (низ 1024) — стало 520.
        assert_eq!(anchored_top(904.0, 120.0, 520.0), 504.0);
        assert_eq!(anchored_top(504.0, 520.0, 120.0), 904.0);
    }

    #[test]
    fn resize_order_never_overshoots_the_bottom_edge() {
        // Растёт — сначала поднять верх, потом вытянуть: иначе на кадр
        // панель свисала бы ниже панели задач.
        assert_eq!(resize_order(120.0, 520.0), ResizeOrder::MoveThenSize);
        // Сжимается — сначала укоротить, потом опустить.
        assert_eq!(resize_order(520.0, 120.0), ResizeOrder::SizeThenMove);
        assert_eq!(resize_order(300.0, 300.0), ResizeOrder::SizeThenMove);
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
        assert!(wizard_at_startup(true, false, false));
        // Движок есть — обычный запуск в трей.
        assert!(!wizard_at_startup(true, true, false));
        // «Пропустить» уже нажимали — мастер сам не открывается никогда.
        assert!(!wizard_at_startup(true, false, true));
        // dev: резидент из .venv репозитория, мастер не мешает.
        assert!(!wizard_at_startup(false, false, false));
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
