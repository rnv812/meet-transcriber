"""Ассистент, включённый посреди записи, глазами `meet assist`: догнанное
начало встречи в тикере и вопросах, пометка «неполная» в сводке,
продолжение прошлого включения, остановка «Выключить ассистента», ход
догонялки в `state`, запуск в режиме подключения.

Модель, звук и резидент — поддельные; реплики выдуманы."""

import asyncio
import json
import threading
import time
import urllib.request
from pathlib import Path

from meet.assist.agent import AgentReply
from meet.assist.app import AssistState, _attached_reasons, _heard_end, _prior_entries
from meet.assist.bus import TranscriptBus, chronological
from meet.assist.live_state import LIVE_STATE_JSON, LiveState, covered_note, load_saved
from meet.settings import Settings

from test_assist_app import _Heavy, _run
from test_assist_digester import HINT, NONE, SUMMARY, FakeDialogue, _make, _publish


def _catchup(bus, t, speaker, text, dur=4.0):
    h, m, s = int(t) // 3600, int(t) % 3600 // 60, int(t) % 60
    bus.publish(f"[{h:02d}:{m:02d}:{s:02d}] {speaker}: {text}",
                {"t": float(t), "end": float(t) + dur, "speaker": speaker, "text": text,
                 "catchup": True})


# --- тикер ----------------------------------------------------------------------


def test_catchup_lines_never_tick_hints_or_trigger_them():
    hints = FakeDialogue([AgentReply(text=NONE)] * 5)
    bus, state, d = _make([AgentReply(text=SUMMARY)], hints=hints)
    # Начало встречи: длинная речь и вопрос к владельцу — но это прошлое.
    for i in range(12):
        _catchup(bus, i * 5, "Демьян", f"Вы, Кузьма, согласны со сроком номер {i}?")
    d._scan()
    assert d._trigger is None
    assert not d.hints_due()
    assert d.pending_words() == 0
    # Сводка начало встречи учитывает.
    assert d.summary_due() or d.summary_due(now=1e9)


def test_hints_lane_skips_catchup_but_summary_takes_it():
    hints = FakeDialogue([AgentReply(text=HINT)] * 3)
    calls = []
    bus, state, d = _make([AgentReply(text=SUMMARY)], hints=hints, calls=calls)
    _publish(bus, 600, "Демьян", "Живая реплика про запуск в среду и сроки", dur=25)
    _catchup(bus, 10, "Демьян", "Начало встречи: обсуждали бюджет проекта")
    asyncio.run(d.hints_once())
    assert "Живая реплика" in hints.sent[0] and "бюджет проекта" not in hints.sent[0]
    assert d.hints.cursor == 2  # курсор прошёл и догнанную строку
    asyncio.run(d.summary_once())
    assert "бюджет проекта" in calls[0][0] and "Живая реплика" in calls[0][0]


def test_seed_history_is_chronological_with_catchup():
    bus, state, d = _make()
    _publish(bus, 600, "Демьян", "живое потом")
    _catchup(bus, 10, "Демьян", "начало сначала")
    earlier, recent = d._history(2)
    joined = earlier + recent
    assert joined.index("[00:00:10] Демьян: начало сначала") < joined.index("[00:10:00] Демьян: живое потом")


def test_skip_existing_makes_prior_lines_context_only():
    bus, state, d = _make()
    _catchup(bus, 10, "Демьян", "лента прошлого включения ассистента")
    d.skip_existing()
    assert d.hints.cursor == d.summary.cursor == 1
    assert not d.summary_due(now=1e9)


def test_chronological_keeps_live_order_without_catchup():
    lines = ["b", "a"]
    entries = [{"t": 5.0}, {"t": 1.0}]
    assert chronological(lines, entries) == (lines, entries)  # живые — как пришли
    entries = [{"t": 5.0}, {"t": 1.0, "catchup": True}]
    assert chronological(lines, entries)[0] == ["a", "b"]


