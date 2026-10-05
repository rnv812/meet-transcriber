"""Калибровка порога трёх третей (scripts/owner_enroll_calib.py): только
синтетика — поддельные VAD и эмбеддер."""

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000


def _calib():
    spec = importlib.util.spec_from_file_location("owner_enroll_calib", ROOT / "scripts" / "owner_enroll_calib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tone(seconds=25.0, speech=(1.0, 21.0)):
    t = np.arange(int(seconds * RATE)) / RATE
    audio = np.random.default_rng(0).normal(0, 0.001, t.size)
    m = (t >= speech[0]) & (t < speech[1])
    audio[m] += 0.1 * np.sin(2 * np.pi * 220 * t[m])
    return (audio * 32767).astype(np.int16)


def test_window_stats_reports_cos_even_below_threshold():
    calib = _calib()
    vecs = iter([np.array([1.0, 0.0]), np.array([0.6, 0.8]), np.array([1.0, 0.1])])
    got = calib.window_stats(_tone(), vad=lambda a, sr: [(1.0, 21.0)], embed=lambda part: next(vecs))
    assert got["speech_s"] == 20.0 and got["snr_db"] > 30
    assert got["min_cos"] == min(got["cos"]) and round(got["cos"][0], 2) == 0.6


def test_short_speech_window_has_no_cos():
    calib = _calib()
    got = calib.window_stats(_tone(speech=(1.0, 5.0)), vad=lambda a, sr: [(1.0, 5.0)],
                             embed=lambda part: np.ones(2))
    assert got["min_cos"] is None and got["speech_s"] == 4.0


def test_summary_percentiles_and_pass_counts():
    calib = _calib()
    windows = [{"min_cos": x} for x in (0.62, 0.71, 0.74, 0.78, 0.82)] + [{"min_cos": None}]
    got = calib.summary(windows)
    assert got["windows"] == 6 and got["usable"] == 5 and got["min"] == 0.62
    assert got["pass"]["0.70"] == 4 and got["pass"]["0.75"] == 2


def test_quiet_sys_check():
    calib = _calib()
    silent = np.zeros(RATE * 25, dtype=np.int16)
    loud = _tone()
    assert calib.quiet(silent, 0, len(silent)) is True
    assert calib.quiet(loud, 0, len(loud)) is False
