// Трей оболочки: иконка состояния, меню действий, опрос резидента, уведомления.
//
// Раз в секунду рабочий поток спрашивает резидента `/state` и `/jobs`, сводит
// ответ в `View` и сравнивает с прошлым: разница — это уведомления, сам `View` —
// иконка, тултип и меню. HTTP только в рабочих потоках: главный поток держит
// цикл событий, и подвисший резидент не должен подвешивать трей.
//
// Всё, что проверяется без GUI, — чистые функции с тестами внизу: разбор
// ответов, переходы → уведомления, иконка, тултип, меню, фильтр по настройке.

use std::collections::HashMap;
use std::hash::Hash;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use tauri::image::Image;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Wry};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;

use crate::api::{self, Client};
use crate::live_panel;
use crate::logs::shell_log;
use crate::resident::{self, lock, ResidentStatus, Supervisor};
use crate::upgrade;
use crate::windows;

const TRAY_ID: &str = "meet";
/// Опрос, пока резидент работает, и пока он запускается/упал: во втором случае
/// спрашивать чаще незачем — отвечать некому.
const TICK: Duration = Duration::from_secs(1);
const TICK_SLOW: Duration = Duration::from_secs(3);
/// Столько промахов подряд — и трей показывает «нет связи». Один промах —
/// резидент занят или перезапускается; иконка из-за него не мигает.
const OFFLINE_AFTER_MISSES: u32 = 2;
/// Сколько после уведомления о записи открытие окна из трея ведёт к ней.
const REMEMBER_RECORDING: Duration = Duration::from_secs(5 * 60);
/// Уровень уведомлений перечитывается хотя бы так часто (и перед каждым
/// показом): его помнит и уведомление о сбое резидента, когда спросить уже
/// некого.
const LEVEL_REFRESH: Duration = Duration::from_secs(30);
const ERROR_CHARS: usize = 120;

pub const RECORDING_STARTED: &str = "Идёт запись";
pub const AUTO_RECORDING_STARTED: &str = "Идёт запись (авто)";
pub const RECORDING_SAVED: &str = "Запись сохранена";
pub const TRANSCRIPT_READY: &str = "Расшифровка готова";
pub const TRANSCRIPT_FAILED: &str = "Ошибка расшифровки";
pub const RESIDENT_FAILED: &str = "Служба записи не запускается";
/// Фоновое обслуживание движка при старте (`engine::Upkeep`) не удалось.
pub const ENGINE_UPDATE_FAILED: &str = "Не удалось обновить движок";
pub const IMPORT_FAILED: &str = "Не удалось импортировать";
pub const RECORDING_INTERRUPTED: &str = "Запись прервана";
pub const START_FAILED: &str = "Не удалось начать запись";
pub const STOP_FAILED: &str = "Не удалось остановить запись";
pub const CANCEL_FAILED: &str = "Не удалось отменить запись";
pub const AUTO_FAILED: &str = "Не удалось переключить автозапись";
pub const LIVE_LISTENING: &str = "Ассистент слушает встречу";
pub const LIVE_SAVED: &str = "Ассистент остановлен — запись сохранена";
/// Остановлен, но с ошибкой (не дописал, вышел с кодом): текст — в теле.
pub const LIVE_STOPPED_WITH_ERROR: &str = "Ассистент остановлен";
pub const LIVE_FAILED: &str = "Ассистент завершился с ошибкой";
/// Остановлен раньше, чем загрузилась модель: записи нет.
pub const LIVE_CANCELLED: &str = "Запуск ассистента отменён";
pub const LIVE_START_FAILED: &str = "Не удалось запустить ассистента";
pub const LIVE_STOP_FAILED: &str = "Не удалось остановить ассистента";
/// Автоматическая выгрузка встречи в базу знаний не удалась (`/state.kb_export_failed`).
pub const KB_EXPORT_FAILED: &str = "Не удалось выгрузить встречу в базу знаний";
/// Раздел настроек, куда ведёт отказ `/live/start` без провайдера (409).
pub const ASSISTANT_SECTION: &str = "assistant";
/// Что проходит при `ui.notifications = "important"`: ошибки и автоматический
/// старт записи (спека: «важное — ошибки и автостарт»).
const IMPORTANT: &[&str] = &[
    AUTO_RECORDING_STARTED,
    TRANSCRIPT_FAILED,
    RESIDENT_FAILED,
    ENGINE_UPDATE_FAILED,
    IMPORT_FAILED,
    START_FAILED,
    STOP_FAILED,
    CANCEL_FAILED,
    AUTO_FAILED,
    RECORDING_INTERRUPTED,
    LIVE_STOPPED_WITH_ERROR,
    LIVE_FAILED,
    LIVE_START_FAILED,
    LIVE_STOP_FAILED,
    KB_EXPORT_FAILED,
];

/// Сводка `/state` + `/jobs`, из которой рисуется трей.
#[derive(Debug, Clone, PartialEq)]
pub struct View {
    pub recording: bool,
    pub auto: bool,
    /// `"auto"` | `"manual"`; `None` — запись не идёт.
    pub source: Option<String>,
    pub elapsed_s: f64,
    /// Идёт какая-нибудь задача (расшифровка, импорт, загрузка модели).
    pub busy: bool,
    /// Папки (= id записей) завершённых расшифровок и импортов. Повтор папки —
    /// повторная расшифровка; поэтому это мультимножество, а не множество.
    pub jobs_done: Vec<String>,
    /// (папка, текст ошибки) упавших расшифровок и импортов.
    pub jobs_failed: Vec<(String, String)>,
    /// Чем кончилась последняя запись, по словам резидента.
    pub last_stop: Option<LastStop>,
    /// Резидент сообщает причину остановок (в `/state` есть ключ `last_stop`,
    /// пусть и `null`). Старый резидент — нет, и тогда отмену из трея
    /// распознаёт `CancelMark`.
    pub reports_stops: bool,
    /// Запись с ассистентом (`/state.live`). Обычная запись при этом не идёт:
    /// `recording` — только про неё.
    pub live: Live,
    /// Последний сбой автоматической выгрузки в базу знаний. Резидент
    /// сообщает о каждой встрече не больше одного раза; новое `at` — новое
    /// уведомление.
    pub kb_failed: Option<KbFailure>,
    /// Выбранный в настройках микрофон или вывод не найден — идущая запись
    /// (своя или ассистента) пишет с системного (`/state.devices_fallback`).
    pub devices_fallback: Vec<DeviceFallback>,
}

/// Длиннее — имя устройства в подсказке трея сокращается: у Windows на всю
/// подсказку 127 символов.
const FALLBACK_NAME_MAX: usize = 32;
/// szTip — 128 UTF-16 с завершающим нулём; tray-icon копирует не больше 128,
/// и более длинная строка осталась бы без нуля.
const TOOLTIP_MAX: usize = 127;

/// Элемент `/state.devices_fallback`: что было выбрано и не нашлось.
#[derive(Debug, Clone, PartialEq)]
pub struct DeviceFallback {
    /// Микрофон (`"mic"`); иначе устройство вывода (`"output"`).
    pub mic: bool,
    pub name: String,
}

impl DeviceFallback {
    fn list(value: Option<&Value>) -> Vec<DeviceFallback> {
        let Some(items) = value.and_then(Value::as_array) else {
            return Vec::new();
        };
        items
            .iter()
            .filter_map(|item| {
                let mic = match str_at(item, "kind")? {
                    "mic" => true,
                    "output" => false,
                    _ => return None,
                };
                let name = str_at(item, "name").unwrap_or_default().to_string();
                Some(DeviceFallback { mic, name })
            })
            .collect()
    }

    /// Та же строка, что у кнопки записи в окне (RecordingBadge).
    pub fn text(&self) -> String {
        let name = shorten(&self.name, FALLBACK_NAME_MAX);
        if self.mic {
            format!("Микрофон «{name}» не найден — запись с системного")
        } else {
            format!("Устройство вывода «{name}» не найдено — запись с системного")
        }
    }
}

/// Подсказка трея в пределах szTip: считаем UTF-16, а не символы.
fn fit_tooltip(text: String) -> String {
    if text.encode_utf16().count() <= TOOLTIP_MAX {
        return text;
    }
    let mut out = String::new();
    let mut used = 1; // место под «…»
    for ch in text.chars() {
        used += ch.len_utf16();
        if used > TOOLTIP_MAX {
            break;
        }
        out.push(ch);
    }
    out.push('…');
    out
}

/// `/state.kb_export_failed`: какая встреча, почему и когда (epoch).
#[derive(Debug, Clone, PartialEq)]
pub struct KbFailure {
    pub folder: String,
    pub error: String,
    pub at: f64,
}

impl KbFailure {
    fn from_json(value: &Value) -> Option<KbFailure> {
        Some(KbFailure {
            folder: str_at(value, "folder")?.to_string(),
            error: str_at(value, "error").unwrap_or_default().to_string(),
            at: value.get("at").and_then(Value::as_f64).unwrap_or(0.0),
        })
    }
}

/// `/state.live` без `started_at`: секундомер — забота панели, а меню и
/// уведомлениям он не нужен.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Live {
    /// Ассистент слушает встречу (модель загружена, запись идёт). Остаётся
    /// `true` и во время остановки — пока ассистент дописывает запись.
    pub active: bool,
    /// Процесс запущен, модель ещё грузится.
    pub starting: bool,
    /// Остановка запрошена, ассистент дописывает запись.
    pub stopping: bool,
    pub folder: Option<String>,
    /// Почему упал или остановился с ошибкой последний запуск; сбрасывается
    /// следующим стартом.
    pub error: Option<String>,
}

impl Live {
    fn from_json(value: &Value) -> Live {
        let flag = |key: &str| value.get(key).and_then(Value::as_bool).unwrap_or(false);
        let text = |key: &str| {
            str_at(value, key)
                .map(str::trim)
                .filter(|text| !text.is_empty())
                .map(str::to_string)
        };
        Live {
            active: flag("active"),
            starting: flag("starting"),
            stopping: flag("stopping"),
            folder: text("folder"),
            error: text("error"),
        }
    }

    /// Процесс ассистента жив: грузится, слушает или дописывает.
    pub fn running(&self) -> bool {
        self.active || self.starting || self.stopping
    }
}

/// `/state.last_stop`: папка, причина (`saved` | `discarded` | `short`) и
/// время (epoch). По времени остановки различаются между собой.
#[derive(Debug, Clone, PartialEq)]
pub struct LastStop {
    pub folder: String,
    pub reason: String,
    pub at: f64,
}

impl LastStop {
    fn from_json(value: &Value) -> Option<LastStop> {
        Some(LastStop {
            folder: str_at(value, "folder").unwrap_or_default().to_string(),
            reason: str_at(value, "reason")?.to_string(),
            at: value.get("at").and_then(Value::as_f64).unwrap_or(0.0),
        })
    }
}

impl View {
    pub fn from_json(state: &Value, jobs: &Value) -> View {
        let mut view = View {
            recording: str_at(state, "status") == Some("recording"),
            auto: state
                .pointer("/auto_record/enabled")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            source: str_at(state, "source").map(str::to_string),
            elapsed_s: state
                .get("elapsed_s")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
            busy: false,
            jobs_done: Vec::new(),
            jobs_failed: Vec::new(),
            last_stop: state.get("last_stop").and_then(LastStop::from_json),
            reports_stops: state.get("last_stop").is_some(),
            live: state.get("live").map(Live::from_json).unwrap_or_default(),
            kb_failed: state.get("kb_export_failed").and_then(KbFailure::from_json),
            devices_fallback: DeviceFallback::list(state.get("devices_fallback")),
        };
        let items = jobs
            .get("items")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default();
        for job in items {
            // Только расшифровка и импорт: итоги и вопросы (summary/ask) —
            // не «расшифровываю», у них своя очередь и своё место в окне.
            if !matches!(str_at(job, "kind"), Some("transcribe" | "import")) {
                continue;
            }
            let job_state = str_at(job, "state").unwrap_or_default();
            if job_state == "running" {
                view.busy = true;
            }
            let Some(id) = str_at(job, "folder").and_then(recording_id) else {
                continue;
            };
            match job_state {
                "done" => view.jobs_done.push(id),
                "failed" => {
                    let error = str_at(job, "error")
                        .filter(|text| !text.trim().is_empty())
                        .unwrap_or("неизвестная ошибка");
                    view.jobs_failed.push((id, error.to_string()));
                }
                _ => {}
            }
        }
        view
    }
}

fn str_at<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value.get(key).and_then(Value::as_str)
}

