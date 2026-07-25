import numpy as np

from meet.asr import Segment
from meet.live import LiveEngine, TrackBuffer, fmt_hms, format_live_line


class FakeTranscriber:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.offsets = []
        self.initial_prompts = []

    def transcribe_window(self, audio, *, offset_s=0.0, hotwords=None, initial_prompt=None):
        self.offsets.append(round(offset_s, 3))
        self.initial_prompts.append(initial_prompt)
        rel = self.scripted.pop(0) if self.scripted else []
        return [
            Segment(
                s.start + offset_s,
                s.end + offset_s,
                s.text,
                no_speech_prob=s.no_speech_prob,
                avg_logprob=s.avg_logprob,
            )
            for s in rel
        ]


def _one_second_2ch_48k() -> bytes:
    return (np.zeros(48000 * 2, dtype=np.int16) + 1000).tobytes()


class FakeClock:
    """Deterministic monotonic clock: returns the next value from a scripted
    sequence on each call. The first process_window tick reads the clock twice
    (t0 + now), every later tick reads it once (now)."""

    def __init__(self, ticks):
        self._ticks = list(ticks)

    def __call__(self):
        return self._ticks.pop(0)


def test_process_window_writes_and_advances_offset(tmp_path):
    scripted = [
        [Segment(0.0, 0.5, "привет", no_speech_prob=0.1, avg_logprob=-0.3)],
        [Segment(0.2, 0.4, "как дела", no_speech_prob=0.1, avg_logprob=-0.3)],
    ]
    fake = FakeTranscriber(scripted)
    # Tick 1 reads t0=100, now=101; tick 2 reads now=105.
    clock = FakeClock([100.0, 101.0, 105.0])
    engine = LiveEngine(tmp_path, fake, window_seconds=20.0, clock=clock)
    engine.register_track("sys.wav", rate=48000, channels=2, normalize=True)
    buf = engine._tracks["sys.wav"]["buffer"]

    buf.push(_one_second_2ch_48k())
    engine.process_window()
    buf.push(_one_second_2ch_48k())
    engine.process_window()

    lines = (tmp_path / "live_transcript.md").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "[00:00:00] Собеседник: привет",
        "[00:00:01] Собеседник: как дела",
    ]
    # Wall-clock offsets, lagged one tick: tick 1 → 0.0 (t0), tick 2 → 101-100 = 1.0.
    assert fake.offsets == [0.0, 1.0]


def test_process_window_aligns_tracks_in_same_tick(tmp_path):
    """Both tracks processed in one tick must get the SAME offset_s, even when
    one track's pushed audio is much shorter than the other's."""
    fake = FakeTranscriber([])
    # Tick 1 reads t0=50, now=60; tick 2 reads now=80.
    clock = FakeClock([50.0, 60.0, 80.0])
    engine = LiveEngine(tmp_path, fake, window_seconds=20.0, clock=clock)
    engine.register_track("sys.wav", rate=48000, channels=2, normalize=False)
    engine.register_track("mic.wav", rate=48000, channels=2, normalize=False)

    # First tick drains nothing → t0/tick_origin established, no offsets recorded.
    engine.process_window()
    assert fake.offsets == []

    # Second tick: push very different amounts of audio to each track.
    engine._tracks["sys.wav"]["buffer"].push((np.zeros(48000 * 2 * 5, dtype=np.int16) + 1000).tobytes())
    engine._tracks["mic.wav"]["buffer"].push((np.zeros(48000 * 2, dtype=np.int16) + 1000).tobytes())
    engine.process_window()

    # Both transcribe_window calls in this tick share the origin → same offset.
    assert len(fake.offsets) == 2
    assert fake.offsets[0] == fake.offsets[1] == 10.0  # previous tick: 60 - 50


def test_process_window_drops_hallucinations(tmp_path):
    # денилист, а не порог по no_speech_prob: та метрика относится ко всему окну
    # декодирования и по ней больше не фильтруем (см. drop_hallucinations)
    scripted = [[Segment(0.0, 0.5, "Спасибо за просмотр!", no_speech_prob=0.2, avg_logprob=-0.3)]]
    fake = FakeTranscriber(scripted)
    engine = LiveEngine(tmp_path, fake)
    engine.register_track("sys.wav", rate=16000, channels=1, normalize=False)
    engine._tracks["sys.wav"]["buffer"].push((np.zeros(16000, dtype=np.int16) + 1000).tobytes())

    engine.process_window()

    transcript = tmp_path / "live_transcript.md"
    assert not transcript.exists()


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


def test_process_window_feeds_previous_tail_as_initial_prompt(tmp_path):
    scripted = [
        [Segment(0.0, 0.5, "первое окно", no_speech_prob=0.1, avg_logprob=-0.3)],
        [Segment(0.2, 0.4, "второе окно", no_speech_prob=0.1, avg_logprob=-0.3)],
    ]
    fake = FakeTranscriber(scripted)
    engine = LiveEngine(tmp_path, fake, window_seconds=20.0)
    engine.register_track("sys.wav", rate=48000, channels=2, normalize=False)
    buf = engine._tracks["sys.wav"]["buffer"]

    buf.push(_one_second_2ch_48k())
    engine.process_window()
    buf.push(_one_second_2ch_48k())
    engine.process_window()

    assert fake.initial_prompts == [None, "первое окно"]