def test_quick_answers_see_the_meeting_in_time_order():
    from meet.assist.qa import QAService

    bus, live = TranscriptBus(), LiveState()
    _publish(bus, 600, "Демьян", "живое потом")
    _catchup(bus, 10, "Демьян", "начало сначала")
    qa = QAService(bus, live, system_prompt="s", allowed_dirs=(), cwd=Path("."),
                   runner=None, owner="Вы")
    prompt, _ = qa._build("Кратко", "brief", None)
    assert prompt.index("начало сначала") < prompt.index("живое потом")


# --- сводка: пометки и продолжение -----------------------------------------------


def test_partial_summary_says_where_it_starts_and_that_it_is_incomplete(tmp_path):
    live = LiveState()
    live.apply({"topic": "Запуск", "ops": [{"op": "add", "section": "decisions",
                                           "text": "Запуск в среду"}]})
    live.heard_from(1800.0)
    live.mark_covered(2400.0)
    live.partial = True
    live.save(tmp_path / LIVE_STATE_JSON)
    saved = load_saved(tmp_path)
    assert saved["covered_from"] == 1800.0 and saved["partial"] is True
    md = saved["markdown"]
    assert "с [00:30:00] до [00:40:00]" in md and "не целиком" in md
    # Полная (догнали с начала, не выключали) — прежняя пометка.
    assert covered_note(2400.0, 0.0) == ("_Сводка учитывает реплики до [00:40:00]; "
                                         "более поздние — только в расшифровке._")
    assert covered_note(None) == ""


def test_resume_continues_a_saved_state_with_fresh_ids(tmp_path):
    first = LiveState()
    first.apply({"topic": "Запуск", "ops": [
        {"op": "add", "section": "points", "text": "Сроки"},
        {"op": "add", "section": "hints", "kind": "risk", "text": "Нет ответственного",
         "why": "", "t": "00:00:05"}]})
    first.heard_from(600.0)
    first.mark_covered(900.0)
    first.partial = True
    first.save(tmp_path / LIVE_STATE_JSON)
    second = LiveState()
    assert second.resume(load_saved(tmp_path))
    assert second.topic == "Запуск" and [h["id"] for h in second.hints()] == ["h1"]
    assert second.covered_from == 600.0 and second.covered_t == 900.0 and second.partial
    second.apply({"ops": [{"op": "add", "section": "points", "text": "Бюджет"}]})
    assert [p["id"] for p in second.summary()["points"]] == ["p1", "p2"]
    assert not LiveState().resume({"summary": "мусор"})


def test_prior_transcript_becomes_context_entries(tmp_path):
    path = tmp_path / "live_transcript.md"
    path.write_text("[00:01:00] Вы: раз\n<!-- ошибка окна -->\n[00:02:05] Демьян: два: три\n",
                    encoding="utf-8")
    entries = _prior_entries(path)
    assert [e for _, e in entries] == [
        {"t": 60.0, "speaker": "Вы", "text": "раз", "catchup": True},
        {"t": 125.0, "speaker": "Демьян", "text": "два: три", "catchup": True}]
    assert _prior_entries(tmp_path / "нет.md") == []


class _Engine:
    def __init__(self, progress):
        self._progress = progress

    def catchup_progress(self):
        return self._progress


def _state(tmp_path):
    return AssistState(bus=TranscriptBus(), live=LiveState(), glossary="", vault=None,
                       cwd=tmp_path)


def test_partial_marking_rules(tmp_path):
    state, live = _state(tmp_path), LiveState()
    complete = {"active": False, "complete": True}
    live.heard_from(0.0)
    assert _attached_reasons(live, state, _Engine(complete), {"capped": False}) == []
    assert _attached_reasons(live, state, _Engine({"active": False, "complete": False}),
                             None) == ["catchup_incomplete"]
    assert _attached_reasons(live, state, _Engine(complete), {"capped": True}) == ["capped"]
    late = LiveState()
    late.heard_from(1200.0)
    assert _attached_reasons(late, state, _Engine(None), None) == ["late_start"]
    state.mark_detached()
    assert _attached_reasons(live, state, _Engine(complete), {"capped": False}) == ["detached"]


