// Плавающая панель ассистента (`live.html`): окно без рамки поверх звонка.
//
// Панель можно двигать (за шапку), растягивать за края и разворачивать на весь
// экран. Геометрию оболочка ведёт сама, а не плагин window-state (панель у
// него в denylist): кроме позиции и размера нужно помнить высоту развёрнутой
// панели отдельно от свёрнутой и «поверх всех окон». Всё это лежит в
// `<data_dir>/live_window.json` и пишется с задержкой, когда окно перестало
// двигаться, а при закрытии панели и выходе из приложения — сразу.
//
// Растягивание за края — штатное у Tauri 2.11 на Windows: окну без рамки с
// `resizable(true)` tauri-runtime-wry (`undecorated_resizing.rs`) ставит
// дочернее окно-кольцо шириной в системную рамку (SM_CXFRAME) поверх webview и
// отвечает на WM_NCHITTEST краями и углами. Прозрачность окна этому не мешает;
// тень (`shadow`) выключена, поэтому кольцо — по всем четырём сторонам, а не
// только сверху.
//
// IMPORTANT: окно создаётся только с главного потока (`run_on_main_thread`).
// Команды ниже синхронные — Tauri выполняет их на главном потоке, поэтому
// разворот и смена размера применяются сразу, до чтения новой геометрии.
// Замок состояния не держим во время вызовов окна: события окна (Moved,
// Resized) тоже берут его.

use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, Emitter, Manager, Monitor, PhysicalPosition, PhysicalSize, WebviewUrl,
    WebviewWindow, WebviewWindowBuilder, Window, WindowEvent,
};

use crate::logs::shell_log;
use crate::resident;

pub const LIVE_LABEL: &str = "live";
/// Событие окну панели: изменилось свёрнута/развёрнута, на весь экран,
/// поверх всех окон (`LiveView`).
pub const VIEW_EVENT: &str = "live-window";
const STATE_FILE: &str = "live_window.json";

/// Размеры — логические пиксели.
const DEFAULT_WIDTH: f64 = 360.0;
const MIN_WIDTH: f64 = 300.0;
/// Свёрнутая: шапка и последняя реплика. Это же минимальная высота окна.
const COLLAPSED_HEIGHT: f64 = 120.0;
const DEFAULT_EXPANDED_HEIGHT: f64 = 520.0;
/// Вид по высоте, с гистерезисом: свёрнутую вытянули выше `EXPAND_ABOVE` —
/// развернулась, развёрнутую сжали ниже `COLLAPSE_BELOW` — свернулась; между
/// ними вид прежний (у порога содержимое не мигает). Высота развёрнутой
/// запоминается только от `EXPAND_ABOVE`: «Развернуть» никогда не открывает
/// огрызок.
const COLLAPSE_BELOW: f64 = 170.0;
const EXPAND_ABOVE: f64 = 230.0;
/// Сколько после своей смены размера оболочка не верит событиям окна:
/// промежуточные Moved/Resized (верх уже сдвинут, высота ещё старая) — не
/// выбор человека. Снимается раньше — последним Resized с целевой высотой.
const OWN_RESIZE_QUIET: Duration = Duration::from_millis(500);
/// Отступ от краёв рабочей области при размещении в углу.
const MARGIN: f64 = 16.0;
/// Шапка панели: за неё окно двигают, её должно быть видно на экране.
const HEADER_HEIGHT: f64 = 40.0;
/// Сколько шапки по ширине должно остаться на экране, чтобы окно можно было
/// ухватить и вернуть.
const VISIBLE_GRIP: f64 = 80.0;
/// Окно перестало двигаться — через столько пишем файл.
const SAVE_DELAY: Duration = Duration::from_millis(600);

/// Рабочая область монитора (без панели задач) в физических пикселях и его
/// масштаб — как их отдаёт `Monitor`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Screen {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale: f64,
}

impl Screen {
    fn of(monitor: &Monitor) -> Screen {
        let area = monitor.work_area();
        Screen {
            x: area.position.x,
            y: area.position.y,
            width: area.size.width,
            height: area.size.height,
            scale: monitor.scale_factor(),
        }
    }

    fn scale(&self) -> f64 {
        if self.scale.is_finite() && self.scale > 0.0 {
            self.scale
        } else {
            1.0
        }
    }

    /// Рабочая область в логических пикселях: (x, y, ширина, высота).
    fn logical(&self) -> (f64, f64, f64, f64) {
        let scale = self.scale();
        (
            f64::from(self.x) / scale,
            f64::from(self.y) / scale,
            f64::from(self.width) / scale,
            f64::from(self.height) / scale,
        )
    }
}

/// Что помнит панель между открытиями (`live_window.json`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct LiveGeometry {
    /// Левый верхний угол окна в физических пикселях виртуального экрана:
    /// по нему видно, на каком мониторе панель стояла. Нет — в угол.
    pub x: Option<i32>,
    pub y: Option<i32>,
    /// Ширина — одна для свёрнутой и развёрнутой: сворачивание её не трогает.
    pub width: f64,
    /// Высота развёрнутой панели — последняя, которую выбрал человек.
    pub expanded_height: f64,
    pub expanded: bool,
    /// Была ли на весь экран. Пишется, но при открытии не восстанавливается:
    /// разворот (SW_MAXIMIZE) активирует окно и отнял бы фокус у звонка.
    pub maximized: bool,
    /// Поверх всех окон.
    pub pinned: bool,
}

