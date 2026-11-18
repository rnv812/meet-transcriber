// «Запускать вместе с Windows»: переключатель мастера (шаг «Готово») и его
// восстановление после обновления.
//
// Само значение автозапуска — `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
// с именем продукта (tauri-plugin-autostart). Выбор человека дублируется в
// `<data_dir>\autostart.json`: установщик Tauri при ручном обновлении запускает
// старый деинсталлятор без `/UPDATE`, тот удаляет значение из Run, и без копии
// выбора автозапуск молча пропал бы после каждой новой версии.
//
// macOS — LaunchAgent `~/Library/LaunchAgents/<имя продукта>.plist` (тот же
// плагин). `SMAppService.mainApp` без привязки к Objective-C недоступен (у
// него нет консольной команды), поэтому остаёмся на LaunchAgent, но: путь к
// программе в plist переписывается при каждом старте (Meet перенесли в другую
// папку — автозапуск идёт за ним), переключатель показывает, есть ли файл, а
// выключение в «Системные настройки → Объекты входа» (launchd помечает метку
// выключенной, `launchctl print-disabled`) окно показывает строкой с кнопкой в
// этот раздел (`autostart_blocked`).

use std::path::{Path, PathBuf};

use tauri::AppHandle;
use tauri_plugin_autostart::ManagerExt;

use crate::logs::shell_log;
use crate::resident;

/// Флаг, с которым Windows запускает оболочку при входе в систему.
pub const AUTOSTART_ARG: &str = "--autostart";
/// Выбор человека: `{"enabled": bool}` в папке данных.
pub const CHOICE_FILE: &str = "autostart.json";
#[cfg(windows)]
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

/// macOS: файл LaunchAgent, который пишет tauri-plugin-autostart
/// (`<домашняя папка>/Library/LaunchAgents/<имя продукта>.plist`).
pub fn launch_agent_path(home: &Path, name: &str) -> PathBuf {
    home.join("Library")
        .join("LaunchAgents")
        .join(format!("{name}.plist"))
}

/// Где в plist лежит текст первой строки `ProgramArguments` (байты внутри
/// `<string>…</string>`); формат не узнан — `None`.
fn program_span(plist: &str) -> Option<std::ops::Range<usize>> {
    const KEY: &str = "<key>ProgramArguments</key>";
    let after_key = plist.find(KEY)? + KEY.len();
    let rest = plist[after_key..].trim_start();
    let array = rest.strip_prefix("<array>")?.trim_start();
    let body = array.strip_prefix("<string>")?;
    let start = plist.len() - body.len();
    let end = start + body.find("</string>")?;
    Some(start..end)
}

fn xml_unescape(text: &str) -> String {
    text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&amp;", "&")
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Программа, которую запускает LaunchAgent: первая строка
/// `ProgramArguments`. Формат не узнан — `None`.
pub fn launch_agent_program(plist: &str) -> Option<String> {
    program_span(plist).map(|span| xml_unescape(&plist[span]))
}

/// Тот же plist, но программа — `exe` (метка, аргументы и прочее — как были).
/// Формат не узнан — `None`: чужой файл не переписываем.
pub fn with_launch_agent_program(plist: &str, exe: &str) -> Option<String> {
    let span = program_span(plist)?;
    let mut out = String::with_capacity(plist.len() + exe.len());
    out.push_str(&plist[..span.start]);
    out.push_str(&xml_escape(exe));
    out.push_str(&plist[span.end..]);
    Some(out)
}

/// Решение по LaunchAgent при старте: переписать путь, если Meet переехал
/// (только в установленном приложении и только если человек автозапуск
/// включал). Файла нет — не возвращаем: на Mac его не снимает никакой
/// деинсталлятор, значит, убрали нарочно. `Restore` здесь не бывает.
pub fn launch_agent_fix(
    release: bool,
    choice: Option<bool>,
    plist: Option<&str>,
    exe: &str,
) -> RunFix {
    if !release || choice != Some(true) {
        return RunFix::Keep;
    }
    match plist.and_then(launch_agent_program) {
        Some(program) if program != exe => RunFix::Rewrite,
        _ => RunFix::Keep,
    }
}

/// Применить `launch_agent_fix` к файлу `path`: переписать путь через
/// временный файл рядом. Возвращает, что сделано.
pub fn fix_launch_agent(
    path: &Path,
    release: bool,
    choice: Option<bool>,
    exe: &str,
) -> std::io::Result<RunFix> {
    let plist = match std::fs::read_to_string(path) {
        Ok(text) => Some(text),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error),
    };
    let fix = launch_agent_fix(release, choice, plist.as_deref(), exe);
    if fix == RunFix::Rewrite {
        let Some(body) = plist
            .as_deref()
            .and_then(|p| with_launch_agent_program(p, exe))
        else {
            return Ok(RunFix::Keep);
        };
        let staged = path.with_extension("plist.tmp");
        std::fs::write(&staged, body)?;
        std::fs::rename(&staged, path)?;
    }
    Ok(fix)
}

