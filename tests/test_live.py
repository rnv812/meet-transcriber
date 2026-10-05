import threading
import time

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
    # Таймкод окна — его место в звуке дорожки: второе окно начинается через 1 с.
    assert fake.offsets == [0.0, 1.0]


def test_each_track_keeps_its_own_position(tmp_path):
    """Таймкод окна — позиция в звуке своей дорожки (тот же поток, что и
    файл, с доливкой пауз), а не часы распознавания: дорожка, где звука было
    меньше, не «уезжает» вслед за другой."""
    fake = FakeTranscriber([])
    engine = LiveEngine(tmp_path, fake, window_seconds=20.0)
    engine.register_track("sys.wav", rate=48000, channels=2, normalize=False)
    engine.register_track("mic.wav", rate=48000, channels=2, normalize=False)

    engine.process_window()  # звука нет — и окон нет
    assert fake.offsets == []

    tone = lambda s: (np.zeros(48000 * 2 * s, dtype=np.int16) + 1000).tobytes()  # noqa: E731
    engine._tracks["sys.wav"]["buffer"].push(tone(5))
    engine._tracks["mic.wav"]["buffer"].push(tone(1))
    engine.process_window()
    engine._tracks["sys.wav"]["buffer"].push(tone(1))
    engine._tracks["mic.wav"]["buffer"].push(tone(1))
    engine.process_window()
    assert fake.offsets == [0.0, 0.0, 5.0, 1.0]


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


IVAN = np.array([1.0, 0.0, 0.0])
OTHER = np.array([0.0, 1.0, 0.0])
OWNER_V = np.array([0.0, 0.0, 1.0])


def _voice_of(audio):
    """Фальшивый эмбеддер: голос зашит в громкость звука сегмента."""
    level = float(np.abs(audio).mean())
    if level > 0.08:
        return OWNER_V
    return IVAN if level > 0.02 else OTHER


def _matcher(calls=None, owner=None, base=None):
    from meet.voice_id import VoiceMatcher

    def embed(audio):
        if calls is not None:
            calls.append(len(audio))
        return _voice_of(audio)

    return VoiceMatcher(base={"Демьян": [IVAN * 2.0]} if base is None else base, embed_fn=embed,
                        threshold=0.70, owner=owner or [], mic=True, log=lambda line: None)


def _segments(count, seconds=2.0, gap=0.2, text="реплика"):
    out, t = [], 0.0
    for i in range(count):
        out.append(Segment(t, t + seconds, f"{text} {i + 1}"))
        t += seconds + gap
    return out, t


def _pcm(seconds, value=1000):
    return (np.zeros(int(16000 * seconds), dtype=np.int16) + value).tobytes()


def test_identify_names_cluster_after_enough_speech(tmp_path):
    segs, total = _segments(6)
    fake = FakeTranscriber([segs])
    got = []
    engine = LiveEngine(tmp_path, fake, voice_matcher=_matcher(),
                        on_entry=lambda line, entry: got.append(entry))
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(total + 0.5))
    engine.process_window()
    # Имя — по накопленному голосу: от 8 с речи и на двух проверках подряд.
    assert [e["speaker"] for e in got] == ["Собеседник"] * 5 + ["Демьян"]
    assert {e["voice"] for e in got} == {"sys:0"}


def test_identify_unknown_voice_keeps_default_speaker(tmp_path):
    segs, total = _segments(6)
    fake = FakeTranscriber([segs])
    engine = LiveEngine(tmp_path, fake, voice_matcher=_matcher())
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(total + 0.5, value=100))  # голос не Демьяна
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Собеседник: реплика 6" in text and "Демьян" not in text


def test_track_without_identify_never_embeds(tmp_path):
    calls = []
    fake = FakeTranscriber([[Segment(0.0, 2.0, "моя реплика")]])
    engine = LiveEngine(tmp_path, fake, voice_matcher=_matcher(calls))
    engine.register_track("mic.wav", rate=16000, channels=1)
    engine._tracks["mic.wav"]["buffer"].push(_pcm(3))
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Вы: моя реплика" in text
    assert calls == []


def test_mic_without_owner_sample_is_not_embedded(tmp_path):
    calls = []
    got = []
    fake = FakeTranscriber([[Segment(0.0, 2.0, "моя реплика")]])
    engine = LiveEngine(tmp_path, fake, voice_matcher=_matcher(calls),
                        on_entry=lambda line, entry: got.append(entry))
    engine.register_track("mic.wav", rate=16000, channels=1, identify=True)
    engine._tracks["mic.wav"]["buffer"].push(_pcm(3))
    engine.process_window()
    assert calls == [] and got[0]["speaker"] == "Вы" and "voice" not in got[0]


def test_embedder_error_falls_back_to_default(tmp_path):
    from meet.voice_id import VoiceMatcher

    def broken(audio):
        raise RuntimeError("cuda died")

    fake = FakeTranscriber([[Segment(0.5, 2.5, "реплика")]])
    engine = LiveEngine(tmp_path, fake, voice_matcher=VoiceMatcher(
        base={"Демьян": [IVAN]}, embed_fn=broken, threshold=0.7, owner=[], mic=True,
        log=lambda line: None))
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(3))
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Собеседник: реплика" in text


def test_identify_without_matcher_is_noop(tmp_path):
    fake = FakeTranscriber([[Segment(0.5, 2.5, "реплика")]])
    engine = LiveEngine(tmp_path, fake)  # voice_matcher не передан
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(3))
    engine.process_window()
    text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
    assert "Собеседник: реплика" in text


def test_matcher_not_loaded_yet_keeps_default(tmp_path):
    from meet.voice_id import VoiceMatcher

    fake = FakeTranscriber([[Segment(0.5, 2.5, "реплика")]])
    matcher = VoiceMatcher(base={"Демьян": [IVAN]}, threshold=0.7, owner=[], mic=True)
    engine = LiveEngine(tmp_path, fake, voice_matcher=matcher)  # эмбеддер ещё грузится
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(3))
    engine.process_window()
    assert "Собеседник: реплика" in (tmp_path / "live_transcript.md").read_text(encoding="utf-8")