impl Default for LiveGeometry {
    fn default() -> Self {
        LiveGeometry {
            x: None,
            y: None,
            width: DEFAULT_WIDTH,
            expanded_height: DEFAULT_EXPANDED_HEIGHT,
            expanded: false,
            maximized: false,
            pinned: true,
        }
    }
}

/// То, что нужно странице панели: какие кнопки и какой вид показать.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct LiveView {
    pub expanded: bool,
    pub maximized: bool,
    pub pinned: bool,
}

impl LiveGeometry {
    pub fn view(&self) -> LiveView {
        LiveView {
            expanded: self.expanded,
            maximized: self.maximized,
            pinned: self.pinned,
        }
    }

    /// Битые и слишком маленькие размеры из файла — по умолчанию.
    pub fn sanitized(mut self) -> Self {
        if !(self.width.is_finite() && self.width >= MIN_WIDTH) {
            self.width = DEFAULT_WIDTH;
        }
        if !(self.expanded_height.is_finite() && self.expanded_height >= EXPAND_ABOVE) {
            self.expanded_height = DEFAULT_EXPANDED_HEIGHT;
        }
        self
    }

    /// Высота окна в текущем виде (логические пиксели).
    pub fn height(&self) -> f64 {
        if self.expanded {
            self.expanded_height
        } else {
            COLLAPSED_HEIGHT
        }
    }
}

/// Окно сдвинули или изменили его размер: что из этого запомнить.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sample {
    /// Физические пиксели.
    pub x: i32,
    pub y: i32,
    /// Логические пиксели.
    pub width: f64,
    pub height: f64,
    pub maximized: bool,
    /// Событие Resized (а не Moved): только оно меняет размер и вид.
    pub resized: bool,
}

/// Вид после того, как человек изменил высоту: гистерезис между
/// `COLLAPSE_BELOW` и `EXPAND_ABOVE`.
pub fn next_expanded(expanded: bool, height: f64) -> bool {
    if !height.is_finite() {
        return expanded;
    }
    if expanded {
        height >= COLLAPSE_BELOW
    } else {
        height > EXPAND_ABOVE
    }
}

/// Новая геометрия после движения окна человеком. На весь экран — только
/// отметка: обычные позиция и размер остаются прежними, к ним окно и вернётся.
/// Moved меняет только место; Resized — ширину и вид. Высота развёрнутой
/// здесь не трогается: её фиксирует `commit`, когда окно перестало меняться.
pub fn record(geometry: &LiveGeometry, sample: Sample) -> LiveGeometry {
    let mut next = geometry.clone();
    next.maximized = sample.maximized;
    if sample.maximized {
        return next;
    }
    next.x = Some(sample.x);
    next.y = Some(sample.y);
    if !sample.resized {
        return next;
    }
    if sample.width.is_finite() && sample.width > 0.0 {
        next.width = sample.width;
    }
    if sample.height.is_finite() && sample.height > 0.0 {
        next.expanded = next_expanded(geometry.expanded, sample.height);
    }
    next
}

/// Что записать в файл: последняя высота окна (`height`, логическая)
/// становится высотой развёрнутой, только если панель в итоге развёрнута,
/// не на весь экран, высоту выбрал человек (а не ужал монитор — `capped`) и
/// она не меньше `EXPAND_ABOVE`. Иначе остаётся прежняя.
pub fn commit(geometry: &LiveGeometry, height: f64, capped: bool) -> LiveGeometry {
    let mut next = geometry.clone();
    if next.expanded && !next.maximized && !capped && height.is_finite() && height >= EXPAND_ABOVE {
        next.expanded_height = height;
    }
    next
}

/// Размер (логический), ужатый до рабочей области.
fn fit_size(screen: &Screen, width: f64, height: f64) -> (f64, f64) {
    let (_, _, area_width, area_height) = screen.logical();
    (width.min(area_width), height.min(area_height))
}

/// Правый нижний угол рабочей области (над панелью задач) с отступом.
/// (x, y, ширина, высота) — логические пиксели, как их берёт
/// `WebviewWindowBuilder`.
pub fn bottom_right(screen: Screen, width: f64, height: f64) -> (f64, f64, f64, f64) {
    let (area_x, area_y, area_width, area_height) = screen.logical();
    let width = width
        .min(area_width - 2.0 * MARGIN)
        .max(MIN_WIDTH.min(area_width));
    let height = height
        .min(area_height - 2.0 * MARGIN)
        .max(COLLAPSED_HEIGHT.min(area_height));
    (
        (area_x + area_width - MARGIN - width).max(area_x),
        (area_y + area_height - MARGIN - height).max(area_y),
        width,
        height,
    )
}

/// Окно с левым верхним углом (физические пиксели) и размером (логические)
/// целиком внутри рабочей области: размер ужат, угол сдвинут внутрь.
pub fn fit_into(screen: Screen, x: i32, y: i32, width: f64, height: f64) -> (f64, f64, f64, f64) {
    let scale = screen.scale();
    let (area_x, area_y, area_width, area_height) = screen.logical();
    let (width, height) = fit_size(&screen, width, height);
    let left = (f64::from(x) / scale)
        .min(area_x + area_width - width)
        .max(area_x);
    let top = (f64::from(y) / scale)
        .min(area_y + area_height - height)
        .max(area_y);
    (left, top, width, height)
}

