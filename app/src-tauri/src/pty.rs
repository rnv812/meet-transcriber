// Вкладка «Агент» карточки встречи: настоящий интерактивный Claude Code или
// Codex во встроенном терминале окна (xterm.js).
//
// Терминал — псевдоконсоль (ConPTY на Windows, крейт portable-pty). Процесс
// агента запускается в папке записи, где резидент перед этим кладёт
// `transcript.md` (POST /recordings/{id}/agent-context). Вывод уходит окну
// событиями `agent-data` {id, data}, конец — `agent-exit` {id, code}.
//
// IMPORTANT — безопасность:
// * запускаются только найденные резидентом программы провайдеров
//   (GET /assistant `available`): claude — только родной claude.exe, codex —
//   только codex.exe (сценарий .cmd не запускаем: аргументы через cmd.exe
//   разбираются по его правилам, путь с `%` или `"` превратился бы в команду);
// * папка записи — прямой потомок папки записей (после canonicalize);
// * команды — только из главного окна;
// * у каждой сессии свой job object с KILL_ON_JOB_CLOSE: «Остановить» гасит
//   всё дерево агента (его node-процессы, инструменты), а при выходе или
//   падении оболочки Windows закрывает хэндлы — и агенты умирают вместе с ней.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use portable_pty::{native_pty_system, ChildKiller, CommandBuilder, MasterPty, PtySize};
use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Emitter};

use crate::api::Client;
use crate::logs::shell_log;
use crate::netproxy::{self, InternetSettings};
use crate::{resident, windows};

/// Окно, которому доступны команды агента и уходят его события.
const MAIN: &str = "main";

/// Добавка к системному промпту (Claude — `--append-system-prompt`, Codex —
/// `developer_instructions`).
pub const AGENT_PROMPT: &str = "Ты помогаешь разобрать встречу. В текущей папке \
transcript.md — расшифровка с именами и таймкодами, summary.md — итоги (если есть). \
База знаний (если подключена) — только для чтения; не изменяй её файлы.";

/// Переменные прокси, которые понимают Claude Code и Codex (регистр любой).
const PROXY_VARS: [&str; 3] = ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"];

/// Размер терминала: ConPTY не принимает нулевой, а огромный — признак
/// ошибки окна, не реального экрана.
const MIN_COLS: u16 = 20;
const MAX_COLS: u16 = 500;
const MIN_ROWS: u16 = 5;
const MAX_ROWS: u16 = 300;

/// Сколько ждать после выхода агента, прежде чем закрыть псевдоконсоль:
/// conhost успевает отдать последний кадр (например, вывод `--version`).
const EXIT_GRACE: Duration = Duration::from_millis(200);

// --- чистые функции -------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Provider {
    Claude,
    Codex,
}

impl Provider {
    /// Имя провайдера из окна или резидента.
    pub fn parse(name: &str) -> Option<Provider> {
        match name.trim() {
            "claude-code" | "claude" => Some(Provider::Claude),
            "codex" => Some(Provider::Codex),
            _ => None,
        }
    }

    /// Ключ в `available` ответа GET /assistant.
    pub fn key(self) -> &'static str {
        match self {
            Provider::Claude => "claude-code",
            Provider::Codex => "codex",
        }
    }

    fn title(self) -> &'static str {
        match self {
            Provider::Claude => "Claude Code",
            Provider::Codex => "Codex",
        }
    }
}

/// Годится ли найденный резидентом путь для запуска в терминале. На Windows —
/// только `.exe`: claude.cmd от npm не годится по тому же правилу, что и у
/// резидента, а codex.cmd пришлось бы запускать через cmd.exe.
pub fn check_executable(provider: Provider, path: &str, windows: bool) -> Result<(), String> {
    let path = path.trim();
    if path.is_empty() {
        return Err(format!(
            "{} не найден. Подключите его в настройках",
            provider.title()
        ));
    }
    if !windows {
        return Ok(());
    }
    let ext = Path::new(path)
        .extension()
        .and_then(|e| e.to_str())
        .map(str::to_ascii_lowercase);
    match (provider, ext.as_deref()) {
        (_, Some("exe")) => Ok(()),
        (Provider::Codex, Some("cmd" | "bat")) => Err(
            "Codex установлен как сценарий npm (codex.cmd). Встроенный терминал запускает \
             только программу codex.exe — установите Codex отдельной программой"
                .into(),
        ),
        _ => Err(format!(
            "{} найден не как программа .exe — встроенный терминал его не запускает",
            provider.title()
        )),
    }
}

