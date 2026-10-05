"""Поэтапный старт живого движка: звук пишется до загрузки модели.

`open_source()` — захват (свои устройства или отвод записи резидента) без
моделей; `load_asr()` — модель распознавания; `begin()` — рабочий поток.
Звук, накопленный за загрузку модели, распознаётся, а не теряется.
Устройства и модели — подделки (см. test_live, test_live_attach).
"""

import json
import threading
import time

import numpy as np
import scipy.signal  # noqa: F401 — первое окно грузит его лениво, долго под нагрузкой

from meet import live as live_mod
from meet import recorder
from meet.asr import Segment
from meet.live import LiveEngine
from meet.live_asr import GIGAAM_POLICY

from test_live import _fake_audio
from test_live_attach import FakeReader, FakeTap, tone

SR = 16000


def _wait(cond, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("не дождались")
        time.sleep(0.01)


class Asr:
    """GigaAM-подобный распознаватель: загрузку видно, окна записываются."""

    name = "GigaAM"
    latin_pass = True
    policy = GIGAAM_POLICY

    def __init__(self, delay: float = 0.0):
        self.loaded = False
        self.unloaded = False
        self.windows: list[tuple[float, float]] = []
        self.delay = delay

    def load(self):
        self.loaded = True

    def unload(self):
        self.unloaded = True

    def transcribe_window(self, audio, *, offset_s=0.0, hotwords=None, initial_prompt=None):
        assert self.loaded, "окно до загрузки модели"
        if self.delay:
            time.sleep(self.delay)
        dur = len(audio) / SR
        self.windows.append((round(offset_s, 2), round(dur, 2)))
        return [Segment(offset_s + 0.1, offset_s + dur - 0.1, f"окно {len(self.windows)}")]


class Matcher:
    def __init__(self):
        self.loaded = False

    def load(self):
        self.loaded = True

    def name_for(self, audio):
        return None


def _attached(tmp_path, tap, asr, **kw):
    got = []
    engine = LiveEngine(tmp_path, asr, speaker_name="Вы", mic_device=None, output_device=None,
                        on_entry=lambda line, entry: got.append(entry),
                        tap_connect=lambda: tap, **kw)
    return engine, got


def test_attached_source_opens_before_the_model_and_buffered_audio_is_recognized(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1,
                    "pos": 600 * SR * 2}])
    asr = Asr()
    engine, got = _attached(tmp_path, tap, asr)
    engine.open_source()
    try:
        assert not asr.loaded
        assert engine.attach_positions == {"mic.wav": 600.0}
        # Звук идёт, пока модель грузится: копится, а не теряется.
        tap.feed(1, False, 600 * SR * 2, tone(12.0))
        time.sleep(0.5)
        assert asr.windows == [] and got == []
        engine.load_asr()
        engine.begin()
        _wait(lambda: sum(d for _, d in asr.windows) >= 7.0)
        assert asr.windows[0][0] == 600.0  # с места подключения, по времени записи
    finally:
        tap.end()
        engine.stop()
    assert asr.unloaded


def test_own_capture_opens_before_the_model_and_keeps_the_lock(tmp_path, monkeypatch):
    written = _fake_audio(monkeypatch)
    out_dir = tmp_path / "2026-10-01_10-00"
    asr = Asr()
    engine = LiveEngine(out_dir, asr, speaker_name="Вы", mic_device=None, output_device=None)
    engine.open_source()
    try:
        assert not asr.loaded
        assert out_dir.is_dir()  # запись пошла — папка встречи есть
        lock = json.loads((tmp_path / recorder.LOCK_NAME).read_text(encoding="utf-8"))
        assert lock["folder"] == str(out_dir)
        assert set(written) == {"sys.opus", "mic.opus"}
        assert engine._worker is None  # распознавать нечем — рабочего потока нет
    finally:
        engine.stop()
    assert not (tmp_path / recorder.LOCK_NAME).exists()
    assert not asr.loaded and not asr.unloaded  # модель не трогали


