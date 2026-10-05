"""Калибровка порога трёх третей (scripts/owner_enroll_calib.py): только
синтетика — поддельные VAD и эмбеддер."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

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


def test_meeting_mode_reads_a_copy_and_deletes_it(tmp_path, monkeypatch, capsys):
    """--meeting: папка записи только читается, копия — во временной папке и
    удаляется; печатаются одни числа."""
    calib = _calib()
    folder = tmp_path / "Local" / "meet" / "recordings" / "2026-10-05_10-00"
    folder.mkdir(parents=True)
    (folder / "mic.opus").write_bytes(b"opus")
    before = sorted((p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in folder.iterdir())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    seen = {}

    def fake_run(mics, syss, args):
        (mic,) = mics
        seen.update(mic=mic, exists=mic.exists(), inside=folder not in mic.parents, syss=syss,
                    data=os.environ["MEET_DATA_DIR"], offline=os.environ.get("HF_HUB_OFFLINE"),
                    token=credentials.get_hf_token())
        return 0

    import os

    from meet import credentials

    monkeypatch.setattr(credentials, "get_hf_token", lambda: "секрет")
    monkeypatch.setattr(calib, "run", fake_run)
    assert calib.main(["--meeting", "2026-10-05_10-00"]) == 0
    assert seen["exists"] and seen["inside"] and seen["syss"] == []
    assert seen["offline"] == "1" and "owner-calib-" in seen["data"] and seen["token"] is None
    assert not seen["mic"].exists() and not seen["mic"].parent.exists()
    assert sorted((p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in folder.iterdir()) == before


def test_meeting_name_must_be_a_plain_folder(tmp_path, monkeypatch):
    calib = _calib()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(SystemExit):
        calib.meeting_folder("../секреты")
    with pytest.raises(SystemExit):
        calib.meeting_folder("2026-10-05_10-00")  # нет дорожки


def test_run_prints_numbers_only(monkeypatch, capsys):
    import json as _json
    from types import SimpleNamespace

    calib = _calib()
    from meet import segvoices

    monkeypatch.setattr(calib.owner_enroll, "load_embedder", lambda: lambda part: np.ones(2))
    monkeypatch.setattr(calib.owner_enroll, "_default_vad", lambda a, sr: [(0.5, 24.5)])
    monkeypatch.setattr(segvoices, "decode", lambda path, rate: _tone(speech=(0.5, 24.5)))
    calib.run([Path("mic.opus")], [], SimpleNamespace(window=25.0, max=12, out=None))
    lines = capsys.readouterr().out.strip().splitlines()
    rows = [_json.loads(line) for line in lines]
    assert rows[0] == {"window": 1, "speech_s": 24.0, "snr_db": rows[0]["snr_db"], "min_cos": 1.0}
    assert rows[-1]["usable"] == 1
    for row in rows:
        assert all(isinstance(v, (int, float, dict)) for v in row.values())
