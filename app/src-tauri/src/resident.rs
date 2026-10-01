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
use std::io::Write;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use serde::Serialize;
use tauri::{AppHandle, Manager};

use crate::api;
use crate::engine;
use crate::logs::{self, shell_log};
use crate::tray::{self, Notice};
use crate::upgrade;

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
/// Как часто проверяем старый резидент без API (жив ли держатель tray.lock).
const NO_API_POLL: Duration = Duration::from_secs(10);

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

/// Где искать резидента, по порядку: `MEET_RESIDENT`, движок, поставленный
/// оболочкой (`<data_dir>\engine\<версия>\Scripts\meet-tray.exe` — только с
/// маркером `installed.json` этой версии: недостроенное или чужой версии
/// окружение не годится), `.venv` репозитория (только `dev` — отладочная
/// сборка в `app\src-tauri\target\debug`), затем PATH. Релиз в `.venv` не
/// заглядывает: четырьмя уровнями выше установленного exe может оказаться
/// что угодно.
///
/// Резидента рядом с оболочкой (`resident\meet-tray.exe`) больше нет:
/// установщик несёт uv и колесо, а не готовый резидент.
pub fn candidates(
    exe_dir: &Path,
    env_override: Option<&OsStr>,
    data_dir: &Path,
    version: &str,
    dev: bool,
) -> Vec<PathBuf> {
    let mut list = Vec::new();
    if let Some(chosen) = env_override {
        let chosen = chosen.to_string_lossy();
        if !chosen.trim().is_empty() {
            list.push(PathBuf::from(chosen.trim()));
        }
    }
    let engine = engine::env_dir(data_dir, version);
    if engine::is_installed(&engine, version) {
        list.push(engine::launcher(&engine));
    }
    // target\debug → target → src-tauri → app → корень репозитория
    if let Some(repo) = exe_dir.ancestors().nth(4).filter(|_| dev) {
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

/// Программа и аргументы запуска кандидата.
///
/// IMPORTANT: gui-script из venv (`Scripts\meet-tray.exe`) не запускаем
/// напрямую. Это GUI-лаунчер: CREATE_NO_WINDOW на него не действует, а сам он
/// запускает `Scripts\pythonw.exe`, который в venv от uv — консольный
/// трамплин. Консоли унаследовать неоткуда, и Windows открывает новую — на
/// Windows 11 это вкладка Windows Terminal с заголовком `meet-tray.exe`.
/// Консольный `Scripts\python.exe` получает скрытую консоль от
/// CREATE_NO_WINDOW, и настоящий интерпретатор наследует её, окна нет.
pub fn launch(candidate: &Path, parent_pid: u32) -> (PathBuf, Vec<String>) {
    let venv_python = candidate
        .parent()
        .filter(|scripts| {
            scripts
                .parent()
                .is_some_and(|venv| venv.join("pyvenv.cfg").is_file())
        })
        .map(|scripts| scripts.join("python.exe"))
        .filter(|python| python.is_file());
    match venv_python {
        Some(python) => {
            let mut arguments = vec![
                "-c".to_string(),
                "import sys; from meet.tray import main; sys.exit(main())".to_string(),
            ];
            arguments.extend(args(parent_pid));
            (python, arguments)
        }
        None => (candidate.to_path_buf(), args(parent_pid)),
    }
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

/// Что делает надзор на очередном круге.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Запустить свой резидент.
    Spawn,
    /// Чужой резидент с API — пользуемся им, пока отвечает.
    External,
    /// Чужой резидент без API держит tray.lock — ждём, пока он жив.
    ExternalNoApi,
}

/// Резидент вышел с кодом 3 (tray.lock занят): кто его держит? Отвечает API
/// — обычный чужой резидент; нет — старая версия (upstream `meet-tray
/// --watch` пишет tray.lock, но не daemon.json).
pub fn adopt_mode(api_answers: bool) -> Mode {
    if api_answers {
        Mode::External
    } else {
        Mode::ExternalNoApi
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoApiStep {
    /// Держатель tray.lock жив — свой резидент не запускаем (он вышел бы с
    /// кодом 3, и так по кругу каждые несколько секунд).
    Stay,
    /// API ответил — это всё-таки резидент с API.
    Adopt,
    /// Держателя больше нет — запускаем свой.
    Takeover,
}

/// Очередная проверка старого резидента без API.
pub fn no_api_next(api_answers: bool, holder_alive: bool) -> NoApiStep {
    if api_answers {
        NoApiStep::Adopt
    } else if holder_alive {
        NoApiStep::Stay
    } else {
        NoApiStep::Takeover
    }
}

/// pid держателя из содержимого tray.lock (`{"pid": N, ...}`).
pub fn lock_holder(raw: &str) -> Option<u32> {
    let value: serde_json::Value = serde_json::from_str(raw).ok()?;
    u32::try_from(value.get("pid")?.as_u64()?).ok()
}

fn tray_lock_holder() -> Option<u32> {
    lock_holder(&std::fs::read_to_string(data_dir().join("tray.lock")).ok()?)
}

/// Жив ли процесс. Нет доступа к чужому процессу — значит, он есть.
#[cfg(windows)]
pub fn pid_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, ERROR_ACCESS_DENIED, STILL_ACTIVE,
    };
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    if pid == 0 {
        return false;
    }
    // SAFETY: хэндл проверяется на null и закрывается ровно один раз;
    // GetExitCodeProcess пишет в локальную переменную.
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return GetLastError() == ERROR_ACCESS_DENIED;
        }
        let mut code = 0u32;
        let ok = GetExitCodeProcess(handle, &mut code);
        CloseHandle(handle);
        ok != 0 && code == STILL_ACTIVE as u32
    }
}