/// Видна ли на этом экране шапка окна настолько, чтобы за неё взяться.
fn header_visible(screen: &Screen, x: i32, y: i32, width: f64) -> bool {
    let scale = screen.scale();
    let left = f64::from(x);
    let top = f64::from(y);
    let right = left + width * scale;
    let bottom = top + HEADER_HEIGHT * scale;
    let area_left = f64::from(screen.x);
    let area_top = f64::from(screen.y);
    let area_right = area_left + f64::from(screen.width);
    let area_bottom = area_top + f64::from(screen.height);
    let overlap_x = right.min(area_right) - left.max(area_left);
    let overlap_y = bottom.min(area_bottom) - top.max(area_top);
    overlap_x >= VISIBLE_GRIP * scale && overlap_y >= HEADER_HEIGHT * scale / 2.0
}

/// Где открыть панель: там, где её оставили, если тот монитор на месте и
/// шапку на нём видно; иначе — в правом нижнем углу основного монитора.
/// Размер — запомненный, ужатый до рабочей области. `None` — мониторов не
/// знаем: окно встанет, где решит система.
pub fn restore_rect(
    geometry: &LiveGeometry,
    screens: &[Screen],
    primary: Option<Screen>,
) -> Option<(f64, f64, f64, f64)> {
    let (width, height) = (geometry.width, geometry.height());
    if let (Some(x), Some(y)) = (geometry.x, geometry.y) {
        if let Some(screen) = screens
            .iter()
            .find(|screen| header_visible(screen, x, y, width))
        {
            return Some(fit_into(*screen, x, y, width, height));
        }
    }
    let screen = primary.or_else(|| screens.first().copied())?;
    Some(bottom_right(screen, width, height))
}

/// Высота, до которой развернуть: запомненная, но не выше рабочей области
/// (`area_height` — логическая; `None` — монитор не известен).
pub fn expanded_target(geometry: &LiveGeometry, area_height: Option<f64>) -> f64 {
    let height = geometry.clone().sanitized().expanded_height;
    match area_height {
        Some(area) if area.is_finite() && area > 0.0 => height.min(area),
        _ => height,
    }
}

/// Новый верх окна при смене высоты (физические пиксели). Панель в нижней
/// половине экрана растёт вверх (нижний край на месте, над панелью задач),
/// в верхней — вниз. Окно не выходит за рабочую область (`area` — её верх и
/// низ); монитор не известен — нижний край на месте.
pub fn resized_top(top: f64, height: f64, new_height: f64, area: Option<(f64, f64)>) -> f64 {
    let Some((area_top, area_bottom)) = area else {
        return top + height - new_height;
    };
    let center = top + height / 2.0;
    let middle = (area_top + area_bottom) / 2.0;
    let wanted = if center >= middle {
        top + height - new_height
    } else {
        top
    };
    wanted.min(area_bottom - new_height).max(area_top)
}

/// Порядок двух вызовов при смене высоты панели.
#[derive(Debug, PartialEq, Eq)]
pub enum ResizeOrder {
    /// Растёт: сначала поднять верх, потом вытянуть.
    MoveThenSize,
    /// Сжимается: сначала укоротить, потом опустить.
    SizeThenMove,
}

/// Между двумя вызовами окно на кадр видно: при обратном порядке растущая
/// панель свисала бы ниже нижнего края (за панель задач), а сжимающаяся —
/// на кадр уезжала бы вниз целиком.
pub fn resize_order(height: f64, new_height: f64) -> ResizeOrder {
    if new_height > height {
        ResizeOrder::MoveThenSize
    } else {
        ResizeOrder::SizeThenMove
    }
}

// --- состояние и файл -------------------------------------------------------

pub fn read_geometry(data_dir: &Path) -> LiveGeometry {
    std::fs::read_to_string(data_dir.join(STATE_FILE))
        .ok()
        .and_then(|raw| serde_json::from_str::<LiveGeometry>(&raw).ok())
        .unwrap_or_default()
        .sanitized()
}

pub fn write_geometry(data_dir: &Path, geometry: &LiveGeometry) -> std::io::Result<()> {
    std::fs::create_dir_all(data_dir)?;
    let body = serde_json::to_string_pretty(geometry).map_err(std::io::Error::other)?;
    let staged = data_dir.join(format!("{STATE_FILE}.tmp"));
    std::fs::write(&staged, body)?;
    std::fs::rename(&staged, data_dir.join(STATE_FILE))
}

/// Своя смена размера идёт: до `until` события окна — её эхо, а не выбор
/// человека. `height` — целевая внутренняя высота (физическая): Resized с ней
/// — последнее эхо, после него снова слушаем человека.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Quiet {
    pub until: Instant,
    pub height: Option<u32>,
}

/// Чьё событие окна.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Echo {
    /// Человек (или система) — запомнить.
    User,
    /// Промежуточное эхо своей смены размера — пропустить.
    Own,
    /// Последнее эхо (Resized с целевой высотой) — пропустить и снова слушать.
    OwnLast,
}

pub fn classify(quiet: Option<Quiet>, now: Instant, resized: bool, height: u32) -> Echo {
    match quiet {
        Some(quiet) if now < quiet.until => {
            if resized && quiet.height == Some(height) {
                Echo::OwnLast
            } else {
                Echo::Own
            }
        }
        _ => Echo::User,
    }
}

#[derive(Default)]
struct Inner {
    /// Читается из файла при первом открытии панели за запуск оболочки.
    geometry: Option<LiveGeometry>,
    /// Последняя высота окна (логическая) — кандидат в высоту развёрнутой,
    /// фиксируется `commit` при записи.
    height: Option<f64>,
    /// Высоту окна ужал монитор (запомненная не влезла) — её не фиксируем.
    capped: bool,
    quiet: Option<Quiet>,
    /// Когда геометрия менялась последний раз (для отложенной записи).
    touched: Option<Instant>,
    /// Есть незаписанные изменения.
    dirty: bool,
    /// Поток отложенной записи уже ждёт.
    saving: bool,
}