def test_identify_slices_by_window_relative_times(tmp_path):
    calls = []
    fake = FakeTranscriber([
        [Segment(0.0, 2.0, "первое окно")],
        [Segment(1.0, 3.0, "второе окно")],  # станет [21.0, 23.0] после offset 20
    ])
    clock = FakeClock([0.0, 20.0, 40.0])
    engine = LiveEngine(tmp_path, fake, window_seconds=20.0, clock=clock,
                        voice_matcher=_matcher(calls))
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    buf = engine._tracks["sys.wav"]["buffer"]
    buf.push(_pcm(4))
    engine.process_window()
    buf.push(_pcm(4))
    engine.process_window()
    # оба куска ~2 с: если бы offset не вычитался, второй вылез бы за аудио и был бы пуст
    assert len(calls) == 2 and all(16000 * 1.5 <= n <= 16000 * 2.5 for n in calls)


def test_voices_stats_logged_on_stop(tmp_path):
    segs, total = _segments(3)
    lines = []
    engine = LiveEngine(tmp_path, FakeTranscriber([segs]), voice_matcher=_matcher(), log=lines.append)
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(total + 0.5))
    engine._transcriber.unload = lambda: None
    engine.stop()
    assert any(line.startswith("голоса живого режима: эмбеддингов 3") for line in lines)


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
    assert entry == {"t": 0.0, "end": 0.5, "speaker": "Вы", "text": "привет"}


# --- общий lock записи ------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402
import struct  # noqa: E402
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
        def is_active(self):
            return True

        def stop_stream(self):
            pass

        def close(self):
            pass

    class Endpoints:  # настоящий COM тестам не нужен: устройства не меняются
        def ids(self):
            return ("out", "mic")

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

    written: dict = {}

    class Writer:
        def __init__(self, path, channels, rate):
            self.name = path.name
            written[self.name] = 0

        def write(self, data):
            written[self.name] += len(data)

        def close(self):
            pass

    fake = types.SimpleNamespace(paWASAPI=13, paInt16=8, paContinue=0, PyAudio=PA)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)
    monkeypatch.setattr(recorder, "OpusWriter", Writer)
    monkeypatch.setattr(recorder, "_DefaultEndpoints", Endpoints)
    monkeypatch.setattr(recorder, "_find_loopback", lambda p: {
        "index": 0, "name": "loopback", "maxInputChannels": 2,
        "defaultSampleRate": 48000})
    return written


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


def test_partial_start_failure_unloads_the_model(tmp_path, monkeypatch):
    """Модель загрузилась, а устройство не открылось: модель выгружается
    (и временная папка окон GigaAM удаляется), а не висит до конца процесса."""
    _fake_audio(monkeypatch)
    spy = _LoadSpy()
    monkeypatch.setattr(recorder, "_find_loopback", lambda p: (_ for _ in ()).throw(OSError("нет устройства")))
    engine = LiveEngine(tmp_path / "2026-10-01_10-00", spy, speaker_name="Вы")
    with pytest.raises(BaseException):
        engine.start()
    assert spy.loaded and spy.unloaded
    assert not (tmp_path / recorder.LOCK_NAME).exists()


def test_meeting_folder_appears_only_after_models_load(tmp_path, monkeypatch):
    """Остановка во время загрузки модели (резидент убивает ребёнка, пока
    порта ещё нет) не должна оставлять пустую датированную папку."""
    _fake_audio(monkeypatch)
    out_dir = tmp_path / "2026-10-01_10-00"
    seen = {}

    class Spy(_LoadSpy):
        def load(self):
            seen["folder_during_load"] = out_dir.exists()
            raise RuntimeError("убит во время загрузки")

    engine = LiveEngine(out_dir, Spy(), speaker_name="Вы")
    with pytest.raises(RuntimeError):
        engine.start()
    assert seen["folder_during_load"] is False
    assert not out_dir.exists()


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
    for _ in range(8):  # 4 с тишины; тикер доливает раз в полсекунды
        clock.now += 0.5
        w.tick()
    clock.now += 0.02  # звук вернулся: первый буфер callback'а к t=5.02
    second = b"\x02\x00" * 20
    w.write(second)
    data = sink.data
    assert data[:2000] == first
    assert data[-40:] == second
    assert set(data[2000:-40]) == {0}
    # Возобновившийся звук — на своём месте с точностью до порога паузы
    # (как у записи, recorder._Track.tick_pad), а не сразу после первой секунды.
    resumed_at = (len(data) - 40) / 2000
    assert 5.0 - WallClockWriter.PAD_GAP_S <= resumed_at <= 5.0


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


def test_callback_never_pads_more_than_about_a_tick():
    """Доливка — из тикера, не из аудио-callback'а: callback PortAudio не
    должен писать в пайп ffmpeg минуты нулей одним куском после долгой паузы."""
    sink, clock = _Sink(), _Clock()
    w = WallClockWriter(sink, channels=1, rate=1000, clock=clock)
    clock.now += 600.0  # 10 минут без callback'ов и без тикера
    chunk = b"\x01\x00" * 100
    w.write(chunk)
    padded = len(sink.data) - len(chunk)
    assert 0 < padded <= WallClockWriter.MAX_INLINE_PAD_S * 1000 * 2


