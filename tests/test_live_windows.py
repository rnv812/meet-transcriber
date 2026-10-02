"""Окна распознавания живого режима: резка в паузе, постоянный такт, защита
от отставания, хвост для вопроса без ожидания, исправления текста.

Звук синтетический (тон и паузы), распознавание — поддельное."""

import threading
import time

import numpy as np

from meet.asr import Segment
from meet.live import LiveEngine
from meet.live_asr import GIGAAM_POLICY, TextFixes

SR = 16000


def tone(seconds: float, amp: int = 6000) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (np.sin(2 * np.pi * 220 * t) * amp).astype(np.int16)


def quiet(seconds: float) -> np.ndarray:
    return (np.random.default_rng(1).normal(0, 20, int(seconds * SR))).astype(np.int16)


class FakeGigaam:
    """Распознаёт «окно» одной репликой во всю его длину."""

    name = "GigaAM"
    latin_pass = True
    policy = GIGAAM_POLICY

    def __init__(self, delay: float = 0.0):
        self.windows: list[tuple[float, float]] = []
        self.delay = delay

    def transcribe_window(self, audio, *, offset_s=0.0, hotwords=None, initial_prompt=None):
        if self.delay:
            time.sleep(self.delay)
        dur = len(audio) / SR
        self.windows.append((round(offset_s, 2), round(dur, 2)))
        return [Segment(offset_s + 0.1, offset_s + dur - 0.1, f"окно {len(self.windows)}")]


def _engine(tmp_path, asr, **kw):
    got = []
    engine = LiveEngine(tmp_path, asr, speaker_name="Вы",
                        on_entry=lambda line, entry: got.append(entry), **kw)
    engine.register_track("mic.wav", rate=SR, channels=1)
    return engine, engine._tracks["mic.wav"]["buffer"], got


def test_window_is_cut_in_the_pause_between_4_and_7_seconds(tmp_path):
    asr = FakeGigaam()
    engine, buf, got = _engine(tmp_path, asr)
    # речь 0–3,2; пауза 3,2–3,6; речь 3,6–5,5; пауза 5,5–5,9; речь дальше
    for piece in (tone(3.2), quiet(0.4), tone(1.9), quiet(0.4), tone(6.1)):
        buf.push(piece.tobytes())
    engine.step()
    start, dur = asr.windows[0]
    assert start == 0.0 and 5.5 <= dur <= 5.9          # последняя пауза в [4, 7]
    assert got[0]["t"] == 0.1 and got[0]["end"] == round(dur - 0.1, 2)
    assert engine._tracks["mic.wav"]["pos"] == dur     # следующее окно — с места резки


def test_no_window_before_enough_audio_then_on_the_next_step(tmp_path):
    asr = FakeGigaam()
    engine, buf, _ = _engine(tmp_path, asr)
    buf.push(tone(6.0).tobytes())
    assert engine.step() == 0                           # 6 с < 7 с — ждём
    buf.push(tone(1.5).tobytes())
    assert engine.step() == 1                           # такт, а не «20 с после прошлого»
    assert 4.0 <= asr.windows[0][1] <= 7.0


def test_falling_behind_merges_windows_up_to_20_seconds(tmp_path):
    asr = FakeGigaam()
    engine, buf, _ = _engine(tmp_path, asr)
    for _ in range(6):                                  # 30 с разом (распознавание отстало)
        buf.push(tone(4.6).tobytes())
        buf.push(quiet(0.4).tobytes())
    engine.step()
    first = asr.windows[0][1]
    assert 15.0 <= first <= 20.0
    assert sum(d for _, d in asr.windows) <= 30.0
    starts = [s for s, _ in asr.windows]
    assert starts == sorted(starts) and starts[1] == first   # окна встык, без дыр


def test_hopeless_backlog_is_skipped_and_logged(tmp_path):
    logs = []
    asr = FakeGigaam()
    engine, buf, _ = _engine(tmp_path, asr, log=logs.append)
    buf.push(tone(100.0).tobytes())
    engine.step()
    assert engine.stats["skipped_s"] == 80.0
    assert any("пропущено 80 с" in line for line in logs)
    assert asr.windows[0][0] == 80.0                     # таймкоды не съехали


def test_question_tail_never_waits_for_a_window_in_progress(tmp_path):
    asr = FakeGigaam(delay=0.5)
    engine, buf, got = _engine(tmp_path, asr)
    buf.push(tone(7.5).tobytes())
    worker = threading.Thread(target=engine.step)
    worker.start()
    time.sleep(0.1)                                      # окно распознаётся
    t = time.monotonic()
    assert engine.flush_tail() is False                  # не ждём его
    assert time.monotonic() - t < 0.1
    worker.join()
    buf.push(tone(1.0).tobytes())
    asr.delay = 0.0
    assert engine.flush_tail() is True                   # хвост — сразу, любой длины
    assert len(asr.windows) == 2
    assert round(sum(asr.windows[1]), 2) == 8.5          # весь остаток, до конца звука


def test_whisper_keeps_long_windows(tmp_path):
    class FakeWhisper(FakeGigaam):
        name = "Whisper"
        latin_pass = False
        policy = None                                     # окна движка: window_seconds

    asr = FakeWhisper()
    engine, buf, _ = _engine(tmp_path, asr, window_seconds=20.0)
    buf.push(tone(12.0).tobytes())
    assert engine.step() == 0
    buf.push(quiet(0.5).tobytes())
    buf.push(tone(9.0).tobytes())
    engine.step()
    assert 15.0 <= asr.windows[0][1] <= 20.0


def test_text_fixes_reach_each_line_and_latin_only_after_gigaam(tmp_path):
    class Says(FakeGigaam):
        def transcribe_window(self, audio, *, offset_s=0.0, **kw):
            return [Segment(offset_s, offset_s + 1.0, "Проверим апи и кафку")]

    seen = []

    def fixes(segments, *, latin):
        seen.append(latin)
        return TextFixes([{"from": "кафку", "to": "Kafka"}], ["API"], latin=True)(segments, latin=latin)

    engine, buf, got = _engine(tmp_path, Says(), text_fixes=fixes)
    buf.push(tone(1.0).tobytes())
    engine.process_window()
    assert seen == [True]
    assert got[0]["text"] == "Проверим API и Kafka"


def test_stats_line_has_timing_but_no_text(tmp_path):
    asr = FakeGigaam()
    engine, buf, _ = _engine(tmp_path, asr)
    buf.push(tone(8.0).tobytes())
    engine.step()
    line = engine.stats_line()
    assert "GigaAM" in line and "окон 1" in line and "окно" not in line.split("окон")[0]
    assert "с на секунду" in line
