import asyncio
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from meet.assist.agent import AgentReply
from meet.assist.app import AssistState
from meet.assist.bus import TranscriptBus
from meet.assist.digest import Digest
from meet.settings import Settings


def test_state_set_task_rebuilds_prompts(tmp_path, monkeypatch):
    vault = tmp_path / "Claude"
    (vault / "demo").mkdir(parents=True)
    (vault / "demo" / "_demo.md").write_text(
        "---\ntype: hub\n---\n# Demo\n\n## Сейчас\n\n- **Состояние:** пилот\n",
        encoding="utf-8",
    )
    state = AssistState(bus=TranscriptBus(), digest=Digest(),
                        glossary="джоба", vault=vault, cwd=tmp_path)
    assert "джоба" in state.digester_system
    asyncio.run(state.set_task("demo"))
    assert "пилот" in state.digester_system and "пилот" in state.qa_system


def test_state_without_vault_has_no_vault_rules(tmp_path):
    state = AssistState(bus=TranscriptBus(), digest=Digest(),
                        glossary="", vault=None, cwd=tmp_path)
    assert "superseded" not in state.qa_system
    assert state.qa_allowed_dirs == (tmp_path,)


async def _never_called_runner(prompt, **kw):
    raise AssertionError("в тестах модель не вызывается")


def _run_assist_capturing_matcher(tmp_path, monkeypatch, **kwargs):
    """Гоняет run_assist с замоканными тяжёлыми частями и возвращает
    voice_matcher, с которым был создан LiveEngine."""
    captured = {}

    class FakeEngine:
        def __init__(self, out_dir, transcriber, **kw):
            captured["voice_matcher"] = kw.get("voice_matcher", "MISSING")

        def start(self):
            pass

        def stop(self):
            pass

        def process_window(self):
            pass

    async def fake_check_auth():
        return None

    async def fake_main(state, port, **kw):
        pass

    monkeypatch.setattr("meet.llm.resolve",
                        lambda cfg: ("claude-code", _never_called_runner))
    monkeypatch.setattr("meet.assist.app.check_auth", fake_check_auth)
    monkeypatch.setattr("meet.assist.app._main", fake_main)
    monkeypatch.setattr("meet.asr.Transcriber", lambda: object())
    monkeypatch.setattr("meet.live.LiveEngine", FakeEngine)

    from meet.assist.app import run_assist

    run_assist(out_root=str(tmp_path), cfg=Settings.from_raw({}), **kwargs)
    return captured["voice_matcher"]


def test_run_assist_creates_voice_matcher_by_default(tmp_path, monkeypatch):
    from meet.voice_id import VoiceMatcher

    matcher = _run_assist_capturing_matcher(tmp_path, monkeypatch)
    assert isinstance(matcher, VoiceMatcher)


def test_run_assist_no_voices_disables_matcher(tmp_path, monkeypatch):
    matcher = _run_assist_capturing_matcher(tmp_path, monkeypatch, no_voices=True)
    assert matcher is None


# --- режим дочернего процесса -----------------------------------------------


class _Heavy:
    """Подмены тяжёлых частей run_assist: движок, ASR, модель, авторизация.
    Модель и звук в тестах не трогаются никогда."""

    def __init__(self, monkeypatch, *, resolved=("claude-code", _never_called_runner),
                 digester_run=None, start_error=None):
        self.engine = None
        self.auth_calls = 0
        self.resolve_calls = 0
        self.runner_for_calls = []
        self.digester_kwargs = None
        self.qa_kwargs = None
        self.opened = []
        heavy = self

        class FakeEngine:
            def __init__(self, out_dir, transcriber, **kw):
                self.out_dir = out_dir
                self.kw = kw
                self.started = self.stopped = False
                heavy.engine = self

            def start(self):
                if start_error is not None:
                    raise start_error
                self.started = True

            def stop(self):
                self.stopped = True

            def process_window(self):
                pass

        class FakeDigester:
            status = None

            def __init__(self, bus, digest, **kw):
                heavy.digester_kwargs = kw

            def set_system_prompt(self, text):
                pass

            async def run(self, stop):
                if digester_run is not None:
                    await digester_run(stop)
                else:
                    await stop.wait()

        from meet.assist import app as app_mod

        real_qa = app_mod.QAService

        def fake_qa(*a, **kw):
            heavy.qa_kwargs = kw
            return real_qa(*a, **kw)

        async def fake_check_auth():
            heavy.auth_calls += 1
            return None

        def fake_resolve(cfg):
            heavy.resolve_calls += 1
            return resolved

        def fake_runner_for(name, cfg):
            heavy.runner_for_calls.append(name)
            return _never_called_runner

        monkeypatch.setattr("meet.llm.resolve", fake_resolve)
        monkeypatch.setattr("meet.llm.runner_for", fake_runner_for)
        monkeypatch.setattr("meet.assist.app.check_auth", fake_check_auth)
        monkeypatch.setattr("meet.assist.app.Digester", FakeDigester)
        monkeypatch.setattr("meet.assist.app.QAService", fake_qa)
        monkeypatch.setattr("meet.asr.Transcriber", lambda: object())
        monkeypatch.setattr("meet.live.LiveEngine", FakeEngine)
        monkeypatch.setattr("webbrowser.open", self.opened.append)


def _run(tmp_path, **kw):
    from meet.assist.app import run_assist

    kw.setdefault("no_voices", True)
    run_assist(out_root=str(tmp_path / "rec"), cfg=Settings.from_raw({}), **kw)


