"""Дубли соседа в живой ленте (meet.live_dedupe): задержка строк людей рядом,
проверка против догнавшего sys, перепроверка показанных раньше времени."""

from meet.asr import Segment, Word
from meet.live_dedupe import HOLD_MAX_S, LiveDuplicates

LAG = 0.2  # сосед: в sys позже микрофона


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def seg(start, text, step=0.4):
    words = [Word(round(start + i * step, 3), round(start + i * step + 0.3, 3), " " + w)
             for i, w in enumerate(text.split())]
    return Segment(words[0].start, words[-1].end, text, words=words)


PHRASES = ["давайте перенесём релиз на пятницу", "бюджет согласован с финансовым отделом",
           "тестовый стенд поднимем к среде утром", "клиент просил добавить отчёт по складу",
           "демо назначим на следующую неделю вечером"]


def held_and_heard(d, t, text, role="room", extra_sys=True):
    mic = seg(t, text)
    item = {"line": text}
    d.hold(item, mic, role)
    if extra_sys:
        d.heard_sys([seg(t + LAG, text)], mic.end + 3.5)
    return item


def warm_up(d, count=3):
    """Уверенные пары соседа: до MIN_LAG_PAIRS L* нет и ничего не удаляется."""
    published = []
    for i in range(count):
        held_and_heard(d, 10.0 * i, PHRASES[i])
        published += d.release()
    return published


def test_room_copies_published_until_lag_is_known_then_dropped():
    d = LiveDuplicates(clock=Clock())
    first = warm_up(d)
    assert [it["line"] for it in first] == PHRASES[:3]  # L* ещё нет — показываем
    held_and_heard(d, 40.0, PHRASES[3])
    assert d.release() == []  # копия соседа — не показывается
    assert d.stats["dropped"] == 1 and d.pending() == 0


def test_line_waits_until_sys_catches_up():
    clock = Clock()
    d = LiveDuplicates(clock=clock)
    mic = seg(5.0, PHRASES[0])
    d.hold({"line": "x"}, mic, "unsure")
    d.heard_sys([], mic.end + 1.0)  # sys ещё не дошёл до конца строки + 3 с
    assert d.release() == []
    d.heard_sys([], mic.end + 3.1)
    out = d.release()
    assert len(out) == 1 and out[0]["due"] is True
    assert [t.text for t in out[0]["words"]] == [" " + w for w in PHRASES[0].split()]


def test_hold_is_capped_by_wall_clock_and_final():
    clock = Clock()
    d = LiveDuplicates(clock=clock)
    d.hold({"line": "a"}, seg(5.0, PHRASES[0]), "room")
    clock.t = HOLD_MAX_S - 0.1
    assert d.release() == []
    clock.t = HOLD_MAX_S
    out = d.release()
    assert len(out) == 1 and out[0]["due"] is False
    d.hold({"line": "b"}, seg(9.0, PHRASES[1]), "room")
    assert len(d.release(final=True)) == 1  # остановка — ждать некогда


def test_partial_copy_keeps_own_words():
    d = LiveDuplicates(clock=Clock())
    warm_up(d)
    mic = seg(40.0, PHRASES[3] + " а ещё про склад и логистику")
    d.hold({"line": "x"}, mic, "room")
    d.heard_sys([seg(40.0 + LAG, PHRASES[3])], mic.end + 3.5)
    out = d.release()
    assert len(out) == 1
    assert "".join(t.text for t in out[0]["words"]).strip() == "а ещё про склад и логистику"
    assert d.stats["trimmed"] == 1


def test_shown_early_copy_is_hidden_when_sys_catches_up():
    clock = Clock()
    d = LiveDuplicates(clock=clock)
    warm_up(d)
    clock.t = 100.0
    mic = seg(40.0, PHRASES[3])
    d.hold({"line": "x"}, mic, "room")
    clock.t += HOLD_MAX_S
    out = d.release()  # sys отстал — показали
    assert len(out) == 1 and out[0]["due"] is False
    d.shown(out[0])
    assert d.recheck() == []  # sys ещё не догнал
    d.heard_sys([seg(40.0 + LAG, PHRASES[3])], mic.end + 3.5)
    hidden = d.recheck()
    assert hidden == [out[0]] and d.stats["hidden"] == 1
    assert d.recheck() == []


def test_shown_line_that_is_not_a_copy_stays():
    clock = Clock()
    d = LiveDuplicates(clock=clock)
    warm_up(d)
    mic = seg(40.0, PHRASES[3])
    d.hold({"line": "x"}, mic, "room")
    clock.t += HOLD_MAX_S
    item = d.release()[0]
    d.shown(item)
    d.heard_sys([seg(40.5, "совсем другие слова собеседника тут")], mic.end + 3.5)
    assert d.recheck() == []


def test_recheck_gives_up_after_a_while():
    clock = Clock()
    d = LiveDuplicates(clock=clock)
    d.hold({"line": "x"}, seg(40.0, PHRASES[3]), "room")
    clock.t += HOLD_MAX_S
    d.shown(d.release()[0])
    clock.t += 31.0
    assert d.recheck() == [] and d._shown == []


def test_owner_lines_are_never_held():
    assert not LiveDuplicates.wants("owner", seg(0.0, PHRASES[0]))
    assert LiveDuplicates.wants("room", seg(0.0, PHRASES[0]))
    assert LiveDuplicates.wants("unsure", seg(0.0, PHRASES[0]))
    assert not LiveDuplicates.wants("room", Segment(0.0, 1.0, "без слов"))  # Whisper без слов


def test_sys_ring_keeps_last_minute():
    d = LiveDuplicates(clock=Clock())
    d.heard_sys([seg(0.0, "старое слово")], 5.0)
    d.heard_sys([seg(100.0, "новое слово")], 110.0)
    assert [t.text for t in d._sys] == [" новое", " слово"]


def test_envelope_frames_grow_with_audio():
    import numpy as np

    d = LiveDuplicates(clock=Clock())
    d.sound("mic", 1.0, np.full(16000, 0.5, dtype=np.float32))
    d.sound("mic", 0.0, np.full(8000, 0.25, dtype=np.float32))
    amp = d._amp["mic"].view()
    assert len(amp) == 100  # до 2 с по 20 мс
    assert abs(float(amp[0]) - 0.25) < 1e-6 and abs(float(amp[60]) - 0.5) < 1e-6
    assert float(amp[30]) == 0.0  # дыра — тишина