impl Inner {
    fn geometry(&mut self) -> &mut LiveGeometry {
        self.geometry
            .get_or_insert_with(|| read_geometry(&resident::data_dir()))
    }

    /// Зафиксировать высоту развёрнутой (см. `commit`).
    fn settle(&mut self) -> LiveGeometry {
        let (height, capped) = (self.height, self.capped);
        let geometry = self.geometry();
        if let Some(height) = height {
            *geometry = commit(geometry, height, capped);
        }
        geometry.clone()
    }
}

/// Геометрия панели в памяти оболочки (`app.manage`).
#[derive(Default, Clone)]
pub struct LivePanel {
    inner: Arc<Mutex<Inner>>,
    /// Файл пишут поток отложенной записи, закрытие окна и выход — по одному.
    writing: Arc<Mutex<()>>,
}

impl LivePanel {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
    }

    fn current(&self) -> LiveGeometry {
        self.lock().geometry().clone()
    }

    /// Поменять геометрию (и остальное состояние); вернуть (было, стало).
    fn update(
        &self,
        change: impl FnOnce(&mut LiveGeometry, &mut Inner),
    ) -> (LiveGeometry, LiveGeometry) {
        let mut inner = self.lock();
        let mut geometry = inner.geometry().clone();
        let before = geometry.clone();
        change(&mut geometry, &mut inner);
        inner.geometry = Some(geometry.clone());
        inner.touched = Some(Instant::now());
        inner.dirty = true;
        (before, geometry)
    }

    /// Записать файл, когда окно перестанет меняться на `SAVE_DELAY`. Пока
    /// окно тащат, события идут десятками — поток один, он ждёт тишины.
    fn save_later(&self) {
        {
            let mut inner = self.lock();
            if inner.saving {
                return;
            }
            inner.saving = true;
        }
        let panel = self.clone();
        std::thread::spawn(move || {
            loop {
                let since = {
                    let mut inner = panel.lock();
                    let since = inner.touched.map_or(SAVE_DELAY, |at| at.elapsed());
                    if since >= SAVE_DELAY {
                        inner.saving = false;
                        break;
                    }
                    since
                };
                std::thread::sleep(SAVE_DELAY - since);
            }
            panel.flush();
        });
    }

    /// Записать незаписанное сейчас (и из потока отложенной записи):
    /// окно закрывается, приложение выходит.
    pub fn flush(&self) {
        let _writing = self
            .writing
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        let geometry = {
            let mut inner = self.lock();
            if !inner.dirty {
                return;
            }
            inner.dirty = false;
            inner.settle()
        };
        if let Err(error) = write_geometry(&resident::data_dir(), &geometry) {
            shell_log!("панель ассистента: {STATE_FILE} не записался: {error}");
        }
    }
}

/// Записать отложенную геометрию панели (выход из приложения).
pub fn flush(app: &AppHandle) {
    if let Some(panel) = app.try_state::<LivePanel>() {
        panel.flush();
    }
}

// --- окно -------------------------------------------------------------------

/// Открыть панель ассистента. Только с главного потока (`run_on_main_thread`
/// из опроса трея). Уже открыта — не трогаем: фокус она не забирает.
pub fn open_live(app: &AppHandle) {
    if app.get_webview_window(LIVE_LABEL).is_some() {
        return;
    }
    let panel = app.state::<LivePanel>();
    // На весь экран при открытии не возвращаем (см. `LiveGeometry::maximized`).
    let (_, geometry) = panel.update(|geometry, _| geometry.maximized = false);
    let mut builder =
        WebviewWindowBuilder::new(app, LIVE_LABEL, WebviewUrl::App("live.html".into()))
            .title("Meet — ассистент")
            .inner_size(geometry.width, geometry.height())
            .min_inner_size(MIN_WIDTH, COLLAPSED_HEIGHT)
            .resizable(true)
            .maximizable(true)
            .decorations(false)
            .transparent(true)
            // Тень Windows у окна без рамки — светлая каёмка вокруг
            // скруглённой панели; без тени и рамка для растягивания — по
            // всем четырём краям.
            .shadow(false)
            .always_on_top(geometry.pinned)
            // Откреплённую панель другие окна могут закрыть — тогда её
            // находят на панели задач.
            .skip_taskbar(geometry.pinned)
            .focused(false);
    let screens: Vec<Screen> = app
        .available_monitors()
        .map(|monitors| monitors.iter().map(Screen::of).collect())
        .unwrap_or_default();
    let primary = app.primary_monitor().ok().flatten().map(|m| Screen::of(&m));
    let rect = restore_rect(&geometry, &screens, primary);
    match rect {
        Some((x, y, width, height)) => {
            builder = builder.position(x, y).inner_size(width, height);
        }
        None => shell_log!("панель ассистента: монитор не найден, позиция по умолчанию"),
    }
    // События создания окна — не выбор человека; ужатая под монитор высота —
    // тоже: запомненный размер остаётся прежним.
    let opened_height = rect.map_or(geometry.height(), |(_, _, _, height)| height);
    panel.update(|_, inner| {
        inner.height = Some(opened_height);
        inner.capped = opened_height < geometry.height();
        inner.quiet = Some(Quiet {
            until: Instant::now() + OWN_RESIZE_QUIET,
            height: None,
        });
    });
    if let Err(error) = builder.build() {
        shell_log!("панель ассистента не открылась: {error}");
    }
}