def test_tick_pads_silence_incrementally_and_close_only_the_tail():
    sink, clock = _Sink(), _Clock()
    w = WallClockWriter(sink, channels=1, rate=1000, clock=clock)
    sizes = []
    for _ in range(20):  # 10 с тишины, тик каждые полсекунды
        clock.now += 0.5
        before = len(sink.data)
        w.tick()
        sizes.append(len(sink.data) - before)
    assert max(sizes) <= 1.5 * 1000 * 2  # не больше пары тиков за раз
    assert len(sink.data) >= 9 * 1000 * 2  # файл держится у часов
    before = len(sink.data)
    clock.now += 0.3
    w.close()
    assert len(sink.data) - before <= 1.5 * 1000 * 2
    assert len(sink.data) == round(10.3 * 1000) * 2


def test_padding_and_audio_reach_the_window_buffer_in_order():
    """Окно расшифровки видит ту же тишину, что и файл: после паузы реплики
    ленты не уезжают к началу окна (таймкоды не отстают)."""
    sink, clock, buf = _Sink(), _Clock(), TrackBuffer()
    w = WallClockWriter(sink, channels=1, rate=1000, clock=clock, buffer=buf)
    clock.now += 3.0
    w.tick()
    w.write(b"\x01\x00" * 100)
    raw, silent = buf.drain_window()
    assert raw == sink.data
    assert silent is False
    clock.now += 3.0
    w.tick()
    raw, silent = buf.drain_window()
    assert raw and set(raw) == {0} and silent is True


def test_window_of_pure_padding_is_not_transcribed(tmp_path):
    fake = FakeTranscriber([[Segment(0.0, 0.5, "галлюцинация")]])
    engine = LiveEngine(tmp_path, fake, speaker_name="Вы")
    engine.register_track("sys.wav", rate=16000, channels=1)
    engine._tracks["sys.wav"]["buffer"].push_silence(b"\x00" * 32000)
    engine.process_window()
    assert fake.offsets == []  # loopback без звука не гоняет ASR впустую


def test_gain_is_calibrated_on_audio_not_on_padding(tmp_path):
    from meet.audio import compute_gain, pcm16_to_float32_mono

    fake = FakeTranscriber([[]])
    engine = LiveEngine(tmp_path, fake, speaker_name="Вы")
    engine.register_track("sys.wav", rate=16000, channels=1, normalize=True)
    buf = engine._tracks["sys.wav"]["buffer"]
    loud = (np.zeros(16000, dtype=np.int16) + 1000).tobytes()
    buf.push_silence(b"\x00" * 16000 * 2 * 3)
    buf.push(loud)
    engine.process_window()
    expected = compute_gain(pcm16_to_float32_mono(loud, 1))
    assert abs(engine._tracks["sys.wav"]["gain"] - expected) < 1e-3


def test_window_offsets_count_padded_pauses(tmp_path):
    """Пауза (доливка тишины) — часть звука дорожки: окно после неё
    начинается там, где оно в файле дорожки, а окно из одной доливки не
    распознаётся."""
    fake = FakeTranscriber([[], []])
    engine = LiveEngine(tmp_path, fake, speaker_name="Вы")
    engine.register_track("mic.wav", rate=16000, channels=1)
    buf = engine._tracks["mic.wav"]["buffer"]
    buf.push(b"\x01\x00" * 16000)            # 1 с речи
    engine.process_window()                     # окно [0, 1]
    buf.push_silence(b"\x00" * 2 * 16000 * 20)  # пауза 20 с
    engine.process_window()                     # одна доливка — без распознавания
    buf.push(b"\x01\x00" * 16000)
    engine.process_window()                     # окно [21, 22]
    assert fake.offsets == [0.0, 21.0]


def test_engine_pads_silent_tracks_from_a_ticker(tmp_path, monkeypatch):
    """Живой режим без единого callback'а: дорожки растут тикером, а не
    одним куском при остановке."""
    import meet.live as live_mod

    written = _fake_audio(monkeypatch)
    monkeypatch.setattr(live_mod, "PAD_TICK_S", 0.02)
    monkeypatch.setattr(WallClockWriter, "PAD_GAP_S", 0.05)
    engine = LiveEngine(tmp_path / "2026-10-01_10-00", _LoadSpy(), window_seconds=3600,
                        speaker_name="Вы")
    engine.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (written and all(written.values())):
            time.sleep(0.02)
        assert written and all(written.values())  # обе дорожки уже растут
    finally:
        engine.stop()
    assert not any(t.name == "meet-live-pad" for t in threading.enumerate())


# --- Живой режим пишет с выбранных в настройках устройств ---------------------


def _spy_resolve(monkeypatch, missing=()):
    """resolve_device под наблюдением: какие имена спросили и что вернули."""
    asked = []

    def resolve(p, kind, wanted):
        asked.append((kind, wanted))
        fell_back = bool(wanted) and wanted in missing
        name = wanted if wanted and not fell_back else f"system-{kind}"
        return ({"index": 0 if kind == "output" else 1, "name": name,
                 "maxInputChannels": 1, "defaultSampleRate": 16000}, fell_back)

    monkeypatch.setattr(recorder, "resolve_device", resolve)
    return asked


def test_live_capture_uses_pinned_devices(tmp_path, monkeypatch, capsys):
    _fake_audio(monkeypatch)
    asked = _spy_resolve(monkeypatch, missing={"Наушники"})
    engine = LiveEngine(tmp_path / "2026-10-01_10-00", _LoadSpy(), window_seconds=3600,
                        speaker_name="Вы", mic_device="USB-микрофон",
                        output_device="Наушники")
    engine.start()
    engine.stop()
    assert ("mic", "USB-микрофон") in asked and ("output", "Наушники") in asked
    out = capsys.readouterr().out
    assert "mic.wav: USB-микрофон" in out
    assert ("Выбранное устройство вывода Наушники не найдено — запись идёт с системного"
            in out)
    assert engine.devices_fallback == [
        {"kind": "output", "name": "Наушники", "device": "system-output"}]