def test_reattach_keeps_holes_a_gap_catchup_cannot_fill(tmp_path):
    """Первое включение не догнало начало (или упёрлось в 30 минут) — следующее
    догоняет только после услышанного: та дыра остаётся, сводка — неполной.
    «Выключили» чинится: дыру после выключения новое включение догоняет."""
    state = _state(tmp_path)
    complete = {"active": False, "complete": True}
    first = LiveState()
    first.heard_from(0.0)
    first.partial_reasons = ["catchup_incomplete", "detached"]
    first.partial = True
    first.mark_covered(300.0)
    first.save(tmp_path / LIVE_STATE_JSON)
    again = LiveState()
    again.resume(load_saved(tmp_path))
    assert again.partial_reasons == ["catchup_incomplete", "detached"]
    reasons = _attached_reasons(again, state, _Engine(complete), {"capped": False})
    assert reasons == ["catchup_incomplete"]


def test_heard_end_is_the_end_of_the_last_line():
    bus = TranscriptBus()
    _publish(bus, 100, "Демьян", "раз", dur=6.5)
    _catchup(bus, 10, "Демьян", "начало")
    assert _heard_end(bus, None) == 106.5
    assert _heard_end(bus, 200.0) == 200.0
    assert _heard_end(TranscriptBus(), None) is None


# --- state: ход догонялки --------------------------------------------------------


def test_state_view_carries_catchup_progress_and_signature_follows_it(tmp_path):
    state = _state(tmp_path)
    assert "catchup" not in state.view() and state.catchup_view() is None
    progress = {"active": True, "done_s": 30.0, "total_s": 120.0, "from_t": 0.0,
                "to_t": 120.0, "capped": False, "complete": False}
    state.catchup = lambda: progress
    sig = state.signature()
    assert state.view()["catchup"] == {"active": True, "percent": 25, "from_t": 0.0,
                                       "to_t": 120.0, "capped": False, "complete": False}
    progress.update(done_s=60.0)
    assert state.signature() != sig
    progress.update(active=False, done_s=120.0, complete=True)
    assert state.view()["catchup"]["percent"] == 100 and not state.view()["catchup"]["active"]


def test_stop_route_with_detach_marks_the_state(tmp_path):
    from aiohttp import web

    from meet.assist.web import build_app

    state = _state(tmp_path)
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box = {}

    def serve():
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(build_app(state))
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        box["port"] = runner.addresses[0][1]
        ready.set()
        loop.run_forever()
        loop.run_until_complete(runner.cleanup())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(10)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        plain = urllib.request.Request(f"http://127.0.0.1:{box['port']}/stop", data=b"{}",
                                       method="POST", headers={"Content-Type": "application/json"})
        with opener.open(plain, timeout=10) as r:
            assert json.loads(r.read()) == {"ok": True}
        assert state.detached is False
        detach = urllib.request.Request(f"http://127.0.0.1:{box['port']}/stop",
                                        data=b'{"detach": true}', method="POST",
                                        headers={"Content-Type": "application/json"})
        with opener.open(detach, timeout=10) as r:
            assert json.loads(r.read()) == {"ok": True}
        assert state.detached is True
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


def test_threadsafe_stop_before_the_loop_is_remembered(tmp_path):
    state = _state(tmp_path)
    state.request_stop_threadsafe()
    assert state._stop_early is True


# --- run_assist в режиме подключения ----------------------------------------------


