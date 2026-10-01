// Помощник установщика: дождаться, пока процессы из папки установки выйдут.
//
// Установщик (`windows/hooks.nsh`) заменяет файлы в `$INSTDIR\resources`, а
// резидент, дописывая запись, запускает оттуда ffmpeg.exe. Закрыть оболочку
// мало: резидент (свой — после «Выхода» он уже вышел; осиротевший после
// принудительного закрытия 0.1.0 или чужой, `External`) ещё может сохранять
// запись — и копирование упало бы с «Error opening file for writing».
//
// Установщик распаковывает новую оболочку во временную папку и запускает её
// с флагом, без Tauri и окон:
//   `--installer-busy <папка>` — код 1, если там что-то работает, иначе 0;
//   `--installer-wait <папка>` — попросить резидент штатно выйти (`/shutdown`:
//      идущая запись сохраняется) и ждать до 90 с, пока не выйдут все
//      процессы, чей exe лежит в папке установки (резидент из движка, его
//      Python, ffmpeg, uv). Код 0 — вышли, 1 — не дождались.

use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

use crate::api;
use crate::resident;

pub const BUSY_ARG: &str = "--installer-busy";
pub const WAIT_ARG: &str = "--installer-wait";
/// Ждём не дольше: дальше установщик продолжит и, если файл занят, сам
/// предложит «Повторить».
const WAIT_LIMIT: Duration = Duration::from_secs(90);
const POLL: Duration = Duration::from_millis(500);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Busy,
    Wait,
}

/// Режим помощника и папка установки из аргументов (первый — путь к exe).
pub fn requested(args: &[String]) -> Option<(Mode, PathBuf)> {
    let mut rest = args.iter().skip(1);
    let mode = match rest.next()?.as_str() {
        BUSY_ARG => Mode::Busy,
        WAIT_ARG => Mode::Wait,
        _ => return None,
    };
    let root = rest.next().map(|text| text.trim().trim_matches('"'))?;
    (!root.is_empty()).then(|| (mode, PathBuf::from(root)))
}

/// Лежит ли `path` в папке `root` (сравнение по частям пути, без учёта
/// регистра, как в Windows; `..` в пути — не в папке).
pub fn inside(path: &Path, root: &Path) -> bool {
    let parts = |p: &Path| -> Option<Vec<String>> {
        p.components()
            .map(|part| match part {
                Component::ParentDir => None,
                Component::CurDir => Some(String::new()),
                other => Some(
                    other
                        .as_os_str()
                        .to_string_lossy()
                        .trim_end_matches(['\\', '/'])
                        .to_lowercase(),
                ),
            })
            .filter(|part| part.as_deref() != Some(""))
            .collect()
    };
    match (parts(path), parts(root)) {
        (Some(path), Some(root)) => {
            !root.is_empty() && path.len() > root.len() && path.starts_with(&root)
        }
        _ => false,
    }
}

/// Процессы (pid, exe), которые держат папку установки, кроме самого
/// помощника.
pub fn blocking(processes: &[(u32, PathBuf)], root: &Path, own_pid: u32) -> Vec<u32> {
    processes
        .iter()
        .filter(|(pid, exe)| *pid != own_pid && inside(exe, root))
        .map(|(pid, _)| *pid)
        .collect()
}

/// Запуск помощника: код выхода процесса.
pub fn run(mode: Mode, root: &Path) -> i32 {
    let own = std::process::id();
    let busy = || !blocking(&processes(), root, own).is_empty();
    match mode {
        Mode::Busy => i32::from(busy() || resident_answers()),
        Mode::Wait => {
            let deadline = Instant::now() + WAIT_LIMIT;
            // Резидент, который ещё отвечает (чужой или осиротевший), просим
            // выйти штатно: он сохраняет запись и гасит своих детей.
            if let Some(endpoint) = resident::read_endpoint().filter(resident::answers) {
                let _ = api::Client::new(&endpoint).post("/shutdown", serde_json::Value::Null);
            }
            while Instant::now() < deadline {
                if !busy() {
                    return 0;
                }
                std::thread::sleep(POLL);
            }
            1
        }
    }
}