/// Имя папки записи — её id. Разделители обоих видов: резидент пишет пути
/// Windows, а тесты гоняются где угодно.
fn recording_id(folder: &str) -> Option<String> {
    folder
        .split(['/', '\\'])
        .rev()
        .find(|part| !part.is_empty())
        .map(str::to_string)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Notice {
    pub title: String,
    pub body: String,
    /// Запись, к которой относится уведомление (см. `remember`).
    pub recording: Option<String>,
}

impl Notice {
    fn new(title: &str, body: impl Into<String>, recording: Option<String>) -> Notice {
        Notice {
            title: title.to_string(),
            body: body.into(),
            recording,
        }
    }
}

/// Что изменилось между двумя снимками — то и сообщаем. Первый снимок (`prev =
/// None`) — точка отсчёта: запись, идущая на момент запуска оболочки, и старые
/// готовые расшифровки уведомлений не дают.
pub fn transitions(prev: Option<&View>, next: &View) -> Vec<Notice> {
    let Some(prev) = prev else {
        return Vec::new();
    };
    let mut out = Vec::new();
    if !prev.recording && next.recording {
        if next.source.as_deref() == Some("auto") {
            out.push(Notice::new(
                AUTO_RECORDING_STARTED,
                "Автозапись по звонку",
                None,
            ));
        } else {
            out.push(Notice::new(
                RECORDING_STARTED,
                "Остановить — в меню значка Meet",
                None,
            ));
        }
    }
    // Отменённая запись не «сохранена»: причину говорит сам резидент, если
    // умеет (`last_stop`). Считается только свежая причина — та, что
    // появилась вместе с этой остановкой; без неё — как раньше, «сохранена»
    // (отмену из трея тогда уберёт `CancelMark`).
    let fresh_stop = next
        .last_stop
        .as_ref()
        .filter(|stop| prev.last_stop.as_ref() != Some(*stop));
    let discarded = fresh_stop.is_some_and(|stop| stop.reason == "discarded");
    if prev.recording && !next.recording && !discarded {
        // Будет ли расшифровка (`auto_transcribe`), оболочка не знает — о ней
        // скажет своё уведомление, когда задача закончится.
        out.push(Notice::new(
            RECORDING_SAVED,
            "Запись остановлена и сохранена",
            None,
        ));
    }
    if !prev.live.active && next.live.active {
        out.push(Notice::new(
            LIVE_LISTENING,
            "Остановить — в меню значка Meet",
            None,
        ));
    }
    if prev.live.running() && !next.live.running() {
        out.push(live_ended(&prev.live, &next.live));
    }
    for id in added(&prev.jobs_done, &next.jobs_done) {
        out.push(Notice::new(
            TRANSCRIPT_READY,
            format!("{id} — откройте окно Meet из области уведомлений"),
            Some(id.clone()),
        ));
    }
    for (id, error) in added(&prev.jobs_failed, &next.jobs_failed) {
        out.push(Notice::new(
            TRANSCRIPT_FAILED,
            shorten(error, ERROR_CHARS),
            Some(id.clone()),
        ));
    }
    if let Some(failed) = next
        .kb_failed
        .as_ref()
        .filter(|failed| prev.kb_failed.as_ref() != Some(*failed))
    {
        out.push(Notice::new(
            KB_EXPORT_FAILED,
            shorten(&failed.error, ERROR_CHARS),
            recording_id(&failed.folder),
        ));
    }
    out
}

/// Ассистент был жив (`was`) и больше нет (`now`). Различаем по снимку, без
/// событий шины: ошибку резидент оставляет в `live.error` до следующего
/// старта, а была ли просьба остановиться — видно по прошлому снимку.
///
/// `live.failed` всегда несёт ошибку, а `live.stopped` — только неудачный:
/// ошибки нет — это штатная остановка (или ассистент вышел сам, кодом 0).
/// Ошибка без просьбы остановиться — падение.
fn live_ended(was: &Live, now: &Live) -> Notice {
    let recording = was.folder.as_deref().and_then(recording_id);
    match now.error.as_deref() {
        None if was.active => Notice::new(
            LIVE_SAVED,
            "О готовой расшифровке придёт отдельное уведомление",
            recording,
        ),
        None => Notice::new(LIVE_CANCELLED, "Запись не началась", None),
        Some(error) if was.stopping => Notice::new(
            LIVE_STOPPED_WITH_ERROR,
            shorten(error, ERROR_CHARS),
            recording,
        ),
        Some(error) => Notice::new(LIVE_FAILED, shorten(error, ERROR_CHARS), recording),
    }
}

/// Окно ассистента по фронту `live.active`: `Some(true)` — открыть,
/// `Some(false)` — закрыть, `None` — ничего. Только по фронтам: закрытое
/// человеком окно не возвращается до следующего старта.
pub fn live_window_change(was_active: bool, active: bool) -> Option<bool> {
    (was_active != active).then_some(active)
}

/// Элементы `next`, которых нет в `prev`, с учётом кратности.
fn added<'a, T: Eq + Hash>(prev: &[T], next: &'a [T]) -> Vec<&'a T> {
    let mut seen: HashMap<&T, usize> = HashMap::new();
    for item in prev {
        *seen.entry(item).or_default() += 1;
    }
    next.iter()
        .filter(|item| match seen.get_mut(item) {
            Some(count) if *count > 0 => {
                *count -= 1;
                false
            }
            _ => true,
        })
        .collect()
}

fn shorten(text: &str, limit: usize) -> String {
    let text = text.trim();
    if text.chars().count() <= limit {
        return text.to_string();
    }
    let mut short: String = text.chars().take(limit - 1).collect();
    short.push('…');
    short
}

/// Память опроса между тиками.
///
/// IMPORTANT: промах связи не затирает последний снимок — сравниваем всегда с
/// последним *полученным*. Иначе восстановление связи было бы «первым
/// снимком» и глотало бы всё, что случилось за время обрыва.
///
/// Но `recording → (обрыв) → idle` — это не штатная остановка: резидент упал
/// или перезапустился посреди записи, и сообщать «Запись сохранена» было бы
/// неправдой. После обрыва (не меньше `OFFLINE_AFTER_MISSES` промахов) такой
/// переход сообщается как «Запись прервана». Единичный промах — не обрыв:
/// остановка, попавшая на него, остаётся «сохранена».
#[derive(Default)]
pub struct Tracker {
    last: Option<View>,
    misses: u32,
}

impl Tracker {
    /// Очередной опрос → (что показывать, что сообщить).
    pub fn observe(&mut self, polled: Option<View>) -> (Option<View>, Vec<Notice>) {
        match polled {
            Some(view) => {
                let after_gap = self.misses >= OFFLINE_AFTER_MISSES;
                self.misses = 0;
                let mut notices = transitions(self.last.as_ref(), &view);
                if after_gap {
                    // Ассистент — та же запись: «остановлен — расшифровываю»
                    // после обрыва было бы неправдой.
                    for notice in notices
                        .iter_mut()
                        .filter(|n| n.title == RECORDING_SAVED || n.title == LIVE_SAVED)
                    {
                        *notice = Notice::new(
                            RECORDING_INTERRUPTED,
                            "Служба записи перезапустилась во время записи — часть встречи могла не сохраниться",
                            None,
                        );
                    }
                }
                self.last = Some(view.clone());
                (Some(view), notices)
            }
            None => {
                self.misses = self.misses.saturating_add(1);
                let shown = if self.misses < OFFLINE_AFTER_MISSES {
                    self.last.clone()
                } else {
                    None
                };
                (shown, Vec::new())
            }
        }
    }
}

/// «Отменить запись» из трея останавливает запись без сохранения — сообщать о
/// ней «Запись сохранена» было бы неправдой. Отметка привязана к ближайшему
/// переходу `recording → idle`, а не к окну времени, и снимается неудачной
/// отменой: иначе она проглотила бы следующую настоящую остановку. Отмену из
/// окна трей не видит.
///
/// Отметка ставится до запроса (`requested`), а не по ответу: резидент снимает
/// флаг записи раньше, чем отвечает, и опрос может увидеть idle до ответа.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
enum CancelMark {
    #[default]
    None,
    /// `/recording/cancel` отправлен, ответа ещё нет.
    InFlight,
    /// Резидент подтвердил отмену (`action: "cancelled"`); остановку ещё не
    /// видели.
    Confirmed,
    /// Остановку увидели (и промолчали) раньше, чем пришёл ответ.
    SeenEarly,
}

impl CancelMark {
    fn requested(self) -> CancelMark {
        CancelMark::InFlight
    }

    /// Ответ на `/recording/cancel`: `confirmed` — отмена состоялась.
    fn replied(self, confirmed: bool) -> CancelMark {
        match (self, confirmed) {
            (CancelMark::InFlight, true) => CancelMark::Confirmed,
            // Неудача снимает отметку; SeenEarly — отмена уже отработала.
            _ => CancelMark::None,
        }
    }

    /// Уведомления очередного тика с учётом отметки → (новая отметка, что
    /// показывать).
    /// `settle` с учётом резидента: если он сам сообщает причину остановки
    /// (`View::reports_stops`), отмену уже отсеял `transitions`, а отметка
    /// не нужна — снимаем её, чтобы она не проглотила чужую остановку.
    fn settle_for(self, view: Option<&View>, notices: Vec<Notice>) -> (CancelMark, Vec<Notice>) {
        if view.is_some_and(|view| view.reports_stops) {
            return (CancelMark::None, notices);
        }
        self.settle(notices)
    }

    fn settle(self, notices: Vec<Notice>) -> (CancelMark, Vec<Notice>) {
        let has = |title: &str| notices.iter().any(|notice| notice.title == title);
        if has(RECORDING_INTERRUPTED) {
            // Запись оборвалась сама — отменять больше нечего, а сообщение о
            // сбое важнее.
            return (CancelMark::None, notices);
        }
        if !has(RECORDING_SAVED) {
            return (self, notices);
        }
        let next = match self {
            CancelMark::InFlight => CancelMark::SeenEarly,
            CancelMark::Confirmed => CancelMark::None,
            CancelMark::None | CancelMark::SeenEarly => return (self, notices),
        };
        let kept = notices
            .into_iter()
            .filter(|notice| notice.title != RECORDING_SAVED)
            .collect();
        (next, kept)
    }
}

fn cancel_confirmed(reply: Option<&api::Result<Value>>) -> bool {
    matches!(reply, Some(Ok(body)) if str_at(body, "action") == Some("cancelled"))
}

/// Команда резиденту из меню трея.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Start,
    Stop,
    Cancel,
    AutoRecord(bool),
    /// Запись с ассистентом.
    LiveStart,
    LiveStop,
}

impl Action {
    fn path(self) -> &'static str {
        match self {
            Action::Start => "/recording/start",
            Action::Stop => "/recording/stop",
            Action::Cancel => "/recording/cancel",
            Action::AutoRecord(_) => "/auto-record",
            Action::LiveStart => "/live/start",
            Action::LiveStop => "/live/stop",
        }
    }

    fn body(self) -> Value {
        match self {
            Action::AutoRecord(enabled) => json!({ "enabled": enabled }),
            _ => Value::Null,
        }
    }

    fn failure_title(self) -> &'static str {
        match self {
            Action::Start => START_FAILED,
            Action::Stop => STOP_FAILED,
            Action::Cancel => CANCEL_FAILED,
            Action::AutoRecord(_) => AUTO_FAILED,
            Action::LiveStart => LIVE_START_FAILED,
            Action::LiveStop => LIVE_STOP_FAILED,
        }
    }
}

/// Итог команды → уведомление о неудаче или `None`, если всё прошло.
/// `reply = None` — резидент не опубликовал адрес (`daemon.json` нет).
///
/// Резидент отвечает на отказ не ошибкой, а `200 {"ok": false, "action": …}`
/// («запись уже идёт», «запись не идёт») — это тоже неудача для человека,
/// нажавшего пункт меню.
pub fn action_notice(action: Action, reply: Option<&api::Result<Value>>) -> Option<Notice> {
    let body = match reply {
        None => "Служба записи не запущена".to_string(),
        Some(Err(api::Error::Transport(_))) => "Служба записи не отвечает".to_string(),
        Some(Err(api::Error::Status { message, .. })) => message.clone(),
        Some(Err(error)) => error.to_string(),
        Some(Ok(reply)) => {
            let refused = reply.get("ok").and_then(Value::as_bool) == Some(false);
            let error = str_at(reply, "error").filter(|text| !text.trim().is_empty());
            if !refused && error.is_none() {
                return None;
            }
            match (str_at(reply, "action"), error) {
                (Some("already-recording"), _) => "Запись уже идёт".to_string(),
                (Some("not-recording"), _) => "Запись не идёт".to_string(),
                (Some("not-live"), _) => "Ассистент не запущен".to_string(),
                (_, Some(error)) => error.to_string(),
                _ => "Служба записи отклонила команду".to_string(),
            }
        }
    };
    Some(Notice::new(
        action.failure_title(),
        shorten(&body, ERROR_CHARS),
        None,
    ))
}

/// `/live/start` ответил 409: не подключён ни Claude Code, ни Codex. Кроме
/// уведомления — открыть окно на разделе настроек ассистента.
pub fn needs_provider(action: Action, reply: Option<&api::Result<Value>>) -> bool {
    action == Action::LiveStart && matches!(reply, Some(Err(api::Error::Status { code: 409, .. })))
}

/// Значок трея — кольцо Meet (`scripts/make_app_icons.py`): приглушённое —
/// ждём; яркое с красной точкой — запись; яркое с точкой в центре — слушает
/// ассистент; с разрывом — занят; приглушённое с янтарной точкой — нет связи
/// со службой записи или движка.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayIconKind {
    Idle,
    Recording,
    Live,
    Busy,
    Offline,
}

/// Нарисованные размеры значка трея: масштаб 100, 125, 150 и 200 %.
pub const TRAY_SIZES: [u32; 4] = [16, 20, 24, 32];

/// PNG состояния во всех размерах `TRAY_SIZES` (вшиты в exe).
macro_rules! tray_pngs {
    ($state:literal) => {
        [
            include_bytes!(concat!("../icons/tray-", $state, "-16.png")).as_slice(),
            include_bytes!(concat!("../icons/tray-", $state, "-20.png")).as_slice(),
            include_bytes!(concat!("../icons/tray-", $state, "-24.png")).as_slice(),
            include_bytes!(concat!("../icons/tray-", $state, "-32.png")).as_slice(),
        ]
    };
}

impl TrayIconKind {
    #[cfg(test)]
    pub const ALL: [TrayIconKind; 5] = [
        TrayIconKind::Idle,
        TrayIconKind::Recording,
        TrayIconKind::Live,
        TrayIconKind::Busy,
        TrayIconKind::Offline,
    ];

