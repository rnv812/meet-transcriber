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
