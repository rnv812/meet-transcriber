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
use std::sync::{mpsc, Arc, Condvar, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

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
transcript.md — расшифровка с именами и таймкодами, summary.md — итоги (если есть), \
analysis.json — разметка встречи (если есть): типы и важность реплик, главы, наблюдения, \
категория и название; номера реплик в ней (ключи phrase_types и importance, start_i и end_i \
глав, refs наблюдений) — номера сегментов в transcript.json по порядку, с нуля. \
База знаний (если подключена) — только для чтения; не изменяй её файлы.";

/// Переменные прокси, которые понимают Claude Code и Codex (регистр любой).
const PROXY_VARS: [&str; 3] = ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"];

/// Метки чужого сеанса Claude Code / Codex, унаследованные оболочкой (её
/// запустили из терминала агента, из его команды и т. п.). Агенту во вкладке
/// они вредят: с `CLAUDE_CODE_CHILD_SESSION` Claude Code считает себя
/// вложенным и не сохраняет сеанс («Transcript saving is off — inherited
/// CLAUDE_CODE_CHILD_SESSION marker»), и `--continue` потом нечего продолжать.
///
/// Имена — из самих программ (строки claude.exe 2.1.x и codex.exe 0.159,
/// 2026-10): это ровно то, что Claude Code выставляет своим дочерним командам
/// (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`,
/// `CLAUDE_CODE_SESSION_ATTENDED`, `CLAUDE_PID`, `CLAUDE_EFFORT`, `AI_AGENT`) и
/// сам же убирает, запуская независимый сеанс; связь с родителем (канал
/// сообщений с токеном, порт IDE, путь к его программе, точка входа) и метка
/// песочницы Codex. Настройки и вход (`ANTHROPIC_*`, `CLAUDE_CONFIG_DIR`,
/// `CODEX_HOME`, прокси) не трогаем. Сохранение сеансов ничем не выключаем.
pub const SESSION_MARKERS: [&str; 18] = [
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_EVAL_INTERVIEW_SESSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "AI_AGENT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
];

/// Размер терминала: ConPTY не принимает нулевой, а огромный — признак
/// ошибки окна, не реального экрана.
const MIN_COLS: u16 = 20;
const MAX_COLS: u16 = 500;
const MIN_ROWS: u16 = 5;
const MAX_ROWS: u16 = 300;

/// Сколько ждать после выхода агента, прежде чем закрыть псевдоконсоль:
/// conhost успевает отдать последний кадр (например, вывод `--version`).
const EXIT_GRACE: Duration = Duration::from_millis(200);

/// Сколько «Удалить» и «Объединить» ждут, пока агенты записи выйдут: агент
/// работает в папке записи, а Windows не удаляет папку, которая чья-то рабочая.
pub const KILL_WAIT: Duration = Duration::from_secs(2);

/// Ошибка, когда служба записи не отвечает.
const NO_RESIDENT: &str = "Служба записи не отвечает — агент не может запуститься";

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

/// Какой сеанс агента запустить.
///
/// Claude Code: новый сеанс — с нашим id (`--session-id <uuid>`), его
/// резидент кладёт в метку meta.json; «Продолжить прошлую» — `--resume
/// <uuid>` именно этого сеанса. `--continue` («последний разговор в папке»)
/// — только когда id неизвестен (метка ранней сборки): последним мог быть
/// чужой сеанс. Codex своего id при запуске не принимает, а выданный пишет
/// только в своё хранилище (его не читаем) — у него «Продолжить» — `codex
/// resume --last`: отбор по рабочей папке и только интерактивные сеансы;
/// фоновые вызовы Codex — `exec --ephemeral` и в этот отбор не попадают.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentSession {
    /// Новый сеанс без нашего id (Codex; свой `--session-id` в параметрах).
    Fresh,
    /// Новый сеанс Claude с этим id.
    New(String),
    /// Продолжить сеанс Claude с этим id.
    Resume(String),
    /// Продолжить последний: Claude `--continue`, Codex `resume --last`.
    ResumeLast,
}

/// Аргументы агента. Claude: база знаний через `--add-dir` и промпт. Codex:
/// `--cd` и промпт в `developer_instructions`; `--add-dir` у Codex делает
/// папку доступной на запись, поэтому базу знаний ему только называем
/// (читать файлы вне рабочей папки песочница Codex и так разрешает).
///
/// `session` — какой сеанс (см. `AgentSession`). Сверено с `claude --help` и
/// `codex resume --help` (2.1.287, 0.159.0).
///
/// `--add-dir` у Claude принимает несколько папок подряд, поэтому он идёт
/// раньше `--append-system-prompt`: следующий за ним аргумент не станет
/// «ещё одной папкой».
pub fn agent_args(
    provider: Provider,
    folder: &str,
    knowledge: Option<&str>,
    session: &AgentSession,
) -> Vec<String> {
    let knowledge = knowledge.map(str::trim).filter(|k| !k.is_empty());
    let resume = matches!(session, AgentSession::Resume(_) | AgentSession::ResumeLast);
    match provider {
        Provider::Claude => {
            let mut args = Vec::new();
            match session {
                AgentSession::Fresh => {}
                AgentSession::New(id) => args.extend(["--session-id".to_string(), id.clone()]),
                AgentSession::Resume(id) => args.extend(["--resume".to_string(), id.clone()]),
                AgentSession::ResumeLast => args.push("--continue".to_string()),
            }
            if let Some(dir) = knowledge {
                args.extend(["--add-dir".to_string(), dir.to_string()]);
            }
            args.extend(["--append-system-prompt".to_string(), AGENT_PROMPT.into()]);
            args
        }
        Provider::Codex => {
            let mut prompt = AGENT_PROMPT.to_string();
            if let Some(dir) = knowledge {
                prompt.push_str(&format!(" Папка базы знаний: {dir}"));
            }
            let mut args = Vec::new();
            if resume {
                args.extend(["resume".to_string(), "--last".to_string()]);
            }
            args.extend([
                "--cd".into(),
                folder.to_string(),
                "-c".into(),
                format!("developer_instructions={}", toml_string(&prompt)),
            ]);
            args
        }
    }
}