    /// PNG размера `size` из `TRAY_SIZES` (другой — самый крупный).
    #[cfg_attr(target_os = "macos", allow(dead_code))]
    fn png(self, size: u32) -> &'static [u8] {
        let set: [&'static [u8]; 4] = match self {
            TrayIconKind::Idle => tray_pngs!("idle"),
            TrayIconKind::Recording => tray_pngs!("recording"),
            TrayIconKind::Live => tray_pngs!("live"),
            TrayIconKind::Busy => tray_pngs!("busy"),
            TrayIconKind::Offline => tray_pngs!("offline"),
        };
        let at = TRAY_SIZES
            .iter()
            .position(|&s| s == size)
            .unwrap_or(TRAY_SIZES.len() - 1);
        set[at]
    }

    /// Шаблон строки меню macOS (@2x, 36 px): одноцветный, macOS красит его
    /// сама; tray-icon приводит картинку к 18 pt.
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    fn template_png(self) -> &'static [u8] {
        match self {
            TrayIconKind::Idle => include_bytes!("../icons/tray-template-idle@2x.png").as_slice(),
            TrayIconKind::Recording => {
                include_bytes!("../icons/tray-template-recording@2x.png").as_slice()
            }
            TrayIconKind::Live => include_bytes!("../icons/tray-template-live@2x.png").as_slice(),
            TrayIconKind::Busy => include_bytes!("../icons/tray-template-busy@2x.png").as_slice(),
            TrayIconKind::Offline => {
                include_bytes!("../icons/tray-template-offline@2x.png").as_slice()
            }
        }
    }

    /// Значок состояния: на Windows — цветной PNG размера `size`, в строке
    /// меню macOS — шаблон (`template_png`).
    fn image(self, size: u32) -> tauri::Result<Image<'static>> {
        #[cfg(target_os = "macos")]
        {
            let _ = size;
            Image::from_bytes(self.template_png())
        }
        #[cfg(not(target_os = "macos"))]
        Image::from_bytes(self.png(size))
    }
}

/// Размер значка трея по метрике Windows (SM_CXSMICON: 16 при 100 %, 20 при
/// 125 %…): наименьший нарисованный не меньше её — Windows не растягивает
/// картинку, края остаются резкими. Крупнее 32 — растянет 32; метрики нет
/// (0) — тоже 32, как раньше.
pub fn pick_tray_size(metric: i32) -> u32 {
    let largest = TRAY_SIZES[TRAY_SIZES.len() - 1];
    if metric <= 0 {
        return largest;
    }
    TRAY_SIZES
        .iter()
        .copied()
        .find(|&size| i64::from(size) >= i64::from(metric))
        .unwrap_or(largest)
}

#[cfg(windows)]
fn tray_size() -> u32 {
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSMICON};
    // SAFETY: GetSystemMetrics не принимает указателей; ошибка — 0.
    pick_tray_size(unsafe { GetSystemMetrics(SM_CXSMICON) })
}

#[cfg(not(windows))]
fn tray_size() -> u32 {
    pick_tray_size(0)
}

/// Запись важнее расшифровки: идёт встреча — это главное, что нужно видеть.
/// Ассистент слушает или дописывает — свой значок; грузит модель — «занят».
/// Без связи с резидентом: запуск, обновление движка и выход — «занят» (это
/// ненадолго и не сбой), остальное — «нет связи».
pub fn icon_for(view: Option<&View>, status: &ResidentStatus) -> TrayIconKind {
    match view {
        None => match status {
            ResidentStatus::Starting
            | ResidentStatus::EngineUpdating { .. }
            | ResidentStatus::Quitting => TrayIconKind::Busy,
            _ => TrayIconKind::Offline,
        },
        Some(view) if view.recording => TrayIconKind::Recording,
        Some(view) if view.live.active || view.live.stopping => TrayIconKind::Live,
        Some(view) if view.live.starting || view.busy => TrayIconKind::Busy,
        Some(_) => TrayIconKind::Idle,
    }
}

pub fn tooltip(view: Option<&View>, status: &ResidentStatus) -> String {
    if *status == ResidentStatus::Quitting {
        return "Meet · сохранение записи и выход".to_string();
    }
    if *status == ResidentStatus::ExternalNoApi {
        return "Meet · запущена старая версия службы записи (меню недоступно)".to_string();
    }
    if *status == ResidentStatus::EngineMissing {
        return "Meet · движок не установлен, откройте окно".to_string();
    }
    if let ResidentStatus::EngineUpdating { step, of } = *status {
        return if step > 0 {
            format!("Meet · обновление движка: шаг {step} из {of}")
        } else {
            "Meet · обновление движка".to_string()
        };
    }
    let text = match view {
        None => "служба записи не запущена".to_string(),
        Some(view) if view.recording => {
            let mut text = format!("идёт запись {}", clock(view.elapsed_s));
            if view.source.as_deref() == Some("auto") {
                text.push_str(" (авто)");
            }
            text
        }
        Some(view) if view.live.stopping => "ассистент завершает запись".to_string(),
        Some(view) if view.live.active => "ассистент слушает встречу".to_string(),
        Some(view) if view.live.starting => "ассистент запускается".to_string(),
        Some(view) if view.busy => "идёт расшифровка".to_string(),
        Some(_) if *status == ResidentStatus::External => {
            "служба записи запущена вне приложения".to_string()
        }
        Some(_) => "ожидание звонка".to_string(),
    };
    let mut tip = format!("Meet · {text}");
    // Подмена устройства — про идущую запись (свою или ассистента).
    if let Some(view) = view.filter(|view| view.recording || view.live.active) {
        for fallback in &view.devices_fallback {
            tip.push('\n');
            tip.push_str(&fallback.text());
        }
    }
    fit_tooltip(tip)
}

/// 754 → «12:34», 3723 → «1:02:03».
fn clock(seconds: f64) -> String {
    let total = if seconds.is_finite() && seconds > 0.0 {
        seconds as u64
    } else {
        0
    };
    let (hours, minutes, secs) = (total / 3600, total / 60 % 60, total % 60);
    if hours > 0 {
        format!("{hours}:{minutes:02}:{secs:02}")
    } else {
        format!("{minutes:02}:{secs:02}")
    }
}

/// То, от чего зависит меню. Пересобираем его только при смене этого, а не на
/// каждом тике: секунды записи в меню не видны.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MenuState {
    /// Резидент отвечает — действия с записью доступны.
    pub online: bool,
    pub recording: bool,
    pub live: LivePhase,
    pub auto: bool,
    /// Журнал упавшего резидента (пункт «Открыть журнал»).
    pub log: Option<PathBuf>,
    /// Резидент сдался — пункт «Перезапустить службу записи».
    pub restart: bool,
    /// Идёт «Выход»: меню целиком недоступно.
    pub quitting: bool,
}

/// Где ассистент, с точки зрения меню.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LivePhase {
    #[default]
    Off,
    Starting,
    Active,
    Stopping,
}

impl LivePhase {
    fn of(live: &Live) -> LivePhase {
        if live.stopping {
            LivePhase::Stopping
        } else if live.active {
            LivePhase::Active
        } else if live.starting {
            LivePhase::Starting
        } else {
            LivePhase::Off
        }
    }
}

/// «Открыть» — что именно: окно приложения.
pub const OPEN_LABEL: &str = "Открыть Meet";
/// «Выход», пока идёт работа: вопрос с безопасной кнопкой первой (по умолчанию).
pub const QUIT_TITLE: &str = "Выход из Meet";
pub const QUIT_RECORDING: &str = "Идёт запись — при выходе она остановится и сохранится.";
pub const QUIT_TRANSCRIBE: &str =
    "Идёт расшифровка — при выходе она прервётся и начнётся заново при следующем запуске Meet.";
pub const QUIT_UNSAVED: &str =
    "В настройках есть несохранённые изменения — при выходе они пропадут.";
pub const QUIT_ASK: &str = "Выйти?";
pub const QUIT_CONFIRM: &str = "Выйти";
pub const QUIT_KEEP: &str = "Не выходить";

/// Окно сообщает, что в настройках есть несохранённое (`set_settings_dirty`):
/// «Выход» из трея тогда тоже спрашивает.
static SETTINGS_DIRTY: AtomicBool = AtomicBool::new(false);

#[tauri::command]
pub fn set_settings_dirty(dirty: bool) {
    SETTINGS_DIRTY.store(dirty, Ordering::SeqCst);
}

pub fn settings_dirty() -> bool {
    SETTINGS_DIRTY.load(Ordering::SeqCst)
}

/// Чем занята задача резидента — словами для вопроса о выходе.
fn job_kind_text(kind: &str) -> &'static str {
    match kind {
        "download-model" => "загрузка модели",
        "install-engine" => "установка движка",
        "summary" | "ask" => "итоги и вопросы",
        "analyze" => "анализ встречи",
        "improve" => "улучшение расшифровки",
        "profile" => "профиль человека",
        "speaker_split" | "rediarize" => "разделение на спикеров",
        _ => "фоновая задача",
    }
}

/// О чём спросить перед «Выход»: идёт запись, расшифровка или другая работа
/// резидента, есть несохранённые настройки — вопрос с перечнем того, что
/// пропадёт или прервётся; ничего такого нет — None, выходим сразу.
pub fn quit_question(
    state: Option<&Value>,
    jobs: Option<&Value>,
    settings_dirty: bool,
) -> Option<String> {
    let mut lines: Vec<String> = Vec::new();
    if let Some(state) = state {
        if crate::upgrade::resident_busy(state) {
            lines.push(QUIT_RECORDING.to_string());
        }
        if crate::upgrade::resident_working(state, jobs) {
            let active: Vec<&str> = jobs
                .and_then(|jobs| jobs.get("items"))
                .and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter(|job| {
                            matches!(
                                job.get("state").and_then(Value::as_str),
                                Some("queued" | "running")
                            )
                        })
                        .filter_map(|job| job.get("kind").and_then(Value::as_str))
                        .collect()
                })
                .unwrap_or_default();
            if active
                .iter()
                .any(|kind| matches!(*kind, "transcribe" | "import" | "merge"))
            {
                lines.push(QUIT_TRANSCRIBE.to_string());
            }
            // Каждое занятие — один раз, в порядке очереди.
            let mut other: Vec<&str> = Vec::new();
            for kind in active
                .iter()
                .filter(|kind| !matches!(**kind, "transcribe" | "import" | "merge"))
            {
                let text = job_kind_text(kind);
                if !other.contains(&text) {
                    other.push(text);
                }
            }
            if !other.is_empty() {
                lines.push(format!(
                    "Идёт фоновая работа ({}) — при выходе она прервётся.",
                    other.join(", ")
                ));
            } else if active.is_empty() {
                lines.push("Идёт фоновая работа — при выходе она прервётся.".to_string());
            }
        }
    }
    if settings_dirty {
        lines.push(QUIT_UNSAVED.to_string());
    }
    if lines.is_empty() {
        return None;
    }
    lines.push(QUIT_ASK.to_string());
    Some(lines.join("\n"))
}

/// Ответ на вопрос о выходе: выходим только по явной кнопке «Выйти».
pub fn quit_confirmed_by(answer: &rfd::MessageDialogResult) -> bool {
    matches!(answer, rfd::MessageDialogResult::Custom(label) if label == QUIT_CONFIRM)
}

/// Остановка записи (обычной и с ассистентом): записанное сохраняется.
pub const STOP_LABEL: &str = "Остановить и сохранить";
/// Отмена записи: многоточие — перед удалением спрашиваем подтверждение.
pub const CANCEL_LABEL: &str = "Отменить запись…";
pub const CANCEL_TITLE: &str = "Отменить запись";
pub const CANCEL_QUESTION: &str = "Удалить текущую запись? Записанное не сохранится.";
pub const CANCEL_CONFIRM: &str = "Удалить";
pub const CANCEL_KEEP: &str = "Продолжить запись";
/// Пока был открыт вопрос, запись закончилась или началась другая.
pub const CANCEL_STALE: &str =
    "Пока был открыт вопрос, запись закончилась или началась новая — ничего не удалено";

/// Пункты записи в верхней части меню: (id, текст, доступен ли). Пока идёт
/// любая запись — обычная или с ассистентом — пунктов «Начать…» нет. У
/// ассистента своя остановка (`/live/stop`) и нет отмены: резидент её не
/// умеет. Отмены обычной записи здесь тоже нет: она внизу меню, отдельно от
/// остановки (`menu_model`), — рядом их легко перепутать.
pub fn record_items(state: &MenuState) -> Vec<(&'static str, &'static str, bool)> {
    let online = state.online;
    if state.recording {
        return vec![("stop", STOP_LABEL, online)];
    }
    match state.live {
        LivePhase::Off => vec![
            ("start", "Начать запись", online),
            ("live-start", "Начать запись с ассистентом", online),
        ],
        LivePhase::Starting => vec![
            ("live-starting", "Ассистент запускается…", false),
            ("live-stop", STOP_LABEL, online),
        ],
        LivePhase::Active => vec![("live-stop", STOP_LABEL, online)],
        // Остановка уже идёт — второй раз нажимать нечего.
        LivePhase::Stopping => vec![("live-stop", STOP_LABEL, false)],
    }
}

/// Пункт меню трея (на macOS — меню значка в строке меню, то же самое).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Entry {
    Item {
        id: &'static str,
        text: &'static str,
        enabled: bool,
    },
    Check {
        id: &'static str,
        text: &'static str,
        enabled: bool,
        checked: bool,
    },
    Separator,
}

/// Меню целиком, по порядку: «Открыть», запись (начать / остановить и
/// сохранить), импорт, «Автозапись», разделитель, служебные пункты, «Отменить
/// запись…» (только пока идёт запись — внизу, подальше от остановки) и
/// «Выход». Пока идёт «Выход», недоступно всё.
pub fn menu_model(state: &MenuState) -> Vec<Entry> {
    let usable = !state.quitting;
    let item = |id, text, enabled: bool| Entry::Item {
        id,
        text,
        enabled: enabled && usable,
    };
    let mut model = vec![item("open", OPEN_LABEL, true)];
    for (id, text, enabled) in record_items(state) {
        model.push(item(id, text, enabled));
    }
    model.push(item("import", "Импортировать файл…", state.online));
    // id несёт действие: клик по включённой галочке выключает, и наоборот —
    // обработчику не нужно гадать о текущем состоянии.
    model.push(Entry::Check {
        id: if state.auto { "auto-off" } else { "auto-on" },
        text: "Автозапись",
        enabled: state.online && usable,
        checked: state.auto,
    });
    model.push(Entry::Separator);
    if state.log.is_some() {
        model.push(item("log", "Открыть журнал", true));
    }
    if state.restart {
        model.push(item("restart", "Перезапустить службу записи", true));
    }
    if state.recording {
        model.push(item("cancel", CANCEL_LABEL, state.online));
    }
    model.push(item("quit", "Выход", true));
    model
}

