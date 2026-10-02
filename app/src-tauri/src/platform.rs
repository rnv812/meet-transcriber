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

#[cfg(test)]
mod tests {
    use super::*;

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
