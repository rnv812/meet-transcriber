// Меню приложения на macOS (строка меню вверху экрана, пока Meet впереди).
//
// Своё, а не стандартное меню Tauri: «Настройки…» по Cmd+, открывает
// настройки Meet, «О Meet» — раздел «О программе», а «Завершить Meet» (Cmd+Q)
// идёт тем же путём, что «Выход» из трея (`tray::confirm_quit`): вопрос, если
// идёт запись или расшифровка, и штатная остановка резидента. Стандартный
// пункт Quit завершал приложение мимо резидента. «Правка» обязательна: без
// её пунктов в полях ввода и в терминале «Агент» не работают Cmd+C/V/Z/A.
//
// Строится только на macOS (`main.rs`); на Windows у окон меню нет.
#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::{AppHandle, Wry};

use crate::logs::shell_log;
use crate::{tray, windows};

/// Стандартный пункт: его делает система (текст наш, действие и сочетание
/// клавиш — системные).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Standard {
    Hide,
    CloseWindow,
    Undo,
    Redo,
    Cut,
    Copy,
    Paste,
    SelectAll,
    Minimize,
    Maximize,
}

/// Пункт меню приложения.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Item {
    /// Свой пункт: `id` — действие (`action`), `accelerator` — сочетание
    /// клавиш в записи Tauri.
    Custom {
        id: &'static str,
        text: &'static str,
        accelerator: Option<&'static str>,
    },
    Standard(Standard, &'static str),
    Separator,
}

/// Особая роль подменю для macOS: «Окно» система дополняет списком окон,
/// «Справка» — полем поиска по меню.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    Plain,
    Window,
    Help,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Submenu {
    pub title: &'static str,
    pub role: Role,
    pub items: Vec<Item>,
}

/// Что делает свой пункт меню.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    About,
    Settings,
    StartRecording,
    Import,
    Quit,
    OpenLogs,
}

/// Раздел окна для «О Meet» и «Настройки…» (`windows::open_main`).
const ABOUT_SECTION: &str = "about";
const SETTINGS_SECTION: &str = "app";

/// `id` своих пунктов. С приставкой `app-`: обработчик меню трея получает и
/// события этого меню, и общий `id` сработал бы дважды.
const ACTIONS: [(&str, Action); 6] = [
    ("app-about", Action::About),
    ("app-settings", Action::Settings),
    ("app-start", Action::StartRecording),
    ("app-import", Action::Import),
    ("app-quit", Action::Quit),
    ("app-logs", Action::OpenLogs),
];

fn custom(id: &'static str, text: &'static str, accelerator: Option<&'static str>) -> Item {
    Item::Custom {
        id,
        text,
        accelerator,
    }
}

/// Меню приложения целиком, по порядку подменю.
pub fn menu_model() -> Vec<Submenu> {
    vec![
        Submenu {
            title: "Meet",
            role: Role::Plain,
            items: vec![
                custom("app-about", "О Meet", None),
                Item::Separator,
                custom("app-settings", "Настройки…", Some("CmdOrCtrl+,")),
                Item::Separator,
                Item::Standard(Standard::Hide, "Скрыть"),
                Item::Separator,
                custom("app-quit", "Завершить Meet", Some("CmdOrCtrl+Q")),
            ],
        },
        Submenu {
            title: "Файл",
            role: Role::Plain,
            items: vec![
                custom("app-start", "Начать запись", None),
                custom("app-import", "Импортировать…", None),
                Item::Separator,
                Item::Standard(Standard::CloseWindow, "Закрыть окно"),
            ],
        },
        Submenu {
            title: "Правка",
            role: Role::Plain,
            items: vec![
                Item::Standard(Standard::Undo, "Отменить"),
                Item::Standard(Standard::Redo, "Повторить"),
                Item::Separator,
                Item::Standard(Standard::Cut, "Вырезать"),
                Item::Standard(Standard::Copy, "Копировать"),
                Item::Standard(Standard::Paste, "Вставить"),
                Item::Standard(Standard::SelectAll, "Выбрать все"),
            ],
        },
        Submenu {
            title: "Окно",
            role: Role::Window,
            items: vec![
                Item::Standard(Standard::Minimize, "Свернуть"),
                Item::Standard(Standard::Maximize, "Изменить масштаб"),
            ],
        },
        Submenu {
            title: "Справка",
            role: Role::Help,
            items: vec![custom("app-logs", "Открыть журналы", None)],
        },
    ]
}