/// Строка TOML (значение `-c key=value` у Codex): JSON-строка — она же
/// корректная базовая строка TOML (те же экранирования `\"`, `\\`, `\n`, `\uXXXX`).
fn toml_string(text: &str) -> String {
    serde_json::to_string(text).unwrap_or_else(|_| "\"\"".into())
}

// --- свои параметры запуска (Настройки → Ассистент → «Запуск агента») -----------

pub const ARGS_CONTROL: &str = "Недопустимый управляющий символ в параметрах запуска";
pub const ARGS_QUOTE: &str = "Незакрытая кавычка в параметрах запуска (обратная косая черта перед \
кавычкой \\\" считается частью текста — уберите её в конце пути)";

/// Строка «Дополнительные параметры» → отдельные аргументы, без командной
/// оболочки. Правила — как у резидента (`meet.agent_launch`) и окна
/// (`lib/agentLaunch.ts`): разделители — пробел и табуляция; «"…"» и «'…'»
/// объединяют текст и примыкают к соседнему; обратная косая черта — обычный
/// символ (пути Windows), только внутри двойных кавычек `\"` — сама кавычка;
/// управляющие символы недопустимы.
pub fn parse_launch_args(text: &str) -> Result<Vec<String>, String> {
    let control = |c: char| (c as u32) < 0x20 || c as u32 == 0x7f;
    let mut args = Vec::new();
    let mut cur: Option<String> = None;
    let mut quote: Option<char> = None;
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if control(ch) && !(ch == '\t' && quote.is_none()) {
            return Err(ARGS_CONTROL.into());
        }
        match quote {
            None => match ch {
                ' ' | '\t' => {
                    if let Some(arg) = cur.take() {
                        args.push(arg);
                    }
                }
                '"' | '\'' => {
                    quote = Some(ch);
                    cur.get_or_insert_with(String::new);
                }
                _ => cur.get_or_insert_with(String::new).push(ch),
            },
            Some('\'') => {
                if ch == '\'' {
                    quote = None;
                } else {
                    cur.get_or_insert_with(String::new).push(ch);
                }
            }
            Some(_) => {
                if ch == '\\' && chars.peek() == Some(&'"') {
                    chars.next();
                    cur.get_or_insert_with(String::new).push('"');
                } else if ch == '"' {
                    quote = None;
                } else {
                    cur.get_or_insert_with(String::new).push(ch);
                }
            }
        }
    }
    if quote.is_some() {
        return Err(ARGS_QUOTE.into());
    }
    args.extend(cur);
    Ok(args)
}

