// Значок окон Windows — из ресурса exe, кадр нужного размера.
//
// Окнам Tauri значок даёт generate_context!: tauri-codegen 2.6 берёт из
// icons/icon.ico первый кадр, а он 16×16. Для заголовка окна этого хватает, но
// Alt+Tab и панель задач (там, где Windows берёт значок у окна) растягивают его
// до 32 px и больше — кольцо выходит мутным. В exe тот же icon.ico лежит
// целиком (tauri-build, ресурс 32512), и LoadImageW сам выбирает кадр под
// размер: большой значок — SM_CXICON (32 px при 100 %, 48 при 150 %),
// маленький — SM_CXSMICON. Отданный кадр — нарисованный под этот размер, а не
// уменьшенный (scripts/make_app_icons.py).

use tauri::WebviewWindow;

/// Ресурс значка приложения в exe (`set_icon_with_id(…, "32512")` в tauri-build).
#[cfg(windows)]
const APP_ICON_RESOURCE: u16 = 32512;

/// Размер значка по метрике Windows; 0 — метрика недоступна.
#[cfg(any(windows, test))]
pub fn icon_size(metric: i32, fallback: i32) -> i32 {
    if metric > 0 {
        metric
    } else {
        fallback
    }
}

/// Поставить окну большой и маленький значки из ресурса exe. Без ресурса
/// (`cargo test`, сборка без иконки) остаётся значок Tauri.
#[cfg(windows)]
pub fn apply(window: &WebviewWindow) {
    use std::sync::OnceLock;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        SendMessageW, ICON_BIG, ICON_SMALL, SM_CXICON, SM_CXSMICON, SM_CYICON, SM_CYSMICON,
        WM_SETICON,
    };

    // HICON — на всё время жизни процесса: окна открываются заново, а
    // загруженный значок у всех один.
    static ICONS: OnceLock<(usize, usize)> = OnceLock::new();
    let (big, small) = *ICONS.get_or_init(|| {
        (
            load(SM_CXICON, SM_CYICON, 32),
            load(SM_CXSMICON, SM_CYSMICON, 16),
        )
    });
    let Ok(hwnd) = window.hwnd() else {
        return;
    };
    for (kind, icon) in [(ICON_BIG, big), (ICON_SMALL, small)] {
        if icon != 0 {
            // SAFETY: hwnd — живое окно этого процесса; WM_SETICON принимает
            // HICON в lParam и указатели не разыменовывает.
            unsafe { SendMessageW(hwnd.0, WM_SETICON, kind as usize, icon as isize) };
        }
    }
}

#[cfg(not(windows))]
pub fn apply(_window: &WebviewWindow) {}

/// HICON нужного размера из ресурса exe; 0 — ресурса нет.
#[cfg(windows)]
fn load(
    cx: windows_sys::Win32::UI::WindowsAndMessaging::SYSTEM_METRICS_INDEX,
    cy: windows_sys::Win32::UI::WindowsAndMessaging::SYSTEM_METRICS_INDEX,
    fallback: i32,
) -> usize {
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, LoadImageW, IMAGE_ICON, LR_DEFAULTCOLOR,
    };
    // SAFETY: GetSystemMetrics не принимает указателей; ошибка — 0.
    let (width, height) = unsafe {
        (
            icon_size(GetSystemMetrics(cx), fallback),
            icon_size(GetSystemMetrics(cy), fallback),
        )
    };
    // SAFETY: null — модуль самого exe; имя ресурса — MAKEINTRESOURCEW(id),
    // то есть число в младшем слове указателя, LoadImageW его не читает как
    // строку. Ошибка — null.
    unsafe {
        let module = GetModuleHandleW(std::ptr::null());
        let name = APP_ICON_RESOURCE as usize as *const u16;
        LoadImageW(module, name, IMAGE_ICON, width, height, LR_DEFAULTCOLOR) as usize
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn icon_size_follows_the_metric() {
        assert_eq!(icon_size(32, 32), 32); // 100 %
        assert_eq!(icon_size(48, 32), 48); // 150 %
        assert_eq!(icon_size(20, 16), 20); // маленький при 125 %
        assert_eq!(icon_size(0, 32), 32); // метрики нет
        assert_eq!(icon_size(-1, 16), 16);
    }
}
