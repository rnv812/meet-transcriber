"""Ход задач модели (meet.llm_progress): оценки по прошлым вызовам, поток
текста, ход по времени, «дольше обычного», окна анализа."""

import asyncio
import json

import pytest

from meet import events, llm_progress
from meet.llm.base import AgentReply
from meet.llm_progress import REPAIRED, VALIDATED, Stats, Tracker, seed


def _bus():
    bus = events.EventBus()
    seen = []
    bus.subscribe(lambda e: seen.append(e.data) if e.kind == "progress" else None)
    return bus, seen


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _tracker(tmp_path, clock=None, **kw):
    bus, seen = _bus()
    clock = clock or Clock()
    tr = Tracker(bus, "analyze", "анализ встречи", provider="claude-code", stats=Stats(tmp_path / "s.json"),
                 clock=clock, **kw)
    llm_progress.attach(bus, tr)
    return tr, bus, seen, clock


# --- оценки --------------------------------------------------------------------


def test_stats_start_from_seed_and_learn_a_running_median(tmp_path):
    stats = Stats(tmp_path / "s.json")
    s0, out0 = seed("summary", 40_000)
    assert stats.expect("summary", "codex", 40_000) == (s0, out0, 0)
    # Codex на итогах вдвое медленнее затравки, ответы — в полтора раза длиннее.
    for _ in range(3):
        stats.record("summary", "codex", 40_000, s0 * 2, int(out0 * 1.5))
    stats.record("summary", "codex", 40_000, s0 * 50, int(out0 * 1.5))  # выброс медиану не сдвигает
    again = Stats(tmp_path / "s.json")  # с диска
    s, out, n = again.expect("summary", "codex", 40_000)
    assert n == 4 and s == pytest.approx(s0 * 2) and out == pytest.approx(out0 * 1.5, rel=0.01)
    # Другой провайдер — своя история.
    assert again.expect("summary", "claude-code", 40_000)[2] == 0


def test_stats_keep_only_recent_runs_and_survive_a_broken_file(tmp_path):
    path = tmp_path / "s.json"
    stats = Stats(path)
    for i in range(llm_progress.KEEP + 5):
        stats.record("ask", None, 1000, 10.0 + i, 500)
    assert len(json.loads(path.read_text(encoding="utf-8"))["ask:модель"]) == llm_progress.KEEP
    path.write_text("{битый", encoding="utf-8")
    assert Stats(path).expect("ask", None, 1000)[2] == 0


def test_stats_file_lives_in_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    Stats().record("ask", "codex", 100, 3.0, 50)
    assert (tmp_path / llm_progress.STATS_NAME).exists()


# --- ход вызова ---------------------------------------------------------------------


def test_time_based_progress_is_smooth_monotonic_and_never_complete(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)
    tr.started_call(20_000)
    expect = tr.call.expect_s
    for i in range(1, 300):
        clock.t = expect * i / 100
        tr.tick()
    fractions = [e["fraction"] for e in seen]
    assert fractions == sorted(fractions)
    assert all(e["fraction"] <= e["cap"] for e in seen)
    assert max(fractions) < VALIDATED  # пока модель не ответила — не «готово»
    # Половина ожидаемого — около 45 % вызова.
    half = next(e for e in seen if e["elapsed_s"] >= expect / 2)
    assert half["fraction"] == pytest.approx(VALIDATED * 0.45, abs=0.02)
    assert all(e["unit"] == "time" and e["phase"] == "request" for e in seen[1:])


def test_longer_than_usual_switches_to_slow_without_going_back(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)
    tr.started_call(20_000)
    expect = tr.call.expect_s
    clock.t = expect * 0.9
    tr.tick()
    assert seen[-1]["slow"] is False
    clock.t = expect * 1.2
    tr.tick()
    before = seen[-1]["fraction"]
    assert seen[-1]["slow"] is True and "eta_s" not in seen[-1]
    clock.t = expect * 3
    tr.tick()
    assert seen[-1]["slow"] is True and before <= seen[-1]["fraction"] < VALIDATED * 0.95 + 1e-9


