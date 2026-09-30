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
mod tray;
mod windows;

use tauri::RunEvent;

use resident::Supervisor;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .invoke_handler(tauri::generate_handler![
            windows::endpoint,
            windows::open_folder,
            windows::save_text,
            windows::pick_media,
            windows::resident_status
        ])
        .setup(|app| {
            Supervisor::start(app.handle());
            tray::build(app)?;
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
