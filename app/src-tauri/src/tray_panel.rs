// Панель записи под значком в строке меню macOS (`tray.html`).
//
// Левый щелчок по значку на macOS открывает маленькое окно без рамки прямо
// под ним: идёт ли запись и сколько, «Остановить», «Начать запись», последняя
// запись. Правый щелчок — прежнее меню. На Windows левый щелчок, как и
// раньше, открывает окно приложения (`tray::left_click`); код панели от ОС не
// зависит, но вызывается только оттуда.
//
// Окно создаётся при первом щелчке и дальше только прячется: второй показ
// мгновенный, а тем, кто панель не открывает, она не стоит ничего. Страница
// сама слушает резидента (SSE, тот же клиент `lib/api.ts`, что у окна), пока
// панель видна, — оболочка лишь говорит ей «показана/спрятана» событием
// `tray-panel`.
//
// Прячется панель по Esc, повторным щелчком по значку и щелчком мимо (окно
// теряет фокус). Щелчок по значку при открытой панели сначала снимает с неё
// фокус, и только потом приходит сам щелчок — без `REOPEN_GUARD` панель
// пряталась бы и тут же открывалась снова. Щелчок ловим на нажатии кнопки
// (как системные меню): к отпусканию долгого щелчка защита бы истекла.
//
// IMPORTANT: приложение при закрытии панели не прячем (`AppHandle::hide`),
// хотя так фокус вернулся бы прежней программе: у спрятанного приложения
// tao `show` окно на экран не выводит, а `set_focus` его не трогает (окно
// «не видно»), — панель и окна Meet больше не открывались бы до щелчка по
// Dock. Фокус после Esc остаётся у Meet.
//
// macOS: окно прозрачное, под страницей — системное «стекло» popover
// (NSVisualEffectView через `window-vibrancy`, Effect::Popover) со
// скруглением; прозрачность окна требует частного API — фича
// `macos-private-api` у tauri и `app.macOSPrivateApi` в tauri.conf.json.

use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, Monitor, Rect, WebviewUrl,
    WebviewWindow, WebviewWindowBuilder, Window, WindowEvent,
};

use crate::logs::shell_log;

pub const PANEL_LABEL: &str = "tray-panel";
/// Событие странице панели: `{ "visible": bool }`.
pub const PANEL_EVENT: &str = "tray-panel";

/// Размеры — логические пиксели (точки macOS).
pub const WIDTH: f64 = 304.0;
/// До первого замера страницей.
const DEFAULT_HEIGHT: f64 = 280.0;
const MIN_HEIGHT: f64 = 120.0;
const MAX_HEIGHT: f64 = 560.0;
/// Зазор между низом строки меню (значка) и панелью — как у системных.
const GAP: f64 = 6.0;
/// Отступ от краёв экрана, если панель упирается в край.
const MARGIN: f64 = 8.0;
/// Скругление «стекла» под страницей — то же, что у `.tp` в tray.css.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const RADIUS: f64 = 12.0;
/// Щелчок по значку в течение этого времени после того, как панель спряталась
/// из-за потери фокуса, — тот самый щелчок, что снял фокус: закрыть, а не
/// открыть снова.
const REOPEN_GUARD: Duration = Duration::from_millis(350);
/// Страница не прислала свой размер (не загрузилась) — показать как есть.
const SHOW_FALLBACK: Duration = Duration::from_millis(600);

/// Прямоугольник в физических пикселях.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Area {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl Area {
    fn contains(&self, x: f64, y: f64) -> bool {
        x >= self.x && x < self.x + self.width && y >= self.y && y < self.y + self.height
    }

    /// Расстояние от точки до прямоугольника (0 — внутри).
    fn distance(&self, x: f64, y: f64) -> f64 {
        let dx = (self.x - x).max(0.0).max(x - (self.x + self.width));
        let dy = (self.y - y).max(0.0).max(y - (self.y + self.height));
        dx.hypot(dy)
    }

    /// Значок из события трея. tray-icon на macOS и Windows отдаёт физические
    /// пиксели; логические (другие ОС) берём как есть.
    pub fn of_rect(rect: &Rect) -> Area {
        let position = rect.position.to_physical::<f64>(1.0);
        let size = rect.size.to_physical::<f64>(1.0);
        Area {
            x: position.x,
            y: position.y,
            width: size.width,
            height: size.height,
        }
    }
}