def test_eta_only_when_confident(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)
    tr.started_call(20_000)
    clock.t = 5
    tr.tick()
    assert "eta_s" not in seen[-1]  # затравка без замеров — не обещаем
    for _ in range(3):
        tr.stats.record("analyze", "claude-code", 20_000, 40.0, 3000)
    tr.part(1, 1)
    clock.t = 10
    tr.started_call(20_000)
    clock.t = 20
    tr.tick()
    assert seen[-1]["eta_s"] == pytest.approx(30.0, abs=0.5)


def test_streaming_progress_follows_received_text(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)
    tr.started_call(10_000)
    expect_out = tr.call.expect_out
    clock.t = 3
    tr.text("{")
    assert seen[-1]["phase"] == "generating" and seen[-1]["unit"] == "chars"
    got = []
    piece = "x" * int(expect_out / 20)
    for i in range(1, 19):
        clock.t = 3 + i
        tr.text(piece)
        got.append(seen[-1]["fraction"])
    assert got == sorted(got) and got[-1] > got[0]
    assert got[-1] < VALIDATED
    assert "eta_s" in seen[-1]  # поток идёт дольше 2 с — оценка по его скорости
    tr.text(None)  # новое сообщение модели: полоска не откатывается
    assert seen[-1]["fraction"] >= got[-1]


def test_events_are_throttled_to_four_per_second(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)
    tr.started_call(10_000)
    n = len(seen)
    for i in range(200):  # двести кусков за секунду
        clock.t = 1 + i / 200
        tr.text("ab")
    assert len(seen) - n <= 5


def test_validating_and_repair_are_sub_steps_of_the_window(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)
    tr.started_call(10_000)
    clock.t = 10
    tr.finished_call(500, ok=True)
    assert seen[-1]["phase"] == "validating" and seen[-1]["fraction"] == pytest.approx(VALIDATED)
    tr.started_call(12_000)  # ответ битый — просим исправить
    assert seen[-1]["phase"] == "repair"
    clock.t = 30
    tr.tick()
    assert VALIDATED <= seen[-1]["fraction"] < REPAIRED
    assert seen[-1]["cap"] == pytest.approx(REPAIRED)
    tr.finished_call(500, ok=True)
    # Замер — только у первого вызова (исправление другой величины).
    assert len(tr.stats.runs(Stats.key("analyze", "claude-code"))) == 1