def test_live_capture_reads_devices_from_settings(tmp_path, monkeypatch):
    from meet import settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    settings.patch({"recording": {"mic_device": {"name": "USB-микрофон"}}})
    _fake_audio(monkeypatch)
    asked = _spy_resolve(monkeypatch)
    engine = LiveEngine(tmp_path / "rec" / "2026-10-01_10-00", _LoadSpy(),
                        window_seconds=3600, speaker_name="Вы")
    engine.start()
    engine.stop()
    assert ("mic", "USB-микрофон") in asked and ("output", None) in asked


# --- Живой режим переживает смену аудио-устройства -----------------------------

# Надзор за устройствами — только Windows: на macOS его нет.
_device_watch = pytest.mark.skipif(recorder._MAC, reason="надзор за устройствами — Windows")


def _switchable_audio(monkeypatch):
    """Подделка звука, у которой устройства меняются на ходу. `world` — что
    сейчас видит система: системные устройства ролей (None — устройства
    нет), устройства по имени (выбранные в настройках), ID системных
    endpoint'ов (None — COM не отвечает) и журнал того, что с ней делали."""
    import meet.live as live_mod

    world = {
        "output": {"index": 0, "name": "Наушники [Loopback]", "maxInputChannels": 2,
                   "defaultSampleRate": 48000},
        "mic": {"index": 1, "name": "Микрофон", "maxInputChannels": 1,
                "defaultSampleRate": 16000},
        "ids": ("out-1", "mic-1"),
        "named": {},  # имя из настроек → устройство; нет имени — fallback на системное
        "fail_open": set(),  # индексы устройств, которые не открываются
        # terminate() падает: "streams" — пока в реестре экземпляра есть
        # стримы (как настоящий на битом стриме), "always" — в любом случае.
        "terminate_broken": None,
        "terminated": 0,
        "fail_start": set(),  # индексы устройств, чей start_stream() падает
        "on_init": None,  # () -> None при каждом подъёме PyAudio
        "broken_writers": set(),  # имена файлов, запись в которые падает
        "on_resolve": None,  # (kind) -> None перед каждым выбором устройства
        "streams": [],
        "inits": 0,
        "polls": 0,
        "written": {},
        "created": {},
        "channels": {},
        "closed": [],
        "at_start": [],  # сколько байт было в файлах на момент start_stream()
    }

    class Stream:
        def __init__(self, kw):
            self.kw = kw
            self.started = kw.get("start", True)
            self.active = self.started
            self.closed = False

        def is_active(self):
            return self.active

        def start_stream(self):
            if self.kw["input_device_index"] in world["fail_start"]:
                raise OSError("стрим не стартовал")
            world["at_start"].append(
                {name: sum(map(len, chunks)) for name, chunks in world["written"].items()})
            self.started = self.active = True

        def stop_stream(self):
            self.active = False

        def close(self):
            self.closed = True

        def feed(self, data: bytes):
            return self.kw["stream_callback"](data, 0, None, 0)

    class PA:
        def __init__(self):
            world["inits"] += 1
            self._streams = set()  # закрытый стрим из реестра не уходит: «битый»
            if world["on_init"] is not None:
                world["on_init"]()

        def open(self, **kw):
            if kw["input_device_index"] in world["fail_open"]:
                raise OSError("устройство не открылось")
            stream = Stream(kw)
            self._streams.add(stream)
            world["streams"].append(stream)
            return stream

        def terminate(self):
            broken = world["terminate_broken"]
            if broken == "always" or (broken == "streams" and self._streams):
                raise OSError("битый стрим")
            world["terminated"] += 1

    class Writer:
        def __init__(self, path, channels, rate):
            self.name = path.name
            world["written"].setdefault(self.name, [])
            world["created"][self.name] = world["created"].get(self.name, 0) + 1
            world["channels"][self.name] = channels

        def write(self, data):
            if self.name in world["broken_writers"]:
                raise OSError("ffmpeg умер")
            world["written"][self.name].append(bytes(data))

        def close(self):
            world["closed"].append(self.name)

    class Endpoints:
        def ids(self):
            world["polls"] += 1
            return world["ids"]

        def close(self):
            pass

    def resolve(p, kind, wanted):
        if world["on_resolve"] is not None:
            world["on_resolve"](kind)
        if wanted and wanted in world["named"]:
            return dict(world["named"][wanted]), False
        dev = world[kind]
        if dev is None:
            raise OSError("устройства нет")
        return dict(dev), bool(wanted)

    fake = types.SimpleNamespace(paWASAPI=13, paInt16=8, paContinue=0, paAbort=2, PyAudio=PA)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)
    monkeypatch.setattr(recorder, "OpusWriter", Writer)
    monkeypatch.setattr(recorder, "resolve_device", resolve)
    monkeypatch.setattr(recorder, "_DefaultEndpoints", Endpoints)
    monkeypatch.setattr(recorder, "RESTART_MIN_S", 0.0)
    monkeypatch.setattr(recorder, "RETRY_S", 0.05)
    monkeypatch.setattr(live_mod, "PAD_TICK_S", 0.02)
    # Рабочий поток буферы не трогает: тесты читают их сами.
    monkeypatch.setattr(live_mod, "STEP_S", 3600)
    return world


def _live(tmp_path, log=None, **kw):
    return LiveEngine(tmp_path / "2026-10-01_10-00", _LoadSpy(), window_seconds=3600,
                      speaker_name="Вы", log=(log.append if log is not None else print),
                      **kw)