/// Отмена всё ещё про ту запись, о которой спрашивали: папка идущей записи
/// та же, что при открытии вопроса (`/recording/cancel` своего id не знает —
/// без проверки «Удалить» стёрло бы запись, начатую автозаписью, пока окно
/// вопроса висело за другими окнами).
pub fn cancel_still_meant(asked: Option<&str>, now: Option<&str>) -> bool {
    matches!((asked, now), (Some(asked), Some(now)) if asked == now)
}

/// Ответ на вопрос «Удалить текущую запись?»: удаляем только по явной кнопке
/// «Удалить». Esc, закрытие окна и «Продолжить запись» запись не трогают.
pub fn cancel_confirmed_by(answer: &rfd::MessageDialogResult) -> bool {
    matches!(answer, rfd::MessageDialogResult::Custom(label) if label == CANCEL_CONFIRM)
}

pub fn menu_state(view: Option<&View>, status: &ResidentStatus) -> MenuState {
    let quitting = *status == ResidentStatus::Quitting;
    let failed = matches!(status, ResidentStatus::Failed { .. });
    MenuState {
        online: view.is_some() && !quitting,
        recording: view.is_some_and(|view| view.recording),
        live: view
            .map(|view| LivePhase::of(&view.live))
            .unwrap_or_default(),
        auto: view.is_some_and(|view| view.auto),
        log: match status {
            ResidentStatus::Failed { log } => Some(log.clone()),
            _ => None,
        },
        restart: failed,
        quitting,
    }
}

/// `ui.notifications` из настроек резидента.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Level {
    #[default]
    All,
    Important,
    Off,
}

impl Level {
    pub fn from_settings(settings: &Value) -> Level {
        match settings
            .pointer("/ui/notifications")
            .and_then(Value::as_str)
            .map(str::trim)
        {
            Some("important") => Level::Important,
            Some("off") => Level::Off,
            _ => Level::All,
        }
    }
}

pub fn filter(notices: Vec<Notice>, level: Level) -> Vec<Notice> {
    match level {
        Level::All => notices,
        Level::Important => notices
            .into_iter()
            .filter(|notice| important(&notice.title))
            .collect(),
        Level::Off => Vec::new(),
    }
}

/// Важное уведомление: из списка `IMPORTANT` или о смене версии приложения
/// (его заголовок несёт номер версии — сверяем по началу).
fn important(title: &str) -> bool {
    IMPORTANT.contains(&title)
        || title.starts_with(upgrade::UPDATED_PREFIX)
        || title.starts_with(upgrade::DOWNGRADED_PREFIX)
}

/// Запись из недавнего уведомления, если оно было не дольше 5 минут назад.
pub fn pending_recording(stored: Option<&(String, Instant)>, now: Instant) -> Option<String> {
    stored
        .filter(|(_, at)| now.saturating_duration_since(*at) <= REMEMBER_RECORDING)
        .map(|(id, _)| id.clone())
}

/// Состояние трея, общее для опроса и обработчиков меню.
#[derive(Default)]
pub struct TrayState {
    level: Mutex<Level>,
    /// Запись из последнего показанного уведомления и когда оно было.
    last_recording: Mutex<Option<(String, Instant)>>,
    cancel: Mutex<CancelMark>,
    /// Пересобрать меню на следующем тике, даже если `MenuState` не сменился:
    /// галочку «Автозапись» Windows переключает сам по клику, и при неудачном
    /// запросе она врала бы до следующей смены состояния.
    menu_dirty: AtomicBool,
    /// Вопрос «Удалить текущую запись?» уже на экране — второй не открываем.
    cancel_asking: AtomicBool,
}

/// Показать уведомления с учётом `ui.notifications` (последнего прочитанного).
pub fn notify(app: &AppHandle, notices: Vec<Notice>) {
    let level = app
        .try_state::<TrayState>()
        .map(|state| *lock(&state.level))
        .unwrap_or_default();
    notify_with_level(app, notices, level);
}

/// То же с явным уровнем: при старте (`upgrade`) резидент ещё не ответил, и
/// уровень трей пока не знает — его читают из `config.json`.
pub fn notify_with_level(app: &AppHandle, notices: Vec<Notice>, level: Level) {
    for notice in filter(notices, level) {
        if let Some(id) = &notice.recording {
            remember(app, id);
        }
        let shown = app
            .notification()
            .builder()
            .title(&notice.title)
            .body(&notice.body)
            .show();
        if let Err(error) = shown {
            shell_log!("уведомление не показалось: {error}");
        }
    }
}

/// «Клик по уведомлению открывает запись».
///
/// IMPORTANT: tauri-plugin-notification на Windows не доставляет приложению
/// событие клика по тосту. Поэтому запоминаем запись из последнего уведомления,
/// и следующее открытие окна из трея в течение 5 минут открывает её
/// (`open_main(app, Some(id))`); открытие забирает её — второй раз окно
/// откроется как обычно.
fn remember(app: &AppHandle, id: &str) {
    if let Some(state) = app.try_state::<TrayState>() {
        *lock(&state.last_recording) = Some((id.to_string(), Instant::now()));
    }
}

fn open_window(app: &AppHandle) {
    let recording = app.try_state::<TrayState>().and_then(|state| {
        let mut slot = lock(&state.last_recording);
        let id = pending_recording(slot.as_ref(), Instant::now());
        *slot = None;
        id
    });
    windows::open_main(app, recording, None);
}

/// Иконка в трее и поток опроса. Вызывать из `setup` после `Supervisor::start`.
pub fn build(app: &tauri::App) -> tauri::Result<()> {
    app.manage(TrayState::default());
    let handle = app.handle().clone();
    let initial = menu_state(None, &ResidentStatus::Starting);
    let menu = build_menu(&handle, &initial)?;
    let icon_size = tray_size();
    TrayIconBuilder::with_id(TRAY_ID)
        .icon(icon_for(None, &ResidentStatus::Starting).image(icon_size)?)
        // Строка меню macOS: шаблонная картинка, цвет подставляет система.
        .icon_as_template(cfg!(target_os = "macos"))
        .tooltip(tooltip(None, &ResidentStatus::Starting))
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| on_menu(app, event.id().as_ref()))
        .on_tray_icon_event(|tray, event| match event {
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            }
            | TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } => open_window(tray.app_handle()),
            _ => {}
        })
        .build(app)?;
    thread::Builder::new()
        .name("meet-tray-poll".into())
        .spawn(move || poll_loop(&handle, initial, icon_size))?;
    Ok(())
}

fn build_menu(app: &AppHandle, state: &MenuState) -> tauri::Result<Menu<Wry>> {
    let menu = Menu::new(app)?;
    for entry in menu_model(state) {
        match entry {
            Entry::Item { id, text, enabled } => {
                menu.append(&MenuItem::with_id(app, id, text, enabled, None::<&str>)?)?
            }
            Entry::Check {
                id,
                text,
                enabled,
                checked,
            } => menu.append(&CheckMenuItem::with_id(
                app,
                id,
                text,
                enabled,
                checked,
                None::<&str>,
            )?)?,
            Entry::Separator => menu.append(&PredefinedMenuItem::separator(app)?)?,
        }
    }
    Ok(menu)
}

/// «Отменить запись…»: системный вопрос, кнопка по умолчанию — «Продолжить
/// запись» (Enter запись не удаляет). Отмена уходит резиденту, только если
/// нажато «Удалить» (`cancel_confirmed_by`).
///
/// rfd напрямую, а не плагин диалогов: плагин выдаёт Esc и закрытие окна за
/// нажатие второй кнопки, а вторая здесь — «Удалить». Кнопка по умолчанию — первая
/// (и на Windows, и на macOS), поэтому «Продолжить запись» идёт первой.
/// Вызывается из обработчика меню — главного потока, как требует macOS.
fn confirm_cancel(app: &AppHandle) {
    let Some(state) = app.try_state::<TrayState>() else {
        return;
    };
    if state.cancel_asking.swap(true, Ordering::SeqCst) {
        return; // вопрос уже открыт
    }
    // Какая запись идёт сейчас — параллельно с показом вопроса (запрос к
    // резиденту локальный и быстрый, а показ должен быть в главном потоке).
    let asked = thread::spawn(recording_folder_now);
    let dialog = rfd::AsyncMessageDialog::new()
        .set_level(rfd::MessageLevel::Warning)
        .set_title(CANCEL_TITLE)
        .set_description(CANCEL_QUESTION)
        .set_buttons(rfd::MessageButtons::OkCancelCustom(
            CANCEL_KEEP.to_string(),
            CANCEL_CONFIRM.to_string(),
        ))
        .show();
    let app = app.clone();
    thread::spawn(move || {
        let answer = tauri::async_runtime::block_on(dialog);
        let asked = asked.join().ok().flatten();
        if let Some(state) = app.try_state::<TrayState>() {
            state.cancel_asking.store(false, Ordering::SeqCst);
        }
        if !cancel_confirmed_by(&answer) {
            return;
        }
        if !cancel_still_meant(asked.as_deref(), recording_folder_now().as_deref()) {
            shell_log!("отмена записи не отправлена: запись сменилась, пока был открыт вопрос");
            notify(&app, vec![Notice::new(CANCEL_FAILED, CANCEL_STALE, None)]);
            return;
        }
        if let Some(state) = app.try_state::<TrayState>() {
            let mut mark = lock(&state.cancel);
            *mark = mark.requested();
        }
        command(&app, Action::Cancel);
    });
}

/// Папка идущей обычной записи по словам резидента (`/state.folder`); нет
/// записи или ответа — None.
fn recording_folder_now() -> Option<String> {
    let endpoint = resident::read_endpoint()?;
    let state = Client::new(&endpoint).get_state().ok()?;
    if str_at(&state, "status") != Some("recording") {
        return None;
    }
    str_at(&state, "folder").map(str::to_string)
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "open" => open_window(app),
        "start" => command(app, Action::Start),
        "stop" => command(app, Action::Stop),
        "live-start" => command(app, Action::LiveStart),
        "live-stop" => command(app, Action::LiveStop),
        "cancel" => confirm_cancel(app),
        "auto-on" | "auto-off" => {
            if let Some(state) = app.try_state::<TrayState>() {
                state.menu_dirty.store(true, Ordering::SeqCst);
            }
            command(app, Action::AutoRecord(id == "auto-on"));
        }
        "import" => import(app),
        "log" => open_log(app),
        "restart" => app.state::<Supervisor>().restart(app),
        "quit" => confirm_quit(app),
        _ => {}
    }
}

/// Команда резиденту в отдельном потоке: обработчик меню — главный поток.
/// Удача видна на следующем тике опроса; неудача — уведомлением, иначе клик
/// по меню просто ничего бы не сделал.
fn command(app: &AppHandle, action: Action) {
    let app = app.clone();
    thread::spawn(move || {
        let reply = resident::read_endpoint()
            .map(|endpoint| Client::new(&endpoint).post(action.path(), action.body()));
        if action == Action::Cancel {
            if let Some(state) = app.try_state::<TrayState>() {
                let mut mark = lock(&state.cancel);
                *mark = mark.replied(cancel_confirmed(reply.as_ref()));
            }
        }
        if let Some(notice) = action_notice(action, reply.as_ref()) {
            shell_log!("{}: {}", action.path(), notice.body);
            notify(&app, vec![notice]);
        }
        if needs_provider(action, reply.as_ref()) {
            let handle = app.clone();
            let _ = app.run_on_main_thread(move || {
                windows::open_main(&handle, None, Some(ASSISTANT_SECTION))
            });
        }
    });
}

fn import(app: &AppHandle) {
    let handle = app.clone();
    app.dialog()
        .file()
        .add_filter("Аудио и видео", windows::MEDIA_EXTS)
        .pick_file(move |chosen| {
            let Some(chosen) = chosen else {
                return; // отказ
            };
            thread::spawn(move || {
                let result = chosen
                    .into_path()
                    .map_err(|error| error.to_string())
                    .and_then(|path| import_file(&path));
                if let Err(message) = result {
                    notify(&handle, vec![Notice::new(IMPORT_FAILED, message, None)]);
                }
            });
        });
}

/// `POST /recordings/import`; `Err` — текст для уведомления. Удачный импорт
/// отдельного уведомления не даёт: о нём скажет «Расшифровка готова».
fn import_file(path: &Path) -> Result<(), String> {
    let endpoint = resident::read_endpoint().ok_or("служба записи не запущена")?;
    let body = json!({ "path": path.to_string_lossy() });
    match Client::new(&endpoint).post("/recordings/import", body) {
        Ok(reply) => match str_at(&reply, "error") {
            Some(error) => Err(error.to_string()),
            None => Ok(()),
        },
        Err(api::Error::Status { code, message }) => {
            shell_log!("импорт отклонён резидентом ({code})");
            Err(message)
        }
        Err(other) => Err(other.to_string()),
    }
}

fn open_log(app: &AppHandle) {
    let ResidentStatus::Failed { log } = app.state::<Supervisor>().status() else {
        return;
    };
    // Папка, а не файл: рядом лежат shell.log и архивы `.1`, а причина
    // бывает в любом из них.
    let target = log_folder(&log, &resident::data_dir());
    // explorer возвращает ненулевой код и при успехе — ждём только запуска.
    if let Err(error) = crate::platform::open_folder(&target) {
        shell_log!("журнал не открылся: {error}");
    }
}