/// Аргументы агента. Claude: промпт и база знаний через `--add-dir`. Codex:
/// `--cd` и промпт в `developer_instructions`; `--add-dir` у Codex делает
/// папку доступной на запись, поэтому базу знаний ему только называем
/// (читать файлы вне рабочей папки песочница Codex и так разрешает).
pub fn agent_args(provider: Provider, folder: &str, knowledge: Option<&str>) -> Vec<String> {
    let knowledge = knowledge.map(str::trim).filter(|k| !k.is_empty());
    match provider {
        Provider::Claude => {
            let mut args = vec!["--append-system-prompt".to_string(), AGENT_PROMPT.into()];
            if let Some(dir) = knowledge {
                args.extend(["--add-dir".to_string(), dir.to_string()]);
            }
            args
        }
        Provider::Codex => {
            let mut prompt = AGENT_PROMPT.to_string();
            if let Some(dir) = knowledge {
                prompt.push_str(&format!(" Папка базы знаний: {dir}"));
            }
            vec![
                "--cd".into(),
                folder.to_string(),
                "-c".into(),
                format!("developer_instructions={}", toml_string(&prompt)),
            ]
        }
    }
}

/// Строка TOML (значение `-c key=value` у Codex): JSON-строка — она же
/// корректная базовая строка TOML (те же экранирования `\"`, `\\`, `\n`, `\uXXXX`).
fn toml_string(text: &str) -> String {
    serde_json::to_string(text).unwrap_or_else(|_| "\"\"".into())
}

/// Режим настройки `llm.proxy` (как `meet.netproxy` резидента).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProxyMode {
    /// Переменные среды, если заданы, иначе прокси Windows (WinINET).
    System,
    /// Без прокси: унаследованные переменные убираются.
    None,
    /// Заданный адрес.
    Url(String),
}

/// Значение настройки → режим. Негодное — «как в системе», как у резидента.
pub fn proxy_mode(raw: Option<&str>) -> ProxyMode {
    let text = raw.unwrap_or("").trim();
    match text {
        "none" => ProxyMode::None,
        "" | "system" => ProxyMode::System,
        url => {
            let url = url.strip_suffix('/').unwrap_or(url);
            if netproxy::valid_url(url) {
                ProxyMode::Url(url.to_string())
            } else {
                ProxyMode::System
            }
        }
    }
}

/// Что поменять в унаследованном окружении агента.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct EnvPlan {
    pub set: Vec<(String, String)>,
    pub remove: Vec<String>,
}

impl EnvPlan {
    pub fn get(&self, key: &str) -> Option<&str> {
        self.set
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(key))
            .map(|(_, v)| v.as_str())
    }
}

/// Окружение агента поверх унаследованного (`inherited` — чтение переменной
/// оболочки без учёта регистра). Claude без ANTHROPIC_API_KEY (подписка
/// важнее ключа — как у резидента); прокси по `llm.proxy` — так же, как
/// `meet.netproxy.child_env`: без него Claude Code за VPN отвечает 403
/// «Request not allowed»; терминал — xterm-256color с truecolor.
pub fn agent_env(
    provider: Provider,
    mode: &ProxyMode,
    inherited: &dyn Fn(&str) -> Option<String>,
    system: &InternetSettings,
) -> EnvPlan {
    let mut plan = EnvPlan::default();
    if provider == Provider::Claude {
        plan.remove.push("ANTHROPIC_API_KEY".into());
    }
    let present = |name: &str| inherited(name).is_some_and(|v| !v.trim().is_empty());
    let inherited_no_proxy = inherited("NO_PROXY").filter(|v| !v.trim().is_empty());
    let mut proxied = false;
    match mode {
        ProxyMode::None => {
            for name in PROXY_VARS {
                plan.remove.push(name.into());
                plan.remove.push(name.to_ascii_lowercase());
            }
        }
        ProxyMode::Url(url) => {
            plan.set.push(("HTTPS_PROXY".into(), url.clone()));
            plan.set.push(("HTTP_PROXY".into(), url.clone()));
            if present("ALL_PROXY") {
                plan.set.push(("ALL_PROXY".into(), url.clone()));
            }
            proxied = true;
        }
        ProxyMode::System => {
            let env_proxy = present("HTTPS_PROXY") || present("HTTP_PROXY");
            let found = netproxy::proxy_env_from(env_proxy, inherited_no_proxy.as_deref(), system);
            for (key, value) in found {
                plan.set
                    .push((key.to_string(), value.to_string_lossy().into_owned()));
            }
            proxied = plan.get("NO_PROXY").is_none() && PROXY_VARS.iter().any(|n| present(n));
        }
    }
    // Есть прокси — локальные адреса мимо него (унаследованный NO_PROXY не затираем).
    if proxied {
        plan.set.push((
            "NO_PROXY".into(),
            netproxy::no_proxy(inherited_no_proxy.as_deref(), None),
        ));
    }
    plan.set.push(("TERM".into(), "xterm-256color".into()));
    plan.set.push(("COLORTERM".into(), "truecolor".into()));
    plan
}

/// Id записи из окна: имя папки, без разделителей и `..`.
pub fn recording_id_valid(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 255
        && id != "."
        && id != ".."
        && !id.contains(['/', '\\', ':', '\0'])
}

