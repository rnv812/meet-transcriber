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