#[cfg(not(windows))]
pub fn pid_alive(pid: u32) -> bool {
    pid != 0 && Path::new(&format!("/proc/{pid}")).exists()
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResidentStatus {
    Starting,
    Running,
    /// Резидент запущен вне приложения (`--watch` из автозагрузки).
    External,
    /// tray.lock держит резидент без API (старая версия `meet-tray --watch`):
    /// записью он управляет сам, меню оболочки ему не указ.
    ExternalNoApi,
    /// Перезапуски исчерпаны; `log` — журнал, который стоит открыть.
    Failed {
        log: PathBuf,
    },
    /// Релиз без движка своей версии (первый запуск, обновление): резидента
    /// ещё нет, его поставит мастер в окне. Не сбой — без уведомления.
    EngineMissing,
    /// Оболочка обслуживает движок в фоне (`engine::Upkeep`), резидент ждёт
    /// конца: `step` из `of` — идущий шаг установки (0 — ещё не начат).
    EngineUpdating {
        step: usize,
        of: usize,
    },
    /// «Выход»: резидент сохраняет запись и гасится, затем выйдет оболочка.
    Quitting,
}

/// Что делать, когда ни один кандидат не запустился.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NoCandidate {
    /// Ждать установки движка молча (`EngineMissing`).
    EngineMissing,
    /// Сдаться: `Failed` и уведомление.
    GiveUp,
}

/// Релиз без установленного движка текущей версии — резидента и не должно
/// быть, пока мастер его не поставит. В dev резидент берётся из `.venv`, и
/// его отсутствие — по-прежнему сбой.
pub fn no_candidate(dev: bool, engine_installed: bool) -> NoCandidate {
    if !dev && !engine_installed {
        NoCandidate::EngineMissing
    } else {
        NoCandidate::GiveUp
    }
}

/// «Перезапустить сервис»: только из `Failed` (надзор уже закончился).
/// Статус сразу `Starting` — второй клик до старта нового надзора ничего не
/// делает. `true` — надо поднять надзор заново.
pub fn begin_restart(status: &mut ResidentStatus) -> bool {
    if !matches!(status, ResidentStatus::Failed { .. }) {
        return false;
    }
    *status = ResidentStatus::Starting;
    true
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
    /// Кандидат, из которого запущен свой процесс: установке движка надо
    /// знать, не держит ли резидент файлы окружения.
    running_from: Mutex<Option<PathBuf>>,
    /// «Выходим»: надзор больше не перезапускает и не запускает.
    quitting: AtomicBool,
    /// Поколение надзора. `respawn`/`stop_if_from` его увеличивают: поток
    /// надзора прежнего поколения видит это и уходит, ничего не трогая, —
    /// иначе он перезапустил бы остановленный резидент или принял бы новый
    /// процесс за свой.
    generation: AtomicU64,
    /// Второй «Выход» ждёт первый, а не выходит раньше, чем резидент сохранил
    /// запись. Под этим же замком — остановка ради установки движка.
    shutdown_gate: Mutex<()>,
}

/// Окружение движка текущей версии и PATH с ffmpeg для резидента из него.
struct EngineContext {
    data_dir: PathBuf,
    version: String,
    env_dir: PathBuf,
    ffmpeg_dir: Option<PathBuf>,
}

impl EngineContext {
    fn of(app: &AppHandle) -> Self {
        let data_dir = data_dir();
        let version = app.package_info().version.to_string();
        EngineContext {
            env_dir: engine::env_dir(&data_dir, &version),
            ffmpeg_dir: engine::ffmpeg_dir(app),
            data_dir,
            version,
        }
    }

    /// Кандидат из окружения движка (текущей версии).
    fn owns(&self, candidate: &Path) -> bool {
        candidate.starts_with(&self.env_dir)
    }
}

