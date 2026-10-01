// «Запускать вместе с Windows»: переключатель мастера (шаг «Готово») и его
// восстановление после обновления.
//
// Само значение автозапуска — `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
// с именем продукта (tauri-plugin-autostart). Выбор человека дублируется в
// `<data_dir>\autostart.json`: установщик Tauri при ручном обновлении запускает
// старый деинсталлятор без `/UPDATE`, тот удаляет значение из Run, и без копии
// выбора автозапуск молча пропал бы после каждой новой версии.

use std::path::Path;

use tauri::AppHandle;
use tauri_plugin_autostart::ManagerExt;

use crate::logs::shell_log;
use crate::resident;

/// Флаг, с которым Windows запускает оболочку при входе в систему.
pub const AUTOSTART_ARG: &str = "--autostart";
/// Выбор человека: `{"enabled": bool}` в папке данных.
pub const CHOICE_FILE: &str = "autostart.json";
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// Запуск при входе в систему: только трей, никаких окон — даже мастера.
pub fn autostarted(args: &[String]) -> bool {
    args.iter().skip(1).any(|arg| arg == AUTOSTART_ARG)
}

pub fn write_choice(data_dir: &Path, enabled: bool) -> std::io::Result<()> {
    std::fs::create_dir_all(data_dir)?;
    let body = serde_json::json!({ "enabled": enabled }).to_string();
    let staged = data_dir.join(format!("{CHOICE_FILE}.tmp"));
    std::fs::write(&staged, body)?;
    std::fs::rename(&staged, data_dir.join(CHOICE_FILE))
}

/// Записанный выбор; файла нет или он битый — `None` (выбора не было).
pub fn read_choice(data_dir: &Path) -> Option<bool> {
    let raw = std::fs::read_to_string(data_dir.join(CHOICE_FILE)).ok()?;
    serde_json::from_str::<serde_json::Value>(&raw)
        .ok()?
        .get("enabled")?
        .as_bool()
}

/// Что сделать со значением автозапуска в Run при старте оболочки.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RunFix {
    Keep,
    /// Значения нет (его снял деинсталлятор прежней версии) — вернуть.
    Restore,
    /// Значение ведёт на другой exe (программу поставили в другую папку) —
    /// переписать на этот, не трогая отметку диспетчера задач.
    Rewrite,
}

/// Значение, которое пишет tauri-plugin-autostart: `<exe> --autostart`.
pub fn run_command(exe: &Path) -> String {
    format!("{} {AUTOSTART_ARG}", exe.display())
}

/// Решение по значению в Run: только в установленном приложении (dev-сборка
/// прописала бы в Run свой exe из target) и только если человек автозапуск
/// включал. `current` — `None`, если реестр не ответил (тогда не трогаем),
/// `Some(None)` — значения нет. Значение есть, но выключено в диспетчере
/// задач (`StartupApproved`) — это тоже выбор человека, его не перебиваем:
/// переписывается только путь.
pub fn run_fix(
    release: bool,
    choice: Option<bool>,
    current: Option<Option<&str>>,
    expected: &str,
) -> RunFix {
    if !release || choice != Some(true) {
        return RunFix::Keep;
    }
    match current {
        None => RunFix::Keep,
        Some(None) => RunFix::Restore,
        Some(Some(value)) if !value.trim().eq_ignore_ascii_case(expected.trim()) => RunFix::Rewrite,
        Some(Some(_)) => RunFix::Keep,
    }
}

/// Значение `name` в `HKCU\...\Run`: `Some(None)` — его нет, `None` — реестр
/// не ответил.
#[cfg(windows)]
pub fn run_value(name: &str) -> Option<Option<String>> {
    use std::ffi::c_void;
    use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_EXPAND_SZ, RRF_RT_REG_SZ,
    };

    let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let (key, value) = (wide(RUN_KEY), wide(name));
    let flags = RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ;
    let mut size: u32 = 0;
    // SAFETY: строки с нулём на конце живут до конца вызова; первый вызов
    // только узнаёт размер (буфер — null).
    let status = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            value.as_ptr(),
            flags,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            &mut size,
        )
    };
    match status {
        ERROR_SUCCESS => {}
        ERROR_FILE_NOT_FOUND => return Some(None),
        _ => return None,
    }
    let mut buffer = vec![0u16; (size as usize).div_ceil(2).max(1)];
    // SAFETY: буфер на `size` байт; RegGetValueW дописывает нуль сам.
    let status = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            value.as_ptr(),
            flags,
            std::ptr::null_mut(),
            buffer.as_mut_ptr().cast::<c_void>(),
            &mut size,
        )
    };
    if status != ERROR_SUCCESS {
        return None;
    }
    let len = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
    Some(Some(String::from_utf16_lossy(&buffer[..len])))
}