def test_run_assist_attached_uses_the_recording_folder_and_tap(tmp_path, monkeypatch):
    folder = tmp_path / "rec" / "2026-10-03_09-00"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    # Прошлое включение ассистента в этой записи: его сводка и лента.
    prior = LiveState()
    prior.apply({"topic": "Запуск", "ops": [{"op": "add", "section": "points", "text": "Сроки"}]})
    prior.save(folder / LIVE_STATE_JSON)
    (folder / "live_transcript.md").write_text("[00:01:00] Вы: прошлое\n", encoding="utf-8")

    async def stop_soon(stop):
        await asyncio.sleep(0.05)

    heavy = _Heavy(monkeypatch, digester_run=stop_soon)
    captured = {}

    def on_engine(engine):
        captured["engine"] = engine
        engine.attach_positions = {"sys.wav": 300.0, "mic.wav": 300.0}
        engine.catchup_progress = lambda: {"active": False, "done_s": 240.0, "total_s": 240.0,
                                           "from_t": 60.0, "to_t": 300.0, "capped": False,
                                           "complete": True}

        def start_catchup(tracks, info=None):
            captured["catchup"] = (tracks, info)
            return True

        engine.start_catchup = start_catchup

    heavy.on_engine = on_engine
    published = []
    real_publish = TranscriptBus.publish
    monkeypatch.setattr(TranscriptBus, "publish",
                        lambda self, line, entry=None: (published.append(entry),
                                                        real_publish(self, line, entry))[1])

    class Digester:
        status = None

        def __init__(self, bus, live, **kw):
            heavy.live = live
            self.skipped = False

        def set_system_prompt(self, *a):
            pass

        def skip_existing(self):
            captured["skipped"] = True

        async def run(self, stop):
            await stop_soon(stop)

        def close(self):
            pass

    monkeypatch.setattr("meet.assist.app.Digester", Digester)
    _run(tmp_path, open_browser=False, port=0, attach_to=str(folder), tap_port=12345,
         tap_token="t" * 32, cfg=Settings.from_raw({}))
    engine = captured["engine"]
    assert engine.out_dir == folder  # не новая датированная папка
    assert callable(engine.kw["tap_connect"]) and callable(engine.kw["on_source_end"])
    tracks, info = captured["catchup"]
    # Догоняем только то, чего прошлая лента не слышала: с 60 до 300 с.
    assert tracks["sys.wav"] == (folder / "sys.opus", 60.0, 300.0)
    assert captured["skipped"] is True
    assert published[0] == {"t": 60.0, "speaker": "Вы", "text": "прошлое", "catchup": True}
    saved = json.loads((folder / LIVE_STATE_JSON).read_text(encoding="utf-8"))
    assert saved["summary"]["topic"] == "Запуск"  # прошлая сводка продолжена
    # Прошлое включение слышало начало, дыру догнали целиком — сводка полная.
    assert "partial" not in saved and saved["covered_from"] == 0.0


def test_run_assist_attached_refuses_without_a_folder_or_tap(tmp_path, monkeypatch):
    import pytest

    _Heavy(monkeypatch)
    with pytest.raises(SystemExit, match="Папка записи не найдена"):
        _run(tmp_path, open_browser=False, port=0, attach_to=str(tmp_path / "нет"),
             tap_port=1, tap_token="t")
    (tmp_path / "есть").mkdir()
    with pytest.raises(SystemExit, match="отвода звука"):
        _run(tmp_path, open_browser=False, port=0, attach_to=str(tmp_path / "есть"))


# --- CLI ------------------------------------------------------------------------


def test_cli_attach_and_detach_go_through_the_resident(monkeypatch, capsys):
    from meet import cli, control

    calls = []

    def request(path, method="GET", payload=None, **kw):
        calls.append((path, method))
        if path == "/live/attach":
            return {"ok": True, "folder": "D:/rec/2026-10-03_09-00", "attached": True}
        return {"ok": True, "action": "stopping"}

    monkeypatch.setattr(control, "request", request)
    assert cli.main(["assist", "--attach"]) == 0
    assert "догонит уже записанное" in capsys.readouterr().out
    assert cli.main(["assist", "--detach"]) == 0
    assert "запись продолжается" in capsys.readouterr().out
    assert calls == [("/live/attach", "POST"), ("/live/detach", "POST")]


