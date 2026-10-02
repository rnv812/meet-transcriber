"""Прокси для дочерних процессов: Claude Code, Codex, загрузки моделей.

Claude Code и Codex — Node-программы: системный прокси Windows (WinINET,
«Параметры → Сеть → Прокси») они не видят и понимают только переменные
HTTPS_PROXY/HTTP_PROXY. В терминале они часто заданы, а у приложения,
запущенного из Проводника, — нет: соединение идёт напрямую, и там, где сервис
доступен только через VPN/прокси, модель отвечает 403 «Request not allowed».

Настройка `llm.proxy`:

* `system` (по умолчанию) — переменные среды, если уже заданы, иначе прокси
  из WinINET (ProxyEnable/ProxyServer/ProxyOverride). Сценарий автонастройки
  (PAC, AutoConfigURL) не поддерживается. На macOS системный прокси читается
  из `scutil --proxy` и приводится к тем же значениям (`scutil_values`).
* `none` — без прокси: унаследованные переменные убираются.
* адрес (`http://host:port`, `https://…`, `socks5://…`) — он.

Только stdlib (winreg — на Windows, scutil — на macOS): модуль читает резидент.
"""

import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SYSTEM = "system"
NONE = "none"
SCHEMES = ("http", "https", "socks5", "socks5h")
LOOPBACK = ("localhost", "127.0.0.1", "::1")
# Переменные, по которым дочерние программы находят прокси (регистр — любой).
PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
NO_PROXY_VAR = "NO_PROXY"
INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

HINT = (
    "Похоже, нет доступа к сервису напрямую. Если вы используете VPN или прокси, "
    "проверьте настройку «Прокси для подключения к моделям» в разделе «Ассистент»."
)
# Признаки «до сервиса не достучались» в ошибках Claude Code (Node) и Codex (Rust).
_CONNECTION_MARKERS = (
    "econnrefused", "econnreset", "etimedout", "enotfound", "eai_again",
    "unable to connect", "connection error", "fetch failed",
    "error sending request",
)

_WINDOWS = sys.platform == "win32"
_pac_warned = False

Registry = Callable[[], Mapping | None]


# --- значение настройки -------------------------------------------------------


def check(value) -> str | None:
    """None — значение годится для `llm.proxy`, иначе объяснение для человека."""
    text = str(value if value is not None else "").strip()
    if text in (SYSTEM, NONE):
        return None
    if not text:
        return "Укажите адрес прокси, например http://127.0.0.1:8080"
    if "://" not in text:
        return "Адрес прокси должен начинаться с http://, https:// или socks5://"
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return "Адрес прокси не распознан: проверьте узел и порт"
    if parts.scheme.lower() not in SCHEMES:
        return "Адрес прокси должен начинаться с http://, https:// или socks5://"
    if not parts.hostname:
        return "В адресе прокси нет узла, например http://127.0.0.1:8080"
    if port is None:
        return "В адресе прокси нет порта, например http://127.0.0.1:8080"
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return "В адресе прокси нужны только схема, узел и порт, например http://127.0.0.1:8080"
    return None


def normalize(value) -> str:
    """Значение из файла: негодное — `system` (файл правят руками)."""
    text = str(value if value is not None else "").strip()
    if check(text) is not None:
        return SYSTEM
    if text in (SYSTEM, NONE):
        return text
    return text.rstrip("/")


def _mode(cfg) -> str:
    """Режим из Settings, Settings.llm или строки."""
    if cfg is None or isinstance(cfg, str):
        return normalize(cfg)
    llm = getattr(cfg, "llm", cfg)
    return normalize(getattr(llm, "proxy", None))


# --- системный прокси (WinINET) -------------------------------------------------


def read_registry() -> dict | None:
    """Значения прокси из HKCU\\…\\Internet Settings; None — не Windows или
    ключ не читается. На macOS — те же значения из `scutil --proxy`."""
    if sys.platform == "darwin":
        return read_scutil()
    if not _WINDOWS:
        return None
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS)
    except (ImportError, OSError):
        return None
    values: dict = {}
    with key:
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                pass
    return values


SCUTIL_TIMEOUT_S = 5.0
_SCUTIL_LINE = re.compile(r"^\s*([^:{}]+?)\s*:\s*(.*?)\s*$")


def parse_scutil(text: str) -> dict:
    """Вывод `scutil --proxy` → {ключ: значение}; массивы (`<array> {…}`) —
    списками строк. Вложенных словарей у прокси нет."""
    values: dict = {}
    array_key = None
    for line in (text or "").splitlines():
        if line.strip() == "}":
            array_key = None
            continue
        match = _SCUTIL_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if array_key is not None:
            values[array_key].append(value)
        elif value.startswith("<array>"):
            array_key = key
            values[key] = []
        elif not value.startswith("<dictionary>"):
            values[key] = value
    return values