#[cfg(not(windows))]
pub fn run_value(_name: &str) -> Option<Option<String>> {
    None
}

/// Переписать значение `name` в Run (только его — без отметки диспетчера
/// задач, которую `enable()` плагина сбросила бы во «включено»).
#[cfg(windows)]
fn write_run_value(name: &str, command: &str) -> Result<(), String> {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{RegSetKeyValueW, HKEY_CURRENT_USER, REG_SZ};

    let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let (key, value, data) = (wide(RUN_KEY), wide(name), wide(command));
    // SAFETY: строки с нулём на конце живут до конца вызова; размер данных —
    // в байтах, вместе с нулём.
    let status = unsafe {
        RegSetKeyValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            value.as_ptr(),
            REG_SZ,
            data.as_ptr().cast(),
            (data.len() * 2) as u32,
        )
    };
    if status == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(format!("код {status}"))
    }
}

#[cfg(not(windows))]
fn write_run_value(_name: &str, _command: &str) -> Result<(), String> {
    Err("только в Windows".to_string())
}

/// При старте оболочки: автозапуск был включён, а значения в Run нет (его
/// снял деинсталлятор прежней версии) — вернуть; значение ведёт на другой
/// exe (установка в другую папку) — переписать на этот.
pub fn restore_at_startup(app: &AppHandle) {
    let choice = read_choice(&resident::data_dir());
    let name = app.package_info().name.clone();
    let Ok(exe) = std::env::current_exe() else {
        return;
    };
    let expected = run_command(&exe);
    let current = run_value(&name);
    let fix = run_fix(
        !cfg!(debug_assertions),
        choice,
        current.as_ref().map(Option::as_deref),
        &expected,
    );
    match fix {
        RunFix::Keep => {}
        RunFix::Restore => match app.autolaunch().enable() {
            Ok(()) => {
                shell_log!("автозапуск был включён, но пропал из реестра (обновление) — вернул")
            }
            Err(error) => shell_log!("автозапуск не восстановился: {error}"),
        },
        RunFix::Rewrite => match write_run_value(&name, &expected) {
            Ok(()) => shell_log!(
                "автозапуск вёл на другой exe — переписал на {}",
                exe.display()
            ),
            Err(error) => shell_log!("автозапуск не переписался: {error}"),
        },
    }
}

/// Что показать переключателю: выбор человека (`autostart.json`), а если его
/// не было — включён ли автозапуск в реестре. `None` — не выбирали и не
/// включено: мастер тогда предлагает «вкл» по умолчанию, настройки — «выкл».
pub fn shown_state(choice: Option<bool>, enabled: Option<bool>) -> Option<bool> {
    choice.or(enabled.filter(|on| *on))
}

/// Состояние автозапуска для переключателей мастера и настроек.
#[tauri::command]
pub async fn get_autostart(app: AppHandle) -> Option<bool> {
    shown_state(
        read_choice(&resident::data_dir()),
        app.autolaunch().is_enabled().ok(),
    )
}

