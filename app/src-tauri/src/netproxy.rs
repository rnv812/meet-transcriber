// Системный прокси Windows для загрузок движка (uv) и агентов вкладки
// «Агент» (`pty.rs`: там же режим `llm.proxy`).
//
// uv, как и Claude Code с Codex, надёжно понимает только переменные
// HTTPS_PROXY/HTTP_PROXY. Оболочка, запущенная из Проводника, их обычно не
// имеет, а доступ в сеть у человека может идти через прокси VPN-клиента,
// прописанный в настройках Windows (WinINET). Тогда передаём его uv
// переменными — так же, как резидент передаёт его моделям
// (`meet.netproxy` в Python). Сценарий автонастройки (PAC) не поддерживается.

use std::ffi::OsString;

/// Локальные адреса в NO_PROXY — всегда.
const LOOPBACK: [&str; 3] = ["localhost", "127.0.0.1", "::1"];
const SCHEMES: [&str; 4] = ["http", "https", "socks5", "socks5h"];

/// Значения прокси из `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct InternetSettings {
    pub enabled: Option<u32>,
    pub server: Option<String>,
    pub overrides: Option<String>,
}

/// `scheme://[user:pass@]host:port[/]` с известной схемой и числовым портом.
pub fn valid_url(url: &str) -> bool {
    let Some((scheme, rest)) = url.split_once("://") else {
        return false;
    };
    if !SCHEMES.contains(&scheme.to_ascii_lowercase().as_str()) {
        return false;
    }
    let rest = rest.strip_suffix('/').unwrap_or(rest);
    if rest.contains(['/', '?', '#']) {
        return false;
    }
    let host_port = rest.rsplit_once('@').map_or(rest, |(_, hp)| hp);
    let Some((host, port)) = host_port.rsplit_once(':') else {
        return false;
    };
    !host.is_empty()
        && !port.is_empty()
        && port.parse::<u16>().is_ok()
        && (host.starts_with('[') || !host.contains(':'))
}

/// `host:port` или `http=h:p;https=h:p;socks=h:p` → адрес со схемой. Из
/// списка по протоколам — https, иначе http; SOCKS-только не берём.
pub fn pick_server(raw: &str) -> Option<String> {
    let raw = raw.trim();
    let entry = if raw.contains('=') {
        let mut http = None;
        let mut https = None;
        for part in raw.split(';') {
            if let Some((proto, address)) = part.split_once('=') {
                let address = address.trim();
                match proto.trim().to_ascii_lowercase().as_str() {
                    "https" if !address.is_empty() => https = Some(address),
                    "http" if !address.is_empty() => http = Some(address),
                    _ => {}
                }
            }
        }
        https.or(http)?
    } else {
        raw.split(';').map(str::trim).find(|p| !p.is_empty())?
    };
    let url = if entry.contains("://") {
        entry.to_string()
    } else {
        format!("http://{entry}")
    };
    let url = url.strip_suffix('/').unwrap_or(&url).to_string();
    valid_url(&url).then_some(url)
}

/// NO_PROXY для uv: унаследованный NO_PROXY (не затираем), локальные адреса
/// всегда и ProxyOverride (`<local>` сводится к локальным, `*.domain` →
/// `.domain`, прочие маски вроде `10.*` опускаются). Без повторов, регистр
/// не важен.
pub fn no_proxy(inherited: Option<&str>, overrides: Option<&str>) -> String {
    let mut items: Vec<String> = Vec::new();
    let push = |items: &mut Vec<String>, entry: &str| {
        if !items.iter().any(|i| i.eq_ignore_ascii_case(entry)) {
            items.push(entry.to_string());
        }
    };
    for entry in inherited.unwrap_or("").split(',').map(str::trim) {
        if !entry.is_empty() {
            push(&mut items, entry);
        }
    }
    for entry in LOOPBACK {
        push(&mut items, entry);
    }
    for part in overrides.unwrap_or("").split([';', ',']) {
        let entry = part.trim();
        if entry.is_empty() || entry.eq_ignore_ascii_case("<local>") {
            continue;
        }
        let entry = entry
            .strip_prefix('*')
            .filter(|e| e.starts_with('.'))
            .unwrap_or(entry);
        if !entry.contains('*') {
            push(&mut items, entry);
        }
    }
    items.join(",")
}

/// Переменные прокси для uv: пусто, если прокси уже задан переменными среды
/// или в Windows не включён.
pub fn proxy_env_from(
    env_has_proxy: bool,
    inherited_no_proxy: Option<&str>,
    settings: &InternetSettings,
) -> Vec<(&'static str, OsString)> {
    if env_has_proxy || settings.enabled != Some(1) {
        return Vec::new();
    }
    let Some(url) = settings.server.as_deref().and_then(pick_server) else {
        return Vec::new();
    };
    vec![
        ("HTTPS_PROXY", url.clone().into()),
        ("HTTP_PROXY", url.into()),
        (
            "NO_PROXY",
            no_proxy(inherited_no_proxy, settings.overrides.as_deref()).into(),
        ),
    ]
}

/// Задан ли прокси переменными среды этого процесса.
pub fn env_has_proxy() -> bool {
    ["HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"]
        .iter()
        .any(|name| std::env::var_os(name).is_some_and(|v| !v.is_empty()))
}