def test_windows_are_weighted_by_plan_and_never_go_back(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.plan([("analyze", 60_000), ("analyze", 20_000), ("analyze-final", 3000)])
    for n in (1, 2, 3):
        tr.part(n, 3, key="analyze-final" if n == 3 else None, note=f"окно {n} из 2" if n < 3 else "итог")
        tr.started_call(10_000)
        clock.t += 30
        tr.tick()
        tr.finished_call(100, ok=True)
    fractions = [e["fraction"] for e in seen]
    assert fractions == sorted(fractions)
    starts = [next(e for e in seen if e["part"] == n) for n in (1, 2, 3)]
    assert all(e["parts"] == 3 and e["phase"] == "request" for e in starts)
    w = tr.weights
    assert starts[1]["fraction"] == pytest.approx(w[0] / sum(w), abs=1e-3)  # первое окно — самое тяжёлое
    assert w[0] > w[1] > w[2]
    assert seen[1]["note"] == "окно 1 из 2"


def test_part_without_tracker_sends_the_old_coarse_event():
    bus, seen = _bus()
    llm_progress.part(bus, 2, 3, stage="analyze", label="анализ встречи")
    assert seen[-1]["done"] == 1 and seen[-1]["total"] == 3 and seen[-1]["stage"] == "analyze"
    llm_progress.part(None, 1, 1, stage="analyze", label="x")  # без шины — молча


def test_wrapped_runner_streams_through_claude_style_on_text(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)
    outer = []

    async def runner(prompt, *, system_prompt, on_text=None, **kw):
        for piece in ("а" * 50, "б" * 50):
            clock.t += 1
            on_text(piece)
        return AgentReply(text="а" * 50 + "б" * 50)

    wrapped = tr.wrap(runner)
    reply = asyncio.run(wrapped("п" * 1000, system_prompt="с", on_text=outer.append))
    assert reply.text.startswith("а")
    assert outer == ["а" * 50, "б" * 50]  # чужой on_text не потерян
    assert any(e["phase"] == "generating" for e in seen)
    assert seen[-1]["phase"] == "validating"
    runs = tr.stats.runs(Stats.key("analyze", "claude-code"))
    assert runs == [{"in": 1001, "s": 2.0, "out": 100}]


def test_wrapped_runner_without_on_text_is_called_as_before(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)

    async def runner(prompt, system_prompt=None):
        return AgentReply(text="ok")

    assert asyncio.run(tr.wrap(runner)("x", system_prompt="y")).text == "ok"
    assert seen[-1]["phase"] == "validating"


def test_failed_call_is_not_learned(tmp_path):
    tr, bus, seen, clock = _tracker(tmp_path)
    tr.part(1, 1)

    async def runner(prompt, **kw):
        return AgentReply(text="", error="таймаут вызова модели")

    asyncio.run(tr.wrap(runner)("x", system_prompt="y"))
    assert tr.stats.runs(Stats.key("analyze", "claude-code")) == []


# --- задачи целиком ---------------------------------------------------------------


def _transcribed(tmp_path, monkeypatch, n=3):
    from meet import library

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    folder = tmp_path / "rec" / "2026-10-01_10-00"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"segments": [
        {"start": float(i), "end": float(i) + 1, "speaker": "Спикер 1", "text": f"реплика {i}"}
        for i in range(n)]})
    return folder


def test_analyze_job_reports_fine_progress_with_a_fake_runner(tmp_path, monkeypatch, capsys):
    import meet.llm as llm
    from meet import job_worker

    reply = {"phrase_types": {"0": "statement"}, "importance": {"0": 0.4},
             "chapters": [{"start_i": 0, "end_i": 0, "title": "Старт", "short": "Старт"}],
             "insights": [], "category": None, "title": "Начало работы"}

    async def runner(prompt, *, on_text=None, **kwargs):
        text = json.dumps(reply, ensure_ascii=False)
        await asyncio.sleep(0.05)
        for i in range(0, len(text), 20):
            on_text(text[i:i + 20])
        return AgentReply(text=text)

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("claude-code", runner))
    folder = _transcribed(tmp_path, monkeypatch)
    assert job_worker.main(["analyze", str(folder)]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    progress = [x for x in lines if x["kind"] == "progress" and x.get("fraction") is not None]
    assert progress and {x["stage"] for x in progress} == {"analyze"}
    fractions = [x["fraction"] for x in progress]
    assert fractions == sorted(fractions)
    assert {"request", "generating", "validating"} <= {x["phase"] for x in progress}
    assert (tmp_path / "data" / llm_progress.STATS_NAME).exists()  # замер на следующий раз
    # В событиях — только числа и подписи, без текста встречи и ответа.
    assert not any("реплика" in json.dumps(x, ensure_ascii=False) for x in progress)
    assert not any("Начало работы" in json.dumps(x, ensure_ascii=False) for x in progress)


def test_summary_job_reports_progress_under_the_llm_stage(tmp_path, monkeypatch, capsys):
    import meet.llm as llm
    from meet import job_worker

    async def runner(prompt, **kwargs):
        return AgentReply(text="## Итоги\n\n- Договорились.")

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("codex", runner))
    folder = _transcribed(tmp_path, monkeypatch)
    assert job_worker.main(["summary", str(folder)]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    progress = [x for x in lines if x["kind"] == "progress" and x.get("fraction") is not None]
    assert progress and all(x["stage"] == "llm" and x["label"] == "итоги встречи" for x in progress)
    assert progress[-1]["phase"] == "validating"
