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
mod install_wait;
mod live_panel;
mod logs;
mod netproxy;
mod pty;
mod resident;
mod tray;
mod updater;
mod upgrade;
mod windows;

use tauri::{RunEvent, WindowEvent};

use resident::Supervisor;

fn main() {
    // Помощник установщика (`windows/hooks.nsh`): ждёт процессы из папки
    // установки и выходит, не поднимая ни Tauri, ни окон.
    let args: Vec<String> = std::env::args().collect();
    if let Some((mode, root)) = install_wait::requested(&args) {
        std::process::exit(install_wait::run(mode, &root));
    }
    tauri::Builder::default()
        // Первым: второй экземпляр должен выйти до того, как другие плагины и
        // `setup` успеют что-то сделать (второй трей, второй резидент). Колбэк
        // приходит в первый экземпляр внутри WM_COPYDATA, пока второй ждёт
        // ответа, — окно строим позже, отдельной задачей главного потока.
        // IMPORTANT: `run_on_main_thread`, вызванный с главного потока,
        // выполняет задачу на месте, то есть внутри WM_COPYDATA: WebView2 там
        // не создаётся, и виснут оба экземпляра. Поэтому зовём его из другого
        // потока — задача уходит в очередь цикла событий.
        //
        // Второй запуск с `--autostart` (две записи автозапуска) окна не
        // открывает: при входе в Windows — только трей.
        //
        // `--quit` (установщик новой версии перед заменой файлов) — тот же
        // «Выход», что в трее: резидент сохраняет запись и гасится, затем
        // выходит оболочка. Установщик ждёт, пока процесс не исчезнет.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if upgrade::quit_requested(&args) {
                logs::shell_log!("выход по просьбе установщика (--quit)");
                tray::quit(app);
                return;
            }
            let recording = windows::recording_arg(&args);
            if recording.is_none() && autostart::autostarted(&args) {
                return;
            }
            let handle = app.clone();
            std::thread::spawn(move || {
                let main = handle.clone();
                let _ =
                    handle.run_on_main_thread(move || windows::open_main(&main, recording, None));
            });
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
            // Геометрию панели ассистента оболочка ведёт сама
            // (`live_panel.rs`, `live_window.json`): ей нужна ещё высота
            // развёрнутой панели и «поверх всех окон», а позиция с
            // отключённого монитора — в угол.
            tauri_plugin_window_state::Builder::default()
                .with_denylist(&[live_panel::LIVE_LABEL])
                .build(),
        )
        .manage(live_panel::LivePanel::default())
        .invoke_handler(tauri::generate_handler![
            windows::endpoint,
            windows::open_folder,
            windows::save_text,
            windows::pick_media,
            windows::pick_folder,
            windows::resident_status,
            // Плавающая панель ассистента: вид, размер, перетаскивание.
            live_panel::live_window_state,
            live_panel::live_set_expanded,
            live_panel::live_set_maximized,
            live_panel::live_set_pinned,
            live_panel::live_start_drag,
            // Мастер первого запуска: движок расшифровки, страницы HF.
            engine::engine_status,
            engine::install_engine,
            engine::reinstall_engine,
            engine::retry_gigaam_install,
            engine::gpu_info,
            windows::open_url,
            windows::mark_wizard_done,
            windows::open_logs,
            autostart::set_autostart,
            autostart::get_autostart,
            // «О программе»: обновление по кнопке с GitHub.
            updater::check_update,
            updater::install_update,
            updater::releases_page,
            // Вкладка «Агент»: Claude Code / Codex во встроенном терминале.
            pty::agent_spawn,
            pty::agent_write,
            pty::agent_resize,
            pty::agent_kill,
            pty::agent_kill_recording
        ])
        // Агенты вкладки «Агент» живут, пока открыта карточка: закрытое или
        // перезагруженное главное окно их гасит (карточка уже не размонтируется).
        .on_window_event(|window, event| {
            if window.label() == "main" && matches!(event, WindowEvent::Destroyed) {
                pty::kill_all();
            }
            // Панель ассистента запоминает, где и какого размера её оставили.
            live_panel::on_window_event(window, event);
        })
        .on_page_load(|webview, payload| {
            if webview.label() == "main"
                && payload.event() == tauri::webview::PageLoadEvent::Started
            {
                pty::kill_all();
            }
        })
        .setup(|app| {
            let args: Vec<String> = std::env::args().collect();
            // `--quit`, а приложение не запущено (иначе плагин single-instance
            // уже передал флаг ему и завершил этот процесс): выходить некому —
            // выходим сами, не поднимая ни трей, ни резидент.
            if upgrade::quit_requested(&args) {
                std::process::exit(0);
            }
            // Движок, собранный из другого колеса той же версии, и движок
            // новой версии после обновления приложения ставятся в фоне;
            // резидент ждёт конца, чтобы не подняться из старого кода.
            let upkeep = engine::plan_upkeep(app.handle());
            let engine_upkeep = upkeep.holds_resident();
            Supervisor::start(app.handle(), engine_upkeep);
            // Автозапуск, снятый деинсталлятором прежней версии, — вернуть.
            autostart::restore_at_startup(app.handle());
            tray::build(app)?;
            // Версия сменилась (установщик поверх прежней) — одно уведомление.
            upgrade::note_version_at_startup(app.handle());
            engine::run_upkeep_in_background(app.handle(), upkeep);
            // Обычный запуск — только трей. `--recording <id>` (например, из
            // уведомления) — сразу окно на этой записи; первый запуск без
            // движка — окно с мастером; автозапуск при входе в Windows —
            // только трей, без окон.
            if let Some(recording) = windows::recording_arg(&args) {
                windows::open_main(app.handle(), Some(recording), None);
            } else if autostart::autostarted(&args) {
                logs::shell_log!("автозапуск при входе в Windows: только трей");
            } else {
                windows::open_wizard_on_first_run(app.handle(), engine_upkeep);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("оболочка meet не запустилась")
        .run(|app, event| match event {
            // Закрытие последнего окна не завершает приложение: оно живёт в
            // трее. Выход — только «Выход» (app.exit(0), у него code = Some).
            RunEvent::ExitRequested { api, code, .. } if code.is_none() => api.prevent_exit(),
            // Job object агентов и так закроет Windows; гасим явно и раньше.
            // Геометрию панели ассистента, ждущую отложенной записи, — в файл.
            RunEvent::Exit => {
                live_panel::flush(app);
                pty::kill_all();
            }
            _ => {}
        });
}