/// Папка записи, если она прямо внутри папки записей (сравнение после
/// canonicalize: `..`, ссылки и регистр не выводят наружу). Возвращает путь
/// как есть — без префикса `\\?\`, который смутил бы node-программы в cwd.
pub fn recording_folder(folder: &Path, root: &Path) -> Option<PathBuf> {
    let target = folder.canonicalize().ok()?;
    let root = root.canonicalize().ok()?;
    (target.is_dir() && target.parent() == Some(root.as_path())).then(|| folder.to_path_buf())
}

/// Размер терминала в допустимых пределах.
pub fn clamp_size(cols: u16, rows: u16) -> (u16, u16) {
    (
        cols.clamp(MIN_COLS, MAX_COLS),
        rows.clamp(MIN_ROWS, MAX_ROWS),
    )
}

/// Байты псевдоконсоли → строки UTF-8 без потерь на стыках чтений: начало
/// многобайтного символа в конце куска ждёт продолжения. Битые байты —
/// U+FFFD, остальное не теряется.
#[derive(Debug, Default)]
pub struct Utf8Chunker {
    pending: Vec<u8>,
}

impl Utf8Chunker {
    pub fn push(&mut self, bytes: &[u8]) -> String {
        self.pending.extend_from_slice(bytes);
        let mut out = String::new();
        let mut rest: &[u8] = &self.pending;
        loop {
            match std::str::from_utf8(rest) {
                Ok(text) => {
                    out.push_str(text);
                    rest = &[];
                    break;
                }
                Err(error) => {
                    let valid = error.valid_up_to();
                    out.push_str(std::str::from_utf8(&rest[..valid]).unwrap_or_default());
                    match error.error_len() {
                        Some(bad) => {
                            out.push(char::REPLACEMENT_CHARACTER);
                            rest = &rest[valid + bad..];
                        }
                        None => {
                            rest = &rest[valid..];
                            break;
                        }
                    }
                }
            }
        }
        self.pending = rest.to_vec();
        out
    }

    /// Хвост в конце потока: недописанный символ — U+FFFD.
    pub fn finish(&mut self) -> String {
        let tail = String::from_utf8_lossy(&self.pending).into_owned();
        self.pending.clear();
        tail
    }
}

// --- сессии ---------------------------------------------------------------------

/// Что запустить.
pub struct SpawnSpec {
    pub recording: String,
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
    pub env: EnvPlan,
    pub cols: u16,
    pub rows: u16,
}

struct Session {
    recording: String,
    /// Ввод агента; закрывается, когда он вышел (см. ожидание в `spawn`).
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    /// Закрытие мастера закрывает псевдоконсоль — читатель получает конец потока.
    master: Mutex<Option<Box<dyn MasterPty + Send>>>,
    killer: Mutex<Box<dyn ChildKiller + Send + Sync>>,
    #[cfg(windows)]
    job: Mutex<Option<crate::engine::Job>>,
}

impl Session {
    /// Погасить агента и всё, что он запустил.
    fn kill(&self) {
        // WinChildKiller возвращает Err и при успехе — результат не смотрим.
        let _ = self.killer.lock().map(|mut k| k.kill());
        #[cfg(windows)]
        if let Ok(mut job) = self.job.lock() {
            job.take();
        }
    }
}

/// Кусок вывода сессии (id, текст).
pub type DataSink = Box<dyn Fn(&str, String) + Send + 'static>;
/// Конец сессии (id, код выхода).
pub type ExitSink = Box<dyn FnOnce(&str, Option<u32>) + Send + 'static>;

#[derive(Default)]
pub struct Sessions {
    map: Mutex<HashMap<String, Arc<Session>>>,
    next: AtomicU64,
}

impl Sessions {
    /// Запустить агента в псевдоконсоли. Прежняя сессия той же записи
    /// гасится: одна запись — один агент. Возвращает id сессии.
    pub fn spawn(
        &self,
        spec: SpawnSpec,
        on_data: DataSink,
        on_exit: ExitSink,
    ) -> Result<String, String> {
        self.kill_recording(&spec.recording);
        let (cols, rows) = clamp_size(spec.cols, spec.rows);
        let pair = native_pty_system()
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| format!("не удалось открыть терминал: {e}"))?;
        let mut command = CommandBuilder::new(&spec.program);
        command.args(&spec.args);
        command.cwd(&spec.cwd);
        for key in &spec.env.remove {
            command.env_remove(key);
        }
        for (key, value) in &spec.env.set {
            command.env(key, value);
        }
        let mut child = pair
            .slave
            .spawn_command(command)
            .map_err(|e| format!("агент не запустился: {e}"))?;
        // Слейв больше не нужен: держи его — псевдоконсоль не закрылась бы.
        drop(pair.slave);
        #[cfg(windows)]
        let job = bind_to_job(child.as_mut());
        let reader = pair.master.try_clone_reader();
        let writer = pair.master.take_writer();
        let (reader, writer) = match (reader, writer) {
            (Ok(reader), Ok(writer)) => (reader, writer),
            (Err(e), _) | (_, Err(e)) => {
                let _ = child.kill();
                return Err(format!("терминал агента недоступен: {e}"));
            }
        };
        let id = format!("agent-{}", self.next.fetch_add(1, Ordering::Relaxed) + 1);
        let session = Arc::new(Session {
            recording: spec.recording.clone(),
            writer: Mutex::new(Some(writer)),
            master: Mutex::new(Some(pair.master)),
            killer: Mutex::new(child.clone_killer()),
            #[cfg(windows)]
            job: Mutex::new(job),
        });
        self.lock().insert(id.clone(), session.clone());