def test_child_mode_endpoint_file_and_stop_route(tmp_path, monkeypatch):
    heavy = _Heavy(monkeypatch)
    ep = tmp_path / "run" / "assist.json"
    seen = {}

    def client():
        deadline = time.monotonic() + 30
        while not ep.exists():
            if time.monotonic() > deadline:
                return
            time.sleep(0.05)
        seen.update(json.loads(ep.read_text(encoding="utf-8")))
        base = f"http://127.0.0.1:{seen['port']}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        # Открытый SSE-поток (как у резидента) не должен держать выход.
        with opener.open(f"{base}/events", timeout=10) as sse:
            assert sse.readline().startswith(b"event: state")
            req = urllib.request.Request(f"{base}/stop", data=b"", method="POST")
            with opener.open(req, timeout=10) as r:
                seen["reply"] = json.loads(r.read())
            sse.read()  # поток закрывается сервером
            seen["sse_closed"] = True

    t = threading.Thread(target=client, daemon=True)
    t.start()
    started = time.monotonic()
    _run(tmp_path, open_browser=False, port=0, endpoint_file=str(ep))
    t.join(10)
    assert seen["reply"] == {"ok": True} and seen["sse_closed"]
    assert seen["pid"] == os.getpid()
    assert isinstance(seen["port"], int) and seen["port"] > 0
    assert Path(seen["folder"]).parent == tmp_path / "rec"
    assert Path(seen["folder"]).is_absolute()
    assert not ep.exists()  # удалён при выходе
    assert not list(ep.parent.iterdir())  # атомарная запись без хвостов .tmp
    assert heavy.engine.started and heavy.engine.stopped
    assert heavy.opened == []  # --no-browser
    assert time.monotonic() - started < 20  # /stop не ждёт таймаутов


def test_endpoint_removed_when_main_loop_fails(tmp_path, monkeypatch):
    ep = tmp_path / "assist.json"
    written = {}

    async def boom(stop):
        written.update(json.loads(ep.read_text(encoding="utf-8")))
        raise RuntimeError("сбой дайджестера")

    heavy = _Heavy(monkeypatch, digester_run=boom)
    with pytest.raises(RuntimeError, match="сбой"):
        _run(tmp_path, open_browser=False, port=0, endpoint_file=str(ep))
    assert written["pid"] == os.getpid()
    assert not ep.exists()
    assert heavy.engine.stopped


def test_endpoint_removed_on_ctrl_c(tmp_path, monkeypatch):
    ep = tmp_path / "assist.json"

    async def interrupt(stop):
        assert ep.exists()
        raise KeyboardInterrupt

    heavy = _Heavy(monkeypatch, digester_run=interrupt)
    _run(tmp_path, open_browser=False, port=0, endpoint_file=str(ep))
    assert not ep.exists()
    assert heavy.engine.stopped


def test_endpoint_removed_when_engine_start_fails(tmp_path, monkeypatch):
    heavy = _Heavy(monkeypatch,
                   start_error=SystemExit("Запись уже идёт (папка x)"))
    ep = tmp_path / "assist.json"
    ep.write_text("{}", encoding="utf-8")  # хвост прошлого падения
    with pytest.raises(SystemExit, match="Запись уже идёт"):
        _run(tmp_path, open_browser=False, port=0, endpoint_file=str(ep))
    assert not ep.exists()
    assert heavy.engine.stopped  # stop() безопасен и после неудачного start


def test_default_cli_path_opens_browser_on_8765(tmp_path, monkeypatch):
    """Путь автора: `meet assist` без новых флагов — страница в браузере, 8765."""
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, digester_run=done)
    ports = []

    class FakeRunner:
        addresses = [("127.0.0.1", 8765)]

        async def cleanup(self):
            pass

    async def fake_run_web(state, port):
        ports.append(port)
        return FakeRunner()

    monkeypatch.setattr("meet.assist.app.run_web", fake_run_web)
    from meet.assist.app import run_assist

    run_assist(out_root=str(tmp_path / "rec"), no_voices=True,
               cfg=Settings.from_raw({}))
    assert ports == [8765]
    assert heavy.opened == ["http://127.0.0.1:8765/"]
    assert heavy.auth_calls == 1  # проверка авторизации Claude — как раньше
    assert heavy.engine.started and heavy.engine.stopped


def test_no_provider_exits_with_clear_error(tmp_path, monkeypatch):
    heavy = _Heavy(monkeypatch, resolved=(None, None))
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, open_browser=False, port=0)
    assert "Подключите Claude Code или Codex в настройках" in str(exc.value.code)
    assert heavy.engine is None and heavy.auth_calls == 0


def test_runner_comes_from_llm_resolve(tmp_path, monkeypatch):
    async def codex_runner(prompt, **kw):
        return AgentReply(text="ok")

    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, resolved=("codex", codex_runner), digester_run=done)
    _run(tmp_path, open_browser=False, port=0)
    assert heavy.resolve_calls == 1
    assert heavy.digester_kwargs["runner"] is codex_runner
    assert heavy.qa_kwargs["runner"] is codex_runner
    assert heavy.auth_calls == 0  # проверка Claude — только для claude-code


def test_explicit_provider_skips_resolve(tmp_path, monkeypatch):
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, digester_run=done)
    _run(tmp_path, open_browser=False, port=0, provider="codex")
    assert heavy.resolve_calls == 0
    assert heavy.runner_for_calls == ["codex"]
    assert heavy.digester_kwargs["runner"] is _never_called_runner