def _wait(cond, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return bool(cond())


def _watch_ticks(world, n: int = 5) -> None:
    """Дождаться ещё `n` завершённых тактов надзора. Считаются опросы
    endpoint'ов, а опрос — начало такта: один сверху."""
    target = world["polls"] + n + 1
    assert _wait(lambda: world["polls"] >= target)


def _open_on(world, index: int) -> list:
    """Запущенные и не закрытые стримы на устройстве с этим индексом."""
    return [s for s in world["streams"]
            if s.kw["input_device_index"] == index and s.started and not s.closed]


def _audio_chunks(world, name: str) -> list[bytes]:
    """Что дошло до файла дорожки, кроме доливки тишины."""
    return [c for c in world["written"][name] if any(c)]


def _has(log: list[str], text: str) -> bool:
    return any(text in line for line in log)


SPEAKERS_LB = {"index": 7, "name": "Динамики [Loopback]", "maxInputChannels": 2,
               "defaultSampleRate": 48000}


@_device_watch
def test_default_device_change_reopens_live_tracks(tmp_path, monkeypatch):
    """Наушники отключили — система ушла на динамики: дорожка звука
    собеседников переоткрывается на новом устройстве, а не молчит до конца
    встречи; звук нового устройства доходит и до файла, и до окна."""
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        first = _open_on(world, 0)[0]
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: _open_on(world, 7))
        assert first.closed
        engine._tracks["sys.wav"]["buffer"].drain_runs()  # доливка до смены — не в счёт
        _open_on(world, 7)[0].feed(b"\x01\x00" * 2 * 480)
        assert _audio_chunks(world, "sys.opus") == [b"\x01\x00" * 2 * 480]
        runs = engine._tracks["sys.wav"]["buffer"].drain_runs()
        assert (b"\x01\x00" * 2 * 480, False) in runs
        # Одна смена — один перезапуск: холодный старт PortAudio (свежий
        # список устройств), а не перезапуск на каждом такте.
        _watch_ticks(world)
        assert world["inits"] == 2
        assert len(_open_on(world, 7)) == 1 and len(_open_on(world, 1)) == 1
    finally:
        engine.stop()
    # Файл дорожки — один на всю запись: переоткрывается стрим, не писатель.
    assert world["created"] == {"sys.opus": 1, "mic.opus": 1}
    assert _has(log, "сменилось дефолтное аудио-устройство")
    assert _has(log, "запись возобновлена: Динамики [Loopback]")


@_device_watch
def test_dead_live_stream_is_reopened(tmp_path, monkeypatch):
    """Стрим умер, а дефолт не менялся (устройство моргнуло между тактами)."""
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        first = _open_on(world, 0)[0]
        first.active = False
        assert _wait(lambda: len(_open_on(world, 0)) == 1 and _open_on(world, 0)[0] is not first)
        assert first.closed
    finally:
        engine.stop()
    assert _has(log, "дорожка остановилась")


@_device_watch
def test_missing_device_is_awaited_and_picked_up_when_back(tmp_path, monkeypatch):
    """Устройства роли нет вовсе: дорожка ждёт без повторов, возврат
    устройства — смена системного."""
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        headphones = world["output"]
        world["output"], world["ids"] = None, (None, "mic-1")
        assert _wait(lambda: _has(log, "sys.wav: устройство недоступно — жду"))
        assert _open_on(world, 0) == []  # старый стрим закрыт, нового нет
        assert _wait(lambda: len(_open_on(world, 1)) == 1)  # микрофон при этом пишет
        _watch_ticks(world)
        assert world["inits"] == 2  # системного устройства нет — повторять нечем
        world["output"], world["ids"] = headphones, ("out-1", "mic-1")
        assert _wait(lambda: len(_open_on(world, 0)) == 1)
        _open_on(world, 0)[0].feed(b"\x02\x00" * 2 * 480)
        assert _audio_chunks(world, "sys.opus") == [b"\x02\x00" * 2 * 480]
    finally:
        engine.stop()
    assert sum("устройство недоступно — жду" in line for line in log) == 1
    assert _has(log, "запись возобновлена: Наушники [Loopback]")


@_device_watch
def test_waiting_track_is_retried_with_growing_pause(tmp_path, monkeypatch):
    """Системное устройство есть, а стрим на нём не открывается: повторы с
    нарастающей паузой (каждый рвёт и здоровую дорожку), строка в журнале —
    одна, с причиной; открылось — пауза повторов сбрасывается."""
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        world["fail_open"] = {0}
        _open_on(world, 0)[0].active = False
        assert _wait(lambda: world["inits"] >= 4)  # перезапуск и повторы
        assert engine._retry_wait > recorder.RETRY_S * 2
        assert _open_on(world, 0) == []
        world["fail_open"] = set()
        assert _wait(lambda: len(_open_on(world, 0)) == 1)
        assert _wait(lambda: engine._retry_wait == recorder.RETRY_S)
    finally:
        engine.stop()
    waits = [line for line in log if "устройство недоступно — жду" in line]
    assert len(waits) == 1 and "устройство не открылось" in waits[0]


@_device_watch
def test_restarts_are_rate_limited_but_not_lost(tmp_path, monkeypatch):
    """Вторая смена сразу за первой ждёт RESTART_MIN_S, но не теряется."""
    world = _switchable_audio(monkeypatch)
    monkeypatch.setattr(recorder, "RESTART_MIN_S", 1.0)
    engine = _live(tmp_path, [])
    engine.start()
    try:
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: _open_on(world, 7))
        first_at = time.monotonic()
        world["output"] = {**SPEAKERS_LB, "index": 8, "name": "Гарнитура [Loopback]"}
        world["ids"] = ("out-3", "mic-1")
        _watch_ticks(world)
        assert world["inits"] == 2 and not _open_on(world, 8)  # ещё рано
        assert _wait(lambda: _open_on(world, 8))
        assert time.monotonic() - first_at >= 0.8
    finally:
        engine.stop()


@_device_watch
def test_pinned_live_tracks_ignore_default_change(tmp_path, monkeypatch):
    """Оба устройства выбраны в настройках и на месте: смена системного их
    не касается, перезапуск только рвал бы дорожки."""
    world = _switchable_audio(monkeypatch)
    world["named"] = {"Наушники": world["output"], "Микрофон": world["mic"]}
    engine = _live(tmp_path, [], mic_device="Микрофон", output_device="Наушники")
    engine.start()
    try:
        world["ids"] = ("out-2", "mic-2")
        _watch_ticks(world, 10)
        assert world["inits"] == 1
        assert len(world["streams"]) == 2 and not any(s.closed for s in world["streams"])
    finally:
        engine.stop()


