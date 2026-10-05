"""scripts/diar_bench_mac.py: замер для Mac коллеги. Здесь — его сетевая
часть: каждая загрузка с huggingface.co на своём HTTP-клиенте (прокси —
своё окружение), а в отчёт адрес прокси не попадает."""

import importlib.util
import sys
import types
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diar_bench_mac.py"


def _script(monkeypatch):
    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "false")
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    spec = importlib.util.spec_from_file_location("diar_bench_mac", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_online_load_gets_a_fresh_hub_session(monkeypatch):
    """huggingface_hub 1.x держит один httpx-клиент, а прокси httpx читает при
    его создании: без закрытия сессии загрузка «напрямую» шла бы через прокси."""
    bench = _script(monkeypatch)
    events = []
    hub = types.ModuleType("huggingface_hub")
    hub.close_session = lambda: events.append("close")
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)

    class Pipeline:
        @staticmethod
        def from_pretrained(repo, token=None):
            events.append("load")
            return object()

    assert bench.load_online(Pipeline, "hf_x")["ok"] is True
    assert bench.load_online(Pipeline, "hf_x")["ok"] is True
    assert events == ["close", "load", "close", "load"]


def test_old_hub_without_close_session_is_fine(monkeypatch):
    bench = _script(monkeypatch)
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.ModuleType("huggingface_hub"))

    class Pipeline:
        @staticmethod
        def from_pretrained(repo, token=None):
            raise ConnectionError("нет сети")

    assert bench.load_online(Pipeline, None)["error"] == "ConnectionError"


def test_proxy_is_reported_without_its_address(monkeypatch):
    bench = _script(monkeypatch)
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.example.org:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("NO_PROXY", "intranet.example.org")
    assert bench._proxy_vars() == {"HTTPS_PROXY": "http://другой узел:3128", "HTTP_PROXY": "http://локальный:7890"}
