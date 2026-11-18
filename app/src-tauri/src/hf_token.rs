// Токен Hugging Face на macOS: хранит оболочка, в связке ключей от имени Meet.app.
//
// Связка ключей привязывает доступ к записи к программе, которая её создала.
// Meet.app подписан постоянным сертификатом — доступ переживает обновления; а
// Python движка свой в каждой версии, и чтение из него открывало бы системное
// окно «python хочет доступ…», пока задача ждёт ответа. Поэтому: оболочка
// читает токен при запуске резидента и передаёт переменной `ENV`
// (`meet.credentials.SHELL_ENV`); окно сохраняет и удаляет токен командами
// отсюда — проверку на huggingface.co по-прежнему делает резидент
// (`POST /hf/token {"store": "shell"}`), а записывает оболочка.
//
// На Windows ничего из этого не работает: токен там в диспетчере учётных
// данных через keyring резидента, окно зовёт резидент напрямую.
//
// IMPORTANT: значение токена не попадает ни в журнал, ни в текст ошибки.

use std::process::Command;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use serde_json::{json, Value};

use crate::api;
use crate::logs::shell_log;
use crate::resident;

/// Переменная, которой оболочка передаёт токен резиденту.
pub const ENV: &str = "MEET_SHELL_HF_TOKEN";
/// Запись в связке ключей: служба — id приложения. Не `meet`, как у keyring
/// Python: та запись принадлежит прежнему python и спрашивала бы снова.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const SERVICE: &str = "com.meet.desktop";
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const ACCOUNT: &str = "huggingface";
/// Сколько ждать связку ключей при запуске резидента: заблокированная
/// связка спрашивает пароль, а резидент без токена лучше, чем без резидента.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const LAUNCH_WAIT: Duration = Duration::from_secs(5);

/// Где лежит токен (macOS — связка ключей; в тестах — заглушка). Ошибки —
/// текстом без значения токена.
pub trait Store {
    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    fn get(&self) -> Result<Option<String>, String>;
    fn set(&self, token: &str) -> Result<(), String>;
    fn delete(&self) -> Result<(), String>;
}

/// Control API резидента: то, что нужно токену.
pub trait Resident {
    fn post(&self, path: &str, body: Value) -> Result<Value, String>;
    fn delete(&self, path: &str) -> Result<Value, String>;
}

impl Resident for api::Client {
    fn post(&self, path: &str, body: Value) -> Result<Value, String> {
        api::Client::post(self, path, body).map_err(|error| error.to_string())
    }

    fn delete(&self, path: &str) -> Result<Value, String> {
        api::Client::delete(self, path).map_err(|error| error.to_string())
    }
}

/// Токен для запуска резидента: прочитать не дольше `wait`. Нет записи,
/// пустая, ошибка или связка не ответила — `None` (резидент возьмёт запасной
/// путь сам).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn launch_value<F>(read: F, wait: Duration) -> Option<String>
where
    F: FnOnce() -> Result<Option<String>, String> + Send + 'static,
{
    let (sender, receiver) = mpsc::channel();
    // Поток, который не дождались, кончится сам, когда связка ответит.
    thread::spawn(move || {
        let _ = sender.send(read());
    });
    match receiver.recv_timeout(wait) {
        Ok(Ok(value)) => value
            .map(|token| token.trim().to_string())
            .filter(|token| !token.is_empty()),
        Ok(Err(error)) => {
            shell_log!("токен HF из связки ключей не прочитан: {error}");
            None
        }
        Err(_) => {
            shell_log!(
                "связка ключей не ответила за {} мс — резидент без токена от оболочки",
                wait.as_millis()
            );
            None
        }
    }
}

/// Переменная токена в команде запуска резидента: своя или никакой —
/// унаследованная от окружения оболочки за токен «от оболочки» не сойдёт.
pub fn set_launch_env(command: &mut Command, token: Option<&str>) {
    command.env_remove(ENV);
    if let Some(token) = token {
        command.env(ENV, token);
    }
}

/// Токен для запуска резидента: на macOS — из связки ключей, иначе нет.
pub fn for_launch() -> Option<String> {
    #[cfg(target_os = "macos")]
    {
        launch_value(|| Keychain.get(), LAUNCH_WAIT)
    }
    #[cfg(not(target_os = "macos"))]
    {
        None
    }
}

/// Проверить токен у резидента и, если доступ есть, сохранить в `store`.
/// Ответ — результат проверки резидента (`HfCheck` окна). Связка ключей не
/// записала — токен сохраняет резидент своим путём (keyring или
/// `config.json`), чтобы он не пропал после перезапуска.
pub fn save(token: &str, store: &dyn Store, resident: &dyn Resident) -> Result<Value, String> {
    let token = token.trim();
    if token.is_empty() {
        return Err("пустой токен".into());
    }
    let check = resident.post("/hf/token", json!({"token": token, "store": "shell"}))?;
    if check.get("ok").and_then(Value::as_bool) != Some(true) {
        return Ok(check);
    }
    match store.set(token) {
        Ok(()) => {
            shell_log!("токен HF сохранён в связке ключей");
            Ok(check)
        }
        Err(error) => {
            shell_log!("связка ключей не сохранила токен HF ({error}) — сохраняет резидент");
            resident.post("/hf/token", json!({"token": token}))
        }
    }
}

