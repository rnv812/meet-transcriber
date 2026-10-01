"""Обрезка хвоста автозаписи: план — чистыми функциями, ffmpeg — подменой;
один интеграционный тест на крошечных сгенерированных файлах."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from meet import library, tail

START = 1_790_000_000.0


def _folder(tmp_path, duration=1200.0, call_end_offset=300.0, tracks=("sys", "mic")):
    folder = tmp_path / "2026-09-30_10-00"
    folder.mkdir()
    for role in tracks:
        (folder / f"{role}.opus").write_bytes(b"x")
    events = [{"kind": "record.started", "at": START},
              {"kind": "record.stopped", "at": START + duration, "duration_s": duration}]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n",
                                         encoding="utf-8")
    if call_end_offset is not None:
        tail.write_call_end(folder, START + call_end_offset, 900.0)
    return folder


# --- план --------------------------------------------------------------------------


def test_parse_silences():
    err = ("[silencedetect @ 0] silence_start: 2.5\n"
           "[silencedetect @ 0] silence_end: 10 | silence_duration: 7.5\n"
           "[silencedetect @ 0] silence_start: 40\n")
    assert tail.parse_silences(err) == [(2.5, 10.0), (40.0, None)]


@pytest.mark.parametrize("silences, expected", [
    ([], 1200.0),                         # звук до самого конца
    ([(0.0, 900.0)], 300.0),              # тишина весь хвост (закрыта на EOF)
    ([(0.0, None)], 300.0),               # не закрыта
    ([(0.0, 60.0), (200.0, 900.0)], 500.0),  # разговор после конца сигнала до 500 с
    ([(0.0, 60.0), (200.0, 400.0)], 1200.0),  # тишина кончилась до конца — звук до конца
])
def test_last_activity(silences, expected):
    assert tail.last_activity(silences, 300.0, 1200.0) == expected


def test_trim_point_uses_the_latest_sound_on_either_track_plus_pad():
    # микрофон молчит с конца сигнала, а собеседников слышно до 500 с
    assert tail.trim_point(300.0, 1200.0, [300.0, 500.0]) == 530.0


def test_trim_point_never_cuts_before_the_signal_end():
    assert tail.trim_point(300.0, 1200.0, [10.0]) == 330.0


def test_trim_point_skips_small_savings():
    assert tail.trim_point(300.0, 380.0, [300.0]) is None  # сэкономит 50 с
    assert tail.trim_point(300.0, 1200.0, [1190.0]) is None


def test_commands():
    assert tail.silence_command(Path("a/sys.opus"), 300)[3:7] == ["-ss", "300.000", "-i", str(Path("a/sys.opus"))]
    cmd = tail.trim_command(Path("a/sys.opus"), Path("a/sys.opus.part"), 330.0)
    assert cmd[cmd.index("-t") + 1] == "330.000" and cmd[-1] == str(Path("a/sys.opus.part"))


def test_tail_bounds(tmp_path):
    assert tail.tail_bounds(_folder(tmp_path)) == (300.0, 1200.0)


def test_no_call_end_no_tail(tmp_path):
    assert tail.tail_bounds(_folder(tmp_path, call_end_offset=None)) is None


# --- с подменённым ffmpeg ------------------------------------------------------------


class FakeFfmpeg:
    def __init__(self, silences: dict, fail_on=None):
        self.silences = silences
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "silencedetect" in " ".join(cmd):
            track = Path(cmd[cmd.index("-i") + 1]).stem
            return subprocess.CompletedProcess(cmd, 0, "", self.silences[track])
        out = Path(cmd[-1])
        if self.fail_on and out.name.startswith(self.fail_on):
            out.write_bytes(b"half")
            return subprocess.CompletedProcess(cmd, 1, "", "диск полон")
        out.write_bytes(b"trimmed")
        return subprocess.CompletedProcess(cmd, 0, "", "")


QUIET = "silence_start: 0\nsilence_end: 900 | silence_duration: 900\n"
TALK_TILL_500 = "silence_start: 200\nsilence_end: 900 | silence_duration: 700\n"


def test_trim_replaces_all_tracks_and_records_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(tail.shutil, "which", lambda name: name)
    folder = _folder(tmp_path)
    run = FakeFfmpeg({"sys": TALK_TILL_500, "mic": QUIET})
    assert tail.trim(folder, run=run) == 530.0
    assert (folder / "sys.opus").read_bytes() == b"trimmed"
    assert (folder / "mic.opus").read_bytes() == b"trimmed"
    assert not list(folder.glob("*.part"))
    assert library.describe(folder).duration_s == 530.0
    assert tail.tail_bounds(folder) is None  # второй раз не режем


def test_failed_trim_keeps_the_tracks(tmp_path, monkeypatch):
    monkeypatch.setattr(tail.shutil, "which", lambda name: name)
    folder = _folder(tmp_path)
    with pytest.raises(RuntimeError):
        tail.trim(folder, run=FakeFfmpeg({"sys": QUIET, "mic": QUIET}, fail_on="mic"))
    assert (folder / "sys.opus").read_bytes() == b"x" and (folder / "mic.opus").read_bytes() == b"x"
    assert not list(folder.glob("*.part"))
    assert library.describe(folder).duration_s == 1200.0


def test_nothing_to_trim_runs_no_encoder(tmp_path, monkeypatch):
    monkeypatch.setattr(tail.shutil, "which", lambda name: name)
    folder = _folder(tmp_path)
    run = FakeFfmpeg({"sys": "", "mic": QUIET})  # собеседников слышно до конца
    assert tail.trim(folder, run=run) is None
    assert all("silencedetect" in " ".join(c) for c in run.calls)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")
def test_real_ffmpeg_finds_the_last_sound_and_trims(tmp_path):
    folder = tmp_path / "2026-09-30_10-00"
    folder.mkdir()
    # звук 5 с, потом 115 с тишины; сигнал пропал на 3-й секунде
    for role in ("sys", "mic"):
        src = "sine=f=440:d=5,apad=pad_dur=115" if role == "sys" else "anullsrc=r=16000:cl=mono:d=120"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                        "-c:a", "libopus", str(folder / f"{role}.opus")], check=True)
    events = [{"kind": "record.started", "at": START},
              {"kind": "record.stopped", "at": START + 120, "duration_s": 120.0}]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    tail.write_call_end(folder, START + 3, 117)
    cut = tail.trim(folder)
    assert cut == pytest.approx(35.0, abs=0.5)  # последний звук 5 с + 30 с
    from meet import merge

    assert merge.probe_duration(folder / "sys.opus") == pytest.approx(35.0, abs=0.3)