/// Годное имя переменной окружения: латинские буквы, цифры и `_`, не с цифры.
pub fn env_name_valid(key: &str) -> bool {
    let mut chars = key.chars();
    chars
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Свои параметры запуска агента из настроек (`agent.launch.<провайдер>`).
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Launch {
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
}

/// `agent.launch.<провайдер>` из ответа GET /settings. Резидент хранит только
/// проверенное; здесь — проверка ещё раз: негодные параметры — ошибка запуска
/// с объяснением, негодные переменные пропускаются.
pub fn launch_from_settings(settings: &Value, provider: Provider) -> Result<Launch, String> {
    let item = settings
        .get("agent")
        .and_then(|a| a.get("launch"))
        .and_then(|l| l.get(provider.key()));
    let Some(item) = item else {
        return Ok(Launch::default());
    };
    let args = item.get("args").and_then(Value::as_str).unwrap_or("");
    let args = parse_launch_args(args)
        .map_err(|e| format!("Параметры запуска {}: {e}", provider.title()))?;
    let env = item
        .get("env")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .filter_map(|e| {
                    let key = e.get("key")?.as_str()?;
                    let value = e.get("value").and_then(Value::as_str).unwrap_or("");
                    let clean = !value.chars().any(|c| (c as u32) < 0x20 || c as u32 == 0x7f);
                    (env_name_valid(key) && clean).then(|| (key.to_string(), value.to_string()))
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(Launch { args, env })
}

/// Флаги, которые есть у нас и у человека: его вариант остаётся, наш — убираем
/// (повтор флага Codex считает ошибкой, а у Claude он лишний).
fn user_has(user: &[String], names: &[&str]) -> bool {
    user.iter().any(|arg| {
        names.iter().any(|name| {
            arg == name || (name.starts_with("--") && arg.starts_with(&format!("{name}=")))
        })
    })
}

/// Сеанс до ответа резидента: новый сеанс Claude — с новым id (`new_id`),
/// «Продолжить» — пока «последний» (id даст резидент, см. `resolved_session`).
/// Свой выбор сеанса в параметрах Claude (`CLAUDE_SESSION_FLAGS`) — наш не нужен.
pub fn planned_session(
    provider: Provider,
    resume: bool,
    user: &[String],
    new_id: impl FnOnce() -> String,
) -> AgentSession {
    match (provider, resume) {
        (Provider::Claude, _) if user_has(user, &CLAUDE_SESSION_FLAGS) => AgentSession::Fresh,
        (Provider::Claude, false) => AgentSession::New(new_id()),
        (_, true) => AgentSession::ResumeLast,
        (Provider::Codex, false) => AgentSession::Fresh,
    }
}

/// После ответа резидента: «Продолжить» Claude — сеанс с известным id; id нет
/// (метка ранней сборки) — `--continue`.
pub fn resolved_session(
    planned: AgentSession,
    provider: Provider,
    known: Option<&str>,
) -> AgentSession {
    match (planned, provider, known) {
        (AgentSession::ResumeLast, Provider::Claude, Some(id)) => {
            AgentSession::Resume(id.to_string())
        }
        (planned, _, _) => planned,
    }
}

/// Свои параметры, которые сами выбирают сеанс Claude: с ними наш выбор
/// (`--session-id`, `--resume`, `--continue`) не передаётся.
pub const CLAUDE_SESSION_FLAGS: [&str; 5] = ["--continue", "-c", "--resume", "-r", "--session-id"];

/// Убрать из `args` флаг `name` вместе с его значением (если `valued`).
fn drop_flag(args: &mut Vec<String>, name: &str, valued: bool) {
    if let Some(at) = args.iter().position(|a| a == name) {
        let end = if valued { at + 2 } else { at + 1 };
        args.drain(at..end.min(args.len()));
    }
}

/// Наши аргументы + свои из настроек. Свои идут после наших — у параметра,
/// заданного дважды, действует последнее значение, то есть своё. Наш
/// дубликат убирается, если повтор был бы ошибкой или лишним: Claude —
/// наш выбор сеанса, когда человек сам задал `--continue`/`-c`/`--resume`/
/// `-r`/`--session-id`; Codex — `--last` (при «Продолжить») и `--cd`, когда
/// они есть у человека. Папка запуска, подсказка о встрече и `resume` Codex
/// остаются.
pub fn with_user_args(provider: Provider, ours: Vec<String>, user: &[String]) -> Vec<String> {
    let mut args = ours;
    match provider {
        Provider::Claude => {
            if user_has(user, &CLAUDE_SESSION_FLAGS) {
                drop_flag(&mut args, "--continue", false);
                drop_flag(&mut args, "--resume", true);
                drop_flag(&mut args, "--session-id", true);
            }
        }
        Provider::Codex => {
            if user_has(user, &["--last"]) {
                args.retain(|a| a != "--last");
            }
            if user_has(user, &["--cd", "-C"]) {
                drop_flag(&mut args, "--cd", true);
            }
        }
    }
    args.extend(user.iter().cloned());
    args
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
/// оболочки без учёта регистра). Без меток чужого сеанса (`SESSION_MARKERS`);
/// Claude без ANTHROPIC_API_KEY (подписка
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
    // Метки чужого сеанса — у обоих агентов (см. SESSION_MARKERS).
    plan.remove
        .extend(SESSION_MARKERS.iter().map(|name| name.to_string()));
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

/// Команда запуска: программа, аргументы, папка и окружение — унаследованное
/// окружение оболочки без `env.remove` (без учёта регистра на Windows), затем
/// `env.set` по порядку: позднее перекрывает раннее.
pub fn command_for(spec: &SpawnSpec) -> CommandBuilder {
    let mut command = CommandBuilder::new(&spec.program);
    command.args(&spec.args);
    command.cwd(&spec.cwd);
    for key in &spec.env.remove {
        command.env_remove(key);
    }
    for (key, value) in &spec.env.set {
        command.env(key, value);
    }
    command
}

/// «Сеанс закончился»: агент вышел, псевдоконсоль закрыта, его job закрыт.
#[derive(Default)]
struct Exited {
    done: Mutex<bool>,
    changed: Condvar,
}

impl Exited {
    fn set(&self) {
        let mut done = self.done.lock().unwrap_or_else(|p| p.into_inner());
        *done = true;
        self.changed.notify_all();
    }

    /// Дождаться конца сеанса до `deadline`; false — не дождались.
    fn wait_until(&self, deadline: Instant) -> bool {
        let mut done = self.done.lock().unwrap_or_else(|p| p.into_inner());
        while !*done {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return false;
            }
            done = self
                .changed
                .wait_timeout(done, left)
                .unwrap_or_else(|p| p.into_inner())
                .0;
        }
        true
    }
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
    /// macOS: portable-pty запускает агента лидером новой сессии (setsid) —
    /// его группа процессов и есть «агент со всем, что он запустил».
    #[cfg(unix)]
    group: Option<u32>,
    exited: Exited,
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
        #[cfg(unix)]
        if let Some(group) = self.group {
            crate::platform::kill_group(group, false);
        }
    }
}

/// Кусок вывода сессии (id, текст).
pub type DataSink = Box<dyn Fn(&str, String) + Send + 'static>;
/// Конец сессии (id, код выхода).
pub type ExitSink = Box<dyn FnOnce(&str, Option<u32>) + Send + 'static>;

type SessionMap = Arc<Mutex<HashMap<String, Arc<Session>>>>;

fn lock_map(map: &SessionMap) -> std::sync::MutexGuard<'_, HashMap<String, Arc<Session>>> {
    map.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Сеансы агента. Сеанс в списке, пока его процесс не вышел: «Остановить» и
/// замена сеанса его гасят, а убирает из списка поток ожидания — так
/// `kill_recording_wait` видит и уже погашенные, но ещё не вышедшие.
#[derive(Default)]
pub struct Sessions {
    map: SessionMap,
    next: AtomicU64,
    /// Запуск целиком (погасить прежний сеанс записи → запустить → внести в
    /// список) — под одним замком: два запуска одной записи не разойдутся.
    spawning: Mutex<()>,
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
        let _spawning = self
            .spawning
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
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
        let mut child = pair
            .slave
            .spawn_command(command_for(&spec))
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
            #[cfg(unix)]
            group: child.process_id(),
            exited: Exited::default(),
        });

        let pump_id = id.clone();
        let pump = match thread::Builder::new()
            .name(format!("{id}-read"))
            .spawn(move || pump_output(reader, |text| on_data(&pump_id, text)))
        {
            Ok(pump) => pump,
            Err(e) => {
                session.kill();
                return Err(format!("поток чтения агента не запустился: {e}"));
            }
        };
        // Поток ожидания убирает сеанс из списка и сообщает о конце только
        // после того, как запуск внёс его туда: агент, вышедший мгновенно, не
        // оставит в списке мёртвую запись.
        let (registered, wait_registered) = mpsc::channel::<()>();
        let waiter_id = id.clone();
        let waiter_session = session.clone();
        let map = self.map.clone();
        let waiter = thread::Builder::new()
            .name(format!("{id}-wait"))
            .spawn(move || {
                let session = waiter_session;
                let code = child.wait().ok().map(|status| status.exit_code());
                thread::sleep(EXIT_GRACE);
                // Ввод закрыт: conhost, который ещё ждёт от терминала ответа о
                // позиции курсора (агента убили до ответа), иначе не завершился
                // бы. Псевдоконсоль закрыта — читатель дочитывает и выходит.
                // Закрываем вне замков сеанса: ClosePseudoConsole может ждать,
                // а запись и смена размера не должны висеть на нём.
                let writer = session.writer.lock().ok().and_then(|mut w| w.take());
                drop(writer);
                let master = session.master.lock().ok().and_then(|mut m| m.take());
                drop(master);
                let _ = pump.join();
                // Агент вышел сам — его оставшиеся дети уходят вместе с job.
                session.kill();
                let _ = wait_registered.recv();
                lock_map(&map).remove(&waiter_id);
                session.exited.set();
                drop(session);
                on_exit(&waiter_id, code);
            });
        if let Err(e) = waiter {
            session.kill();
            return Err(format!("поток ожидания агента не запустился: {e}"));
        }
        self.lock().insert(id.clone(), session);
        let _ = registered.send(());
        Ok(id)
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, Arc<Session>>> {
        lock_map(&self.map)
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

    /// «Остановить». Сессии уже нет — не ошибка. Из списка сеанс уберёт
    /// поток ожидания, когда процесс выйдет.
    pub fn kill(&self, id: &str) {
        let session = self.lock().get(id).cloned();
        if let Some(session) = session {
            session.kill();
        }
    }

    /// Сеансы записи (в том числе погашенные, но ещё не вышедшие).
    fn of_recording(&self, recording: &str) -> Vec<Arc<Session>> {
        self.lock()
            .values()
            .filter(|s| s.recording == recording)
            .cloned()
            .collect()
    }

    fn kill_recording(&self, recording: &str) {
        self.of_recording(recording).iter().for_each(|s| s.kill());
    }

    /// Погасить агентов записи и дождаться, пока они выйдут (до `timeout`):
    /// папку записи, которая рабочая у живого процесса, Windows не удалит.
    /// false — кто-то не вышел за отведённое время.
    pub fn kill_recording_wait(&self, recording: &str, timeout: Duration) -> bool {
        let sessions = self.of_recording(recording);
        sessions.iter().for_each(|s| s.kill());
        let deadline = Instant::now() + timeout;
        sessions.iter().all(|s| s.exited.wait_until(deadline))
    }

    /// Закрыто главное окно или выход из приложения — гасим всех.
    pub fn kill_all(&self) {
        let all: Vec<Arc<Session>> = self.lock().values().cloned().collect();
        if !all.is_empty() {
            shell_log!("агент: остановлено сессий: {}", all.len());
        }
        all.iter().for_each(|s| s.kill());
    }

    #[cfg(all(test, windows))]
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
/// прокси, папка записи (и свежий transcript.md в ней). Резидент заодно
/// помечает в meta.json записи, что в её папке работал этот агент: по метке
/// окно предлагает «Продолжить прошлую» (хранилище самих CLI не читаем).
fn prepare(
    recording: &str,
    provider: Provider,
    resume: bool,
    cols: u16,
    rows: u16,
) -> Result<SpawnSpec, String> {
    if !recording_id_valid(recording) {
        return Err("неизвестная запись".into());
    }
    let endpoint = resident::read_endpoint().ok_or_else(|| NO_RESIDENT.to_string())?;
    let client = Client::new(&endpoint);
    let fail = |e: crate::api::Error| e.to_string();
    // Без проверки локальной модели: агенту нужны только пути к CLI, а
    // проверка — сетевое соединение, которое задержало бы запуск.
    let assistant = client.get("/assistant?local=0").map_err(fail)?;
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
    // Свои параметры — до записи файлов и метки: негодные не запускают агента.
    let launch = launch_from_settings(&settings, provider)?;
    let state = client.get_state().map_err(fail)?;
    let root = windows::recordings_root(&state)
        .ok_or_else(|| "Служба записи не назвала папку записей".to_string())?;
    let planned = planned_session(provider, resume, &launch.args, || {
        uuid::Uuid::new_v4().to_string()
    });
    let new_id = match &planned {
        AgentSession::New(id) => Some(id.clone()),
        _ => None,
    };
    let context = client
        .post(
            &format!(
                "/recordings/{}/agent-context",
                windows::encode_component(recording)
            ),
            serde_json::json!({ "provider": provider.key(), "session": new_id, "resume": resume }),
        )
        .map_err(fail)?;
    let session = resolved_session(planned, provider, text_at(&context, &["session"]));
    let folder = text_at(&context, &["folder"]).ok_or("Служба записи не назвала папку записи")?;
    let folder = recording_folder(Path::new(folder), &root).ok_or_else(|| {
        shell_log!("агент: отказ, папка вне папки записей: {folder}");
        "Папка записи вне папки записей — агент не запущен".to_string()
    })?;
    let folder_text = folder.to_string_lossy().into_owned();
    let mut env = agent_env(
        provider,
        &mode,
        &|name| std::env::var(name).ok(),
        &netproxy::read_internet_settings(),
    );
    // Свои переменные — последними: они перекрывают и очистку меток, и наши.
    env.set.extend(launch.env);
    let ours = agent_args(provider, &folder_text, knowledge.as_deref(), &session);
    Ok(SpawnSpec {
        recording: recording.to_string(),
        args: with_user_args(provider, ours, &launch.args),
        program,
        cwd: folder,
        env,
        cols,
        rows,
    })
}

/// Запустить агента в папке записи. Возвращает id сессии; вывод — события
/// `agent-data`, конец — `agent-exit`. `resume` — «Продолжить прошлую»
/// (см. `agent_args`); нет — новый сеанс.
#[tauri::command]
pub async fn agent_spawn(
    app: AppHandle,
    window: tauri::Window,
    recording_id: String,
    provider: String,
    cols: u16,
    rows: u16,
    resume: Option<bool>,
) -> Result<String, String> {
    main_only(&window)?;
    let provider = Provider::parse(&provider).ok_or("неизвестный агент")?;
    let resume = resume.unwrap_or(false);
    tauri::async_runtime::spawn_blocking(move || {
        let spec = prepare(&recording_id, provider, resume, cols, rows)?;
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
            "агент: {} запущен в записи {recording_id} ({id}{})",
            provider.title(),
            if resume {
                ", продолжение"
            } else {
                ""
            }
        );
        Ok(id)
    })
    .await
    .map_err(|e| e.to_string())?
}

// Ввод и размер — не в главном потоке: запись в псевдоконсоль может ждать
// (conhost занят), и окно не должно замирать вместе с ней.
#[tauri::command(async)]
pub fn agent_write(window: tauri::Window, id: String, data: String) -> Result<(), String> {
    main_only(&window)?;
    sessions().write(&id, &data)
}

#[tauri::command(async)]
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

/// Перед «Удалить» и «Объединить»: погасить агентов записи и дождаться (до
/// 2 с), пока они выйдут, — иначе их рабочая папка не даст удалить запись.
#[tauri::command]
pub async fn agent_kill_recording(
    window: tauri::Window,
    recording_id: String,
) -> Result<(), String> {
    main_only(&window)?;
    if !recording_id_valid(&recording_id) {
        return Err("неизвестная запись".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        if sessions().kill_recording_wait(&recording_id, KILL_WAIT) {
            Ok(())
        } else {
            shell_log!("агент записи {recording_id} не вышел за {KILL_WAIT:?}");
            Err("Агент не завершился за 2 секунды".to_string())
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(windows)]
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
            agent_args(
                Provider::Claude,
                r"D:\rec\r1",
                Some(r"D:\kb"),
                &AgentSession::Fresh
            ),
            vec![
                "--add-dir",
                r"D:\kb",
                "--append-system-prompt",
                AGENT_PROMPT,
            ]
        );
        assert_eq!(
            agent_args(
                Provider::Claude,
                r"D:\rec\r1",
                Some("  "),
                &AgentSession::Fresh
            ),
            vec!["--append-system-prompt", AGENT_PROMPT]
        );
        assert!(
            AGENT_PROMPT.contains("transcript.md") && AGENT_PROMPT.contains("только для чтения")
        );
        // Разметка анализа встречи: агент знает, что номера реплик — порядок в расшифровке.
        assert!(AGENT_PROMPT.contains("analysis.json") && AGENT_PROMPT.contains("номера реплик"));
    }

    #[test]
    fn codex_works_in_the_folder_and_never_gets_write_access_to_the_knowledge_dir() {
        let args = agent_args(
            Provider::Codex,
            r"D:\rec\r1",
            Some(r"D:\kb"),
            &AgentSession::Fresh,
        );
        assert_eq!(args[..3], ["--cd", r"D:\rec\r1", "-c"]);
        assert!(!args.iter().any(|a| a == "--add-dir"));
        let value = args[3].strip_prefix("developer_instructions=").unwrap();
        let prompt: String = serde_json::from_str(value).unwrap();
        assert!(prompt.starts_with(AGENT_PROMPT));
        assert!(prompt.ends_with(r"Папка базы знаний: D:\kb"));
        assert_eq!(
            agent_args(Provider::Codex, "D:/r", None, &AgentSession::Fresh).len(),
            4
        );
    }

    /// «Продолжить прошлую»: Claude — `--continue` первым, Codex — подкоманда
    /// `resume --last` (подкоманда — первой), остальное как у нового сеанса.
    #[test]
    fn resume_continues_the_last_session_in_the_folder() {
        let claude = agent_args(
            Provider::Claude,
            r"D:\rec\r1",
            Some(r"D:\kb"),
            &AgentSession::ResumeLast,
        );
        assert_eq!(claude[0], "--continue");
        assert_eq!(
            claude[1..],
            agent_args(
                Provider::Claude,
                r"D:\rec\r1",
                Some(r"D:\kb"),
                &AgentSession::Fresh
            )[..]
        );
        let codex = agent_args(
            Provider::Codex,
            r"D:\rec\r1",
            None,
            &AgentSession::ResumeLast,
        );
        assert_eq!(codex[..4], ["resume", "--last", "--cd", r"D:\rec\r1"]);
        assert_eq!(
            codex[2..],
            agent_args(Provider::Codex, r"D:\rec\r1", None, &AgentSession::Fresh)[..]
        );
    }

    /// Переменные процесса тестов общие: тесты, что их меняют, идут по одному
    /// и по окончании возвращают прежние значения (и при падении — в Drop).
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    struct EnvGuard {
        saved: Vec<(String, Option<std::ffi::OsString>)>,
        _lock: std::sync::MutexGuard<'static, ()>,
    }

    impl EnvGuard {
        fn set(pairs: &[(&str, &str)]) -> EnvGuard {
            let lock = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
            let saved = pairs
                .iter()
                .map(|(key, _)| (key.to_string(), std::env::var_os(key)))
                .collect();
            for (key, value) in pairs {
                std::env::set_var(key, value);
            }
            EnvGuard { saved, _lock: lock }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            for (key, value) in &self.saved {
                match value {
                    Some(value) => std::env::set_var(key, value),
                    None => std::env::remove_var(key),
                }
            }
        }
    }

    /// Оболочку запустили из сеанса Claude Code: его метки агенту не
    /// достаются (иначе Claude не сохранит сеанс), вход и настройки — да.
    #[test]
    fn inherited_session_markers_are_scrubbed_but_auth_and_config_stay() {
        let parent = [
            ("CLAUDE_CODE_CHILD_SESSION", "1"),
            ("CLAUDECODE", "1"),
            ("CLAUDE_CODE_ENTRYPOINT", "cli"),
            ("CLAUDE_CODE_SSE_PORT", "45123"),
            (
                "CLAUDE_CODE_SESSION_ID",
                "11111111-2222-3333-4444-555555555555",
            ),
            ("CLAUDE_CODE_MESSAGING_TOKEN", "m9-test-token"),
            ("CODEX_SANDBOX_NETWORK_DISABLED", "1"),
            ("ANTHROPIC_BASE_URL", "https://gateway.example.invalid"),
            ("CLAUDE_CONFIG_DIR", r"D:\m9-test\claude"),
            ("CODEX_HOME", r"D:\m9-test\codex"),
        ];
        let _env = EnvGuard::set(&parent);
        for provider in [Provider::Claude, Provider::Codex] {
            let plan = agent_env(provider, &ProxyMode::None, &|_| None, &wininet(None));
            let spec = SpawnSpec {
                recording: "r1".into(),
                program: PathBuf::from("agent.exe"),
                args: Vec::new(),
                cwd: std::env::temp_dir(),
                env: plan,
                cols: 80,
                rows: 24,
            };
            let command = command_for(&spec);
            for marker in SESSION_MARKERS {
                assert_eq!(command.get_env(marker), None, "{marker}");
            }
            assert_eq!(
                command.get_env("ANTHROPIC_BASE_URL"),
                Some(std::ffi::OsStr::new("https://gateway.example.invalid"))
            );
            assert!(command.get_env("CLAUDE_CONFIG_DIR").is_some());
            assert!(command.get_env("CODEX_HOME").is_some());
            assert_eq!(
                command.get_env("TERM"),
                Some(std::ffi::OsStr::new("xterm-256color"))
            );
        }
        // Ничего, что выключало бы сохранение, не выставляется.
        let plan = agent_env(
            Provider::Claude,
            &ProxyMode::System,
            &|_| None,
            &wininet(None),
        );
        assert!(!plan.set.iter().any(|(k, _)| k.contains("PERSIST")
            || k.contains("SKIP_PROMPT_HISTORY")
            || k.contains("SESSION")));
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
        assert_eq!(plan.remove[0], "ANTHROPIC_API_KEY");
        assert_eq!(plan.remove[1..], SESSION_MARKERS);
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
        assert_eq!(codex.remove, SESSION_MARKERS);
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

    /// «Удалить» запись: агенты в её папке гасятся, и вызов ждёт их выхода —
    /// после него папку можно удалить (рабочая папка больше ничья).
    #[cfg(windows)]
    #[test]
    fn kill_recording_wait_returns_once_the_folder_is_free() {
        let tmp = TempDir::new("wait");
        let folder = tmp.0.join("2026-09-30_10-00");
        std::fs::create_dir_all(&folder).unwrap();
        let sessions = Sessions::default();
        let long = "ping -n 30 127.0.0.1 >nul";
        let (id, data, exit) =
            spawn_cmd(&sessions, cmd_spec("r1", &folder, long, EnvPlan::default()));
        // Терминал отвечает на запрос позиции курсора — агент реально запущен.
        let answer = {
            let deadline = Instant::now() + Duration::from_secs(10);
            let mut seen = String::new();
            while Instant::now() < deadline && !seen.contains("[6n") {
                if let Ok(chunk) = data.recv_timeout(Duration::from_millis(50)) {
                    seen.push_str(&chunk);
                }
            }
            seen.contains("[6n")
        };
        if answer {
            let _ = sessions.write(&id, "\u{1b}[1;1R");
        }
        thread::sleep(Duration::from_millis(300));
        assert!(
            std::fs::rename(&folder, tmp.0.join("moved")).is_err(),
            "папка занята агентом"
        );
        assert!(sessions.kill_recording_wait("r1", KILL_WAIT));
        assert!(exit.recv_timeout(Duration::from_secs(5)).is_ok());
        assert_eq!(sessions.len(), 0);
        assert!(
            sessions.kill_recording_wait("r1", KILL_WAIT),
            "нечего ждать — сразу"
        );
        std::fs::rename(&folder, tmp.0.join("moved")).expect("папка свободна");
    }

    #[test]
    fn exited_wait_times_out_and_wakes_up() {
        let exited = Arc::new(Exited::default());
        assert!(!exited.wait_until(Instant::now() + Duration::from_millis(30)));
        let setter = exited.clone();
        let handle = thread::spawn(move || {
            thread::sleep(Duration::from_millis(50));
            setter.set();
        });
        assert!(exited.wait_until(Instant::now() + Duration::from_secs(5)));
        handle.join().unwrap();
        assert!(exited.wait_until(Instant::now()));
    }

    #[test]
    fn resident_errors_say_sluzhba() {
        assert!(NO_RESIDENT.starts_with("Служба записи"));
    }

    /// Те же примеры, что у резидента (tests/test_agent_launch.py) и окна
    /// (lib/agentLaunch.test.ts): правила разбора совпадают.
    #[test]
    fn launch_args_parse_like_the_resident_and_the_window() {
        let cases: &[(&str, &[&str])] = &[
            ("", &[]),
            ("   ", &[]),
            ("--model opus", &["--model", "opus"]),
            (
                "--permission-mode  acceptEdits\t--verbose",
                &["--permission-mode", "acceptEdits", "--verbose"],
            ),
            (r"--add-dir D:\Docs", &["--add-dir", r"D:\Docs"]),
            (
                r#"--add-dir "D:\Мои документы\База""#,
                &["--add-dir", r"D:\Мои документы\База"],
            ),
            (
                r"--add-dir \\server\share\kb",
                &["--add-dir", r"\\server\share\kb"],
            ),
            (r#""\\server\share\kb""#, &[r"\\server\share\kb"]),
            (r#"--x="a b" c"#, &["--x=a b", "c"]),
            (r#"'single quoted' """#, &["single quoted", ""]),
            (r#""say \"hi\"""#, &[r#"say "hi""#]),
            (
                "-m gpt-5 -c model_reasoning_effort=high",
                &["-m", "gpt-5", "-c", "model_reasoning_effort=high"],
            ),
        ];
        for (text, expected) in cases {
            assert_eq!(
                parse_launch_args(text).unwrap(),
                expected.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
                "{text:?}"
            );
        }
        for (text, error) in [
            (r#"--add-dir "D:\Docs"#, ARGS_QUOTE),
            (r#""D:\Docs\""#, ARGS_QUOTE),
            ("'abc", ARGS_QUOTE),
            ("--model opus\n--verbose", ARGS_CONTROL),
            ("a\u{0}b", ARGS_CONTROL),
            ("'a\tb'", ARGS_CONTROL),
        ] {
            assert_eq!(parse_launch_args(text).unwrap_err(), error, "{text:?}");
        }
    }

    #[test]
    fn launch_comes_from_settings_and_bad_values_are_explained() {
        let settings = serde_json::json!({ "agent": { "launch": {
            "claude-code": { "args": "--model opus", "env": [
                { "key": "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "value": "1" },
                { "key": "1BAD", "value": "x" },
                { "key": "MULTI", "value": "a\nb" },
                { "key": "EMPTY", "value": "" }
            ] },
            "codex": { "args": "\"unclosed", "env": [] }
        } } });
        let claude = launch_from_settings(&settings, Provider::Claude).unwrap();
        assert_eq!(claude.args, ["--model", "opus"]);
        assert_eq!(
            claude.env,
            [
                (
                    "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE".to_string(),
                    "1".to_string()
                ),
                ("EMPTY".to_string(), String::new()),
            ]
        );
        let codex = launch_from_settings(&settings, Provider::Codex).unwrap_err();
        assert!(
            codex.starts_with("Параметры запуска Codex: Незакрытая кавычка"),
            "{codex}"
        );
        // Прежний резидент без секции agent — ничего своего.
        assert_eq!(
            launch_from_settings(&serde_json::json!({}), Provider::Claude).unwrap(),
            Launch::default()
        );
    }

    fn strings(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    /// Свои параметры — после наших (у повторённого параметра действует свой);
    /// наш дубликат убирается там, где повтор — ошибка или лишний.
    #[test]
    fn user_args_go_last_and_win_where_safe() {
        let ours = agent_args(
            Provider::Claude,
            r"D:\rec\r1",
            Some(r"D:\kb"),
            &AgentSession::ResumeLast,
        );
        let args = with_user_args(
            Provider::Claude,
            ours.clone(),
            &strings(&["--model", "opus"]),
        );
        assert_eq!(args[..ours.len()], ours[..]);
        assert_eq!(args[ours.len()..], ["--model", "opus"]);
        // Свой --resume/-c/--session-id — нашего выбора сеанса нет; подсказка о
        // встрече остаётся.
        let with_id = agent_args(
            Provider::Claude,
            r"D:\rec\r1",
            None,
            &AgentSession::New(SID.into()),
        );
        for user in [
            &["--resume", "abc"][..],
            &["-c"][..],
            &["--resume=abc"][..],
            &["--session-id", "abc"][..],
        ] {
            for ours in [ours.clone(), with_id.clone()] {
                let args = with_user_args(Provider::Claude, ours, &strings(user));
                assert!(!args.iter().any(|a| a == "--continue"), "{user:?}");
                assert!(!args.iter().any(|a| a == SID), "{user:?}");
                assert!(args.iter().any(|a| a == "--append-system-prompt"));
            }
        }
        let codex = agent_args(
            Provider::Codex,
            r"D:\rec\r1",
            None,
            &AgentSession::ResumeLast,
        );
        let args = with_user_args(
            Provider::Codex,
            codex,
            &strings(&["--last", "-C", r"D:\other", "-m", "gpt-5"]),
        );
        assert_eq!(args[0], "resume");
        assert_eq!(args.iter().filter(|a| *a == "--last").count(), 1);
        assert!(!args.iter().any(|a| a == "--cd"));
        assert!(args
            .iter()
            .any(|a| a.starts_with("developer_instructions=")));
        assert_eq!(
            args[args.len() - 5..],
            ["--last", "-C", r"D:\other", "-m", "gpt-5"]
        );
    }

    const SID: &str = "0b6f8a52-3c1d-4e2f-9a7b-1c2d3e4f5a6b";

    /// Claude: новый сеанс — со своим id, «Продолжить» — `--resume` этого id
    /// (а не «последний разговор в папке», который мог оставить кто-то
    /// другой); id неизвестен — `--continue`. Codex — `resume --last`.
    #[test]
    fn claude_sessions_are_started_and_resumed_by_id() {
        let none: Vec<String> = Vec::new();
        let new = planned_session(Provider::Claude, false, &none, || SID.to_string());
        assert_eq!(new, AgentSession::New(SID.into()));
        let args = agent_args(Provider::Claude, "D:/r", None, &new);
        assert_eq!(args[..2], ["--session-id", SID]);
        let planned = planned_session(Provider::Claude, true, &none, || unreachable!());
        let resumed = resolved_session(planned.clone(), Provider::Claude, Some(SID));
        assert_eq!(resumed, AgentSession::Resume(SID.into()));
        assert_eq!(
            agent_args(Provider::Claude, "D:/r", None, &resumed)[..2],
            ["--resume", SID]
        );
        assert_eq!(
            resolved_session(planned, Provider::Claude, None),
            AgentSession::ResumeLast
        );
        // Свой выбор сеанса в параметрах — нашего id нет.
        assert_eq!(
            planned_session(
                Provider::Claude,
                false,
                &strings(&["-r", "x"]),
                || unreachable!()
            ),
            AgentSession::Fresh
        );
        // Codex: id не задаём, «Продолжить» — resume --last.
        assert_eq!(
            planned_session(Provider::Codex, false, &none, || unreachable!()),
            AgentSession::Fresh
        );
        let codex = resolved_session(
            planned_session(Provider::Codex, true, &none, || unreachable!()),
            Provider::Codex,
            Some(SID),
        );
        assert_eq!(codex, AgentSession::ResumeLast);
        assert_eq!(
            agent_args(Provider::Codex, "D:/r", None, &codex)[..2],
            ["resume", "--last"]
        );
        // Новый id каждый раз — настоящий UUID v4.
        let a = uuid::Uuid::new_v4().to_string();
        assert_eq!(a.len(), 36);
        assert_ne!(a, uuid::Uuid::new_v4().to_string());
    }

    /// Свои переменные — после очистки меток и наших: человек может нарочно
    /// задать и CLAUDE_CODE_FORCE_SESSION_PERSISTENCE, и переменную из списка меток.
    #[test]
    fn user_env_is_applied_after_the_scrub() {
        let _env = EnvGuard::set(&[("CLAUDE_EFFORT", "m9-parent")]);
        let mut plan = agent_env(
            Provider::Claude,
            &ProxyMode::System,
            &|_| None,
            &wininet(None),
        );
        plan.set.extend([
            (
                "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE".to_string(),
                "1".to_string(),
            ),
            ("CLAUDE_EFFORT".to_string(), "high".to_string()),
            ("TERM".to_string(), "xterm".to_string()),
        ]);
        let spec = SpawnSpec {
            recording: "r1".into(),
            program: PathBuf::from("claude.exe"),
            args: Vec::new(),
            cwd: std::env::temp_dir(),
            env: plan,
            cols: 80,
            rows: 24,
        };
        let command = command_for(&spec);
        let get = |k: &str| command.get_env(k).map(|v| v.to_string_lossy().into_owned());
        assert_eq!(
            get("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE").as_deref(),
            Some("1")
        );
        assert_eq!(get("CLAUDE_EFFORT").as_deref(), Some("high"));
        assert_eq!(get("TERM").as_deref(), Some("xterm"));
    }
}