@_device_watch
def test_pinned_device_falls_back_and_returns(tmp_path, monkeypatch):
    """Выбранное устройство пропало — дорожка пишет с системного и с этого
    момента следит за ним; вернулось к следующему перезапуску — снова с него."""
    world = _switchable_audio(monkeypatch)
    headphones = world["output"]
    world["named"] = {"Наушники": headphones}
    world["output"] = SPEAKERS_LB  # системное — динамики
    log: list[str] = []
    engine = _live(tmp_path, log, output_device="Наушники")
    engine.start()
    try:
        assert engine.devices_fallback == []
        world["named"] = {}
        _open_on(world, 0)[0].active = False
        assert _wait(lambda: _open_on(world, 7))
        assert _wait(lambda: _has(log, "Выбранное устройство вывода Наушники не найдено"))
        world["named"] = {"Наушники": headphones}
        world["ids"] = ("out-2", "mic-1")  # дорожка в fallback следит за системным
        assert _wait(lambda: _open_on(world, 0))
        assert not _open_on(world, 7)
        assert _wait(lambda: _has(log, "Выбранное устройство вывода Наушники снова доступно"))
    finally:
        engine.stop()


@_device_watch
def test_other_device_format_is_converted_to_the_track_format(tmp_path, monkeypatch):
    """Файл дорожки один на всю запись: звук устройства с другой частотой и
    числом каналов приводится к формату первого."""
    world = _switchable_audio(monkeypatch)
    engine = _live(tmp_path, [])
    engine.start()
    try:
        world["output"] = {"index": 7, "name": "Гарнитура [Loopback]",
                           "maxInputChannels": 1, "defaultSampleRate": 16000}
        world["ids"] = ("out-2", "mic-1")
        assert _wait(lambda: _open_on(world, 7))
        stream = _open_on(world, 7)[0]
        assert (stream.kw["rate"], stream.kw["channels"]) == (16000, 1)
        stream.feed(b"\x10\x00" * 16000)  # секунда моно 16 кГц
        (chunk,) = _audio_chunks(world, "sys.opus")
        assert abs(len(chunk) - 48000 * 2 * 2) <= 16  # секунда стерео 48 кГц
    finally:
        engine.stop()


@_device_watch
def test_multichannel_track_keeps_its_format_across_devices(tmp_path, monkeypatch):
    """Массив из четырёх микрофонов пишется всеми каналами, как и раньше;
    сменивший его стерео-микрофон раскладывается по тем же четырём парами —
    моно-сведение дорожки от этого не меняется."""
    world = _switchable_audio(monkeypatch)
    world["mic"] = {"index": 1, "name": "Массив микрофонов", "maxInputChannels": 4,
                    "defaultSampleRate": 16000}
    engine = _live(tmp_path, [])
    engine.start()
    try:
        assert world["channels"]["mic.opus"] == 4
        assert engine._tracks["mic.wav"]["channels"] == 4
        four = struct.pack("<4h", 1, 2, 3, 4) * 160
        _open_on(world, 1)[0].feed(four)
        assert _audio_chunks(world, "mic.opus") == [four]  # то же устройство — как есть
        world["mic"] = {"index": 9, "name": "USB-микрофон", "maxInputChannels": 2,
                        "defaultSampleRate": 16000}
        world["ids"] = ("out-1", "mic-2")
        assert _wait(lambda: _open_on(world, 9))
        _open_on(world, 9)[0].feed(struct.pack("<2h", 5, 7) * 160)
        assert _audio_chunks(world, "mic.opus")[-1] == struct.pack("<4h", 5, 7, 5, 7) * 160
    finally:
        engine.stop()


@_device_watch
def test_restart_pause_is_padded_before_the_stream_resumes(tmp_path, monkeypatch):
    """Перезапуск короче PAD_GAP_S: ни callback, ни тикер паузу не долили бы,
    и дорожка с непрерывным звуком осталась бы сдвинутой. Тишина до часов
    дописывается до старта переоткрытого стрима."""
    world = _switchable_audio(monkeypatch)
    # Тикер и callback при таком пороге не доливают ничего: всё, что окажется
    # в файле к старту стрима, дописано перед стартом.
    monkeypatch.setattr(WallClockWriter, "PAD_GAP_S", 3600.0)
    engine = _live(tmp_path, [])
    engine.start()
    try:
        time.sleep(0.2)  # заметно больше TAIL_GAP_S
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: _open_on(world, 7))
        stream = _open_on(world, 7)[0]
        assert stream.kw["start"] is False and stream.active
        at_start = world["at_start"][0]  # первый переоткрытый стрим — sys
        assert at_start["sys.opus"] >= int(0.15 * 48000) * 4
    finally:
        engine.stop()


@_device_watch
def test_baseline_ids_are_taken_before_devices_are_picked(tmp_path, monkeypatch):
    """Системное устройство сменилось, пока открывались стримы: смена не
    теряется, дорожка уходит на новое с первым тактом надзора."""
    world = _switchable_audio(monkeypatch)

    def switch_after_output_is_picked(kind):
        if kind == "mic" and world["ids"] == ("out-1", "mic-1"):
            world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")

    world["on_resolve"] = switch_after_output_is_picked
    engine = _live(tmp_path, [])
    engine.start()
    try:
        assert _wait(lambda: _open_on(world, 7))
        assert not _open_on(world, 0)
    finally:
        engine.stop()


