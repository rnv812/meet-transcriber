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
