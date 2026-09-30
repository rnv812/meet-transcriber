// Резидент (Python, `meet-tray --headless`) как дочерний процесс оболочки.
//
// Запись, автозапись и расшифровка живут в резиденте; оболочка его находит,
// запускает без окна консоли, перезапускает при падении и штатно гасит на
// «Выход». Если резидент уже работает сам по себе (авторский `meet-tray --watch`
// из автозагрузки), второй не поднимается: оболочка подключается к нему.
//
// Всё, что проверяется без GUI и процессов, — чистые функции внизу с тестами:
// порядок кандидатов, аргументы, решение после выхода процесса.

use std::ffi::OsStr;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Manager};

use crate::api;
use crate::tray::{self, Notice};

const EXE: &str = "meet-tray.exe";
/// Явный путь к резиденту — первым кандидатом. Для разработки из рабочей
/// копии без своего `.venv` (venv живёт в основном клоне) и для отладки.
const OVERRIDE_ENV: &str = "MEET_RESIDENT";
/// `tray.EXIT_ALREADY_RUNNING`: tray.lock занят другим резидентом.
const EXIT_ALREADY_RUNNING: i32 = 3;
/// Перезапусков подряд, после которых сдаёмся.
const MAX_RESTARTS: u32 = 3;
/// Проработал столько — прошлые падения прощаются.
const STABLE_AFTER: Duration = Duration::from_secs(60);
const RESTART_PAUSE: Duration = Duration::from_secs(2);
/// Сколько ждём публикации `daemon.json` до предупреждения в консоль.
const PUBLISH_TIMEOUT: Duration = Duration::from_secs(10);
const POLL: Duration = Duration::from_millis(250);
/// После ответа `/shutdown` резиденту нужно погасить сервер и снять lock.
const EXIT_GRACE: Duration = Duration::from_secs(10);
/// Как часто проверяем, жив ли чужой резидент, и сколько промахов подряд
/// значат, что его больше нет.
const EXTERNAL_POLL: Duration = Duration::from_secs(3);
const EXTERNAL_MISSES: u32 = 2;

/// Адрес и токен резидента из `daemon.json`. Debug нет нарочно — токен не
/// должен попасть в лог даже случайным `{:?}`.
#[derive(Serialize, Clone)]
pub struct Endpoint {
    pub port: u16,
    pub token: String,
}

/// Та же папка, что `paths.data_dir()` в Python: `MEET_DATA_DIR` (портативный
/// режим, тесты) или `%LOCALAPPDATA%\meet`.
pub fn data_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("MEET_DATA_DIR") {
        let dir = dir.to_string_lossy();
        if !dir.trim().is_empty() {
            return PathBuf::from(dir.trim());
        }
    }
    let base = std::env::var_os("LOCALAPPDATA").unwrap_or_else(|| ".".into());
    PathBuf::from(base).join("meet")
}

pub fn read_endpoint() -> Option<Endpoint> {
    let raw = std::fs::read_to_string(data_dir().join("daemon.json")).ok()?;
    let value: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let port = u16::try_from(value.get("port")?.as_u64()?).ok()?;
    let token = value.get("token")?.as_str()?.to_string();
    Some(Endpoint { port, token })
}

fn answers(endpoint: &Endpoint) -> bool {
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], endpoint.port));
    TcpStream::connect_timeout(&address, Duration::from_millis(400)).is_ok()
}

/// Отвечает ли опубликованный резидент на самом деле.
///
/// IMPORTANT: одного файла мало. Упавший процесс оставляет `daemon.json` на
/// диске, и доверие файлу превращало запуск резидента в пустышку: публикация
/// есть — значит «уже работает», и ничего не запускалось. Проверяем
/// соединением и убираем протухшую публикацию — та же болезнь, что у
/// lock-файлов записи.
fn endpoint_answers() -> bool {
    let Some(endpoint) = read_endpoint() else {
        return false;
    };
    let alive = answers(&endpoint);
    if !alive {
        let _ = std::fs::remove_file(data_dir().join("daemon.json"));
    }
    alive
}