/// Действие своего пункта по его `id`. Чужой `id` (меню трея: его события
/// Tauri тоже доставляет сюда) — `None`.
pub fn action(id: &str) -> Option<Action> {
    ACTIONS
        .iter()
        .find(|(own, _)| *own == id)
        .map(|(_, action)| *action)
}

/// Собрать меню приложения по `menu_model`.
pub fn build(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let menu = Menu::new(app)?;
    for model in menu_model() {
        let submenu = tauri::menu::Submenu::new(app, model.title, true)?;
        for item in model.items {
            match item {
                Item::Custom {
                    id,
                    text,
                    accelerator,
                } => submenu.append(&MenuItem::with_id(app, id, text, true, accelerator)?)?,
                Item::Standard(kind, text) => submenu.append(&standard(app, kind, text)?)?,
                Item::Separator => submenu.append(&PredefinedMenuItem::separator(app)?)?,
            }
        }
        menu.append(&submenu)?;
        #[cfg(target_os = "macos")]
        match model.role {
            Role::Window => submenu.set_as_windows_menu_for_nsapp()?,
            Role::Help => submenu.set_as_help_menu_for_nsapp()?,
            Role::Plain => {}
        }
    }
    Ok(menu)
}

fn standard(app: &AppHandle, kind: Standard, text: &str) -> tauri::Result<PredefinedMenuItem<Wry>> {
    let text = Some(text);
    match kind {
        Standard::Hide => PredefinedMenuItem::hide(app, text),
        Standard::CloseWindow => PredefinedMenuItem::close_window(app, text),
        Standard::Undo => PredefinedMenuItem::undo(app, text),
        Standard::Redo => PredefinedMenuItem::redo(app, text),
        Standard::Cut => PredefinedMenuItem::cut(app, text),
        Standard::Copy => PredefinedMenuItem::copy(app, text),
        Standard::Paste => PredefinedMenuItem::paste(app, text),
        Standard::SelectAll => PredefinedMenuItem::select_all(app, text),
        Standard::Minimize => PredefinedMenuItem::minimize(app, text),
        Standard::Maximize => PredefinedMenuItem::maximize(app, text),
    }
}

