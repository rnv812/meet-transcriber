// Крестик главного окна и несохранённые настройки.
//
// Решает оболочка, а не страница: несохранённого нет (`tray::settings_dirty`)
// — окно закрывается как обычно, без вопроса и без участия страницы. Есть —
// закрытие придерживается, странице уходит `settings-close-guard`, и она
// показывает «Сохранить / Не сохранять / Остаться». Окно закрывается в любом
// случае, если страница не подтвердила вопрос за ACK_TIMEOUT (зависла,
// перезагружается, сломалась) или крестик нажат второй раз, пока вопрос
// открыт: закрыть окно можно всегда.
//
// IMPORTANT: JS-обработчик `onCloseRequested` здесь не используется
// намеренно: с ним Tauri придерживает каждое закрытие и ждёт страницу.

use std::sync::{Mutex, MutexGuard};
use std::thread;
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager, Window, WindowEvent};

use crate::tray;

pub const GUARD_EVENT: &str = "settings-close-guard";
/// Сколько ждать, что страница показала вопрос.
pub const ACK_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloseStep {
    /// Закрыть окно как обычно.
    Close,
    /// Придержать закрытие и спросить страницу.
    Ask,
}

/// Состояние вопроса: идёт ли он, подтвердила ли его страница, номер вопроса
/// (сторож старого вопроса не закрывает окно по новому).
#[derive(Debug, Default)]
pub struct CloseGuard {
    asking: bool,
    acked: bool,
    round: u64,
}

impl CloseGuard {
    pub const fn new() -> Self {
        CloseGuard {
            asking: false,
            acked: false,
            round: 0,
        }
    }

    /// Крестик нажат. → что делать и номер вопроса для сторожа.
    pub fn request(&mut self, dirty: bool) -> (CloseStep, u64) {
        if !dirty || self.asking {
            // Нечего терять — или это второй крестик при открытом вопросе.
            self.reset();
            return (CloseStep::Close, self.round);
        }
        self.asking = true;
        self.acked = false;
        self.round += 1;
        (CloseStep::Ask, self.round)
    }

    /// Страница показала вопрос.
    pub fn ack(&mut self) {
        if self.asking {
            self.acked = true;
        }
    }

    /// Срок вышел: закрыть, если страница так и не показала этот вопрос.
    pub fn timed_out(&mut self, round: u64) -> bool {
        let close = self.asking && !self.acked && self.round == round;
        if close {
            self.reset();
        }
        close
    }

    /// Ответ получен («Остаться», «Не сохранять», «Сохранить») или окно ушло.
    pub fn reset(&mut self) {
        self.asking = false;
        self.acked = false;
    }
}

static GUARD: Mutex<CloseGuard> = Mutex::new(CloseGuard::new());

fn guard() -> MutexGuard<'static, CloseGuard> {
    GUARD
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn destroy_main(app: &AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(window) = handle.get_webview_window("main") {
            let _ = window.destroy();
        }
    });
}

/// Ушли без вопроса: окно уничтожено или страница загружается заново —
/// её черновик настроек пропал вместе с ней.
fn forget() {
    guard().reset();
    tray::reset_settings_dirty();
}

pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if window.label() != "main" {
        return;
    }
    match event {
        WindowEvent::CloseRequested { api, .. } => {
            let (step, round) = guard().request(tray::settings_dirty());
            if step == CloseStep::Close {
                return;
            }
            api.prevent_close();
            let app = window.app_handle().clone();
            let _ = app.emit_to("main", GUARD_EVENT, ());
            thread::spawn(move || {
                thread::sleep(ACK_TIMEOUT);
                if guard().timed_out(round) {
                    tray::reset_settings_dirty();
                    destroy_main(&app);
                }
            });
        }
        WindowEvent::Destroyed => forget(),
        _ => {}
    }
}

/// Страница главного окна начала загружаться (перезагрузка, переход).
pub fn on_page_started() {
    forget();
}

/// Команда пришла из главного окна: вопрос о закрытии — его, панель живого
/// режима (тоже наша страница) им не управляет.
pub fn from_main(window: &Window) -> bool {
    window.label() == "main"
}

/// Страница показала вопрос — сторож больше не закрывает окно сам.
#[tauri::command]
pub fn settings_close_ack(window: Window) {
    if from_main(&window) {
        guard().ack();
    }
}

/// «Остаться»: окно остаётся, следующий крестик снова спросит.
#[tauri::command]
pub fn settings_close_stay(window: Window) {
    if from_main(&window) {
        guard().reset();
    }
}

/// «Не сохранять» или «Сохранить» (сохранилось): закрыть окно.
#[tauri::command]
pub fn settings_close_go(app: AppHandle, window: Window) {
    if !from_main(&window) {
        return;
    }
    forget();
    destroy_main(&app);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nothing_unsaved_closes_at_once() {
        let mut g = CloseGuard::new();
        assert_eq!(g.request(false).0, CloseStep::Close);
    }

    #[test]
    fn unsaved_asks_and_a_second_click_closes_anyway() {
        let mut g = CloseGuard::new();
        let (step, round) = g.request(true);
        assert_eq!(step, CloseStep::Ask);
        g.ack();
        assert!(!g.timed_out(round)); // вопрос на экране — ждём человека
        assert_eq!(g.request(true).0, CloseStep::Close); // второй крестик
    }

    #[test]
    fn a_silent_page_does_not_keep_the_window_open() {
        let mut g = CloseGuard::new();
        let (_, round) = g.request(true);
        assert!(g.timed_out(round)); // страница не ответила за ACK_TIMEOUT
        assert!(!g.timed_out(round)); // и только один раз
        assert_eq!(g.request(true).0, CloseStep::Ask); // следующий крестик — снова вопрос
    }

    #[test]
    fn stay_resets_and_an_old_watchdog_does_not_close_a_new_question() {
        let mut g = CloseGuard::new();
        let (_, first) = g.request(true);
        g.ack();
        g.reset(); // «Остаться»
        let (step, second) = g.request(true);
        assert_eq!(step, CloseStep::Ask);
        assert!(!g.timed_out(first)); // сторож первого вопроса
        assert!(g.timed_out(second));
    }

    #[test]
    fn ack_without_a_question_does_nothing() {
        let mut g = CloseGuard::new();
        g.ack();
        let (_, round) = g.request(true);
        assert!(g.timed_out(round));
    }
}
