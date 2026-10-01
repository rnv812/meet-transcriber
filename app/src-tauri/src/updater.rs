// «Проверить обновления» и «Скачать и установить» (Настройки → «О программе»).
//
// Только по кнопке: фоновых проверок нет. Источник — последний выпуск
// публичного репозитория на GitHub (`UPDATE_REPO`): его JSON из API,
// установщик `meet_<версия>_x64-setup.exe` и `SHA256SUMS.txt` рядом. Скачанный
// установщик сверяется с суммой, запускается, и приложение штатно выходит
// («Выход» из трея) — дальше работает установщик (`windows/hooks.nsh`).
//
// Всё, что проверяется без сети, — чистые функции с тестами: сравнение
// версий, разбор выпуска, выбор файла, разбор сумм, отказ во время записи.

use std::cmp::Ordering;
use std::io::{Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter};

use crate::api;
use crate::logs::shell_log;
use crate::netproxy::{self, InternetSettings};
use crate::resident;
use crate::tray;
use crate::upgrade;

/// Откуда берутся обновления — единственное место с именем репозитория.
pub const UPDATE_REPO: &str = "rnv812/meet-transcriber";
/// Событие хода загрузки: `{done, total}` в байтах (`total` — 0, если неизвестен).
pub const PROGRESS_EVENT: &str = "update-progress";
const SUMS: &str = "SHA256SUMS.txt";
const INSTALLER_PREFIX: &str = "meet_";
const INSTALLER_SUFFIX: &str = "_x64-setup.exe";
/// Запрос к API GitHub — не дольше (и соединение при загрузке).
const API_TIMEOUT: Duration = Duration::from_secs(10);
/// Загрузка установщика ограничена не временем целиком, а паузой в данных:
/// зависшая загрузка не держит кнопку вечно, медленная — доходит.
const DOWNLOAD_STALL: Duration = Duration::from_secs(30);
/// События прогресса — не чаще.
const PROGRESS_EVERY: Duration = Duration::from_millis(200);
/// Папка загрузки в %TEMP%.
const DOWNLOAD_DIR: &str = "meet-update";

pub const NO_NETWORK: &str = "Не удалось проверить: нет связи с GitHub";
pub const RATE_LIMITED: &str =
    "Не удалось проверить: GitHub временно ограничил число запросов, попробуйте позже";
pub const BAD_REPLY: &str = "Не удалось проверить: непонятный ответ GitHub";
pub const RECORDING: &str = "Остановите запись, чтобы обновиться";
/// Не отказ, а вопрос: окно показывает его с кнопкой «Обновить сейчас» и
/// повторяет установку с `confirmed`. Окно сверяет текст дословно.
pub const WORK_IN_PROGRESS: &str =
    "Идёт расшифровка — она будет прервана и продолжится после обновления. Обновить сейчас?";
pub const CORRUPTED: &str = "Файл обновления повреждён";
pub const NOTHING_NEWER: &str = "Новой версии нет";
pub const NO_INSTALLER: &str = "В выпуске нет установщика для Windows";
pub const NO_SUMS: &str = "В выпуске нет контрольной суммы установщика (SHA256SUMS.txt)";
pub const BUSY: &str = "Обновление уже скачивается";
pub const NO_DOWNLOAD: &str = "Не удалось скачать обновление: нет связи с GitHub";
pub const RECORDING_AFTER_DOWNLOAD: &str =
    "Остановите запись, затем нажмите «Скачать и установить» ещё раз — установщик уже скачан";
pub const FOREIGN_HOST: &str =
    "Не удалось скачать обновление: GitHub перенаправил загрузку на чужой адрес";

pub fn releases_url() -> String {
    format!("https://github.com/{UPDATE_REPO}/releases")
}

fn latest_api_url() -> String {
    format!("https://api.github.com/repos/{UPDATE_REPO}/releases/latest")
}

/// Ссылки на файлы выпуска — только этого репозитория (GitHub сам
/// перенаправит на свой CDN).
fn download_prefix() -> String {
    format!("https://github.com/{UPDATE_REPO}/releases/download/")
}

// --- версии -------------------------------------------------------------------

/// Версия по semver: `x.y.z[-pre][+build]`, ведущее `v` допускается.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Version {
    core: [u64; 3],
    pre: Vec<String>,
}

pub fn parse_version(text: &str) -> Option<Version> {
    let text = text.trim();
    let text = text.strip_prefix(['v', 'V']).unwrap_or(text);
    let text = text.split_once('+').map_or(text, |(core, _build)| core);
    let (core, pre) = match text.split_once('-') {
        Some((core, pre)) => (core, Some(pre)),
        // PEP 440 (версия пакета Python): «0.2.0rc1» = «0.2.0-rc1».
        None => match text.char_indices().find(|&(i, c)| {
            c.is_ascii_alphabetic() && i > 0 && text.as_bytes()[i - 1].is_ascii_digit()
        }) {
            Some((i, _)) => (&text[..i], Some(&text[i..])),
            None => (text, None),
        },
    };
    let mut parts = core.split('.');
    let mut numbers = [0u64; 3];
    for slot in &mut numbers {
        let part = parts.next()?;
        if part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        *slot = part.parse().ok()?;
    }
    if parts.next().is_some() {
        return None;
    }
    let pre = match pre {
        None => Vec::new(),
        Some(pre) => {
            let items: Vec<String> = pre.split('.').map(str::to_string).collect();
            if items.iter().any(|item| {
                item.is_empty() || !item.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
            }) {
                return None;
            }
            items
        }
    };
    Some(Version { core: numbers, pre })
}

