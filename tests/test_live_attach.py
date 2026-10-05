"""Ассистент, включённый посреди обычной записи: отвод звука записи, его
подключение к живому движку и догонялка уже записанного.

Устройств, ffmpeg и моделей здесь нет: запись — подделки PortAudio из
`test_recorder`, распознавание — поддельное, кусок дорожки — поддельный ffmpeg.
"""

import json
from types import SimpleNamespace
import socket
import threading
import time

import numpy as np
import pytest

from meet import live_catchup, pcm_tap, recorder
from meet.asr import Segment
from meet.live import LiveEngine
from meet.live_asr import GIGAAM_POLICY

SR = 16000


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("не дождались")
        time.sleep(0.01)


# --- отвод: хаб, сервер, клиент ------------------------------------------------


def _hub(tracks=((0, "sys.opus", 48000, 2), (1, "mic.opus", 16000, 1))):
    hub = pcm_tap.TapHub()
    hub.begin(len(tracks))
    for index, name, rate, channels in tracks:
        hub.configure(index, name, rate, channels)
    return hub


def test_hub_without_subscribers_takes_pushes_silently():
    hub = _hub()
    hub.push(0, b"\x01\x00" * 4, 0)
    hub.push(7, b"\x01\x00", 0)  # неизвестная дорожка — тоже не ошибка
    assert hub.subscribers() == 0


def test_subscriber_starts_where_the_recording_is_now():
    hub = _hub()
    hub.push(1, b"\x01\x00" * 10, 0)
    sub, tracks = hub.subscribe()
    assert [t["pos"] for t in tracks] == [0, 20]  # mic уже записал 20 байт
    hub.push(1, b"\x02\x00" * 5, 20)
    hub.push(1, b"\x00\x00" * 5, 30, pad=True)
    items = sub.take(0.1)
    assert items == [(1, 0, 20, b"\x02\x00" * 5), (1, pcm_tap.FLAG_PAD, 30, b"\x00\x00" * 5)]
    hub.end()
    assert sub.take(0.1) is None  # конец записи — EOF


def test_subscribe_refused_without_a_recording():
    hub = pcm_tap.TapHub()
    with pytest.raises(pcm_tap.TapError, match="не идёт"):
        hub.subscribe(timeout=0.1)


def test_slow_reader_loses_oldest_frames_not_the_recording():
    sub = pcm_tap._Subscriber(max_bytes=10)
    for i in range(5):
        sub.offer(0, 0, i * 4, b"abcd")
    items = sub.take(0.1)
    assert sub.dropped == 3 and [pos for _, _, pos, _ in items] == [12, 16]


def test_server_streams_frames_to_the_client_with_the_token():
    hub = _hub()
    hub.push(0, b"\x05\x00" * 4, 0)
    server = pcm_tap.TapServer(hub)
    client = pcm_tap.TapClient(server.port, server.token)
    try:
        assert [(t["name"], t["pos"]) for t in client.tracks] == [("sys.opus", 8), ("mic.opus", 0)]
        _wait(lambda: hub.subscribers() == 1)
        hub.push(0, b"\x06\x00" * 4, 8)
        hub.push(1, b"\x00\x00" * 2, 0, pad=True)
        hub.end()
        frames = list(client.frames())
        assert frames == [(0, False, 8, b"\x06\x00" * 4), (1, True, 0, b"\x00\x00" * 2)]
    finally:
        client.close()
        server.close()
    server.join(5)
    assert server.closed


def test_wrong_token_is_turned_away_and_the_right_one_still_works():
    hub = _hub()
    server = pcm_tap.TapServer(hub)
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as bad:
            bad.sendall(b"0" * 32 + b"\n")
            assert bad.recv(100) == b""  # закрыто без заголовка
        assert hub.subscribers() == 0
        client = pcm_tap.TapClient(server.port, server.token)
        assert len(client.tracks) == 2
        client.close()
    finally:
        server.close()