#[cfg(windows)]
pub fn read_internet_settings() -> InternetSettings {
    use std::ffi::c_void;
    use std::ptr::null_mut;
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_DWORD, RRF_RT_REG_SZ,
    };

    const KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings";
    let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
    let key = wide(KEY);
    let dword = |name: &str| -> Option<u32> {
        let name = wide(name);
        let mut value: u32 = 0;
        let mut size = std::mem::size_of::<u32>() as u32;
        // SAFETY: строки с нулём на конце и буфер под DWORD живут до конца вызова.
        let status = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_DWORD,
                null_mut(),
                (&mut value as *mut u32).cast::<c_void>(),
                &mut size,
            )
        };
        (status == ERROR_SUCCESS).then_some(value)
    };
    let string = |name: &str| -> Option<String> {
        let name = wide(name);
        let mut size: u32 = 0;
        // SAFETY: первый вызов только узнаёт размер (буфер — null).
        let status = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                null_mut(),
                null_mut(),
                &mut size,
            )
        };
        if status != ERROR_SUCCESS || size == 0 {
            return None;
        }
        let mut buffer = vec![0u16; (size as usize).div_ceil(2)];
        // SAFETY: буфер на `size` байт; RegGetValueW дописывает нуль сам.
        let status = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                null_mut(),
                buffer.as_mut_ptr().cast::<c_void>(),
                &mut size,
            )
        };
        if status != ERROR_SUCCESS {
            return None;
        }
        let len = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
        Some(String::from_utf16_lossy(&buffer[..len]))
    };
    InternetSettings {
        enabled: dword("ProxyEnable"),
        server: string("ProxyServer"),
        overrides: string("ProxyOverride"),
    }
}

#[cfg(not(windows))]
pub fn read_internet_settings() -> InternetSettings {
    InternetSettings::default()
}

/// Прокси Windows для детей оболочки (uv), если переменными он не задан.
pub fn system_proxy_env() -> Vec<(&'static str, OsString)> {
    if env_has_proxy() {
        return Vec::new();
    }
    let inherited = std::env::var("NO_PROXY")
        .or_else(|_| std::env::var("no_proxy"))
        .ok();
    proxy_env_from(false, inherited.as_deref(), &read_internet_settings())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings(
        enabled: Option<u32>,
        server: Option<&str>,
        overrides: Option<&str>,
    ) -> InternetSettings {
        InternetSettings {
            enabled,
            server: server.map(str::to_string),
            overrides: overrides.map(str::to_string),
        }
    }

    fn get(env: &[(&'static str, OsString)], key: &str) -> Option<String> {
        env.iter()
            .find(|(k, _)| *k == key)
            .map(|(_, v)| v.to_string_lossy().into_owned())
    }

    #[test]
    fn host_port_becomes_http_url() {
        let env = proxy_env_from(
            false,
            None,
            &settings(Some(1), Some("127.0.0.1:3067"), None),
        );
        assert_eq!(
            get(&env, "HTTPS_PROXY").as_deref(),
            Some("http://127.0.0.1:3067")
        );
        assert_eq!(
            get(&env, "HTTP_PROXY").as_deref(),
            Some("http://127.0.0.1:3067")
        );
        assert_eq!(
            get(&env, "NO_PROXY").as_deref(),
            Some("localhost,127.0.0.1,::1")
        );
    }

    #[test]
    fn per_protocol_list_prefers_https_then_http() {
        assert_eq!(
            pick_server("http=10.0.0.1:80;https=10.0.0.2:443;socks=10.0.0.3:1080").as_deref(),
            Some("http://10.0.0.2:443")
        );
        assert_eq!(
            pick_server("http=10.0.0.1:8080;ftp=x:21").as_deref(),
            Some("http://10.0.0.1:8080")
        );
        assert_eq!(pick_server("socks=10.0.0.3:1080"), None);
    }

    #[test]
    fn explicit_scheme_is_kept_and_garbage_rejected() {
        assert_eq!(
            pick_server("https://proxy.example.com:8443/").as_deref(),
            Some("https://proxy.example.com:8443")
        );
        assert_eq!(pick_server(""), None);
        assert_eq!(pick_server("не адрес"), None);
        assert_eq!(pick_server("host"), None);
        assert_eq!(pick_server("host:99999"), None);
        assert_eq!(pick_server("ftp://host:21"), None);
        assert_eq!(
            pick_server("[::1]:8080").as_deref(),
            Some("http://[::1]:8080")
        );
    }

    #[test]
    fn disabled_or_missing_gives_nothing() {
        assert!(proxy_env_from(
            false,
            None,
            &settings(Some(0), Some("127.0.0.1:3067"), None)
        )
        .is_empty());
        assert!(
            proxy_env_from(false, None, &settings(None, Some("127.0.0.1:3067"), None)).is_empty()
        );
        assert!(proxy_env_from(false, None, &settings(Some(1), None, None)).is_empty());
        assert!(proxy_env_from(false, None, &InternetSettings::default()).is_empty());
    }

    #[test]
    fn existing_env_proxy_wins() {
        assert!(
            proxy_env_from(true, None, &settings(Some(1), Some("127.0.0.1:3067"), None)).is_empty()
        );
    }

    #[test]
    fn proxy_override_becomes_no_proxy() {
        assert_eq!(
            no_proxy(None, Some("*.corp.example;intranet;10.*;<local>;localhost")),
            "localhost,127.0.0.1,::1,.corp.example,intranet"
        );
        assert_eq!(no_proxy(None, None), "localhost,127.0.0.1,::1");
    }

    #[test]
    fn inherited_no_proxy_is_merged_not_overwritten() {
        assert_eq!(
            no_proxy(Some("intranet, LOCALHOST"), Some("corp;intranet")),
            "intranet,LOCALHOST,127.0.0.1,::1,corp"
        );
        let env = proxy_env_from(
            false,
            Some("intranet"),
            &settings(Some(1), Some("127.0.0.1:3067"), None),
        );
        assert_eq!(
            get(&env, "NO_PROXY").as_deref(),
            Some("intranet,localhost,127.0.0.1,::1")
        );
    }
}
