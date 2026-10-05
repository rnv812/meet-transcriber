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
from meet.assist.live_state import LIVE_STATE_JSON, LiveState
from meet.settings import Settings


def test_state_set_task_rebuilds_prompts(tmp_path, monkeypatch):
    vault = tmp_path / "Claude"
    (vault / "demo").mkdir(parents=True)
    (vault / "demo" / "_demo.md").write_text(
        "---\ntype: hub\n---\n# Demo\n\n## Сейчас\n\n- **Состояние:** пилот\n",
        encoding="utf-8",
    )
    state = AssistState(bus=TranscriptBus(), live=LiveState(),
                        glossary="джоба", vault=vault, cwd=tmp_path)
    assert "джоба" in state.digester_system
    asyncio.run(state.set_task("demo"))
    assert "пилот" in state.digester_system and "пилот" in state.qa_system


def test_state_view_and_hint_actions(tmp_path):
    live = LiveState()
    state = AssistState(bus=TranscriptBus(), live=live, glossary="", vault=None, cwd=tmp_path)
    saved = []
    state.on_change = lambda: saved.append(1)
    live.apply({"topic": "Запуск", "ops": [{"op": "add", "section": "hints", "kind": "risk",
                                           "text": "Нет владельца", "why": "", "t": "00:00:05"}]})
    sig = state.signature()
    view = state.view()
    assert view["hints"][0]["id"] == "h1" and view["summary"]["topic"] == "Запуск"
    assert "Запуск" in view["digest"] and view["status"] is None and "qa" not in view
    assert state.qa_version() == 0 and state.qa_items() == []
    assert view["hints_enabled"] is True
    assert state.hint_action("h1", "pin") and saved == [1]
    assert state.signature() != sig
    assert not state.hint_action("h9", "dismiss") and saved == [1]
    assert state.hint_action("h1", "dismiss") and state.view()["hints"] == []
    # «Вернуть» сразу после «Скрыть».
    assert state.hint_action("h1", "restore") and [h["id"] for h in state.view()["hints"]] == ["h1"]
    assert state.hint_action("h1", "dismiss")
    with pytest.raises(ValueError):
        state.hint_action("h1", "взорвать")


