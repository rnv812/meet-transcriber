//! Оформление окон (0.4, Atlas Aurora): тема системной рамки главного окна и
//! рассылка выбора («Оформление» в настройках) всем окнам приложения.
//!
//! Источник истины — `ui.theme` в настройках резидента. При открытии окна
//! резидент может ещё не ответить, поэтому тему читаем из `config.json` сами
//! — по тем же правилам, что `settings.Ui` (у обновившегося без ключа — тёмная).

use serde_json::Value;
use tauri::window::Color;
use tauri::{AppHandle, Emitter, Manager, Runtime, Theme, WebviewWindow};

use crate::logs::shell_log;
use crate::resident;

/// Событие окнам: `{theme, aurora, auroraStyle, motion}` — как прислало окно настроек.
pub const EVENT: &str = "appearance";

/// Значение `ui.theme`: `Some(Some(тема))` — явная, `Some(None)` — системная,
/// `None` — ключа нет или он негодный.
fn parse(theme: Option<&str>) -> Option<Option<Theme>> {
    match theme.map(str::trim) {
        Some("dark") => Some(Some(Theme::Dark)),
        Some("light") => Some(Some(Theme::Light)),
        Some("system") => Some(None),
        _ => None,
    }
}

/// Новая установка — те же признаки, что `is_new` в `Settings.from_raw`:
/// нет `version` и нет ни одной секции, которую писала прежняя версия.
fn is_new(settings: &Value) -> bool {
    match settings.as_object() {
        None => true,
        Some(map) => {
            !map.contains_key("version")
                && !["llm", "assist", "hooks", "auto_record", "post_record_hook"]
                    .iter()
                    .any(|key| map.contains_key(*key))
        }
    }
}

/// `Some(тема)` — явная, `None` — как в системе. Ключа нет: у обновившегося
/// окно было тёмным (`settings.LEGACY_THEME`), у новой установки — системная.
pub fn theme_pref(settings: &Value) -> Option<Theme> {
    parse(settings.pointer("/ui/theme").and_then(Value::as_str)).unwrap_or_else(|| {
        if is_new(settings) {
            None
        } else {
            Some(Theme::Dark)
        }
    })
}

/// Тема для нового окна: из `config.json`; файла нет — как в системе.
pub fn startup_theme() -> Option<Theme> {
    let path = resident::data_dir().join("config.json");
    let raw = std::fs::read_to_string(path).ok();
    let settings = raw
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .unwrap_or(Value::Null);
    theme_pref(&settings)
}

/// Холст Aurora (`--canvas`) — фон окна до загрузки страницы.
pub fn canvas(theme: Option<Theme>) -> Color {
    match theme {
        Some(Theme::Light) => Color(255, 255, 255, 255),
        _ => Color(15, 15, 15, 255),
    }
}

/// Скрипт до любого скрипта страницы (`initialization_script`, CSP на него не
/// действует): тема, которую оболочка решила по `config.json`. Нужна, пока в
/// окне нет кеша оформления (первый запуск 0.4): без неё обновившийся на
/// светлой ОС увидел бы светлое окно до ответа резидента
/// (`public/appearance-boot.js`, `readCached`).
pub fn theme_hint_script(theme: Option<Theme>) -> String {
    let name = match theme {
        Some(Theme::Dark) => "dark",
        Some(Theme::Light) => "light",
        _ => "system",
    };
    format!("window.__MEET_THEME__ = \"{name}\";")
}

/// Фон окна под тему. У «Системной» холст до загрузки страницы тёмный
/// (`canvas(None)`), поэтому после создания окна берём тему, которую окно
/// взяло у ОС, — иначе на светлой ОС каждое открытие мигает тёмным.
pub fn paint_canvas<R: Runtime>(window: &WebviewWindow<R>, theme: Option<Theme>) {
    let actual = match theme {
        Some(theme) => Some(theme),
        None => match window.theme() {
            Ok(os) => Some(os),
            Err(error) => {
                shell_log!("тема ОС не определилась: {error}");
                None
            }
        },
    };
    if let Err(error) = window.set_background_color(Some(canvas(actual))) {
        shell_log!("фон окна не сменился: {error}");
    }
}

/// Окно настроек сменило оформление: рамка главного окна — в новую тему,
/// остальным окнам — событие (панель ассистента и панель трея применят сами).
#[tauri::command]
pub fn set_appearance(app: AppHandle, appearance: Value) {
    // Окно настроек шлёт только допустимые значения; мусор — «как в системе».
    let theme = parse(appearance.get("theme").and_then(Value::as_str)).unwrap_or(None);
    if let Some(main) = app.get_webview_window("main") {
        if let Err(error) = main.set_theme(theme) {
            shell_log!("тема окна не сменилась: {error}");
        }
        paint_canvas(&main, theme);
    }
    if let Err(error) = app.emit(EVENT, appearance) {
        shell_log!("оформление не разослано окнам: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tauri::Theme;

    #[test]
    fn explicit_theme_wins() {
        assert_eq!(
            theme_pref(&json!({"version": 2, "ui": {"theme": "light"}})),
            Some(Theme::Light)
        );
        assert_eq!(
            theme_pref(&json!({"ui": {"theme": "dark"}})),
            Some(Theme::Dark)
        );
        assert_eq!(
            theme_pref(&json!({"version": 2, "ui": {"theme": "system"}})),
            None
        );
    }

    #[test]
    fn upgraded_config_without_theme_stays_dark() {
        // Резидент 0.4 считает такого пользователя обновившимся (settings.LEGACY_THEME).
        assert_eq!(
            theme_pref(&json!({"version": 2, "ui": {"notifications": "all"}})),
            Some(Theme::Dark)
        );
        assert_eq!(theme_pref(&json!({"llm": {}})), Some(Theme::Dark));
        // Негодное значение у обновившегося — тоже тёмная, как в settings.Ui.
        assert_eq!(
            theme_pref(&json!({"version": 2, "ui": {"theme": "розовая"}})),
            Some(Theme::Dark)
        );
    }

    #[test]
    fn fresh_install_follows_system() {
        assert_eq!(theme_pref(&json!({})), None);
        assert_eq!(theme_pref(&json!(null)), None);
        assert_eq!(theme_pref(&json!({"ui": {"theme": "розовая"}})), None);
    }

    #[test]
    fn theme_hint_script_tells_the_page_the_startup_theme() {
        assert_eq!(
            theme_hint_script(Some(Theme::Dark)),
            "window.__MEET_THEME__ = \"dark\";"
        );
        assert_eq!(
            theme_hint_script(Some(Theme::Light)),
            "window.__MEET_THEME__ = \"light\";"
        );
        assert_eq!(
            theme_hint_script(None),
            "window.__MEET_THEME__ = \"system\";"
        );
    }

    #[test]
    fn canvas_matches_aurora_tokens() {
        assert_eq!(
            canvas(Some(Theme::Light)),
            tauri::window::Color(255, 255, 255, 255)
        );
        // Chaos Black — холст тёмной темы; «системная» до загрузки страницы — тоже тёмная.
        assert_eq!(
            canvas(Some(Theme::Dark)),
            tauri::window::Color(15, 15, 15, 255)
        );
        assert_eq!(canvas(None), tauri::window::Color(15, 15, 15, 255));
    }
}