impl Ord for Version {
    fn cmp(&self, other: &Self) -> Ordering {
        self.core.cmp(&other.core).then_with(|| {
            // Без пре-релиза — старше любого пре-релиза той же версии.
            match (self.pre.is_empty(), other.pre.is_empty()) {
                (true, true) => Ordering::Equal,
                (true, false) => Ordering::Greater,
                (false, true) => Ordering::Less,
                (false, false) => compare_pre(&self.pre, &other.pre),
            }
        })
    }
}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Пре-релизы по semver: числа — численно и младше слов, слова — по ASCII,
/// при равном начале длиннее — старше.
fn compare_pre(a: &[String], b: &[String]) -> Ordering {
    for (x, y) in a.iter().zip(b) {
        let order = match (x.parse::<u64>(), y.parse::<u64>()) {
            (Ok(x), Ok(y)) => x.cmp(&y),
            (Ok(_), Err(_)) => Ordering::Less,
            (Err(_), Ok(_)) => Ordering::Greater,
            (Err(_), Err(_)) => x.cmp(y),
        };
        if order != Ordering::Equal {
            return order;
        }
    }
    a.len().cmp(&b.len())
}

/// Одна и та же версия, в какой бы записи (semver или PEP 440, с `v` или
/// без). Непонятные — равны, только если совпадают как строки.
pub fn same_version(a: &str, b: &str) -> bool {
    match (parse_version(a), parse_version(b)) {
        (Some(a), Some(b)) => a == b,
        _ => a.trim() == b.trim(),
    }
}

/// `latest` новее `current`. Непонятная версия — не новее (не предлагаем
/// то, что не можем сравнить).
pub fn is_newer(latest: &str, current: &str) -> bool {
    match (parse_version(latest), parse_version(current)) {
        (Some(latest), Some(current)) => latest > current,
        _ => false,
    }
}

// --- выпуск -------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Asset {
    pub name: String,
    pub url: String,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Release {
    /// Версия из тега без `v`.
    pub version: String,
    pub notes_url: String,
    pub assets: Vec<Asset>,
}