def test_state_without_vault_has_no_vault_rules(tmp_path):
    state = AssistState(bus=TranscriptBus(), live=LiveState(),
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
            captured["kw"] = kw

        def start(self):
            pass

        def stop(self):
            pass

        def process_window(self):
            pass

    async def fake_check_auth(proxy=None, model=None):
        return None

    async def fake_main(state, port, **kw):
        pass

    monkeypatch.setattr("meet.llm.resolve",
                        lambda cfg: ("claude-code", _never_called_runner))
    monkeypatch.setattr("meet.assist.app.check_auth", fake_check_auth)
    monkeypatch.setattr("meet.assist.app._claude_login_problem", lambda: None)
    monkeypatch.setattr("meet.assist.app._main", fake_main)
    monkeypatch.setattr("meet.asr.Transcriber", lambda: object())
    monkeypatch.setattr("meet.live.LiveEngine", FakeEngine)

    from meet.assist.app import run_assist

    run_assist(out_root=str(tmp_path), cfg=Settings.from_raw({}), **kwargs)
    _run_assist_capturing_matcher.kw = captured["kw"]
    return captured["voice_matcher"]


def test_run_assist_creates_voice_matcher_by_default(tmp_path, monkeypatch):
    from meet.voice_id import VoiceMatcher

    matcher = _run_assist_capturing_matcher(tmp_path, monkeypatch)
    assert isinstance(matcher, VoiceMatcher)


def test_run_assist_relabels_voices_through_the_bus(tmp_path, monkeypatch):
    from meet.assist.bus import TranscriptBus

    _run_assist_capturing_matcher(tmp_path, monkeypatch)
    kw = _run_assist_capturing_matcher.kw
    relabel = kw["on_relabel"]
    assert isinstance(relabel.__self__, TranscriptBus) and relabel.__func__ is TranscriptBus.relabel
    assert kw["on_entry"].__self__ is relabel.__self__  # та же шина, что у строк


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
        self.auth_proxy = "MISSING"
        self.auth_model = "MISSING"
        self.resolve_calls = 0
        self.runner_for_calls = []
        self.digester_kwargs = None
        self.qa_kwargs = None
        self.opened = []
        self.kb = None
        self.on_stop = None   # зовётся из FakeEngine.stop (проверки момента)
        self.on_engine = None  # подправить движок сразу после создания
        self.live = None
        self.stop_event = None
        self.loop = None
        heavy = self

        class FakeEngine:
            def __init__(self, out_dir, transcriber, **kw):
                self.out_dir = out_dir
                self.kw = kw
                self.started = self.stopped = False
                heavy.engine = self
                if heavy.on_engine is not None:
                    heavy.on_engine(self)

            def start(self):
                if start_error is not None:
                    raise start_error
                self.started = True

            def stop(self):
                if heavy.on_stop is not None:
                    heavy.on_stop()
                self.stopped = True

            def process_window(self):
                pass

        class FakeDigester:
            status = None

            def __init__(self, bus, live, **kw):
                heavy.digester_kwargs = kw
                heavy.live = live

            def set_system_prompt(self, text):
                pass

            def set_kb(self, kb):
                heavy.kb = kb  # указатель базы знаний строится в фоне

            async def run(self, stop):
                heavy.stop_event = stop
                heavy.loop = asyncio.get_running_loop()
                if digester_run is not None:
                    await digester_run(stop)
                else:
                    await stop.wait()

        from meet.assist import app as app_mod

        real_qa = app_mod.QAService

        def fake_qa(*a, **kw):
            heavy.qa_kwargs = kw
            return real_qa(*a, **kw)

        async def fake_check_auth(proxy=None, model=None):
            heavy.auth_calls += 1
            heavy.auth_proxy = proxy
            heavy.auth_model = model
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
        # Быстрая проверка CLI/входа (`claude auth status`) — не в тестах.
        monkeypatch.setattr("meet.assist.app._claude_login_problem", lambda: None)
        monkeypatch.setattr("meet.assist.app.Digester", FakeDigester)
        monkeypatch.setattr("meet.assist.app.QAService", fake_qa)
        monkeypatch.setattr("meet.asr.Transcriber", lambda: object())
        monkeypatch.setattr("meet.live.LiveEngine", FakeEngine)
        monkeypatch.setattr("webbrowser.open", self.opened.append)

    def emergency_stop(self):
        """Сторож теста: погасить run_assist из другого потока, чтобы сбой
        клиента всплыл ошибкой, а не вечным зависанием."""
        if self.loop is not None and self.stop_event is not None:
            try:
                self.loop.call_soon_threadsafe(self.stop_event.set)
            except RuntimeError:
                pass  # цикл уже закрыт — run_assist и так вышел


def _run(tmp_path, **kw):
    from meet.assist.app import run_assist

    kw.setdefault("no_voices", True)
    kw.setdefault("cfg", Settings.from_raw({}))
    run_assist(out_root=str(tmp_path / "rec"), **kw)


def test_child_mode_endpoint_file_and_stop_route(tmp_path, monkeypatch):
    heavy = _Heavy(monkeypatch)
    ep = tmp_path / "run" / "assist.json"
    seen = {}
    # Эндпоинт живёт до выхода процесса: при финализации движка он ещё на месте.
    heavy.on_stop = lambda: seen.update(ep_at_engine_stop=ep.exists())

    def client():
        try:
            deadline = time.monotonic() + 30
            while not ep.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("файл эндпоинта не появился")
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
        except BaseException as e:  # сбой клиента — гасим run_assist, ошибку в ассерт
            seen["client_error"] = repr(e)
            heavy.emergency_stop()

    t = threading.Thread(target=client, daemon=True)
    watchdog = threading.Timer(60, heavy.emergency_stop)
    watchdog.daemon = True
    t.start()
    watchdog.start()
    started = time.monotonic()
    try:
        _run(tmp_path, open_browser=False, port=0, endpoint_file=str(ep))
    finally:
        watchdog.cancel()
    t.join(10)
    assert "client_error" not in seen, seen.get("client_error")
    assert seen["reply"] == {"ok": True} and seen["sse_closed"]
    assert seen["ep_at_engine_stop"] is True
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
    """Прежний путь: `meet assist` без новых флагов — страница в браузере, 8765."""
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


def test_claude_auth_check_uses_proxy_setting(tmp_path, monkeypatch):
    """Проверка входа перед стартом идёт тем же прокси, что и вызовы модели."""
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, digester_run=done)
    _run(tmp_path, open_browser=False, port=0,
         cfg=Settings.from_raw({"llm": {"provider": "claude-code", "proxy": "none"}}))
    assert heavy.auth_calls == 1
    assert heavy.auth_proxy == "none"


