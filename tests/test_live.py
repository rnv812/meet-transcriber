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


def test_speaker_name_comes_from_settings(tmp_path, monkeypatch):
    """Живая лента и точный транскрипт должны называть человека одинаково."""
    import json

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "config.json").write_text(
        json.dumps({"recording": {"speaker_name": "Алексей"}}), encoding="utf-8"
    )
    engine = LiveEngine(tmp_path, transcriber=None)
    assert engine.SPEAKERS["mic.wav"] == "Алексей"
    assert engine.SPEAKERS["sys.wav"] == "Собеседник"


def test_speaker_name_defaults_to_you(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "пусто"))
    assert LiveEngine(tmp_path, transcriber=None).SPEAKERS["mic.wav"] == "Вы"


def test_on_entry_gets_structure_next_to_line(tmp_path):
    fake = FakeTranscriber([[Segment(0.0, 0.5, "привет", no_speech_prob=0.1, avg_logprob=-0.3)]])
    got = []
    engine = LiveEngine(tmp_path, fake, speaker_name="Вы",
                        on_entry=lambda line, entry: got.append((line, entry)))
    engine.register_track("mic.wav", rate=48000, channels=2, normalize=False)
    engine._tracks["mic.wav"]["buffer"].push(_one_second_2ch_48k())
    engine.process_window()
    line, entry = got[0]
    assert line == format_live_line(0.0, "Вы", "привет")
    assert entry == {"t": 0.0, "speaker": "Вы", "text": "привет"}


# --- общий lock записи ------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402

import meet.recorder as recorder  # noqa: E402  (реальный pyaudio — до подмены)


class _LoadSpy:
    def __init__(self, fail=False):
        self.fail = fail
        self.loaded = False
        self.unloaded = False

    def load(self):
        if self.fail:
            raise RuntimeError("модель не загрузилась")
        self.loaded = True

    def unload(self):
        self.unloaded = True

    def transcribe_window(self, audio, **kw):
        return []


def _fake_audio(monkeypatch):
    """Подменяет устройства: start() не открывает настоящий звук."""
    class Stream:
        def stop_stream(self):
            pass

        def close(self):
            pass

    class PA:
        def get_host_api_info_by_type(self, t):
            return {"defaultInputDevice": 1}

        def get_device_info_by_index(self, i):
            return {"index": i, "name": "mic", "maxInputChannels": 1,
                    "defaultSampleRate": 16000}

        def open(self, **kw):
            return Stream()

        def terminate(self):
            pass

    class Writer:
        def __init__(self, path, channels, rate):
            pass

        def write(self, data):
            pass

        def close(self):
            pass

    fake = types.SimpleNamespace(paWASAPI=13, paInt16=8, paContinue=0, PyAudio=PA)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)
    monkeypatch.setattr(recorder, "OpusWriter", Writer)
    monkeypatch.setattr(recorder, "_find_loopback", lambda p: {
        "index": 0, "name": "loopback", "maxInputChannels": 2,
        "defaultSampleRate": 48000})


def test_start_refuses_when_recording_lock_busy(tmp_path):
    lock = tmp_path / recorder.LOCK_NAME
    lock.write_text(json.dumps({"pid": os.getpid(), "folder": "другая"}),
                    encoding="utf-8")
    spy = _LoadSpy()
    engine = LiveEngine(tmp_path / "2026-10-01_10-00", spy, speaker_name="Вы")
    with pytest.raises(SystemExit, match="Запись уже идёт"):
        engine.start()
    assert not spy.loaded  # модели не грузились: отказ сразу
    assert not (tmp_path / "2026-10-01_10-00").exists()  # пустой папки нет
    engine.stop()  # штатный finally вызывающего не трогает чужой lock
    assert json.loads(lock.read_text(encoding="utf-8"))["folder"] == "другая"


def test_start_takes_shared_lock_and_stop_releases_it(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    out_dir = tmp_path / "2026-10-01_10-00"
    engine = LiveEngine(out_dir, _LoadSpy(), window_seconds=0.05, speaker_name="Вы")
    engine.start()
    lock = tmp_path / recorder.LOCK_NAME
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        assert data == {"pid": os.getpid(), "folder": str(out_dir)}
        # Резидентная запись при идущем живом режиме — та же ошибка.
        with pytest.raises(SystemExit, match="Запись уже идёт"):
            recorder._acquire_lock(tmp_path, tmp_path / "другая")
    finally:
        engine.stop()
    assert not lock.exists()


def test_partial_start_failure_releases_lock(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    engine = LiveEngine(tmp_path / "2026-10-01_10-00", _LoadSpy(fail=True),
                        speaker_name="Вы")
    with pytest.raises(RuntimeError):
        engine.start()
    assert not (tmp_path / recorder.LOCK_NAME).exists()
    engine.stop()  # повторная уборка после сбоя безопасна


# --- Дорожки живого режима держатся у стенных часов ---------------------------

from meet.live import WallClockWriter  # noqa: E402


class _Sink:
    def __init__(self):
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data):
        self.chunks.append(bytes(data))

    def close(self):
        self.closed = True

    @property
    def data(self) -> bytes:
        return b"".join(self.chunks)


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_silent_loopback_still_gives_full_length_track():
    """Loopback без системного звука не зовёт callback вовсе: без доливки
    sys.opus остаётся пустым и офлайн-расшифровка записи падает на ffmpeg."""
    sink, clock = _Sink(), _Clock()
    w = WallClockWriter(sink, channels=2, rate=1000, clock=clock)
    clock.now += 3.0
    w.close()
    assert sink.closed
    assert len(sink.data) == 3 * 1000 * 4
    assert set(sink.data) == {0}


def test_gap_in_stream_is_filled_before_resumed_audio():
    """Пауза в звуке доливается тишиной ДО возобновившихся данных: иначе
    реплики после паузы уезжают к началу и interleave с mic врёт."""
    sink, clock = _Sink(), _Clock()
    w = WallClockWriter(sink, channels=1, rate=1000, clock=clock)
    first = b"\x01\x00" * 1000  # 1 с звука, пришла к t=1
    clock.now += 1.0
    w.write(first)
    clock.now += 5.0  # 4 с тишины, затем ещё секунда звука к t=6
    second = b"\x02\x00" * 1000
    w.write(second)
    data = sink.data
    assert data[:2000] == first
    assert data[-2000:] == second
    assert len(data) == 6 * 1000 * 2
    assert set(data[2000:-2000]) == {0}


def test_callback_jitter_is_not_padded():
    """Обычная задержка callback'а (доли секунды) — не пауза: лишней тишины
    между буферами быть не должно."""
    sink, clock = _Sink(), _Clock()
    w = WallClockWriter(sink, channels=1, rate=1000, clock=clock)
    chunk = b"\x01\x00" * 100  # 0.1 с
    for _ in range(10):
        clock.now += 0.1
        w.write(chunk)
    clock.now += 0.4  # запаздывание последнего буфера
    w.write(chunk)
    assert sink.data == chunk * 11
