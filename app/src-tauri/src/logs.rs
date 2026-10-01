// Журналы: `%LOCALAPPDATA%\meet\logs\resident.log` — вывод резидента (его
// stdout/stderr, в том числе traceback падения), `shell.log` — строки самой
// оболочки. Без них «Сервис записи не запускается» было не с чем разбирать:
// релизная оболочка без консоли, и вывод резидента уходил в никуда.
//
// IMPORTANT: токен API сюда не пишется никогда — ни строкой, ни `{:?}`
// (у `Endpoint` и `api::Client` нарочно нет Debug).

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::resident;

/// Больше этого журнал при следующем открытии уезжает в `.1` (один архив).
pub const ROTATE_AT: u64 = 1024 * 1024;

/// Строка в `shell.log` через `format!`-синтаксис.
macro_rules! shell_log {
    ($($arg:tt)*) => {
        $crate::logs::write(&format!($($arg)*))
    };
}
pub(crate) use shell_log;

pub fn logs_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("logs")
}

pub fn resident_log(data_dir: &Path) -> PathBuf {
    logs_dir(data_dir).join("resident.log")
}

pub fn shell_log_path(data_dir: &Path) -> PathBuf {
    logs_dir(data_dir).join("shell.log")
}

/// Вывод uv при установке движка (`engine.rs`).
pub fn engine_install_log(data_dir: &Path) -> PathBuf {
    logs_dir(data_dir).join("engine-install.log")
}

/// Журнал больше `limit` — переименовать в `<имя>.1` (прежний `.1` теряется).
/// `Ok(true)` — ротация была; нет файла — `Ok(false)`.
pub fn rotate(path: &Path, limit: u64) -> io::Result<bool> {
    let size = match fs::metadata(path) {
        Ok(meta) => meta.len(),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if size <= limit {
        return Ok(false);
    }
    let mut archive = path.as_os_str().to_owned();
    archive.push(".1");
    let archive = PathBuf::from(archive);
    match fs::remove_file(&archive) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    fs::rename(path, archive)?;
    Ok(true)
}

/// Открыть журнал на дописывание: папки создаются, большой файл сначала
/// уезжает в `.1`.
pub fn open_append(path: &Path) -> io::Result<File> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir)?;
    }
    rotate(path, ROTATE_AT)?;
    OpenOptions::new().create(true).append(true).open(path)
}

/// Строка журнала: время (UTC) и текст.
pub fn line(at: SystemTime, text: &str) -> String {
    let seconds = at
        .duration_since(UNIX_EPOCH)
        .map(|since| since.as_secs())
        .unwrap_or(0);
    format!("{} {text}\n", utc(seconds))
}

/// Сейчас (UTC) — «2026-09-21 14:13:20Z».
pub fn utc_now() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|since| since.as_secs())
        .unwrap_or(0);
    utc(seconds)
}

/// Секунды эпохи → «2026-09-21 14:13:20Z». Без крейта времени ради одной
/// строки: гражданская дата из числа дней (алгоритм Хиннанта).
fn utc(seconds: u64) -> String {
    let days = (seconds / 86_400) as i64;
    let rest = seconds % 86_400;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02}Z",
        rest / 3600,
        rest / 60 % 60,
        rest % 60
    )
}

/// Записать строку в `shell.log`; в отладочной сборке — ещё и в консоль.
/// Журнал не открылся — не повод падать: теряется только диагностика.
pub fn write(text: &str) {
    #[cfg(debug_assertions)]
    eprintln!("meet: {text}");
    let path = shell_log_path(&resident::data_dir());
    if let Ok(mut file) = open_append(&path) {
        let _ = file.write_all(line(SystemTime::now(), text).as_bytes());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(name: &str) -> Self {
            let dir =
                std::env::temp_dir().join(format!("meet-logs-test-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            TempDir(dir)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn logs_live_in_data_dir() {
        let data = Path::new(r"C:\Users\u\AppData\Local\meet");
        assert_eq!(logs_dir(data), data.join("logs"));
        assert_eq!(resident_log(data), data.join("logs").join("resident.log"));
        assert_eq!(shell_log_path(data), data.join("logs").join("shell.log"));
        assert_eq!(
            engine_install_log(data),
            data.join("logs").join("engine-install.log")
        );
    }

    #[test]
    fn big_log_moves_to_dot_one() {
        let dir = TempDir::new("rotate");
        let log = dir.0.join("resident.log");
        let archive = dir.0.join("resident.log.1");
        fs::write(&archive, b"old archive").unwrap();
        fs::write(&log, b"0123456789").unwrap();
        assert!(rotate(&log, 5).unwrap());
        assert!(!log.exists());
        assert_eq!(fs::read(&archive).unwrap(), b"0123456789");
    }

    #[test]
    fn small_or_missing_log_stays() {
        let dir = TempDir::new("keep");
        let log = dir.0.join("resident.log");
        assert!(!rotate(&log, 5).unwrap(), "файла нет — нечего вращать");
        fs::write(&log, b"12345").unwrap();
        assert!(!rotate(&log, 5).unwrap());
        assert_eq!(fs::read(&log).unwrap(), b"12345");
        assert!(!dir.0.join("resident.log.1").exists());
    }

    #[test]
    fn open_append_creates_folders_and_appends() {
        let dir = TempDir::new("append");
        let log = dir.0.join("logs").join("shell.log");
        open_append(&log).unwrap().write_all(b"a").unwrap();
        open_append(&log).unwrap().write_all(b"b").unwrap();
        assert_eq!(fs::read(&log).unwrap(), b"ab");
    }

    #[test]
    fn line_is_stamped_with_utc_time() {
        let at = UNIX_EPOCH + Duration::from_secs(1_790_000_000);
        assert_eq!(line(at, "запуск"), "2026-09-21 14:13:20Z запуск\n");
        assert_eq!(utc(951_782_400), "2000-02-29 00:00:00Z");
        assert_eq!(utc(0), "1970-01-01 00:00:00Z");
    }
}
