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


@pytest.fixture(autouse=True)
def _ffmpeg_on_path(monkeypatch):
    """Тесты с FakeRun не зависят от ffmpeg на машине (на CI его нет в PATH).
    Тест про отсутствие ffmpeg переопределяет это своим monkeypatch."""
    real_which = shutil.which
    monkeypatch.setattr(
        playback.shutil, "which",
        lambda name, *a, **k: "ffmpeg" if name == "ffmpeg" else real_which(name, *a, **k))


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


# --- сдвиг микрофона старой записи (meet.track_sync) ---------------------------------


def _mic_chain(cmd):
    graph = cmd[cmd.index("-filter_complex") + 1]
    return graph.split("[1:a]", 1)[1].split(";", 1)[0], graph.split(";", 1)[0]


def test_mix_command_delays_the_mic_by_a_positive_offset(tmp_path):
    cmd = playback.mix_command(tmp_path / "sys.opus", tmp_path / "mic.opus", tmp_path / "p.tmp",
                               mic_offset=0.4)
    mic, sys_ = _mic_chain(cmd)
    assert "adelay=delays=400" in mic and "all=1" in mic
    assert "adelay" not in sys_ and "atrim" not in sys_


def test_mix_command_trims_the_mic_start_by_a_negative_offset(tmp_path):
    """Отрицательный сдвиг — как во временной копии при расшифровке: начало
    микрофона срезано, звук собеседников не трогаем (его время — время текста)."""
    cmd = playback.mix_command(tmp_path / "sys.opus", tmp_path / "mic.opus", tmp_path / "p.tmp",
                               mic_offset=-0.25)
    mic, sys_ = _mic_chain(cmd)
    assert "atrim=start=0.250000" in mic and "asetpts=PTS-STARTPTS" in mic
    assert "adelay" not in sys_ and "atrim" not in sys_


def _call(tmp_path, transcript=None, meta=None):
    from meet import library

    _touch(tmp_path / "sys.opus", mtime=1_000_000)
    _touch(tmp_path / "mic.opus", mtime=1_000_000)
    if transcript is not None:
        library.write_transcript(tmp_path, transcript)
    if meta is not None:
        library.write_meta(tmp_path, meta)


def test_applied_offset_delays_the_mic_in_the_mix(tmp_path):
    from meet import track_sync

    _call(tmp_path, {"segments": [], track_sync.APPLIED_KEY: 0.4})
    run = FakeRun()
    playback.playback_path(tmp_path, run=run)
    assert "adelay=delays=400" in _mic_chain(run.calls[0])[0]
    stamp = json.loads((tmp_path / playback.STAMP_NAME).read_text(encoding="utf-8"))
    assert stamp["mic_offset_s"] == pytest.approx(0.4)


def test_estimated_but_not_applied_offset_is_not_in_the_mix(tmp_path):
    from meet import track_sync

    _call(tmp_path, {"segments": []}, meta={track_sync.META_KEY: {"mic_s": 0.4, "method": "durations"}})
    run = FakeRun()
    playback.playback_path(tmp_path, run=run)
    mic, _ = _mic_chain(run.calls[0])
    assert "adelay" not in mic and "atrim" not in mic


def test_changed_offset_makes_the_mix_stale(tmp_path):
    """Перерасшифровка применила сдвиг — дорожки те же, а сведение уже не то."""
    from meet import library, track_sync

    _call(tmp_path, {"segments": []})
    run = FakeRun()
    playback.playback_path(tmp_path, run=run)
    assert len(run.calls) == 1
    library.write_transcript(tmp_path, {"segments": [], track_sync.APPLIED_KEY: 0.4})
    playback.playback_path(tmp_path, run=run)
    assert len(run.calls) == 2
    assert "adelay=delays=400" in _mic_chain(run.calls[1])[0]
    playback.playback_path(tmp_path, run=run)  # тот же сдвиг — из кэша
    assert len(run.calls) == 2


def test_old_stamp_without_offset_stays_fresh_for_an_unshifted_recording(tmp_path):
    """Отметка сведения прежних версий (без сдвига) у записи без сдвига —
    свежая: обновление не пересводит библиотеку."""
    _call(tmp_path, {"segments": []})
    run = FakeRun()
    playback.playback_path(tmp_path, run=run)
    stamp_path = tmp_path / playback.STAMP_NAME
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    stamp_path.write_text(json.dumps({"sources": stamp["sources"]}), encoding="utf-8")
    playback.playback_path(tmp_path, run=run)
    assert len(run.calls) == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")