/// Выпуск из ответа `releases/latest`. Черновик, пре-релиз и тег, который не
/// версия, — не выпуск для обновления (`None`).
pub fn parse_release(value: &Value) -> Option<Release> {
    if value.get("draft").and_then(Value::as_bool).unwrap_or(false)
        || value
            .get("prerelease")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    {
        return None;
    }
    let tag = value.get("tag_name")?.as_str()?.trim();
    parse_version(tag)?;
    let version = tag.strip_prefix(['v', 'V']).unwrap_or(tag).to_string();
    let notes_url = value
        .get("html_url")
        .and_then(Value::as_str)
        .filter(|url| {
            let page = releases_url();
            *url == page || url.starts_with(&format!("{page}/"))
        })
        .map(str::to_string)
        .unwrap_or_else(releases_url);
    let assets = value
        .get("assets")
        .and_then(Value::as_array)
        .map(|list| {
            list.iter()
                .filter_map(|asset| {
                    Some(Asset {
                        name: asset.get("name")?.as_str()?.to_string(),
                        url: asset.get("browser_download_url")?.as_str()?.to_string(),
                        size: asset.get("size").and_then(Value::as_u64).unwrap_or(0),
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    Some(Release {
        version,
        notes_url,
        assets,
    })
}

/// Имя установщика: `meet_<версия>_x64-setup.exe`, версия — из цифр, букв,
/// точек и дефисов (имя станет именем файла в %TEMP%).
pub fn installer_name_ok(name: &str) -> bool {
    name.strip_prefix(INSTALLER_PREFIX)
        .and_then(|rest| rest.strip_suffix(INSTALLER_SUFFIX))
        .is_some_and(|version| {
            !version.is_empty()
                && version
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
        })
}

/// Ссылка на файл — из выпусков этого репозитория.
fn asset_url_ok(asset: &Asset) -> bool {
    let lower = asset.url.to_ascii_lowercase();
    asset.url.starts_with(&download_prefix())
        && !asset.url.contains(['"', ' ', '\\'])
        && !lower.contains("..")
        && !lower.contains("%2e")
}

/// Куда GitHub может перенаправить загрузку файла выпуска: сам github.com и
/// его хранилище (`objects.githubusercontent.com`, `*.githubusercontent.com`).
/// Только https.
pub fn download_host_ok(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host = authority.rsplit('@').next().unwrap_or("");
    let host = host.split(':').next().unwrap_or("").to_ascii_lowercase();
    host == "github.com"
        || host == "objects.githubusercontent.com"
        || (host.ends_with(".githubusercontent.com") && host.len() > ".githubusercontent.com".len())
}

/// Прежние загрузки в папке обновления (`meet_*_x64-setup.exe` и их `.part`),
/// кроме `keep` — уже скачанного и сверенного установщика этого выпуска.
pub fn stale_downloads(names: &[String], keep: Option<&str>) -> Vec<String> {
    names
        .iter()
        .filter(|name| {
            let base = name.strip_suffix(".part").unwrap_or(name);
            installer_name_ok(base) && Some(name.as_str()) != keep
        })
        .cloned()
        .collect()
}

/// Установщик выпуска: сначала точное имя с версией выпуска, иначе любой
/// подходящий по шаблону.
pub fn pick_installer(release: &Release) -> Option<&Asset> {
    let exact = format!("{INSTALLER_PREFIX}{}{INSTALLER_SUFFIX}", release.version);
    let usable = |asset: &&Asset| installer_name_ok(&asset.name) && asset_url_ok(asset);
    release
        .assets
        .iter()
        .filter(usable)
        .find(|asset| asset.name == exact)
        .or_else(|| release.assets.iter().find(usable))
}

pub fn pick_sums(release: &Release) -> Option<&Asset> {
    release
        .assets
        .iter()
        .find(|asset| asset.name == SUMS && asset_url_ok(asset))
}

/// Сумма файла `name` из `SHA256SUMS.txt` (`<hex>  <имя>` или `<hex> *<имя>`),
/// в нижнем регистре.
pub fn parse_sums(text: &str, name: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let line = line.trim().trim_start_matches('\u{feff}');
        let (hash, file) = line.split_once(char::is_whitespace)?;
        let file = file.trim_start().trim_start_matches('*');
        let valid = hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_hexdigit());
        (valid && file == name).then(|| hash.to_ascii_lowercase())
    })
}

/// Ответ `check_update` окну.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct UpdateCheck {
    pub current: String,
    /// `None` — выпусков ещё нет («Обновления пока не опубликованы»).
    pub latest: Option<String>,
    pub newer: bool,
    pub notes_url: Option<String>,
    /// Установщик в выпуске; без него «Скачать и установить» недоступна.
    pub asset_name: Option<String>,
    pub size: Option<u64>,
}

pub fn check_result(release: Option<&Release>, current: &str) -> UpdateCheck {
    let Some(release) = release else {
        return UpdateCheck {
            current: current.to_string(),
            latest: None,
            newer: false,
            notes_url: None,
            asset_name: None,
            size: None,
        };
    };
    let installer = pick_installer(release).filter(|_| pick_sums(release).is_some());
    UpdateCheck {
        current: current.to_string(),
        latest: Some(release.version.clone()),
        newer: is_newer(&release.version, current),
        notes_url: Some(release.notes_url.clone()),
        asset_name: installer.map(|asset| asset.name.clone()),
        size: installer.map(|asset| asset.size),
    }
}

/// Текст ошибки по коду ответа GitHub (`None` — ответа не было вовсе).
pub fn check_error(status: Option<u16>) -> String {
    match status {
        None => NO_NETWORK.to_string(),
        Some(403 | 429) => RATE_LIMITED.to_string(),
        Some(code) => format!("Не удалось проверить: GitHub ответил ошибкой {code}"),
    }
}

/// Можно ли обновляться сейчас: `state` — `/state` резидента (`None` — его
/// нет, тогда нет ни записи, ни задач), `jobs` — его `/jobs`. Идёт запись
/// или ассистент — отказ. Идёт или ждёт задача (GPU занят) — вопрос
/// WORK_IN_PROGRESS, пока человек не подтвердил (`confirmed`): прерванную
/// задачу резидент новой версии поставит снова.
pub fn install_refusal(
    state: Option<&Value>,
    jobs: Option<&Value>,
    confirmed: bool,
) -> Option<&'static str> {
    let state = state?;
    if upgrade::resident_busy(state) {
        return Some(RECORDING);
    }
    if !confirmed && upgrade::resident_working(state, jobs) {
        return Some(WORK_IN_PROGRESS);
    }
    None
}

/// Прокси для запросов к GitHub: переменные среды (`HTTPS_PROXY`,
/// `HTTP_PROXY`), иначе системный прокси Windows (WinINET). SOCKS ureq без
/// своей фичи не умеет — такой прокси пропускаем (запрос пойдёт напрямую);
/// `https://` у адреса прокси — тот же CONNECT, что и у `http://`.
pub fn proxy_url(env: Option<&str>, settings: &InternetSettings) -> Option<String> {
    let raw = match env.map(str::trim).filter(|value| !value.is_empty()) {
        Some(value) => netproxy::pick_server(value)?,
        None if settings.enabled == Some(1) => {
            settings.server.as_deref().and_then(netproxy::pick_server)?
        }
        None => return None,
    };
    let (scheme, rest) = raw.split_once("://")?;
    match scheme.to_ascii_lowercase().as_str() {
        "http" | "https" => Some(format!("http://{rest}")),
        _ => None,
    }
}

// --- сеть ---------------------------------------------------------------------

fn env_proxy() -> Option<String> {
    ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"]
        .iter()
        .find_map(|name| {
            std::env::var(name)
                .ok()
                .filter(|value| !value.trim().is_empty())
        })
}

fn agent(app_version: &str) -> Result<ureq::Agent, String> {
    let tls = native_tls::TlsConnector::new()
        .map_err(|error| format!("Не удалось подготовить защищённое соединение: {error}"))?;
    let mut builder = ureq::AgentBuilder::new()
        .tls_connector(Arc::new(tls))
        .user_agent(&format!("meet-desktop/{app_version}"))
        .timeout_connect(API_TIMEOUT)
        .timeout_read(DOWNLOAD_STALL)
        .https_only(true)
        .try_proxy_from_env(false);
    let env = env_proxy();
    if let Some(url) = proxy_url(env.as_deref(), &netproxy::read_internet_settings()) {
        match ureq::Proxy::new(&url) {
            Ok(proxy) => builder = builder.proxy(proxy),
            Err(error) => shell_log!("прокси для GitHub не подошёл: {error}"),
        }
    }
    Ok(builder.build())
}

/// Последний выпуск: `Ok(None)` — выпусков нет (404), `Err` — текст для окна.
fn fetch_latest(app_version: &str) -> Result<Option<Release>, String> {
    let agent = agent(app_version)?;
    let reply = agent
        .get(&latest_api_url())
        .set("Accept", "application/vnd.github+json")
        .set("X-GitHub-Api-Version", "2022-11-28")
        .timeout(API_TIMEOUT)
        .call();
    let response = match reply {
        Ok(response) => response,
        Err(ureq::Error::Status(404, _)) => return Ok(None),
        Err(ureq::Error::Status(code, _)) => {
            shell_log!("проверка обновлений: GitHub ответил {code}");
            return Err(check_error(Some(code)));
        }
        Err(error) => {
            shell_log!("проверка обновлений: {error}");
            return Err(check_error(None));
        }
    };
    let value: Value = response.into_json().map_err(|error| {
        shell_log!("проверка обновлений: ответ не разобран: {error}");
        BAD_REPLY.to_string()
    })?;
    Ok(parse_release(&value))
}

fn app_version(app: &AppHandle) -> String {
    app.package_info().version.to_string()
}

/// Страница выпусков для «Скачать новую версию» в окне: имя репозитория
/// живёт только здесь (`UPDATE_REPO`).
#[tauri::command]
pub fn releases_page() -> String {
    releases_url()
}

/// «Проверить обновления»: последний выпуск на GitHub против своей версии.
#[tauri::command]
pub async fn check_update(app: AppHandle) -> Result<UpdateCheck, String> {
    let current = app_version(&app);
    tauri::async_runtime::spawn_blocking(move || {
        let release = fetch_latest(&current)?;
        let result = check_result(release.as_ref(), &current);
        shell_log!(
            "проверка обновлений: у нас {current}, последняя {}",
            result.latest.as_deref().unwrap_or("— (выпусков нет)")
        );
        Ok(result)
    })
    .await
    .map_err(|error| error.to_string())?
}

static INSTALLING: AtomicBool = AtomicBool::new(false);

struct Busy;

impl Busy {
    fn begin() -> Result<Busy, String> {
        INSTALLING
            .compare_exchange(false, true, AtomicOrdering::SeqCst, AtomicOrdering::SeqCst)
            .map(|_| Busy)
            .map_err(|_| BUSY.to_string())
    }
}

impl Drop for Busy {
    fn drop(&mut self) {
        INSTALLING.store(false, AtomicOrdering::SeqCst);
    }
}

/// `/state` и `/jobs` резидента; нет его — (None, None).
fn resident_load() -> (Option<Value>, Option<Value>) {
    let Some(endpoint) = resident::read_endpoint() else {
        return (None, None);
    };
    let client = api::Client::new(&endpoint);
    let state = client.get_state().ok();
    let jobs = state.as_ref().and_then(|_| client.get_jobs().ok());
    (state, jobs)
}

fn refusal_now(confirmed: bool) -> Option<&'static str> {
    let (state, jobs) = resident_load();
    install_refusal(state.as_ref(), jobs.as_ref(), confirmed)
}