/// Что открывает «Открыть журнал»: папку журнала резидента (`logs`), а если
/// её нет (резидент не дожил до первой строки) — `data_dir`.
pub(crate) fn log_folder(log: &Path, data_dir: &Path) -> PathBuf {
    log.parent()
        .filter(|dir| dir.is_dir())
        .map(Path::to_path_buf)
        .unwrap_or_else(|| data_dir.to_path_buf())
}

static QUIT_ASKING: AtomicBool = AtomicBool::new(false);

/// «Выход» из меню трея: идёт запись или расшифровка — сначала вопрос
/// (тот же rfd, что у отмены записи: Esc и крестик — «Не выходить»).
/// Состояние резидента — в отдельном потоке, вопрос — в главном.
fn confirm_quit(app: &AppHandle) {
    if QUIT_ASKING.swap(true, Ordering::SeqCst) {
        return; // вопрос уже открыт
    }
    let app = app.clone();
    thread::spawn(move || {
        let (state, jobs) = match resident::read_endpoint() {
            Some(endpoint) => {
                let client = Client::new(&endpoint);
                let state = client.get_state().ok();
                let jobs = state.as_ref().and_then(|_| client.get_jobs().ok());
                (state, jobs)
            }
            None => (None, None),
        };
        let dirty = SETTINGS_DIRTY.load(Ordering::SeqCst);
        let Some(question) = quit_question(state.as_ref(), jobs.as_ref(), dirty) else {
            QUIT_ASKING.store(false, Ordering::SeqCst);
            quit(&app);
            return;
        };
        let handle = app.clone();
        let shown = app.run_on_main_thread(move || {
            let dialog = rfd::AsyncMessageDialog::new()
                .set_level(rfd::MessageLevel::Warning)
                .set_title(QUIT_TITLE)
                .set_description(question)
                .set_buttons(rfd::MessageButtons::OkCancelCustom(
                    QUIT_KEEP.to_string(),
                    QUIT_CONFIRM.to_string(),
                ))
                .show();
            thread::spawn(move || {
                let answer = tauri::async_runtime::block_on(dialog);
                QUIT_ASKING.store(false, Ordering::SeqCst);
                if quit_confirmed_by(&answer) {
                    quit(&handle);
                }
            });
        });
        if shown.is_err() {
            QUIT_ASKING.store(false, Ordering::SeqCst);
        }
    });
}

/// «Выход»: резидент сохраняет идущую запись и гасится (до 70 с), и только
/// потом выходит оболочка. Ждём в отдельном потоке — главный поток держит
/// цикл событий и трей. Тот же путь — у `--quit` от установщика новой версии
/// и у «Скачать и установить» (`updater`).
pub fn quit(app: &AppHandle) {
    let app = app.clone();
    thread::spawn(move || {
        app.state::<Supervisor>().shutdown();
        let handle = app.clone();
        if app.run_on_main_thread(move || handle.exit(0)).is_err() {
            app.exit(0);
        }
    });
}

fn fetch_view(client: &Client) -> Option<View> {
    let state = client.get_state().ok()?;
    let jobs = client.get_jobs().ok()?;
    Some(View::from_json(&state, &jobs))
}

fn fetch_level(client: &Client) -> Option<Level> {
    client
        .get("/settings")
        .ok()
        .map(|settings| Level::from_settings(&settings))
}