/// Где искать резидента, по порядку: `MEET_RESIDENT`, рядом с оболочкой
/// (`resident\meet-tray.exe` установленного приложения), `.venv` репозитория
/// (dev: оболочка в `app\src-tauri\target\debug`), затем PATH.
pub fn candidates(exe_dir: &Path, env_override: Option<&OsStr>) -> Vec<PathBuf> {
    let mut list = Vec::new();
    if let Some(chosen) = env_override {
        let chosen = chosen.to_string_lossy();
        if !chosen.trim().is_empty() {
            list.push(PathBuf::from(chosen.trim()));
        }
    }
    list.push(exe_dir.join("resident").join(EXE));
    // target\debug → target → src-tauri → app → корень репозитория
    if let Some(repo) = exe_dir.ancestors().nth(4) {
        list.push(repo.join(".venv").join("Scripts").join(EXE));
    }
    list.push(PathBuf::from(EXE));
    list
}

/// Резидент без своей иконки, живущий и умирающий вместе с оболочкой.
pub fn args(parent_pid: u32) -> Vec<String> {
    vec![
        "--headless".into(),
        "--parent-pid".into(),
        parent_pid.to_string(),
    ]
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Restart,
    /// Резидент уже работает сам по себе — подключаемся к нему.
    AdoptExternal,
    GiveUp,
}

/// Что делать, когда резидент вышел. Код 3 — не сбой, а «дежурный уже есть»:
/// перезапускать бессмысленно, иначе оболочка крутила бы перезапуски впустую.
pub fn next_action(exit_code: Option<i32>, restarts_in_window: u32) -> Action {
    if exit_code == Some(EXIT_ALREADY_RUNNING) {
        Action::AdoptExternal
    } else if restarts_in_window < MAX_RESTARTS {
        Action::Restart
    } else {
        Action::GiveUp
    }
}