/// «Скачать и установить»: скачать установщик последнего выпуска, сверить
/// SHA-256 с `SHA256SUMS.txt` выпуска, запустить его и штатно выйти.
/// `confirmed` — человек согласился прервать идущую расшифровку
/// (см. WORK_IN_PROGRESS).
#[tauri::command]
pub async fn install_update(app: AppHandle, confirmed: Option<bool>) -> Result<(), String> {
    let confirmed = confirmed.unwrap_or(false);
    tauri::async_runtime::spawn_blocking(move || install_blocking(&app, confirmed))
        .await
        .map_err(|error| error.to_string())?
}

fn install_blocking(app: &AppHandle, confirmed: bool) -> Result<(), String> {
    let _busy = Busy::begin()?;
    if let Some(refusal) = refusal_now(confirmed) {
        return Err(refusal.to_string());
    }
    let current = app_version(app);
    let release = fetch_latest(&current)?.ok_or(NOTHING_NEWER)?;
    if !is_newer(&release.version, &current) {
        return Err(NOTHING_NEWER.to_string());
    }
    let installer = pick_installer(&release).ok_or(NO_INSTALLER)?;
    let sums = pick_sums(&release).ok_or(NO_SUMS)?;
    let agent = agent(&current)?;
    let sums_reply = agent
        .get(&sums.url)
        .timeout(API_TIMEOUT)
        .call()
        .map_err(|error| {
            shell_log!("обновление: SHA256SUMS.txt не скачался: {error}");
            NO_DOWNLOAD.to_string()
        })?;
    if !download_host_ok(sums_reply.get_url()) {
        shell_log!(
            "обновление: SHA256SUMS.txt пришёл с {}",
            sums_reply.get_url()
        );
        return Err(FOREIGN_HOST.to_string());
    }
    let sums_text = sums_reply
        .into_string()
        .map_err(|_| CORRUPTED.to_string())?;
    let expected = parse_sums(&sums_text, &installer.name).ok_or(NO_SUMS)?;

    let dir = std::env::temp_dir().join(DOWNLOAD_DIR);
    std::fs::create_dir_all(&dir)
        .map_err(|error| format!("Не удалось создать папку для обновления: {error}"))?;
    let target = dir.join(&installer.name);
    let partial = dir.join(format!("{}.part", installer.name));
    // Уже скачан и сверен (прошлый раз помешала запись) — не качаем заново.
    let ready = crate::engine::file_sha256(&target).is_ok_and(|hash| hash == expected);
    let names: Vec<String> = std::fs::read_dir(&dir)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .map(|entry| entry.file_name().to_string_lossy().into_owned())
                .collect()
        })
        .unwrap_or_default();
    let keep = ready.then_some(installer.name.as_str());
    for name in stale_downloads(&names, keep) {
        let _ = std::fs::remove_file(dir.join(name));
    }
    if ready {
        shell_log!("обновление: {} уже скачан и сверен", installer.name);
    } else {
        shell_log!(
            "обновление: скачиваю {} ({} байт)",
            installer.name,
            installer.size
        );
        let actual = match download(app, &agent, installer, &partial) {
            Ok(hash) => hash,
            Err(error) => {
                let _ = std::fs::remove_file(&partial);
                return Err(error);
            }
        };
        if actual != expected {
            shell_log!("обновление: SHA-256 не совпал (ждали {expected}, получили {actual})");
            let _ = std::fs::remove_file(&partial);
            return Err(CORRUPTED.to_string());
        }
        let _ = std::fs::remove_file(&target);
        std::fs::rename(&partial, &target)
            .map_err(|error| format!("Не удалось сохранить обновление: {error}"))?;
    }
    // Пока качали, могла начаться запись (или расшифровка): файл оставляем,
    // установщик не запускаем; следующий щелчок возьмёт уже скачанный.
    match refusal_now(confirmed) {
        Some(RECORDING) => return Err(RECORDING_AFTER_DOWNLOAD.to_string()),
        Some(other) => return Err(other.to_string()),
        None => {}
    }
    launch_and_quit(app, &target)
}

