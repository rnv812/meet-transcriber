"""Системный прокси macOS: `scutil --proxy` приводится к значениям WinINET и
дальше идёт тем же путём, что на Windows (meet.netproxy)."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from meet import netproxy

FIXTURES = Path(__file__).parent / "fixtures" / "mac"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_scutil_output_is_parsed_with_arrays():
    raw = netproxy.parse_scutil(_text("scutil_proxy.txt"))
    assert raw["HTTPSProxy"] == "proxy.example.test"
    assert raw["HTTPSPort"] == "3129"
    assert raw["ExceptionsList"] == ["*.local", "169.254/16", "intranet.example"]
    assert "<dictionary>" not in raw


def test_scutil_values_look_like_wininet():
    values = netproxy.scutil_values(_text("scutil_proxy.txt"))
    assert values == {
        "ProxyEnable": 1,
        "ProxyServer": "https=proxy.example.test:3129;http=proxy.example.test:3128",
        "ProxyOverride": "*.local;169.254/16;intranet.example",
    }
    url, no_proxy = netproxy.from_registry(values)
    assert url == "http://proxy.example.test:3129"
    assert no_proxy == "localhost,127.0.0.1,::1,.local,169.254/16,intranet.example"


def test_pac_only_is_not_supported_like_on_windows():
    values = netproxy.scutil_values(_text("scutil_pac.txt"))
    assert values["ProxyEnable"] == 0
    assert values["AutoConfigURL"] == "http://wpad.example.test/proxy.pac"
    assert netproxy.from_registry(values) is None


def test_children_get_the_mac_proxy_in_system_mode():
    values = netproxy.scutil_values(_text("scutil_proxy.txt"))
    env = netproxy.proxy_env("system", environ={}, registry=lambda: values)
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://proxy.example.test:3129"
    assert ".local" in env["NO_PROXY"]


def test_read_scutil_runs_scutil_and_survives_failures():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=_text("scutil_proxy.txt"))

    assert netproxy.read_scutil(run=run)["ProxyEnable"] == 1
    assert calls == [["scutil", "--proxy"]]
    assert netproxy.read_scutil(run=lambda *a, **k: SimpleNamespace(returncode=1, stdout="")) is None

    def missing(*_a, **_k):
        raise FileNotFoundError("scutil")

    assert netproxy.read_scutil(run=missing) is None

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("scutil", 5)

    assert netproxy.read_scutil(run=slow) is None
