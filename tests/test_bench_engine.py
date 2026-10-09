"""Замеры движка (0.5.1): scripts/bench_engine.py — время и память шагов на
своей записи, сверка с прошлым прогоном (слова, разметка спикеров) и порогами
`scripts/bench_targets.json`. Здесь — его расчёты; сам замер требует движка и
видеокарты. И порог, который проверяется без них: память спектра Whisper."""

import importlib.util
import json
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bench_engine.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("bench_engine", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_der_is_zero_for_the_same_turns_whatever_the_labels(bench):
    ref = [(0.0, 10.0, "A"), (10.0, 20.0, "B")]
    assert bench.der(ref, [(0.0, 10.0, "x"), (10.0, 20.0, "y")]) == pytest.approx(0.0)
    # Вторая половина отдана первому спикеру — путаница на половине речи.
    assert bench.der(ref, [(0.0, 20.0, "x")]) == pytest.approx(0.5)
    assert bench.der(ref, []) == pytest.approx(1.0)  # всё пропущено


def test_word_report_counts_changed_words_and_shifts_of_matched_ones(bench):
    ref = [("да", 0.0, 0.2), ("мы", 0.3, 0.5), ("начали", 0.6, 1.0), ("встречу", 1.1, 1.5)]
    hyp = [("да", 0.0, 0.2), ("мы", 0.35, 0.5), ("начинаем", 0.6, 1.0), ("встречу", 1.1, 1.5)]
    report = bench.word_report(ref, hyp)
    assert report["changed"] == pytest.approx(0.25)
    assert report["shifted"] == pytest.approx(1 / 3)  # «мы» сдвинулось на 50 мс
    assert bench.word_report([], [])["changed"] == 0.0


def test_check_reports_what_exceeds_the_targets(bench):
    targets = {"gigaam": {"max_s_per_hour": 30, "max_private_gb": 4, "max_changed": 0.05},
               "diarize": {"max_der": 0.05}}
    results = {"gigaam": {"s_per_hour": 40.0, "private_gb": 3.0, "changed": 0.01},
               "diarize": {"der": 0.10}, "align": {"s_per_hour": 99.0}}
    failures = bench.check(results, targets)
    assert len(failures) == 2
    assert any("gigaam" in f and "s_per_hour" in f for f in failures)
    assert any("diarize" in f and "der" in f for f in failures)
    assert bench.check({"gigaam": {"s_per_hour": 20.0}}, targets) == []


def test_targets_file_is_valid_and_names_known_steps(bench):
    data = json.loads((ROOT / "scripts" / "bench_targets.json").read_text(encoding="utf-8"))
    assert data["profiles"]
    for profile in data["profiles"].values():
        assert set(profile["steps"]) <= set(bench.STEPS)
        for limits in profile["steps"].values():
            assert all(k.startswith("max_") and isinstance(v, (int, float)) for k, v in limits.items())
            # Качество — не хуже 5% против прошлого прогона (договорённость 0.5.1).
            assert limits.get("max_changed", 0) <= 0.05 and limits.get("max_der", 0) <= 0.05


def test_whisper_spectrogram_of_ten_minutes_stays_small():
    """Порог памяти (0.5.1): спектр 10 минут блоками — не больше 120 МБ сверх
    результата (было 587 МБ одним куском у faster-whisper, блоками — 65 МБ)."""
    from meet import whisper_features as wf

    class Extractor:
        n_fft, hop_length, sampling_rate = 400, 160, 16000
        mel_filters = np.random.default_rng(1).random((128, 201)).astype(np.float32)

    audio = np.random.default_rng(2).standard_normal(10 * 60 * 16000).astype(np.float32) * 0.1
    tracemalloc.start()
    try:
        out = wf.log_mel(Extractor(), audio)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert (peak - out.nbytes) / 2**20 < 120
