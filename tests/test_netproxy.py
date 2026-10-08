"""Прокси для дочерних процессов: WinINET → HTTPS_PROXY/HTTP_PROXY/NO_PROXY.

Реестр подменяется: тест не читает настройки машины разработчика.
"""

import logging

import pytest

from meet import netproxy

LOOPBACK = "localhost,127.0.0.1,::1"


def reg(**values):
    return lambda: values


NO_REG = reg()


# --- проверка значения настройки -------------------------------------------


@pytest.mark.parametrize("value", [
    "system", "none", "http://127.0.0.1:8080", "https://proxy.example.com:3128",
    "socks5://127.0.0.1:1080", "http://user:secret@proxy.example.com:3128",
    "http://[::1]:8080", " http://127.0.0.1:8080/ ",
])
def test_check_accepts(value):
    assert netproxy.check(value) is None


@pytest.mark.parametrize("value, fragment", [
    ("", "Укажите адрес прокси"),
    ("127.0.0.1:8080", "http://, https:// или socks5://"),
    ("ftp://127.0.0.1:21", "http://, https:// или socks5://"),
    ("http://127.0.0.1", "порт"),
    ("http://:8080", "узла"),
    ("http://127.0.0.1:99999", "не распознан"),
    ("http://127.0.0.1:8080/path", "только схема"),
])
def test_check_rejects_with_russian_text(value, fragment):
    error = netproxy.check(value)
    assert error is not None and fragment in error


