"""Резидент: ассистент, включённый посреди обычной записи («Включить
ассистента» / «Выключить ассистента»).

Ребёнок `meet assist` — та же заглушка, что в test_live_control (настоящий
процесс с HTTP и подключением к отводу), запись — поддельный record() с
настоящим `pcm_tap.TapHub`.
"""

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meet import control, jobs, library, live_control, pcm_tap, tray, tray_control

# Заглушка ребёнка и фикстуры резидента — общие с test_live_control.
from test_live_control import (  # noqa: F401  (фикстуры pytest)
    _active,
    _call,
    _idle,
    _wait_for,
    data_dir,
    make_live,
    resident,
)


def _hub_with_tracks():
    hub = pcm_tap.TapHub()
    hub.begin(2)
    hub.configure(0, "sys.opus", 48000, 2)
    hub.configure(1, "mic.opus", 16000, 1)
    return hub


# --- LiveControl ---------------------------------------------------------------


def test_attach_spawns_a_child_on_the_recording_tap(make_live, tmp_path):
    live, stub, rec = make_live()
    folder = tmp_path / "recordings" / "2026-10-03_09-00"
    folder.mkdir(parents=True)
    server = pcm_tap.TapServer(_hub_with_tracks())
    reply = live.start(tmp_path / "recordings",
                       attach={"folder": str(folder), "server": server, "started_at": 1000.0})
    # Папка известна сразу: это папка идущей записи.
    assert reply["ok"] and reply["attached"] is True and reply["folder"] == str(folder)
    assert live.attached()
    _wait_for(lambda: _active(live))
    assert live.status()["started_at"] == 1000.0  # секундомер — от начала записи
    argv = stub.argv
    assert argv[argv.index("--attach-to") + 1] == str(folder)
    assert argv[argv.index("--tap-port") + 1] == str(server.port)
    assert server.token not in " ".join(argv)  # токен — в окружении, не в командной строке
    assert stub.note("env_token") == [server.token]
    header = json.loads(stub.note("tap_header")[0])
    assert [t["name"] for t in header["tracks"]] == ["sys.opus", "mic.opus"]
    live.stop(detach=True)
    _wait_for(lambda: _idle(live))
    assert [json.loads(b) for b in stub.note("stop_body")] == [{"detach": True}]
    stopped = rec.last(live_control.LIVE_STOPPED).data
    assert stopped["attached"] is True and stopped["detached"] is True
    assert stopped["folder"] == str(folder) and stopped["error"] is None
    _wait_for(lambda: server.closed)  # отвод закрыт вместе с ассистентом
    assert not live.attached() and live.status()["attached"] is False
    assert not (folder / "sys.opus").exists()  # дорожки ассистент не писал


def test_plain_stop_of_an_attached_assistant_is_not_a_detach(make_live, tmp_path):
    live, stub, rec = make_live()
    folder = tmp_path / "rec" / "f"
    folder.mkdir(parents=True)
    live.start(tmp_path / "rec", attach={"folder": str(folder),
                                         "server": pcm_tap.TapServer(_hub_with_tracks())})
    _wait_for(lambda: _active(live))
    live.stop(wait=True)
    assert [json.loads(b) for b in stub.note("stop_body")] == [{}]
    assert rec.last(live_control.LIVE_STOPPED).data["detached"] is False


def test_attach_spawn_failure_closes_the_tap(make_live, tmp_path):
    live, _, rec = make_live()

    def boom(argv, log_file, extra_env=None):
        raise OSError("нет интерпретатора")

    live._spawn = boom
    server = pcm_tap.TapServer(_hub_with_tracks())
    reply = live.start(tmp_path, attach={"folder": str(tmp_path), "server": server})
    assert reply["ok"] is False and server.closed
    assert rec.last(live_control.LIVE_FAILED).data["attached"] is True


def test_busy_live_refuses_attach_and_closes_its_tap(make_live, tmp_path):
    live, _, _ = make_live()
    live.start(tmp_path / "recordings")
    server = pcm_tap.TapServer(_hub_with_tracks())
    with pytest.raises(live_control.LiveBusy):
        live.start(tmp_path / "recordings", attach={"folder": str(tmp_path), "server": server})
    assert server.closed  # один ассистент за раз; отвод не висит


# --- TrayControl ---------------------------------------------------------------