def test_on_line_callback_receives_lines(tmp_path):
    fake = FakeTranscriber([[Segment(0.0, 0.5, "привет", no_speech_prob=0.1, avg_logprob=-0.3)]])
    got: list[str] = []
    engine = LiveEngine(tmp_path, fake, on_line=got.append)
    engine.register_track("mic.wav", rate=48000, channels=2, normalize=False)
    engine._tracks["mic.wav"]["buffer"].push(_one_second_2ch_48k())
    engine.process_window()
    assert got and got[0].endswith("Вы: привет")


def test_on_line_error_does_not_break_window(tmp_path):
    def boom(line):
        raise RuntimeError("consumer failed")

    fake = FakeTranscriber([[Segment(0.0, 0.5, "привет", no_speech_prob=0.1, avg_logprob=-0.3)]])
    engine = LiveEngine(tmp_path, fake, on_line=boom)
    engine.register_track("mic.wav", rate=48000, channels=2, normalize=False)
    engine._tracks["mic.wav"]["buffer"].push(_one_second_2ch_48k())
    engine.process_window()  # не должен упасть
    assert (tmp_path / "live_transcript.md").read_text(encoding="utf-8")


class FakeMatcher:
    def __init__(self, names):
        self.names = names  # список ответов name_for по порядку вызовов
        self.slices = []    # длины полученных кусков аудио (в сэмплах)

    def load(self):
        pass

    def name_for(self, audio):
        self.slices.append(len(audio))
        return self.names.pop(0) if self.names else None


def test_identify_track_uses_matcher_name(tmp_path):
    fake = FakeTranscriber([[Segment(0.5, 2.5, "тезис про токены")]])
    matcher = FakeMatcher(["Григорий Лебедев"])
    engine = LiveEngine(tmp_path, fake, voice_matcher=matcher)
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(
        np.zeros(16000 * 3, dtype=np.int16).tobytes()
    )
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Григорий Лебедев: тезис про токены" in text
    assert "Собеседник" not in text
    # матчеру ушёл кусок аудио примерно длины сегмента (2 с) на 16 кГц
    assert 16000 * 1.5 <= matcher.slices[0] <= 16000 * 2.5


def test_identify_no_match_keeps_default_speaker(tmp_path):
    fake = FakeTranscriber([[Segment(0.5, 2.5, "неизвестный голос")]])
    engine = LiveEngine(tmp_path, fake, voice_matcher=FakeMatcher([None]))
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(
        np.zeros(16000 * 3, dtype=np.int16).tobytes()
    )
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Собеседник: неизвестный голос" in text


def test_track_without_identify_never_calls_matcher(tmp_path):
    fake = FakeTranscriber([[Segment(0.0, 2.0, "моя реплика")]])
    matcher = FakeMatcher(["Кто-То"])
    engine = LiveEngine(tmp_path, fake, voice_matcher=matcher)
    engine.register_track("mic.wav", rate=16000, channels=1)
    engine._tracks["mic.wav"]["buffer"].push(
        np.zeros(16000 * 3, dtype=np.int16).tobytes()
    )
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Вы: моя реплика" in text
    assert matcher.slices == []


def test_matcher_error_falls_back_to_default(tmp_path):
    class BrokenMatcher:
        def load(self):
            pass

        def name_for(self, audio):
            raise RuntimeError("cuda died")

    fake = FakeTranscriber([[Segment(0.5, 2.5, "реплика")]])
    engine = LiveEngine(tmp_path, fake, voice_matcher=BrokenMatcher())
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(
        np.zeros(16000 * 3, dtype=np.int16).tobytes()
    )
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Собеседник: реплика" in text


def test_identify_without_matcher_is_noop(tmp_path):
    fake = FakeTranscriber([[Segment(0.5, 2.5, "реплика")]])
    engine = LiveEngine(tmp_path, fake)  # voice_matcher не передан
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(
        np.zeros(16000 * 3, dtype=np.int16).tobytes()
    )
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Собеседник: реплика" in text


def test_identify_slices_by_window_relative_times(tmp_path):
    fake = FakeTranscriber([
        [Segment(0.0, 2.0, "первое окно")],
        [Segment(1.0, 3.0, "второе окно")],  # станет [21.0, 23.0] после offset 20
    ])
    matcher = FakeMatcher(["Демьян", "Демьян"])
    clock = FakeClock([0.0, 20.0, 40.0])
    engine = LiveEngine(tmp_path, fake, window_seconds=20.0, clock=clock,
                        voice_matcher=matcher)
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    buf = engine._tracks["sys.wav"]["buffer"]
    buf.push(np.zeros(16000 * 4, dtype=np.int16).tobytes())
    engine.process_window()
    buf.push(np.zeros(16000 * 4, dtype=np.int16).tobytes())
    engine.process_window()
    # оба куска ~2 с: если бы offset не вычитался, второй вылез бы за аудио и был бы пуст
    assert all(16000 * 1.5 <= n <= 16000 * 2.5 for n in matcher.slices)


def test_near_silent_first_window_does_not_freeze_gain(tmp_path):
    fake = FakeTranscriber([[], []])
    engine = LiveEngine(tmp_path, fake, window_seconds=20.0)
    engine.register_track("sys.wav", rate=48000, channels=2, normalize=True)
    buf = engine._tracks["sys.wav"]["buffer"]

    # Near-silent opening window: must not lock in a gain of 1.0.
    buf.push(np.zeros(48000 * 2, dtype=np.int16).tobytes())
    engine.process_window()
    assert engine._tracks["sys.wav"]["gain"] is None

    # Later loud window: now calibrate and freeze a real gain.
    buf.push((np.zeros(48000 * 2, dtype=np.int16) + 1000).tobytes())
    engine.process_window()
    assert isinstance(engine._tracks["sys.wav"]["gain"], float)