/// Закрыть панель ассистента (ассистент остановился). Закрыта человеком —
/// ничего не делаем.
pub fn close_live(app: &AppHandle) {
    app.state::<LivePanel>().flush();
    if let Some(window) = app.get_webview_window(LIVE_LABEL) {
        if let Err(error) = window.destroy() {
            shell_log!("панель ассистента не закрылась: {error}");
        }
    }
}

/// Окно панели сдвинули или изменили его размер (руками, кнопкой, системой):
/// запомнить и, если сменился вид, сказать странице.
pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if window.label() != LIVE_LABEL {
        return;
    }
    let resized = match event {
        WindowEvent::Resized(_) => true,
        WindowEvent::Moved(_) => false,
        WindowEvent::Destroyed => {
            window.state::<LivePanel>().flush();
            return;
        }
        _ => return,
    };
    // Свёрнутое системой окно (Win+D) — размер нулевой, запоминать нечего.
    if window.is_minimized().unwrap_or(false) {
        return;
    }
    let (Ok(scale), Ok(position), Ok(size)) = (
        window.scale_factor(),
        window.outer_position(),
        window.inner_size(),
    ) else {
        return;
    };
    if size.width == 0 || size.height == 0 {
        return;
    }
    let panel = window.state::<LivePanel>();
    {
        let mut inner = panel.lock();
        match classify(inner.quiet, Instant::now(), resized, size.height) {
            Echo::Own => return,
            Echo::OwnLast => {
                inner.quiet = None;
                return;
            }
            Echo::User => inner.quiet = None,
        }
    }
    let sample = Sample {
        x: position.x,
        y: position.y,
        width: f64::from(size.width) / scale,
        height: f64::from(size.height) / scale,
        maximized: window.is_maximized().unwrap_or(false),
        resized,
    };
    let (before, after) = panel.update(|geometry, inner| {
        *geometry = record(geometry, sample);
        if resized && !sample.maximized {
            inner.height = Some(sample.height);
            inner.capped = false;
        }
    });
    if before.view() != after.view() {
        let _ = window.emit_to(LIVE_LABEL, VIEW_EVENT, after.view());
    }
    panel.save_later();
}

fn live_window(app: &AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window(LIVE_LABEL)
        .ok_or_else(|| "панели ассистента нет".to_string())
}

fn text(error: tauri::Error) -> String {
    error.to_string()
}

/// Вид панели при загрузке страницы.
#[tauri::command]
pub fn live_window_state(app: AppHandle) -> LiveView {
    app.state::<LivePanel>().current().view()
}

/// Свернуть (120) или развернуть (до запомненной высоты) панель. Ширина не
/// меняется; на весь экран — сначала вернуть обычный размер.
#[tauri::command]
pub fn live_set_expanded(app: AppHandle, expanded: bool) -> Result<LiveView, String> {
    let window = live_window(&app)?;
    let panel = app.state::<LivePanel>();
    // Всё, что окно делает до конца команды, — эхо. Высоту, до которой
    // человек дотянул развёрнутую, — зафиксировать до сворачивания.
    let geometry = {
        let mut inner = panel.lock();
        inner.quiet = Some(Quiet {
            until: Instant::now() + OWN_RESIZE_QUIET,
            height: None,
        });
        inner.settle()
    };
    if window.is_maximized().map_err(text)? {
        window.unmaximize().map_err(text)?;
    }
    let scale = window.scale_factor().map_err(text)?;
    let position = window.outer_position().map_err(text)?;
    // Без рамки и тени внешний размер совпадает с внутренним; для якоря
    // берём внешний (это край окна на экране), задаём — внутренний.
    let outer = window.outer_size().map_err(text)?;
    let inner = window.inner_size().map_err(text)?;
    let area = window
        .current_monitor()
        .ok()
        .flatten()
        .map(|monitor| Screen::of(&monitor));
    let wanted = if expanded {
        expanded_target(&geometry, area.map(|screen| screen.logical().3))
    } else {
        COLLAPSED_HEIGHT
    };
    let new_height = (wanted * scale).round();
    // Последнее эхо — Resized с этой высотой; после него снова слушаем человека.
    panel.lock().quiet = Some(Quiet {
        until: Instant::now() + OWN_RESIZE_QUIET,
        height: Some(new_height as u32),
    });
    let frame = f64::from(outer.height) - f64::from(inner.height);
    let top = resized_top(
        f64::from(position.y),
        f64::from(outer.height),
        new_height + frame,
        area.map(|screen| {
            let top = f64::from(screen.y);
            (top, top + f64::from(screen.height))
        }),
    );
    let size = || {
        window
            .set_size(PhysicalSize::new(inner.width, new_height as u32))
            .map_err(text)
    };
    let place = || {
        window
            .set_position(PhysicalPosition::new(position.x, top.round() as i32))
            .map_err(text)
    };
    match resize_order(f64::from(outer.height), new_height + frame) {
        ResizeOrder::MoveThenSize => {
            place()?;
            size()?;
        }
        ResizeOrder::SizeThenMove => {
            size()?;
            place()?;
        }
    }
    let (_, geometry) = panel.update(|geometry, inner| {
        geometry.expanded = expanded;
        geometry.maximized = false;
        geometry.x = Some(position.x);
        geometry.y = Some(top.round() as i32);
        inner.height = Some(wanted);
        // Ужатая монитором высота — не выбор человека: запомненная остаётся.
        inner.capped = expanded && wanted < geometry.expanded_height;
    });
    panel.save_later();
    Ok(geometry.view())
}

