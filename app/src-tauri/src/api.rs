// Синхронный клиент control API резидента.
//
// Вызывается из рабочих потоков оболочки (надзор, опрос трея, «Выход»), поэтому
// блокирующий ureq, а не async: рантайм ради пары запросов в секунду не нужен.
// Токен уходит только заголовком Authorization и нигде не печатается — у
// клиента нарочно нет Debug.

use std::fmt;
use std::time::Duration;

use serde_json::Value;

use crate::resident::Endpoint;

/// Обычный запрос: резидент на той же машине отвечает мгновенно, а трей не
/// должен подвисать, если он занят или умер.
const TIMEOUT: Duration = Duration::from_secs(3);
/// `/shutdown`, `/recording/stop` и `/recording/cancel` отвечают только после
/// того, как резидент дождётся потока записи (до 60 с): короткий таймаут дал бы
/// ложную ошибку при медленной, но успешной остановке.
const LONG_TIMEOUT: Duration = Duration::from_secs(70);
/// `/live/start` и `/live/attach`: резидент ждёт, пока у записи откроются
/// устройства (до 10 с, `tray_control.LIVE_RECORD_WAIT_S`), и обрывает хвост
/// прошлого ассистента (до 5 + 5 с) — в худшем случае около 22 с.
const LIVE_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug)]
pub enum Error {
    /// Резидент ответил ошибкой; `message` — поле `error` из его ответа.
    Status { code: u16, message: String },
    /// Не достучались: резидента нет, он перезапускается или не успел ответить.
    Transport(String),
    /// Ответ пришёл, но это не JSON.
    Decode(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Status { message, .. } => f.write_str(message),
            Error::Transport(detail) => write!(f, "резидент не отвечает: {detail}"),
            Error::Decode(detail) => write!(f, "непонятный ответ резидента: {detail}"),
        }
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

pub struct Client {
    base: String,
    token: String,
    agent: ureq::Agent,
}

impl Client {
    pub fn new(ep: &Endpoint) -> Self {
        Client {
            base: format!("http://127.0.0.1:{}", ep.port),
            token: ep.token.clone(),
            agent: ureq::AgentBuilder::new().build(),
        }
    }

    pub fn get_state(&self) -> Result<Value> {
        self.get("/state")
    }

    pub fn get_jobs(&self) -> Result<Value> {
        self.get("/jobs")
    }

    pub fn get(&self, path: &str) -> Result<Value> {
        let request = self.request("GET", path);
        read(request.call())
    }

    pub fn delete(&self, path: &str) -> Result<Value> {
        read(self.request("DELETE", path).call())
    }

    /// POST с JSON-телом (`Value::Null` — без тела).
    pub fn post(&self, path: &str, body: Value) -> Result<Value> {
        let request = self.request("POST", path);
        if body.is_null() {
            read(request.call())
        } else {
            read(request.send_json(body))
        }
    }

