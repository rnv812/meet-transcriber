"""Файл для плеера карточки: обе стороны разговора в одной дорожке."""

import json
import os
import shutil
import subprocess
import threading
import time

import pytest

from meet import playback

# conftest глушит фоновое сведение во всех тестах; здесь проверяется настоящее.
REAL_SCHEDULE = playback.schedule


def _touch(path, data=b"x", mtime=None):
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class FakeRun:
    """subprocess.run для ffmpeg: пишет файл-результат (последний аргумент)."""

    def __init__(self, returncode=0, stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.returncode == 0:
            with open(cmd[-1], "wb") as f:
                f.write(b"OggS-mixed")
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


def test_sources_prefers_imported_source(tmp_path):
    _touch(tmp_path / "source.mp4")
    _touch(tmp_path / "sys.opus")
    assert playback.sources(tmp_path) == [tmp_path / "source.mp4"]


def test_sources_are_both_sides_of_a_call(tmp_path):
    _touch(tmp_path / "mic.opus")
    _touch(tmp_path / "sys.opus")
    assert playback.sources(tmp_path) == [tmp_path / "sys.opus", tmp_path / "mic.opus"]


def test_mix_command_normalizes_mic_and_mixes_both(tmp_path):
    sys_track, mic, out = tmp_path / "sys.opus", tmp_path / "mic.opus", tmp_path / "p.tmp"
    cmd = playback.mix_command(sys_track, mic, out)
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-i") + 1] == str(sys_track)
    assert cmd[len(cmd) - 1 - cmd[::-1].index("-i") + 1] == str(mic)
    graph = cmd[cmd.index("-filter_complex") + 1]
    # Тихий микрофон выравнивается по громкости, звук собеседников — как есть.
    assert "[1:a]" in graph and "speechnorm" in graph.split("[1:a]", 1)[1].split(";", 1)[0]
    assert "speechnorm" not in graph.split(";", 1)[0]
    assert "amix=inputs=2:duration=longest:normalize=0" in graph
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-c:a") + 1] == "libopus"
    assert cmd[cmd.index("-f") + 1] == "ogg"
    assert cmd[-1] == str(out)


def test_no_tracks_no_playback(tmp_path):
    run = FakeRun()
    assert playback.playback_path(tmp_path, run=run) is None
    assert run.calls == []


def test_single_track_is_played_as_is(tmp_path):
    only = _touch(tmp_path / "sys.opus")
    run = FakeRun()
    assert playback.playback_path(tmp_path, run=run) == only
    assert run.calls == []


def test_two_tracks_are_mixed_once_and_cached(tmp_path):
    _touch(tmp_path / "sys.opus", mtime=1_000_000)
    _touch(tmp_path / "mic.opus", mtime=1_000_000)
    run = FakeRun()
    path = playback.playback_path(tmp_path, run=run)
    assert path == tmp_path / playback.PLAYBACK_NAME
    assert path.read_bytes() == b"OggS-mixed"
    assert len(run.calls) == 1
    assert run.calls[0][-1] != str(path), "пишется во временный файл, затем подменяется"
    assert not list(tmp_path.glob("*.tmp"))
    stamp = json.loads((tmp_path / playback.STAMP_NAME).read_text(encoding="utf-8"))
    assert [s["name"] for s in stamp["sources"]] == ["sys.opus", "mic.opus"]
    # Второй запрос (перемотка, Range) — из кэша, без ffmpeg.
    assert playback.playback_path(tmp_path, run=run) == path
    assert len(run.calls) == 1


def _mixed_once(tmp_path):
    _touch(tmp_path / "sys.opus", mtime=1_000_000)
    _touch(tmp_path / "mic.opus", mtime=1_000_000)
    run = FakeRun()
    playback.playback_path(tmp_path, run=run)
    assert len(run.calls) == 1
    return run


def test_mix_is_rebuilt_when_a_track_changes_time(tmp_path):
    run = _mixed_once(tmp_path)
    os.utime(tmp_path / "mic.opus", (1_000_200, 1_000_200))  # дозапись/замена дорожки
    playback.playback_path(tmp_path, run=run)
    assert len(run.calls) == 2


def test_mix_is_rebuilt_when_a_track_changes_size_at_the_same_time(tmp_path):
    run = _mixed_once(tmp_path)
    _touch(tmp_path / "sys.opus", b"longer track", mtime=1_000_000)
    playback.playback_path(tmp_path, run=run)
    assert len(run.calls) == 2


def test_cache_without_stamp_is_not_trusted(tmp_path):
    """Обрезанный/чужой playback.opus без отметки о дорожках свежим не считается,
    даже если он новее дорожек."""
    _touch(tmp_path / "sys.opus", mtime=1_000_000)
    _touch(tmp_path / "mic.opus", mtime=1_000_000)
    _touch(tmp_path / playback.PLAYBACK_NAME, b"trunc", mtime=1_000_100)
    run = FakeRun()
    assert playback.playback_path(tmp_path, run=run).read_bytes() == b"OggS-mixed"
    assert len(run.calls) == 1


def test_empty_cache_is_rebuilt(tmp_path):
    run = _mixed_once(tmp_path)
    (tmp_path / playback.PLAYBACK_NAME).write_bytes(b"")
    playback.playback_path(tmp_path, run=run)
    assert len(run.calls) == 2


def test_busy_old_file_is_served_when_replace_is_refused(tmp_path, monkeypatch):
    """Старое сведение отдаётся плееру (файл открыт) — подменить нельзя: отдаём старое, не 500."""
    run = _mixed_once(tmp_path)
    (tmp_path / playback.PLAYBACK_NAME).write_bytes(b"old")
    os.utime(tmp_path / "mic.opus", (1_000_200, 1_000_200))
    monkeypatch.setattr(playback, "REPLACE_PAUSE_S", 0)
    real = os.replace

    def busy(src, dst):
        if str(dst).endswith(playback.PLAYBACK_NAME):
            raise PermissionError(32, "файл занят")
        return real(src, dst)

    monkeypatch.setattr(playback.os, "replace", busy)
    assert playback.playback_path(tmp_path, run=run).read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tmp"))


def test_busy_replace_without_old_file_is_an_error(tmp_path, monkeypatch):
    _touch(tmp_path / "sys.opus")
    _touch(tmp_path / "mic.opus")
    monkeypatch.setattr(playback, "REPLACE_PAUSE_S", 0)
    monkeypatch.setattr(playback.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError(32, "занят")))
    with pytest.raises(RuntimeError, match="занят"):
        playback.playback_path(tmp_path, run=FakeRun())


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("ffmpeg", 1), OSError("нет доступа")])
def test_ffmpeg_timeout_or_os_error_is_runtime_error(tmp_path, error):
    _touch(tmp_path / "sys.opus")
    _touch(tmp_path / "mic.opus")

    def run(cmd, **kwargs):
        raise error

    with pytest.raises(RuntimeError):
        playback.playback_path(tmp_path, run=run)
    assert not (tmp_path / playback.PLAYBACK_NAME).exists()