fn resident_answers() -> bool {
    resident::read_endpoint().is_some_and(|endpoint| resident::answers(&endpoint))
}

/// Все процессы, чей путь к exe удалось узнать.
#[cfg(windows)]
fn processes() -> Vec<(u32, PathBuf)> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    let mut list = Vec::new();
    // SAFETY: снимок закрывается ровно один раз; структура инициализирована
    // нулями с верным dwSize; буфер пути — на `size` символов.
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return list;
        }
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut more = Process32FirstW(snapshot, &mut entry) != 0;
        while more {
            let pid = entry.th32ProcessID;
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if !handle.is_null() {
                let mut buffer = vec![0u16; 32_768];
                let mut size = buffer.len() as u32;
                if QueryFullProcessImageNameW(handle, 0, buffer.as_mut_ptr(), &mut size) != 0 {
                    let path = std::ffi::OsString::from_wide(&buffer[..size as usize]);
                    list.push((pid, PathBuf::from(path)));
                }
                CloseHandle(handle);
            }
            more = Process32NextW(snapshot, &mut entry) != 0;
        }
        CloseHandle(snapshot);
    }
    list
}

#[cfg(not(windows))]
fn processes() -> Vec<(u32, PathBuf)> {
    Vec::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    const ROOT: &str = r"C:\Users\someone\AppData\Local\meet";

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn helper_modes_come_with_the_install_folder() {
        assert_eq!(
            requested(&argv(&["helper.exe", "--installer-wait", ROOT])),
            Some((Mode::Wait, PathBuf::from(ROOT)))
        );
        assert_eq!(
            requested(&argv(&[
                "helper.exe",
                "--installer-busy",
                &format!("\"{ROOT}\"")
            ])),
            Some((Mode::Busy, PathBuf::from(ROOT)))
        );
        assert_eq!(requested(&argv(&["helper.exe", "--installer-wait"])), None);
        assert_eq!(
            requested(&argv(&["helper.exe", "--installer-wait", " "])),
            None
        );
        assert_eq!(requested(&argv(&["meet-desktop.exe", "--quit"])), None);
        assert_eq!(requested(&argv(&["meet-desktop.exe"])), None);
    }

    #[test]
    fn processes_from_the_install_folder_block_the_copy() {
        let root = Path::new(ROOT);
        let list = vec![
            (10, PathBuf::from(format!(r"{ROOT}\resources\ffmpeg.exe"))),
            (
                11,
                PathBuf::from(format!(r"{ROOT}\engine\0.1.0\Scripts\python.exe")),
            ),
            // Регистр в Windows не важен.
            (
                12,
                PathBuf::from(r"c:\users\SOMEONE\appdata\local\MEET\engine\python\python.exe"),
            ),
            // Чужой ffmpeg из PATH — не наш.
            (20, PathBuf::from(r"C:\Tools\ffmpeg\bin\ffmpeg.exe")),
            // Соседняя папка с тем же началом имени — не наша.
            (
                21,
                PathBuf::from(r"C:\Users\someone\AppData\Local\meet-old\ffmpeg.exe"),
            ),
            // Сам помощник не ждёт сам себя.
            (30, PathBuf::from(format!(r"{ROOT}\meet-desktop.exe"))),
            // «..» уводит из папки.
            (40, PathBuf::from(format!(r"{ROOT}\..\other\x.exe"))),
        ];
        assert_eq!(blocking(&list, root, 30), vec![10, 11, 12]);
        assert!(blocking(&[], root, 1).is_empty());
    }

    #[test]
    fn the_folder_itself_is_not_inside_itself() {
        assert!(!inside(Path::new(ROOT), Path::new(ROOT)));
        assert!(!inside(Path::new(r"C:\x.exe"), Path::new("")));
    }
}
