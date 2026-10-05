// Платформа оболочки: Windows (основная) и macOS (экспериментально, Apple
// Silicon). Здесь — то, что различается между ОС и нужно нескольким модулям:
// папка данных, имена программ и раскладка venv, «открыть папку или адрес»,
// «жив ли процесс», группа процессов вместо job object, свободное место.
//
// Код Windows остаётся там, где был (`windows_sys` в модулях); здесь — его
// двойники для macOS и выбор по `cfg`. Чистые функции (с параметром ОС)
// проверяются тестами на любой платформе.

use std::path::{Path, PathBuf};
use std::process::Command;

/// ОС, под которую собрана оболочка.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Os {
    Windows,
    MacOs,
}

pub const fn current() -> Os {
    if cfg!(target_os = "macos") {
        Os::MacOs
    } else {
        Os::Windows
    }
}

/// Папка, внутри которой лежит `meet`: `%LOCALAPPDATA%` на Windows,
/// `~/Library/Application Support` на macOS. Переменной нет — «.», как раньше.
pub fn data_root_for(os: Os, local_app_data: Option<&str>, home: Option<&str>) -> PathBuf {
    let pick = |value: Option<&str>| value.filter(|v| !v.trim().is_empty()).map(PathBuf::from);
    match os {
        Os::Windows => pick(local_app_data).unwrap_or_else(|| PathBuf::from(".")),
        Os::MacOs => pick(home)
            .map(|home| home.join("Library").join("Application Support"))
            .unwrap_or_else(|| PathBuf::from(".")),
    }
}

pub fn data_root() -> PathBuf {
    let local = std::env::var("LOCALAPPDATA").ok();
    let home = std::env::var("HOME").ok();
    data_root_for(current(), local.as_deref(), home.as_deref())
}

/// Имя программы: `uv` → `uv.exe` на Windows.
pub fn exe_for(os: Os, name: &str) -> String {
    match os {
        Os::Windows => format!("{name}.exe"),
        Os::MacOs => name.to_string(),
    }
}

pub fn exe(name: &str) -> String {
    exe_for(current(), name)
}

/// Папка программ venv: `Scripts` на Windows, `bin` на macOS.
pub fn venv_bin_for(os: Os) -> &'static str {
    match os {
        Os::Windows => "Scripts",
        Os::MacOs => "bin",
    }
}

pub fn venv_bin() -> &'static str {
    venv_bin_for(current())
}

/// Профиль движка, который ставится на этой ОС, если видеокарты NVIDIA нет
/// (на macOS — всегда «Apple Silicon»).
pub fn engine_profile_for(os: Os, nvidia: bool) -> &'static str {
    match os {
        Os::MacOs => "mac",
        Os::Windows if nvidia => "cuda",
        Os::Windows => "cpu",
    }
}

/// Команда «открыть папку или адрес»: explorer на Windows, `open` на macOS.
pub fn open_command_for(os: Os, target: &Path) -> Command {
    let mut command = Command::new(match os {
        Os::Windows => "explorer",
        Os::MacOs => "open",
    });
    command.arg(target);
    command
}

/// Открыть папку в Finder/Проводнике. Ждём только запуска: explorer
/// возвращает ненулевой код и при успехе.
pub fn open_folder(target: &Path) -> std::io::Result<()> {
    open_command_for(current(), target).spawn().map(|_| ())
}