def test_claude_auth_check_uses_the_configured_model(tmp_path, monkeypatch):
    """Проверка входа — моделью из настроек, а не «haiku»: её может не быть
    среди разрешённых у организации или прокси."""
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, digester_run=done)
    _run(tmp_path, open_browser=False, port=0,
         cfg=Settings.from_raw({"llm": {"provider": "claude-code", "model": "opus"}}))
    assert heavy.auth_model == "opus"


def test_no_provider_exits_with_clear_error(tmp_path, monkeypatch):
    heavy = _Heavy(monkeypatch, resolved=(None, None))
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, open_browser=False, port=0)
    assert "Подключите Claude Code, Codex или OpenCode в настройках" in str(exc.value.code)
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


def test_slow_model_call_does_not_delay_finalization(tmp_path, monkeypatch):
    """Вызов модели в потоке (Codex/локальная, asyncio.to_thread) после /stop
    не должен держать engine.stop(): хвост и lock — сразу, а не через 180 с."""
    release = threading.Event()

    async def slow_tick(stop):
        asyncio.ensure_future(asyncio.to_thread(release.wait, 30))
        await asyncio.sleep(0.1)  # поток модели занят
        stop.set()  # как POST /stop
        await asyncio.sleep(3600)

    heavy = _Heavy(monkeypatch, digester_run=slow_tick)
    stopped_at = {}
    heavy.on_stop = lambda: stopped_at.setdefault("t", time.monotonic())
    started = time.monotonic()
    try:
        _run(tmp_path, open_browser=False, port=0)
        returned = time.monotonic()
    finally:
        release.set()
    assert heavy.engine.stopped
    assert stopped_at["t"] - started < 5
    assert returned - started < 5


def test_child_stops_itself_when_parent_resident_dies(tmp_path, monkeypatch):
    """Резидент умер жёстко: ребёнок сам штатно дописывает дорожки и выходит,
    а не держит микрофон и `.recording.lock` сиротой."""
    from meet.assist import app as app_mod

    heavy = _Heavy(monkeypatch)
    alive = iter([True, True])
    monkeypatch.setattr(app_mod, "PARENT_POLL_S", 0.01)
    monkeypatch.setattr(app_mod, "_pid_alive", lambda pid: next(alive, False))
    watchdog = threading.Timer(30, heavy.emergency_stop)
    watchdog.daemon = True
    watchdog.start()
    started = time.monotonic()
    try:
        _run(tmp_path, open_browser=False, port=0, parent_pid=4321)
    finally:
        watchdog.cancel()
    assert time.monotonic() - started < 10
    assert heavy.engine.started and heavy.engine.stopped


# --- база знаний в живом режиме ---------------------------------------------


def test_state_knowledge_dir_reaches_qa_and_term_index(tmp_path):
    """База знаний: вопросам — на чтение (Codex берёт рабочей папкой первую из
    allowed_dirs[1:]); тикам — только указатель терминов, без инструментов."""
    kb = tmp_path / "kb"
    kb.mkdir()
    state = AssistState(bus=TranscriptBus(), live=LiveState(), glossary="",
                        vault=None, cwd=tmp_path, knowledge=kb)
    assert state.qa_allowed_dirs == (tmp_path, kb)
    assert state.knowledge == kb
    assert str(kb) in state.qa_system and str(kb) not in state.digester_system
    assert "superseded" not in state.qa_system  # конвенция хаба — только у vault