/// Обработчик меню приложения (главный поток). События меню трея сюда тоже
/// приходят — их `id` здесь не узнаются и пропускаются.
pub fn on_event(app: &AppHandle, id: &str) {
    match action(id) {
        Some(Action::About) => windows::open_main(app, None, Some(ABOUT_SECTION)),
        Some(Action::Settings) => windows::open_main(app, None, Some(SETTINGS_SECTION)),
        Some(Action::StartRecording) => tray::command(app, tray::Action::Start),
        Some(Action::Import) => tray::import(app),
        Some(Action::Quit) => tray::confirm_quit(app),
        Some(Action::OpenLogs) => {
            std::thread::spawn(|| {
                if let Err(error) = windows::open_logs_folder() {
                    shell_log!("{error}");
                }
            });
        }
        None => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn submenu(title: &str) -> Submenu {
        menu_model()
            .into_iter()
            .find(|submenu| submenu.title == title)
            .unwrap_or_else(|| panic!("нет подменю «{title}»"))
    }

    fn custom(submenu: &Submenu, text: &str) -> (&'static str, Option<&'static str>) {
        submenu
            .items
            .iter()
            .find_map(|item| match item {
                Item::Custom {
                    id,
                    text: label,
                    accelerator,
                } if *label == text => Some((*id, *accelerator)),
                _ => None,
            })
            .unwrap_or_else(|| panic!("нет пункта «{text}» в «{}»", submenu.title))
    }

    fn standards(submenu: &Submenu) -> Vec<Standard> {
        submenu
            .items
            .iter()
            .filter_map(|item| match item {
                Item::Standard(kind, _) => Some(*kind),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn submenus_go_in_the_mac_order() {
        let titles: Vec<_> = menu_model().iter().map(|submenu| submenu.title).collect();
        assert_eq!(titles, ["Meet", "Файл", "Правка", "Окно", "Справка"]);
    }

    #[test]
    fn meet_menu_has_about_settings_hide_and_quit() {
        let meet = submenu("Meet");
        let (about, _) = custom(&meet, "О Meet");
        assert_eq!(action(about), Some(Action::About));
        let (settings, keys) = custom(&meet, "Настройки…");
        assert_eq!(keys, Some("CmdOrCtrl+,"));
        assert_eq!(action(settings), Some(Action::Settings));
        assert_eq!(standards(&meet), [Standard::Hide]);
        // «Завершить Meet» — свой пункт, а не системный Quit: тот завершал
        // приложение мимо вопроса и штатной остановки резидента.
        let (quit, keys) = custom(&meet, "Завершить Meet");
        assert_eq!(keys, Some("CmdOrCtrl+Q"));
        assert_eq!(action(quit), Some(Action::Quit));
        assert!(matches!(meet.items.last(), Some(Item::Custom { id, .. }) if *id == quit));
    }

    #[test]
    fn file_menu_starts_recording_imports_and_closes_the_window() {
        let file = submenu("Файл");
        assert_eq!(
            action(custom(&file, "Начать запись").0),
            Some(Action::StartRecording)
        );
        assert_eq!(
            action(custom(&file, "Импортировать…").0),
            Some(Action::Import)
        );
        // Закрыть окно — системный пункт: Cmd+W ему даёт система.
        assert!(file
            .items
            .contains(&Item::Standard(Standard::CloseWindow, "Закрыть окно")));
    }

    #[test]
    fn edit_menu_has_the_standard_items() {
        let edit = submenu("Правка");
        assert_eq!(
            standards(&edit),
            [
                Standard::Undo,
                Standard::Redo,
                Standard::Cut,
                Standard::Copy,
                Standard::Paste,
                Standard::SelectAll,
            ]
        );
    }

    #[test]
    fn window_and_help_menus_have_their_mac_roles() {
        let window = submenu("Окно");
        assert_eq!(window.role, Role::Window);
        assert_eq!(standards(&window), [Standard::Minimize, Standard::Maximize]);
        let help = submenu("Справка");
        assert_eq!(help.role, Role::Help);
        assert_eq!(
            action(custom(&help, "Открыть журналы").0),
            Some(Action::OpenLogs)
        );
        for other in ["Meet", "Файл", "Правка"] {
            assert_eq!(submenu(other).role, Role::Plain);
        }
    }

    #[test]
    fn own_ids_never_collide_with_the_tray_menu() {
        // Обработчик меню трея получает и события меню приложения: один id
        // в обоих меню сработал бы дважды.
        let tray_ids = [
            "open",
            "start",
            "stop",
            "live-start",
            "live-start-temp",
            "temp-stop",
            "keep",
            "live-stop",
            "live-attach",
            "live-detach",
            "cancel",
            "auto-on",
            "auto-off",
            "import",
            "log",
            "restart",
            "quit",
        ];
        let mut seen = Vec::new();
        for submenu in menu_model() {
            for item in submenu.items {
                if let Item::Custom { id, .. } = item {
                    assert!(!tray_ids.contains(&id), "{id}");
                    assert!(action(id).is_some(), "{id}");
                    assert!(!seen.contains(&id), "{id} дважды");
                    seen.push(id);
                }
            }
        }
        assert_eq!(seen.len(), 6);
        assert_eq!(action("quit"), None);
        assert_eq!(action("start"), None);
    }
}