/// Монитор: границы, рабочая область (без строки меню и Dock / панели задач)
/// — физические пиксели, как их отдаёт `Monitor`, — и масштаб.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Display {
    pub bounds: Area,
    pub work: Area,
    pub scale: f64,
}

impl Display {
    fn of(monitor: &Monitor) -> Display {
        let (position, size, work) = (monitor.position(), monitor.size(), monitor.work_area());
        Display {
            bounds: Area {
                x: f64::from(position.x),
                y: f64::from(position.y),
                width: f64::from(size.width),
                height: f64::from(size.height),
            },
            work: Area {
                x: f64::from(work.position.x),
                y: f64::from(work.position.y),
                width: f64::from(work.size.width),
                height: f64::from(work.size.height),
            },
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
}

/// Монитор, где точка `point` в логических пикселях общего пространства
/// (точках macOS): его границы, делённые на *его* масштаб.
fn display_at(point: (f64, f64), displays: &[Display]) -> Option<Display> {
    displays
        .iter()
        .find(|display| {
            let scale = display.scale();
            let bounds = display.bounds;
            Area {
                x: bounds.x / scale,
                y: bounds.y / scale,
                width: bounds.width / scale,
                height: bounds.height / scale,
            }
            .contains(point.0, point.1)
        })
        .copied()
}

/// Монитор со значком.
///
/// macOS: и прямоугольник значка, и `Monitor::position()` — точки, умноженные
/// на масштаб *своего* экрана, поэтому при мониторах с разным масштабом
/// «физические» прямоугольники перекрываются, и по центру значка экран не
/// угадать. Там решает курсор (`cursor` — точки общего пространства: щелчок
/// только что был по значку). Без курсора (и на Windows, где пространство
/// сплошное) — монитор, где центр значка; иначе ближайший.
pub fn pick_display(
    icon: Area,
    cursor: Option<(f64, f64)>,
    displays: &[Display],
) -> Option<Display> {
    cursor
        .and_then(|point| display_at(point, displays))
        .or_else(|| display_of(icon, displays))
}

fn display_of(icon: Area, displays: &[Display]) -> Option<Display> {
    let (cx, cy) = (icon.x + icon.width / 2.0, icon.y + icon.height / 2.0);
    displays
        .iter()
        .find(|display| display.bounds.contains(cx, cy))
        .or_else(|| {
            displays.iter().min_by(|a, b| {
                a.bounds
                    .distance(cx, cy)
                    .total_cmp(&b.bounds.distance(cx, cy))
            })
        })
        .copied()
}

/// Левый верхний угол панели (логические пиксели) размером `width`×`height`
/// под значком `icon` на мониторе `display` (`pick_display`): по центру под
/// ним, на `GAP` ниже, целиком в рабочей области монитора (с отступом
/// `MARGIN` от краёв). Значок в нижней половине экрана (панель задач снизу)
/// — панель над ним. Значок пересчитывается масштабом этого монитора.
pub fn panel_origin(icon: Area, display: Display, width: f64, height: f64) -> (f64, f64) {
    let scale = display.scale();
    let logical = |area: Area| Area {
        x: area.x / scale,
        y: area.y / scale,
        width: area.width / scale,
        height: area.height / scale,
    };
    let (icon, work, bounds) = (
        logical(icon),
        logical(display.work),
        logical(display.bounds),
    );
    let span = |start: f64, length: f64, size: f64, wanted: f64| {
        let low = start + MARGIN;
        let high = start + length - MARGIN - size;
        if high < low {
            // Не влезает с отступами — по центру области (или от её края).
            start + ((length - size) / 2.0).max(0.0)
        } else {
            wanted.clamp(low, high)
        }
    };
    let x = span(
        work.x,
        work.width,
        width,
        icon.x + icon.width / 2.0 - width / 2.0,
    );
    let below = icon.y + icon.height / 2.0 < bounds.y + bounds.height / 2.0;
    let wanted_y = if below {
        icon.y + icon.height + GAP
    } else {
        icon.y - GAP - height
    };
    // Под строкой меню, а не на ней: верх — не выше рабочей области.
    let y = if below {
        let top = wanted_y.max(work.y + GAP);
        if top + height > work.y + work.height - MARGIN {
            span(work.y, work.height, height, top)
        } else {
            top
        }
    } else {
        span(work.y, work.height, height, wanted_y)
    };
    (x, y)
}

/// Курсор в точках общего пространства macOS: tao отдаёт его умноженным на
/// масштаб *главного* экрана — один масштаб на всё пространство.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn cursor_to_points(x: f64, y: f64, main_scale: f64) -> (f64, f64) {
    let scale = if main_scale.is_finite() && main_scale > 0.0 {
        main_scale
    } else {
        1.0
    };
    (x / scale, y / scale)
}

#[cfg(target_os = "macos")]
fn cursor_points(app: &AppHandle) -> Option<(f64, f64)> {
    let cursor = app.cursor_position().ok()?;
    let main = app.primary_monitor().ok().flatten()?;
    Some(cursor_to_points(cursor.x, cursor.y, main.scale_factor()))
}

/// Вне macOS пространство сплошное — монитор находится по самому значку.
#[cfg(not(target_os = "macos"))]
fn cursor_points(_app: &AppHandle) -> Option<(f64, f64)> {
    None
}

/// Высота окна под содержимое страницы: в пределах `MIN_HEIGHT..MAX_HEIGHT`
/// и не выше рабочей области (`area` — логическая высота, если известна).
pub fn fit_height(requested: f64, area: Option<f64>) -> f64 {
    let height = if requested.is_finite() {
        requested.clamp(MIN_HEIGHT, MAX_HEIGHT)
    } else {
        DEFAULT_HEIGHT
    };
    match area.filter(|area| area.is_finite() && *area > 2.0 * MARGIN) {
        Some(area) => height.min(area - 2.0 * MARGIN),
        None => height,
    }
}

/// Что сделать со щелчком по значку.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Toggle {
    Show,
    Hide,
    /// Панель только что спряталась от этого же щелчка — ничего.
    Nothing,
}