/// Забыть токен: в связке ключей и у резидента. Ответ — `HfStatus` резидента.
pub fn delete(store: &dyn Store, resident: &dyn Resident) -> Result<Value, String> {
    let stored = store.delete();
    let status = resident.delete("/hf/token")?;
    match stored {
        Ok(()) => {
            shell_log!("токен HF удалён из связки ключей");
            Ok(status)
        }
        Err(error) => {
            shell_log!("токен HF не удалён из связки ключей: {error}");
            Err(format!("токен не удалён из связки ключей: {error}"))
        }
    }
}

/// Связка ключей macOS: запись принадлежит Meet.app (постоянная подпись).
#[cfg(target_os = "macos")]
struct Keychain;

#[cfg(target_os = "macos")]
impl Store for Keychain {
    fn get(&self) -> Result<Option<String>, String> {
        match security_framework::passwords::get_generic_password(SERVICE, ACCOUNT) {
            Ok(bytes) => Ok(String::from_utf8(bytes).ok()),
            Err(error) if error.code() == ERR_SEC_ITEM_NOT_FOUND => Ok(None),
            Err(error) => Err(error.to_string()),
        }
    }

    fn set(&self, token: &str) -> Result<(), String> {
        security_framework::passwords::set_generic_password(SERVICE, ACCOUNT, token.as_bytes())
            .map_err(|error| error.to_string())
    }

    fn delete(&self) -> Result<(), String> {
        match security_framework::passwords::delete_generic_password(SERVICE, ACCOUNT) {
            Ok(()) => Ok(()),
            Err(error) if error.code() == ERR_SEC_ITEM_NOT_FOUND => Ok(()),
            Err(error) => Err(error.to_string()),
        }
    }
}

/// `errSecItemNotFound`: записи нет — не ошибка.
#[cfg(target_os = "macos")]
const ERR_SEC_ITEM_NOT_FOUND: i32 = -25300;

#[cfg(target_os = "macos")]
fn store() -> Result<Box<dyn Store>, String> {
    Ok(Box::new(Keychain))
}

#[cfg(not(target_os = "macos"))]
fn store() -> Result<Box<dyn Store>, String> {
    Err("токен Hugging Face в оболочке хранится только на macOS".into())
}

fn client() -> Result<api::Client, String> {
    resident::read_endpoint()
        .map(|endpoint| api::Client::new(&endpoint))
        .ok_or_else(|| "служба записи не запущена".to_string())
}

/// Окно на macOS: «Проверить и сохранить» токен. Ответ — проверка резидента.
#[tauri::command]
pub async fn hf_token_save(token: String) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || save(&token, store()?.as_ref(), &client()?))
        .await
        .map_err(|error| error.to_string())?
}