def scutil_values(text: str) -> dict:
    """Прокси macOS → значения в форме WinINET (`ProxyEnable`, `ProxyServer`
    вида `https=h:p;http=h:p`, `ProxyOverride`, `AutoConfigURL`): дальше они
    идут тем же путём, что и на Windows (`from_registry`)."""
    raw = parse_scutil(text)
    servers = []
    for proto, prefix in (("https", "HTTPS"), ("http", "HTTP")):
        host = str(raw.get(f"{prefix}Proxy") or "").strip()
        port = str(raw.get(f"{prefix}Port") or "").strip()
        if _enabled(raw.get(f"{prefix}Enable")) and host:
            servers.append(f"{proto}={host}:{port}" if port else f"{proto}={host}")
    values: dict = {"ProxyEnable": 1 if servers else 0}
    if servers:
        values["ProxyServer"] = ";".join(servers)
    exceptions = raw.get("ExceptionsList")
    if isinstance(exceptions, list) and exceptions:
        values["ProxyOverride"] = ";".join(str(e) for e in exceptions)
    if _enabled(raw.get("ProxyAutoConfigEnable")) and raw.get("ProxyAutoConfigURLString"):
        values["AutoConfigURL"] = raw["ProxyAutoConfigURLString"]
    return values


def read_scutil(run=None) -> dict | None:
    """Системный прокси macOS (`scutil --proxy`); не ответил — None."""
    run = run or subprocess.run
    try:
        out = run(["scutil", "--proxy"], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=SCUTIL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return scutil_values(out.stdout)


def _enabled(value) -> bool:
    try:
        return int(value or 0) == 1
    except (TypeError, ValueError):
        return False


def _pick_server(raw: str) -> str | None:
    """`host:port` или `http=h:p;https=h:p;socks=h:p` → адрес с схемой.
    Из списка по протоколам — https, иначе http; SOCKS-только не берём:
    Claude Code SOCKS-прокси не поддерживает."""
    raw = (raw or "").strip()
    if "=" in raw:
        table = {}
        for part in raw.split(";"):
            proto, sep, address = part.partition("=")
            if sep:
                table[proto.strip().lower()] = address.strip()
        entry = table.get("https") or table.get("http")
    else:
        entry = next((p.strip() for p in raw.split(";") if p.strip()), None)
    if not entry:
        return None
    if "://" not in entry:
        entry = "http://" + entry
    return entry.rstrip("/") if check(entry) is None else None


def _no_proxy(override: str | None) -> str:
    """ProxyOverride → NO_PROXY. Локальные адреса — всегда (`<local>` тоже
    сводится к ним); `*.domain` → `.domain`; прочие маски (`10.*`) в NO_PROXY
    не выражаются и опускаются."""
    items = list(LOOPBACK)
    for part in str(override or "").replace(",", ";").split(";"):
        entry = part.strip()
        if not entry or entry.lower() == "<local>":
            continue
        if entry.startswith("*."):
            entry = entry[1:]
        if "*" in entry or entry in items:
            continue
        items.append(entry)
    return ",".join(items)


def _reset_pac_warning() -> None:
    global _pac_warned
    _pac_warned = False


def from_registry(values: Mapping | None) -> tuple[str, str] | None:
    """(адрес прокси, NO_PROXY) из значений WinINET или None."""
    if not values:
        return None
    if _enabled(values.get("ProxyEnable")):
        server = _pick_server(str(values.get("ProxyServer") or ""))
        if server:
            return server, _no_proxy(values.get("ProxyOverride"))
    if values.get("AutoConfigURL"):
        global _pac_warned
        if not _pac_warned:
            _pac_warned = True
            log.info("системный прокси задан сценарием автонастройки (PAC) — "
                     "не поддерживается; укажите адрес прокси в настройках")
    return None


# --- переменные для детей -------------------------------------------------------


def _env_proxy(environ: Mapping) -> str | None:
    """HTTPS_PROXY/HTTP_PROXY из окружения (любой регистр)."""
    upper = {str(k).upper(): v for k, v in environ.items()}
    for name in ("HTTPS_PROXY", "HTTP_PROXY"):
        if upper.get(name):
            return upper[name]
    return None


def _both_cases(names, value: str) -> dict[str, str]:
    env = {}
    for name in names:
        env[name] = env[name.lower()] = value
    return env


def _has_any_proxy(environ: Mapping) -> bool:
    return any(_is_proxy_var(k) and v for k, v in environ.items())


def _merge_no_proxy(environ: Mapping, extra: str) -> str:
    """NO_PROXY ребёнка: унаследованный (любой регистр) плюс `extra`, без
    повторов (регистр не важен) — чужие исключения не затираются."""
    items: list[str] = []
    seen: set[str] = set()
    inherited = [str(v) for k, v in environ.items() if str(k).upper() == NO_PROXY_VAR and v]
    for raw in [*inherited, extra]:
        for part in raw.split(","):
            entry = part.strip()
            if entry and entry.lower() not in seen:
                seen.add(entry.lower())
                items.append(entry)
    return ",".join(items)


def proxy_env(cfg, environ: Mapping | None = None,
              registry: Registry | None = None) -> dict[str, str]:
    """Переменные, которые надо ДОБАВИТЬ окружению ребёнка.

    `none` — пусто (унаследованные переменные убирает вызывающий, см.
    `child_env`/`prepare`); адрес — он (и поверх унаследованного ALL_PROXY);
    `system` — прокси из WinINET, если переменными он ещё не задан. Если у
    ребёнка в итоге есть хоть какой-то прокси, локальные адреса дописываются в
    NO_PROXY: локальная модель и резидент через прокси не ходят никогда."""
    environ = os.environ if environ is None else environ
    mode = _mode(cfg)
    if mode == NONE:
        return {}
    added: dict[str, str] = {}
    bypass = ",".join(LOOPBACK)
    if mode == SYSTEM:
        found = None if _env_proxy(environ) else from_registry((registry or read_registry)())
        if found:
            url, bypass = found
            added = _both_cases(("HTTPS_PROXY", "HTTP_PROXY"), url)
    else:
        added = _both_cases(("HTTPS_PROXY", "HTTP_PROXY"), mode)
        if any(str(k).upper() == "ALL_PROXY" for k in environ):
            added.update(_both_cases(("ALL_PROXY",), mode))
    if not added and not _has_any_proxy(environ):
        return {}
    added.update(_both_cases((NO_PROXY_VAR,), _merge_no_proxy(environ, bypass)))
    return added


def _is_proxy_var(name: str) -> bool:
    return str(name).upper() in PROXY_VARS


def _for_platform(env: Mapping) -> dict[str, str]:
    """На Windows имена переменных регистронезависимы: два ключа HTTPS_PROXY
    и https_proxy в блоке окружения — дубли. Сводим к верхнему регистру
    (поздний ключ побеждает)."""
    if not _WINDOWS:
        return dict(env)
    return {str(k).upper(): v for k, v in env.items()}


def child_env(cfg, base: Mapping | None = None,
              registry: Registry | None = None) -> dict[str, str]:
    """Полное окружение дочернего процесса: `base` (по умолчанию os.environ)
    с прокси по настройке; `base` не меняется."""
    base = os.environ if base is None else base
    mode = _mode(cfg)
    env = {k: v for k, v in base.items() if not (mode == NONE and _is_proxy_var(k))}
    env = _for_platform(env)
    env.update(_for_platform(proxy_env(mode, base, registry)))
    return env


def settings_env(base: Mapping | None = None) -> dict[str, str]:
    """`child_env` по сохранённым настройкам (`llm.proxy`) — для подпроцессов
    резидента. Настройки не прочитались — «как в системе»."""
    try:
        from meet import settings

        cfg = settings.load()
    except Exception:  # резидент не должен падать из-за файла настроек
        cfg = None
    return child_env(cfg, base)


def prepare(cfg, registry: Registry | None = None) -> dict[str, str]:
    """Для вызова CLI из этого процесса (Claude Agent SDK: `options.env` умеет
    только добавлять): `none` убирает переменные прокси из os.environ, иначе —
    переменные, которые надо добавить. Зовут только процессы, где живёт
    вызов модели (job_worker, meet assist, meet.llm.check, CLI)."""
    mode = _mode(cfg)
    if mode == NONE:
        for name in [k for k in os.environ if _is_proxy_var(k)]:
            os.environ.pop(name, None)
        return {}
    return _for_platform(proxy_env(mode, os.environ, registry))


# --- для окна и ошибок --------------------------------------------------------------


def _mask(url: str) -> str:
    """Логин и пароль в адресе не показываем."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    auth, at, host = rest.rpartition("@")
    return f"{scheme}://***@{host}" if at and auth else url


def _system(environ: Mapping, registry: Registry | None) -> tuple[str | None, str | None]:
    """(адрес без логина и пароля, источник) режима «как в системе»."""
    inherited = _env_proxy(environ)
    if inherited:
        return _mask(str(inherited)), "env"
    found = from_registry((registry or read_registry)())
    if found:
        return _mask(found[0]), "system"
    return None, None


def describe(cfg, environ: Mapping | None = None,
             registry: Registry | None = None) -> dict:
    """Для окна настроек: режим (`system`/`none`/`custom`), какой прокси
    получат дети (без логина и пароля) и откуда он (`env`/`system`/`setting`);
    `system` — что дал бы режим «как в системе» (подпись этого варианта)."""
    environ = os.environ if environ is None else environ
    mode = _mode(cfg)
    system, source = _system(environ, registry)
    if mode == NONE:
        return {"mode": NONE, "effective": None, "source": None, "system": system}
    if mode != SYSTEM:
        return {"mode": "custom", "effective": _mask(mode), "source": "setting",
                "system": system}
    return {"mode": SYSTEM, "effective": system, "source": source, "system": system}


def with_hint(error: str | None) -> str | None:
    """Ошибка модели «сервис недоступен напрямую» (403 Request not allowed,
    нет соединения) — с подсказкой про настройку прокси."""
    if not error or HINT in error:
        return error
    low = error.lower()
    blocked = "403" in low and "request not allowed" in low
    if blocked or any(marker in low for marker in _CONNECTION_MARKERS):
        return f"{error}\n{HINT}"
    return error