        let pump_id = id.clone();
        let pump = thread::Builder::new()
            .name(format!("{id}-read"))
            .spawn(move || pump_output(reader, |text| on_data(&pump_id, text)))
            .map_err(|e| format!("поток чтения агента не запустился: {e}"))?;
        let waiter_id = id.clone();
        thread::Builder::new()
            .name(format!("{id}-wait"))
            .spawn(move || {
                let code = child.wait().ok().map(|status| status.exit_code());
                thread::sleep(EXIT_GRACE);
                // Ввод закрыт: conhost, который ещё ждёт от терминала ответа о
                // позиции курсора (агента убили до ответа), иначе не завершился
                // бы. Псевдоконсоль закрыта — читатель дочитывает и выходит.
                if let Ok(mut writer) = session.writer.lock() {
                    writer.take();
                }
                if let Ok(mut master) = session.master.lock() {
                    master.take();
                }
                let _ = pump.join();
                // Агент вышел сам — его оставшиеся дети уходят вместе с job.
                session.kill();
                drop(session);
                on_exit(&waiter_id, code);
            })
            .map_err(|e| format!("поток ожидания агента не запустился: {e}"))?;
        Ok(id)
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, Arc<Session>>> {
        self.map
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    fn get(&self, id: &str) -> Result<Arc<Session>, String> {
        self.lock()
            .get(id)
            .cloned()
            .ok_or_else(|| "агент уже остановлен".to_string())
    }

    /// Сессия закончилась сама — убрать из списка.
    pub fn forget(&self, id: &str) {
        self.lock().remove(id);
    }

    pub fn write(&self, id: &str, data: &str) -> Result<(), String> {
        let session = self.get(id)?;
        let mut writer = session
            .writer
            .lock()
            .map_err(|_| "терминал агента недоступен".to_string())?;
        let writer = writer
            .as_mut()
            .ok_or_else(|| "агент уже остановлен".to_string())?;
        writer
            .write_all(data.as_bytes())
            .and_then(|()| writer.flush())
            .map_err(|e| format!("агент не принял ввод: {e}"))
    }

    pub fn resize(&self, id: &str, cols: u16, rows: u16) -> Result<(), String> {
        let session = self.get(id)?;
        let (cols, rows) = clamp_size(cols, rows);
        let master = session
            .master
            .lock()
            .map_err(|_| "терминал агента недоступен".to_string())?;
        match master.as_ref() {
            Some(master) => master
                .resize(PtySize {
                    rows,
                    cols,
                    pixel_width: 0,
                    pixel_height: 0,
                })
                .map_err(|e| format!("размер терминала не изменился: {e}")),
            None => Ok(()),
        }
    }

    /// «Остановить». Сессии уже нет — не ошибка.
    pub fn kill(&self, id: &str) {
        let session = self.lock().remove(id);
        if let Some(session) = session {
            session.kill();
        }
    }

    fn kill_recording(&self, recording: &str) {
        let stale: Vec<Arc<Session>> = {
            let mut map = self.lock();
            let ids: Vec<String> = map
                .iter()
                .filter(|(_, s)| s.recording == recording)
                .map(|(id, _)| id.clone())
                .collect();
            ids.iter().filter_map(|id| map.remove(id)).collect()
        };
        stale.iter().for_each(|s| s.kill());
    }

    /// Закрыто главное окно или выход из приложения — гасим всех.
    pub fn kill_all(&self) {
        let all: Vec<Arc<Session>> = self.lock().drain().map(|(_, s)| s).collect();
        if !all.is_empty() {
            shell_log!("агент: остановлено сессий: {}", all.len());
        }
        all.iter().for_each(|s| s.kill());
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.lock().len()
    }
}

/// Читать псевдоконсоль до конца потока; куски — строками UTF-8.
fn pump_output(mut reader: Box<dyn Read + Send>, on_data: impl Fn(String)) {
    let mut chunker = Utf8Chunker::default();
    let mut buffer = vec![0u8; 16 * 1024];
    loop {
        match reader.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(read) => {
                let text = chunker.push(&buffer[..read]);
                if !text.is_empty() {
                    on_data(text);
                }
            }
        }
    }
    let tail = chunker.finish();
    if !tail.is_empty() {
        on_data(tail);
    }
}

/// Свой job object сессии. Не вышло — агент работает, но «Остановить» и
/// выход приложения погасят только сам процесс, без его детей.
#[cfg(windows)]
fn bind_to_job(child: &mut (dyn portable_pty::Child + Send + Sync)) -> Option<crate::engine::Job> {
    let job = crate::engine::Job::kill_on_close()?;
    let handle = child.as_raw_handle()?;
    if job.assign_handle(handle) {
        Some(job)
    } else {
        shell_log!("агент (pid {:?}) не привязан к job", child.process_id());
        None
    }
}