/// Окно на macOS: «Удалить» токен. Ответ — `HfStatus` резидента.
#[tauri::command]
pub async fn hf_token_delete() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || delete(store()?.as_ref(), &client()?))
        .await
        .map_err(|error| error.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::time::Instant;

    const TOKEN: &str = "hf_RUST_secret_000";

    #[derive(Default)]
    struct FakeStore {
        value: RefCell<Option<String>>,
        fail: bool,
        calls: RefCell<Vec<&'static str>>,
    }

    impl Store for FakeStore {
        fn get(&self) -> Result<Option<String>, String> {
            self.calls.borrow_mut().push("get");
            Ok(self.value.borrow().clone())
        }
        fn set(&self, token: &str) -> Result<(), String> {
            self.calls.borrow_mut().push("set");
            if self.fail {
                return Err("связка ключей заблокирована".into());
            }
            *self.value.borrow_mut() = Some(token.to_string());
            Ok(())
        }
        fn delete(&self) -> Result<(), String> {
            self.calls.borrow_mut().push("delete");
            if self.fail {
                return Err("связка ключей заблокирована".into());
            }
            *self.value.borrow_mut() = None;
            Ok(())
        }
    }

    struct FakeResident {
        check: Value,
        down: bool,
        calls: RefCell<Vec<(String, String, Value)>>,
    }

    impl FakeResident {
        fn answering(check: Value) -> Self {
            FakeResident {
                check,
                down: false,
                calls: RefCell::new(Vec::new()),
            }
        }
    }

    impl Resident for FakeResident {
        fn post(&self, path: &str, body: Value) -> Result<Value, String> {
            self.calls
                .borrow_mut()
                .push(("POST".into(), path.into(), body));
            if self.down {
                return Err("резидент не отвечает".into());
            }
            Ok(self.check.clone())
        }
        fn delete(&self, path: &str) -> Result<Value, String> {
            self.calls
                .borrow_mut()
                .push(("DELETE".into(), path.into(), Value::Null));
            if self.down {
                return Err("резидент не отвечает".into());
            }
            Ok(json!({"configured": false, "source": null, "check": null}))
        }
    }

    fn ok() -> Value {
        json!({"ok": true, "reason": "ok", "message": "Доступ есть"})
    }

    #[test]
    fn launch_value_trims_and_skips_empty_or_failed() {
        let wait = Duration::from_secs(1);
        assert_eq!(
            launch_value(|| Ok(Some(format!(" {TOKEN}\n"))), wait).as_deref(),
            Some(TOKEN)
        );
        assert_eq!(launch_value(|| Ok(Some("  ".into())), wait), None);
        assert_eq!(launch_value(|| Ok(None), wait), None);
        assert_eq!(launch_value(|| Err("нет доступа".into()), wait), None);
    }

    #[test]
    fn launch_value_does_not_wait_for_a_stuck_keychain() {
        let started = Instant::now();
        let value = launch_value(
            || {
                thread::sleep(Duration::from_secs(5));
                Ok(Some(TOKEN.into()))
            },
            Duration::from_millis(100),
        );
        assert_eq!(value, None);
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn launch_env_is_ours_or_none() {
        let envs = |command: &Command| {
            command
                .get_envs()
                .filter(|(key, _)| *key == ENV)
                .map(|(_, value)| value.map(|v| v.to_string_lossy().into_owned()))
                .collect::<Vec<_>>()
        };
        let mut command = Command::new("x");
        set_launch_env(&mut command, Some(TOKEN));
        assert_eq!(envs(&command), vec![Some(TOKEN.to_string())]);
        let mut command = Command::new("x");
        set_launch_env(&mut command, None);
        // Снята явно: переменная из окружения самой оболочки не наследуется.
        assert_eq!(envs(&command), vec![None]);
    }

    #[test]
    fn save_checks_first_then_stores_in_keychain() {
        let store = FakeStore::default();
        let resident = FakeResident::answering(ok());
        assert_eq!(save(&format!("  {TOKEN} "), &store, &resident), Ok(ok()));
        assert_eq!(store.value.borrow().as_deref(), Some(TOKEN));
        let calls = resident.calls.borrow();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].1, "/hf/token");
        assert_eq!(calls[0].2, json!({"token": TOKEN, "store": "shell"}));
    }

    #[test]
    fn failed_check_leaves_keychain_alone() {
        let store = FakeStore::default();
        let bad = json!({"ok": false, "reason": "invalid_token", "message": "Неверный токен"});
        let resident = FakeResident::answering(bad.clone());
        assert_eq!(save(TOKEN, &store, &resident), Ok(bad));
        assert!(store.calls.borrow().is_empty());
    }

    #[test]
    fn empty_token_goes_nowhere() {
        let store = FakeStore::default();
        let resident = FakeResident::answering(ok());
        assert!(save("  ", &store, &resident).is_err());
        assert!(resident.calls.borrow().is_empty());
        assert!(store.calls.borrow().is_empty());
    }

    #[test]
    fn resident_down_is_an_error_and_nothing_is_stored() {
        let store = FakeStore::default();
        let mut resident = FakeResident::answering(ok());
        resident.down = true;
        let error = save(TOKEN, &store, &resident).unwrap_err();
        assert!(!error.contains(TOKEN));
        assert!(store.calls.borrow().is_empty());
    }

    #[test]
    fn keychain_refusal_falls_back_to_the_resident_store() {
        let store = FakeStore {
            fail: true,
            ..Default::default()
        };
        let resident = FakeResident::answering(ok());
        assert_eq!(save(TOKEN, &store, &resident), Ok(ok()));
        let calls = resident.calls.borrow();
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[1].2, json!({"token": TOKEN}));
    }

    #[test]
    fn delete_clears_keychain_and_resident() {
        let store = FakeStore {
            value: RefCell::new(Some(TOKEN.into())),
            ..Default::default()
        };
        let resident = FakeResident::answering(ok());
        let status = delete(&store, &resident).unwrap();
        assert_eq!(status["configured"], json!(false));
        assert_eq!(*store.value.borrow(), None);
        assert_eq!(resident.calls.borrow()[0].0, "DELETE");
    }

    #[test]
    fn delete_reports_keychain_refusal_but_still_clears_resident() {
        let store = FakeStore {
            fail: true,
            ..Default::default()
        };
        let resident = FakeResident::answering(ok());
        assert!(delete(&store, &resident).is_err());
        assert_eq!(resident.calls.borrow().len(), 1);
    }
}
