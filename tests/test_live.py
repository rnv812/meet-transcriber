from meet.live import TrackBuffer, fmt_hms, format_live_line


def test_fmt_hms_always_three_parts():
    assert fmt_hms(65.0) == "00:01:05"
    assert fmt_hms(3725.0) == "01:02:05"


def test_format_live_line():
    assert format_live_line(65.0, "Вы", "привет") == "[00:01:05] Вы: привет"


def test_track_buffer_fifo_drain():
    b = TrackBuffer()
    b.push(b"ab")
    b.push(b"cd")
    assert b.drain() == b"abcd"
    assert b.drain() == b""