def test_cli_child_mode_passes_attach_and_token_from_env(monkeypatch):
    from meet import cli
    from meet.assist import app as app_mod

    seen = {}
    monkeypatch.setattr(app_mod, "run_assist", lambda *a, **kw: seen.update(kw))
    monkeypatch.setenv("MEET_TAP_TOKEN", "a" * 32)
    cli.main(["assist", "--no-browser", "--port", "0", "--attach-to", "D:/rec/f",
              "--tap-port", "4567"])
    assert seen["attach_to"] == "D:/rec/f" and seen["tap_port"] == 4567
    assert seen["tap_token"] == "a" * 32
    # Токен ушёл из окружения: процессы модели и их инструменты его не наследуют.
    import os

    assert "MEET_TAP_TOKEN" not in os.environ
    seen.clear()
    cli.main(["assist", "--no-browser", "--port", "0"])
    assert seen["attach_to"] is None and seen["tap_token"] is None


def test_status_shows_the_attached_assistant(monkeypatch, capsys):
    from meet import cli, control

    monkeypatch.setattr(control, "request", lambda path, **kw: {
        "status": "recording", "source": "manual", "folder": "D:/rec/f", "elapsed_s": 75,
        "live": {"active": True, "attached": True, "folder": "D:/rec/f",
                 "started_at": time.time() - 75},
        "auto_record": {}, "recordings_dir": "D:/rec"})
    cli.print_status()
    out = capsys.readouterr().out
    assert "Идёт запись (вручную): D:/rec/f" in out and "Ассистент: слушает запись" in out
    assert "Идёт запись с ассистентом" not in out


def test_reattach_catches_up_from_the_end_of_the_last_heard_line(tmp_path, monkeypatch):
    """Конец последней реплики прошлого включения — в live_state.json; новое
    включение догоняет с него, а не с её начала (без повтора реплики)."""
    folder = tmp_path / "rec" / "2026-10-03_09-00"
    folder.mkdir(parents=True)
    for name in ("sys.opus", "mic.opus"):
        (folder / name).write_bytes(b"x")
    prior = LiveState()
    prior.heard_from(0.0)
    prior.heard_t = 66.5
    prior.save(folder / LIVE_STATE_JSON)
    (folder / "live_transcript.md").write_text("[00:01:00] Вы: прошлое\n", encoding="utf-8")

    async def stop_soon(stop):
        await asyncio.sleep(0.05)

    heavy = _Heavy(monkeypatch, digester_run=stop_soon)
    captured = {}

    def on_engine(engine):
        engine.attach_positions = {"mic.wav": 300.0}
        engine.catchup_progress = lambda: None
        engine.start_catchup = lambda tracks, info=None: captured.update(tracks=tracks)

    heavy.on_engine = on_engine
    _run(tmp_path, open_browser=False, port=0, attach_to=str(folder), tap_port=1,
         tap_token="t" * 32)
    assert captured["tracks"]["mic.wav"][1:] == (66.5, 300.0)
    saved = json.loads((folder / LIVE_STATE_JSON).read_text(encoding="utf-8"))
    assert saved["heard_t"] == 66.5 and "partial" not in saved


def test_status_of_a_recording_with_assistant_shows_its_stage(monkeypatch, capsys):
    from meet import cli, control

    monkeypatch.setattr(control, "request", lambda path, **kw: {
        "status": "recording", "source": "live", "folder": "D:/rec/f", "elapsed_s": 5,
        "live": {"active": True, "ready": False, "attached": True, "folder": "D:/rec/f",
                 "stage": "загружаю модель распознавания…", "started_at": time.time() - 5},
        "auto_record": {}, "recordings_dir": "D:/rec"})
    cli.print_status()
    out = capsys.readouterr().out
    assert "Идёт запись (с ассистентом): D:/rec/f" in out
    assert "Ассистент: запускается (загружаю модель распознавания…)" in out
