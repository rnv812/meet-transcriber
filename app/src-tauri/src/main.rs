// Оболочка meet: плавающая панель, окно настроек, иконка в трее.
//
// Логики записи здесь нет и быть не должно — она вся в резиденте (Python), а
// оболочка только показывает его состояние и передаёт команды. Rust отвечает за
// то, чего веб-страница не может: окно без рамки поверх всех окон, трей,
// поиск резидента на диске и его запуск.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, WebviewWindow};

/// Адрес и токен резидента из `%LOCALAPPDATA%/meet/daemon.json`.
#[derive(Serialize, Clone)]
struct Endpoint {
    port: u16,
    token: String,
}

fn data_dir() -> Option<PathBuf> {
    // Тот же путь, что у paths.data_dir() в Python: MEET_DATA_DIR — override
    // для портативного режима и тестов, иначе LOCALAPPDATA/meet.
    if let Ok(dir) = std::env::var("MEET_DATA_DIR") {
        if !dir.trim().is_empty() {
            return Some(PathBuf::from(dir));
        }
    }
    std::env::var("LOCALAPPDATA").ok().map(|dir| PathBuf::from(dir).join("meet"))
}

fn read_endpoint() -> Option<Endpoint> {
    let path = data_dir()?.join("daemon.json");
    let raw = std::fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let port = value.get("port")?.as_u64()? as u16;
    let token = value.get("token")?.as_str()?.to_string();
    Some(Endpoint { port, token })
}

/// Где искать резидента. Установленное приложение кладёт его рядом с собой;
/// при разработке он живёт в venv репозитория.
fn resident_candidates() -> Vec<PathBuf> {
    let mut found = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            found.push(dir.join("meet-tray.exe"));
            // dev: target/debug/meet-desktop.exe → ../../../.. = корень репозитория
            if let Some(repo) = dir.ancestors().nth(4) {
                found.push(repo.join(".venv/Scripts/meet-tray.exe"));
            }
        }
    }
    found.push(PathBuf::from("meet-tray.exe")); // из PATH
    found
}

#[tauri::command]
fn endpoint() -> Option<Endpoint> {
    read_endpoint()
}

/// Отвечает ли резидент на самом деле.
///
/// IMPORTANT: одного файла мало. Упавший процесс оставляет `daemon.json` на
/// диске, и доверие файлу превращало кнопку «Запустить дежурного» в пустышку:
/// команда видела публикацию и молча ничего не запускала. Проверяем соединением
/// и убираем протухшую публикацию — та же болезнь, что у lock-файлов записи.
fn endpoint_answers() -> bool {
    let Some(endpoint) = read_endpoint() else {
        return false;
    };
    let address = format!("127.0.0.1:{}", endpoint.port);
    let alive = address
        .parse()
        .ok()
        .and_then(|addr| {
            TcpStream::connect_timeout(&addr, Duration::from_millis(400)).ok()
        })
        .is_some();
    if !alive {
        if let Some(dir) = data_dir() {
            let _ = std::fs::remove_file(dir.join("daemon.json"));
        }
    }
    alive
}