fn sessions() -> &'static Sessions {
    static SESSIONS: OnceLock<Sessions> = OnceLock::new();
    SESSIONS.get_or_init(Sessions::default)
}

/// Погасить все сессии агента (окно закрыто, перезагружено, выход).
pub fn kill_all() {
    sessions().kill_all();
}

// --- команды окна ---------------------------------------------------------------

#[derive(Clone, Serialize)]
struct AgentData {
    id: String,
    data: String,
}

#[derive(Clone, Serialize)]
struct AgentExit {
    id: String,
    code: Option<u32>,
}

fn main_only(window: &tauri::Window) -> Result<(), String> {
    if window.label() == MAIN {
        Ok(())
    } else {
        Err("агент доступен только в главном окне".into())
    }
}

/// Строка по пути `a.b` из JSON-ответа резидента.
fn text_at<'a>(value: &'a Value, path: &[&str]) -> Option<&'a str> {
    path.iter()
        .try_fold(value, |v, key| v.get(key))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
}

/// Всё, что нужно для запуска, — у резидента: путь к CLI, база знаний,
/// прокси, папка записи (и свежий transcript.md в ней).
fn prepare(recording: &str, provider: Provider, cols: u16, rows: u16) -> Result<SpawnSpec, String> {
    if !recording_id_valid(recording) {
        return Err("неизвестная запись".into());
    }
    let endpoint = resident::read_endpoint()
        .ok_or_else(|| "Сервис записи не отвечает — агент не может запуститься".to_string())?;
    let client = Client::new(&endpoint);
    let fail = |e: crate::api::Error| e.to_string();
    let assistant = client.get("/assistant").map_err(fail)?;
    let program = text_at(&assistant, &["available", provider.key(), "path"]).unwrap_or("");
    check_executable(provider, program, cfg!(windows))?;
    let program = PathBuf::from(program);
    if !program.is_file() {
        return Err(format!(
            "{} не найден. Подключите его в настройках",
            provider.title()
        ));
    }
    let knowledge = text_at(&assistant, &["knowledge_dir"]).map(str::to_string);
    let settings = client.get("/settings").map_err(fail)?;
    let mode = proxy_mode(text_at(&settings, &["llm", "proxy"]));
    let state = client.get_state().map_err(fail)?;
    let root = windows::recordings_root(&state)
        .ok_or_else(|| "Сервис записи не назвал папку записей".to_string())?;
    let context = client
        .post(
            &format!(
                "/recordings/{}/agent-context",
                windows::encode_component(recording)
            ),
            Value::Null,
        )
        .map_err(fail)?;
    let folder = text_at(&context, &["folder"]).ok_or("Сервис записи не назвал папку записи")?;
    let folder = recording_folder(Path::new(folder), &root).ok_or_else(|| {
        shell_log!("агент: отказ, папка вне папки записей: {folder}");
        "Папка записи вне папки записей — агент не запущен".to_string()
    })?;
    let folder_text = folder.to_string_lossy().into_owned();
    let env = agent_env(
        provider,
        &mode,
        &|name| std::env::var(name).ok(),
        &netproxy::read_internet_settings(),
    );
    Ok(SpawnSpec {
        recording: recording.to_string(),
        args: agent_args(provider, &folder_text, knowledge.as_deref()),
        program,
        cwd: folder,
        env,
        cols,
        rows,
    })
}

