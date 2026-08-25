import json

from meet import events


def test_emit_reaches_subscriber():
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    bus.emit(events.LOG, text="привет")
    assert [(e.kind, e.data["text"]) for e in seen] == [("log", "привет")]


def test_event_carries_wall_clock_time():
    """Стенное время, а не монотонное: события сопоставляются с record.log."""
    before = __import__("time").time()
    e = events.Event(events.LOG, {"text": "x"})
    assert e.at >= before


def test_unsubscribe_stops_delivery():
    bus = events.EventBus()
    seen = []
    off = bus.subscribe(seen.append)
    bus.emit(events.LOG, text="1")
    off()
    bus.emit(events.LOG, text="2")
    assert len(seen) == 1


def test_unsubscribe_twice_is_harmless():
    """SSE может отписаться и по разрыву, и по завершению хендлера."""
    bus = events.EventBus()
    off = bus.subscribe(lambda e: None)
    off()
    off()


def test_subscriber_error_does_not_break_publisher():
    """Закрытая вкладка или сломанный файл журнала не должны ронять запись."""
    bus = events.EventBus()
    delivered = []

    def boom(event):
        raise RuntimeError("подписчик сломался")

    bus.subscribe(boom)
    bus.subscribe(delivered.append)
    bus.emit(events.RECORD_STARTED, folder="x")
    assert len(delivered) == 1
    assert bus.failures == 1


def test_no_subscribers_is_cheap_noop():
    bus = events.EventBus()
    assert bus.emit(events.LOG, text="в никуда").kind == "log"


def test_progress_adds_human_label():
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    bus.progress("diarize")
    (event,) = seen
    assert event.data["stage"] == "diarize"
    assert event.data["label"] == events.STAGE_LABELS["diarize"]
    # шкалы у диаризации нет — выдуманный прогресс хуже отсутствующего
    assert event.data["done"] is None and event.data["total"] is None


def test_progress_carries_counters():
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    bus.progress("asr", done=1, total=2, note="sys")
    assert seen[0].data["done"] == 1 and seen[0].data["note"] == "sys"


def test_to_json_keeps_cyrillic_readable():
    line = events.Event(events.LOG, {"text": "дорожка"}).to_json()
    assert "дорожка" in line
    assert json.loads(line)["kind"] == "log"


def test_jsonl_sink_writes_lines(tmp_path):
    sink = events.JsonlSink(tmp_path / "events.jsonl")
    sink(events.Event(events.RECORD_STARTED, {"folder": "x"}))
    sink(events.Event(events.LOG, {"text": "строка"}))
    sink.close()
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["kind"] for x in lines] == ["record.started", "log"]


def test_jsonl_sink_skips_levels(tmp_path):
    """Уровни идут дважды в секунду — это данные метра, а не истории."""
    path = tmp_path / "events.jsonl"
    sink = events.JsonlSink(path)
    sink(events.Event(events.RECORD_LEVEL, {"levels": {"mic.opus": 0.5}}))
    sink(events.Event(events.LOG, {"text": "видно"}))
    sink.close()
    assert "record.level" not in path.read_text(encoding="utf-8")


def test_jsonl_sink_survives_unwritable_path(tmp_path):
    """Журнал не должен ронять запись, даже если файл открыть нельзя."""
    busy = tmp_path / "dir"
    busy.mkdir()
    sink = events.JsonlSink(busy)  # путь — папка, open() упадёт
    sink(events.Event(events.LOG, {"text": "x"}))
    sink.close()


def test_jsonl_sink_after_close_is_silent(tmp_path):
    sink = events.JsonlSink(tmp_path / "events.jsonl")
    sink.close()
    sink(events.Event(events.LOG, {"text": "поздно"}))


def test_stages_cover_pipeline_order():
    assert events.STAGES == (
        "convert", "asr", "align", "diarize", "voices", "render"
    )