def test_state_knowledge_same_as_vault_keeps_the_old_setup(tmp_path):
    """knowledge_dir мигрировал из vault: доступ и промпты — прежние."""
    vault = tmp_path / "Claude"
    vault.mkdir()
    plain = AssistState(bus=TranscriptBus(), live=LiveState(), glossary="",
                        vault=vault, cwd=tmp_path)
    same = AssistState(bus=TranscriptBus(), live=LiveState(), glossary="",
                       vault=vault, cwd=tmp_path, knowledge=vault)
    assert same.qa_allowed_dirs == plain.qa_allowed_dirs == (tmp_path, vault)
    assert same.knowledge is None
    assert same.qa_system == plain.qa_system
    assert same.digester_system == plain.digester_system


def test_state_missing_knowledge_dir_is_ignored(tmp_path):
    state = AssistState(bus=TranscriptBus(), live=LiveState(), glossary="",
                        vault=None, cwd=tmp_path, knowledge=tmp_path / "нет")
    assert state.qa_allowed_dirs == (tmp_path,)
    assert state.knowledge is None


def test_run_assist_passes_knowledge_dir_to_qa_and_term_index(tmp_path, monkeypatch):
    async def done(stop):
        return None

    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "Шлюз.md").write_text("# Платёжный шлюз\n\nСервис приёма платежей.\n",
                               encoding="utf-8")
    heavy = _Heavy(monkeypatch, digester_run=done)
    _run(tmp_path, open_browser=False, port=0, knowledge_dir=str(kb))
    assert kb in heavy.qa_kwargs["allowed_dirs"]
    assert "allowed_dirs" not in heavy.digester_kwargs  # тики без инструментов
    assert heavy.digester_kwargs["kb"] is None  # старт его не ждёт
    assert len(heavy.kb) >= 1


def test_final_live_state_saved_to_recording(tmp_path, monkeypatch):
    async def done(stop):
        heavy.live.apply({"topic": "Запуск", "ops": [
            {"op": "add", "section": "decisions", "text": "Запуск в среду"}]})

    heavy = _Heavy(monkeypatch, digester_run=done)

    def fake_start():
        heavy.engine.out_dir.mkdir(parents=True)
        heavy.engine.started = True

    heavy.on_engine = lambda engine: setattr(engine, "start", fake_start)
    _run(tmp_path, open_browser=False, port=0)
    saved = json.loads((heavy.engine.out_dir / LIVE_STATE_JSON).read_text(encoding="utf-8"))
    assert saved["summary"]["topic"] == "Запуск"
    assert saved["summary"]["decisions"][0]["text"] == "Запуск в среду"


def test_empty_live_state_not_written(tmp_path, monkeypatch):
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, digester_run=done)
    heavy.on_engine = lambda engine: setattr(
        engine, "start", lambda: engine.out_dir.mkdir(parents=True))
    _run(tmp_path, open_browser=False, port=0)
    assert not (heavy.engine.out_dir / LIVE_STATE_JSON).exists()


def test_remove_endpoint_retries_while_resident_reads_it(tmp_path, monkeypatch):
    """Windows: резидент читает live.json в тот миг, когда ребёнок его
    удаляет, — sharing violation. Файл должен уйти со второй попытки, иначе
    резидент ждёт выхода процесса вместо сигнала «запись дописана»."""
    from meet.assist import app as app_mod

    ep = tmp_path / "live.json"
    ep.write_text("{}", encoding="utf-8")
    real, calls = Path.unlink, []

    def busy_once(self, missing_ok=False):
        calls.append(self)
        if len(calls) == 1:
            raise PermissionError(32, "занят другим процессом")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", busy_once)
    monkeypatch.setattr(app_mod, "REMOVE_RETRY_S", 0.01)
    app_mod.remove_endpoint(ep)
    assert len(calls) == 2 and not ep.exists()