def test_client_gone_unsubscribes_and_pushes_keep_working():
    hub = _hub()
    server = pcm_tap.TapServer(hub)
    client = pcm_tap.TapClient(server.port, server.token)
    _wait(lambda: hub.subscribers() == 1)
    client.close()  # ассистент упал или выключен
    # Сервер замечает обрыв на следующей записи в сокет.
    deadline = time.monotonic() + 5
    while hub.subscribers() and time.monotonic() < deadline:
        hub.push(0, b"\x01\x00" * 4096, 0)
        time.sleep(0.01)
    assert hub.subscribers() == 0
    hub.push(0, b"\x01\x00" * 4, 0)  # запись идёт дальше, без ошибок
    server.close()


def test_server_reports_a_stopped_recording_to_the_client():
    hub = _hub()
    hub.end()
    server = pcm_tap.TapServer(hub)
    try:
        with pytest.raises(pcm_tap.TapError, match="не идёт"):
            pcm_tap.TapClient(server.port, server.token)
    finally:
        server.close()


# --- запись отдаёт отводу ровно то, что пишет в файл -----------------------------


def _record_session(monkeypatch, tmp_path, hub):
    import test_recorder as tr

    tr._fake_audio(monkeypatch, render=tr._dev("Колонки", rate=48000, ch=2))
    session = recorder._Session(tmp_path, pcm_tap=hub)
    session.start()
    return session, tr


def _pcm(n, seed):
    return (np.random.default_rng(seed).integers(-3000, 3000, n)).astype("<i2").tobytes()


def test_tracks_feed_the_tap_with_the_same_bytes_as_the_file(tmp_path, monkeypatch):
    hub = pcm_tap.TapHub()
    session, tr = _record_session(monkeypatch, tmp_path, hub)
    sys_track, mic_track = session.tracks
    sub, tracks = hub.subscribe(timeout=1)
    assert [(t["name"], t["rate"], t["channels"]) for t in tracks] == [
        ("sys.opus", 48000, 2), ("mic.opus", 16000, 1)]
    chunks = [_pcm(2048, i) for i in range(5)]
    for chunk in chunks:
        sys_track.stream.pump(chunk)
        mic_track.stream.pump(chunk[:1024])
    items = sub.take(0.1)
    got_sys = b"".join(d for i, flags, _, d in items if i == 0 and not flags)
    got_mic = b"".join(d for i, flags, _, d in items if i == 1 and not flags)
    # Файл (ffmpeg) получил то же, что и раньше; отвод — точную копию.
    assert sys_track.writer.data.endswith(b"".join(chunks))
    assert got_sys == b"".join(chunks)
    assert got_mic == b"".join(c[:1024] for c in chunks)
    # Позиции — подряд, с учётом доливки до первого звука.
    ends = {}
    for index, _, pos, data in items:
        assert ends.get(index, pos) == pos
        ends[index] = pos + len(data)
    assert ends[0] == sys_track.bytes_written and ends[1] == mic_track.bytes_written
    session.close()
    rest = sub.take(0.1)
    while rest:  # хвост доливки до остановки, потом — EOF
        rest = sub.take(0.1)
    assert rest is None


def test_files_are_bit_identical_with_and_without_the_tap(tmp_path, monkeypatch):
    import test_recorder as tr

    data = [_pcm(2048, i) for i in range(8)]

    def run(folder, hub):
        tr._fake_audio(monkeypatch, render=tr._dev("Колонки", rate=48000, ch=2))
        session = recorder._Session(folder, pcm_tap=hub)
        session.start()
        sub = hub.subscribe(timeout=1)[0] if hub is not None else None
        for chunk in data:
            session.tracks[0].stream.pump(chunk)
        written = session.tracks[0].writer.data
        session.close()
        return written, sub

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain, _ = run(tmp_path / "a", None)
    tapped, sub = run(tmp_path / "b", pcm_tap.TapHub())
    # Доливка до первого звука зависит от часов — сравниваем звук целиком.
    assert plain.endswith(b"".join(data)) and tapped.endswith(b"".join(data))
    assert plain.lstrip(b"\x00") == tapped.lstrip(b"\x00")