/// Запустить агента в папке записи. Возвращает id сессии; вывод — события
/// `agent-data`, конец — `agent-exit`.
#[tauri::command]
pub async fn agent_spawn(
    app: AppHandle,
    window: tauri::Window,
    recording_id: String,
    provider: String,
    cols: u16,
    rows: u16,
) -> Result<String, String> {
    main_only(&window)?;
    let provider = Provider::parse(&provider).ok_or("неизвестный агент")?;
    tauri::async_runtime::spawn_blocking(move || {
        let spec = prepare(&recording_id, provider, cols, rows)?;
        let data_app = app.clone();
        let on_data: DataSink = Box::new(move |id, data| {
            let id = id.to_string();
            let _ = data_app.emit_to(MAIN, "agent-data", AgentData { id, data });
        });
        let on_exit: ExitSink = Box::new(move |id, code| {
            sessions().forget(id);
            shell_log!("агент: сессия {id} завершилась (код {code:?})");
            let id = id.to_string();
            let _ = app.emit_to(MAIN, "agent-exit", AgentExit { id, code });
        });
        let id = sessions().spawn(spec, on_data, on_exit)?;
        shell_log!(
            "агент: {} запущен в записи {recording_id} ({id})",
            provider.title()
        );
        Ok(id)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
pub fn agent_write(window: tauri::Window, id: String, data: String) -> Result<(), String> {
    main_only(&window)?;
    sessions().write(&id, &data)
}

#[tauri::command]
pub fn agent_resize(window: tauri::Window, id: String, cols: u16, rows: u16) -> Result<(), String> {
    main_only(&window)?;
    sessions().resize(&id, cols, rows)
}

#[tauri::command]
pub fn agent_kill(window: tauri::Window, id: String) -> Result<(), String> {
    main_only(&window)?;
    sessions().kill(&id);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    fn env_of(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |name: &str| {
            pairs
                .iter()
                .find(|(k, _)| k.eq_ignore_ascii_case(name))
                .map(|(_, v)| v.to_string())
        }
    }

    fn wininet(server: Option<&str>) -> InternetSettings {
        InternetSettings {
            enabled: server.map(|_| 1),
            server: server.map(str::to_string),
            overrides: Some("*.corp.example;<local>".into()),
        }
    }

    #[test]
    fn provider_names_and_keys() {
        assert_eq!(Provider::parse("claude-code"), Some(Provider::Claude));
        assert_eq!(Provider::parse("codex"), Some(Provider::Codex));
        assert_eq!(Provider::parse("openai-compatible"), None);
        assert_eq!(Provider::Claude.key(), "claude-code");
    }

    #[test]
    fn only_native_executables_are_started_on_windows() {
        let claude = r"C:\Users\user\.local\bin\claude.exe";
        assert!(check_executable(Provider::Claude, claude, true).is_ok());
        assert!(check_executable(Provider::Claude, r"C:\npm\claude.cmd", true).is_err());
        assert!(check_executable(Provider::Claude, r"C:\npm\claude", true).is_err());
        assert!(check_executable(Provider::Codex, r"C:\Codex\bin\codex.EXE", true).is_ok());
        let shim = check_executable(Provider::Codex, r"C:\npm\codex.cmd", true).unwrap_err();
        assert!(shim.contains("codex.exe"), "{shim}");
        assert!(check_executable(Provider::Codex, "", true).is_err());
        assert!(check_executable(Provider::Codex, "/usr/bin/codex", false).is_ok());
    }

    #[test]
    fn claude_gets_the_prompt_and_the_knowledge_dir() {
        assert_eq!(
            agent_args(Provider::Claude, r"D:\rec\r1", Some(r"D:\kb")),
            vec![
                "--append-system-prompt",
                AGENT_PROMPT,
                "--add-dir",
                r"D:\kb"
            ]
        );
        assert_eq!(
            agent_args(Provider::Claude, r"D:\rec\r1", Some("  ")),
            vec!["--append-system-prompt", AGENT_PROMPT]
        );
        assert!(
            AGENT_PROMPT.contains("transcript.md") && AGENT_PROMPT.contains("только для чтения")
        );
    }

    #[test]
    fn codex_works_in_the_folder_and_never_gets_write_access_to_the_knowledge_dir() {
        let args = agent_args(Provider::Codex, r"D:\rec\r1", Some(r"D:\kb"));
        assert_eq!(args[..3], ["--cd", r"D:\rec\r1", "-c"]);
        assert!(!args.iter().any(|a| a == "--add-dir"));
        let value = args[3].strip_prefix("developer_instructions=").unwrap();
        let prompt: String = serde_json::from_str(value).unwrap();
        assert!(prompt.starts_with(AGENT_PROMPT));
        assert!(prompt.ends_with(r"Папка базы знаний: D:\kb"));
        assert_eq!(agent_args(Provider::Codex, "D:/r", None).len(), 4);
    }

    #[test]
    fn toml_string_escapes_quotes_and_backslashes() {
        assert_eq!(toml_string(r#"a "b" \c"#), r#""a \"b\" \\c""#);
    }

    #[test]
    fn proxy_setting_is_read_like_the_resident() {
        assert_eq!(proxy_mode(None), ProxyMode::System);
        assert_eq!(proxy_mode(Some("system")), ProxyMode::System);
        assert_eq!(proxy_mode(Some("none")), ProxyMode::None);
        assert_eq!(
            proxy_mode(Some("http://user:pw@10.0.0.1:3128/")),
            ProxyMode::Url("http://user:pw@10.0.0.1:3128".into())
        );
        assert_eq!(proxy_mode(Some("10.0.0.1:3128")), ProxyMode::System);
    }

    #[test]
    fn claude_loses_the_api_key_and_gets_a_terminal() {
        let plan = agent_env(
            Provider::Claude,
            &ProxyMode::System,
            &env_of(&[("ANTHROPIC_API_KEY", "sk-test")]),
            &wininet(None),
        );
        assert_eq!(plan.remove, vec!["ANTHROPIC_API_KEY"]);
        assert_eq!(plan.get("TERM"), Some("xterm-256color"));
        assert_eq!(plan.get("COLORTERM"), Some("truecolor"));
        assert_eq!(plan.get("HTTPS_PROXY"), None);
        assert_eq!(plan.get("NO_PROXY"), None);
        let codex = agent_env(
            Provider::Codex,
            &ProxyMode::System,
            &env_of(&[]),
            &wininet(None),
        );
        assert!(codex.remove.is_empty());
    }

    #[test]
    fn system_mode_takes_the_windows_proxy() {
        let plan = agent_env(
            Provider::Claude,
            &ProxyMode::System,
            &env_of(&[("no_proxy", "intranet")]),
            &wininet(Some("127.0.0.1:3067")),
        );
        assert_eq!(plan.get("HTTPS_PROXY"), Some("http://127.0.0.1:3067"));
        assert_eq!(plan.get("HTTP_PROXY"), Some("http://127.0.0.1:3067"));
        assert_eq!(
            plan.get("NO_PROXY"),
            Some("intranet,localhost,127.0.0.1,::1,.corp.example")
        );
    }

    #[test]
    fn system_mode_keeps_an_inherited_proxy_and_adds_loopback() {
        let plan = agent_env(
            Provider::Codex,
            &ProxyMode::System,
            &env_of(&[("HTTPS_PROXY", "http://env:8080")]),
            &wininet(Some("127.0.0.1:3067")),
        );
        assert_eq!(plan.get("HTTPS_PROXY"), None);
        assert_eq!(plan.get("NO_PROXY"), Some("localhost,127.0.0.1,::1"));
    }

    #[test]
    fn custom_proxy_wins_over_everything() {
        let plan = agent_env(
            Provider::Claude,
            &ProxyMode::Url("http://10.0.0.1:3128".into()),
            &env_of(&[
                ("HTTPS_PROXY", "http://env:8080"),
                ("ALL_PROXY", "socks5://x:1"),
            ]),
            &wininet(Some("127.0.0.1:3067")),
        );
        assert_eq!(plan.get("HTTPS_PROXY"), Some("http://10.0.0.1:3128"));
        assert_eq!(plan.get("HTTP_PROXY"), Some("http://10.0.0.1:3128"));
        assert_eq!(plan.get("ALL_PROXY"), Some("http://10.0.0.1:3128"));
        assert_eq!(plan.get("NO_PROXY"), Some("localhost,127.0.0.1,::1"));
    }

    #[test]
    fn none_mode_strips_inherited_proxy_variables() {
        let plan = agent_env(
            Provider::Codex,
            &ProxyMode::None,
            &env_of(&[("HTTPS_PROXY", "http://env:8080")]),
            &wininet(Some("127.0.0.1:3067")),
        );
        for name in ["HTTPS_PROXY", "http_proxy", "ALL_PROXY"] {
            assert!(plan.remove.iter().any(|r| r == name), "{name}");
        }
        assert_eq!(plan.get("HTTPS_PROXY"), None);
        assert_eq!(plan.get("NO_PROXY"), None);
    }

    #[test]
    fn chunker_keeps_multibyte_characters_split_across_reads() {
        let text = "Привет, мир — ✓ 😀";
        let bytes = text.as_bytes();
        for cut in 0..=bytes.len() {
            let mut chunker = Utf8Chunker::default();
            let mut out = chunker.push(&bytes[..cut]);
            out.push_str(&chunker.push(&bytes[cut..]));
            out.push_str(&chunker.finish());
            assert_eq!(out, text, "разрез на {cut}");
        }
        let mut chunker = Utf8Chunker::default();
        let mut out = String::new();
        for byte in bytes {
            out.push_str(&chunker.push(std::slice::from_ref(byte)));
        }
        assert_eq!(out, text);
    }

    #[test]
    fn chunker_replaces_broken_bytes_without_losing_the_rest() {
        let mut chunker = Utf8Chunker::default();
        assert_eq!(chunker.push(b"a\xffb\xd0"), "a\u{FFFD}b");
        assert_eq!(chunker.finish(), "\u{FFFD}");
        assert_eq!(chunker.push(b"ok"), "ok");
    }

    #[test]
    fn recording_ids_cannot_leave_the_folder() {
        assert!(recording_id_valid("2026-09-30_16-04"));
        for bad in ["", ".", "..", "a/b", r"a\b", "C:x", "a\0"] {
            assert!(!recording_id_valid(bad), "{bad:?}");
        }
    }

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir().join(format!("meet-pty-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&path);
            std::fs::create_dir_all(&path).unwrap();
            TempDir(path)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn recording_folder_must_sit_right_inside_the_root() {
        let tmp = TempDir::new("folder");
        let root = tmp.0.join("recordings");
        let rec = root.join("2026-09-30_16-04");
        let deep = rec.join("inner");
        let outside = tmp.0.join("other");
        for dir in [&deep, &outside] {
            std::fs::create_dir_all(dir).unwrap();
        }
        assert_eq!(recording_folder(&rec, &root), Some(rec.clone()));
        assert_eq!(recording_folder(&deep, &root), None);
        assert_eq!(recording_folder(&outside, &root), None);
        assert_eq!(
            recording_folder(&root.join("..").join("other"), &root),
            None
        );
        assert_eq!(recording_folder(&root, &root), None);
        assert_eq!(recording_folder(&root.join("missing"), &root), None);
        std::fs::write(root.join("file.txt"), "x").unwrap();
        assert_eq!(recording_folder(&root.join("file.txt"), &root), None);
    }

    #[test]
    fn size_is_clamped() {
        assert_eq!(clamp_size(0, 0), (MIN_COLS, MIN_ROWS));
        assert_eq!(clamp_size(120, 40), (120, 40));
        assert_eq!(clamp_size(u16::MAX, u16::MAX), (MAX_COLS, MAX_ROWS));
    }

    /// Терминал на нашей стороне: conhost при старте спрашивает позицию
    /// курсора (`ESC[6n`) и ждёт ответа — в окне отвечает xterm.js, здесь мы.
    /// Возвращает весь вывод и код выхода.
    #[cfg(windows)]
    fn run_terminal(
        sessions: &Sessions,
        id: &str,
        data: &mpsc::Receiver<String>,
        exit: &mpsc::Receiver<Option<u32>>,
    ) -> (String, Option<u32>) {
        let deadline = std::time::Instant::now() + Duration::from_secs(20);
        let mut output = String::new();
        loop {
            assert!(
                std::time::Instant::now() < deadline,
                "агент не завершился: {output:?}"
            );
            if let Ok(chunk) = data.recv_timeout(Duration::from_millis(50)) {
                if chunk.contains("[6n") {
                    let _ = sessions.write(id, "[1;1R");
                }
                output.push_str(&chunk);
            }
            if let Ok(code) = exit.try_recv() {
                output.extend(data.try_iter());
                return (output, code);
            }
        }
    }

    #[cfg(windows)]
    fn cmd_spec(recording: &str, cwd: &Path, line: &str, env: EnvPlan) -> SpawnSpec {
        SpawnSpec {
            recording: recording.into(),
            program: PathBuf::from(std::env::var("ComSpec").unwrap_or_else(|_| "cmd.exe".into())),
            args: vec!["/d".into(), "/c".into(), line.into()],
            cwd: cwd.to_path_buf(),
            env,
            cols: 80,
            rows: 24,
        }
    }

    #[cfg(windows)]
    fn spawn_cmd(
        sessions: &Sessions,
        spec: SpawnSpec,
    ) -> (String, mpsc::Receiver<String>, mpsc::Receiver<Option<u32>>) {
        let (data_tx, data_rx) = mpsc::channel();
        let (exit_tx, exit_rx) = mpsc::channel();
        let id = sessions
            .spawn(
                spec,
                Box::new(move |_, data| {
                    let _ = data_tx.send(data);
                }),
                Box::new(move |_, code| {
                    let _ = exit_tx.send(code);
                }),
            )
            .unwrap();
        (id, data_rx, exit_rx)
    }

    /// Настоящая псевдоконсоль: вывод дочернего процесса (с кириллицей)
    /// приходит событиями, рабочая папка — папка записи, конец — с кодом выхода.
    #[cfg(windows)]
    #[test]
    fn echo_runs_through_the_pseudo_console() {
        let tmp = TempDir::new("echo");
        let sessions = Sessions::default();
        let env = agent_env(
            Provider::Codex,
            &ProxyMode::None,
            &|_| None,
            &InternetSettings::default(),
        );
        let spec = cmd_spec(
            "r1",
            &tmp.0,
            "chcp 65001 >nul && echo привет-pty && cd",
            env,
        );
        let (id, data, exit) = spawn_cmd(&sessions, spec);
        assert!(id.starts_with("agent-"));
        let (output, code) = run_terminal(&sessions, &id, &data, &exit);
        assert_eq!(code, Some(0), "{output:?}");
        assert!(output.contains("привет-pty"), "{output:?}");
        let folder = tmp.0.file_name().unwrap().to_string_lossy().into_owned();
        assert!(output.contains(&folder), "cwd: {output:?}");
        sessions.forget(&id);
        assert_eq!(sessions.len(), 0);
    }

    /// «Остановить» гасит долгий процесс; новый запуск той же записи гасит прежний.
    #[cfg(windows)]
    #[test]
    fn kill_stops_a_running_session_and_a_second_spawn_replaces_the_first() {
        let tmp = TempDir::new("kill");
        let sessions = Sessions::default();
        let long = "ping -n 30 127.0.0.1 >nul";
        let (first, first_data, first_exit) =
            spawn_cmd(&sessions, cmd_spec("r1", &tmp.0, long, EnvPlan::default()));
        let (second, second_data, second_exit) =
            spawn_cmd(&sessions, cmd_spec("r1", &tmp.0, long, EnvPlan::default()));
        assert_ne!(first, second);
        let (_, code) = run_terminal(&sessions, &first, &first_data, &first_exit);
        assert_ne!(code, Some(0));
        assert_eq!(sessions.len(), 1);
        sessions.kill(&second);
        let (_, code) = run_terminal(&sessions, &second, &second_data, &second_exit);
        assert_ne!(code, Some(0));
        assert_eq!(sessions.len(), 0);
        assert!(sessions.write(&second, "x").is_err());
    }
}