def test_parent_check_sees_death_while_someone_holds_its_handle():
    """Оболочка держит хэндл резидента (std::process::Child): OpenProcess на
    умерший процесс при открытом хэндле удаётся. Мёртвый — по коду выхода."""
    import subprocess
    import sys

    from meet.assist import app as app_mod

    assert app_mod._pid_alive(os.getpid()) is True
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()  # объект жив — хэндл процесса открыт
    assert app_mod._pid_alive(proc.pid) is False



def test_endpoint_file_carries_device_fallback(tmp_path):
    from meet.assist import app

    path = tmp_path / "live.json"
    fallback = [{"kind": "mic", "name": "USB-микрофон", "device": "Микрофон"}]
    app.write_endpoint(path, port=5, folder=tmp_path, devices_fallback=fallback)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["devices_fallback"] == fallback
    app.write_endpoint(path, port=5, folder=tmp_path)
    assert "devices_fallback" not in json.loads(path.read_text(encoding="utf-8"))


def test_cadence_follows_assist_settings():
    from meet.assist.app import cadence_of
    from meet.assist.digester import ACTIVE, CALM, SUMMARY_ONLY

    def assist(**raw):
        return Settings.from_raw({"assist": raw}).assist

    assert cadence_of(assist()) == CALM
    assert cadence_of(assist(activity="active")) == ACTIVE
    assert cadence_of(assist(activity="summary")).hints is False
    assert cadence_of(assist(activity="summary")) == SUMMARY_ONLY
    tuned = cadence_of(assist(max_hints=3, min_words=100))
    assert (tuned.max_hints, tuned.min_words, tuned.hint_gap_s) == (3, 100, CALM.hint_gap_s)


def test_run_assist_applies_activity_tier_and_prefs(tmp_path, monkeypatch):
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, resolved=("claude-code", _never_called_runner), digester_run=done)
    cfg = Settings.from_raw({"assist": {"activity": "summary", "hints_model": "fast",
                                        "quiet_default": True}})
    from meet.assist import app as app_mod

    seen = {}
    real_state = app_mod.AssistState

    def capture(**kw):
        seen["state"] = real_state(**kw)
        return seen["state"]

    monkeypatch.setattr(app_mod, "AssistState", capture)
    _run(tmp_path, open_browser=False, port=0, cfg=cfg)
    assert heavy.digester_kwargs["call_kwargs"] == {"model": "haiku", "thinking": "disabled"}
    assert heavy.digester_kwargs["cadence"].hints is False
    assert heavy.live.hints_enabled is False
    state = seen["state"]
    assert state.view()["prefs"] == {"quiet_default": True, "activity": "summary"}
    assert '"kind"' not in state.digester_system  # сводка — без схемы подсказок


def test_model_comes_from_settings(tmp_path, monkeypatch):
    """`llm.model` доходит до живого ассистента: тики «Как у агента» и
    вопросы во время встречи идут на модели из настроек, а не на модели по
    умолчанию."""
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, resolved=("claude-code", _never_called_runner), digester_run=done)
    _run(tmp_path, open_browser=False, port=0, cfg=Settings.from_raw({"llm": {"model": "opus"}}))
    assert heavy.digester_kwargs["call_kwargs"] == {"model": "opus"}
    assert heavy.qa_kwargs["model"] == "opus"


def test_fast_ticks_keep_their_model_but_questions_use_the_configured_one(tmp_path, monkeypatch):
    async def done(stop):
        return None

    heavy = _Heavy(monkeypatch, resolved=("claude-code", _never_called_runner), digester_run=done)
    cfg = Settings.from_raw({"llm": {"model": "opus"}, "assist": {"hints_model": "fast"}})
    _run(tmp_path, open_browser=False, port=0, cfg=cfg)
    assert heavy.digester_kwargs["call_kwargs"] == {"model": "haiku", "thinking": "disabled"}
    assert heavy.qa_kwargs["model"] == "opus"
