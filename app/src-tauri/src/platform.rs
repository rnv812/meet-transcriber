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

/// Основная версия macOS из вывода `sw_vers -productVersion` («14.4.1» → 14).
pub fn parse_macos_major(product_version: &str) -> Option<u32> {
    product_version.trim().split('.').next()?.parse().ok()
}

/// Основная версия macOS этой машины; не macOS или не прочиталась — `None`.
/// Нужна установке движка: MLX ставится только на macOS 14 и новее.
pub fn macos_major() -> Option<u32> {
    if current() != Os::MacOs {
        return None;
    }
    let output = Command::new("/usr/bin/sw_vers")
        .arg("-productVersion")
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_macos_major(&String::from_utf8_lossy(&output.stdout))
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
/// новые — в конец по порядку, без повторов и пустых. Из `extra` — только
/// абсолютные: оболочка, не раскрывшая `"$PATH"` по-POSIX-ски (nu, xonsh),
/// отдаёт буквальное `$PATH`, и папкой оно не станет. Второе — сколько папок
/// добавилось.
pub fn merged_path(current: &str, extra: &str) -> (String, usize) {
    let mut dirs: Vec<&str> = Vec::new();
    for dir in current.split(':').filter(|d| !d.is_empty()) {
        if !dirs.contains(&dir) {
            dirs.push(dir);
        }
    }
    let before = dirs.len();
    for dir in extra.split(':').filter(|d| d.starts_with('/')) {
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

/// Файл кэша PATH оболочки входа в папке данных.
#[cfg_attr(not(unix), allow(dead_code))]
const PATH_CACHE_FILE: &str = "login-path.txt";
/// В фоне ждём дольше: окна уже открыты, торопиться некуда.
#[cfg_attr(not(unix), allow(dead_code))]
const LOGIN_PATH_BACKGROUND_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(15);
/// Программы агентов: нашлась любая — интерактивная оболочка не нужна.
#[cfg_attr(not(unix), allow(dead_code))]
const AGENT_PROGRAMS: [&str; 2] = ["claude", "codex"];

/// Кэш PATH: первая строка файла. Нет файла, не UTF-8, пусто или нет ни
/// одной абсолютной папки — как будто кэша нет.
#[cfg_attr(not(unix), allow(dead_code))]
pub fn read_path_cache(file: &Path) -> Option<String> {
    let text = std::fs::read_to_string(file).ok()?;
    let line = text.lines().next()?.trim();
    line.split(':')
        .any(|dir| dir.starts_with('/'))
        .then(|| line.to_string())
}

/// Записать кэш PATH (создаёт папку данных, если её ещё нет).
#[cfg_attr(not(unix), allow(dead_code))]
pub fn write_path_cache(file: &Path, path: &str) -> std::io::Result<()> {
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(file, format!("{path}\n"))
}

/// Нужна ли интерактивная оболочка (`-i`): ни claude, ни codex не найдены ни
/// в одной папке PATH (nvm, conda и подобное настраивают `.zshrc`).
#[cfg_attr(not(unix), allow(dead_code))]
pub fn needs_interactive_refresh(path: &str, exists: impl Fn(&Path) -> bool) -> bool {
    !path.split(':').filter(|dir| !dir.is_empty()).any(|dir| {
        AGENT_PROGRAMS
            .iter()
            .any(|name| exists(&Path::new(dir).join(name)))
    })
}

/// Аргументы оболочки: `-lc` (профили входа), `-ilc` — ещё и `.zshrc`.
#[cfg_attr(not(unix), allow(dead_code))]
pub fn shell_args(interactive: bool, script: &str) -> Vec<String> {
    let mut args = Vec::new();
    if interactive {
        args.push("-i".to_string());
    }
    args.extend(["-l".to_string(), "-c".to_string(), script.to_string()]);
    args
}

/// Итог обновления PATH.
#[cfg_attr(not(unix), allow(dead_code))]
#[derive(Debug, PartialEq, Eq)]
pub struct RefreshOutcome {
    /// PATH оболочки входа — его и кладём в кэш.
    pub login: String,
    /// Понадобилась интерактивная оболочка и она ответила.
    pub interactive: bool,
}

/// Сначала `read(false)` (`-lc`); claude и codex не нашлись ни в `current`, ни
/// в ответе — `read(true)` (`-ilc`). Сбой интерактивной не отменяет ответ
/// `-lc`; сбой `-lc` — ошибка (кэш не трогаем).
#[cfg_attr(not(unix), allow(dead_code))]
pub fn refresh_login_path(
    current: &str,
    mut read: impl FnMut(bool) -> Result<String, String>,
    exists: impl Fn(&Path) -> bool,
) -> Result<RefreshOutcome, String> {
    let plain = read(false)?;
    let (merged, _) = merged_path(current, &plain);
    if !needs_interactive_refresh(&merged, &exists) {
        return Ok(RefreshOutcome {
            login: plain,
            interactive: false,
        });
    }
    Ok(match read(true) {
        Ok(login) => RefreshOutcome {
            login,
            interactive: true,
        },
        Err(_) => RefreshOutcome {
            login: plain,
            interactive: false,
        },
    })
}

/// macOS: дополнить PATH процесса PATH-ом оболочки входа. Приложение из
/// Finder, Dock или автозапуска получает PATH launchd
/// (`/usr/bin:/bin:/usr/sbin:/sbin`), и ни резидент, ни агент в терминале не
/// видят `~/.local/bin/claude`, `/opt/homebrew/bin/codex`, node от npm.
/// До создания окон оболочку не ждём: есть кэш в папке данных — берём PATH
/// из него, а обновляем в фоне; кэша нет (первый запуск) — один `-lc` без `-i`
/// (профили входа, без `.zshrc`, который будит запросы доступа к папкам).
/// `-ilc` — в фоне и только если claude и codex не нашлись. Вызывать в начале
/// `main`, до потоков: резидент, задания и агенты наследуют окружение. Не
/// вышло — только строка в журнале.
#[cfg(unix)]
pub fn adopt_login_path() {
    let shell = login_shell(std::env::var("SHELL").ok().as_deref());
    let cache_file = crate::resident::data_dir().join(PATH_CACHE_FILE);
    let current = std::env::var("PATH").unwrap_or_default();
    if let Some(cached) = read_path_cache(&cache_file) {
        apply_login_path(&current, &cached, "кэш");
        std::thread::spawn(move || {
            let result = refresh_login_path(
                &current,
                |interactive| read_login_path(&shell, interactive, LOGIN_PATH_BACKGROUND_TIMEOUT),
                Path::exists,
            );
            finish_background_refresh(&shell, &cache_file, Some(&cached), result);
        });
        return;
    }
    match read_login_path(&shell, false, LOGIN_PATH_TIMEOUT) {
        Ok(login) => {
            apply_login_path(&current, &login, "оболочка входа");
            if let Err(error) = write_path_cache(&cache_file, &login) {
                crate::logs::shell_log!("кэш PATH не записан: {error}");
            }
            let (merged, _) = merged_path(&current, &login);
            if needs_interactive_refresh(&merged, Path::exists) {
                std::thread::spawn(move || {
                    let result =
                        read_login_path(&shell, true, LOGIN_PATH_BACKGROUND_TIMEOUT).map(|login| {
                            RefreshOutcome {
                                login,
                                interactive: true,
                            }
                        });
                    finish_background_refresh(&shell, &cache_file, None, result);
                });
            }
        }
        Err(error) => {
            crate::logs::shell_log!("PATH оболочки входа ({shell}) не получен: {error}");
        }
    }
}

/// Дописать папки `login` в PATH процесса.
#[cfg(unix)]
fn apply_login_path(current: &str, login: &str, source: &str) {
    let (path, added) = merged_path(current, login);
    if added > 0 {
        std::env::set_var("PATH", &path);
    }
    crate::logs::shell_log!("PATH ({source}): добавлено папок {added}");
}

/// Конец фонового обновления: новый PATH — в кэш и в окружение процесса
/// (дети, запущенные дальше, увидят его), сбой — строка в журнале.
#[cfg(unix)]
fn finish_background_refresh(
    shell: &str,
    cache_file: &Path,
    cached: Option<&str>,
    result: Result<RefreshOutcome, String>,
) {
    match result {
        Ok(outcome) => {
            if cached != Some(outcome.login.as_str()) {
                if let Err(error) = write_path_cache(cache_file, &outcome.login) {
                    crate::logs::shell_log!("кэш PATH не записан: {error}");
                }
                let current = std::env::var("PATH").unwrap_or_default();
                apply_login_path(&current, &outcome.login, "обновление в фоне");
            }
            crate::logs::shell_log!(
                "PATH обновлён в фоне ({shell}), интерактивная оболочка: {}",
                outcome.interactive
            );
        }
        Err(error) => {
            crate::logs::shell_log!("PATH оболочки входа ({shell}) не обновлён: {error}");
        }
    }
}

/// Запустить оболочку входа и прочитать PATH между метками. Вывод читает
/// отдельный поток и отдаёт его, как только пришла вторая метка: демон из
/// профиля (ssh-agent и т. п.) может держать вывод открытым и после выхода
/// оболочки. Не уложилась в `timeout` — гасим её группу.
#[cfg(unix)]
fn read_login_path(
    shell: &str,
    interactive: bool,
    timeout: std::time::Duration,
) -> Result<String, String> {
    use std::io::Read;
    use std::process::Stdio;
    use std::sync::mpsc;

    let script = format!("printf '%s%s%s' '{LOGIN_PATH_BEGIN}' \"$PATH\" '{LOGIN_PATH_END}'");
    let mut command = Command::new(shell);
    command
        .args(shell_args(interactive, &script))
        // Как у VS Code: тяжёлый профиль может пропустить лишнее для нас.
        .env("MEET_RESOLVING_ENVIRONMENT", "1")
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
    let Ok(output) = receive.recv_timeout(timeout) else {
        // Зависла (ждёт ввода, `exec tmux` и т. п.) — гасим всю её группу.
        kill_group(child.id(), true);
        let _ = child.wait();
        return Err(format!(
            "оболочка не ответила за {} с",
            timeout.as_secs_f32()
        ));
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
        // nu/xonsh не раскрыли "$PATH", относительное — тоже не папка PATH.
        assert_eq!(merged_path("/a", "$PATH:bin:/b"), ("/a:/b".to_string(), 1));
    }

    fn temp_dir(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("meet-path-cache-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn path_cache_roundtrips_and_ignores_garbage() {
        let dir = temp_dir("roundtrip");
        let file = dir.join(PATH_CACHE_FILE);
        assert_eq!(read_path_cache(&file), None, "файла нет");
        write_path_cache(&file, "/opt/homebrew/bin:/usr/bin").unwrap();
        assert_eq!(
            read_path_cache(&file).as_deref(),
            Some("/opt/homebrew/bin:/usr/bin")
        );
        // Пустой, без абсолютных папок или повреждённый кэш — как отсутствие.
        std::fs::write(
            &file, "  
",
        )
        .unwrap();
        assert_eq!(read_path_cache(&file), None);
        std::fs::write(&file, "$PATH:bin").unwrap();
        assert_eq!(read_path_cache(&file), None);
        std::fs::write(&file, [0xff, 0xfe, 0x00]).unwrap();
        assert_eq!(read_path_cache(&file), None);
        // Запись создаёт недостающую папку данных.
        let nested = dir.join("a").join(PATH_CACHE_FILE);
        write_path_cache(&nested, "/x").unwrap();
        assert_eq!(read_path_cache(&nested).as_deref(), Some("/x"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn interactive_refresh_only_when_agents_are_missing() {
        let has =
            |dirs: &'static [&'static str]| move |p: &Path| dirs.iter().any(|d| p.starts_with(d));
        // claude нашёлся — интерактивная оболочка не нужна.
        assert!(!needs_interactive_refresh(
            "/usr/bin:/Users/u/.local/bin",
            has(&["/Users/u/.local/bin"])
        ));
        // codex нашёлся — тоже.
        assert!(!needs_interactive_refresh(
            "/opt/homebrew/bin:/usr/bin",
            has(&["/opt/homebrew/bin"])
        ));
        // Ни того ни другого — нужна (nvm и подобное живут в .zshrc).
        assert!(needs_interactive_refresh("/usr/bin:/bin", has(&[])));
        assert!(needs_interactive_refresh("", has(&["/usr/bin"])));
    }

    #[test]
    fn refresh_tries_plain_login_shell_first() {
        let mut calls: Vec<bool> = Vec::new();
        let exists = |p: &Path| p.starts_with("/Users/u/.local/bin");
        let outcome = refresh_login_path(
            "/usr/bin:/bin",
            |interactive| {
                calls.push(interactive);
                Ok("/Users/u/.local/bin:/usr/bin".to_string())
            },
            exists,
        )
        .unwrap();
        assert_eq!(calls, vec![false], "-ilc не нужен: claude нашёлся");
        assert_eq!(outcome.login, "/Users/u/.local/bin:/usr/bin");
        assert!(!outcome.interactive);
    }

    #[test]
    fn refresh_falls_back_to_interactive_shell_and_keeps_its_path() {
        let mut calls: Vec<bool> = Vec::new();
        let exists = |p: &Path| p.starts_with("/Users/u/.nvm/bin");
        let outcome = refresh_login_path(
            "/usr/bin:/bin",
            |interactive| {
                calls.push(interactive);
                Ok(if interactive {
                    "/Users/u/.nvm/bin:/usr/bin".to_string()
                } else {
                    "/usr/bin".to_string()
                })
            },
            exists,
        )
        .unwrap();
        assert_eq!(calls, vec![false, true]);
        assert_eq!(outcome.login, "/Users/u/.nvm/bin:/usr/bin");
        assert!(outcome.interactive);
    }

    #[test]
    fn refresh_survives_a_failed_interactive_shell() {
        let outcome = refresh_login_path(
            "/usr/bin",
            |interactive| {
                if interactive {
                    Err("таймаут".to_string())
                } else {
                    Ok("/opt/x/bin".to_string())
                }
            },
            |_| false,
        )
        .unwrap();
        assert_eq!(outcome.login, "/opt/x/bin", "берём то, что дал -lc");
        // Если и -lc не ответила, это ошибка: кэш не трогаем.
        assert!(refresh_login_path("/usr/bin", |_| Err("нет".into()), |_| false).is_err());
    }

    #[test]
    fn shell_arguments_drop_i_unless_asked() {
        let script = "echo";
        assert_eq!(shell_args(false, script), ["-l", "-c", script]);
        assert_eq!(shell_args(true, script), ["-i", "-l", "-c", script]);
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
        let path = read_login_path("/bin/sh", false, LOGIN_PATH_TIMEOUT).unwrap();
        assert!(!path.is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn hung_login_shell_is_killed_at_the_timeout() {
        use std::os::unix::fs::PermissionsExt;
        // «Оболочка», которая молчит полминуты (профиль с `exec tmux` и т. п.).
        let dir = std::env::temp_dir().join(format!("meet-login-shell-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let shell = dir.join("hangs.sh");
        std::fs::write(&shell, "#!/bin/sh\nsleep 30\n").unwrap();
        std::fs::set_permissions(&shell, std::fs::Permissions::from_mode(0o755)).unwrap();
        let started = std::time::Instant::now();
        let result = read_login_path(
            shell.to_str().unwrap(),
            false,
            std::time::Duration::from_millis(300),
        );
        let took = started.elapsed();
        let _ = std::fs::remove_dir_all(&dir);
        assert!(result.unwrap_err().contains("не ответила"));
        // wait() после kill вернулся сразу: оболочка убита, а не дождалась sleep.
        assert!(took < std::time::Duration::from_secs(5), "{took:?}");
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

    #[test]
    fn macos_major_is_read_from_the_product_version() {
        assert_eq!(parse_macos_major("14.4.1\n"), Some(14));
        assert_eq!(parse_macos_major("13.6"), Some(13));
        assert_eq!(parse_macos_major("26.0"), Some(26));
        assert_eq!(parse_macos_major(""), None);
        assert_eq!(parse_macos_major("нет"), None);
    }

    #[cfg(unix)]
    #[test]
    fn own_process_is_alive_and_free_space_is_known() {
        assert!(pid_alive(std::process::id()));
        assert!(!pid_alive(0));
        assert!(free_gb(&std::env::temp_dir()).is_some_and(|gb| gb > 0.0));
    }
}