def test_broken_tap_never_reaches_the_audio_callback(tmp_path, monkeypatch):
    pushes = []

    class Broken:
        def begin(self, n):
            return self

        def configure(self, *a):
            raise RuntimeError("сломан")

        def push(self, *a):
            pushes.append(a)
            raise RuntimeError("сломан")

        def end(self):
            raise RuntimeError("сломан")

    session, tr = _record_session(monkeypatch, tmp_path, Broken())
    mic = session.tracks[1]
    assert mic.stream.pump(b"\x01\x00" * 100) == (None, 0)  # paContinue, не paAbort
    assert pushes  # отвод звали — и его сбой проглочен
    assert mic.writer.data.endswith(b"\x01\x00" * 100)
    session.close()  # и закрытие записи не падает


def test_late_thread_of_the_previous_recording_cannot_touch_the_new_one():
    hub = pcm_tap.TapHub()
    old = hub.begin(1)
    old.configure(0, "sys.opus", 16000, 1)
    new = hub.begin(1)
    new.configure(0, "sys.opus", 16000, 1)
    sub, tracks = hub.subscribe(timeout=1)
    old.push(0, b"\x01\x00" * 4, 999)  # прошлая запись дописывает хвост
    old.end()
    assert hub.active() and sub.take(0.05) == []
    new.push(0, b"\x02\x00" * 4, 0)
    assert sub.take(0.05) == [(0, 0, 0, b"\x02\x00" * 4)]
    new.end()
    assert not hub.active()


def test_session_close_ends_the_tap(tmp_path, monkeypatch):
    hub = pcm_tap.TapHub()
    session, _ = _record_session(monkeypatch, tmp_path, hub)
    sub, _ = hub.subscribe(timeout=1)
    session.close()
    assert not hub.active()
    items = sub.take(0.1)
    # Хвост доливки до момента остановки ещё приходит, потом — EOF.
    while items:
        items = sub.take(0.1)
    assert items is None


# --- живой движок из отвода -----------------------------------------------------


class FakeGigaam:
    name = "GigaAM"
    latin_pass = False
    policy = GIGAAM_POLICY

    def __init__(self):
        self.windows: list[tuple[float, float]] = []
        self.loaded = self.unloaded = False

    def load(self):
        self.loaded = True

    def unload(self):
        self.unloaded = True

    def transcribe_window(self, audio, *, offset_s=0.0, hotwords=None, initial_prompt=None):
        dur = len(audio) / SR
        self.windows.append((round(offset_s, 2), round(dur, 2)))
        return [Segment(offset_s + 0.1, offset_s + dur - 0.1, f"окно {len(self.windows)}")]


class FakeTap:
    """Отвод записи: заголовок и кадры из очереди; close() — конец."""

    def __init__(self, tracks):
        self.tracks = tracks
        self._frames: list = []
        self._cond = threading.Condition()
        self._closed = False

    def feed(self, *frame):
        with self._cond:
            self._frames.append(frame)
            self._cond.notify_all()

    def end(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    close = end

    def frames(self):
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._frames or self._closed, 5)
                if self._frames:
                    frame = self._frames.pop(0)
                elif self._closed:
                    return
                else:
                    continue
            yield frame


def tone(seconds: float, amp: int = 6000) -> bytes:
    t = np.arange(int(seconds * SR)) / SR
    return (np.sin(2 * np.pi * 220 * t) * amp).astype("<i2").tobytes()


def _attached_engine(tmp_path, tap, **kw):
    got = []
    engine = LiveEngine(tmp_path, FakeGigaam(), speaker_name="Вы", mic_device=None,
                        output_device=None, on_entry=lambda line, entry: got.append(entry),
                        tap_connect=lambda: tap, **kw)
    return engine, got