impl Supervisor {
    /// Поднять надзор в фоновом потоке и положить его в состояние приложения.
    /// `held` — резидент ждёт обслуживания движка (`EngineUpdating`): надзор
    /// начнётся с `respawn` по его окончании.
    pub fn start(app: &AppHandle, held: bool) {
        let supervisor = Supervisor {
            inner: Arc::new(Inner {
                status: Mutex::new(if held {
                    ResidentStatus::EngineUpdating { step: 0, of: 0 }
                } else {
                    ResidentStatus::Starting
                }),
                child: Mutex::new(None),
                running_from: Mutex::new(None),
                quitting: AtomicBool::new(false),
                generation: AtomicU64::new(0),
                shutdown_gate: Mutex::new(()),
            }),
        };
        app.manage(supervisor.clone());
        if !held {
            supervisor.supervise_in_background(app);
        }
    }

    /// Шаг фонового обслуживания движка — в статус (подсказка трея). Только
    /// пока резидент его ждёт: установка из мастера статус не трогает.
    pub fn engine_step(&self, step: usize, of: usize) {
        let mut status = lock(&self.inner.status);
        if matches!(*status, ResidentStatus::EngineUpdating { .. }) {
            *status = ResidentStatus::EngineUpdating { step, of };
        }
    }

    /// Поток надзора текущего поколения. Счётчик перезапусков живёт в нём,
    /// так что новый поток — это и сброс счётчика.
    fn supervise_in_background(&self, app: &AppHandle) {
        let supervisor = self.clone();
        let app = app.clone();
        let generation = self.inner.generation.load(Ordering::SeqCst);
        let spawned = thread::Builder::new()
            .name("meet-resident".into())
            .spawn(move || supervisor.supervise(&app, generation));
        if let Err(error) = spawned {
            shell_log!("поток надзора за резидентом не запустился: {error}");
        }
    }

    /// «Перезапустить сервис» из трея: сдавшийся надзор начинается заново,
    /// с нулевым счётчиком перезапусков.
    ///
    /// Под тем же замком, что `respawn`/`stop_if_from`/`shutdown`, и с новым
    /// поколением: иначе клик посреди `respawn` поднял бы второй поток надзора
    /// того же поколения — и второй резидент. Замок занят (идёт `respawn` или
    /// «Выход») — клик ничего не делает: зовётся из меню трея, главный поток
    /// ждать не должен, а надзор и так начинается заново.
    pub fn restart(&self, app: &AppHandle) {
        let _gate = match self.inner.shutdown_gate.try_lock() {
            Ok(gate) => gate,
            Err(std::sync::TryLockError::Poisoned(poisoned)) => poisoned.into_inner(),
            Err(std::sync::TryLockError::WouldBlock) => return,
        };
        if self.quitting() || !begin_restart(&mut lock(&self.inner.status)) {
            return;
        }
        shell_log!("перезапуск сервиса по просьбе пользователя");
        self.inner.generation.fetch_add(1, Ordering::SeqCst);
        self.supervise_in_background(app);
    }

    /// Начать надзор заново, в каком бы состоянии он ни был: свой резидент
    /// штатно гасится (`/shutdown` — идущая запись сохраняется), новый поток
    /// заново собирает кандидатов. Так после установки движка резидент
    /// переезжает в новое окружение. Чужой резидент не трогаем. Блокирует до
    /// 80 с: вызывать не из главного потока.
    pub fn respawn(&self, app: &AppHandle) {
        let _gate = lock(&self.inner.shutdown_gate);
        if self.quitting() {
            return;
        }
        self.inner.generation.fetch_add(1, Ordering::SeqCst);
        if self.child_pid().is_some() {
            shell_log!("перезапуск резидента: движок обновлён");
        }
        self.stop_child();
        *lock(&self.inner.status) = ResidentStatus::Starting;
        self.supervise_in_background(app);
    }

    /// Остановить свой резидент, если он запущен из `dir` (окружение движка,
    /// которое сейчас будут пересобирать), и не запускать его снова до
    /// `respawn`. `true` — остановили.
    pub fn stop_if_from(&self, dir: &Path) -> bool {
        let _gate = lock(&self.inner.shutdown_gate);
        if self.quitting() || self.child_pid().is_none() {
            return false;
        }
        let from_dir = lock(&self.inner.running_from)
            .as_deref()
            .is_some_and(|from| from.starts_with(dir));
        if !from_dir {
            return false;
        }
        shell_log!("останавливаю резидент: его движок переустанавливается");
        self.inner.generation.fetch_add(1, Ordering::SeqCst);
        self.stop_child();
        *lock(&self.inner.status) = ResidentStatus::Starting;
        true
    }

    pub fn status(&self) -> ResidentStatus {
        lock(&self.inner.status).clone()
    }

