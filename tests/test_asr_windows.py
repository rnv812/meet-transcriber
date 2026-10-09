"""Длинная запись — окнами (0.5.1): память не растёт с длиной записи.

faster-whisper строит спектр сразу всей записи: на 88 минутах — 1,55 ГБ одним
куском, и при занятой памяти машины распознавание падало с MemoryError. Окно
до WINDOW_S, разрез — в паузе, связь между окнами — хвостом текста в подсказке.
"""

import sys
import types
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from meet import asr


def test_short_record_is_one_window():
    assert asr.plan_windows([(1.0, 50.0)], 60.0, window_s=300, min_s=180) == [(0.0, 60.0)]


def test_cut_lands_in_the_longest_pause_inside_the_allowed_range():
    # Паузы: 100–102 (рано), 200–210 (длинная), 250–252 (короче), конец 700.
    regions = [(0, 100), (102, 200), (210, 250), (252, 700)]
    windows = asr.plan_windows(regions, 700.0, window_s=300, min_s=180)
    assert windows[0] == (0.0, 205.0)
    assert windows[0][1] == windows[1][0] and windows[-1][1] == 700.0
    assert all(end - start <= 300 + 1e-9 for start, end in windows)


def test_no_pause_means_a_hard_cut_at_the_window_size():
    windows = asr.plan_windows([(0, 900)], 900.0, window_s=300, min_s=180)
    assert windows == [(0.0, 300.0), (300.0, 600.0), (600.0, 900.0)]


def _wav(path, seconds, sr=16000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.zeros(int(seconds * sr), dtype=np.int16).tobytes())
    return path


def _fake_whisper(monkeypatch, calls):
    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, audio, **kw):
            calls.append({"seconds": len(audio) / 16000, **kw})
            n = len(calls)
            word = SimpleNamespace(start=1.0, end=1.5, word=f" слово{n}")
            seg = SimpleNamespace(start=1.0, end=2.0, text=f" Текст окна {n}.", words=[word],
                                  no_speech_prob=0.0, avg_logprob=-0.1)
            return iter([seg]), SimpleNamespace(duration=len(audio) / 16000)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(asr, "_add_nvidia_dll_dirs", lambda: None)
    monkeypatch.setattr(asr, "_apply_hf_token", lambda: None)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cuda")
    monkeypatch.setattr(asr, "_asr_settings", lambda: ("m", "ru"))


def test_long_record_is_transcribed_window_by_window_with_offsets_and_context(monkeypatch, tmp_path):
    calls, progress = [], []
    _fake_whisper(monkeypatch, calls)
    monkeypatch.setattr(asr, "WINDOW_S", 5.0)
    monkeypatch.setattr(asr, "WINDOW_MIN_S", 3.0)
    monkeypatch.setattr(asr, "_speech_regions", lambda audio: [(0.0, 4.0), (4.5, 13.0)])
    segs = asr.transcribe_wav(_wav(tmp_path / "long.wav", 13.0), hotwords="Kafka", on_progress=progress.append)
    # Окна: 0–4.25 (пауза 4–4.5), 4.25–9.25, 9.25–13.
    assert [round(c["seconds"], 2) for c in calls] == [4.25, 5.0, 3.75]
    assert [round(s.start, 2) for s in segs] == [1.0, 5.25, 10.25]
    assert [round(s.words[0].start, 2) for s in segs] == [1.0, 5.25, 10.25]
    # Связь окон — хвост прошлого текста; подсказки терминов — в каждом окне.
    assert calls[0].get("initial_prompt") is None
    assert calls[1]["initial_prompt"].endswith("Текст окна 1.")
    assert all(c["hotwords"] == "Kafka" and c["vad_filter"] and c["word_timestamps"] for c in calls)
    assert progress == sorted(progress) and progress[-1] == pytest.approx(1.0)


def test_short_record_keeps_a_single_call_without_prompt(monkeypatch, tmp_path):
    calls = []
    _fake_whisper(monkeypatch, calls)
    asr.transcribe_wav(_wav(tmp_path / "short.wav", 2.0))
    assert len(calls) == 1 and calls[0].get("initial_prompt") is None
