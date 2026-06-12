from pathlib import Path

from meet.tui import latest_recording


def _make(root: Path, name: str, tracks=("sys.wav", "mic.wav")) -> Path:
    d = root / name
    d.mkdir()
    for t in tracks:
        (d / t).write_bytes(b"")
    return d


def test_latest_recording_picks_newest_complete(tmp_path):
    _make(tmp_path, "2026-06-12_01-05")
    newest = _make(tmp_path, "2026-06-12_10-59")
    _make(tmp_path, "2026-06-12_23-59", tracks=("sys.wav",))  # дорожка потерялась
    assert latest_recording(tmp_path) == newest


def test_latest_recording_none_when_no_recordings(tmp_path):
    assert latest_recording(tmp_path) is None
    assert latest_recording(tmp_path / "missing") is None