pub fn on_icon_click(visible: bool, hidden_at: Option<Instant>, now: Instant) -> Toggle {
    if visible {
        Toggle::Hide
    } else if hidden_at.is_some_and(|at| now.saturating_duration_since(at) < REOPEN_GUARD) {
        Toggle::Nothing
    } else {
        Toggle::Show
    }
}

/// Адрес страницы: на macOS под ней системное стекло (`glass=native`), иначе
/// страница рисует фон сама.
pub fn panel_url(native_glass: bool) -> String {
    if native_glass {
        "tray.html?glass=native".to_string()
    } else {
        "tray.html".to_string()
    }
}

#[derive(Debug, Clone, Copy, Serialize)]
struct PanelEvent {
    visible: bool,
}

#[derive(Default)]
struct Inner {
    /// Значок, под которым панель показана последней, и его монитор — его
    /// выбирают при щелчке (`pick_display`), пока курсор над значком.
    icon: Option<Area>,
    display: Option<Display>,
    /// Высота, которую попросила страница.
    height: Option<f64>,
    hidden_at: Option<Instant>,
    /// Окно только что создано: показать, когда страница пришлёт размер.
    pending_show: bool,
}

/// Состояние панели в памяти оболочки (`app.manage`).
#[derive(Default)]
pub struct TrayPanel {
    inner: Mutex<Inner>,
}

