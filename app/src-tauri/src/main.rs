// Оболочка meet: иконка в трее, уведомления, окно приложения по требованию.
//
// Логики записи здесь нет и быть не должно — она вся в резиденте (Python),
// который оболочка запускает дочерним процессом без окна (`resident.rs`) и с
// которым говорит по HTTP (`api.rs`). Rust отвечает за то, чего веб-страница не
// может: трей, уведомления Windows, жизненный цикл резидента.
//
// IMPORTANT: webview создаётся только из обработчиков трея/меню (главный
// поток) или через `app.run_on_main_thread`. Создание окна из команды
// (рабочий поток) роняло приложение на Windows целиком (exit -1).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod api;
mod resident;

use std::path::PathBuf;
use std::process::Command;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent};

use resident::{Endpoint, Supervisor};

#[tauri::command]
fn endpoint() -> Option<Endpoint> {
    resident::read_endpoint()
}

/// Показать папку записи в проводнике. Приложение не заменяет файловый
/// менеджер: иногда быстрее открыть папку, чем искать её в библиотеке.
#[tauri::command]
fn open_folder(path: String) -> Result<(), String> {
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("папки нет: {path}"));
    }
    Command::new("explorer")
        .arg(&target)
        .spawn()
        .map(|_| ())
        // explorer возвращает ненулевой код даже при успехе, поэтому ждать
        // завершения нельзя — нас интересует только сам запуск.
        .map_err(|error| format!("не удалось открыть папку: {error}"))
}

/// «Выход»: резидент сохраняет идущую запись и гасится (до 70 с), и только
/// потом выходит оболочка. Ждём в отдельном потоке — главный поток держит
/// цикл событий и трей.
fn quit(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || {
        app.state::<Supervisor>().shutdown();
        let handle = app.clone();
        if app.run_on_main_thread(move || handle.exit(0)).is_err() {
            app.exit(0);
        }
    });
}

/// Временный минимальный трей: только «Выход». Полное меню, иконки состояний
/// и уведомления — в `tray.rs` (следующий шаг).
fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    let quit_item = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&quit_item])?;
    let mut tray = TrayIconBuilder::with_id("meet")
        .tooltip("meet")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            if event.id().as_ref() == "quit" {
                quit(app);
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .invoke_handler(tauri::generate_handler![endpoint, open_folder])
        .setup(|app| {
            Supervisor::start(app.handle());
            build_tray(app)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("оболочка meet не запустилась")
        .run(|_app, event| {
            // Закрытие последнего окна не завершает приложение: оно живёт в
            // трее. Выход — только «Выход» (app.exit(0), у него code = Some).
            if let RunEvent::ExitRequested { api, code, .. } = event {
                if code.is_none() {
                    api.prevent_exit();
                }
            }
        });
}