/// На весь экран (рабочую область) и обратно. «Поверх всех окон» не
/// меняется.
#[tauri::command]
pub fn live_set_maximized(app: AppHandle, maximized: bool) -> Result<LiveView, String> {
    let window = live_window(&app)?;
    if maximized {
        window.maximize().map_err(text)?;
    } else {
        window.unmaximize().map_err(text)?;
    }
    let panel = app.state::<LivePanel>();
    let (_, geometry) = panel.update(|geometry, _| geometry.maximized = maximized);
    panel.save_later();
    Ok(geometry.view())
}

/// Закрепить поверх всех окон или открепить. Откреплённая видна на панели
/// задач: иначе, закрытую другими окнами, её не найти.
#[tauri::command]
pub fn live_set_pinned(app: AppHandle, pinned: bool) -> Result<LiveView, String> {
    let window = live_window(&app)?;
    window.set_always_on_top(pinned).map_err(text)?;
    window.set_skip_taskbar(pinned).map_err(text)?;
    let panel = app.state::<LivePanel>();
    let (_, geometry) = panel.update(|geometry, _| geometry.pinned = pinned);
    panel.save_later();
    Ok(geometry.view())
}

/// Перетаскивание окна за шапку (зажата левая кнопка мыши).
#[tauri::command]
pub fn live_start_drag(app: AppHandle) -> Result<(), String> {
    live_window(&app)?.start_dragging().map_err(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn screen(width: u32, height: u32, scale: f64) -> Screen {
        Screen {
            x: 0,
            y: 0,
            width,
            height,
            scale,
        }
    }

    fn geometry() -> LiveGeometry {
        LiveGeometry::default()
    }

    #[test]
    fn panel_sits_bottom_right_above_the_taskbar() {
        // 1920×1080, панель задач 40 px снизу: рабочая область 1920×1040.
        assert_eq!(
            bottom_right(screen(1920, 1040, 1.0), 360.0, 120.0),
            (1544.0, 904.0, 360.0, 120.0)
        );
        // 150 %: физические пиксели → логические, отступ тот же в логических.
        assert_eq!(
            bottom_right(screen(2880, 1560, 1.5), 360.0, 120.0),
            (1544.0, 904.0, 360.0, 120.0)
        );
        // Рабочая область не с нуля (панель задач слева/сверху).
        let shifted = Screen {
            x: 60,
            y: 40,
            ..screen(1860, 1040, 1.0)
        };
        assert_eq!(
            bottom_right(shifted, 360.0, 120.0),
            (1544.0, 944.0, 360.0, 120.0)
        );
        // Масштаб не известен — как 100 %.
        assert_eq!(
            bottom_right(screen(1920, 1040, 0.0), 360.0, 120.0),
            (1544.0, 904.0, 360.0, 120.0)
        );
        // Развёрнутая выше экрана — ужата до рабочей области с отступами.
        assert_eq!(
            bottom_right(screen(1280, 700, 1.0), 360.0, 900.0),
            (904.0, 16.0, 360.0, 668.0)
        );
    }

    #[test]
    fn first_open_goes_to_the_corner_of_the_primary_monitor() {
        let primary = screen(1920, 1040, 1.0);
        assert_eq!(
            restore_rect(&geometry(), &[primary], Some(primary)),
            Some((1544.0, 904.0, 360.0, 120.0))
        );
        // Основной не назван — первый из списка; мониторов нет — None.
        assert_eq!(
            restore_rect(&geometry(), &[primary], None),
            Some((1544.0, 904.0, 360.0, 120.0))
        );
        assert_eq!(restore_rect(&geometry(), &[], None), None);
    }

    #[test]
    fn saved_place_and_size_come_back() {
        let primary = screen(1920, 1040, 1.0);
        let saved = LiveGeometry {
            x: Some(200),
            y: Some(100),
            width: 640.0,
            expanded_height: 700.0,
            expanded: true,
            ..geometry()
        };
        assert_eq!(
            restore_rect(&saved, &[primary], Some(primary)),
            Some((200.0, 100.0, 640.0, 700.0))
        );
        // Свёрнутая — та же ширина, высота свёрнутой.
        let collapsed = LiveGeometry {
            expanded: false,
            ..saved.clone()
        };
        assert_eq!(
            restore_rect(&collapsed, &[primary], Some(primary)),
            Some((200.0, 100.0, 640.0, 120.0))
        );
    }

    #[test]
    fn saved_place_on_a_second_monitor_uses_its_scale() {
        let primary = screen(1920, 1040, 1.0);
        // Второй монитор справа, 150 %: 2880×1560 физических.
        let second = Screen {
            x: 1920,
            y: 0,
            width: 2880,
            height: 1560,
            scale: 1.5,
        };
        let saved = LiveGeometry {
            x: Some(1920 + 300),
            y: Some(150),
            ..geometry()
        };
        assert_eq!(
            restore_rect(&saved, &[primary, second], Some(primary)),
            Some((1480.0, 100.0, 360.0, 120.0))
        );
    }

    #[test]
    fn panel_from_a_removed_monitor_falls_back_to_the_corner() {
        let primary = screen(1920, 1040, 1.0);
        // Стояла на втором мониторе справа, его отключили.
        let saved = LiveGeometry {
            x: Some(2400),
            y: Some(300),
            ..geometry()
        };
        assert_eq!(
            restore_rect(&saved, &[primary], Some(primary)),
            Some((1544.0, 904.0, 360.0, 120.0))
        );
        // Шапка ушла за верх экрана — тоже в угол.
        let above = LiveGeometry {
            x: Some(400),
            y: Some(-200),
            ..geometry()
        };
        assert_eq!(
            restore_rect(&above, &[primary], Some(primary)),
            Some((1544.0, 904.0, 360.0, 120.0))
        );
    }

    #[test]
    fn partly_visible_panel_is_pulled_inside_the_work_area() {
        let primary = screen(1920, 1040, 1.0);
        // Шапки видно 120 px справа — окно на месте, но целиком на экране.
        let saved = LiveGeometry {
            x: Some(1800),
            y: Some(980),
            ..geometry()
        };
        assert_eq!(
            restore_rect(&saved, &[primary], Some(primary)),
            Some((1560.0, 920.0, 360.0, 120.0))
        );
        // Видно меньше 80 px шапки — не ухватить: в угол.
        let edge = LiveGeometry {
            x: Some(1860),
            ..saved
        };
        assert_eq!(
            restore_rect(&edge, &[primary], Some(primary)),
            Some((1544.0, 904.0, 360.0, 120.0))
        );
    }

    #[test]
    fn fit_into_shrinks_a_panel_larger_than_the_screen() {
        assert_eq!(
            fit_into(screen(1280, 680, 1.0), 100, 50, 1600.0, 900.0),
            (0.0, 0.0, 1280.0, 680.0)
        );
    }

    #[test]
    fn broken_sizes_from_the_file_fall_back_to_defaults() {
        let broken = LiveGeometry {
            width: 40.0,
            expanded_height: f64::NAN,
            ..geometry()
        }
        .sanitized();
        assert_eq!(broken.width, 360.0);
        assert_eq!(broken.expanded_height, 520.0);
        let small = LiveGeometry {
            expanded_height: 200.0,
            ..geometry()
        }
        .sanitized();
        assert_eq!(small.expanded_height, 520.0);
    }

    fn sample(width: f64, height: f64) -> Sample {
        Sample {
            x: 10,
            y: 20,
            width,
            height,
            maximized: false,
            resized: true,
        }
    }

    fn moved(x: i32, y: i32) -> Sample {
        Sample {
            x,
            y,
            width: 360.0,
            height: 120.0,
            maximized: false,
            resized: false,
        }
    }

    fn expanded() -> LiveGeometry {
        LiveGeometry {
            expanded: true,
            expanded_height: 640.0,
            ..geometry()
        }
    }

    #[test]
    fn manual_resize_of_the_expanded_panel_becomes_its_size_when_saved() {
        let next = record(&expanded(), sample(800.0, 700.0));
        assert_eq!((next.x, next.y), (Some(10), Some(20)));
        assert_eq!(next.width, 800.0);
        assert!(next.expanded);
        // Высота развёрнутой фиксируется при записи, не на каждом событии.
        assert_eq!(next.expanded_height, 640.0);
        assert_eq!(commit(&next, 700.0, false).expanded_height, 700.0);
    }

    #[test]
    fn collapsed_panel_keeps_its_expanded_height() {
        // Свернули кнопкой: высота 120, ширина та же.
        let collapsed = record(&expanded(), sample(800.0, 120.0));
        assert!(!collapsed.expanded);
        assert_eq!(commit(&collapsed, 120.0, false).expanded_height, 640.0);
        // Свёрнутую растянули вширь — развёрнутая высота не меняется.
        let wider = record(&collapsed, sample(900.0, 130.0));
        assert!(!wider.expanded);
        assert_eq!(wider.width, 900.0);
        assert_eq!(commit(&wider, 130.0, false).expanded_height, 640.0);
    }

    #[test]
    fn stretching_the_collapsed_panel_down_expands_it() {
        let next = record(&geometry(), sample(360.0, 420.0));
        assert!(next.expanded);
        assert_eq!(commit(&next, 420.0, false).expanded_height, 420.0);
    }

    #[test]
    fn mode_has_hysteresis_around_the_threshold() {
        // Свёрнутая разворачивается только выше 230.
        assert!(!next_expanded(false, 200.0));
        assert!(!next_expanded(false, 230.0));
        assert!(next_expanded(false, 231.0));
        // Развёрнутая сворачивается только ниже 170.
        assert!(next_expanded(true, 200.0));
        assert!(next_expanded(true, 170.0));
        assert!(!next_expanded(true, 169.0));
        assert!(next_expanded(true, f64::NAN));
    }

    #[test]
    fn dragging_through_the_threshold_does_not_flip_or_leave_a_stub() {
        // Регрессия: тянули развёрнутую вниз через 200 — вид мигал, а высота
        // развёрнутой становилась огрызком.
        let mut g = expanded();
        for height in [400.0, 250.0, 205.0, 195.0, 205.0, 180.0] {
            g = record(&g, sample(360.0, height));
            assert!(g.expanded, "{height}");
        }
        // Остановились на 180: развёрнута, но такую высоту не запоминаем.
        assert_eq!(commit(&g, 180.0, false).expanded_height, 640.0);
        // Дотянули до 150 — свернулась; запомненная прежняя.
        g = record(&g, sample(360.0, 150.0));
        assert!(!g.expanded);
        assert_eq!(commit(&g, 150.0, false).expanded_height, 640.0);
        // Обратно до 210 — всё ещё свёрнута.
        g = record(&g, sample(360.0, 210.0));
        assert!(!g.expanded);
    }

    #[test]
    fn moved_events_never_change_the_mode_or_size() {
        // Регрессия: Moved от своей смены размера (верх сдвинут, высота ещё
        // 120) сворачивал только что развёрнутую панель.
        let g = record(&expanded(), moved(40, 50));
        assert!(g.expanded);
        assert_eq!((g.x, g.y, g.width), (Some(40), Some(50), 360.0));
        let collapsed = record(
            &geometry(),
            Sample {
                height: 900.0,
                ..moved(1, 2)
            },
        );
        assert!(!collapsed.expanded);
    }

    #[test]
    fn height_capped_by_the_monitor_is_not_remembered() {
        let g = expanded();
        assert_eq!(commit(&g, 500.0, true).expanded_height, 640.0);
        assert_eq!(commit(&g, 500.0, false).expanded_height, 500.0);
        // На весь экран — тоже не запоминаем.
        let full = LiveGeometry {
            maximized: true,
            ..g
        };
        assert_eq!(commit(&full, 1040.0, false).expanded_height, 640.0);
    }

    #[test]
    fn own_resize_echo_is_skipped_until_the_target_height_arrives() {
        let now = Instant::now();
        let quiet = Some(Quiet {
            until: now + Duration::from_millis(500),
            height: Some(780),
        });
        // Moved и промежуточный Resized — эхо.
        assert_eq!(classify(quiet, now, false, 180), Echo::Own);
        assert_eq!(classify(quiet, now, true, 180), Echo::Own);
        assert_eq!(classify(quiet, now, false, 780), Echo::Own);
        // Resized с целевой высотой — последнее эхо.
        assert_eq!(classify(quiet, now, true, 780), Echo::OwnLast);
        // Время вышло — снова человек.
        let later = now + Duration::from_millis(600);
        assert_eq!(classify(quiet, later, true, 180), Echo::User);
        assert_eq!(classify(None, now, true, 180), Echo::User);
        // Без целевой высоты (открытие окна) — эхо до конца срока.
        let opening = Some(Quiet {
            until: now + Duration::from_millis(500),
            height: None,
        });
        assert_eq!(classify(opening, now, true, 780), Echo::Own);
    }

    #[test]
    fn maximized_panel_keeps_its_normal_geometry() {
        let normal = LiveGeometry {
            x: Some(300),
            y: Some(200),
            width: 500.0,
            expanded_height: 600.0,
            expanded: true,
            ..geometry()
        };
        let full = record(
            &normal,
            Sample {
                x: 0,
                y: 0,
                width: 1920.0,
                height: 1040.0,
                maximized: true,
                resized: true,
            },
        );
        assert!(full.maximized);
        assert_eq!(
            LiveGeometry {
                maximized: false,
                ..full.clone()
            },
            normal
        );
        // Вернулся к обычному размеру — отметка снята.
        assert!(!record(&full, sample(500.0, 600.0)).maximized);
    }

    #[test]
    fn expanded_target_is_remembered_but_fits_the_screen() {
        let tall = LiveGeometry {
            expanded_height: 900.0,
            ..geometry()
        };
        assert_eq!(expanded_target(&tall, Some(1040.0)), 900.0);
        assert_eq!(expanded_target(&tall, Some(700.0)), 700.0);
        assert_eq!(expanded_target(&tall, None), 900.0);
        assert_eq!(expanded_target(&geometry(), Some(1040.0)), 520.0);
    }

    #[test]
    fn panel_in_the_lower_half_grows_upward() {
        // Было 120 высотой с верхом на 904 (низ 1024) — стало 520.
        let area = Some((0.0, 1040.0));
        assert_eq!(resized_top(904.0, 120.0, 520.0, area), 504.0);
        assert_eq!(resized_top(504.0, 520.0, 120.0, area), 904.0);
        // Монитор не известен — нижний край на месте.
        assert_eq!(resized_top(904.0, 120.0, 520.0, None), 504.0);
    }

    #[test]
    fn panel_in_the_upper_half_grows_downward() {
        let area = Some((0.0, 1040.0));
        assert_eq!(resized_top(40.0, 120.0, 520.0, area), 40.0);
        assert_eq!(resized_top(40.0, 520.0, 120.0, area), 40.0);
    }

    #[test]
    fn resized_panel_stays_inside_the_work_area() {
        let area = Some((0.0, 1040.0));
        // Растёт вверх, но места сверху нет — упирается в верх экрана.
        assert_eq!(resized_top(600.0, 120.0, 900.0, area), 0.0);
        // Растёт вниз, но места снизу нет — поднимается.
        assert_eq!(resized_top(400.0, 120.0, 900.0, area), 140.0);
    }

    #[test]
    fn resize_order_never_overshoots_the_bottom_edge() {
        assert_eq!(resize_order(120.0, 520.0), ResizeOrder::MoveThenSize);
        assert_eq!(resize_order(520.0, 120.0), ResizeOrder::SizeThenMove);
        assert_eq!(resize_order(300.0, 300.0), ResizeOrder::SizeThenMove);
    }

    fn temp_dir(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("meet-live-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn geometry_round_trips_through_the_file() {
        let dir = temp_dir("roundtrip");
        assert_eq!(read_geometry(&dir), geometry());
        let saved = LiveGeometry {
            x: Some(-1500),
            y: Some(300),
            width: 720.0,
            expanded_height: 640.0,
            expanded: true,
            maximized: true,
            pinned: false,
        };
        write_geometry(&dir, &saved).unwrap();
        assert_eq!(read_geometry(&dir), saved);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn broken_or_partial_file_gives_defaults() {
        let dir = temp_dir("broken");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(STATE_FILE), "{не json").unwrap();
        assert_eq!(read_geometry(&dir), geometry());
        std::fs::write(dir.join(STATE_FILE), r#"{"pinned": false, "width": 10}"#).unwrap();
        assert_eq!(
            read_geometry(&dir),
            LiveGeometry {
                pinned: false,
                ..geometry()
            }
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