/// Жив ли процесс (macOS): сигнал 0 ничего не посылает; EPERM — процесс
/// есть, но чужой.
#[cfg(unix)]
pub fn pid_alive(pid: u32) -> bool {
    let Ok(pid) = libc::pid_t::try_from(pid) else {
        return false;
    };
    if pid <= 0 {
        return false;
    }
    // SAFETY: kill с сигналом 0 только проверяет существование процесса.
    let result = unsafe { libc::kill(pid, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

/// Запускать ребёнка лидером своей группы процессов: его потомков (uv →
/// python, лаунчер резидента → Python) потом гасит `kill_group` — двойник
/// job object Windows.
#[cfg(unix)]
pub fn own_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    command.process_group(0);
}

#[cfg(not(unix))]
pub fn own_group(_command: &mut Command) {}

/// Погасить группу процессов `pid` (её лидер — `pid`, см. `own_group`).
#[cfg(unix)]
pub fn kill_group(pid: u32, hard: bool) {
    let Ok(pid) = libc::pid_t::try_from(pid) else {
        return;
    };
    if pid <= 0 {
        return;
    }
    let signal = if hard { libc::SIGKILL } else { libc::SIGTERM };
    // SAFETY: killpg только посылает сигнал; группа — наша (own_group).
    unsafe {
        libc::killpg(pid, signal);
    }
}

/// Свободно на диске пути (ближайшая существующая папка), ГБ.
#[cfg(unix)]
pub fn free_gb(path: &Path) -> Option<f64> {
    use std::os::unix::ffi::OsStrExt;
    let existing = path.ancestors().find(|dir| dir.exists())?;
    let mut bytes = existing.as_os_str().as_bytes().to_vec();
    bytes.push(0);
    // SAFETY: строка с нулём на конце живёт до конца вызова; statvfs пишет в
    // локальную структуру.
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    let ok = unsafe { libc::statvfs(bytes.as_ptr().cast(), &mut stat) } == 0;
    #[allow(clippy::unnecessary_cast)]
    let free = stat.f_bavail as f64 * stat.f_frsize as f64;
    ok.then(|| free / f64::from(1u32 << 30))
}

/// Метки вокруг PATH в выводе оболочки входа: профили (`.zprofile`,
/// `.zshrc`) бывают разговорчивыми — берём только текст между метками.
const LOGIN_PATH_BEGIN: &str = "__MEET_LOGIN_PATH_BEGIN__";
const LOGIN_PATH_END: &str = "__MEET_LOGIN_PATH_END__";
/// Дольше оболочку входа не ждём: запуск приложения важнее.
#[cfg_attr(not(unix), allow(dead_code))]
const LOGIN_PATH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(3);

/// PATH из вывода оболочки входа (между метками); нет меток или пусто — None.
#[cfg_attr(not(unix), allow(dead_code))]
pub fn marked_path(output: &str) -> Option<&str> {
    let start = output.find(LOGIN_PATH_BEGIN)? + LOGIN_PATH_BEGIN.len();
    let length = output[start..].find(LOGIN_PATH_END)?;
    let path = output[start..start + length].trim();
    (!path.is_empty()).then_some(path)
}

/// PATH (macOS, `:`) с папками из `extra`: прежние остаются на своих местах,
/// новые — в конец по порядку, без повторов и пустых. Второе — сколько папок
/// добавилось.
pub fn merged_path(current: &str, extra: &str) -> (String, usize) {
    let mut dirs: Vec<&str> = Vec::new();
    for dir in current.split(':').filter(|d| !d.is_empty()) {
        if !dirs.contains(&dir) {
            dirs.push(dir);
        }
    }
    let before = dirs.len();
    for dir in extra.split(':').filter(|d| !d.is_empty()) {
        if !dirs.contains(&dir) {
            dirs.push(dir);
        }
    }
    let added = dirs.len() - before;
    (dirs.join(":"), added)
}

/// Оболочка входа пользователя: `$SHELL`, иначе zsh (по умолчанию в macOS).
#[cfg_attr(not(unix), allow(dead_code))]
pub fn login_shell(shell_var: Option<&str>) -> String {
    shell_var
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("/bin/zsh")
        .to_string()
}

/// macOS: дополнить PATH процесса PATH-ом оболочки входа. Приложение из
/// Finder, Dock или автозапуска получает PATH launchd
/// (`/usr/bin:/bin:/usr/sbin:/sbin`), и ни резидент, ни агент в терминале не
/// видят `~/.local/bin/claude`, `/opt/homebrew/bin/codex`, node от npm.
/// Спрашиваем `$SHELL -ilc` (как Терминал: профиль и rc) и дописываем его
/// папки в конец; прежние остаются. Вызывать в начале `main`, до потоков:
/// резидент, задания и агенты наследуют окружение. Не вышло — только строка
/// в журнале.
#[cfg(unix)]
pub fn adopt_login_path() {
    let shell = login_shell(std::env::var("SHELL").ok().as_deref());
    match read_login_path(&shell) {
        Ok(login) => {
            let current = std::env::var("PATH").unwrap_or_default();
            let (path, added) = merged_path(&current, &login);
            if added > 0 {
                std::env::set_var("PATH", &path);
            }
            crate::logs::shell_log!("PATH из оболочки входа ({shell}): добавлено папок {added}");
        }
        Err(error) => {
            crate::logs::shell_log!("PATH оболочки входа ({shell}) не получен: {error}");
        }
    }
}

/// Запустить оболочку входа и прочитать PATH между метками. Вывод читает
/// отдельный поток и отдаёт его, как только пришла вторая метка: демон из
/// профиля (ssh-agent и т. п.) может держать вывод открытым и после выхода
/// оболочки. Не уложилась в `LOGIN_PATH_TIMEOUT` — гасим её группу.
#[cfg(unix)]
fn read_login_path(shell: &str) -> Result<String, String> {
    use std::io::Read;
    use std::process::Stdio;
    use std::sync::mpsc;

    let script = format!("printf '%s%s%s' '{LOGIN_PATH_BEGIN}' \"$PATH\" '{LOGIN_PATH_END}'");
    let mut command = Command::new(shell);
    command
        .args(["-i", "-l", "-c", &script])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    // Своя сессия без управляющего терминала (как `detached` у VS Code):
    // интерактивный zsh, запущенный из терминала (`cargo tauri dev`), иначе
    // тянул бы терминал к себе из фоновой группы и останавливался SIGTTIN.
    // Лидер сессии — лидер и своей группы: `kill_group` гасит всё.
    // SAFETY: между fork и exec — только setsid, он async-signal-safe.
    unsafe {
        use std::os::unix::process::CommandExt;
        command.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("не запустилась: {e}"))?;
    let mut stdout = child.stdout.take().ok_or("нет вывода")?;
    let (send, receive) = mpsc::channel();
    std::thread::spawn(move || {
        let mut output = Vec::new();
        let mut chunk = [0u8; 4096];
        loop {
            match stdout.read(&mut chunk) {
                Ok(0) | Err(_) => break,
                Ok(n) => output.extend_from_slice(&chunk[..n]),
            }
            if marked_path(&String::from_utf8_lossy(&output)).is_some() {
                break;
            }
        }
        let _ = send.send(String::from_utf8_lossy(&output).into_owned());
    });
    let Ok(output) = receive.recv_timeout(LOGIN_PATH_TIMEOUT) else {
        // Зависла (ждёт ввода, `exec tmux` и т. п.) — гасим всю её группу.
        kill_group(child.id(), true);
        let _ = child.wait();
        return Err("оболочка не ответила за 3 с".into());
    };
    // PATH получен: оболочка после printf выходит сама; не вышла — гасим
    // только её (демоны профиля, оставшиеся в группе, не наше дело).
    if !matches!(child.try_wait(), Ok(Some(_))) {
        let _ = child.kill();
    }
    let _ = child.wait();
    marked_path(&output)
        .map(str::to_string)
        .ok_or_else(|| "в выводе нет PATH".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn login_path_is_read_between_markers() {
        let noisy = format!(
            "Last login: Mon\nwelcome!\n{LOGIN_PATH_BEGIN}/opt/homebrew/bin:/usr/bin{LOGIN_PATH_END}"
        );
        assert_eq!(marked_path(&noisy), Some("/opt/homebrew/bin:/usr/bin"));
        assert_eq!(marked_path("/usr/bin"), None, "без меток — не PATH");
        assert_eq!(marked_path(&format!("{LOGIN_PATH_BEGIN}/usr/bin")), None);
        assert_eq!(
            marked_path(&format!("{LOGIN_PATH_BEGIN} {LOGIN_PATH_END}")),
            None
        );
    }

    #[test]
    fn login_path_is_appended_keeping_existing_dirs() {
        let (path, added) = merged_path(
            "/usr/bin:/bin:/usr/sbin:/sbin",
            "/opt/homebrew/bin:/usr/bin:/Users/u/.local/bin::/bin",
        );
        assert_eq!(
            path,
            "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/Users/u/.local/bin"
        );
        assert_eq!(added, 2);
        assert_eq!(merged_path("/a:/b", "/b:/a"), ("/a:/b".to_string(), 0));
        assert_eq!(merged_path("", "/a"), ("/a".to_string(), 1));
        assert_eq!(merged_path("/a::/a", ""), ("/a".to_string(), 0));
    }

    #[test]
    fn login_shell_defaults_to_zsh() {
        assert_eq!(
            login_shell(Some("/opt/homebrew/bin/fish")),
            "/opt/homebrew/bin/fish"
        );
        assert_eq!(login_shell(Some("  ")), "/bin/zsh");
        assert_eq!(login_shell(None), "/bin/zsh");
    }

    #[cfg(unix)]
    #[test]
    fn login_path_comes_from_a_real_shell() {
        // /bin/sh: без профилей пользователя, но метки и PATH — настоящие.
        let path = read_login_path("/bin/sh").unwrap();
        assert!(!path.is_empty());
    }

    #[test]
    fn data_root_follows_the_os() {
        assert_eq!(
            data_root_for(Os::Windows, Some(r"C:\Users\u\AppData\Local"), Some("/x")),
            PathBuf::from(r"C:\Users\u\AppData\Local")
        );
        assert_eq!(
            data_root_for(Os::MacOs, Some("ignored"), Some("/Users/u")),
            PathBuf::from("/Users/u")
                .join("Library")
                .join("Application Support")
        );
        assert_eq!(data_root_for(Os::Windows, None, None), PathBuf::from("."));
        assert_eq!(
            data_root_for(Os::MacOs, None, Some(" ")),
            PathBuf::from(".")
        );
    }

    #[test]
    fn program_names_and_venv_layout() {
        assert_eq!(exe_for(Os::Windows, "uv"), "uv.exe");
        assert_eq!(exe_for(Os::MacOs, "uv"), "uv");
        assert_eq!(venv_bin_for(Os::Windows), "Scripts");
        assert_eq!(venv_bin_for(Os::MacOs), "bin");
    }

    #[test]
    fn engine_profile_is_apple_silicon_on_mac() {
        assert_eq!(engine_profile_for(Os::Windows, true), "cuda");
        assert_eq!(engine_profile_for(Os::Windows, false), "cpu");
        assert_eq!(engine_profile_for(Os::MacOs, false), "mac");
        assert_eq!(engine_profile_for(Os::MacOs, true), "mac");
    }

    #[test]
    fn open_uses_explorer_or_open() {
        let target = Path::new("folder");
        assert_eq!(
            open_command_for(Os::Windows, target).get_program(),
            "explorer"
        );
        let mac = open_command_for(Os::MacOs, target);
        assert_eq!(mac.get_program(), "open");
        assert_eq!(mac.get_args().collect::<Vec<_>>(), vec!["folder"]);
    }

    #[cfg(unix)]
    #[test]
    fn own_process_is_alive_and_free_space_is_known() {
        assert!(pid_alive(std::process::id()));
        assert!(!pid_alive(0));
        assert!(free_gb(&std::env::temp_dir()).is_some_and(|gb| gb > 0.0));
    }
}