def test_stop_before_the_model_finalizes_without_recognizing(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    asr = Asr()
    engine, got = _attached(tmp_path, tap, asr)
    engine.open_source()
    tap.feed(1, False, 0, tone(8.0))
    time.sleep(0.2)
    began = time.monotonic()
    engine.stop()
    assert time.monotonic() - began < 5
    assert asr.windows == [] and got == []


def test_begin_after_stop_starts_nothing(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    asr = Asr()
    engine, _ = _attached(tmp_path, tap, asr)
    engine.open_source()
    engine.stop()
    engine.load_asr()
    engine.begin()
    assert engine._worker is None


def test_voices_load_separately_from_the_model(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    matcher = Matcher()
    engine, _ = _attached(tmp_path, tap, Asr(), voice_matcher=matcher)
    engine.open_source()
    try:
        engine.load_asr()
        engine.begin()
        assert not matcher.loaded  # окна идут, голоса — когда загрузятся
        engine.load_voices()
        assert matcher.loaded
    finally:
        tap.end()
        engine.stop()


def test_startup_backlog_is_not_skipped_even_when_long(tmp_path, monkeypatch):
    """Звук, накопленный за долгую загрузку модели, распознаётся целиком:
    пропуск «распознавание не успевает» — только для отставания уже потом."""
    monkeypatch.setattr(live_mod, "BACKLOG_MAX_S", 20.0)
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    asr = Asr()
    engine, _ = _attached(tmp_path, tap, asr)
    engine.open_source()
    try:
        tap.feed(1, False, 0, tone(45.0))
        _wait(lambda: engine._tracks["mic.wav"]["buffer"]._chunks)
        time.sleep(0.2)
        engine.load_asr()
        engine.begin()
        _wait(lambda: sum(d for _, d in asr.windows) >= 38.0)
        assert engine.stats["skipped_s"] == 0
    finally:
        tap.end()
        engine.stop()


def _live_engine(tmp_path):
    engine = LiveEngine(tmp_path, Asr(), speaker_name="Вы", mic_device=None, output_device=None)
    engine._transcriber.loaded = True
    engine.register_track("mic.wav", rate=SR, channels=1)
    engine.start_catchup({"mic.wav": ("x", 0.0, 60.0)}, reader=lambda *a: FakeReader(60))
    return engine, engine._tracks["mic.wav"]["buffer"]


def test_catchup_waits_while_live_audio_lags(tmp_path):
    """Живой звук отстал (в очереди больше двух окон) — в этот такт догонялка
    ждёт: живая лента важнее начала встречи (оно и так будет в полной
    расшифровке), а её звук иначе пропускался бы как «не успевает»."""
    engine, buf = _live_engine(tmp_path)
    buf.push(tone(40.0))
    engine.step()
    assert engine._catchup_due() is False
    buf.push(tone(3.0))  # догнали: в очереди меньше окна
    engine.step()
    assert engine._catchup_due() is True


def test_catchup_runs_alongside_live_that_keeps_up(tmp_path):
    engine, buf = _live_engine(tmp_path)
    buf.push(tone(8.0))
    engine.step()
    assert engine._catchup_due() is True


def test_old_start_still_loads_models_before_opening_devices(tmp_path, monkeypatch):
    _fake_audio(monkeypatch)
    out_dir = tmp_path / "2026-10-01_10-00"
    seen = {}

    class Spy(Asr):
        def load(self):
            seen["folder_during_load"] = out_dir.exists()
            super().load()

    engine = LiveEngine(out_dir, Spy(), speaker_name="Вы", mic_device=None, output_device=None)
    engine.start()
    try:
        assert seen["folder_during_load"] is False
        assert engine._worker is not None
    finally:
        engine.stop()


def test_threads_of_a_stopped_engine_do_not_linger(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    engine, _ = _attached(tmp_path, tap, Asr())
    before = threading.active_count()
    engine.open_source()
    engine.load_asr()
    engine.begin()
    tap.end()
    engine.stop()
    _wait(lambda: threading.active_count() <= before)
    assert np  # numpy — общий для подделок звука