@_device_watch
def test_callback_error_is_logged_and_aborts_the_stream(tmp_path, monkeypatch):
    """Сбой в аудио-callback — строка в журнале и paAbort (PortAudio на нём
    останавливает стрим; дальше — путь умершего стрима)."""
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        stream = _open_on(world, 0)[0]
        assert stream.feed(b"\x01\x00" * 2 * 480) == (None, 0)
        world["broken_writers"] = {"sys.opus"}
        assert stream.feed(b"\x01\x00" * 2 * 480) == (None, 2)
        assert _has(log, "sys.wav: ошибка в аудио-callback")
        world["broken_writers"] = set()
    finally:
        engine.stop()


@_device_watch
def test_log_failure_does_not_stop_padding_or_watch(tmp_path, monkeypatch):
    """Журнал недоступен (закрытый вывод): поток доливки живёт, дорожки
    переоткрываются."""
    world = _switchable_audio(monkeypatch)

    def broken_log(line):
        raise OSError("вывод закрыт")

    engine = LiveEngine(tmp_path / "2026-10-01_10-00", _LoadSpy(), window_seconds=3600,
                        speaker_name="Вы", log=broken_log)
    engine.start()
    try:
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: _open_on(world, 7))
        assert _wait(lambda: len(_open_on(world, 1)) == 1)
        assert engine._pad_thread.is_alive()
    finally:
        engine.stop()


@_device_watch
def test_com_unavailable_still_reopens_a_dead_stream(tmp_path, monkeypatch):
    world = _switchable_audio(monkeypatch)
    world["ids"] = None
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        assert _wait(lambda: _has(log, "COM недоступен"))
        first = _open_on(world, 0)[0]
        first.active = False
        assert _wait(lambda: len(_open_on(world, 0)) == 1 and _open_on(world, 0)[0] is not first)
    finally:
        engine.stop()


@_device_watch
def test_com_breaking_midway_is_logged_once(tmp_path, monkeypatch):
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        _watch_ticks(world, 2)
        world["ids"] = None
        _watch_ticks(world, 5)
        assert world["inits"] == 1  # пропажа COM — не смена устройства
    finally:
        engine.stop()
    assert sum("COM перестал отвечать" in line for line in log) == 1


@_device_watch
def test_broken_terminate_is_retried_and_reported(tmp_path, monkeypatch):
    """Битый стрим роняет terminate(): повтор без реестра стримов проходит
    молча; не прошёл и он — строка в журнале, запись продолжается."""
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        world["terminate_broken"] = "streams"
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: _open_on(world, 7))
        assert world["terminated"] == 1 and not _has(log, "PyAudio.terminate")
        world["terminate_broken"] = "always"
        first = _open_on(world, 7)[0]
        first.active = False
        assert _wait(lambda: _has(log, "PyAudio.terminate"))
        assert _wait(lambda: len(_open_on(world, 7)) == 1 and _open_on(world, 7)[0] is not first)
        world["terminate_broken"] = "streams"
    finally:
        engine.stop()  # и при остановке — тот же обход, без исключения
    assert world["terminated"] == 2


@_device_watch
def test_restart_does_not_bring_audio_up_once_stopping(tmp_path, monkeypatch):
    """Остановка застала перезапуск: стримы закрыты, новый PyAudio уже не
    поднимается — закрывать его было бы некому."""
    world = _switchable_audio(monkeypatch)
    engine = _live(tmp_path, [])
    engine.start()
    try:
        engine._stop.set()
        engine._pad_thread.join(timeout=5)
        engine._restart_capture(("out-2", "mic-1"), None)
        assert world["inits"] == 1
        assert all(s.closed for s in world["streams"])
    finally:
        engine.stop()
    assert sorted(world["closed"]) == ["mic.opus", "sys.opus"]


@_device_watch
def test_stop_arriving_while_audio_comes_up_leaves_nothing_open(tmp_path, monkeypatch):
    """Остановка пришла, пока поднимался новый PyAudio: он завершается, стримы
    не открываются."""
    world = _switchable_audio(monkeypatch)
    engine = _live(tmp_path, [])
    engine.start()
    try:
        world["on_init"] = engine._stop.set
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: world["inits"] == 2 and world["terminated"] == 2)
        assert _wait(lambda: not engine._pad_thread.is_alive())
        assert engine._p is None and not _open_on(world, 7)
    finally:
        engine.stop()


@_device_watch
def test_stop_arriving_between_tracks_skips_the_rest(tmp_path, monkeypatch):
    """Остановка пришла, пока переоткрывалась первая дорожка: вторая уже не
    открывается."""
    world = _switchable_audio(monkeypatch)
    engine = _live(tmp_path, [])
    engine.start()
    try:
        mic_opens = len([s for s in world["streams"] if s.kw["input_device_index"] == 1])
        world["on_resolve"] = lambda kind: engine._stop.set()
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: not engine._pad_thread.is_alive())
        assert len([s for s in world["streams"]
                    if s.kw["input_device_index"] == 1]) == mic_opens
    finally:
        engine.stop()
    assert all(s.closed for s in world["streams"])


@_device_watch
def test_stream_that_fails_to_start_is_closed_and_retried(tmp_path, monkeypatch):
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        world["fail_start"] = {7}
        world["output"], world["ids"] = SPEAKERS_LB, ("out-2", "mic-1")
        assert _wait(lambda: _has(log, "sys.wav: устройство недоступно — жду"))
        assert all(s.closed for s in world["streams"] if s.kw["input_device_index"] == 7)
        world["fail_start"] = set()
        assert _wait(lambda: len(_open_on(world, 7)) == 1)
    finally:
        engine.stop()