def test_real_ffmpeg_mixes_a_delayed_mic(tmp_path):
    """Граф с задержкой микрофона принимает настоящий ffmpeg: сведение длиннее
    на сдвиг."""
    from meet import library, track_sync

    for name, src in (("sys.opus", "sine=f=440:d=2"), ("mic.opus", "sine=f=220:d=2,volume=0.05")):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                        "-c:a", "libopus", str(tmp_path / name)], check=True)
    library.write_transcript(tmp_path, {"segments": [], track_sync.APPLIED_KEY: 1.0})
    path = playback.playback_path(tmp_path)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    assert 2.8 < float(probe.stdout.strip()) < 3.3


# --- дорожка «Вы» для плеера: микрофон на шкале текста ---------------------------------


def test_mic_path_without_applied_offset_is_the_raw_mic(tmp_path):
    """Расшифровано без сдвига — микрофон играется как есть, без ffmpeg."""
    _call(tmp_path, {"segments": []})
    run = FakeRun()
    assert playback.mic_path(tmp_path, run=run) == tmp_path / "mic.opus"
    assert run.calls == []


def test_mic_path_ignores_an_estimated_but_not_applied_offset(tmp_path):
    from meet import track_sync

    _call(tmp_path, {"segments": []}, meta={track_sync.META_KEY: {"mic_s": 0.4, "method": "durations"}})
    run = FakeRun()
    assert playback.mic_path(tmp_path, run=run) == tmp_path / "mic.opus"
    assert run.calls == []


def test_mic_path_without_mic_is_none(tmp_path):
    _touch(tmp_path / "sys.opus")
    assert playback.mic_path(tmp_path, run=FakeRun()) is None


def test_mic_path_with_applied_offset_is_shifted_once_and_cached(tmp_path):
    """Применённый сдвиг — микрофон сдвигается тем же фильтром, что у сведения,
    пишется рядом и отдаётся из кэша, пока дорожка и сдвиг те же."""
    from meet import library, track_sync

    _call(tmp_path, {"segments": [], track_sync.APPLIED_KEY: 0.4})
    run = FakeRun()
    path = playback.mic_path(tmp_path, run=run)
    assert path == tmp_path / playback.MIC_NAME
    assert path.read_bytes() == b"OggS-mixed"
    assert len(run.calls) == 1
    cmd = run.calls[0]
    assert cmd[cmd.index("-i") + 1] == str(tmp_path / "mic.opus")
    assert "adelay=delays=400" in cmd[cmd.index("-af") + 1]
    assert cmd[-1] != str(path), "пишется во временный файл, затем подменяется"
    assert not list(tmp_path.glob("*.tmp"))
    # Сведение обеих сторон не тронуто.
    assert not (tmp_path / playback.PLAYBACK_NAME).exists()
    assert playback.mic_path(tmp_path, run=run) == path
    assert len(run.calls) == 1
    # Перерасшифровка сменила сдвиг — дорожка «Вы» строится заново.
    library.write_transcript(tmp_path, {"segments": [], track_sync.APPLIED_KEY: -0.25})
    playback.mic_path(tmp_path, run=run)
    assert len(run.calls) == 2
    assert "atrim=start=0.250000" in run.calls[1][run.calls[1].index("-af") + 1]


def test_mic_path_is_rebuilt_when_the_mic_changes(tmp_path):
    from meet import track_sync

    _call(tmp_path, {"segments": [], track_sync.APPLIED_KEY: 0.4})
    run = FakeRun()
    playback.mic_path(tmp_path, run=run)
    os.utime(tmp_path / "mic.opus", (1_000_200, 1_000_200))
    playback.mic_path(tmp_path, run=run)
    assert len(run.calls) == 2


def test_mic_path_failure_is_a_runtime_error(tmp_path):
    from meet import track_sync

    _call(tmp_path, {"segments": [], track_sync.APPLIED_KEY: 0.4})
    with pytest.raises(RuntimeError, match="ffmpeg"):
        playback.mic_path(tmp_path, run=FakeRun(returncode=1, stderr="плохой файл"))
    assert not (tmp_path / playback.MIC_NAME).exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")