    /// Статус от надзора поколения `generation`. Прежнее поколение молчит:
    /// его поток уже не отвечает за резидент. После «Выхода» статус остаётся
    /// `Quitting`: надзор, ещё не заметивший флаг, не должен вернуть трею
    /// «запускается» или «работает».
    fn set_status(&self, generation: u64, status: ResidentStatus) {
        let mut slot = lock(&self.inner.status);
        if self.current(generation) {
            *slot = status;
        }
    }

    fn quitting(&self) -> bool {
        self.inner.quitting.load(Ordering::SeqCst)
    }

    /// Надзор поколения `generation` всё ещё в силе: не выходим и не начат
    /// заново.
    fn current(&self, generation: u64) -> bool {
        !self.quitting() && self.inner.generation.load(Ordering::SeqCst) == generation
    }

    fn supervise(&self, app: &AppHandle, generation: u64) {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|exe| exe.parent().map(Path::to_path_buf))
            .unwrap_or_default();
        let engine = EngineContext::of(app);
        let list = candidates(
            &exe_dir,
            std::env::var_os(OVERRIDE_ENV).as_deref(),
            &engine.data_dir,
            &engine.version,
            cfg!(debug_assertions),
        );
        let mut restarts = 0;
        let mut mode = if endpoint_answers() {
            Mode::External
        } else {
            Mode::Spawn
        };
        loop {
            if !self.current(generation) {
                return;
            }
            match mode {
                Mode::External => {
                    // Чужой резидент не вечен: это может быть осиротевший
                    // резидент прошлой оболочки, который дописывает запись и
                    // сейчас выйдет.
                    shell_log!("резидент уже работает вне приложения — подключаюсь к нему");
                    self.set_status(generation, ResidentStatus::External);
                    self.watch_external(generation, &engine.version);
                    if !self.current(generation) {
                        return;
                    }
                    shell_log!("внешнего резидента больше нет — запускаю свой");
                    mode = Mode::Spawn;
                    continue;
                }
                Mode::ExternalNoApi => {
                    shell_log!(
                        "tray.lock держит резидент без API (старая версия) — жду его выхода"
                    );
                    self.set_status(generation, ResidentStatus::ExternalNoApi);
                    mode = self.watch_no_api(generation);
                    continue;
                }
                Mode::Spawn => {}
            }
            self.set_status(generation, ResidentStatus::Starting);
            let Some((pid, from_engine)) = self.spawn(&list, generation, &engine) else {
                if self.current(generation) {
                    let installed = engine::is_installed(&engine.env_dir, &engine.version);
                    match no_candidate(cfg!(debug_assertions), installed) {
                        NoCandidate::EngineMissing => {
                            shell_log!(
                                "движок {} не установлен — резидент запустится после мастера",
                                engine.version
                            );
                            self.set_status(generation, ResidentStatus::EngineMissing);
                        }
                        NoCandidate::GiveUp => {
                            shell_log!("резидент не найден ни по одному пути");
                            self.give_up(app, generation);
                        }
                    }
                }
                return;
            };
            let started = Instant::now();
            // Ответил резидент из движка текущей версии — окружения прежних
            // версий больше не нужны.
            let cleanup = from_engine.then_some(&engine);
            let code = self.wait(pid, generation, cleanup);
            if !self.current(generation) {
                return;
            }
            restarts = restarts_after_exit(restarts, started.elapsed());
            match next_action(code, restarts) {
                Action::Restart => {
                    restarts += 1;
                    shell_log!(
                        "резидент вышел (код {code:?}), перезапуск {restarts} из {MAX_RESTARTS}"
                    );
                    thread::sleep(RESTART_PAUSE);
                }
                // Код 3 — не падение: счётчик перезапусков не растёт, дальше
                // следим за тем, кто держит tray.lock.
                Action::AdoptExternal => {
                    mode = adopt_mode(read_endpoint().is_some_and(|endpoint| answers(&endpoint)))
                }
                Action::GiveUp => {
                    shell_log!("резидент падает раз за разом (код {code:?}) — сдаюсь");
                    self.give_up(app, generation);
                    return;
                }
            }
        }
    }

    /// Ждать, пока жив держатель tray.lock без API; вернуть, что дальше:
    /// API ответил — `External`, держателя нет — `Spawn`.
    fn watch_no_api(&self, generation: u64) -> Mode {
        loop {
            thread::sleep(NO_API_POLL);
            if !self.current(generation) {
                return Mode::Spawn; // надзор увидит это и выйдет
            }
            let api = read_endpoint().is_some_and(|endpoint| answers(&endpoint));
            let alive = tray_lock_holder().is_some_and(pid_alive);
            match no_api_next(api, alive) {
                NoApiStep::Stay => {}
                NoApiStep::Adopt => return Mode::External,
                NoApiStep::Takeover => {
                    shell_log!("старый резидент вышел — запускаю свой");
                    return Mode::Spawn;
                }
            }
        }
    }

    /// Ждать, пока чужой резидент не перестанет отвечать (или пока надзор
    /// не кончился).
    ///
    /// Проверка без удаления `daemon.json`: файл принадлежит живому чужому
    /// резиденту, и единичный промах не должен стирать его публикацию.
    ///
    /// Резидент другой версии (его оставила прежняя оболочка, пока дописывала
    /// запись, или он из прежнего движка) после обновления работать не должен:
    /// простаивает — просим штатно выйти (`/shutdown`) и поднимаем свой; идёт
    /// запись — ждём её конца, проверяя каждые `EXTERNAL_POLL`.
    fn watch_external(&self, generation: u64, app_version: &str) {
        let mut misses = 0;
        let mut waiting = false;
        loop {
            thread::sleep(EXTERNAL_POLL);
            if !self.current(generation) {
                return;
            }
            let endpoint = read_endpoint().filter(answers);
            let (step, next) = external_next(endpoint.is_some(), misses);
            misses = next;
            if step == ExternalStep::Takeover {
                return;
            }
            let Some(endpoint) = endpoint else {
                continue;
            };
            let client = api::Client::new(&endpoint);
            let Ok(state) = client.get_state() else {
                continue;
            };
            let version = state
                .get("version")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("0.1.0 или раньше")
                .to_string();
            match upgrade::external_version(&state, app_version) {
                upgrade::ExternalVersion::Keep => waiting = false,
                upgrade::ExternalVersion::WaitIdle => {
                    if !waiting {
                        shell_log!(
                            "внешний резидент версии {version} занят записью — заменю его, когда закончит"
                        );
                        waiting = true;
                    }
                }
                upgrade::ExternalVersion::Replace => {
                    shell_log!(
                        "внешний резидент версии {version}, приложение {app_version} — прошу его выйти"
                    );
                    if let Err(error) = client.post("/shutdown", serde_json::Value::Null) {
                        shell_log!("/shutdown внешнему резиденту не прошёл: {error}");
                    }
                    let deadline = Instant::now() + EXIT_GRACE;
                    while Instant::now() < deadline && answers(&endpoint) {
                        if !self.current(generation) {
                            return;
                        }
                        thread::sleep(POLL);
                    }
                    return;
                }
            }
        }
    }

    /// Запустить первого нашедшегося кандидата; pid и «из движка текущей
    /// версии» или `None`.
    fn spawn(
        &self,
        list: &[PathBuf],
        generation: u64,
        engine: &EngineContext,
    ) -> Option<(u32, bool)> {
        for candidate in list {
            let (program, arguments) = launch(candidate, std::process::id());
            let mut command = Command::new(&program);
            command
                .args(&arguments)
                .stdin(Stdio::null())
                // Вывод резидента — в resident.log: UTF-8 (иначе кириллица в
                // cp1251) и без буфера (traceback падения не должен
                // потеряться в буфере умирающего процесса).
                .env("PYTHONIOENCODING", "utf-8")
                .env("PYTHONUNBUFFERED", "1");
            let from_engine = engine.owns(candidate);
            // ffmpeg установленного приложения лежит в ресурсах, а не в PATH
            // пользователя: резидент (и его задачи) находят его по PATH.
            if let Some(ffmpeg) = engine.ffmpeg_dir.as_deref().filter(|_| from_engine) {
                command.env(
                    "PATH",
                    engine::path_with(ffmpeg, std::env::var_os("PATH").as_deref()),
                );
            }
            redirect_output(&mut command, &program);
            hide_console(&mut command);
            match command.spawn() {
                Ok(mut child) => {
                    let pid = child.id();
                    let mut slot = lock(&self.inner.child);
                    // shutdown()/respawn() меняют флаг или поколение до того,
                    // как берут этот замок: либо они увидят процесс здесь,
                    // либо мы увидим перемену.
                    if !self.current(generation) {
                        drop(slot);
                        kill_tree(pid);
                        let _ = child.wait();
                        return None;
                    }
                    *slot = Some(child);
                    *lock(&self.inner.running_from) = Some(candidate.clone());
                    shell_log!("резидент запущен: {} (pid {pid})", candidate.display());
                    return Some((pid, from_engine));
                }
                Err(error) => shell_log!("{}: {error}", candidate.display()),
            }
        }
        None
    }

    /// Ждать выхода своего процесса; попутно отметить публикацию API.
    /// `cleanup` — резидент из движка текущей версии: когда он ответит,
    /// убрать окружения прежних версий.
    ///
    /// pid из `daemon.json` с `pid` процесса не сравниваем: `meet-tray.exe` из
    /// venv — лаунчер, а сам Python — его дочерний процесс с другим pid.
    /// Протухшая публикация уже убрана `endpoint_answers()`, так что
    /// отвечающий адрес после запуска — наш.
    fn wait(&self, pid: u32, generation: u64, cleanup: Option<&EngineContext>) -> Option<i32> {
        let started = Instant::now();
        let mut warned = false;
        loop {
            {
                let mut slot = lock(&self.inner.child);
                // Надзор начат заново или выходим: процессом распоряжается
                // тот, кто это сделал, а в слоте может быть уже новый.
                if !self.current(generation) {
                    return None;
                }
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
                        shell_log!("не удалось проверить резидента (pid {pid}): {error}");
                        *slot = None;
                        return None;
                    }
                }
            }
            if self.status() == ResidentStatus::Starting {
                if read_endpoint().is_some_and(|endpoint| answers(&endpoint)) {
                    self.set_status(generation, ResidentStatus::Running);
                    if let Some(engine) = cleanup {
                        engine::remove_stale_in_background(
                            engine.data_dir.clone(),
                            engine.version.clone(),
                        );
                    }
                } else if !warned && started.elapsed() >= PUBLISH_TIMEOUT {
                    warned = true;
                    shell_log!(
                        "резидент (pid {pid}) за {} с не опубликовал daemon.json",
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
    fn give_up(&self, app: &AppHandle, generation: u64) {
        // resident.log, а не watch.log: причина падения (traceback, «модуль
        // не найден») — в выводе процесса, в журнал решений детектора она не
        // попадает.
        self.set_status(
            generation,
            ResidentStatus::Failed {
                log: logs::resident_log(&data_dir()),
            },
        );
        tray::notify(
            app,
            vec![Notice {
                title: tray::RESIDENT_FAILED.into(),
                body: "Откройте журнал из меню трея".into(),
                recording: None,
            }],
        );
    }

    /// Штатно погасить свой резидент перед выходом из приложения. Чужой
    /// резидент (`External`) не трогаем. Блокирует: вызывать не из главного
    /// потока.
    pub fn shutdown(&self) {
        let _gate = lock(&self.inner.shutdown_gate);
        if self.inner.quitting.swap(true, Ordering::SeqCst) {
            return; // уже погашен предыдущим вызовом
        }
        *lock(&self.inner.status) = ResidentStatus::Quitting;
        // Своего процесса нет — External-резидент принадлежит пользователю
        // (например, `--watch` из автозагрузки), «Выход» его не гасит.
        self.stop_child();
    }

    /// Погасить свой процесс: `POST /shutdown` (идущая запись сохраняется,
    /// ответ — до 70 с), затем, если процесс ещё жив, — убить дерево.
    /// Своего процесса нет — ничего не делает.
    fn stop_child(&self) {
        let Some(pid) = self.child_pid() else {
            return;
        };
        match read_endpoint() {
            Some(endpoint) => {
                if let Err(error) =
                    api::Client::new(&endpoint).post("/shutdown", serde_json::Value::Null)
                {
                    shell_log!("/shutdown не прошёл: {error}");
                }
            }
            None => shell_log!("резидент не опубликовал адрес — /shutdown некуда слать"),
        }
        let deadline = Instant::now() + EXIT_GRACE;
        while Instant::now() < deadline {
            if self.child_exited() {
                *lock(&self.inner.running_from) = None;
                return;
            }
            thread::sleep(POLL);
        }
        shell_log!("резидент (pid {pid}) не вышел сам — завершаю дерево процессов");
        kill_tree(pid);
        if let Some(mut child) = lock(&self.inner.child).take() {
            let _ = child.wait();
        }
        *lock(&self.inner.running_from) = None;
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

/// stdout и stderr резидента — в `logs\resident.log` (дописыванием, большой
/// файл сначала уезжает в `.1`), с заголовком запуска. Журнал не открылся —
/// вывод в никуда, но резидент всё равно запускается.
fn redirect_output(command: &mut Command, program: &Path) {
    let path = logs::resident_log(&data_dir());
    let opened = logs::open_append(&path).and_then(|mut file| {
        let header = format!("--- запуск резидента: {}", program.display());
        file.write_all(logs::line(SystemTime::now(), &header).as_bytes())?;
        let copy = file.try_clone()?;
        Ok((file, copy))
    });
    match opened {
        Ok((out, err)) => {
            command.stdout(out).stderr(err);
        }
        Err(error) => {
            shell_log!("журнал резидента {} не открылся: {error}", path.display());
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
}

/// Без окна консоли: резидент, taskkill, uv и nvidia-smi — консольные
/// программы.
pub(crate) fn hide_console(command: &mut Command) {
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
        shell_log!("taskkill не запустился: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;
    use std::path::Path;

    const DEV_EXE_DIR: &str = r"C:\repo\app\src-tauri\target\debug";
    const DEV_VENV: &str = r"C:\repo\.venv\Scripts\meet-tray.exe";

    /// Папка данных с окружением движка `0.2.0`, маркер — версии `marker`.
    fn data_with_engine(name: &str, marker: Option<&str>) -> TempTree {
        let tree = TempTree::new(name, &[r"engine\0.2.0\Scripts\meet-tray.exe"]);
        if let Some(version) = marker {
            std::fs::write(
                tree.0.join(r"engine\0.2.0\installed.json"),
                engine::marker_json(version, "cuda", "2026-10-01 03:00:00Z", None),
            )
            .unwrap();
        }
        tree
    }

    #[test]
    fn without_engine_dev_venv_then_path() {
        // Dev-режим как раньше: движок не установлен — резидент из .venv.
        let data = TempTree::new("cand-none", &[]);
        let list = candidates(Path::new(DEV_EXE_DIR), None, &data.0, "0.2.0", true);
        assert_eq!(
            list,
            vec![PathBuf::from(DEV_VENV), PathBuf::from("meet-tray.exe")]
        );
    }

    #[test]
    fn installed_engine_goes_before_dev_venv() {
        let data = data_with_engine("cand-engine", Some("0.2.0"));
        let engine = data.0.join(r"engine\0.2.0\Scripts\meet-tray.exe");
        let list = candidates(Path::new(DEV_EXE_DIR), None, &data.0, "0.2.0", true);
        assert_eq!(
            list,
            vec![
                engine.clone(),
                PathBuf::from(DEV_VENV),
                PathBuf::from("meet-tray.exe")
            ]
        );
        let list = candidates(Path::new(DEV_EXE_DIR), None, &data.0, "0.2.0", false);
        assert_eq!(list, vec![engine, PathBuf::from("meet-tray.exe")]);
    }

    #[test]
    fn engine_of_another_version_is_not_a_candidate() {
        // Маркер 0.1.0 в папке 0.2.0 — окружение не этой версии.
        let data = data_with_engine("cand-other", Some("0.1.0"));
        let list = candidates(Path::new(DEV_EXE_DIR), None, &data.0, "0.2.0", false);
        assert_eq!(list, vec![PathBuf::from("meet-tray.exe")]);
        // Приложение обновилось до 0.3.0, а движок есть только для 0.2.0.
        let data = data_with_engine("cand-update", Some("0.2.0"));
        let list = candidates(Path::new(DEV_EXE_DIR), None, &data.0, "0.3.0", false);
        assert_eq!(list, vec![PathBuf::from("meet-tray.exe")]);
        // Недостроенное окружение без маркера — тоже нет.
        let data = data_with_engine("cand-half", None);
        let list = candidates(Path::new(DEV_EXE_DIR), None, &data.0, "0.2.0", false);
        assert_eq!(list, vec![PathBuf::from("meet-tray.exe")]);
    }

    #[test]
    fn meet_resident_env_goes_before_everything() {
        let data = data_with_engine("cand-env", Some("0.2.0"));
        let chosen = r"D:\other\.venv\Scripts\meet-tray.exe";
        let list = candidates(
            Path::new(DEV_EXE_DIR),
            Some(OsStr::new(chosen)),
            &data.0,
            "0.2.0",
            true,
        );
        assert_eq!(list[0], Path::new(chosen));
        assert_eq!(list[1], data.0.join(r"engine\0.2.0\Scripts\meet-tray.exe"));
        assert_eq!(list.last().unwrap(), Path::new("meet-tray.exe"));
    }

    #[test]
    fn blank_meet_resident_env_is_ignored() {
        let data = TempTree::new("cand-blank", &[]);
        let list = candidates(
            Path::new(DEV_EXE_DIR),
            Some(OsStr::new("  ")),
            &data.0,
            "0.2.0",
            true,
        );
        assert_eq!(list[0], Path::new(DEV_VENV));
    }

    #[test]
    fn release_build_never_looks_into_a_dev_venv() {
        // Установленное приложение не должно подхватить .venv из папки,
        // которая случайно лежит на 4 уровня выше exe.
        let data = TempTree::new("cand-release", &[]);
        let exe_dir = Path::new(r"C:\repo\app\src-tauri\target\release");
        let list = candidates(exe_dir, None, &data.0, "0.2.0", false);
        assert_eq!(list, vec![PathBuf::from("meet-tray.exe")]);
    }

    #[test]
    fn release_without_engine_waits_for_the_wizard_silently() {
        // Первый запуск и каждое обновление версии: резидента ещё нет — это
        // не сбой, уведомлять не о чем.
        assert_eq!(no_candidate(false, false), NoCandidate::EngineMissing);
        // Движок стоит, а резидент не нашёлся/не запустился — сбой.
        assert_eq!(no_candidate(false, true), NoCandidate::GiveUp);
        // Dev — как раньше: без .venv это сбой с уведомлением.
        assert_eq!(no_candidate(true, false), NoCandidate::GiveUp);
        assert_eq!(no_candidate(true, true), NoCandidate::GiveUp);
    }

    #[test]
    fn restart_is_only_for_a_failed_resident() {
        let mut status = ResidentStatus::Failed {
            log: PathBuf::from(r"C:\data\logs\resident.log"),
        };
        assert!(begin_restart(&mut status));
        assert_eq!(status, ResidentStatus::Starting);
        // Уже перезапускается (второй клик) или работает — не трогаем.
        assert!(!begin_restart(&mut status));
        let mut running = ResidentStatus::Running;
        assert!(!begin_restart(&mut running));
        assert_eq!(running, ResidentStatus::Running);
        let mut quitting = ResidentStatus::Quitting;
        assert!(!begin_restart(&mut quitting));
    }

    /// Временная папка теста с заданными файлами; удаляется в конце теста.
    struct TempTree(PathBuf);

    impl TempTree {
        fn new(name: &str, files: &[&str]) -> Self {
            let root =
                std::env::temp_dir().join(format!("meet-shell-test-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            for file in files {
                let path = root.join(file);
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(path, b"").unwrap();
            }
            TempTree(root)
        }
    }

    impl Drop for TempTree {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn venv_launcher_runs_through_console_python() {
        let tree = TempTree::new(
            "venv",
            &[
                "pyvenv.cfg",
                r"Scripts\python.exe",
                r"Scripts\pythonw.exe",
                r"Scripts\meet-tray.exe",
            ],
        );
        let candidate = tree.0.join("Scripts").join("meet-tray.exe");
        let (program, arguments) = launch(&candidate, 4242);
        assert_eq!(program, tree.0.join("Scripts").join("python.exe"));
        assert_eq!(
            arguments,
            vec![
                "-c",
                "import sys; from meet.tray import main; sys.exit(main())",
                "--headless",
                "--parent-pid",
                "4242"
            ]
        );
    }

    #[test]
    fn non_venv_resident_is_run_as_is() {
        // MEET_RESIDENT на exe вне venv (без pyvenv.cfg рядом).
        let tree = TempTree::new("bundled", &[r"resident\meet-tray.exe"]);
        let candidate = tree.0.join("resident").join("meet-tray.exe");
        let (program, arguments) = launch(&candidate, 7);
        assert_eq!(program, candidate);
        assert_eq!(arguments, args(7));
        // Голое имя (поиск по PATH) — тоже как есть.
        let (program, _) = launch(Path::new("meet-tray.exe"), 7);
        assert_eq!(program, Path::new("meet-tray.exe"));
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

    // --- чужой резидент без API (`meet-tray --watch` из upstream) ----------

    #[test]
    fn exit_3_without_answering_api_is_an_old_resident() {
        assert_eq!(adopt_mode(true), Mode::External);
        assert_eq!(adopt_mode(false), Mode::ExternalNoApi);
    }

    #[test]
    fn old_resident_is_left_alone_while_its_lock_holder_lives() {
        // Без этого оболочка запускала своего каждые ~6 с: запуск → код 3 →
        // API нет → «пропал» → запуск…
        assert_eq!(no_api_next(false, true), NoApiStep::Stay);
    }

    #[test]
    fn dead_lock_holder_means_takeover() {
        assert_eq!(no_api_next(false, false), NoApiStep::Takeover);
    }

    #[test]
    fn answering_api_means_an_external_resident_after_all() {
        assert_eq!(no_api_next(true, true), NoApiStep::Adopt);
        assert_eq!(no_api_next(true, false), NoApiStep::Adopt);
    }

    #[test]
    fn tray_lock_names_its_holder() {
        assert_eq!(
            lock_holder(r#"{"pid": 4242, "name": "pythonw.exe", "started": 1.5}"#),
            Some(4242)
        );
        assert_eq!(lock_holder(r#"{"pid": 7}"#), Some(7));
        assert_eq!(lock_holder(r#"{"name": "pythonw.exe"}"#), None);
        assert_eq!(lock_holder(r#"{"pid": -1}"#), None);
        assert_eq!(lock_holder("мусор"), None);
    }

    #[test]
    fn pid_alive_tells_a_living_process_from_a_finished_one() {
        assert!(pid_alive(std::process::id()));
        assert!(!pid_alive(0));
        let mut child = Command::new("cmd")
            .args(["/C", "exit 0"])
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        let pid = child.id();
        child.wait().unwrap();
        drop(child); // закрыть свой хэндл — иначе процесс-зомби держит pid
        assert!(!pid_alive(pid));
    }
}