    fn request(&self, method: &str, path: &str) -> ureq::Request {
        self.agent
            .request(method, &format!("{}{}", self.base, path))
            .timeout(timeout_for(path))
            .set("Authorization", &format!("Bearer {}", self.token))
    }
}

fn timeout_for(path: &str) -> Duration {
    match path {
        "/shutdown" | "/recording/stop" | "/recording/cancel" => LONG_TIMEOUT,
        // Запись с ассистентом и «Включить ассистента»: см. LIVE_TIMEOUT.
        "/live/start" | "/live/attach" => LIVE_TIMEOUT,
        _ => TIMEOUT,
    }
}

fn read(result: std::result::Result<ureq::Response, ureq::Error>) -> Result<Value> {
    match result {
        Ok(response) => {
            let text = response
                .into_string()
                .map_err(|e| Error::Transport(e.to_string()))?;
            parse_body(&text)
        }
        Err(ureq::Error::Status(code, response)) => {
            let text = response.into_string().unwrap_or_default();
            Err(Error::Status {
                code,
                message: error_message(code, &text),
            })
        }
        Err(ureq::Error::Transport(transport)) => Err(Error::Transport(transport.to_string())),
    }
}

fn parse_body(text: &str) -> Result<Value> {
    if text.trim().is_empty() {
        return Ok(Value::Object(Default::default()));
    }
    serde_json::from_str(text).map_err(|e| Error::Decode(e.to_string()))
}

/// Текст ошибки для человека: резидент кладёт его в поле `error`.
fn error_message(code: u16, body: &str) -> String {
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|v| v.get("error").and_then(Value::as_str).map(str::to_string))
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| format!("резидент ответил {code}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpListener;
    use std::thread;

    /// Однозапросный сервер: отдаёт `reply` и возвращает увиденный запрос.
    fn serve_once(status: &str, reply: &str) -> (u16, thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let status = status.to_string();
        let reply = reply.to_string();
        let handle = thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut head = String::new();
            let mut length = 0usize;
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                    length = v.trim().parse().unwrap();
                }
                if line == "\r\n" {
                    break;
                }
                head.push_str(&line);
            }
            let mut body = vec![0u8; length];
            reader.read_exact(&mut body).unwrap();
            head.push_str(&String::from_utf8(body).unwrap());
            let mut stream = stream;
            write!(
                stream,
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}",
                reply.len()
            )
            .unwrap();
            head
        });
        (port, handle)
    }

    fn client(port: u16) -> Client {
        Client::new(&Endpoint {
            port,
            token: "secret-token".into(),
        })
    }

    #[test]
    fn sends_bearer_token_and_parses_json() {
        let (port, server) = serve_once("200 OK", r#"{"status":"idle"}"#);
        let state = client(port).get_state().unwrap();
        assert_eq!(state["status"], "idle");
        let seen = server.join().unwrap();
        assert!(seen.starts_with("GET /state "), "{seen}");
        assert!(
            seen.contains("Authorization: Bearer secret-token"),
            "{seen}"
        );
    }

    #[test]
    fn post_sends_json_body() {
        let (port, server) = serve_once("200 OK", r#"{"ok":true}"#);
        let reply = client(port)
            .post("/auto-record", serde_json::json!({"enabled": false}))
            .unwrap();
        assert_eq!(reply["ok"], true);
        let seen = server.join().unwrap();
        assert!(seen.starts_with("POST /auto-record "), "{seen}");
        assert!(seen.ends_with(r#"{"enabled":false}"#), "{seen}");
    }

    #[test]
    fn error_status_carries_resident_message() {
        let (port, server) = serve_once("400 Bad Request", r#"{"error":"файла нет"}"#);
        let error = client(port)
            .post("/recordings/import", serde_json::json!({"path": "x"}))
            .unwrap_err();
        server.join().unwrap();
        match error {
            Error::Status { code, ref message } => {
                assert_eq!(code, 400);
                assert_eq!(message, "файла нет");
            }
            other => panic!("ожидали Status, получили {other:?}"),
        }
        assert_eq!(error.to_string(), "файла нет");
    }

    #[test]
    fn error_without_body_falls_back_to_code() {
        assert_eq!(error_message(502, ""), "резидент ответил 502");
    }

    #[test]
    fn shutdown_waits_longer_than_other_calls() {
        assert_eq!(timeout_for("/shutdown"), Duration::from_secs(70));
        assert_eq!(timeout_for("/state"), Duration::from_secs(3));
    }

    #[test]
    fn stop_and_cancel_wait_for_the_recorder() {
        // Резидент ждёт поток записи до 60 с, прежде чем ответить.
        assert_eq!(timeout_for("/recording/stop"), Duration::from_secs(70));
        assert_eq!(timeout_for("/recording/cancel"), Duration::from_secs(70));
        assert_eq!(timeout_for("/recording/start"), Duration::from_secs(3));
    }

    #[test]
    fn live_start_and_attach_outlast_the_resident_waits() {
        // Резидент ждёт устройства записи (до 10 с) и обрывает хвост прошлого
        // ассистента (до 5 + 5 с): вместе с проверкой провайдера — около 22 с.
        assert_eq!(timeout_for("/live/start"), Duration::from_secs(30));
        assert_eq!(timeout_for("/live/attach"), Duration::from_secs(30));
    }
}