def test_mixes_share_a_global_limit(tmp_path, monkeypatch):
    entered = []

    class Slots:
        def __enter__(self):
            entered.append("in")

        def __exit__(self, *exc):
            entered.append("out")

    monkeypatch.setattr(playback, "_slots", Slots())
    _touch(tmp_path / "sys.opus")
    _touch(tmp_path / "mic.opus")
    playback.playback_path(tmp_path, run=FakeRun())
    assert entered == ["in", "out"]


def test_wait_idle_waits_for_open_streams(tmp_path):
    with playback.using(tmp_path):
        assert playback.wait_idle(tmp_path, timeout=0.05) is False
    assert playback.wait_idle(tmp_path, timeout=0.05) is True

    held = threading.Event()
    done = threading.Event()

    def stream():
        with playback.using(tmp_path):
            held.set()
            time.sleep(0.2)
        done.set()

    threading.Thread(target=stream).start()
    held.wait(1)
    assert playback.wait_idle(tmp_path, timeout=2) is True
    assert done.is_set()


def test_schedule_mixes_in_background_one_at_a_time(tmp_path, monkeypatch):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    seen = []
    started, gate, finished = threading.Event(), threading.Event(), threading.Event()

    def fake(folder):
        if folder == first:
            started.set()
            gate.wait(2)
        seen.append(folder)
        if folder == second:
            finished.set()

    monkeypatch.setattr(playback, "playback_path", fake)
    monkeypatch.setattr(playback, "schedule", REAL_SCHEDULE)
    playback.schedule(first)
    assert started.wait(2)
    playback.schedule(second)
    playback.schedule(second)  # пока ждёт в очереди — повтор не добавляется
    gate.set()
    assert finished.wait(2)
    time.sleep(0.1)
    assert seen == [first, second]


def test_failed_mix_raises_and_leaves_nothing(tmp_path):
    _touch(tmp_path / "sys.opus")
    _touch(tmp_path / "mic.opus")
    run = FakeRun(returncode=1, stderr="Invalid data found")
    with pytest.raises(RuntimeError, match="Invalid data"):
        playback.playback_path(tmp_path, run=run)
    assert not (tmp_path / playback.PLAYBACK_NAME).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_ffmpeg_is_an_error(tmp_path, monkeypatch):
    _touch(tmp_path / "sys.opus")
    _touch(tmp_path / "mic.opus")
    monkeypatch.setattr(playback.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        playback.playback_path(tmp_path, run=FakeRun())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")
def test_real_ffmpeg_mixes_both_tracks(tmp_path):
    """Настоящий ffmpeg принимает граф: обе дорожки есть в результате."""
    for name, src in (("sys.opus", "sine=f=440:d=2"), ("mic.opus", "sine=f=220:d=3,volume=0.05")):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                        "-c:a", "libopus", str(tmp_path / name)], check=True)
    path = playback.playback_path(tmp_path)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=channels",
                            "-of", "default=nw=1", str(path)], capture_output=True, text=True, check=True)
    assert "channels=1" in probe.stdout
    duration = float(probe.stdout.split("duration=")[1].split()[0])
    assert 2.8 < duration < 3.3, "длина — по более длинной дорожке"