/// Поток опроса. Сеттеры трея сами переходят на главный поток.
fn poll_loop(app: &AppHandle, initial_menu: MenuState, icon_size: u32) {
    let mut tracker = Tracker::default();
    let mut shown_icon = Some(icon_for(None, &ResidentStatus::Starting));
    let mut shown_tip: Option<String> = None;
    let mut shown_menu = Some(initial_menu);
    let mut level_read: Option<Instant> = None;
    // Окно ассистента открыто нами (по последнему полученному снимку). Без
    // связи не трогаем: обрыв — не конец ассистента.
    let mut live_shown = false;
    loop {
        let status = app.state::<Supervisor>().status();
        let state = app.state::<TrayState>();
        let client = resident::read_endpoint().map(|endpoint| Client::new(&endpoint));
        let polled = client.as_ref().and_then(fetch_view);
        let online = polled.is_some();
        let (view, notices) = tracker.observe(polled);

        if let Some(client) = client.as_ref().filter(|_| online) {
            let stale = match level_read {
                None => true,
                Some(at) => at.elapsed() >= LEVEL_REFRESH,
            };
            if !notices.is_empty() || stale {
                if let Some(level) = fetch_level(client) {
                    *lock(&state.level) = level;
                    level_read = Some(Instant::now());
                }
            }
        }
        if !notices.is_empty() {
            let notices = {
                let mut mark = lock(&state.cancel);
                let (next, kept) = mark.settle_for(view.as_ref(), notices);
                *mark = next;
                kept
            };
            notify(app, notices);
        }

        if let Some(view) = view.as_ref() {
            match live_window_change(live_shown, view.live.active) {
                Some(true) => {
                    let handle = app.clone();
                    let _ = app.run_on_main_thread(move || live_panel::open_live(&handle));
                }
                Some(false) => {
                    let handle = app.clone();
                    let _ = app.run_on_main_thread(move || live_panel::close_live(&handle));
                }
                None => {}
            }
            live_shown = view.live.active;
        }

        if let Some(tray) = app.tray_by_id(TRAY_ID) {
            let kind = icon_for(view.as_ref(), &status);
            if shown_icon != Some(kind) {
                match kind
                    .image(icon_size)
                    .and_then(|image| tray.set_icon(Some(image)))
                    .and_then(|()| {
                        if cfg!(target_os = "macos") {
                            tray.set_icon_as_template(true)
                        } else {
                            Ok(())
                        }
                    }) {
                    Ok(()) => shown_icon = Some(kind),
                    Err(error) => shell_log!("иконка трея не сменилась: {error}"),
                }
            }
            let text = tooltip(view.as_ref(), &status);
            if shown_tip.as_deref() != Some(text.as_str()) && tray.set_tooltip(Some(&text)).is_ok()
            {
                shown_tip = Some(text);
            }
            let wanted = menu_state(view.as_ref(), &status);
            let dirty = state.menu_dirty.swap(false, Ordering::SeqCst);
            if dirty || shown_menu.as_ref() != Some(&wanted) {
                match build_menu(app, &wanted).and_then(|menu| tray.set_menu(Some(menu))) {
                    Ok(()) => shown_menu = Some(wanted),
                    Err(error) => shell_log!("меню трея не пересобралось: {error}"),
                }
            }
        }

        // «Выход» — частый опрос: тултип и меню должны смениться сразу.
        let alive = matches!(
            status,
            ResidentStatus::Running | ResidentStatus::External | ResidentStatus::Quitting
        );
        thread::sleep(if alive { TICK } else { TICK_SLOW });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::PathBuf;
    use std::time::{Duration, Instant};

    fn idle() -> View {
        View {
            recording: false,
            auto: true,
            source: None,
            elapsed_s: 0.0,
            busy: false,
            jobs_done: vec![],
            jobs_failed: vec![],
            last_stop: None,
            reports_stops: true,
            live: Live::default(),
            kb_failed: None,
            devices_fallback: vec![],
        }
    }

    fn stopped(reason: &str, at: f64) -> LastStop {
        LastStop {
            folder: r"D:\rec\2026-09-30_16-04".into(),
            reason: reason.into(),
            at,
        }
    }

    fn notice(title: &str) -> Notice {
        Notice {
            title: title.into(),
            body: String::new(),
            recording: None,
        }
    }

    // --- переходы → уведомления ------------------------------------------

    #[test]
    fn first_snapshot_never_notifies() {
        let mut v = idle();
        v.recording = true;
        v.source = Some("auto".into());
        assert!(transitions(None, &v).is_empty());
    }

    #[test]
    fn auto_recording_start_notifies_with_marker() {
        let mut next = idle();
        next.recording = true;
        next.source = Some("auto".into());
        let n = transitions(Some(&idle()), &next);
        assert_eq!(n.len(), 1);
        assert_eq!(n[0].title, "Идёт запись (авто)");
        assert_eq!(n[0].body, "Автозапись по звонку");
    }

    #[test]
    fn manual_recording_start_has_no_marker() {
        let mut next = idle();
        next.recording = true;
        next.source = Some("manual".into());
        let n = transitions(Some(&idle()), &next);
        assert_eq!(n.len(), 1);
        assert_eq!(n[0].title, "Идёт запись");
    }

    #[test]
    fn stop_notifies_saved() {
        let mut prev = idle();
        prev.recording = true;
        let n = transitions(Some(&prev), &idle());
        assert_eq!(n[0].title, "Запись сохранена");
    }

    #[test]
    fn discarded_stop_is_not_reported_as_saved() {
        // Отмену из окна приложения трей иначе не видит: резидент говорит
        // причину сам.
        let mut prev = idle();
        prev.recording = true;
        let mut next = idle();
        next.last_stop = Some(stopped("discarded", 10.0));
        assert!(transitions(Some(&prev), &next).is_empty());
    }

    #[test]
    fn saved_and_short_stops_are_saves() {
        for reason in ["saved", "short"] {
            let mut prev = idle();
            prev.recording = true;
            let mut next = idle();
            next.last_stop = Some(stopped(reason, 10.0));
            assert_eq!(
                titles(&transitions(Some(&prev), &next)),
                vec!["Запись сохранена"],
                "{reason}"
            );
        }
    }

    #[test]
    fn stale_last_stop_does_not_decide_a_new_stop() {
        // Прошлая запись была отменена; эта остановка причины не оставила
        // (папка не получена) — старая причина к ней не относится.
        let mut prev = idle();
        prev.recording = true;
        prev.last_stop = Some(stopped("discarded", 10.0));
        let mut next = idle();
        next.last_stop = Some(stopped("discarded", 10.0));
        assert_eq!(
            titles(&transitions(Some(&prev), &next)),
            vec!["Запись сохранена"]
        );
    }

    #[test]
    fn old_resident_without_last_stop_still_says_saved() {
        let mut prev = idle();
        prev.recording = true;
        prev.reports_stops = false;
        let next = View {
            reports_stops: false,
            ..idle()
        };
        assert_eq!(
            titles(&transitions(Some(&prev), &next)),
            vec!["Запись сохранена"]
        );
    }

    #[test]
    fn ticking_recording_is_not_a_transition() {
        let mut prev = idle();
        prev.recording = true;
        prev.elapsed_s = 10.0;
        let mut next = prev.clone();
        next.elapsed_s = 11.0;
        next.busy = true;
        assert!(transitions(Some(&prev), &next).is_empty());
    }

    #[test]
    fn finished_job_notifies_once_with_recording() {
        let mut next = idle();
        next.jobs_done = vec!["2026-09-30_16-04".into()];
        let n = transitions(Some(&idle()), &next);
        assert_eq!(n[0].title, "Расшифровка готова");
        assert_eq!(n[0].recording.as_deref(), Some("2026-09-30_16-04"));
        assert!(transitions(Some(&next), &next).is_empty());
    }

    #[test]
    fn transcribing_the_same_recording_again_notifies_again() {
        let mut prev = idle();
        prev.jobs_done = vec!["a".into()];
        let mut next = idle();
        next.jobs_done = vec!["a".into(), "a".into()];
        let n = transitions(Some(&prev), &next);
        assert_eq!(n.len(), 1);
        assert_eq!(n[0].recording.as_deref(), Some("a"));
    }

    #[test]
    fn failed_job_notifies_with_short_error() {
        let long = "ё".repeat(300);
        let mut next = idle();
        next.jobs_failed = vec![("rec".into(), long)];
        let n = transitions(Some(&idle()), &next);
        assert_eq!(n.len(), 1);
        assert_eq!(n[0].title, "Ошибка расшифровки");
        assert_eq!(n[0].recording.as_deref(), Some("rec"));
        assert_eq!(n[0].body.chars().count(), 120);
        assert!(transitions(Some(&next), &next).is_empty());
    }

    // --- опрос: пропуски связи -------------------------------------------

    #[test]
    fn one_missed_poll_keeps_the_last_view() {
        let mut tracker = Tracker::default();
        let (shown, _) = tracker.observe(Some(idle()));
        assert_eq!(shown, Some(idle()));
        let (shown, notices) = tracker.observe(None);
        assert_eq!(shown, Some(idle()), "единичный промах не мигает иконкой");
        assert!(notices.is_empty());
        let (shown, _) = tracker.observe(None);
        assert_eq!(shown, None, "два промаха подряд — резидента нет");
    }

    #[test]
    fn crash_mid_recording_is_interrupted_not_saved() {
        let mut rec = idle();
        rec.recording = true;
        let mut tracker = Tracker::default();
        tracker.observe(Some(rec));
        for _ in 0..OFFLINE_AFTER_MISSES {
            tracker.observe(None);
        }
        let (_, notices) = tracker.observe(Some(idle()));
        assert_eq!(notices.len(), 1, "{notices:?}");
        assert_eq!(notices[0].title, "Запись прервана");
        assert_eq!(
            notices[0].body,
            "Служба записи перезапустилась во время записи — часть встречи могла не сохраниться"
        );
    }

    #[test]
    fn stop_during_a_single_blip_is_still_a_save() {
        let mut rec = idle();
        rec.recording = true;
        let mut tracker = Tracker::default();
        tracker.observe(Some(rec));
        tracker.observe(None);
        let (_, notices) = tracker.observe(Some(idle()));
        assert_eq!(titles(&notices), vec!["Запись сохранена"]);
    }

    #[test]
    fn reconnect_does_not_invent_a_stopped_recording() {
        let mut rec = idle();
        rec.recording = true;
        let mut tracker = Tracker::default();
        tracker.observe(Some(rec.clone()));
        for _ in 0..5 {
            let (_, notices) = tracker.observe(None);
            assert!(notices.is_empty());
        }
        let mut again = rec.clone();
        again.elapsed_s = 40.0;
        let (shown, notices) = tracker.observe(Some(again.clone()));
        assert!(notices.is_empty(), "{notices:?}");
        assert_eq!(shown, Some(again));
    }

    // --- разбор ответов резидента ----------------------------------------

    #[test]
    fn view_from_state_and_jobs() {
        let state = json!({
            "status": "recording", "source": "auto", "elapsed_s": 754.2,
            "folder": "D:\\rec\\2026-09-30_16-04",
            "auto_record": {"enabled": true},
        });
        let jobs = json!({"items": [
            {"kind": "transcribe", "folder": "D:\\rec\\2026-09-29_10-00", "state": "done", "error": null},
            {"kind": "import", "folder": "D:/rec/2026-09-29_12-00_import/", "state": "failed", "error": "ffmpeg упал"},
            {"kind": "transcribe", "folder": "D:\\rec\\x", "state": "failed", "error": null},
            {"kind": "transcribe", "folder": "D:\\rec\\y", "state": "cancelled", "error": null},
            {"kind": "download_model", "folder": "D:\\models", "state": "done", "error": null},
            {"kind": "transcribe", "folder": "D:\\rec\\z", "state": "running", "error": null},
        ]});
        let v = View::from_json(&state, &jobs);
        assert!(v.recording);
        assert!(v.auto);
        assert_eq!(v.source.as_deref(), Some("auto"));
        assert_eq!(v.elapsed_s, 754.2);
        assert!(v.busy);
        assert_eq!(v.jobs_done, vec!["2026-09-29_10-00".to_string()]);
        assert_eq!(
            v.jobs_failed,
            vec![
                (
                    "2026-09-29_12-00_import".to_string(),
                    "ffmpeg упал".to_string()
                ),
                ("x".to_string(), "неизвестная ошибка".to_string()),
            ]
        );
    }

    #[test]
    fn view_reads_last_stop() {
        let state = json!({"status": "idle", "last_stop": {
            "folder": r"D:\rec\2026-09-30_16-04", "reason": "discarded", "at": 10.0}});
        let v = View::from_json(&state, &json!({"items": []}));
        assert!(v.reports_stops);
        assert_eq!(v.last_stop, Some(stopped("discarded", 10.0)));
        // Ключ есть, остановок ещё не было.
        let v = View::from_json(&json!({"status": "idle", "last_stop": null}), &json!({}));
        assert!(v.reports_stops);
        assert_eq!(v.last_stop, None);
        // Старый резидент ключа не знает.
        let v = View::from_json(&json!({"status": "idle"}), &json!({}));
        assert!(!v.reports_stops);
        assert_eq!(v.last_stop, None);
    }

    #[test]
    fn view_from_idle_state_without_jobs() {
        let state = json!({"status": "idle", "source": null, "elapsed_s": 0.0,
                           "auto_record": {"enabled": false}});
        let v = View::from_json(&state, &json!({"items": []}));
        assert_eq!(
            v,
            View {
                auto: false,
                reports_stops: false,
                ..idle()
            }
        );
        // Мусор вместо ответа — не паника, а «ничего не идёт».
        let v = View::from_json(&json!(null), &json!("?"));
        assert!(!v.recording && !v.busy && v.jobs_done.is_empty());
    }

    // --- иконка, тултип, меню --------------------------------------------

    #[test]
    fn offline_has_its_own_icon() {
        let running = ResidentStatus::Running;
        assert_eq!(icon_for(None, &running), TrayIconKind::Offline);
        let mut rec = idle();
        rec.recording = true;
        assert_eq!(icon_for(Some(&rec), &running), TrayIconKind::Recording);
        let mut busy = idle();
        busy.busy = true;
        assert_eq!(icon_for(Some(&busy), &running), TrayIconKind::Busy);
        assert_eq!(icon_for(Some(&idle()), &running), TrayIconKind::Idle);
        let mut both = rec.clone();
        both.busy = true;
        assert_eq!(icon_for(Some(&both), &running), TrayIconKind::Recording);
    }

    #[test]
    fn no_resident_icon_depends_on_why() {
        // Ненадолго и не сбой — «занят» (дуга), а не янтарная точка.
        for status in [
            ResidentStatus::Starting,
            ResidentStatus::EngineUpdating { step: 2, of: 4 },
            ResidentStatus::Quitting,
        ] {
            assert_eq!(icon_for(None, &status), TrayIconKind::Busy, "{status:?}");
        }
        for status in [
            ResidentStatus::EngineMissing,
            ResidentStatus::Failed {
                log: PathBuf::from("shell.log"),
            },
            ResidentStatus::ExternalNoApi,
            ResidentStatus::Running,
            ResidentStatus::External,
        ] {
            assert_eq!(icon_for(None, &status), TrayIconKind::Offline, "{status:?}");
        }
        // Снимок есть — решает он, а не статус надзора.
        let mut rec = idle();
        rec.recording = true;
        assert_eq!(
            icon_for(Some(&rec), &ResidentStatus::Quitting),
            TrayIconKind::Recording
        );
    }

    #[cfg(windows)]
    #[test]
    fn tray_icons_are_png_of_every_size() {
        for kind in TrayIconKind::ALL {
            for size in TRAY_SIZES {
                let image = kind.image(size).expect("иконка трея не читается");
                assert_eq!((image.width(), image.height()), (size, size), "{kind:?}");
            }
            // Незнакомый размер — самый крупный.
            assert_eq!(kind.image(48).expect("иконка трея").width(), 32);
        }
        // Состояния различимы: у каждого своя картинка.
        for size in TRAY_SIZES {
            let mut seen: Vec<&[u8]> = TrayIconKind::ALL.iter().map(|k| k.png(size)).collect();
            seen.sort();
            seen.dedup();
            assert_eq!(seen.len(), TrayIconKind::ALL.len(), "{size}");
        }
    }

    #[test]
    fn menu_bar_templates_are_36px_png_and_distinct() {
        let mut seen: Vec<&[u8]> = Vec::new();
        for kind in TrayIconKind::ALL {
            let image = Image::from_bytes(kind.template_png()).expect("шаблон не читается");
            assert_eq!((image.width(), image.height()), (36, 36), "{kind:?}");
            seen.push(kind.template_png());
        }
        seen.sort();
        seen.dedup();
        assert_eq!(seen.len(), TrayIconKind::ALL.len());
    }

    #[test]
    fn tray_size_follows_the_windows_icon_metric() {
        assert_eq!(pick_tray_size(16), 16); // 100 %
        assert_eq!(pick_tray_size(20), 20); // 125 %
        assert_eq!(pick_tray_size(24), 24); // 150 %
        assert_eq!(pick_tray_size(28), 32); // 175 %
        assert_eq!(pick_tray_size(32), 32); // 200 %
        assert_eq!(pick_tray_size(40), 32); // 250 % — крупнее нет, растянет 32
        assert_eq!(pick_tray_size(12), 16);
        assert_eq!(pick_tray_size(0), 32); // метрики нет
    }

    #[test]
    fn tooltip_texts() {
        let running = ResidentStatus::Running;
        assert_eq!(tooltip(Some(&idle()), &running), "Meet · ожидание звонка");
        let mut rec = idle();
        rec.recording = true;
        rec.source = Some("auto".into());
        rec.elapsed_s = 754.9;
        assert_eq!(
            tooltip(Some(&rec), &running),
            "Meet · идёт запись 12:34 (авто)"
        );
        rec.source = Some("manual".into());
        rec.elapsed_s = 3723.0;
        assert_eq!(tooltip(Some(&rec), &running), "Meet · идёт запись 1:02:03");
        let mut busy = idle();
        busy.busy = true;
        assert_eq!(tooltip(Some(&busy), &running), "Meet · идёт расшифровка");
        assert_eq!(
            tooltip(Some(&idle()), &ResidentStatus::External),
            "Meet · служба записи запущена вне приложения"
        );
        assert_eq!(
            tooltip(None, &ResidentStatus::Starting),
            "Meet · служба записи не запущена"
        );
    }

    #[test]
    fn old_resident_without_api_is_explained_and_menu_is_off() {
        let status = ResidentStatus::ExternalNoApi;
        assert_eq!(
            tooltip(None, &status),
            "Meet · запущена старая версия службы записи (меню недоступно)"
        );
        let m = menu_state(None, &status);
        assert!(!m.online, "действиям с записью нужен API");
        assert_eq!(m.log, None);
    }

    #[test]
    fn menu_follows_view_and_resident() {
        let mut rec = idle();
        rec.recording = true;
        rec.elapsed_s = 5.0;
        let running = ResidentStatus::Running;
        let m = menu_state(Some(&rec), &running);
        assert_eq!(
            m,
            MenuState {
                online: true,
                recording: true,
                live: LivePhase::Off,
                auto: true,
                log: None,
                restart: false,
                quitting: false,
            }
        );
        // Идущие секунды записи не пересобирают меню.
        let mut later = rec.clone();
        later.elapsed_s = 6.0;
        assert_eq!(menu_state(Some(&later), &running), m);

        let log = PathBuf::from(r"C:\data\watch.log");
        let failed = ResidentStatus::Failed { log: log.clone() };
        let m = menu_state(None, &failed);
        assert!(!m.online);
        assert_eq!(m.log, Some(log));
        assert!(
            m.restart,
            "рядом с журналом — «Перезапустить службу записи»"
        );
    }

    #[test]
    fn missing_engine_points_to_the_window_without_log_or_restart() {
        let missing = ResidentStatus::EngineMissing;
        assert_eq!(
            tooltip(None, &missing),
            "Meet · движок не установлен, откройте окно"
        );
        let m = menu_state(None, &missing);
        assert!(!m.online);
        assert_eq!(m.log, None);
        assert!(!m.restart, "перезапускать нечего — движок ставит мастер");
    }

    #[test]
    fn engine_update_shows_its_step_and_failure_is_important() {
        let waiting = ResidentStatus::EngineUpdating { step: 0, of: 0 };
        assert_eq!(tooltip(None, &waiting), "Meet · обновление движка");
        let step = ResidentStatus::EngineUpdating { step: 3, of: 4 };
        assert_eq!(tooltip(None, &step), "Meet · обновление движка: шаг 3 из 4");
        let m = menu_state(None, &step);
        assert!(!m.online);
        assert_eq!(m.log, None);
        assert!(!m.restart);
        let failed = vec![notice(ENGINE_UPDATE_FAILED)];
        assert_eq!(filter(failed.clone(), Level::Important), failed);
    }

    #[test]
    fn quitting_disables_the_menu_and_says_so() {
        let mut rec = idle();
        rec.recording = true;
        let quitting = ResidentStatus::Quitting;
        // Резидент ещё отвечает (сохраняет запись), но меню уже не действует.
        let m = menu_state(Some(&rec), &quitting);
        assert!(m.quitting);
        assert!(!m.online);
        assert!(!m.restart);
        assert_eq!(
            tooltip(Some(&rec), &quitting),
            "Meet · сохранение записи и выход"
        );
        assert_eq!(tooltip(None, &quitting), "Meet · сохранение записи и выход");
    }

    #[test]
    fn open_log_shows_the_logs_folder() {
        let data = std::env::temp_dir().join(format!("meet-tray-log-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&data);
        let log = crate::logs::resident_log(&data);
        // Резидент не дожил до первой строки — папки logs нет.
        assert_eq!(log_folder(&log, &data), data);
        std::fs::create_dir_all(log.parent().unwrap()).unwrap();
        assert_eq!(log_folder(&log, &data), data.join("logs"));
        let _ = std::fs::remove_dir_all(&data);
    }

    // --- фильтр уведомлений ----------------------------------------------

    fn all_kinds() -> Vec<Notice> {
        [
            "Идёт запись",
            "Идёт запись (авто)",
            "Запись сохранена",
            "Расшифровка готова",
            "Ошибка расшифровки",
            "Служба записи не запускается",
            "Не удалось импортировать",
            "Не удалось начать запись",
            "Не удалось остановить запись",
            "Не удалось отменить запись",
            "Не удалось переключить автозапись",
            "Запись прервана",
            "Ассистент слушает встречу",
            "Ассистент остановлен — расшифровываю",
            "Ассистент остановлен",
            "Ассистент завершился с ошибкой",
            "Запуск ассистента отменён",
            "Не удалось запустить ассистента",
            "Не удалось остановить ассистента",
            "Не удалось выгрузить встречу в базу знаний",
        ]
        .into_iter()
        .map(notice)
        .collect()
    }

    fn titles(list: &[Notice]) -> Vec<&str> {
        list.iter().map(|n| n.title.as_str()).collect()
    }

    #[test]
    fn level_all_passes_everything() {
        assert_eq!(filter(all_kinds(), Level::All).len(), all_kinds().len());
    }

    #[test]
    fn level_important_passes_errors_and_auto_start() {
        let kept = filter(all_kinds(), Level::Important);
        assert_eq!(
            titles(&kept),
            vec![
                "Идёт запись (авто)",
                "Ошибка расшифровки",
                "Служба записи не запускается",
                "Не удалось импортировать",
                "Не удалось начать запись",
                "Не удалось остановить запись",
                "Не удалось отменить запись",
                "Не удалось переключить автозапись",
                "Запись прервана",
                "Ассистент остановлен",
                "Ассистент завершился с ошибкой",
                "Не удалось запустить ассистента",
                "Не удалось остановить ассистента",
                "Не удалось выгрузить встречу в базу знаний",
            ]
        );
    }

    #[test]
    fn kb_export_failure_is_reported_once_per_new_failure() {
        let state = json!({"status": "idle", "last_stop": null,
                           "kb_export_failed": {"folder": "2026-09-30_10-15",
                                                "error": "диск недоступен", "at": 5.0}});
        let failed = View::from_json(&state, &json!({"items": []}));
        assert_eq!(
            failed.kb_failed,
            Some(KbFailure {
                folder: "2026-09-30_10-15".into(),
                error: "диск недоступен".into(),
                at: 5.0,
            })
        );
        let notices = transitions(Some(&idle()), &failed);
        assert_eq!(
            notices,
            vec![Notice {
                title: KB_EXPORT_FAILED.into(),
                body: "диск недоступен".into(),
                recording: Some("2026-09-30_10-15".into()),
            }]
        );
        // Тот же сбой в следующем снимке — повторного уведомления нет.
        assert!(transitions(Some(&failed), &failed.clone()).is_empty());
        // Первый снимок — точка отсчёта: старый сбой не сообщается.
        assert!(transitions(None, &failed).is_empty());
        assert_eq!(filter(notices.clone(), Level::Important), notices);
    }

    #[test]
    fn level_off_passes_nothing() {
        assert!(filter(all_kinds(), Level::Off).is_empty());
    }

    #[test]
    fn level_from_settings() {
        assert_eq!(
            Level::from_settings(&json!({"ui": {"notifications": "important"}})),
            Level::Important
        );
        assert_eq!(
            Level::from_settings(&json!({"ui": {"notifications": "off"}})),
            Level::Off
        );
        assert_eq!(Level::from_settings(&json!({"ui": {}})), Level::All);
        assert_eq!(
            Level::from_settings(&json!({"ui": {"notifications": "громко"}})),
            Level::All
        );
    }

    // --- отмена из трея -------------------------------------------------

    #[test]
    fn confirmed_cancel_hides_the_next_save_once() {
        let mark = CancelMark::default().requested().replied(true);
        let (mark, kept) = mark.settle(vec![
            notice("Запись сохранена"),
            notice("Расшифровка готова"),
        ]);
        assert_eq!(titles(&kept), vec!["Расшифровка готова"]);
        let (_, later) = mark.settle(vec![notice("Запись сохранена")]);
        assert_eq!(titles(&later), vec!["Запись сохранена"]);
    }

    #[test]
    fn failed_cancel_does_not_hide_a_later_save() {
        let mark = CancelMark::default().requested().replied(false);
        let (_, kept) = mark.settle(vec![notice("Запись сохранена")]);
        assert_eq!(titles(&kept), vec!["Запись сохранена"]);
    }

    #[test]
    fn stop_seen_before_the_cancel_reply_is_still_the_cancel() {
        // Резидент снимает флаг записи до ответа: опрос может увидеть idle
        // раньше, чем придёт ответ на /recording/cancel.
        let mark = CancelMark::default().requested();
        let (mark, kept) = mark.settle(vec![notice("Запись сохранена")]);
        assert!(kept.is_empty());
        let mark = mark.replied(true);
        let (_, later) = mark.settle(vec![notice("Запись сохранена")]);
        assert_eq!(titles(&later), vec!["Запись сохранена"]);
    }

    #[test]
    fn interrupted_recording_clears_a_pending_cancel() {
        let mark = CancelMark::default().requested().replied(true);
        let (mark, kept) = mark.settle(vec![notice("Запись прервана")]);
        assert_eq!(titles(&kept), vec!["Запись прервана"]);
        let (_, later) = mark.settle(vec![notice("Запись сохранена")]);
        assert_eq!(titles(&later), vec!["Запись сохранена"]);
    }

    #[test]
    fn resident_that_reports_stops_makes_the_cancel_mark_moot() {
        let mut view = idle();
        let mark = CancelMark::default().requested().replied(true);
        let (mark, kept) = mark.settle_for(Some(&view), vec![notice("Запись сохранена")]);
        assert_eq!(mark, CancelMark::None);
        assert_eq!(titles(&kept), vec!["Запись сохранена"]);
        // Старый резидент (без last_stop) — отметка работает как раньше.
        view.reports_stops = false;
        let mark = CancelMark::default().requested().replied(true);
        let (_, kept) = mark.settle_for(Some(&view), vec![notice("Запись сохранена")]);
        assert!(kept.is_empty());
    }

    #[test]
    fn cancel_is_confirmed_only_by_its_action() {
        let ok: api::Result<Value> = Ok(json!({"ok": true, "action": "cancelled"}));
        assert!(cancel_confirmed(Some(&ok)));
        let refused: api::Result<Value> = Ok(json!({"ok": false, "action": "not-recording"}));
        assert!(!cancel_confirmed(Some(&refused)));
        let broken: api::Result<Value> = Err(api::Error::Transport("x".into()));
        assert!(!cancel_confirmed(Some(&broken)));
        assert!(!cancel_confirmed(None));
    }

    // --- неудачные команды из меню ---------------------------------------

    fn body_of(action: Action, reply: Option<&api::Result<Value>>) -> (String, String) {
        let notice = action_notice(action, reply).expect("ожидали уведомление");
        (notice.title, notice.body)
    }

    #[test]
    fn action_without_resident_says_so() {
        assert_eq!(
            body_of(Action::Start, None),
            (
                "Не удалось начать запись".into(),
                "Служба записи не запущена".into()
            )
        );
        let broken: api::Result<Value> = Err(api::Error::Transport("connection refused".into()));
        assert_eq!(
            body_of(Action::Stop, Some(&broken)),
            (
                "Не удалось остановить запись".into(),
                "Служба записи не отвечает".into()
            )
        );
    }

    #[test]
    fn action_http_error_carries_resident_text() {
        let error: api::Result<Value> = Err(api::Error::Status {
            code: 500,
            message: "OSError: диск".into(),
        });
        assert_eq!(
            body_of(Action::Cancel, Some(&error)),
            ("Не удалось отменить запись".into(), "OSError: диск".into())
        );
        let bad: api::Result<Value> = Err(api::Error::Status {
            code: 400,
            message: "ожидается enabled: true/false".into(),
        });
        assert_eq!(
            body_of(Action::AutoRecord(true), Some(&bad)),
            (
                "Не удалось переключить автозапись".into(),
                "ожидается enabled: true/false".into()
            )
        );
    }

    #[test]
    fn action_refusals_are_explained() {
        let already: api::Result<Value> = Ok(json!({"ok": false, "action": "already-recording"}));
        assert_eq!(body_of(Action::Start, Some(&already)).1, "Запись уже идёт");
        let idle: api::Result<Value> = Ok(json!({"ok": false, "action": "not-recording"}));
        assert_eq!(body_of(Action::Stop, Some(&idle)).1, "Запись не идёт");
        assert_eq!(body_of(Action::Cancel, Some(&idle)).1, "Запись не идёт");
        let odd: api::Result<Value> = Ok(json!({"ok": false, "action": "что-то новое"}));
        assert_eq!(
            body_of(Action::Start, Some(&odd)).1,
            "Служба записи отклонила команду"
        );
    }

    #[test]
    fn successful_actions_stay_quiet() {
        for (action, reply) in [
            (Action::Start, json!({"ok": true, "action": "started"})),
            (Action::Start, json!({"ok": true, "action": "adopted"})),
            (Action::Stop, json!({"ok": true, "action": "stopped"})),
            (Action::Cancel, json!({"ok": true, "action": "cancelled"})),
            // /auto-record отвечает снимком состояния, без ok.
            (Action::AutoRecord(false), json!({"status": "idle"})),
        ] {
            let reply: api::Result<Value> = Ok(reply);
            assert_eq!(action_notice(action, Some(&reply)), None, "{action:?}");
        }
    }

    // --- запись с ассистентом -------------------------------------------

    const LIVE_FOLDER: &str = r"D:\rec\2026-10-01_10-00";

    fn with_live(active: bool, starting: bool, stopping: bool) -> View {
        View {
            live: Live {
                active,
                starting,
                stopping,
                folder: active.then(|| LIVE_FOLDER.to_string()),
                error: None,
            },
            ..idle()
        }
    }

    fn view_after_live(error: Option<&str>) -> View {
        let mut view = idle();
        view.live.error = error.map(str::to_string);
        view
    }

    #[test]
    fn busy_means_transcription_not_summary_or_question() {
        // Итоги и вопросы (вторая очередь) — не «расшифровываю».
        let jobs = json!({"items": [
            {"kind": "summary", "folder": "D:\\rec\\a", "state": "running", "error": null},
            {"kind": "ask", "folder": "D:\\rec\\b", "state": "running", "error": null},
        ]});
        let v = View::from_json(&json!({"status": "idle"}), &jobs);
        assert!(!v.busy);
        let jobs = json!({"items": [
            {"kind": "import", "folder": "D:\\rec\\c", "state": "running", "error": null},
        ]});
        assert!(View::from_json(&json!({"status": "idle"}), &jobs).busy);
    }

    #[test]
    fn view_reads_live() {
        let state = json!({"status": "idle", "live": {
            "active": true, "starting": false, "stopping": true,
            "folder": LIVE_FOLDER, "error": null, "started_at": 1759300000.0}});
        let v = View::from_json(&state, &json!({"items": []}));
        assert!(!v.recording, "живой режим — не обычная запись");
        assert_eq!(
            v.live,
            Live {
                active: true,
                starting: false,
                stopping: true,
                folder: Some(LIVE_FOLDER.into()),
                error: None,
            }
        );
        let v = View::from_json(
            &json!({"live": {"active": false, "error": "  упал  "}}),
            &json!({}),
        );
        assert_eq!(v.live.error.as_deref(), Some("упал"));
        // Резидент без живого режима — «ассистента нет».
        let v = View::from_json(&json!({"status": "idle"}), &json!({}));
        assert_eq!(v.live, Live::default());
        let v = View::from_json(&json!({"live": {"error": " "}}), &json!({}));
        assert_eq!(v.live.error, None);
    }

    #[test]
    fn live_start_says_listening() {
        let n = transitions(
            Some(&with_live(false, true, false)),
            &with_live(true, false, false),
        );
        assert_eq!(titles(&n), vec!["Ассистент слушает встречу"]);
        // Тик за тиком — тишина.
        let active = with_live(true, false, false);
        assert!(transitions(Some(&active), &active).is_empty());
    }

    #[test]
    fn first_snapshot_with_live_never_notifies() {
        assert!(transitions(None, &with_live(true, false, false)).is_empty());
    }

    #[test]
    fn live_clean_stop_says_recording_saved() {
        let n = transitions(Some(&with_live(true, false, true)), &view_after_live(None));
        assert_eq!(titles(&n), vec!["Ассистент остановлен — запись сохранена"]);
        assert_eq!(n[0].recording.as_deref(), Some("2026-10-01_10-00"));
        // Ассистент вышел сам, без ошибки — это тоже штатная остановка.
        let n = transitions(Some(&with_live(true, false, false)), &view_after_live(None));
        assert_eq!(titles(&n), vec!["Ассистент остановлен — запись сохранена"]);
    }

    #[test]
    fn live_stop_with_error_carries_the_error() {
        let n = transitions(
            Some(&with_live(true, false, true)),
            &view_after_live(Some("Ассистент не дописал запись за отведённое время")),
        );
        assert_eq!(titles(&n), vec!["Ассистент остановлен"]);
        assert_eq!(n[0].body, "Ассистент не дописал запись за отведённое время");
        assert_eq!(n[0].recording.as_deref(), Some("2026-10-01_10-00"));
    }

    #[test]
    fn live_error_without_stop_is_a_crash() {
        let n = transitions(
            Some(&with_live(true, false, false)),
            &view_after_live(Some("CUDA out of memory")),
        );
        assert_eq!(titles(&n), vec!["Ассистент завершился с ошибкой"]);
        assert_eq!(n[0].body, "CUDA out of memory");
        // Не поднялся вовсе.
        let n = transitions(
            Some(&with_live(false, true, false)),
            &view_after_live(Some("Ассистент не запустился за 120 с")),
        );
        assert_eq!(titles(&n), vec!["Ассистент завершился с ошибкой"]);
        assert_eq!(n[0].recording, None);
        let long = "ё".repeat(300);
        let n = transitions(
            Some(&with_live(true, false, false)),
            &view_after_live(Some(&long)),
        );
        assert_eq!(n[0].body.chars().count(), 120);
    }

    #[test]
    fn live_stopped_before_start_is_a_cancel() {
        let n = transitions(Some(&with_live(false, true, true)), &view_after_live(None));
        assert_eq!(titles(&n), vec!["Запуск ассистента отменён"]);
    }

    #[test]
    fn stale_live_error_is_not_repeated() {
        let failed = view_after_live(Some("упал"));
        assert!(transitions(Some(&failed), &failed).is_empty());
    }

    #[test]
    fn resident_restart_mid_live_is_interrupted() {
        let mut tracker = Tracker::default();
        tracker.observe(Some(with_live(true, false, false)));
        for _ in 0..OFFLINE_AFTER_MISSES {
            tracker.observe(None);
        }
        let (_, notices) = tracker.observe(Some(idle()));
        assert_eq!(titles(&notices), vec!["Запись прервана"]);
    }

    #[test]
    fn device_fallback_is_parsed_from_state() {
        let state = json!({"status": "recording", "devices_fallback": [
            {"kind": "mic", "name": "USB-микрофон", "device": "Микрофон"},
            {"kind": "камера", "name": "мусор"},
            "мусор",
            {"kind": "output", "name": "Наушники", "device": null},
        ]});
        let v = View::from_json(&state, &json!({"items": []}));
        assert_eq!(
            v.devices_fallback,
            vec![
                DeviceFallback {
                    mic: true,
                    name: "USB-микрофон".into()
                },
                DeviceFallback {
                    mic: false,
                    name: "Наушники".into()
                },
            ]
        );
        let old = View::from_json(&json!({"status": "idle"}), &json!({"items": []}));
        assert!(old.devices_fallback.is_empty());
    }

    #[test]
    fn tooltip_warns_about_device_fallback_while_recording() {
        let running = ResidentStatus::Running;
        let mut rec = idle();
        rec.recording = true;
        rec.elapsed_s = 754.0;
        rec.devices_fallback = vec![DeviceFallback {
            mic: true,
            name: "USB-микрофон".into(),
        }];
        assert_eq!(
            tooltip(Some(&rec), &running),
            "Meet · идёт запись 12:34\nМикрофон «USB-микрофон» не найден — запись с системного"
        );
        let mut live = with_live(true, false, false);
        live.devices_fallback = vec![DeviceFallback {
            mic: false,
            name: "Наушники".into(),
        }];
        assert_eq!(
            tooltip(Some(&live), &running),
            "Meet · ассистент слушает встречу\nУстройство вывода «Наушники» не найдено — запись с системного"
        );
        // вне записи подмена не показывается
        let mut idle_view = idle();
        idle_view.devices_fallback = rec.devices_fallback.clone();
        assert_eq!(
            tooltip(Some(&idle_view), &running),
            "Meet · ожидание звонка"
        );
    }

    #[test]
    fn tooltip_fits_the_windows_limit() {
        let running = ResidentStatus::Running;
        let mut rec = idle();
        rec.recording = true;
        let long = "Onboard MIC (Технология Intel Smart Sound для цифровых микрофонов)";
        rec.devices_fallback = vec![
            DeviceFallback {
                mic: true,
                name: long.into(),
            },
            DeviceFallback {
                mic: false,
                name: long.into(),
            },
        ];
        let tip = tooltip(Some(&rec), &running);
        // szTip — 128 UTF-16 с завершающим нулём
        assert!(tip.encode_utf16().count() <= 127, "{tip}");
        assert!(tip.contains("Микрофон «Onboard MIC"), "{tip}");
        assert!(tip.ends_with('…'), "{tip}");
    }

    #[test]
    fn live_icon_and_tooltip() {
        let running = ResidentStatus::Running;
        let active = with_live(true, false, false);
        assert_eq!(icon_for(Some(&active), &running), TrayIconKind::Live);
        assert_eq!(
            tooltip(Some(&active), &running),
            "Meet · ассистент слушает встречу"
        );
        let stopping = with_live(true, false, true);
        assert_eq!(icon_for(Some(&stopping), &running), TrayIconKind::Live);
        assert_eq!(
            tooltip(Some(&stopping), &running),
            "Meet · ассистент завершает запись"
        );
        let starting = with_live(false, true, false);
        assert_eq!(icon_for(Some(&starting), &running), TrayIconKind::Busy);
        assert_eq!(
            tooltip(Some(&starting), &running),
            "Meet · ассистент запускается"
        );
    }

    #[test]
    fn live_phase_follows_the_snapshot() {
        let running = ResidentStatus::Running;
        let phase = |view: &View| menu_state(Some(view), &running).live;
        assert_eq!(phase(&idle()), LivePhase::Off);
        assert_eq!(phase(&with_live(false, true, false)), LivePhase::Starting);
        assert_eq!(phase(&with_live(true, false, false)), LivePhase::Active);
        assert_eq!(phase(&with_live(true, false, true)), LivePhase::Stopping);
        // Остановлен ещё до старта — тоже «останавливается».
        assert_eq!(phase(&with_live(false, true, true)), LivePhase::Stopping);
        assert_eq!(menu_state(None, &running).live, LivePhase::Off);
    }

    fn items(recording: bool, live: LivePhase) -> Vec<(&'static str, &'static str, bool)> {
        record_items(&MenuState {
            online: true,
            recording,
            live,
            auto: false,
            log: None,
            restart: false,
            quitting: false,
        })
    }

    #[test]
    fn record_items_offer_both_starts_when_idle() {
        assert_eq!(
            items(false, LivePhase::Off),
            vec![
                ("start", "Начать запись", true),
                ("live-start", "Начать запись с ассистентом", true),
            ]
        );
        assert_eq!(
            items(true, LivePhase::Off),
            vec![("stop", "Остановить и сохранить", true)]
        );
    }

    #[test]
    fn record_items_during_live_stop_the_assistant() {
        assert_eq!(
            items(false, LivePhase::Starting),
            vec![
                ("live-starting", "Ассистент запускается…", false),
                ("live-stop", "Остановить и сохранить", true),
            ]
        );
        assert_eq!(
            items(false, LivePhase::Active),
            vec![("live-stop", "Остановить и сохранить", true)]
        );
        assert_eq!(
            items(false, LivePhase::Stopping),
            vec![("live-stop", "Остановить и сохранить", false)]
        );
    }

    /// Меню строками: «—» — разделитель, «[x]»/«[ ]» — галочка, «(-)» —
    /// недоступный пункт.
    fn layout(state: &MenuState) -> Vec<String> {
        menu_model(state)
            .into_iter()
            .map(|entry| match entry {
                Entry::Item { text, enabled, .. } => {
                    format!("{text}{}", if enabled { "" } else { " (-)" })
                }
                Entry::Check { text, checked, .. } => {
                    format!("[{}] {text}", if checked { "x" } else { " " })
                }
                Entry::Separator => "—".to_string(),
            })
            .collect()
    }

    fn menu_of(recording: bool) -> MenuState {
        MenuState {
            online: true,
            recording,
            live: LivePhase::Off,
            auto: true,
            log: None,
            restart: false,
            quitting: false,
        }
    }

    #[test]
    fn cancel_is_separated_from_stop_and_sits_above_quit() {
        assert_eq!(
            layout(&menu_of(true)),
            [
                "Открыть Meet",
                "Остановить и сохранить",
                "Импортировать файл…",
                "[x] Автозапись",
                "—",
                "Отменить запись…",
                "Выход",
            ]
        );
        let ids: Vec<&str> = menu_model(&menu_of(true))
            .iter()
            .filter_map(|entry| match entry {
                Entry::Item { id, .. } | Entry::Check { id, .. } => Some(*id),
                Entry::Separator => None,
            })
            .collect();
        assert_eq!(
            ids,
            ["open", "stop", "import", "auto-off", "cancel", "quit"]
        );
    }

    #[test]
    fn idle_and_live_menus_have_no_cancel() {
        assert_eq!(
            layout(&menu_of(false)),
            [
                "Открыть Meet",
                "Начать запись",
                "Начать запись с ассистентом",
                "Импортировать файл…",
                "[x] Автозапись",
                "—",
                "Выход",
            ]
        );
        let mut live = menu_of(false);
        live.live = LivePhase::Active;
        assert!(!layout(&live)
            .iter()
            .any(|line| line.starts_with("Отменить")));
    }

    #[test]
    fn service_items_stay_between_separator_and_quit() {
        let mut failed = menu_of(false);
        failed.online = false;
        failed.auto = false;
        failed.log = Some(PathBuf::from(r"C:\data\watch.log"));
        failed.restart = true;
        assert_eq!(
            layout(&failed),
            [
                "Открыть Meet",
                "Начать запись (-)",
                "Начать запись с ассистентом (-)",
                "Импортировать файл… (-)",
                "[ ] Автозапись",
                "—",
                "Открыть журнал",
                "Перезапустить службу записи",
                "Выход",
            ]
        );
        let mut quitting = menu_of(true);
        quitting.quitting = true;
        assert!(menu_model(&quitting).iter().all(|entry| match entry {
            Entry::Item { enabled, .. } | Entry::Check { enabled, .. } => !enabled,
            Entry::Separator => true,
        }));
    }

    #[test]
    fn cancel_goes_only_to_the_recording_it_asked_about() {
        let a = Some(r"D:\rec\2026-10-02_10-00");
        let b = Some(r"D:\rec\2026-10-02_10-40");
        assert!(cancel_still_meant(a, a));
        assert!(!cancel_still_meant(a, b)); // автозапись начала новую
        assert!(!cancel_still_meant(a, None)); // запись уже закончилась
        assert!(!cancel_still_meant(None, a)); // резидент не ответил при открытии
    }

    #[test]
    fn quit_asks_only_while_work_is_running() {
        use rfd::MessageDialogResult as Answer;
        use serde_json::json;
        let idle = json!({ "status": "idle" });
        let recording = json!({ "status": "recording" });
        let transcribing = json!({ "items": [{ "state": "running", "kind": "transcribe" }] });
        let download = json!({ "items": [{ "state": "queued", "kind": "analyze" },
                                         { "state": "running", "kind": "download-model" },
                                         { "state": "queued", "kind": "analyze" }] });
        let none = json!({ "items": [{ "state": "done", "kind": "transcribe" }] });
        assert_eq!(quit_question(None, None, false), None); // резидент молчит — выходим
        assert_eq!(quit_question(Some(&idle), Some(&none), false), None);
        assert_eq!(
            quit_question(Some(&recording), None, false).as_deref(),
            Some("Идёт запись — при выходе она остановится и сохранится.\nВыйти?")
        );
        let text = quit_question(Some(&idle), Some(&transcribing), false).unwrap();
        assert!(text.starts_with(QUIT_TRANSCRIBE));
        // Не расшифровка — так и сказано, что именно прервётся.
        let text = quit_question(Some(&idle), Some(&download), false).unwrap();
        // Каждое занятие — один раз, даже если в очереди не подряд.
        assert!(text.contains("(анализ встречи, загрузка модели)"), "{text}");
        assert!(!text.contains("расшифровка —"));
        // Несохранённые настройки — тоже вопрос, даже если резидент молчит.
        assert_eq!(
            quit_question(None, None, true).as_deref(),
            Some("В настройках есть несохранённые изменения — при выходе они пропадут.\nВыйти?")
        );
        assert!(quit_confirmed_by(&Answer::Custom("Выйти".into())));
        assert!(!quit_confirmed_by(&Answer::Custom("Не выходить".into())));
        assert!(!quit_confirmed_by(&Answer::Cancel)); // Esc, крестик
                                                      // Кнопка по умолчанию — первая: «Не выходить».
        assert_eq!((QUIT_KEEP, QUIT_CONFIRM), ("Не выходить", "Выйти"));
    }

    #[test]
    fn only_the_delete_button_cancels_the_recording() {
        use rfd::MessageDialogResult as Answer;
        assert!(cancel_confirmed_by(&Answer::Custom("Удалить".into())));
        assert!(!cancel_confirmed_by(&Answer::Custom(
            "Продолжить запись".into()
        )));
        assert!(!cancel_confirmed_by(&Answer::Cancel)); // Esc, крестик
        assert!(!cancel_confirmed_by(&Answer::Ok));
        assert_eq!(
            CANCEL_QUESTION,
            "Удалить текущую запись? Записанное не сохранится."
        );
        // Кнопка по умолчанию — первая: «Продолжить запись».
        assert_eq!(
            (CANCEL_KEEP, CANCEL_CONFIRM),
            ("Продолжить запись", "Удалить")
        );
    }

    #[test]
    fn live_window_follows_active_edges() {
        assert_eq!(live_window_change(false, true), Some(true));
        assert_eq!(live_window_change(true, false), Some(false));
        assert_eq!(live_window_change(true, true), None);
        assert_eq!(live_window_change(false, false), None);
    }

    #[test]
    fn live_start_without_provider_points_to_settings() {
        let conflict: api::Result<Value> = Err(api::Error::Status {
            code: 409,
            message: "Подключите Claude Code или Codex в настройках".into(),
        });
        assert_eq!(
            body_of(Action::LiveStart, Some(&conflict)),
            (
                "Не удалось запустить ассистента".into(),
                "Подключите Claude Code или Codex в настройках".into()
            )
        );
        assert!(needs_provider(Action::LiveStart, Some(&conflict)));
        assert!(!needs_provider(Action::Start, Some(&conflict)));
        let busy: api::Result<Value> = Err(api::Error::Status {
            code: 400,
            message: "Идёт обычная запись — сначала остановите её".into(),
        });
        assert!(!needs_provider(Action::LiveStart, Some(&busy)));
        assert_eq!(
            body_of(Action::LiveStart, Some(&busy)).1,
            "Идёт обычная запись — сначала остановите её"
        );
        assert!(!needs_provider(Action::LiveStart, None));
    }

    #[test]
    fn live_actions_report_refusals_and_stay_quiet_on_success() {
        let spawn_failed: api::Result<Value> = Ok(json!({
            "ok": false, "active": false, "error": "Не удалось запустить ассистента: нет python"}));
        assert_eq!(
            body_of(Action::LiveStart, Some(&spawn_failed)).1,
            "Не удалось запустить ассистента: нет python"
        );
        let not_live: api::Result<Value> = Ok(json!({"ok": false, "action": "not-live"}));
        assert_eq!(
            body_of(Action::LiveStop, Some(&not_live)),
            (
                "Не удалось остановить ассистента".into(),
                "Ассистент не запущен".into()
            )
        );
        for (action, reply) in [
            (
                Action::LiveStart,
                json!({"ok": true, "starting": true, "active": false, "error": null}),
            ),
            (
                Action::LiveStop,
                json!({"ok": true, "action": "stopping", "error": null}),
            ),
        ] {
            let reply: api::Result<Value> = Ok(reply);
            assert_eq!(action_notice(action, Some(&reply)), None, "{action:?}");
        }
        assert_eq!(Action::LiveStart.path(), "/live/start");
        assert_eq!(Action::LiveStop.path(), "/live/stop");
    }

    // --- «клик по уведомлению» -------------------------------------------

    #[test]
    fn notified_recording_opens_within_five_minutes() {
        let t0 = Instant::now();
        let stored = ("rec".to_string(), t0);
        assert_eq!(
            pending_recording(Some(&stored), t0 + Duration::from_secs(299)),
            Some("rec".to_string())
        );
        assert_eq!(
            pending_recording(Some(&stored), t0 + Duration::from_secs(301)),
            None
        );
        assert_eq!(pending_recording(None, t0), None);
    }
}