/// Счётчик перезапусков после выхода процесса, прожившего `lived`: минута
/// стабильной работы прощает прошлые падения.
pub fn restarts_after_exit(restarts: u32, lived: Duration) -> u32 {
    if lived >= STABLE_AFTER {
        0
    } else {
        restarts
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExternalStep {
    /// Чужой резидент на месте — продолжаем им пользоваться.
    Stay,
    /// Чужой резидент пропал — запускаем свой.
    Takeover,
}

/// Очередная проверка чужого резидента (`answers`) при `misses` промахах
/// подряд до неё → шаг и новый счётчик промахов. Один промах — не повод:
/// резидент мог на миг не принять соединение; два подряд — его нет. Так
/// оболочка, запущенная, пока осиротевший прежний резидент ещё дописывал
/// запись, не остаётся без резидента, когда тот выйдет.
pub fn external_next(answers: bool, misses: u32) -> (ExternalStep, u32) {
    if answers {
        return (ExternalStep::Stay, 0);
    }
    let misses = misses + 1;
    if misses >= EXTERNAL_MISSES {
        (ExternalStep::Takeover, 0)
    } else {
        (ExternalStep::Stay, misses)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResidentStatus {
    Starting,
    Running,
    /// Резидент запущен вне приложения (`--watch` из автозагрузки).
    External,
    /// Перезапуски исчерпаны; `log` — журнал, который стоит открыть.
    Failed {
        log: PathBuf,
    },
}

/// Надзор за резидентом. Живёт в состоянии приложения:
/// `app.state::<Supervisor>()`.
#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<Inner>,
}

struct Inner {
    status: Mutex<ResidentStatus>,
    /// Свой дочерний процесс; `None` — не запущен, вышел или резидент чужой.
    child: Mutex<Option<Child>>,
    /// «Выходим»: надзор больше не перезапускает и не запускает.
    quitting: AtomicBool,
    /// Второй «Выход» ждёт первый, а не выходит раньше, чем резидент сохранил
    /// запись.
    shutdown_gate: Mutex<()>,
}

impl Supervisor {
    /// Поднять надзор в фоновом потоке и положить его в состояние приложения.
    pub fn start(app: &AppHandle) {
        let supervisor = Supervisor {
            inner: Arc::new(Inner {
                status: Mutex::new(ResidentStatus::Starting),
                child: Mutex::new(None),
                quitting: AtomicBool::new(false),
                shutdown_gate: Mutex::new(()),
            }),
        };
        app.manage(supervisor.clone());
        let app = app.clone();
        let spawned = thread::Builder::new()
            .name("meet-resident".into())
            .spawn(move || supervisor.supervise(&app));
        if let Err(error) = spawned {
            eprintln!("meet: поток надзора за резидентом не запустился: {error}");
        }
    }

    pub fn status(&self) -> ResidentStatus {
        lock(&self.inner.status).clone()
    }

    fn set_status(&self, status: ResidentStatus) {
        *lock(&self.inner.status) = status;
    }

    fn quitting(&self) -> bool {
        self.inner.quitting.load(Ordering::SeqCst)
    }

    fn supervise(&self, app: &AppHandle) {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|exe| exe.parent().map(Path::to_path_buf))
            .unwrap_or_default();
        let list = candidates(&exe_dir, std::env::var_os(OVERRIDE_ENV).as_deref());
        let mut restarts = 0;
        let mut external = endpoint_answers();
        loop {
            if self.quitting() {
                return;
            }
            if external {
                // Чужой резидент не вечен: это может быть осиротевший резидент
                // прошлой оболочки, который дописывает запись и сейчас выйдет.
                eprintln!("meet: резидент уже работает вне приложения — подключаюсь к нему");
                self.set_status(ResidentStatus::External);
                self.watch_external();
                if self.quitting() {
                    return;
                }
                eprintln!("meet: внешний резидент больше не отвечает — запускаю свой");
                external = false;
            }
            self.set_status(ResidentStatus::Starting);
            let Some(pid) = self.spawn(&list) else {
                if !self.quitting() {
                    eprintln!("meet: резидент не найден ни по одному пути");
                    self.give_up(app);
                }
                return;
            };
            let started = Instant::now();
            let code = self.wait(pid);
            if self.quitting() {
                return;
            }
            restarts = restarts_after_exit(restarts, started.elapsed());
            match next_action(code, restarts) {
                Action::Restart => {
                    restarts += 1;
                    eprintln!(
                        "meet: резидент вышел (код {code:?}), перезапуск {restarts} из {MAX_RESTARTS}"
                    );
                    thread::sleep(RESTART_PAUSE);
                }
                // Код 3 — не падение: счётчик перезапусков не растёт, дальше
                // следим за тем, кто держит tray.lock.
                Action::AdoptExternal => external = true,
                Action::GiveUp => {
                    eprintln!("meet: резидент падает раз за разом (код {code:?}) — сдаюсь");
                    self.give_up(app);
                    return;
                }
            }
        }
    }

    /// Ждать, пока чужой резидент не перестанет отвечать (или пока не выходим).
    ///
    /// Проверка без удаления `daemon.json`: файл принадлежит живому чужому
    /// резиденту, и единичный промах не должен стирать его публикацию.
    fn watch_external(&self) {
        let mut misses = 0;
        loop {
            thread::sleep(EXTERNAL_POLL);
            if self.quitting() {
                return;
            }
            let answers = read_endpoint().is_some_and(|endpoint| answers(&endpoint));
            let (step, next) = external_next(answers, misses);
            misses = next;
            if step == ExternalStep::Takeover {
                return;
            }
        }
    }

    /// Запустить первого нашедшегося кандидата; pid или `None`.
    fn spawn(&self, list: &[PathBuf]) -> Option<u32> {
        let args = args(std::process::id());
        for candidate in list {
            let mut command = Command::new(candidate);
            command.args(&args).stdin(Stdio::null());
            hide_console(&mut command);
            match command.spawn() {
                Ok(mut child) => {
                    let pid = child.id();
                    let mut slot = lock(&self.inner.child);
                    // shutdown() ставит флаг до того, как берёт этот замок:
                    // либо он увидит процесс здесь, либо мы увидим флаг.
                    if self.quitting() {
                        drop(slot);
                        kill_tree(pid);
                        let _ = child.wait();
                        return None;
                    }
                    *slot = Some(child);
                    eprintln!(
                        "meet: резидент запущен: {} (pid {pid})",
                        candidate.display()
                    );
                    return Some(pid);
                }
                Err(error) => eprintln!("meet: {}: {error}", candidate.display()),
            }
        }
        None
    }

    /// Ждать выхода своего процесса; попутно отметить публикацию API.
    ///
    /// pid из `daemon.json` с `pid` процесса не сравниваем: `meet-tray.exe` из
    /// venv — лаунчер, а сам Python — его дочерний процесс с другим pid.
    /// Протухшая публикация уже убрана `endpoint_answers()`, так что
    /// отвечающий адрес после запуска — наш.
    fn wait(&self, pid: u32) -> Option<i32> {
        let started = Instant::now();
        let mut warned = false;
        loop {
            {
                let mut slot = lock(&self.inner.child);
                let Some(child) = slot.as_mut() else {
                    return None; // процесс забрал shutdown()
                };
                match child.try_wait() {
                    Ok(Some(status)) => {
                        *slot = None;
                        return status.code();
                    }
                    Ok(None) => {}
                    Err(error) => {
                        eprintln!("meet: не удалось проверить резидента (pid {pid}): {error}");
                        *slot = None;
                        return None;
                    }
                }
            }
            if self.status() == ResidentStatus::Starting {
                if read_endpoint().is_some_and(|endpoint| answers(&endpoint)) {
                    self.set_status(ResidentStatus::Running);
                } else if !warned && started.elapsed() >= PUBLISH_TIMEOUT {
                    warned = true;
                    eprintln!(
                        "meet: резидент (pid {pid}) за {} с не опубликовал daemon.json",
                        PUBLISH_TIMEOUT.as_secs()
                    );
                }
            }
            thread::sleep(POLL);
        }
    }

    /// Сдаться: статус `Failed` и ровно одно уведомление — после него надзор
    /// заканчивается, повторить его некому. Уведомление идёт через трей: оно
    /// подчиняется `ui.notifications` (при «off» молчит).
    fn give_up(&self, app: &AppHandle) {
        self.set_status(ResidentStatus::Failed {
            log: data_dir().join("watch.log"),
        });
        tray::notify(
            app,
            vec![Notice {
                title: tray::RESIDENT_FAILED.into(),
                body: "Откройте журнал из меню трея".into(),
                recording: None,
            }],
        );
    }

    /// Штатно погасить свой резидент: `POST /shutdown` (идущая запись
    /// сохраняется, ответ — до 70 с), затем, если процесс ещё жив, — убить
    /// дерево. Чужой резидент (`External`) не трогаем. Блокирует: вызывать не
    /// из главного потока.
    pub fn shutdown(&self) {
        let _gate = lock(&self.inner.shutdown_gate);
        if self.inner.quitting.swap(true, Ordering::SeqCst) {
            return; // уже погашен предыдущим вызовом
        }
        let Some(pid) = self.child_pid() else {
            // Своего процесса нет. External-резидент принадлежит пользователю
            // (например, `--watch` из автозагрузки) — «Выход» его не гасит.
            return;
        };
        match read_endpoint() {
            Some(endpoint) => {
                if let Err(error) =
                    api::Client::new(&endpoint).post("/shutdown", serde_json::Value::Null)
                {
                    eprintln!("meet: /shutdown не прошёл: {error}");
                }
            }
            None => eprintln!("meet: резидент не опубликовал адрес — /shutdown некуда слать"),
        }
        let deadline = Instant::now() + EXIT_GRACE;
        while Instant::now() < deadline {
            if self.child_exited() {
                return;
            }
            thread::sleep(POLL);
        }
        eprintln!("meet: резидент (pid {pid}) не вышел сам — завершаю дерево процессов");
        kill_tree(pid);
        if let Some(mut child) = lock(&self.inner.child).take() {
            let _ = child.wait();
        }
    }

    fn child_pid(&self) -> Option<u32> {
        lock(&self.inner.child).as_ref().map(Child::id)
    }

    fn child_exited(&self) -> bool {
        let mut slot = lock(&self.inner.child);
        match slot.as_mut().map(Child::try_wait) {
            None => true,
            Some(Ok(Some(_))) => {
                *slot = None;
                true
            }
            Some(_) => false,
        }
    }
}

/// Замок, переживающий панику другого потока: состояние здесь простое, и
/// оставить оболочку без надзора из-за отравленного мьютекса хуже.
pub(crate) fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Без окна консоли: резидент и taskkill — консольные программы.
fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    let _ = command;
}

/// Убить процесс с потомками: лаунчер venv держит под собой сам Python.
fn kill_tree(pid: u32) {
    let mut command = Command::new("taskkill");
    command
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    hide_console(&mut command);
    if let Err(error) = command.status() {
        eprintln!("meet: taskkill не запустился: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;
    use std::path::Path;

    #[test]
    fn candidates_prefer_bundled_then_dev_venv_then_path() {
        let exe_dir = Path::new(r"C:\repo\app\src-tauri\target\debug");
        let list = candidates(exe_dir, None);
        assert_eq!(list[0], exe_dir.join("resident").join("meet-tray.exe"));
        assert_eq!(list[1], Path::new(r"C:\repo\.venv\Scripts\meet-tray.exe"));
        assert_eq!(list.last().unwrap(), Path::new("meet-tray.exe"));
    }

    #[test]
    fn meet_resident_env_goes_before_everything() {
        let exe_dir = Path::new(r"C:\repo\app\src-tauri\target\debug");
        let chosen = r"D:\other\.venv\Scripts\meet-tray.exe";
        let list = candidates(exe_dir, Some(OsStr::new(chosen)));
        assert_eq!(list[0], Path::new(chosen));
        assert_eq!(list[1], exe_dir.join("resident").join("meet-tray.exe"));
        assert_eq!(list.last().unwrap(), Path::new("meet-tray.exe"));
    }

    #[test]
    fn blank_meet_resident_env_is_ignored() {
        let exe_dir = Path::new(r"C:\repo\app\src-tauri\target\debug");
        let list = candidates(exe_dir, Some(OsStr::new("  ")));
        assert_eq!(list[0], exe_dir.join("resident").join("meet-tray.exe"));
    }

    #[test]
    fn args_are_headless_with_parent() {
        assert_eq!(args(4242), vec!["--headless", "--parent-pid", "4242"]);
    }

    #[test]
    fn exit_code_3_means_someone_else_runs_the_resident() {
        assert_eq!(next_action(Some(3), 0), Action::AdoptExternal);
    }

    #[test]
    fn crashes_restart_three_times_then_give_up_once() {
        assert_eq!(next_action(Some(1), 0), Action::Restart);
        assert_eq!(next_action(None, 2), Action::Restart);
        assert_eq!(next_action(Some(1), 3), Action::GiveUp);
    }

    #[test]
    fn four_quick_crashes_give_three_restarts_and_one_give_up() {
        let mut restarts = 0;
        let mut actions = Vec::new();
        for _ in 0..4 {
            restarts = restarts_after_exit(restarts, Duration::from_secs(5));
            let action = next_action(Some(1), restarts);
            if action == Action::Restart {
                restarts += 1;
            }
            actions.push(action);
        }
        assert_eq!(
            actions,
            vec![
                Action::Restart,
                Action::Restart,
                Action::Restart,
                Action::GiveUp
            ]
        );
    }

    #[test]
    fn a_minute_of_stable_work_forgives_earlier_crashes() {
        assert_eq!(restarts_after_exit(3, Duration::from_secs(60)), 0);
        assert_eq!(restarts_after_exit(3, Duration::from_secs(59)), 3);
    }

    #[test]
    fn answering_external_resident_is_kept_and_misses_reset() {
        assert_eq!(external_next(true, 0), (ExternalStep::Stay, 0));
        assert_eq!(external_next(true, 1), (ExternalStep::Stay, 0));
    }

    #[test]
    fn one_miss_is_not_enough_to_take_over() {
        assert_eq!(external_next(false, 0), (ExternalStep::Stay, 1));
    }

    #[test]
    fn two_misses_in_a_row_take_over() {
        let (step, misses) = external_next(false, 0);
        assert_eq!(step, ExternalStep::Stay);
        assert_eq!(external_next(false, misses).0, ExternalStep::Takeover);
    }
}