/// Поднять резидента. Возвращает, чем именно запустили, — панель это показывает.
#[tauri::command]
fn start_resident() -> Result<String, String> {
    if endpoint_answers() {
        return Ok("дежурный уже работает".into());
    }
    let mut last = String::from("исполняемый файл не найден");
    for candidate in resident_candidates() {
        // --watch: дежурить и ждать звонка, а не начинать запись немедленно
        // (без флага meet-tray означает «начать запись», см. tray.main).
        match Command::new(&candidate).arg("--watch").spawn() {
            Ok(_) => {
                // Резидент поднимается пару секунд (pystray, детектор, сервер).
                // Ждём его публикации, чтобы ответить по факту, а не по надежде.
                for _ in 0..40 {
                    std::thread::sleep(Duration::from_millis(250));
                    if endpoint_answers() {
                        return Ok(format!("дежурный запущен: {}", candidate.display()));
                    }
                }
                return Ok(format!(
                    "запустил {}, но он пока не отвечает — посмотрите журнал",
                    candidate.display()
                ));
            }
            Err(error) => last = format!("{}: {error}", candidate.display()),
        }
    }
    Err(format!("не удалось запустить дежурного ({last})"))
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

#[tauri::command]
fn open_settings(app: tauri::AppHandle) -> Result<(), String> {
    show_settings(&app).map_err(|error| error.to_string())
}

/// Показать окно редактора и, если задано, открыть в нём конкретную запись.
///
/// Окно объявлено в конфиге и создаётся скрытым (как настройки), поэтому id
/// передаётся событием: webview уже загружен и слушает "open-recording".
#[tauri::command]
fn open_editor(app: tauri::AppHandle, recording: Option<String>) -> Result<(), String> {
    let Some(window) = app.get_webview_window("editor") else {
        return Ok(());
    };
    window.show().map_err(|e| e.to_string())?;
    window.unminimize().ok();
    window.set_focus().map_err(|e| e.to_string())?;
    if let Some(id) = recording {
        // Небольшая задержка не нужна: окно живёт с запуска и слушатель уже
        // подписан. Emit до show тоже дошёл бы, но так очевиднее.
        let _ = window.emit("open-recording", id);
    }
    Ok(())
}

/// Показать окно настроек.
///
/// IMPORTANT: окно объявлено в `tauri.conf.json` и создаётся при старте
/// скрытым, а здесь только показывается. Создавать webview на лету из команды
/// нельзя: на Windows окна создаются в главном потоке, а команда приходит из
/// рабочего — приложение падало целиком (exit -1) в момент клика по шестерёнке.
fn show_settings(app: &tauri::AppHandle) -> tauri::Result<()> {
    let Some(window) = app.get_webview_window("settings") else {
        return Ok(()); // окна нет в конфиге — молча ничего не делаем
    };
    window.show()?;
    window.unminimize().ok();
    window.set_focus()?;
    Ok(())
}

fn toggle_panel(panel: &WebviewWindow) -> tauri::Result<()> {
    if panel.is_visible()? {
        panel.hide()
    } else {
        panel.show()
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .invoke_handler(tauri::generate_handler![endpoint, start_resident, open_settings, open_folder, open_editor])
        .setup(|app| {
            let panel = app
                .get_webview_window("panel")
                .expect("окно панели объявлено в tauri.conf.json");

            // Крестик окна настроек прячет его, а не уничтожает: пересоздать
            // webview из команды нельзя (см. show_settings), и «Настройки»
            // перестали бы открываться после первого закрытия.
            for label in ["settings", "editor"] {
                if let Some(win) = app.get_webview_window(label) {
                    let handle = win.clone();
                    win.on_window_event(move |event| {
                        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                            api.prevent_close();
                            let _ = handle.hide();
                        }
                    });
                }
            }

            // Панель ставится у нижнего края по центру при первом запуске;
            // дальше её позицию помнит плагин window-state.
            if let Ok(Some(monitor)) = panel.current_monitor() {
                let screen = monitor.size();
                let scale = monitor.scale_factor();
                if let Ok(size) = panel.outer_size() {
                    let x = (screen.width as i32 - size.width as i32) / 2;
                    let y = screen.height as i32 - size.height as i32 - (72.0 * scale) as i32;
                    let _ = panel.set_position(tauri::PhysicalPosition::new(x, y));
                }
            }

            let show = MenuItem::with_id(app, "panel", "Показать панель", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Настройки", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Выйти из панели", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &settings, &quit])?;

            // Трей оболочки намеренно скромный: иконку записи и её пункты держит
            // резидент (pystray), и два одинаковых меню путали бы.
            TrayIconBuilder::with_id("meet-shell")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("meet — панель встреч")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "panel" => {
                        if let Some(window) = app.get_webview_window("panel") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "settings" => {
                        let _ = show_settings(app);
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        if let Some(window) = tray.app_handle().get_webview_window("panel") {
                            let _ = toggle_panel(&window);
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("оболочка meet не запустилась");
}