def _recording_resident(resident, monkeypatch, tmp_path):
    """Обычная запись резидента: поддельный record() — с отводом, как настоящий."""
    folder = tmp_path / "recordings" / "2026-10-03_09-00"
    folder.mkdir(parents=True, exist_ok=True)
    order: list[str] = []

    def fake_record(out_root, stop_event=None, *, bus, pcm_tap=None):
        pcm_tap.begin(2)
        pcm_tap.configure(0, "sys.opus", 48000, 2)
        pcm_tap.configure(1, "mic.opus", 16000, 1)
        stop_event.wait(30)
        order.append("record.stopped")
        pcm_tap.end()
        return folder

    monkeypatch.setattr(tray, "record", fake_record)
    monkeypatch.setattr(resident.tray, "_current_folder", lambda: str(folder))
    real_stop = resident.live.stop

    def stop(*a, **kw):
        order.append("live.stop" + (" detach" if kw.get("detach") else "")
                     + (" wait" if kw.get("wait") else ""))
        return real_stop(*a, **kw)

    monkeypatch.setattr(resident.live, "stop", stop)
    assert resident.tray.start_recording(tray_control.MANUAL)
    _wait_for(lambda: resident.tray.pcm_tap.active())
    return folder, order


def test_attach_needs_a_running_recording(resident):
    with pytest.raises(control.BadRequest, match="Запись не идёт"):
        resident.live_attach()
    assert resident.stub.argv is None


def test_attach_without_provider_is_conflict(resident, monkeypatch, tmp_path):
    from meet.llm import detect

    _recording_resident(resident, monkeypatch, tmp_path)
    monkeypatch.setattr(detect, "available", lambda base_url=None: {})
    try:
        with pytest.raises(control.Conflict, match="Подключите Claude Code или Codex"):
            resident.live_attach()
        assert resident.stub.argv is None
    finally:
        resident.tray.stop_recording()


def test_attach_detach_keeps_the_recording_going(resident, monkeypatch, tmp_path):
    folder, order = _recording_resident(resident, monkeypatch, tmp_path)
    try:
        reply = resident.live_attach()
        assert reply["ok"] and reply["attached"] is True
        _wait_for(lambda: resident.snapshot()["live"]["active"])
        snap = resident.snapshot()
        # Одна запись: резидент пишет, ассистент к ней подключён.
        assert snap["status"] == "recording" and snap["live"]["attached"] is True
        assert snap["live"]["folder"] == str(folder)
        assert json.loads(resident.stub.note("tap_header")[0])["tracks"][0]["name"] == "sys.opus"
        # Второй ассистент — нельзя; «с ассистентом» поверх записи — тоже.
        with pytest.raises(control.BadRequest, match="уже включён"):
            resident.live_attach()
        with pytest.raises(control.BadRequest):
            resident.live_start()
        detached = resident.live_detach()
        assert detached["ok"] is True
        _wait_for(lambda: not resident.live.busy())
        assert resident.tray.recording is True  # запись идёт дальше
        assert resident.queue.submitted == []  # и в расшифровку не встала
        assert "source" not in library.read_meta(Path(folder))
        assert order == ["live.stop detach"]
        assert resident.live.status()["ended_by"] == live_control.ENDED_DETACH
        # Снова включить в ту же запись — можно.
        resident.live_attach()
        _wait_for(lambda: resident.live.status()["active"])
    finally:
        resident.tray.stop_recording()
    _wait_for(lambda: not resident.live.busy())


def test_stop_ends_the_recording_first_then_waits_for_its_assistant(resident, monkeypatch, tmp_path):
    folder, order = _recording_resident(resident, monkeypatch, tmp_path)
    resident.live_attach()
    _wait_for(lambda: resident.live.status()["active"])
    resident.stop_recording()
    # Захват кончился в момент «Стоп»; ассистент дописал сводку уже после.
    assert order == ["record.stopped", "live.stop wait"]
    assert not resident.live.busy() and resident.tray.recording is False
    assert [json.loads(b) for b in resident.stub.note("stop_body")] == [{}]
    # Кончился вместе с записью — трей об этом молчит.
    assert resident.live.status()["ended_by"] == live_control.ENDED_RECORDING
    # Расшифровка — одна, от записи (не «live»).
    _wait_for(lambda: resident.queue.submitted)
    assert resident.queue.submitted == [(jobs.TRANSCRIBE, str(folder))]
    assert library.read_meta(Path(folder))["source"] == "record"


def test_cancelled_recording_waits_for_its_assistant_before_deleting(resident, monkeypatch,
                                                                     tmp_path):
    folder, order = _recording_resident(resident, monkeypatch, tmp_path)
    resident.live_attach()
    _wait_for(lambda: resident.live.status()["active"])
    seen = {}
    real_finish = resident.tray.after_stop

    def finish(discard):
        real_finish(discard)
        seen["live_gone_before_delete"] = not resident.live.busy() and folder.exists()

    monkeypatch.setattr(resident.tray, "after_stop", finish)
    resident.stop_recording(discard=True)
    assert order == ["record.stopped", "live.stop wait"]
    assert seen["live_gone_before_delete"] is True
    assert not folder.exists()
    assert resident.queue.submitted == []


