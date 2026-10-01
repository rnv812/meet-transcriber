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
mod autostart;
mod engine;
mod logs;
mod resident;
mod tray;
mod windows;

use tauri::RunEvent;

use resident::Supervisor;

fn main() {
    tauri::Builder::default()
        // Первым: второй экземпляр должен выйти до того, как другие плагины и
        // `setup` успеют что-то сделать (второй трей, второй резидент). Колбэк
        // приходит в первый экземпляр внутри WM_COPYDATA, пока второй ждёт
        // ответа, — окно строим позже, отдельной задачей главного потока.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            let recording = windows::recording_arg(&args);
            let handle = app.clone();
            let _ = app.run_on_main_thread(move || windows::open_main(&handle, recording, None));
        }))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        // Автозапуск при входе в Windows (переключатель мастера,
        // `autostart::set_autostart`): `--autostart` — признак такого запуска.
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .arg(autostart::AUTOSTART_ARG)
                .build(),
        )
        .plugin(
            // Панель ассистента встаёт в угол по монитору при каждом старте:
            // сохранённая позиция и размер ей не нужны.
            tauri_plugin_window_state::Builder::default()
                .with_denylist(&[windows::LIVE_LABEL])
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            windows::endpoint,
            windows::open_folder,
            windows::save_text,
            windows::pick_media,
            windows::pick_folder,
            windows::resident_status,
            windows::live_resize,
            // Мастер первого запуска: движок расшифровки, страницы HF.
            engine::engine_status,
            engine::install_engine,
            engine::reinstall_engine,
            engine::gpu_info,
            windows::open_url,
            windows::mark_wizard_done,
            autostart::set_autostart
        ])
        .setup(|app| {
            Supervisor::start(app.handle());
            // Автозапуск, снятый деинсталлятором прежней версии, — вернуть.
            autostart::restore_at_startup(app.handle());
            tray::build(app)?;
            // Обычный запуск — только трей. `--recording <id>` (например, из
            // уведомления) — сразу окно на этой записи; первый запуск без
            // движка — окно с мастером; автозапуск при входе в Windows —
            // только трей, без окон.
            let args: Vec<String> = std::env::args().collect();
            if let Some(recording) = windows::recording_arg(&args) {
                windows::open_main(app.handle(), Some(recording), None);
            } else if autostart::autostarted(&args) {
                logs::shell_log!("автозапуск при входе в Windows: только трей");
            } else {
                windows::open_wizard_on_first_run(app.handle());
            }
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