impl TrayPanel {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
    }
}

fn panel_window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window(PANEL_LABEL)
}

/// Щелчок левой кнопкой по значку (macOS). Главный поток.
pub fn toggle(app: &AppHandle, rect: &Rect) {
    let Some(state) = app.try_state::<TrayPanel>() else {
        return;
    };
    let visible = panel_window(app)
        .and_then(|window| window.is_visible().ok())
        .unwrap_or(false);
    let hidden_at = state.lock().hidden_at;
    match on_icon_click(visible, hidden_at, Instant::now()) {
        Toggle::Show => show(app, Area::of_rect(rect)),
        Toggle::Hide => hide(app),
        Toggle::Nothing => {}
    }
}

fn show(app: &AppHandle, icon: Area) {
    let displays: Vec<Display> = app
        .available_monitors()
        .map(|monitors| monitors.iter().map(Display::of).collect())
        .unwrap_or_default();
    let display = pick_display(icon, cursor_points(app), &displays);
    let state = app.state::<TrayPanel>();
    {
        let mut inner = state.lock();
        inner.icon = Some(icon);
        inner.display = display;
    }
    let window = match panel_window(app) {
        Some(window) => window,
        None => match build(app) {
            Some(window) => {
                // Страница ещё грузится: покажем, когда она пришлёт свой
                // размер (`tray_panel_fit`), — без прыжка высоты на глазах.
                state.lock().pending_show = true;
                place(app, &window);
                let handle = app.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(SHOW_FALLBACK);
                    let main = handle.clone();
                    let _ = handle.run_on_main_thread(move || reveal_pending(&main));
                });
                return;
            }
            None => return,
        },
    };
    place(app, &window);
    reveal(app, &window);
}

/// Показать отложенную панель, если она всё ещё ждёт страницу.
fn reveal_pending(app: &AppHandle) {
    let pending = std::mem::take(&mut app.state::<TrayPanel>().lock().pending_show);
    if let (true, Some(window)) = (pending, panel_window(app)) {
        reveal(app, &window);
    }
}

fn reveal(app: &AppHandle, window: &WebviewWindow) {
    // Показываем один раз: второй щелчок, пока страница грузилась, уже
    // показал панель — `tray_panel_fit` не покажет её снова.
    app.state::<TrayPanel>().lock().pending_show = false;
    if let Err(error) = window.show().and_then(|()| window.set_focus()) {
        shell_log!("панель записи не показалась: {error}");
        return;
    }
    let _ = app.emit_to(PANEL_LABEL, PANEL_EVENT, PanelEvent { visible: true });
}

/// Спрятать панель. Уже спрятана — ничего.
pub fn hide(app: &AppHandle) {
    let Some(window) = panel_window(app) else {
        return;
    };
    if !window.is_visible().unwrap_or(false) {
        return;
    }
    if let Some(state) = app.try_state::<TrayPanel>() {
        let mut inner = state.lock();
        inner.hidden_at = Some(Instant::now());
        inner.pending_show = false;
    }
    if let Err(error) = window.hide() {
        shell_log!("панель записи не спряталась: {error}");
    }
    let _ = app.emit_to(PANEL_LABEL, PANEL_EVENT, PanelEvent { visible: false });
}

