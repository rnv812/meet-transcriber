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


def test_trim_point_is_the_signal_end_plus_pad():
    # разговор в комнате после звонка не хранится: режем по сигналу, а не по звуку
    assert tail.trim_point(300.0, 1200.0) == 330.0


def test_trim_point_skips_a_tail_shorter_than_the_threshold():
    assert tail.trim_point(300.0, 334.0) is None  # отрезать 4 с — не стоит перекодирования
    assert tail.trim_point(300.0, 340.0) == 330.0
    assert tail.trim_point(300.0, 320.0) is None  # запись кончилась раньше запаса


def test_commands():
    cmd = tail.trim_command(Path("a/sys.opus"), Path("a/sys.opus.part"), 330.0)
    assert cmd[cmd.index("-t") + 1] == "330.000" and cmd[-1] == str(Path("a/sys.opus.part"))


def test_tail_bounds(tmp_path):
    assert tail.tail_bounds(_folder(tmp_path)) == (300.0, 1200.0)


def test_no_call_end_no_tail(tmp_path):
    folder = _folder(tmp_path, call_end_offset=None)
    assert tail.tail_bounds(folder) is None
    assert tail.cut_point(folder) is None
    assert not tail.pending(folder)


def test_effective_end_for_merging_cuts_even_a_short_tail(tmp_path):
    assert tail.effective_end(_folder(tmp_path, duration=334.0)) == 330.0


def test_call_resumed_within_the_wait_uses_the_last_signal_end(tmp_path):
    """Звонок вернулся за время ожидания: конец сигнала — последний, и
    вернувшийся разговор остаётся целиком."""
    folder = _folder(tmp_path)  # первый обрыв на 300-й секунде
    tail.write_call_end(folder, START + 800.0, 400.0)  # вернулись, второй обрыв — на 800-й
    assert tail.cut_point(folder) == 830.0


def test_call_end_carries_the_transcribe_flag(tmp_path):
    folder = _folder(tmp_path, call_end_offset=None)
    tail.write_call_end(folder, START + 300.0, 600.0, transcribe=False)
    assert tail.call_end(folder) == {"kind": "record.call_end", "at": START + 300.0,
                                     "wait_s": 600.0, "transcribe": False}
    assert tail.pending(folder)


# --- с подменённым ffmpeg ------------------------------------------------------------


class FakeFfmpeg:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = Path(cmd[-1])
        if self.fail_on and out.name.startswith(self.fail_on):
            out.write_bytes(b"half")
            return subprocess.CompletedProcess(cmd, 1, "", "диск полон")
        out.write_bytes(b"trimmed")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_trim_replaces_all_tracks_and_records_duration(tmp_path):
    folder = _folder(tmp_path)
    run = FakeFfmpeg()
    assert tail.trim(folder, run=run) == 330.0
    assert len(run.calls) == 2 and all(c[c.index("-t") + 1] == "330.000" for c in run.calls)
    assert (folder / "sys.opus").read_bytes() == b"trimmed"
    assert (folder / "mic.opus").read_bytes() == b"trimmed"
    assert not list(folder.glob("*.part"))
    assert library.describe(folder).duration_s == 330.0
    assert tail.tail_bounds(folder) is None  # второй раз не режем
    assert not tail.pending(folder)


def test_failed_trim_keeps_the_tracks(tmp_path):
    folder = _folder(tmp_path)
    with pytest.raises(RuntimeError):
        tail.trim(folder, run=FakeFfmpeg(fail_on="mic"))
    assert (folder / "sys.opus").read_bytes() == b"x" and (folder / "mic.opus").read_bytes() == b"x"
    assert not list(folder.glob("*.part"))
    assert library.describe(folder).duration_s == 1200.0
    assert tail.pending(folder)  # резидент повторит после перезапуска


def test_short_tail_runs_no_encoder_and_is_marked_as_decided(tmp_path):
    folder = _folder(tmp_path, duration=332.0)
    run = FakeFfmpeg()
    assert tail.trim(folder, run=run) is None
    assert run.calls == []
    assert not tail.pending(folder)
    assert library.describe(folder).duration_s == 332.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")
def test_real_ffmpeg_trims_at_the_signal_end_plus_pad(tmp_path):
    folder = tmp_path / "2026-09-30_10-00"
    folder.mkdir()
    # звук 5 с, потом 115 с; сигнал пропал на 3-й секунде, а разговор в комнате
    # (здесь — тон до 60-й секунды) после конца звонка не сохраняется
    for role in ("sys", "mic"):
        src = "sine=f=440:d=5,apad=pad_dur=115" if role == "sys" else "sine=f=300:d=60,apad=pad_dur=60"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                        "-c:a", "libopus", str(folder / f"{role}.opus")], check=True)
    events = [{"kind": "record.started", "at": START},
              {"kind": "record.stopped", "at": START + 120, "duration_s": 120.0}]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    tail.write_call_end(folder, START + 3, 117)
    cut = tail.trim(folder)
    assert cut == pytest.approx(33.0, abs=0.01)  # конец сигнала 3 с + 30 с
    from meet import merge

    assert merge.probe_duration(folder / "mic.opus") == pytest.approx(33.0, abs=0.3)
