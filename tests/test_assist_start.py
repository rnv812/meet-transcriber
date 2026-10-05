"""Поэтапный старт `meet assist`: веб и порт — сразу, звук — до модели,
вход в Claude — параллельно с моделью, голоса — после готовности.

Тяжёлые части подменены (`_Heavy` из test_assist_app); у поддельного движка
— поэтапный API (`open_source`/`load_asr`/`begin`/`load_voices`).
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from meet.assist import app as app_mod
from meet.settings import Settings

from test_assist_app import _Heavy, _run


def _staged(heavy, *, asr_gate: threading.Event | None = None):
    """Поэтапный API у поддельного движка; порядок вызовов — в heavy.calls."""
    heavy.calls = []

    def on_engine(engine):
        def open_source():
            heavy.calls.append("open_source")
            engine.started = True

        def load_asr():
            heavy.calls.append("load_asr:begin")
            if asr_gate is not None:
                assert asr_gate.wait(10), "модель так и не «загрузилась»"
            heavy.calls.append("load_asr:end")

        engine.open_source = open_source
        engine.load_asr = load_asr
        engine.begin = lambda: heavy.calls.append("begin")
        engine.load_voices = lambda: heavy.calls.append("load_voices")
        engine.devices_fallback = []

    heavy.on_engine = on_engine


def _record_endpoint(monkeypatch):
    seen = []
    real = app_mod.write_endpoint

    def write(path, **kw):
        seen.append({k: kw.get(k) for k in ("ready", "capturing", "stage")})
        real(path, **kw)

    monkeypatch.setattr(app_mod, "write_endpoint", write)
    return seen


async def _done(stop):
    return None


def test_port_first_then_sound_then_model_then_ready(tmp_path, monkeypatch, capsys):
    heavy = _Heavy(monkeypatch, digester_run=_done)
    _staged(heavy)
    seen = _record_endpoint(monkeypatch)
    _run(tmp_path, open_browser=False, port=0, endpoint_file=str(tmp_path / "live.json"),
         no_voices=False)
    assert seen[0] == {"ready": False, "capturing": False, "stage": None}
    assert {"ready": False, "capturing": False, "stage": app_mod.STAGE_DEVICES} in seen
    assert {"ready": False, "capturing": True, "stage": app_mod.STAGE_ASR} in seen
    assert seen[-1]["ready"] is True
    calls = heavy.calls
    assert calls.index("begin") > calls.index("load_asr:end")
    assert calls.index("begin") > calls.index("open_source")
    assert calls[-1] == "load_voices"  # голоса — после готовности, в фоне
    out = capsys.readouterr().out
    assert "старт: модель распознавания загружена — за" in out
    assert "старт: ассистент готов — за" in out


def test_claude_login_is_checked_while_the_model_loads(tmp_path, monkeypatch):
    gate = threading.Event()
    heavy = _Heavy(monkeypatch, digester_run=_done)
    _staged(heavy, asr_gate=gate)

    async def auth(proxy=None, model=None):
        gate.set()  # модель «догрузится», только если вход проверяют одновременно
        return None

    monkeypatch.setattr(app_mod, "check_auth", auth)
    _run(tmp_path, open_browser=False, port=0,
         cfg=Settings.from_raw({"llm": {"provider": "claude-code"}}))
    assert "begin" in heavy.calls


def test_failed_login_stops_the_start_with_its_text(tmp_path, monkeypatch):
    heavy = _Heavy(monkeypatch)
    _staged(heavy)

    async def auth(proxy=None, model=None):
        return "войдите заново"

    monkeypatch.setattr(app_mod, "check_auth", auth)
    ep = tmp_path / "live.json"
    with pytest.raises(SystemExit, match="Авторизация Claude не прошла: войдите заново"):
        _run(tmp_path, open_browser=False, port=0, endpoint_file=str(ep),
             cfg=Settings.from_raw({"llm": {"provider": "claude-code"}}))
    assert "begin" not in heavy.calls
    assert heavy.engine.stopped and not ep.exists()


def test_stop_while_the_model_loads_is_prompt_and_refuses_questions(tmp_path, monkeypatch,
                                                                     capsys):
    gate = threading.Event()
    heavy = _Heavy(monkeypatch)
    _staged(heavy, asr_gate=gate)
    ep = tmp_path / "live.json"
    seen = {}

    def client():
        try:
            deadline = time.monotonic() + 20
            while True:
                try:
                    info = json.loads(ep.read_text(encoding="utf-8"))
                    if info.get("capturing"):
                        break
                except (OSError, ValueError):
                    pass
                assert time.monotonic() < deadline, "звук так и не пошёл"
                time.sleep(0.02)
            seen["info"] = info
            base = f"http://127.0.0.1:{info['port']}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                opener.open(urllib.request.Request(f"{base}/ask", data=json.dumps({"question": "что?"}).encode(),
                                                   method="POST"), timeout=10)
            except urllib.error.HTTPError as e:
                seen["ask"] = (e.code, e.read().decode("utf-8"))
            opener.open(urllib.request.Request(f"{base}/stop", data=b"", method="POST"),
                        timeout=10).close()
        except BaseException as e:
            seen["error"] = repr(e)
            heavy.emergency_stop()

    threading.Thread(target=client, daemon=True).start()
    began = time.monotonic()
    try:
        _run(tmp_path, open_browser=False, port=0, endpoint_file=str(ep))
    finally:
        gate.set()
    assert "error" not in seen, seen.get("error")
    assert time.monotonic() - began < 15
    assert seen["info"]["ready"] is False and seen["info"]["stage"] == app_mod.STAGE_ASR
    assert seen["ask"][0] == 409 and "запускается" in seen["ask"][1]
    assert "begin" not in heavy.calls and heavy.engine.stopped
    assert "Остановлено:" in capsys.readouterr().out  # звук шёл — запись дописана


def test_attached_assistant_lowers_its_priority_only_once_ready(tmp_path, monkeypatch):
    folder = tmp_path / "rec" / "2026-10-03_09-00"
    folder.mkdir(parents=True)
    heavy = _Heavy(monkeypatch, digester_run=_done)
    _staged(heavy)
    real_on_engine = heavy.on_engine

    def on_engine(engine):
        real_on_engine(engine)
        engine.attach_positions = {"mic.wav": 0.0}
        engine.catchup_progress = lambda: None
        engine.start_catchup = lambda tracks, info=None: True

    heavy.on_engine = on_engine
    monkeypatch.setattr(app_mod, "_lower_priority", lambda: heavy.calls.append("lower"))
    _run(tmp_path, open_browser=False, port=0, attach_to=str(folder), tap_port=1,
         tap_token="t" * 32)
    assert heavy.calls.index("lower") > heavy.calls.index("begin")


def test_cli_turns_text_errors_into_the_no_retry_exit_code(capsys):
    from meet import cli

    with pytest.raises(SystemExit) as exc:
        with cli._fatal_exit_code():
            raise SystemExit("Запись уже идёт (папка x)")
    assert exc.value.code == app_mod.EXIT_FATAL
    assert "Запись уже идёт" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        with cli._fatal_exit_code():
            raise SystemExit(0)
    assert exc.value.code == 0


def test_view_says_what_is_starting(tmp_path):
    from meet.assist.app import AssistState
    from meet.assist.bus import TranscriptBus
    from meet.assist.live_state import LiveState

    state = AssistState(bus=TranscriptBus(), live=LiveState(), glossary="", vault=None,
                        cwd=tmp_path)
    assert "starting" not in state.view()
    sig = state.signature()
    state.ready, state.stage = False, app_mod.STAGE_ASR
    assert state.view()["starting"] == app_mod.STAGE_ASR
    assert state.signature() != sig