fn build(app: &AppHandle) -> Option<WebviewWindow> {
    let native_glass = cfg!(target_os = "macos");
    let builder = WebviewWindowBuilder::new(
        app,
        PANEL_LABEL,
        WebviewUrl::App(panel_url(native_glass).into()),
    )
    .title("Meet")
    .inner_size(WIDTH, DEFAULT_HEIGHT)
    .decorations(false)
    .resizable(false)
    .maximizable(false)
    .minimizable(false)
    .skip_taskbar(true)
    .always_on_top(true)
    // Панель открывается на том рабочем столе (Space), где щёлкнули, а не
    // перебрасывает туда, где её создали.
    .visible_on_all_workspaces(true)
    .visible(false)
    .focused(true)
    .transparent(true)
    // Тема — странице до её скриптов: кеша оформления может ещё не быть.
    .initialization_script(crate::appearance::theme_hint_script(
        crate::appearance::startup_theme(),
    ));
    #[cfg(target_os = "macos")]
    let builder = {
        use tauri::window::{Effect, EffectState, EffectsBuilder};
        builder
            // Тень системная: у прозрачного окна macOS строит её по форме
            // содержимого — по скруглённому стеклу.
            .shadow(true)
            .effects(
                EffectsBuilder::new()
                    .effect(Effect::Popover)
                    .state(EffectState::Active)
                    .radius(RADIUS)
                    .build(),
            )
    };
    // Windows: тень у окна без рамки — светлая каёмка вокруг скругления
    // (как у панели ассистента); фон и тень рисует страница.
    #[cfg(not(target_os = "macos"))]
    let builder = builder.shadow(false);
    match builder.build() {
        Ok(window) => Some(window),
        Err(error) => {
            shell_log!("панель записи не создалась: {error}");
            None
        }
    }
}

/// Поставить панель под значок с высотой, которую просила страница.
fn place(app: &AppHandle, window: &WebviewWindow) {
    let (icon, display, requested) = {
        let state = app.state::<TrayPanel>();
        let inner = state.lock();
        (inner.icon, inner.display, inner.height)
    };
    let area = display.map(|display| display.work.height / display.scale());
    let height = fit_height(requested.unwrap_or(DEFAULT_HEIGHT), area);
    if let Err(error) = window.set_size(LogicalSize::new(WIDTH, height)) {
        shell_log!("панель записи: размер не задан: {error}");
    }
    match icon.zip(display) {
        Some((icon, display)) => {
            let (x, y) = panel_origin(icon, display, WIDTH, height);
            if let Err(error) = window.set_position(LogicalPosition::new(x, y)) {
                shell_log!("панель записи: место не задано: {error}");
            }
        }
        None => shell_log!("панель записи: монитор значка не найден"),
    }
}

/// Панель потеряла фокус — щелчок мимо: спрятать.
pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if window.label() != PANEL_LABEL {
        return;
    }
    if let WindowEvent::Focused(false) = event {
        hide(window.app_handle());
    }
}

/// Страница измерила себя: подогнать высоту окна (и показать новое окно).
#[tauri::command]
pub fn tray_panel_fit(app: AppHandle, height: f64) {
    let Some(window) = panel_window(&app) else {
        return;
    };
    app.state::<TrayPanel>().lock().height = Some(height);
    place(&app, &window);
    reveal_pending(&app);
}

/// Esc на странице панели.
#[tauri::command]
pub fn tray_panel_hide(app: AppHandle) {
    hide(&app);
}

