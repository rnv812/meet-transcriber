// macOS: Meet — программа строки меню, пока не открыто главное окно.
//
// Без главного окна (автозапуск, только панель ассистента или панель записи)
// у Meet нет значка в Dock и меню приложения (`ActivationPolicy::Accessory`):
// так панели видны поверх полноэкранного звонка и щелчок по ним не
// перебрасывает на рабочий стол Meet. Открылось главное окно — обычная
// программа (`Regular`: Dock, Cmd+Tab, меню), закрылось — снова Accessory.
// Решает чистая функция `mode_after` по списку открытых окон; на Windows
// ничего не применяется.

use tauri::{AppHandle, Manager};

/// Окна, при которых Meet — обычная программа. Панели (ассистент, запись
/// из строки меню) сюда не входят.
const REGULAR_WINDOWS: &[&str] = &["main"];

/// Вид приложения на macOS.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DockMode {
    /// Значок в Dock, меню приложения, Cmd+Tab.
    Regular,
    /// Только строка меню.
    Accessory,
}

/// Что происходит с окном: оно открывается (его ещё нет в списке) или
/// уничтожено (может ещё быть в списке).
#[derive(Debug, Clone, Copy)]
pub enum WindowChange<'a> {
    Opening(&'a str),
    Destroyed(&'a str),
}

/// Вид приложения после `change`, если сейчас открыты окна `open`.
pub fn mode_after<'a>(
    open: impl IntoIterator<Item = &'a str>,
    change: WindowChange<'_>,
) -> DockMode {
    let regular = |label: &str| REGULAR_WINDOWS.contains(&label);
    let open_regular = open.into_iter().any(|label| match change {
        WindowChange::Destroyed(gone) => label != gone && regular(label),
        WindowChange::Opening(_) => regular(label),
    });
    let opening_regular = matches!(change, WindowChange::Opening(label) if regular(label));
    if open_regular || opening_regular {
        DockMode::Regular
    } else {
        DockMode::Accessory
    }
}

/// Вид приложения при запуске, до первого окна.
const STARTUP: DockMode = DockMode::Accessory;

/// Вид при запуске — между `build` и `run`: tao при запуске ставит политику
/// делегата (по умолчанию `Regular`), а `setup` идёт уже после этого, и
/// значок мелькнул бы в Dock. `LSUIElement` в Info.plist прячет его ещё
/// раньше, до кода Rust.
pub fn at_startup(app: &mut tauri::App) {
    #[cfg(target_os = "macos")]
    app.set_activation_policy(policy(STARTUP));
    #[cfg(not(target_os = "macos"))]
    let _ = (app, STARTUP);
}

/// Применить вид приложения после `change`. С любого потока: Tauri передаёт
/// смену циклу событий.
pub fn sync(app: &AppHandle, change: WindowChange<'_>) {
    let labels: Vec<String> = app.webview_windows().into_keys().collect();
    let mode = mode_after(labels.iter().map(String::as_str), change);
    apply(app, mode);
}

#[cfg(target_os = "macos")]
fn apply(app: &AppHandle, mode: DockMode) {
    if let Err(error) = app.set_activation_policy(policy(mode)) {
        crate::logs::shell_log!("вид приложения в Dock не сменился: {error}");
    }
}

#[cfg(not(target_os = "macos"))]
fn apply(_app: &AppHandle, _mode: DockMode) {}

/// Политика активации Tauri для вида приложения.
#[cfg(target_os = "macos")]
pub fn policy(mode: DockMode) -> tauri::ActivationPolicy {
    match mode {
        DockMode::Regular => tauri::ActivationPolicy::Regular,
        DockMode::Accessory => tauri::ActivationPolicy::Accessory,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_windows_is_menu_bar_only() {
        assert_eq!(
            mode_after([], WindowChange::Destroyed("main")),
            DockMode::Accessory
        );
        assert_eq!(STARTUP, DockMode::Accessory);
    }

    #[test]
    fn opening_main_window_shows_dock_icon() {
        assert_eq!(
            mode_after([], WindowChange::Opening("main")),
            DockMode::Regular
        );
        assert_eq!(
            mode_after(["live", "tray-panel"], WindowChange::Opening("main")),
            DockMode::Regular
        );
    }

    #[test]
    fn panels_alone_keep_menu_bar_only() {
        assert_eq!(
            mode_after([], WindowChange::Opening("live")),
            DockMode::Accessory
        );
        assert_eq!(
            mode_after(["live"], WindowChange::Opening("tray-panel")),
            DockMode::Accessory
        );
    }

    #[test]
    fn destroyed_main_window_hides_dock_icon_even_if_still_listed() {
        assert_eq!(
            mode_after(["main", "live"], WindowChange::Destroyed("main")),
            DockMode::Accessory
        );
    }

    #[test]
    fn main_window_open_keeps_dock_icon() {
        assert_eq!(
            mode_after(["main", "live"], WindowChange::Destroyed("live")),
            DockMode::Regular
        );
        assert_eq!(
            mode_after(["main"], WindowChange::Opening("main")),
            DockMode::Regular
        );
    }
}
