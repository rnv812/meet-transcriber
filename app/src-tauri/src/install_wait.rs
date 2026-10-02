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

/// Процессы (pid, exe), которые держат папку установки, кроме `skip`.
pub fn blocking(processes: &[(u32, PathBuf)], root: &Path, skip: &[u32]) -> Vec<u32> {
    processes
        .iter()
        .filter(|(pid, exe)| !skip.contains(pid) && inside(exe, root))
        .map(|(pid, _)| *pid)
        .collect()
}

/// Кого помощник не ждёт: себя и того, кто его запустил. Деинсталлятор,
/// запущенный на месте (`_?=$INSTDIR`, «Сначала удалить версию X»), лежит в
/// папке установки и ждёт помощника — ждать его в ответ значит простоять все
/// 90 секунд. `parents` — (pid, pid родителя) из снимка процессов.
pub fn skipped(own: u32, parents: &[(u32, u32)]) -> Vec<u32> {
    let mut skip = vec![own];
    if let Some((_, parent)) = parents.iter().find(|(pid, _)| *pid == own) {
        if *parent != 0 && *parent != own {
            skip.push(*parent);
        }
    }
    skip
}

/// Запуск помощника: код выхода процесса.
pub fn run(mode: Mode, root: &Path) -> i32 {
    // Короткое имя 8.3 (`C:\Users\SOMEON~1\…`) не совпало бы с длинными
    // путями процессов — сравниваем длинные.
    let root = long_path(root);
    let root = root.as_path();
    let skip = skipped(std::process::id(), &parents());
    let busy = || {
        let list: Vec<(u32, PathBuf)> = processes();
        !blocking(&list, root, &skip).is_empty()
    };
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

/// Длинный путь (GetLongPathNameW): `C:\Users\SOMEON~1` → `C:\Users\someone.longname`.
/// Не вышло (папки нет, ошибка) — путь как есть.
#[cfg(windows)]
pub fn long_path(path: &Path) -> PathBuf {
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use windows_sys::Win32::Storage::FileSystem::GetLongPathNameW;

    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    // SAFETY: строка с завершающим нулём; буфер — на `needed` символов,
    // вызов с нулевым буфером только узнаёт размер.
    unsafe {
        let needed = GetLongPathNameW(wide.as_ptr(), std::ptr::null_mut(), 0);
        if needed == 0 {
            return path.to_path_buf();
        }
        let mut buffer = vec![0u16; needed as usize];
        let got = GetLongPathNameW(wide.as_ptr(), buffer.as_mut_ptr(), needed);
        if got == 0 || got >= needed {
            return path.to_path_buf();
        }
        PathBuf::from(std::ffi::OsString::from_wide(&buffer[..got as usize]))
    }
}

#[cfg(not(windows))]
pub fn long_path(path: &Path) -> PathBuf {
    path.to_path_buf()
}

/// (pid, pid родителя) всех процессов.
#[cfg(windows)]
fn parents() -> Vec<(u32, u32)> {
    use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };

    let mut list = Vec::new();
    // SAFETY: снимок закрывается ровно один раз; структура инициализирована
    // нулями с верным dwSize.
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return list;
        }
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
        let mut more = Process32FirstW(snapshot, &mut entry) != 0;
        while more {
            list.push((entry.th32ProcessID, entry.th32ParentProcessID));
            more = Process32NextW(snapshot, &mut entry) != 0;
        }
        CloseHandle(snapshot);
    }
    list
}

#[cfg(not(windows))]
fn parents() -> Vec<(u32, u32)> {
    Vec::new()
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

    #[cfg(windows)]
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
        assert_eq!(blocking(&list, root, &[30]), vec![10, 11, 12]);
        assert!(blocking(&[], root, &[1]).is_empty());
        // Деинсталлятор на месте (родитель помощника) не ждётся.
        let uninstaller = vec![
            (50, PathBuf::from(format!(r"{ROOT}\uninstall.exe"))),
            (51, PathBuf::from(format!(r"{ROOT}\meet-desktop.exe"))),
        ];
        assert!(blocking(&uninstaller, root, &[51, 50]).is_empty());
    }

    #[test]
    fn helper_skips_itself_and_its_parent() {
        let parents = [(4, 0), (50, 4), (51, 50), (60, 51)];
        assert_eq!(skipped(51, &parents), vec![51, 50]);
        // Родитель неизвестен (снимок не удался) — только сам помощник.
        assert_eq!(skipped(51, &[]), vec![51]);
        assert_eq!(skipped(4, &parents), vec![4]);
    }

    #[test]
    fn the_running_helper_knows_its_parent() {
        let own = std::process::id();
        let skip = skipped(own, &parents());
        assert_eq!(skip[0], own);
        if cfg!(windows) {
            assert_eq!(skip.len(), 2, "у тестового процесса есть родитель");
        }
    }

    #[cfg(windows)]
    #[test]
    fn long_path_expands_short_names_and_keeps_unknown_paths() {
        let dir =
            std::env::temp_dir().join(format!("meet-long-folder-name-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let long = long_path(&dir);
        assert!(long
            .to_string_lossy()
            .to_lowercase()
            .ends_with(&format!("meet-long-folder-name-{}", std::process::id())));
        #[cfg(windows)]
        {
            use std::os::windows::ffi::{OsStrExt, OsStringExt};
            use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;
            let wide: Vec<u16> = dir.as_os_str().encode_wide().chain(Some(0)).collect();
            let mut buffer = vec![0u16; 1024];
            // SAFETY: строка с нулём, буфер на 1024 символа.
            let got = unsafe { GetShortPathNameW(wide.as_ptr(), buffer.as_mut_ptr(), 1024) };
            if got > 0 && (got as usize) < buffer.len() {
                let short = PathBuf::from(std::ffi::OsString::from_wide(&buffer[..got as usize]));
                assert!(inside(&long_path(&short).join("x.exe"), &long_path(&dir)));
            }
        }
        let missing = Path::new(r"C:\нет-такой-папки-meet\x");
        assert_eq!(long_path(missing), missing.to_path_buf());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn the_folder_itself_is_not_inside_itself() {
        assert!(!inside(Path::new(ROOT), Path::new(ROOT)));
        assert!(!inside(Path::new(r"C:\x.exe"), Path::new("")));
    }
}
