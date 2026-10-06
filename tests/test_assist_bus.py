from meet.assist.bus import TranscriptBus


def test_since_returns_new_lines_and_cursor():
    bus = TranscriptBus()
    bus.publish("a")
    bus.publish("b")
    lines, cur = bus.since(0)
    assert lines == ["a", "b"] and cur == 2
    lines, cur = bus.since(cur)
    assert lines == [] and cur == 2
    bus.publish("c")
    assert bus.since(cur) == (["c"], 3)
    assert bus.size() == 3


def test_entries_carry_structure_next_to_lines():
    bus = TranscriptBus()
    bus.publish("[00:00:05] Вы: привет",
                {"t": 5.2, "speaker": "Вы", "text": "привет"})
    bus.publish("строка без структуры")
    entries, cur = bus.entries_since(0)
    assert cur == 2
    assert entries[0] == {"t": 5.2, "speaker": "Вы", "text": "привет"}
    # Строка без структуры отдаётся текстом, а не теряется.
    assert entries[1] == {"t": None, "speaker": "", "text": "строка без структуры"}
    assert bus.since(0) == (["[00:00:05] Вы: привет", "строка без структуры"], 2)
    assert bus.entries_since(1) == ([entries[1]], 2)


def test_publish_from_another_thread_wakes_a_waiter_quickly():
    """Сигнал изменений: реплика из потока распознавания будит ждущих (тикер,
    SSE) сразу, а не на следующем опросе."""
    import asyncio
    import threading

    async def scenario():
        bus = TranscriptBus()
        loop = asyncio.get_running_loop()
        bus.changed.bind(loop)
        seen = bus.changed.seq
        started = loop.time()
        threading.Timer(0.05, lambda: bus.publish("[00:00:01] Ольга: привет")).start()
        got = await bus.changed.wait(seen, timeout=5)
        return got, loop.time() - started

    got, took = asyncio.run(scenario())
    assert got == 1 and took < 0.5


def test_wait_returns_at_once_when_something_changed_since_seen():
    import asyncio

    from meet.assist.notify import Notifier

    async def scenario():
        n = Notifier()
        n.notify()  # до ожидания (и до привязки к циклу)
        return await n.wait(0, timeout=5), await n.wait(1, timeout=0.05)

    assert asyncio.run(scenario()) == (1, 1)


# --- голоса задним числом (С3, §4.3) -----------------------------------------------


def _voiced(bus, t, speaker, text, voice):
    return bus.publish(f"[00:00:{t:02d}] {speaker}: {text}",
                       {"t": float(t), "speaker": speaker, "text": text, "voice": voice})


def test_publish_returns_line_number():
    bus = TranscriptBus()
    assert bus.publish("a") == 0
    assert _voiced(bus, 1, "Собеседник", "б", "sys:0") == 1


def test_relabel_rewrites_lines_and_entries_of_that_voice_only():
    bus = TranscriptBus()
    _voiced(bus, 1, "Собеседник", "привет", "sys:0")
    _voiced(bus, 2, "Собеседник", "да", "sys:1")
    bus.publish("[00:00:03] Вы: ок", {"t": 3.0, "speaker": "Вы", "text": "ок"})
    before, _ = bus.entries_since(0)
    seen = bus.changed.seq
    assert bus.relabel("sys:0", "Демьян") == 1
    assert bus.changed.seq > seen
    lines, _ = bus.since(0)
    assert lines == ["[00:00:01] Демьян: привет", "[00:00:02] Собеседник: да", "[00:00:03] Вы: ок"]
    entries, _ = bus.entries_since(0)
    assert entries[0] == {"t": 1.0, "speaker": "Демьян", "text": "привет", "voice": "sys:0"}
    assert before[0]["speaker"] == "Собеседник"  # прежняя запись не правится на месте
    assert bus.voices() == (1, {"sys:0": "Демьян"}, [])


def test_relabel_same_speaker_is_noop():
    bus = TranscriptBus()
    _voiced(bus, 1, "Собеседник", "привет", "sys:0")
    bus.relabel("sys:0", "Демьян")
    assert bus.relabel("sys:0", "Демьян") == 0
    assert bus.voices()[0] == 1


def test_line_published_after_relabel_gets_current_name():
    bus = TranscriptBus()
    bus.relabel("mic:1", "Собеседник рядом")
    _voiced(bus, 4, "Вы", "реплика", "mic:1")  # строка ждала (дубли) — подпись старая
    lines, _ = bus.since(0)
    entries, _ = bus.entries_since(0)
    assert lines == ["[00:00:04] Собеседник рядом: реплика"]
    assert entries[0]["speaker"] == "Собеседник рядом"


def test_hide_marks_lines_and_bumps_rev():
    bus = TranscriptBus()
    for t in range(3):
        bus.publish(f"l{t}")
    assert bus.hide([1]) is True
    assert bus.hide([1]) is False  # уже спрятана
    assert bus.hide([7]) is False  # такой нет
    assert bus.voices() == (1, {}, [1])
    assert bus.hidden() == {1}
    assert bus.size() == 3  # номера строк не сдвигаются


def test_relabel_line_keeps_other_lines():
    from meet.assist.bus import relabel_line

    assert relabel_line("[00:01:02] Собеседник: да: нет", "Собеседник", "Демьян") == "[00:01:02] Демьян: да: нет"
    assert relabel_line("<!-- ошибка -->", "Собеседник", "Демьян") == "<!-- ошибка -->"
    assert relabel_line("[00:01:02] Вы: да", "Собеседник", "Демьян") == "[00:01:02] Вы: да"


def test_each_bus_has_its_own_session_and_visible_lines():
    a, b = TranscriptBus(), TranscriptBus()
    assert a.session and a.session != b.session
    for t in range(3):
        a.publish(f"l{t}")
    a.hide([1])
    assert a.visible_since(0) == ["l0", "l2"]
    assert a.visible_since(2) == ["l2"]