def test_audio_ends_at_the_click_even_if_the_assistant_takes_its_time(resident, monkeypatch,
                                                                     tmp_path):
    """Длина звука записи — до момента «Стоп» (± буфер), хотя подключённый
    ассистент дописывает сводку ещё секунду."""
    import time

    import test_recorder as tr

    from meet import recorder

    tr._fake_audio(monkeypatch)
    created = []

    class Writer(tr._DummyWriter):
        def __init__(self, path, channels, rate):
            super().__init__(path, channels, rate)
            created.append(time.monotonic())

    monkeypatch.setattr(recorder, "OpusWriter", Writer)
    resident.stub.mode = "slow-finalize"  # ассистент финализируется ≥ 1 с
    assert resident.tray.start_recording(tray_control.MANUAL)
    _wait_for(lambda: resident.tray.pcm_tap.active() and len(created) == 2)
    resident.live_attach()
    _wait_for(lambda: resident.live.status()["active"])
    time.sleep(0.5)
    click = time.monotonic()
    resident.stop_recording()
    took = time.monotonic() - click
    assert took >= 1.0  # ассистента дождались…
    for writer, began in zip(tr._DummyWriter.instances, created):
        seconds = len(writer.data) / (2 * writer.channels * writer.rate)
        # …а звук кончился в момент нажатия (буфер 1024 кадра — 64 мс).
        assert abs(seconds - (click - began)) < 0.15, (seconds, click - began)


def test_assistant_crash_does_not_touch_the_recording(resident, monkeypatch, tmp_path):
    folder, order = _recording_resident(resident, monkeypatch, tmp_path)
    try:
        resident.live_attach()
        _wait_for(lambda: resident.live.status()["active"])
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{resident.live._port}/crash", data=b"{}", method="POST"),
            timeout=5).close()
        _wait_for(lambda: not resident.live.busy())
        assert resident.snapshot()["live"]["error"] == "RuntimeError: устройство пропало"
        assert resident.snapshot()["live"]["ended_by"] == live_control.ENDED_CRASH
        assert resident.tray.recording is True and resident.tray.pcm_tap.active()
        assert resident.queue.submitted == [] and order == []
    finally:
        resident.tray.stop_recording()


def test_live_stop_of_an_attached_assistant_only_detaches(resident, monkeypatch, tmp_path):
    folder, order = _recording_resident(resident, monkeypatch, tmp_path)
    try:
        resident.live_attach()
        _wait_for(lambda: resident.live.status()["active"])
        resident.live_stop()  # «Стоп» старой панели или `meet live-stop`
        _wait_for(lambda: not resident.live.busy())
        assert resident.tray.recording is True
        assert order == ["live.stop detach"]
    finally:
        resident.tray.stop_recording()


def test_attach_is_refused_while_the_recording_stops(resident, monkeypatch, tmp_path):
    _recording_resident(resident, monkeypatch, tmp_path)
    resident.tray.stopping = True
    try:
        with pytest.raises(control.BadRequest, match="останавливается"):
            resident.live_attach()
        assert resident.stub.argv is None
    finally:
        resident.tray.stopping = False
        resident.tray.stop_recording()


def test_detach_without_attached_assistant(resident):
    reply = resident.live_detach()
    assert reply["ok"] is False and reply["action"] == "not-attached"


def test_attach_routes_end_to_end(resident, monkeypatch, tmp_path):
    _recording_resident(resident, monkeypatch, tmp_path)
    srv = control.ControlServer(resident)
    srv.start(publish=False)
    try:
        # Чужая страница не включит ассистента (Origin), без токена — тоже.
        req = urllib.request.Request(f"http://127.0.0.1:{srv.port}/live/attach", data=b"",
                                     method="POST", headers={"Origin": "https://evil.example"})
        req.add_header("Authorization", f"Bearer {srv.token}")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 403
        e.value.close()
        bare = urllib.request.Request(f"http://127.0.0.1:{srv.port}/live/attach", data=b"",
                                      method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bare, timeout=10)
        assert e.value.code == 401
        e.value.close()
        assert resident.stub.argv is None
        status, reply = _call(srv, "/live/attach")
        assert status == 200 and reply["attached"] is True
        _wait_for(lambda: _call(srv, "/state", method="GET")[1]["live"]["active"])
        status, reply = _call(srv, "/live/detach")
        assert status == 200 and reply["ok"] is True
        _wait_for(lambda: not _call(srv, "/state", method="GET")[1]["live"]["active"])
        assert _call(srv, "/state", method="GET")[1]["status"] == "recording"
    finally:
        srv.stop()
        resident.tray.stop_recording()