/// Скачать в `path`, считая SHA-256 по пути; события прогресса — окну.
fn download(
    app: &AppHandle,
    agent: &ureq::Agent,
    asset: &Asset,
    path: &Path,
) -> Result<String, String> {
    let response = agent.get(&asset.url).call().map_err(|error| {
        shell_log!("обновление: установщик не скачался: {error}");
        NO_DOWNLOAD.to_string()
    })?;
    if !download_host_ok(response.get_url()) {
        shell_log!("обновление: установщик пришёл с {}", response.get_url());
        return Err(FOREIGN_HOST.to_string());
    }
    let total = response
        .header("Content-Length")
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(asset.size);
    let mut reader = response.into_reader();
    let mut file = std::fs::File::create(path)
        .map_err(|error| format!("Не удалось сохранить обновление: {error}"))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 64 * 1024];
    let mut done: u64 = 0;
    let mut last = Instant::now() - PROGRESS_EVERY;
    loop {
        let read = reader.read(&mut buffer).map_err(|error| {
            shell_log!("обновление: загрузка оборвалась: {error}");
            NO_DOWNLOAD.to_string()
        })?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
        file.write_all(&buffer[..read])
            .map_err(|error| format!("Не удалось сохранить обновление: {error}"))?;
        done += read as u64;
        if last.elapsed() >= PROGRESS_EVERY {
            last = Instant::now();
            let _ = app.emit(PROGRESS_EVENT, json!({ "done": done, "total": total }));
        }
    }
    file.flush()
        .map_err(|error| format!("Не удалось сохранить обновление: {error}"))?;
    let _ = app.emit(
        PROGRESS_EVENT,
        json!({ "done": done, "total": total.max(done) }),
    );
    if total > 0 && done != total {
        return Err(CORRUPTED.to_string());
    }
    Ok(format!("{:x}", hasher.finalize()))
}