def test_normalize_garbage_is_system():
    assert netproxy.normalize(None) == "system"
    assert netproxy.normalize("мусор") == "system"
    assert netproxy.normalize(" none ") == "none"
    assert netproxy.normalize("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"


# --- разбор WinINET ---------------------------------------------------------


def test_system_host_port():
    env = netproxy.proxy_env("system", environ={},
                             registry=reg(ProxyEnable=1, ProxyServer="127.0.0.1:3067"))
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == "http://127.0.0.1:3067"
    assert env["https_proxy"] == env["http_proxy"] == "http://127.0.0.1:3067"
    assert env["NO_PROXY"] == env["no_proxy"] == LOOPBACK


def test_system_per_protocol_prefers_https():
    server = "http=10.0.0.1:80;https=10.0.0.2:443;socks=10.0.0.3:1080"
    env = netproxy.proxy_env("system", environ={},
                             registry=reg(ProxyEnable=1, ProxyServer=server))
    assert env["HTTPS_PROXY"] == "http://10.0.0.2:443"


def test_system_per_protocol_falls_back_to_http():
    env = netproxy.proxy_env("system", environ={},
                             registry=reg(ProxyEnable=1, ProxyServer="http=10.0.0.1:8080;ftp=x:21"))
    assert env["HTTPS_PROXY"] == "http://10.0.0.1:8080"


def test_system_socks_only_is_not_used():
    env = netproxy.proxy_env("system", environ={},
                             registry=reg(ProxyEnable=1, ProxyServer="socks=10.0.0.3:1080"))
    assert env == {}


def test_system_keeps_explicit_scheme():
    env = netproxy.proxy_env("system", environ={},
                             registry=reg(ProxyEnable=1, ProxyServer="https://proxy.example.com:8443"))
    assert env["HTTPS_PROXY"] == "https://proxy.example.com:8443"


@pytest.mark.parametrize("values", [
    {},
    {"ProxyEnable": 0, "ProxyServer": "127.0.0.1:3067"},
    {"ProxyEnable": 1},
    {"ProxyEnable": 1, "ProxyServer": ""},
    {"ProxyEnable": 1, "ProxyServer": "не адрес"},
])
def test_system_missing_or_disabled(values):
    assert netproxy.proxy_env("system", environ={}, registry=lambda: values) == {}


def test_registry_unavailable():
    assert netproxy.proxy_env("system", environ={}, registry=lambda: None) == {}


def test_proxy_override_to_no_proxy():
    env = netproxy.proxy_env("system", environ={}, registry=reg(
        ProxyEnable=1, ProxyServer="127.0.0.1:3067",
        ProxyOverride="*.corp.example;intranet;10.*;<local>"))
    assert env["NO_PROXY"] == LOOPBACK + ",.corp.example,intranet"


def test_pac_is_not_supported_and_logged_once(caplog):
    netproxy._reset_pac_warning()
    caplog.set_level(logging.INFO, logger="meet.netproxy")
    pac = reg(AutoConfigURL="http://wpad.example/proxy.pac")
    assert netproxy.proxy_env("system", environ={}, registry=pac) == {}
    assert netproxy.proxy_env("system", environ={}, registry=pac) == {}
    assert sum("PAC" in r.getMessage() for r in caplog.records) == 1


def test_manual_proxy_wins_over_pac():
    env = netproxy.proxy_env("system", environ={}, registry=reg(
        ProxyEnable=1, ProxyServer="127.0.0.1:3067", AutoConfigURL="http://wpad.example/p.pac"))
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3067"


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"])
def test_system_respects_existing_env(name):
    """Прокси уже задан переменными — его не трогаем, только добавляем
    локальные адреса в NO_PROXY."""
    called = []
    env = netproxy.proxy_env("system", environ={name: "http://10.1.1.1:3128"},
                             registry=lambda: called.append(1) or {})
    assert called == []
    assert not any(k.upper() in ("HTTPS_PROXY", "HTTP_PROXY") for k in env)
    assert env["NO_PROXY"] == env["no_proxy"] == LOOPBACK


def test_no_proxy_is_merged_not_overwritten():
    environ = {"HTTPS_PROXY": "http://10.1.1.1:3128", "no_proxy": "intranet,LOCALHOST"}
    env = netproxy.proxy_env("system", environ=environ, registry=NO_REG)
    assert env["NO_PROXY"] == "intranet,LOCALHOST,127.0.0.1,::1"
    env = netproxy.proxy_env("http://10.2.2.2:8080", environ={"NO_PROXY": "intranet"},
                             registry=NO_REG)
    assert env["NO_PROXY"] == "intranet," + LOOPBACK
    registry = reg(ProxyEnable=1, ProxyServer="127.0.0.1:3067", ProxyOverride="corp")
    env = netproxy.proxy_env("system", environ={"NO_PROXY": "intranet"}, registry=registry)
    assert env["NO_PROXY"] == "intranet," + LOOPBACK + ",corp"


def test_inherited_all_proxy_alone_gets_loopback_bypass():
    env = netproxy.proxy_env("system", environ={"ALL_PROXY": "socks5://10.1.1.1:1080"},
                             registry=NO_REG)
    assert env == {"NO_PROXY": LOOPBACK, "no_proxy": LOOPBACK}


def test_nothing_inherited_nothing_added():
    assert netproxy.proxy_env("system", environ={"NO_PROXY": "x"}, registry=NO_REG) == {}


def test_explicit_url_overrides_inherited_all_proxy():
    env = netproxy.proxy_env("http://10.1.1.1:3128",
                             environ={"all_proxy": "socks5://10.9.9.9:1080"}, registry=NO_REG)
    assert env["ALL_PROXY"] == env["all_proxy"] == "http://10.1.1.1:3128"
    child = netproxy.child_env("http://10.1.1.1:3128",
                               base={"ALL_PROXY": "socks5://10.9.9.9:1080"}, registry=NO_REG)
    assert {k.upper(): v for k, v in child.items()}["ALL_PROXY"] == "http://10.1.1.1:3128"


def test_explicit_url_without_inherited_all_proxy_does_not_add_it():
    env = netproxy.proxy_env("http://10.1.1.1:3128", environ={}, registry=NO_REG)
    assert not any(k.upper() == "ALL_PROXY" for k in env)


def test_child_env_merges_no_proxy_case_insensitively(monkeypatch):
    monkeypatch.setattr(netproxy, "_WINDOWS", True)
    base = {"HTTPS_PROXY": "http://10.1.1.1:3128", "no_proxy": "intranet"}
    env = netproxy.child_env("system", base=base, registry=NO_REG)
    assert [k for k in env if k.upper() == "NO_PROXY"] == ["NO_PROXY"]
    assert env["NO_PROXY"] == "intranet," + LOOPBACK


# --- режимы «без прокси» и «свой адрес» --------------------------------------


def test_none_mode_adds_nothing():
    registry = reg(ProxyEnable=1, ProxyServer="127.0.0.1:3067")
    assert netproxy.proxy_env("none", environ={}, registry=registry) == {}


def test_explicit_url():
    env = netproxy.proxy_env("http://10.1.1.1:3128", environ={"HTTPS_PROXY": "http://other:1"},
                             registry=NO_REG)
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://10.1.1.1:3128"
    assert env["NO_PROXY"] == LOOPBACK


def test_accepts_settings_object():
    from meet.settings import Settings

    cfg = Settings.from_raw({"llm": {"proxy": "http://10.1.1.1:3128"}})
    assert netproxy.proxy_env(cfg, environ={}, registry=NO_REG)["HTTPS_PROXY"] == "http://10.1.1.1:3128"
    assert netproxy.proxy_env(cfg.llm, environ={}, registry=NO_REG)["HTTPS_PROXY"] == "http://10.1.1.1:3128"


# --- окружение ребёнка -------------------------------------------------------


def test_child_env_adds_system_proxy():
    env = netproxy.child_env("system", base={"PATH": "x"},
                             registry=reg(ProxyEnable=1, ProxyServer="127.0.0.1:3067"))
    assert env["PATH"] == "x"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3067"


def test_child_env_none_strips_inherited_proxy():
    base = {"PATH": "x", "HTTPS_PROXY": "a", "http_proxy": "b", "ALL_PROXY": "c", "NO_PROXY": "d"}
    env = netproxy.child_env("none", base=base, registry=NO_REG)
    assert {k.upper() for k in env} == {"PATH", "NO_PROXY"}


def test_child_env_windows_has_no_case_duplicates(monkeypatch):
    monkeypatch.setattr(netproxy, "_WINDOWS", True)
    env = netproxy.child_env("http://10.1.1.1:3128", base={"Path": "x", "HTTPS_PROXY": "old"},
                             registry=NO_REG)
    assert len({k.upper() for k in env}) == len(env)
    assert env["HTTPS_PROXY"] == "http://10.1.1.1:3128"
    assert env["PATH"] == "x"


def test_child_env_does_not_touch_base():
    base = {"HTTPS_PROXY": "a"}
    netproxy.child_env("none", base=base, registry=NO_REG)
    assert base == {"HTTPS_PROXY": "a"}


def test_prepare_in_process_none_strips_environ(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://10.1.1.1:3128")
    assert netproxy.prepare("none", registry=NO_REG) == {}
    import os
    assert "HTTPS_PROXY" not in os.environ


def test_prepare_in_process_returns_vars(monkeypatch):
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    env = netproxy.prepare("system", registry=reg(ProxyEnable=1, ProxyServer="127.0.0.1:3067"))
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3067"


# --- описание для окна --------------------------------------------------------


def test_describe_system_from_registry():
    got = netproxy.describe("system", environ={},
                            registry=reg(ProxyEnable=1, ProxyServer="127.0.0.1:3067"))
    assert got == {"mode": "system", "effective": "http://127.0.0.1:3067", "source": "system",
                   "system": "http://127.0.0.1:3067"}


def test_describe_system_from_env_masks_credentials():
    got = netproxy.describe("system", environ={"HTTPS_PROXY": "http://user:pass@10.1.1.1:3128"},
                            registry=NO_REG)
    assert got == {"mode": "system", "effective": "http://***@10.1.1.1:3128", "source": "env",
                   "system": "http://***@10.1.1.1:3128"}


def test_describe_system_nothing():
    assert netproxy.describe("system", environ={}, registry=NO_REG) == {
        "mode": "system", "effective": None, "source": None, "system": None}


def test_describe_none_and_custom():
    """`system` — что дал бы режим «как в системе»: окно подписывает этот
    вариант, даже когда выбран другой."""
    registry = reg(ProxyEnable=1, ProxyServer="127.0.0.1:3067")
    assert netproxy.describe("none", environ={}, registry=registry) == {
        "mode": "none", "effective": None, "source": None, "system": "http://127.0.0.1:3067"}
    assert netproxy.describe("http://u:p@10.1.1.1:3128", environ={}, registry=NO_REG) == {
        "mode": "custom", "effective": "http://***@10.1.1.1:3128", "source": "setting",
        "system": None}


# --- подсказка к ошибке -------------------------------------------------------


@pytest.mark.parametrize("error", [
    "Claude Code returned an error result: Failed to authenticate. API Error: 403 "
    "{\"error\":{\"type\":\"forbidden\",\"message\":\"Request not allowed\"}}",
    "API Error: Unable to connect to API (ECONNREFUSED)",
    "stream error: error sending request for url (https://api.example.com/v1/responses)",
    "API Error: Connection error.",
])
def test_hint_added(error):
    got = netproxy.with_hint(error)
    assert got.startswith(error) and netproxy.HINT in got
    assert netproxy.with_hint(got) == got  # дважды не добавляется


@pytest.mark.parametrize("error", [None, "", "таймаут", "API Error: 403 Forbidden"])
def test_hint_not_added(error):
    assert netproxy.with_hint(error) == error


def test_hint_text_is_russian():
    assert "Прокси для подключения к моделям" in netproxy.HINT
    assert "«Настройки → Модели ИИ»" in netproxy.HINT
    assert "«Ассистент»" not in netproxy.HINT