def test_attached_engine_takes_no_lock_opens_no_device_and_keeps_record_time(tmp_path, monkeypatch):
    def no_audio():
        raise AssertionError("устройства не открываются")

    monkeypatch.setattr(recorder, "audio_backend", no_audio)
    folder = tmp_path / "rec" / "2026-10-03_10-00"
    folder.mkdir(parents=True)
    lock = tmp_path / "rec" / recorder.LOCK_NAME
    # Lock держит запись резидента (этот же процесс): ассистент его не трогает.
    taken = {"pid": __import__("os").getpid(), "folder": str(folder)}
    lock.write_text(json.dumps(taken), encoding="utf-8")
    # Запись уже 600 с: mic.opus 16 кГц моно — позиция 600 с в байтах.
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 600 * SR * 2}])
    ended = threading.Event()
    engine, got = _attached_engine(folder, tap, on_source_end=ended.set)
    engine.start()
    try:
        assert engine.attach_positions == {"mic.wav": 600.0}
        tap.feed(1, False, 600 * SR * 2, tone(6.0))
        _wait(lambda: (engine.process_window(), bool(got))[1])
        assert got[0]["t"] >= 600.0  # таймкоды — время записи
        assert not (folder / "mic.opus").exists() and not (folder / "sys.opus").exists()
        tap.end()  # запись остановлена
        assert ended.wait(5)
    finally:
        engine.stop()
    assert json.loads(lock.read_text(encoding="utf-8")) == taken  # lock записи не тронут
    assert engine._transcriber.unloaded