/// Запустить установщик и выйти штатно. Установщик и сам закроет
/// приложение (`--quit` в `hooks.nsh`), но выход отсюда быстрее и тот же.
fn launch_and_quit(app: &AppHandle, installer: &Path) -> Result<(), String> {
    shell_log!("обновление: запускаю {}", installer.display());
    crate::windows::shell_execute(&installer.to_string_lossy())
        .map_err(|code| format!("Не удалось запустить установщик (код {code})"))?;
    tray::quit(app);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const RELEASE: &str = r#"{
        "tag_name": "v0.2.0",
        "name": "meet 0.2.0",
        "draft": false,
        "prerelease": false,
        "html_url": "https://github.com/rnv812/meet-transcriber/releases/tag/v0.2.0",
        "assets": [
            {"name": "SHA256SUMS.txt", "size": 90,
             "browser_download_url": "https://github.com/rnv812/meet-transcriber/releases/download/v0.2.0/SHA256SUMS.txt"},
            {"name": "meet_0.2.0_x64-setup.exe", "size": 52428800,
             "browser_download_url": "https://github.com/rnv812/meet-transcriber/releases/download/v0.2.0/meet_0.2.0_x64-setup.exe"},
            {"name": "notes.md", "size": 10,
             "browser_download_url": "https://github.com/rnv812/meet-transcriber/releases/download/v0.2.0/notes.md"}
        ]
    }"#;

    fn release() -> Release {
        parse_release(&serde_json::from_str(RELEASE).unwrap()).unwrap()
    }

    fn asset(name: &str) -> Asset {
        Asset {
            name: name.to_string(),
            url: format!("{}v0.2.0/{name}", download_prefix()),
            size: 1,
        }
    }

    #[test]
    fn versions_compare_numerically_not_as_text() {
        assert!(is_newer("0.10.0", "0.9.0"));
        assert!(is_newer("1.0.0", "0.99.99"));
        assert!(is_newer("0.1.1", "0.1.0"));
        assert!(is_newer("v0.2.0", "0.1.9"));
        assert!(!is_newer("0.9.0", "0.10.0"));
        assert!(!is_newer("0.1.0", "0.1.0"));
        assert!(!is_newer("v0.1.0", "0.1.0"));
    }

    #[test]
    fn prerelease_is_older_than_the_release() {
        assert!(is_newer("0.2.0", "0.2.0-rc1"));
        assert!(!is_newer("0.2.0-rc1", "0.2.0"));
        assert!(is_newer("0.2.0-rc1", "0.1.9"));
        assert!(is_newer("0.2.0-rc.2", "0.2.0-rc.1"));
        assert!(is_newer("0.2.0-rc.10", "0.2.0-rc.9"));
        assert!(is_newer("0.2.0-beta", "0.2.0-alpha"));
        assert!(is_newer("0.2.0-alpha.1", "0.2.0-alpha"));
        assert!(is_newer("0.2.0-alpha", "0.2.0-1"));
        // Метаданные сборки на порядок не влияют.
        assert!(!is_newer("0.2.0+build.5", "0.2.0"));
    }

    #[test]
    fn python_prerelease_equals_semver_prerelease() {
        assert!(same_version("0.2.0rc1", "0.2.0-rc1"));
        assert!(same_version("v0.2.0", "0.2.0"));
        assert!(!same_version("0.2.0rc1", "0.2.0"));
        assert!(!same_version("0.2.0", "0.2.1"));
        assert!(same_version("dev", " dev "));
        assert!(!same_version("dev", "0.2.0"));
    }

    #[test]
    fn garbage_versions_are_never_newer() {
        for bad in [
            "",
            "latest",
            "1.2",
            "1.2.3.4",
            "1.x.3",
            "1.2.3-",
            "1.2.3-a..b",
            "-1.2.3",
        ] {
            assert!(parse_version(bad).is_none(), "{bad}");
            assert!(!is_newer(bad, "0.1.0"), "{bad}");
        }
        assert!(!is_newer("0.2.0", "dev"));
    }

    #[test]
    fn release_json_is_parsed() {
        let release = release();
        assert_eq!(release.version, "0.2.0");
        assert_eq!(
            release.notes_url,
            "https://github.com/rnv812/meet-transcriber/releases/tag/v0.2.0"
        );
        assert_eq!(release.assets.len(), 3);
        assert_eq!(release.assets[1].size, 52_428_800);
    }

    #[test]
    fn drafts_prereleases_and_odd_tags_are_not_updates() {
        let mut value: Value = serde_json::from_str(RELEASE).unwrap();
        value["prerelease"] = json!(true);
        assert!(parse_release(&value).is_none());
        let mut value: Value = serde_json::from_str(RELEASE).unwrap();
        value["draft"] = json!(true);
        assert!(parse_release(&value).is_none());
        let mut value: Value = serde_json::from_str(RELEASE).unwrap();
        value["tag_name"] = json!("nightly");
        assert!(parse_release(&value).is_none());
        assert!(parse_release(&json!({"message": "Not Found"})).is_none());
    }

    #[test]
    fn notes_link_stays_on_the_releases_page() {
        let mut value: Value = serde_json::from_str(RELEASE).unwrap();
        value["html_url"] = json!("https://evil.example/releases/tag/v0.2.0");
        assert_eq!(parse_release(&value).unwrap().notes_url, releases_url());
        assert!(crate::windows::url_allowed(&release().notes_url));
        assert!(crate::windows::url_allowed(&releases_url()));
    }

    #[test]
    fn installer_asset_is_picked_by_pattern() {
        let release = release();
        assert_eq!(
            pick_installer(&release).unwrap().name,
            "meet_0.2.0_x64-setup.exe"
        );
        assert_eq!(pick_sums(&release).unwrap().name, "SHA256SUMS.txt");
    }

    #[test]
    fn exact_version_wins_over_other_installers() {
        let release = Release {
            version: "0.2.0".into(),
            notes_url: releases_url(),
            assets: vec![
                asset("meet_0.1.9_x64-setup.exe"),
                asset("meet_0.2.0_x64-setup.exe"),
            ],
        };
        assert_eq!(
            pick_installer(&release).unwrap().name,
            "meet_0.2.0_x64-setup.exe"
        );
    }

    #[test]
    fn unsuitable_assets_are_ignored() {
        let mut foreign = asset("meet_0.2.0_x64-setup.exe");
        foreign.url = "https://evil.example/meet_0.2.0_x64-setup.exe".into();
        let release = Release {
            version: "0.2.0".into(),
            notes_url: releases_url(),
            assets: vec![
                asset("meet_0.2.0_x64_en-US.msi"),
                asset("meet_0.2.0_arm64-setup.exe"),
                asset("meet_..\\..\\x_x64-setup.exe"),
                asset("meet__x64-setup.exe"),
                foreign,
            ],
        };
        assert!(pick_installer(&release).is_none());
        assert!(installer_name_ok("meet_0.2.0-rc1_x64-setup.exe"));
        for bad in [
            "https://github.com/rnv812/meet-transcriber/releases/download/../../x/meet_0.2.0_x64-setup.exe",
            "https://github.com/rnv812/meet-transcriber/releases/download/%2E%2E/meet_0.2.0_x64-setup.exe",
            "https://github.com/rnv812/meet-transcriber/releases/download/v0.2.0/%2e./meet_0.2.0_x64-setup.exe",
        ] {
            let mut sneaky = asset("meet_0.2.0_x64-setup.exe");
            sneaky.url = bad.to_string();
            let release = Release {
                version: "0.2.0".into(),
                notes_url: releases_url(),
                assets: vec![sneaky],
            };
            assert!(pick_installer(&release).is_none(), "{bad}");
        }
        assert!(!installer_name_ok("meet_0.2.0 _x64-setup.exe"));
    }

    #[test]
    fn downloads_may_land_only_on_github_hosts() {
        assert!(download_host_ok(
            "https://github.com/rnv812/meet-transcriber/releases/download/v0.2.0/x.exe"
        ));
        assert!(download_host_ok(
            "https://objects.githubusercontent.com/github-production-release-asset/1?x=y"
        ));
        assert!(download_host_ok(
            "https://release-assets.githubusercontent.com/a"
        ));
        assert!(download_host_ok("https://GitHub.com:443/a"));
        for bad in [
            "http://objects.githubusercontent.com/a",
            "https://evil.example/a",
            "https://github.com.evil.example/a",
            "https://evilgithubusercontent.com/a",
            "https://.githubusercontent.com/a",
            "https://github.com@evil.example/a",
            "https://evil.example/github.com",
            "",
        ] {
            assert!(!download_host_ok(bad), "{bad}");
        }
    }

    #[test]
    fn old_downloads_are_cleared_but_a_verified_one_is_kept() {
        let names: Vec<String> = [
            "meet_0.1.9_x64-setup.exe",
            "meet_0.2.0_x64-setup.exe",
            "meet_0.2.0_x64-setup.exe.part",
            "notes.txt",
            "meet_0.2.0_x64-setup.exe.bak",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        assert_eq!(
            stale_downloads(&names, Some("meet_0.2.0_x64-setup.exe")),
            vec!["meet_0.1.9_x64-setup.exe", "meet_0.2.0_x64-setup.exe.part"]
        );
        assert_eq!(
            stale_downloads(&names, None),
            vec![
                "meet_0.1.9_x64-setup.exe",
                "meet_0.2.0_x64-setup.exe",
                "meet_0.2.0_x64-setup.exe.part"
            ]
        );
    }

    #[test]
    fn sums_file_gives_the_hash_of_the_installer() {
        let hash = "AB".repeat(32);
        let text = format!(
            "\u{feff}{}  other.exe\n{hash}  meet_0.2.0_x64-setup.exe\r\n",
            "0".repeat(64)
        );
        assert_eq!(
            parse_sums(&text, "meet_0.2.0_x64-setup.exe"),
            Some("ab".repeat(32))
        );
        let binary_mode = format!("{} *meet_0.2.0_x64-setup.exe\n", "c".repeat(64));
        assert_eq!(
            parse_sums(&binary_mode, "meet_0.2.0_x64-setup.exe"),
            Some("c".repeat(64))
        );
        assert_eq!(parse_sums(&text, "meet_0.3.0_x64-setup.exe"), None);
        assert_eq!(
            parse_sums("abc  meet_0.2.0_x64-setup.exe", "meet_0.2.0_x64-setup.exe"),
            None
        );
        assert_eq!(parse_sums("", "x"), None);
    }

    #[test]
    fn check_result_reports_newer_version_with_its_installer() {
        let result = check_result(Some(&release()), "0.1.0");
        assert_eq!(
            result,
            UpdateCheck {
                current: "0.1.0".into(),
                latest: Some("0.2.0".into()),
                newer: true,
                notes_url: Some(
                    "https://github.com/rnv812/meet-transcriber/releases/tag/v0.2.0".into()
                ),
                asset_name: Some("meet_0.2.0_x64-setup.exe".into()),
                size: Some(52_428_800),
            }
        );
        assert!(!check_result(Some(&release()), "0.2.0").newer);
    }

    #[test]
    fn release_without_sums_offers_no_download() {
        let mut release = release();
        release.assets.retain(|asset| asset.name != SUMS);
        let result = check_result(Some(&release), "0.1.0");
        assert!(result.newer);
        assert_eq!(result.asset_name, None);
    }

    #[test]
    fn no_published_release_is_not_an_error() {
        let result = check_result(None, "0.1.0");
        assert_eq!(result.latest, None);
        assert!(!result.newer);
    }

    #[test]
    fn check_errors_are_explained() {
        assert_eq!(check_error(None), NO_NETWORK);
        assert_eq!(check_error(Some(403)), RATE_LIMITED);
        assert_eq!(check_error(Some(429)), RATE_LIMITED);
        assert_eq!(
            check_error(Some(500)),
            "Не удалось проверить: GitHub ответил ошибкой 500"
        );
    }

    #[test]
    fn update_is_refused_while_recording() {
        assert_eq!(
            install_refusal(Some(&json!({"status": "recording"})), None, true),
            Some(RECORDING)
        );
        let live = json!({"status": "idle", "live": {"active": true}});
        assert_eq!(install_refusal(Some(&live), None, true), Some(RECORDING));
        let idle = json!({"status": "idle", "live": {"active": false}});
        assert_eq!(install_refusal(Some(&idle), None, false), None);
        assert_eq!(install_refusal(None, None, false), None);
    }

    #[test]
    fn update_during_transcription_needs_a_confirmation() {
        let idle = json!({"status": "idle", "gpu_busy": false});
        let running = json!({"items": [{"kind": "transcribe", "state": "running"}]});
        assert_eq!(
            install_refusal(Some(&idle), Some(&running), false),
            Some(WORK_IN_PROGRESS)
        );
        assert_eq!(install_refusal(Some(&idle), Some(&running), true), None);
        let gpu = json!({"status": "idle", "gpu_busy": true});
        assert_eq!(
            install_refusal(Some(&gpu), None, false),
            Some(WORK_IN_PROGRESS)
        );
        // Запись важнее подтверждения: её не прерывают.
        let recording = json!({"status": "recording", "gpu_busy": true});
        assert_eq!(
            install_refusal(Some(&recording), Some(&running), true),
            Some(RECORDING)
        );
        assert!(WORK_IN_PROGRESS.ends_with("Обновить сейчас?"));
    }

    #[test]
    fn proxy_comes_from_env_then_windows() {
        let off = InternetSettings::default();
        let windows = InternetSettings {
            enabled: Some(1),
            server: Some("127.0.0.1:3067".into()),
            overrides: None,
        };
        assert_eq!(proxy_url(None, &off), None);
        assert_eq!(
            proxy_url(None, &windows),
            Some("http://127.0.0.1:3067".into())
        );
        assert_eq!(
            proxy_url(Some("http://10.0.0.1:8080"), &windows),
            Some("http://10.0.0.1:8080".into())
        );
        assert_eq!(
            proxy_url(Some("https://10.0.0.1:8443/"), &off),
            Some("http://10.0.0.1:8443".into())
        );
        // SOCKS ureq не умеет — напрямую, а не в никуда.
        assert_eq!(proxy_url(Some("socks5://127.0.0.1:1080"), &windows), None);
        let disabled = InternetSettings {
            enabled: Some(0),
            ..windows
        };
        assert_eq!(proxy_url(None, &disabled), None);
    }

    #[test]
    fn repo_lives_in_one_place() {
        assert_eq!(
            releases_url(),
            "https://github.com/rnv812/meet-transcriber/releases"
        );
        assert_eq!(
            latest_api_url(),
            "https://api.github.com/repos/rnv812/meet-transcriber/releases/latest"
        );
    }
}