/// Выключена ли метка `label` в выводе `launchctl print-disabled gui/<uid>`
/// (строки `"метка" => disabled|enabled`, на старых macOS — `true|false`).
/// Метки нет в списке или значение непонятное — `None`.
pub fn launchd_disabled(listing: &str, label: &str) -> Option<bool> {
    listing.lines().find_map(|line| {
        let rest = line.trim().strip_prefix('"')?;
        let (name, value) = rest.split_once('"')?;
        if name != label {
            return None;
        }
        match value.trim().strip_prefix("=>")?.trim() {
            "disabled" | "true" => Some(true),
            "enabled" | "false" => Some(false),
            _ => None,
        }
    })
}

/// Что показать переключателю на Mac: есть ли файл LaunchAgent (он и есть
/// автозапуск). Не выбирали и файла нет — `None` (мастер предложит «вкл»);
/// файл не прочитали — выбор человека.
pub fn mac_shown_state(choice: Option<bool>, agent: Option<bool>) -> Option<bool> {
    match agent {
        Some(true) => Some(true),
        Some(false) => choice.map(|_| false),
        None => choice,
    }
}

/// Автозапуск включён в Meet (файл есть), но launchd пометил его выключенным
/// (человек снял Meet в «Объектах входа»).
pub fn blocked_by_system(agent: bool, disabled: Option<bool>) -> bool {
    agent && disabled == Some(true)
}

/// Домашняя папка (`HOME`) — там LaunchAgents.
fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

/// Вывод `launchctl print-disabled gui/<uid>`; не ответил — `None`.
#[cfg(target_os = "macos")]
fn launchd_listing() -> Option<String> {
    // SAFETY: getuid не принимает аргументов и не может завершиться ошибкой.
    let uid = unsafe { libc::getuid() };
    let output = std::process::Command::new("/bin/launchctl")
        .arg("print-disabled")
        .arg(format!("gui/{uid}"))
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).into_owned())
}

#[cfg(not(target_os = "macos"))]
fn launchd_listing() -> Option<String> {
    None
}