def test_attached_engine_fills_dropped_frames_with_silence(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    engine, _ = _attached_engine(tmp_path, tap)
    engine.start()
    try:
        tap.feed(1, False, 0, b"\x01\x00" * SR)        # 0–1 с
        tap.feed(1, False, 3 * SR * 2, b"\x01\x00" * SR)  # 3–4 с: 2 с выброшено
        tap.end()
        _wait(lambda: engine.source_ended.is_set())
        runs = engine._tracks["mic.wav"]["buffer"].drain_runs()
        total = sum(len(raw) for raw, _ in runs)
        assert total == 4 * SR * 2
        assert [pad for _, pad in runs] == [False, True, False]
    finally:
        engine.stop()


# --- догонялка ------------------------------------------------------------------


def test_plan_takes_what_was_recorded_capped_to_the_last_30_minutes(tmp_path):
    for name in ("sys.opus", "mic.opus"):
        (tmp_path / name).write_bytes(b"x")
    short = live_catchup.plan({"sys.wav": 600.0, "mic.wav": 600.2}, tmp_path)
    assert short["tracks"]["sys.wav"] == (tmp_path / "sys.opus", 0.0, 600.0)
    assert short["from_t"] == 0.0 and short["to_t"] == 600.2 and not short["capped"]
    assert short["total_s"] == pytest.approx(1200.2)
    long = live_catchup.plan({"mic.wav": 3600.0}, tmp_path)
    assert long["tracks"]["mic.wav"][1:] == (1800.0, 3600.0) and long["capped"]
    # Прошлое включение ассистента уже слышало до 3000 с — догоняем только дыру.
    gap = live_catchup.plan({"mic.wav": 3600.0}, tmp_path, heard=3000.0)
    assert gap["tracks"]["mic.wav"][1:] == (3000.0, 3600.0) and not gap["capped"]


def test_plan_skips_tiny_or_missing_pieces(tmp_path):
    (tmp_path / "mic.opus").write_bytes(b"x")
    plan = live_catchup.plan({"sys.wav": 600.0, "mic.wav": 0.4}, tmp_path)
    assert plan["tracks"] == {} and plan["total_s"] == 0 and plan["from_t"] is None


def test_heard_until_reads_the_last_stamp(tmp_path):
    path = tmp_path / "live_transcript.md"
    assert live_catchup.heard_until(path) is None
    path.write_text("[00:01:00] Вы: раз\n<!-- ошибка окна -->\n[00:02:05] Демьян: два\n",
                    encoding="utf-8")
    assert live_catchup.heard_until(path) == 125.0


def test_chronological_keeps_unstamped_lines_with_their_neighbour():
    lines = ["[00:10:00] Вы: потом", "<!-- ошибка -->", "[00:00:05] Демьян: сначала", ""]
    assert live_catchup.chronological(lines) == [
        "[00:00:05] Демьян: сначала", "[00:10:00] Вы: потом", "<!-- ошибка -->"]


def test_pcm_reader_reads_a_piece_through_ffmpeg_argv():
    seen = {}

    class Proc:
        def __init__(self, argv):
            seen["argv"] = argv
            self.stdout = __import__("io").BytesIO((np.ones(SR * 3, dtype="<i2") * 100).tobytes())

        def poll(self):
            return 0

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    reader = live_catchup.PcmReader("D:/rec/mic.opus", 30.0, 33.0, spawn=Proc)
    argv = seen["argv"]
    assert argv[argv.index("-ss") + 1] == "30.000" and argv[argv.index("-t") + 1] == "3.000"
    assert argv[argv.index("-ar") + 1] == "16000" and argv[-1] == "pipe:1"
    first = reader.read(2.0)
    assert len(first) == 2 * SR and abs(first[0] - 100 / 32768) < 1e-6
    assert len(reader.read(5.0)) == SR and reader.eof
    reader.close()


class FakeReader:
    """Кусок дорожки: тон заданной длины, отдаётся порциями."""

    def __init__(self, seconds):
        self._audio = np.frombuffer(tone(seconds), dtype="<i2").astype(np.float32) / 32768
        self.eof = False
        self.closed = False

    def read(self, seconds):
        n = int(seconds * SR)
        out, self._audio = self._audio[:n], self._audio[n:]
        if not len(out):
            self.eof = True
        return out

    def close(self):
        self.closed = True


def test_catchup_recognizes_the_start_marks_lines_and_reports_progress(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 60 * SR * 2}])
    engine, got = _attached_engine(tmp_path, tap)
    engine.start()
    try:
        tap.feed(1, False, 60 * SR * 2, tone(6.0))
        # Живая строка ленты — раньше догонялки (кадр читает поток отвода).
        _wait(lambda: (engine.process_window(), (tmp_path / "live_transcript.md").exists())[1])
        live_lines = (tmp_path / "live_transcript.md").read_text(encoding="utf-8")
        readers = {}

        def reader(path, start, end):
            readers[path] = FakeReader(end - start)
            return readers[path]

        assert engine.start_catchup({"mic.wav": (tmp_path / "mic.opus", 0.0, 60.0)},
                                    {"capped": False}, reader=reader)
        progress = engine.catchup_progress()
        assert progress["active"] and progress["total_s"] == 60.0 and progress["done_s"] == 0
        while engine.catchup_step(budget_s=0.0):
            p = engine.catchup_progress()
            assert 0 <= p["done_s"] <= 60.0
        done = engine.catchup_progress()
        assert not done["active"] and done["complete"] and done["done_s"] == 60.0
        caught = [e for e in got if e.get("catchup")]
        assert caught and all(e["t"] < 60.0 for e in caught)
        assert [e["t"] for e in caught] == sorted(e["t"] for e in caught)
        assert not any(e.get("catchup") for e in got[:1])
        # Лента в файле — по времени: начало встречи выше живой строки.
        text = (tmp_path / "live_transcript.md").read_text(encoding="utf-8").splitlines()
        assert text[-1] == live_lines.strip().splitlines()[-1]
        assert len(text) == len(caught) + len(live_lines.strip().splitlines())
        assert all(r.closed for r in readers.values())
    finally:
        tap.end()
        engine.stop()