def test_real_ffmpeg_shifts_the_mic(tmp_path):
    """Настоящий ffmpeg: дорожка «Вы» длиннее сырой на применённый сдвиг."""
    from meet import library, track_sync

    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=220:d=2", "-c:a", "libopus", str(tmp_path / "mic.opus")], check=True)
    library.write_transcript(tmp_path, {"segments": [], track_sync.APPLIED_KEY: 1.0})
    path = playback.mic_path(tmp_path)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    assert 2.8 < float(probe.stdout.strip()) < 3.3


# --- WebM для окна на macOS ---------------------------------------------------------


def test_webm_command_copies_the_stream_without_reencoding(tmp_path):
    """Ogg/Opus → WebM без перекодирования: поток копируется как есть."""
    src, out = tmp_path / "playback.opus", tmp_path / "x.tmp"
    cmd = playback.webm_command(src, out)
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-i") + 1] == str(src)
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert "libopus" not in cmd
    assert cmd[cmd.index("-f") + 1] == "webm"
    assert cmd[-1] == str(out)


def test_webm_path_remuxes_once_and_caches_next_to_the_source(tmp_path):
    src = _touch(tmp_path / "sys.opus", mtime=1_000_000)
    _touch(tmp_path / "mic.opus", mtime=1_000_000)
    run = FakeRun()
    path = playback.webm_path(src, run=run)
    assert path.parent == tmp_path and path.suffix == ".webm"
    assert path.read_bytes() == b"OggS-mixed"
    assert len(run.calls) == 1
    assert run.calls[0][run.calls[0].index("-i") + 1] == str(src)
    assert run.calls[0][-1] != str(path), "пишется во временный файл, затем подменяется"
    assert not list(tmp_path.glob("*.tmp"))
    # Кэш не принимается за дорожку записи.
    assert playback.sources(tmp_path) == [tmp_path / "sys.opus", tmp_path / "mic.opus"]
    # Дорожка та же — кэш свежий.
    assert playback.webm_path(src, run=run) == path
    assert len(run.calls) == 1
    # Дорожка изменилась (свели заново, перерасшифровали) — перепаковка заново.
    _touch(src, b"other", mtime=1_000_100)
    assert playback.webm_path(src, run=run) == path
    assert len(run.calls) == 2


def test_webm_caches_of_different_tracks_do_not_collide(tmp_path):
    run = FakeRun()
    paths = {playback.webm_path(_touch(tmp_path / name), run=run)
             for name in ("sys.opus", "mic.opus", playback.PLAYBACK_NAME, playback.MIC_NAME)}
    assert len(paths) == 4


@pytest.mark.parametrize("name", ["source.mp3", "source.m4a", "source.wav", "source.webm"])
def test_webm_path_of_a_track_webkit_plays_is_the_track_itself(tmp_path, name):
    src = _touch(tmp_path / name)
    run = FakeRun()
    assert playback.webm_path(src, run=run) == src
    assert run.calls == []


def test_webm_failure_is_a_runtime_error_and_leaves_nothing(tmp_path):
    src = _touch(tmp_path / "sys.opus")
    with pytest.raises(RuntimeError, match="ffmpeg.*плохой файл"):
        playback.webm_path(src, run=FakeRun(returncode=1, stderr="плохой файл"))
    assert not list(tmp_path.glob("*.webm"))
    assert not list(tmp_path.glob("*.tmp"))


def test_webm_without_ffmpeg_is_a_runtime_error(tmp_path, monkeypatch):
    src = _touch(tmp_path / "sys.opus")
    monkeypatch.setattr(playback.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="ffmpeg не найден"):
        playback.webm_path(src, run=FakeRun())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен")
def test_real_ffmpeg_remuxes_ogg_opus_into_webm(tmp_path):
    """Настоящий ffmpeg: WebM с тем же Opus и той же длительностью."""
    src = tmp_path / "sys.opus"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=440:d=2", "-c:a", "libopus", str(src)], check=True)
    path = playback.webm_path(src)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name,duration:stream=codec_name",
                            "-of", "default=nw=1", str(path)], capture_output=True, text=True, check=True)
    assert "codec_name=opus" in probe.stdout
    assert "webm" in probe.stdout.split("format_name=")[1].split()[0]
    assert 1.9 < float(probe.stdout.split("duration=")[1].split()[0]) < 2.1