/// macOS, при старте: человек автозапуск включал, а LaunchAgent ведёт на
/// другой путь (Meet перенесли) — переписать на этот.
fn restore_launch_agent(app: &AppHandle, choice: Option<bool>) {
    let Some(home) = home_dir() else {
        return;
    };
    // Как плагин: путь без ссылок.
    let Ok(exe) = std::env::current_exe().and_then(|exe| exe.canonicalize()) else {
        return;
    };
    let path = launch_agent_path(&home, &app.package_info().name);
    let exe = exe.display().to_string();
    match fix_launch_agent(&path, !cfg!(debug_assertions), choice, &exe) {
        Ok(RunFix::Rewrite) => shell_log!("автозапуск вёл на другую папку — переписал на {exe}"),
        Ok(_) => {}
        Err(error) => shell_log!("автозапуск (LaunchAgent) не переписался: {error}"),
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
/// exe (установка в другую папку) — переписать на этот. macOS — путь в
/// LaunchAgent (`restore_launch_agent`).
pub fn restore_at_startup(app: &AppHandle) {
    let choice = read_choice(&resident::data_dir());
    if cfg!(target_os = "macos") {
        restore_launch_agent(app, choice);
        return;
    }
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

/// Состояние автозапуска для переключателей мастера и настроек. macOS — есть
/// ли файл LaunchAgent (`mac_shown_state`): его могли убрать и без Meet.
#[tauri::command]
pub async fn get_autostart(app: AppHandle) -> Option<bool> {
    let choice = read_choice(&resident::data_dir());
    let enabled = app.autolaunch().is_enabled().ok();
    if cfg!(target_os = "macos") {
        mac_shown_state(choice, enabled)
    } else {
        shown_state(choice, enabled)
    }
}

/// macOS: автозапуск включён, но выключен в «Системные настройки → Объекты
/// входа» — окно показывает строку с кнопкой в этот раздел. Состояние
/// «требует одобрения» SMAppService без него не узнать; это ближайшее, что
/// видно из launchd. Не macOS или launchctl не ответил — `false`.
#[tauri::command]
pub async fn autostart_blocked(app: AppHandle) -> bool {
    if !cfg!(target_os = "macos") {
        return false;
    }
    let agent = app.autolaunch().is_enabled().unwrap_or(false);
    let label = app.package_info().name.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let disabled = launchd_listing().and_then(|listing| launchd_disabled(&listing, &label));
        blocked_by_system(agent, disabled)
    })
    .await
    .unwrap_or(false)
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

    // --- macOS: LaunchAgent ---------------------------------------------------

    const MAC_EXE: &str = "/Applications/Meet.app/Contents/MacOS/meet";

    /// Plist в том виде, в каком его пишет auto-launch (tauri-plugin-autostart).
    fn plugin_plist(exe: &str) -> String {
        format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
             <plist version=\"1.0\">\n  <dict>\n  <key>Label</key>\n  <string>Meet</string>\n  \
             <key>ProgramArguments</key>\n  <array><string>{exe}</string><string>--autostart</string></array>\n  \
             <key>RunAtLoad</key>\n  <true/>\n  </dict>\n</plist>"
        )
    }

    #[test]
    fn launch_agent_program_is_the_first_program_argument() {
        assert_eq!(
            launch_agent_program(&plugin_plist(MAC_EXE)).as_deref(),
            Some(MAC_EXE)
        );
        // Отступы и переносы, как у plist, сохранённого другими программами.
        let spaced = "<plist><dict><key>ProgramArguments</key>\n\t<array>\n\t\t<string>/A b/Meet.app/Contents/MacOS/meet</string>\n\t</array></dict></plist>";
        assert_eq!(
            launch_agent_program(spaced).as_deref(),
            Some("/A b/Meet.app/Contents/MacOS/meet")
        );
        // Сущности XML раскрываются.
        let escaped = plugin_plist("/Users/a/R&amp;D/Meet.app/Contents/MacOS/meet");
        assert_eq!(
            launch_agent_program(&escaped).as_deref(),
            Some("/Users/a/R&D/Meet.app/Contents/MacOS/meet")
        );
        // Не узнали формат — не знаем и программу.
        for body in [
            "",
            "<plist><dict><key>Program</key><string>/x</string></dict></plist>",
            "<plist><dict><key>ProgramArguments</key><array></array></dict></plist>",
            "<plist><dict><key>ProgramArguments</key><string>/x</string></dict></plist>",
            "<plist><dict><key>ProgramArguments</key><array><string>/x",
        ] {
            assert_eq!(launch_agent_program(body), None, "{body:?}");
        }
    }

    #[test]
    fn launch_agent_program_is_rewritten_in_place() {
        let old = plugin_plist("/Users/a/Downloads/Meet.app/Contents/MacOS/meet");
        let new = with_launch_agent_program(&old, MAC_EXE).unwrap();
        // Только путь: метка, аргументы и RunAtLoad — как были.
        assert_eq!(new, plugin_plist(MAC_EXE));
        assert_eq!(launch_agent_program(&new).as_deref(), Some(MAC_EXE));
        // Путь с метасимволами XML пишется экранированным и читается обратно.
        let odd = "/Users/a/R&D <old>/Meet.app/Contents/MacOS/meet";
        let new = with_launch_agent_program(&old, odd).unwrap();
        assert!(new.contains(
            "<string>/Users/a/R&amp;D &lt;old&gt;/Meet.app/Contents/MacOS/meet</string>"
        ));
        assert_eq!(launch_agent_program(&new).as_deref(), Some(odd));
        // Чужой формат не переписываем.
        assert_eq!(with_launch_agent_program("<plist/>", MAC_EXE), None);
    }

    #[test]
    fn launch_agent_follows_the_app_only_when_chosen_in_release() {
        let here = plugin_plist(MAC_EXE);
        let moved = plugin_plist("/Users/a/Downloads/Meet.app/Contents/MacOS/meet");
        // Meet перенесли в другую папку — LaunchAgent вёл бы в никуда.
        assert_eq!(
            launch_agent_fix(true, Some(true), Some(&moved), MAC_EXE),
            RunFix::Rewrite
        );
        assert_eq!(
            launch_agent_fix(true, Some(true), Some(&here), MAC_EXE),
            RunFix::Keep
        );
        // Не выбирали, выключали, dev-сборка — не трогаем.
        assert_eq!(
            launch_agent_fix(true, None, Some(&moved), MAC_EXE),
            RunFix::Keep
        );
        assert_eq!(
            launch_agent_fix(true, Some(false), Some(&moved), MAC_EXE),
            RunFix::Keep
        );
        assert_eq!(
            launch_agent_fix(false, Some(true), Some(&moved), MAC_EXE),
            RunFix::Keep
        );
        // Файла нет: на Mac его не снимает никакой деинсталлятор — значит,
        // убрали нарочно; не возвращаем.
        assert_eq!(
            launch_agent_fix(true, Some(true), None, MAC_EXE),
            RunFix::Keep
        );
        // Чужой формат — не трогаем.
        assert_eq!(
            launch_agent_fix(true, Some(true), Some("<plist/>"), MAC_EXE),
            RunFix::Keep
        );
    }

    #[test]
    fn launch_agent_file_is_rewritten_at_startup() {
        let dir = TempDir::new("agent");
        std::fs::create_dir_all(&dir.0).unwrap();
        let path = launch_agent_path(&dir.0, "Meet");
        assert_eq!(
            path,
            dir.0
                .join("Library")
                .join("LaunchAgents")
                .join("Meet.plist")
        );
        // Файла нет — ничего не делаем и не создаём.
        assert_eq!(
            fix_launch_agent(&path, true, Some(true), MAC_EXE).unwrap(),
            RunFix::Keep
        );
        assert!(!path.exists());
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(
            &path,
            plugin_plist("/Volumes/Meet/Meet.app/Contents/MacOS/meet"),
        )
        .unwrap();
        assert_eq!(
            fix_launch_agent(&path, true, Some(true), MAC_EXE).unwrap(),
            RunFix::Rewrite
        );
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            plugin_plist(MAC_EXE)
        );
        // Второй старт — уже на месте.
        assert_eq!(
            fix_launch_agent(&path, true, Some(true), MAC_EXE).unwrap(),
            RunFix::Keep
        );
        let staged = path.with_extension("plist.tmp");
        assert!(!staged.exists());
    }

    #[test]
    fn launchd_disabled_list_is_parsed_for_the_label() {
        let listing = "disabled services = {\n\
            \t\"com.apple.ScreenReaderUIServer\" => disabled\n\
            \t\"Meet\" => disabled\n\
            \t\"com.other.app\" => enabled\n\
            }\n\
            \n\
            login item associations = {\n\
            }\n";
        assert_eq!(launchd_disabled(listing, "Meet"), Some(true));
        assert_eq!(launchd_disabled(listing, "com.other.app"), Some(false));
        // Метка — с учётом регистра; нет в списке — система не решала.
        assert_eq!(launchd_disabled(listing, "meet"), None);
        assert_eq!(launchd_disabled(listing, "com.absent"), None);
        assert_eq!(launchd_disabled("", "Meet"), None);
        // Старые macOS пишут true/false.
        let old = "disabled services = {\n\t\"Meet\" => true\n\t\"Other\" => false\n}\n";
        assert_eq!(launchd_disabled(old, "Meet"), Some(true));
        assert_eq!(launchd_disabled(old, "Other"), Some(false));
        // Непонятное значение — не знаем.
        assert_eq!(launchd_disabled("\t\"Meet\" => maybe\n", "Meet"), None);
    }

    #[test]
    fn mac_toggle_shows_whether_the_launch_agent_is_there() {
        // Файл LaunchAgent и есть автозапуск: убрали его — «выкл», даже
        // если в Meet когда-то включали.
        assert_eq!(mac_shown_state(Some(true), Some(false)), Some(false));
        assert_eq!(mac_shown_state(Some(false), Some(true)), Some(true));
        assert_eq!(mac_shown_state(Some(true), Some(true)), Some(true));
        assert_eq!(mac_shown_state(Some(false), Some(false)), Some(false));
        // Не выбирали и файла нет — мастер предложит «вкл».
        assert_eq!(mac_shown_state(None, Some(false)), None);
        assert_eq!(mac_shown_state(None, Some(true)), Some(true));
        // Не прочитали — выбор человека.
        assert_eq!(mac_shown_state(Some(true), None), Some(true));
        assert_eq!(mac_shown_state(None, None), None);
    }

    #[test]
    fn blocked_only_when_the_agent_is_there_and_launchd_disabled_it() {
        assert!(blocked_by_system(true, Some(true)));
        assert!(!blocked_by_system(true, Some(false)));
        assert!(!blocked_by_system(true, None));
        // Нет файла — выключено в самом Meet, системе нечего запрещать.
        assert!(!blocked_by_system(false, Some(true)));
    }

    #[cfg(windows)]
    #[test]
    fn run_key_is_readable_and_unknown_value_is_absent() {
        assert_eq!(run_value("meet-autostart-test-no-such-value"), Some(None));
    }
}