@_device_watch
def test_log_failure_before_the_loop_and_in_callback(tmp_path, monkeypatch):
    """Строка «COM недоступен» пишется до цикла доливки, строка о сбое
    callback'а — из потока PortAudio: сбой журнала не должен остановить ни
    тот, ни другой."""
    world = _switchable_audio(monkeypatch)
    world["ids"] = None

    def broken_log(line):
        raise OSError("вывод закрыт")

    engine = LiveEngine(tmp_path / "2026-10-01_10-00", _LoadSpy(), window_seconds=3600,
                        speaker_name="Вы", log=broken_log)
    engine.start()
    try:
        _watch_ticks(world, 3)
        assert engine._pad_thread.is_alive()
        world["broken_writers"] = {"sys.opus"}
        assert _open_on(world, 0)[0].feed(b"\x01\x00" * 2 * 480) == (None, 2)
        world["broken_writers"] = set()
    finally:
        engine.stop()


@_device_watch
def test_partial_start_failure_closes_the_unopened_track_file(tmp_path, monkeypatch):
    """Микрофон не открылся: закрываются оба файла, включая тот, чей стрим
    так и не появился (иначе его ffmpeg остался бы висеть)."""
    world = _switchable_audio(monkeypatch)
    world["fail_open"] = {1}
    engine = _live(tmp_path, [])
    with pytest.raises(OSError):
        engine.start()
    assert sorted(world["closed"]) == ["mic.opus", "sys.opus"]
    assert all(s.closed for s in world["streams"])
    assert not (tmp_path / recorder.LOCK_NAME).exists()


@_device_watch
def test_stop_during_device_wait_closes_cleanly(tmp_path, monkeypatch):
    world = _switchable_audio(monkeypatch)
    log: list[str] = []
    engine = _live(tmp_path, log)
    engine.start()
    try:
        world["output"], world["ids"] = None, (None, "mic-1")
        assert _wait(lambda: _has(log, "устройство недоступно — жду"))
    finally:
        engine.stop()
    assert not (tmp_path / recorder.LOCK_NAME).exists()
    assert not any(t.name == "meet-live-pad" for t in threading.enumerate())
    assert sorted(world["closed"]) == ["mic.opus", "sys.opus"]


def test_catch_up_pads_a_pause_shorter_than_the_gap():
    sink, clock = _Sink(), _Clock()
    w = WallClockWriter(sink, channels=1, rate=1000, clock=clock)
    clock.now += 0.6  # меньше PAD_GAP_S: tick() и write() такое не доливают
    w.tick()
    assert sink.data == b""
    w.catch_up()
    assert len(sink.data) == 600 * 2 and set(sink.data) == {0}
    w.write(b"\x01\x00" * 100)
    assert sink.data[600 * 2:] == b"\x01\x00" * 100  # звук — сразу за паузой


def test_writer_ignores_a_late_callback_after_close():
    sink, clock = _Sink(), _Clock()
    w = WallClockWriter(sink, channels=1, rate=1000, clock=clock)
    w.write(b"\x01\x00" * 100)
    w.close()
    clock.now += 1.0
    w.write(b"\x02\x00" * 100)
    w.catch_up()
    assert sink.data == b"\x01\x00" * 100


# --- переименование задним числом (С3, §4.3) --------------------------------------


def test_name_arrives_retroactively_in_file_and_consumers(tmp_path):
    segs, total = _segments(6)
    renames, got = [], []
    engine = LiveEngine(tmp_path, FakeTranscriber([segs]), voice_matcher=_matcher(),
                        on_entry=lambda line, entry: got.append(line),
                        on_relabel=lambda voice, speaker: renames.append((voice, speaker)))
    (tmp_path / "live_transcript.md").write_text("[00:00:00] Собеседник: реплика 1\n", encoding="utf-8")
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(total + 0.5))
    engine.process_window()
    assert renames == [("sys:0", "Демьян")]
    lines = (tmp_path / "live_transcript.md").read_text(encoding="utf-8").splitlines()
    # Строка прошлого включения ассистента (та же по тексту) не тронута — только наши.
    assert lines[0] == "[00:00:00] Собеседник: реплика 1"
    assert lines[1:] == [f"[00:00:{t:02d}] Демьян: реплика {i + 1}"
                         for i, t in enumerate((0, 2, 4, 6, 8, 11))]
    assert not (tmp_path / "live_transcript.md.tmp").exists()
    # Дальше лента дописывается в тот же файл.
    engine._write_line("[00:01:00] Вы: дальше")
    engine._out.close()
    assert (tmp_path / "live_transcript.md").read_text(encoding="utf-8").endswith("Вы: дальше\n")


def test_catchup_lines_renamed_before_merge(tmp_path):
    segs, total = _segments(6)
    engine = LiveEngine(tmp_path, FakeTranscriber([segs]), voice_matcher=_matcher())
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    tr = engine._tracks["sys.wav"]
    audio = np.full(int(16000 * (total + 0.5)), 1000 / 32768, dtype=np.float32)
    with engine._window_lock:
        engine._recognize("sys.wav", tr, audio, 0.0, False, catchup=True)
    assert engine._catch_lines[0] == "[00:00:00] Демьян: реплика 1"
    side = (tmp_path / "live_transcript.catchup.md").read_text(encoding="utf-8")
    assert "Собеседник" not in side and side.count("Демьян") == 6
    assert not (tmp_path / "live_transcript.md").exists()


def test_rename_survives_unwritable_transcript(tmp_path, monkeypatch):
    import os

    segs, total = _segments(6)
    lines = []
    renames = []
    engine = LiveEngine(tmp_path, FakeTranscriber([segs]), voice_matcher=_matcher(), log=lines.append,
                        on_relabel=lambda voice, speaker: renames.append(voice))
    engine.register_track("sys.wav", rate=16000, channels=1, identify=True)
    engine._tracks["sys.wav"]["buffer"].push(_pcm(total + 0.5))

    def locked(src, dst):
        raise PermissionError("файл открыт")

    monkeypatch.setattr(os, "replace", locked)
    engine.process_window()
    assert renames == ["sys:0"]  # потребители всё равно узнали
    assert any("лента не переписана" in line for line in lines)