def test_catchup_yields_to_live_windows(tmp_path):
    """Окна догонялки — между тактами живого звука, не дольше бюджета."""
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    engine, _ = _attached_engine(tmp_path, tap)
    engine.start()
    try:
        engine.start_catchup({"mic.wav": ("x", 0.0, 600.0)}, reader=lambda *a: FakeReader(600))
        assert engine.catchup_step(budget_s=0.0) is True  # одно окно — и назад
        assert engine.catchup_progress()["active"]
    finally:
        tap.end()
        engine.stop()
    # Остановили, не догнав: что успели — в ленте, догонялка неполная.
    progress = engine.catchup_progress()
    assert not progress["active"] and not progress["complete"]
    assert (tmp_path / "live_transcript.md").read_text(encoding="utf-8").strip()


def test_catchup_failure_does_not_stop_live(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    engine, got = _attached_engine(tmp_path, tap)
    engine.start()

    class Broken(FakeReader):
        def read(self, seconds):
            raise OSError("ffmpeg не найден")

    try:
        engine.start_catchup({"mic.wav": ("x", 0.0, 60.0)}, reader=lambda *a: Broken(60))
        _wait(lambda: not engine.catchup_progress()["active"])
        assert not engine.catchup_progress()["complete"]
        tap.feed(1, False, 0, tone(8.0))
        _wait(lambda: any(not e.get("catchup") for e in got))  # живой режим идёт
    finally:
        tap.end()
        engine.stop()


def test_tap_tracks_map_to_engine_keys():
    from meet.live import TAP_TRACKS

    assert TAP_TRACKS["sys.opus"][0] == "sys.wav" and TAP_TRACKS["sys.opus"][2] is True
    # Микрофон тоже идёт в голоса: делит его meet.live_voices, только при образце владельца.
    assert TAP_TRACKS["mic.opus"][0] == "mic.wav" and TAP_TRACKS["mic.opus"][1] is False
    assert TAP_TRACKS["mic.opus"][2] is True


# --- раунд 1: отвод не держат молчащие и не читающие клиенты --------------------


def test_silent_connection_does_not_block_the_real_client():
    hub = _hub()
    server = pcm_tap.TapServer(hub)
    silent = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        began = time.monotonic()
        client = pcm_tap.TapClient(server.port, server.token)
        assert time.monotonic() - began < 1.0  # не ждёт молчуна
        assert len(client.tracks) == 2
        client.close()
    finally:
        silent.close()
        server.close()


def test_client_that_stops_reading_is_dropped_after_the_send_timeout(monkeypatch):
    monkeypatch.setattr(pcm_tap, "SEND_TIMEOUT_S", 0.3)
    hub = _hub()
    server = pcm_tap.TapServer(hub)
    raw = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.sendall(server.token.encode("ascii") + b"\n")
    try:
        _wait(lambda: hub.subscribers() == 1)
        deadline = time.monotonic() + 10
        while hub.subscribers() and time.monotonic() < deadline:
            hub.push(0, b"\x01\x00" * 65536, 0)  # клиент не читает
            time.sleep(0.02)
        assert hub.subscribers() == 0  # сброшен, очередь освобождена
        _wait(lambda: server.closed)
    finally:
        raw.close()
        server.close()


# --- раунд 1: догонялка — вежливо и без потерь ----------------------------------


class ThreadsAsr(FakeGigaam):
    def __init__(self):
        super().__init__()
        self.threads = []

    def set_cpu_threads(self, n):
        self.threads.append(n)


def test_catchup_uses_fewer_threads_and_gives_them_back(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    asr = ThreadsAsr()
    engine = LiveEngine(tmp_path, asr, speaker_name="Вы", mic_device=None, output_device=None,
                        tap_connect=lambda: tap)
    engine.start()
    try:
        from meet import live as live_mod

        engine.start_catchup({"mic.wav": ("x", 0.0, 20.0)}, reader=lambda *a: FakeReader(20))
        while engine.catchup_step(budget_s=0.0):
            pass
        assert asr.threads == [live_mod.CATCHUP_THREADS, None]
    finally:
        tap.end()
        engine.stop()


def test_catchup_takes_at_most_its_duty_share_of_the_worker(tmp_path, monkeypatch):
    from meet import live as live_mod

    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 0}])
    asr = FakeGigaam()
    slow = asr.transcribe_window

    def window(audio, **kw):
        time.sleep(0.2)  # окно догонялки «считается» 0,2 с
        return slow(audio, **kw)

    asr.transcribe_window = window
    engine = LiveEngine(tmp_path, asr, speaker_name="Вы", mic_device=None, output_device=None,
                        tap_connect=lambda: tap)
    monkeypatch.setattr(live_mod, "CATCHUP_SLICE_S", 0.05)
    engine.start()
    try:
        engine.start_catchup({"mic.wav": ("x", 0.0, 600.0)}, reader=lambda *a: FakeReader(600))
        began = time.monotonic()
        time.sleep(3.0)
        busy = len(asr.windows) * 0.2
        assert busy <= 0.5 * (time.monotonic() - began) + 0.4
        assert asr.windows  # и при этом идёт
    finally:
        tap.end()
        engine.stop()