/// «Запускать вместе с Windows» (шаг «Готово» мастера). `enabled` —
/// обязательный аргумент: окно проверяет наличие команды вызовом без
/// аргументов и прячет переключатель только на «command not found».
/// Выключить невключённое — не ошибка (плагин в этом случае отказывает:
/// нечего удалять из реестра). Выбор запоминается в `autostart.json`.
#[tauri::command]
pub async fn set_autostart(app: AppHandle, enabled: bool) -> Result<(), String> {
    let manager = app.autolaunch();
    let result = if enabled {
        manager.enable()
    } else {
        manager
            .disable()
            .or_else(|error| match manager.is_enabled() {
                Ok(false) => Ok(()),
                _ => Err(error),
            })
    };
    if let Err(error) = result {
        shell_log!("автозапуск не переключился: {error}");
        return Err(format!("Не удалось изменить автозапуск: {error}"));
    }
    shell_log!("автозапуск: {}", if enabled { "вкл" } else { "выкл" });
    // Реестр уже переключён; не запомнился выбор — пострадает только
    // восстановление после обновления, окну об этом знать незачем.
    if let Err(error) = write_choice(&resident::data_dir(), enabled) {
        shell_log!("выбор автозапуска не записался в {CHOICE_FILE}: {error}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(name: &str) -> Self {
            let dir = std::env::temp_dir()
                .join(format!("meet-autostart-test-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&dir);
            TempDir(dir)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn autostart_flag_is_recognised_only_after_the_exe() {
        assert!(autostarted(&argv(&["meet.exe", "--autostart"])));
        assert!(autostarted(&argv(&[
            "meet.exe",
            "--recording",
            "x",
            "--autostart"
        ])));
        assert!(!autostarted(&argv(&["meet.exe"])));
        assert!(!autostarted(&argv(&["--autostart"])));
        assert!(!autostarted(&argv(&["meet.exe", "--autostart=1"])));
        // Обычный запуск с записью — не автозапуск.
        assert_eq!(
            crate::windows::recording_arg(&argv(&["meet.exe", "--autostart"])),
            None
        );
    }

    const EXE: &str = r"C:\Users\someone\AppData\Local\meet\meet-desktop.exe";

    fn expected() -> String {
        run_command(Path::new(EXE))
    }

    #[test]
    fn run_command_matches_the_plugin_format() {
        assert_eq!(expected(), format!("{EXE} --autostart"));
    }

    #[test]
    fn restored_only_when_chosen_and_missing_from_run_in_release() {
        let exp = expected();
        // Обновление сняло значение из Run, человек автозапуск включал.
        assert_eq!(run_fix(true, Some(true), Some(None), &exp), RunFix::Restore);
        // Значение на месте и ведёт сюда (в том числе выключенное в
        // диспетчере задач — это отдельная отметка).
        assert_eq!(
            run_fix(true, Some(true), Some(Some(&exp)), &exp),
            RunFix::Keep
        );
        // Регистр пути в Windows не важен.
        let upper = exp.to_uppercase();
        assert_eq!(
            run_fix(true, Some(true), Some(Some(&upper)), &exp),
            RunFix::Keep
        );
        // Человек выключал или не выбирал вовсе.
        assert_eq!(run_fix(true, Some(false), Some(None), &exp), RunFix::Keep);
        assert_eq!(run_fix(true, None, Some(None), &exp), RunFix::Keep);
        // Реестр не ответил — не трогаем.
        assert_eq!(run_fix(true, Some(true), None, &exp), RunFix::Keep);
        // Dev-сборка не прописывает в Run свой exe.
        assert_eq!(run_fix(false, Some(true), Some(None), &exp), RunFix::Keep);
    }

    #[test]
    fn run_value_pointing_elsewhere_is_rewritten_to_this_exe() {
        let exp = expected();
        // Обновление поставили в другую папку — автозапуск вёл бы на старый exe.
        let old = r"D:\Apps\meet\meet-desktop.exe --autostart";
        assert_eq!(
            run_fix(true, Some(true), Some(Some(old)), &exp),
            RunFix::Rewrite
        );
        // Без выбора человека — не трогаем и чужое значение.
        assert_eq!(run_fix(true, None, Some(Some(old)), &exp), RunFix::Keep);
        assert_eq!(
            run_fix(false, Some(true), Some(Some(old)), &exp),
            RunFix::Keep
        );
    }

    #[test]
    fn shown_state_prefers_the_saved_choice_then_the_registry() {
        assert_eq!(shown_state(Some(false), Some(true)), Some(false));
        assert_eq!(shown_state(Some(true), Some(false)), Some(true));
        // Выбора не было: включён в реестре — «вкл»; иначе — не выбирали.
        assert_eq!(shown_state(None, Some(true)), Some(true));
        assert_eq!(shown_state(None, Some(false)), None);
        assert_eq!(shown_state(None, None), None);
    }

    #[test]
    fn choice_is_written_read_back_and_overwritten() {
        let dir = TempDir::new("choice");
        assert_eq!(read_choice(&dir.0), None);
        write_choice(&dir.0, true).unwrap();
        assert_eq!(read_choice(&dir.0), Some(true));
        write_choice(&dir.0, false).unwrap();
        assert_eq!(read_choice(&dir.0), Some(false));
        assert!(!dir.0.join(format!("{CHOICE_FILE}.tmp")).exists());
    }

    #[test]
    fn broken_choice_file_means_no_choice() {
        let dir = TempDir::new("broken");
        std::fs::create_dir_all(&dir.0).unwrap();
        for body in ["", "{", "[]", r#"{"enabled": "yes"}"#, r#"{"other": true}"#] {
            std::fs::write(dir.0.join(CHOICE_FILE), body).unwrap();
            assert_eq!(read_choice(&dir.0), None, "{body:?}");
        }
    }

    #[cfg(windows)]
    #[test]
    fn run_key_is_readable_and_unknown_value_is_absent() {
        assert_eq!(run_value("meet-autostart-test-no-such-value"), Some(None));
    }
}