/// «Открыть запись» и «Открыть Meet»: спрятать панель и открыть главное окно
/// — на записи `recording` или разделе `section` (как пункт меню «Открыть
/// Meet» и щелчок по уведомлению).
#[tauri::command]
pub fn tray_panel_open(app: AppHandle, recording: Option<String>, section: Option<String>) {
    hide(&app);
    // Окно создаётся задачей цикла событий, а не внутри обработчика команды
    // (см. `windows::open_main`): из другого потока `run_on_main_thread`
    // ставит её в очередь.
    std::thread::spawn(move || {
        let handle = app.clone();
        let _ = app.run_on_main_thread(move || {
            if recording.is_none() && section.is_none() {
                crate::tray::open_window(&handle);
            } else {
                crate::windows::open_main(&handle, recording, section.as_deref());
            }
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn area(x: f64, y: f64, width: f64, height: f64) -> Area {
        Area {
            x,
            y,
            width,
            height,
        }
    }

    /// MacBook 1512×982 точек при 2×: строка меню 37 точек, Dock снизу 80.
    fn retina() -> Display {
        Display {
            bounds: area(0.0, 0.0, 3024.0, 1964.0),
            work: area(0.0, 74.0, 3024.0, 1964.0 - 74.0 - 160.0),
            scale: 2.0,
        }
    }

    /// Значок в строке меню: 30×37 точек, левый край — на `x` точках.
    fn icon_at(x: f64) -> Area {
        area(x * 2.0, 0.0, 60.0, 74.0)
    }

    /// Левый верхний угол панели высотой `height`; `cursor` — точки.
    fn origin(
        icon: Area,
        displays: &[Display],
        cursor: Option<(f64, f64)>,
        height: f64,
    ) -> (f64, f64) {
        let display = pick_display(icon, cursor, displays).expect("монитор");
        panel_origin(icon, display, WIDTH, height)
    }

    /// Внешний монитор 1920×1080 при 1× справа от MacBook: его левый край —
    /// 1512 точек × его масштаб 1 = 1512 (а не 3024 физических MacBook).
    /// Своя строка меню — 25 точек.
    fn external() -> Display {
        Display {
            bounds: area(1512.0, 0.0, 1920.0, 1080.0),
            work: area(1512.0, 25.0, 1920.0, 1055.0),
            scale: 1.0,
        }
    }

    #[test]
    fn panel_hangs_centered_under_the_icon() {
        let (x, y) = origin(icon_at(1100.0), &[retina()], None, 280.0);
        // Центр значка 1115 → левый край 1115 − 152.
        assert_eq!(x, 963.0);
        // Под строкой меню (37) с зазором.
        assert_eq!(y, 37.0 + GAP);
    }

    #[test]
    fn icon_near_the_right_edge_keeps_the_panel_on_screen() {
        // Значок у самого края (часы справа сдвинуты) — панель прижата к
        // правому краю с отступом, не торчит за экран.
        let (x, _) = origin(icon_at(1490.0), &[retina()], None, 280.0);
        assert_eq!(x, 1512.0 - MARGIN - WIDTH);
        let (x, _) = origin(icon_at(2.0), &[retina()], None, 280.0);
        assert_eq!(x, MARGIN);
    }

    #[test]
    fn tall_panel_is_pulled_up_but_never_onto_the_menu_bar() {
        // Рабочая область 37…902 точек: 850 ещё помещается под строкой меню
        // с отступом снизу.
        let (_, y) = origin(icon_at(700.0), &[retina()], None, 850.0);
        assert_eq!(y, 37.0 + GAP);
        // 900 не влезает — от верха рабочей области, а не на строке меню.
        let (_, y) = origin(icon_at(700.0), &[retina()], None, 900.0);
        assert_eq!(y, 37.0);
    }

    #[test]
    fn mixed_scale_displays_are_told_apart_by_the_cursor() {
        // Значок в строке меню внешнего монитора, в 1000 точках от его левого
        // края: 2512 точек общего пространства × 1. Те же «физические»
        // 2512 лежат и внутри MacBook (0…3024 при 2×) — по самому значку
        // экран не угадать.
        let icon = area(2512.0, 0.0, 30.0, 25.0);
        let displays = [retina(), external()];
        assert_eq!(pick_display(icon, None, &displays), Some(retina()));
        // Курсор (точки) — над значком: внешний монитор, масштаб 1.
        let cursor = Some((2527.0, 12.0));
        assert_eq!(pick_display(icon, cursor, &displays), Some(external()));
        let (x, y) = origin(icon, &displays, cursor, 280.0);
        assert_eq!(x, 2527.0 - WIDTH / 2.0);
        assert_eq!(y, 25.0 + GAP);
        // Значок на MacBook при том же раскладе — MacBook, масштаб 2.
        let (x, y) = origin(icon_at(1100.0), &displays, Some((1115.0, 18.0)), 280.0);
        assert_eq!((x, y), (963.0, 37.0 + GAP));
    }

    #[test]
    fn cursor_is_converted_with_the_main_display_scale() {
        assert_eq!(cursor_to_points(5054.0, 24.0, 2.0), (2527.0, 12.0));
        assert_eq!(cursor_to_points(100.0, 10.0, f64::NAN), (100.0, 10.0));
        assert_eq!(cursor_to_points(100.0, 10.0, 0.0), (100.0, 10.0));
    }

    #[test]
    fn cursor_off_every_display_falls_back_to_the_icon() {
        let (x, _) = origin(icon_at(1100.0), &[retina()], Some((-500.0, -500.0)), 280.0);
        assert_eq!(x, 963.0);
    }

    #[test]
    fn icon_outside_every_display_goes_to_the_nearest() {
        // Монитор сменился, пока щёлкали: прямоугольник значка чуть выше экрана.
        let icon = area(2200.0, -80.0, 60.0, 74.0);
        let (x, y) = origin(icon, &[retina()], None, 280.0);
        assert_eq!(x, 1115.0 - WIDTH / 2.0);
        assert_eq!(y, 37.0 + GAP);
        assert_eq!(pick_display(icon, None, &[]), None);
    }

    #[test]
    fn icon_at_the_bottom_puts_the_panel_above_it() {
        // Windows: панель задач снизу (1040…1080), значок в ней.
        let screen = Display {
            bounds: area(0.0, 0.0, 1920.0, 1080.0),
            work: area(0.0, 0.0, 1920.0, 1040.0),
            scale: 1.0,
        };
        let icon = area(1700.0, 1044.0, 24.0, 32.0);
        let (x, y) = origin(icon, &[screen], None, 280.0);
        assert_eq!(x, 1712.0 - WIDTH / 2.0);
        assert_eq!(y, 1040.0 - MARGIN - 280.0);
    }

    #[test]
    fn broken_scale_counts_as_one() {
        let display = Display {
            scale: f64::NAN,
            ..retina()
        };
        let (x, _) = origin(area(1000.0, 0.0, 30.0, 30.0), &[display], None, 280.0);
        assert_eq!(x, 1015.0 - WIDTH / 2.0);
    }

    #[test]
    fn height_follows_the_page_within_limits() {
        assert_eq!(fit_height(300.0, Some(865.0)), 300.0);
        assert_eq!(fit_height(40.0, Some(865.0)), MIN_HEIGHT);
        assert_eq!(fit_height(5000.0, Some(865.0)), MAX_HEIGHT);
        // Низкий экран — не выше рабочей области с отступами.
        assert_eq!(fit_height(500.0, Some(400.0)), 400.0 - 2.0 * MARGIN);
        assert_eq!(fit_height(f64::NAN, None), DEFAULT_HEIGHT);
        assert_eq!(fit_height(300.0, Some(f64::NAN)), 300.0);
    }

    #[test]
    fn second_click_closes_and_the_blur_click_does_not_reopen() {
        let now = Instant::now();
        assert_eq!(on_icon_click(false, None, now), Toggle::Show);
        assert_eq!(on_icon_click(true, None, now), Toggle::Hide);
        // Щелчок по значку снял с панели фокус (она спряталась), и следом
        // пришёл сам щелчок — панель остаётся закрытой.
        let blurred = now - Duration::from_millis(80);
        assert_eq!(on_icon_click(false, Some(blurred), now), Toggle::Nothing);
        // Давно спрятана — открыть.
        let long_ago = now - Duration::from_secs(5);
        assert_eq!(on_icon_click(false, Some(long_ago), now), Toggle::Show);
    }

    #[test]
    fn page_url_asks_for_native_glass_only_on_mac() {
        assert_eq!(panel_url(true), "tray.html?glass=native");
        assert_eq!(panel_url(false), "tray.html");
    }
}