def test_catchup_lines_survive_a_killed_assistant(tmp_path):
    tap = FakeTap([{"index": 1, "name": "mic.opus", "rate": SR, "channels": 1, "pos": 60 * SR * 2}])
    engine, _ = _attached_engine(tmp_path, tap)
    engine.start()
    try:
        engine.start_catchup({"mic.wav": ("x", 0.0, 60.0)}, reader=lambda *a: FakeReader(60))
        engine.catchup_step(budget_s=0.0)
        side = tmp_path / "live_transcript.catchup.md"
        # Строка уже на диске, хотя догонялка не кончилась (убьют — не пропадёт).
        assert side.read_text(encoding="utf-8").strip()
        saved = side.read_text(encoding="utf-8")
    finally:
        tap.end()
        engine.stop()
    assert not (tmp_path / "live_transcript.catchup.md").exists()  # слита при остановке
    # Убитый ассистент: файл остался — следующее включение сливает его в ленту.
    (tmp_path / "live_transcript.catchup.md").write_text(saved, encoding="utf-8")
    before = (tmp_path / "live_transcript.md").read_text(encoding="utf-8").splitlines()
    assert live_catchup.recover_side(tmp_path) == 0  # те же строки — не дублируются
    (tmp_path / "live_transcript.catchup.md").write_text("[00:00:01] Вы: новая\n",
                                                         encoding="utf-8")
    assert live_catchup.recover_side(tmp_path) == 1
    lines = (tmp_path / "live_transcript.md").read_text(encoding="utf-8").splitlines()
    assert "[00:00:01] Вы: новая" in lines and len(lines) == len(before) + 1
    assert lines == live_catchup.chronological(lines)
    assert not (tmp_path / "live_transcript.catchup.md").exists()


def test_catchup_decoder_runs_below_normal_priority(monkeypatch):
    import subprocess

    seen = {}

    def popen(argv, **kw):
        seen.update(kw)
        raise OSError("не запускаем")

    monkeypatch.setattr(subprocess, "Popen", popen)
    with pytest.raises(OSError):
        live_catchup.PcmReader("x.opus", 0.0, 1.0)
    below = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    assert seen["creationflags"] & below == below


def test_attached_child_starts_at_normal_priority(monkeypatch):
    """Старт подключённого ассистента — с обычным приоритетом: с пониженным
    на занятом звонком процессоре загрузка моделей растягивалась за минуту, и
    резидент убивал его по таймауту. Ниже обычного он ставит себя сам, когда
    готов (`meet.assist.app._lower_priority`), — на догонялку."""
    import subprocess

    from meet import live_control

    seen = []

    def popen(argv, **kw):
        seen.append(kw["creationflags"])
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(subprocess, "Popen", popen)
    live_control._spawn_process(["x"], None)
    live_control._spawn_process(["x"], None, {live_control.TAP_TOKEN_ENV: "t"})
    below = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    assert all(flags & below == 0 for flags in seen) or below == 0
