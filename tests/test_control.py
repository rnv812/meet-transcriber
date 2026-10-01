"""Тесты control API: реальный сервер на эфемерном порту + urllib.

Утиное состояние (FakeState) — по образцу tests/test_assist_web.py: слой
проверяется без трея, записи и аудио.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from meet import control, events


class FakeState:
    def __init__(self) -> None:
        self.bus = events.EventBus()
        self.calls: list = []
        self.recording = False

    def people(self):
        return {"items": [{"name": "Демьян"}]}

    def avatar_path(self, name):
        return None

    def set_avatar(self, name, data):
        if data.startswith(b"bad"):
            raise control.BadRequest("не изображение")
        self.calls.append(("avatar", name, len(data)))
        return {"ok": True}

    def snapshot(self) -> dict:
        return {"status": "recording" if self.recording else "idle",
                "folder": "C:/rec/2026-08-18_11-00" if self.recording else None,
                "levels": {"mic.opus": 0.25}}

    def start_recording(self) -> dict:
        self.calls.append("start")
        self.recording = True
        return {"ok": True, "action": "started"}

    def stop_recording(self, *, discard: bool = False) -> dict:
        self.calls.append("cancel" if discard else "stop")
        self.recording = False
        return {"ok": True, "action": "cancelled" if discard else "stopped"}

    def adopt_recording(self) -> dict:
        self.calls.append("adopt")
        return {"ok": True, "action": "adopted"}

    def settings(self) -> dict:
        return {"version": 1, "auto_record": {"enabled": False}}

    def patch_settings(self, updates: dict) -> dict:
        self.calls.append(("patch", updates))
        return {"settings": {"version": 1}, "restart_required": list(updates)}

    def diagnostics(self, lines: int = 200) -> dict:
        self.calls.append(("diagnostics", lines))
        return {"watch_log": ["строка"], "record_log": [], "lines": lines}

    def track_path(self, rid, track):
        return None

    def processes(self) -> dict:
        return {"available": True, "running": ["Zoom.exe"], "selected": [],
                "known": ["Zoom.exe"]}

    def devices(self) -> dict:
        return {"available": True, "system": {"name": "Колонки"},
                "mic": {"name": "Микрофон"}, "pinning": False}

    def update_recording(self, rid, body):
        self.calls.append(("update", rid, body))
        return {"id": rid, "title": body.get("title")}

    def recordings(self, limit=200, q=None):
        self.calls.append(("recordings", limit, q))
        return {"items": []}

    def delete_recording(self, rid):
        self.calls.append(("delete", rid))
        return {"ok": True}

    def get_hotwords(self):
        return {"text": "SIEM", "budget": 400, "used": 4}

    def put_hotwords(self, body):
        self.calls.append(("hotwords", body))
        return {"text": body["text"], "budget": 400, "used": 0}

    def person(self, name):
        self.calls.append(("person", name))
        return {"name": name, "meetings": []}

    def set_auto_record(self, body):
        self.calls.append(("auto", body))
        return {"status": "idle", "auto_record": {"enabled": body["enabled"]}}


    # --- ассистент ---

    provider_ready = True

    def make_summary(self, rid):
        if not self.provider_ready:
            raise control.Conflict("Подключите Claude Code или Codex в настройках")
        self.calls.append(("summary", rid))
        return {"id": "j1", "kind": "summary"}

    def summary(self, rid):
        if rid == "нет":
            return {"error": "итогов нет"}
        return {"markdown": "# Итоги", "created_at": 1.0}

    def ask(self, rid, body):
        self.calls.append(("ask", rid, body))
        return {"id": "j2", "kind": "ask"}

    def qa(self, rid):
        return {"items": [{"q": "а", "a": "б"}]}

    def to_notes(self, rid):
        raise control.BadRequest("Папка заметок не задана")

    def assistant(self):
        return {"provider": None, "checking": True, "setting": "auto",
                "available": {}, "knowledge_dir": None, "notes_dir": None}

    def check_provider(self, body):
        self.calls.append(("check", body))
        return {"ok": True, "error": None, "provider": body.get("provider")}


class BoomState(FakeState):
    def snapshot(self) -> dict:
        raise RuntimeError("состояние сломалось")


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Живой сервер; endpoint-файл уводим в tmp_path, чтобы не трогать машину."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    state = FakeState()
    srv = control.ControlServer(state)
    srv.start(pid=4242)
    srv.state_obj = state  # для удобства проверок
    try:
        yield srv
    finally:
        srv.stop(pid=4242)


def _get(srv, path, token=None, headers=None, expect=200):
    url = f"http://127.0.0.1:{srv.port}{path}"
    req = urllib.request.Request(url, headers=headers or {})
    if token is not False:
        req.add_header("Authorization", f"Bearer {token or srv.token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == expect
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        assert e.code == expect, f"ожидали {expect}, получили {e.code}"
        return json.loads(e.read().decode("utf-8") or "{}")


def _post(srv, path, payload=None, method="POST", expect=200, headers=None):
    url = f"http://127.0.0.1:{srv.port}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    req.add_header("Authorization", f"Bearer {srv.token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == expect
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        assert e.code == expect, f"ожидали {expect}, получили {e.code}"
        return json.loads(e.read().decode("utf-8") or "{}")


# --- доступ ---------------------------------------------------------------


def test_state_requires_token(server):
    """Без токена — 401: иначе любая открытая веб-страница включала бы запись."""
    _get(server, "/state", token=False, expect=401)


def test_wrong_token_rejected(server):
    # токен уходит в HTTP-заголовок, а там только ASCII — отсюда латиница
    _get(server, "/state", token="not-the-token", expect=401)


def test_token_in_query_works_for_eventsource(server):
    """EventSource не умеет ставить заголовки — токен принимается и в query."""
    url = f"http://127.0.0.1:{server.port}/state?token={server.token}"
    with urllib.request.urlopen(url, timeout=5) as r:
        assert json.loads(r.read().decode("utf-8"))["status"] == "idle"


def test_foreign_origin_rejected(server):
    _get(server, "/state", headers={"Origin": "https://evil.example"}, expect=403)


def test_tauri_origin_allowed_and_echoed(server):
    url = f"http://127.0.0.1:{server.port}/state"
    req = urllib.request.Request(url, headers={"Origin": "tauri://localhost"})
    req.add_header("Authorization", f"Bearer {server.token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.headers["Access-Control-Allow-Origin"] == "tauri://localhost"


def test_lookalike_origin_rejected(server):
    """Проверка по префиксу пропускала бы чужие домены, начинающиеся с
    разрешённого имени."""
    _get(server, "/state", headers={"Origin": "http://localhost.evil.com"}, expect=403)


@pytest.mark.parametrize("origin, allowed", [
    ("http://localhost", True),
    ("http://localhost:5173", True),
    ("http://127.0.0.1:8766", True),
    ("tauri://localhost", True),
    ("http://tauri.localhost", True),
    ("https://tauri.localhost", True),
    ("http://localhost.evil.com", False),
    ("http://127.0.0.1.evil.com", False),
    ("tauri://localhost.evil.com", False),
    ("tauri://evil", False),
    ("https://tauri.localhost.evil.com:443", False),
    ("http://localhost:", False),
    ("http://localhost:80abc", False),
    ("http://localhost/", False),
    ("http://evil.com#http://localhost", False),
    ("https://localhost", False),
])
def test_origin_is_matched_exactly(origin, allowed):
    assert control._origin_allowed(origin) is allowed


def test_binds_only_loopback(server):
    """Наружу порт не смотрит: запись микрофона — не сетевая услуга."""
    assert server._httpd.server_address[0] == "127.0.0.1"


def test_unknown_route_is_404(server):
    _get(server, "/no-such-route", expect=404)


# --- состояние и команды --------------------------------------------------


def test_state_returns_snapshot(server):
    assert _get(server, "/state")["levels"] == {"mic.opus": 0.25}


def test_recording_commands_reach_state(server):
    assert _post(server, "/recording/start")["action"] == "started"
    assert _post(server, "/recording/adopt")["action"] == "adopted"
    assert _post(server, "/recording/stop")["action"] == "stopped"
    assert _post(server, "/recording/cancel")["action"] == "cancelled"
    assert server.state_obj.calls == ["start", "adopt", "stop", "cancel"]


def test_auto_record_route(server):
    reply = _post(server, "/auto-record", {"enabled": False})
    assert reply["auto_record"]["enabled"] is False
    assert ("auto", {"enabled": False}) in server.state_obj.calls


def test_trailing_slash_is_the_same_route(server):
    assert _post(server, "/recording/start/")["ok"] is True


def test_settings_get_and_patch(server):
    assert _get(server, "/settings")["version"] == 1
    got = _post(server, "/settings", {"auto_record": {"enabled": True}},
                method="PATCH")
    assert got["restart_required"] == ["auto_record"]
    assert server.state_obj.calls[-1] == ("patch", {"auto_record": {"enabled": True}})


def test_patch_broken_json_is_400(server):
    url = f"http://127.0.0.1:{server.port}/settings"
    req = urllib.request.Request(url, data="{сломано".encode("utf-8"),
                                 method="PATCH")
    req.add_header("Authorization", f"Bearer {server.token}")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


def test_diagnostics_passes_lines_param(server):
    assert _get(server, "/diagnostics?lines=7")["lines"] == 7


def test_diagnostics_bad_lines_falls_back_to_default(server):
    assert _get(server, "/diagnostics?lines=abc")["lines"] == 200


def test_patch_recording_routes_to_update(server):
    reply = _post(server, "/recordings/2026-09-30_16-04", {"title": "Acme"},
                  method="PATCH")
    assert reply == {"id": "2026-09-30_16-04", "title": "Acme"}
    assert ("update", "2026-09-30_16-04", {"title": "Acme"}) in server.state_obj.calls


def test_delete_recording_routes(server):
    assert _post(server, "/recordings/2026-09-30_16-04", method="DELETE") == {"ok": True}
    assert ("delete", "2026-09-30_16-04") in server.state_obj.calls


def test_hotwords_routes(server):
    assert _get(server, "/hotwords")["text"] == "SIEM"
    reply = _post(server, "/hotwords", {"text": "SOC"}, method="PUT")
    assert reply["text"] == "SOC"
    assert ("hotwords", {"text": "SOC"}) in server.state_obj.calls


def test_recordings_search_passes_query(server):
    _get(server, "/recordings?q=cmdb")
    assert ("recordings", 200, "cmdb") in server.state_obj.calls


def test_person_card_route(server):
    assert _get(server, "/voices/%D0%94%D0%B5%D0%BC%D1%8C%D1%8F%D0%BD")["name"] == "Демьян"
    assert ("person", "Демьян") in server.state_obj.calls


def test_state_error_does_not_kill_server(monkeypatch, tmp_path):
    """Сбой в состоянии — 500 и живой резидент, а не упавший трей."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    logged = []
    srv = control.ControlServer(BoomState(), log=logged.append)
    srv.start(pid=1)
    try:
        _get(srv, "/state", expect=500)
        assert any("RuntimeError" in line for line in logged)
        assert _get(srv, "/")["ok"] is True  # сервер продолжает отвечать
    finally:
        srv.stop(pid=1)


# --- SSE ------------------------------------------------------------------


def _read_event(stream) -> tuple[str, dict]:
    """Прочитать одно SSE-сообщение (пропуская keepalive-комментарии)."""
    name = None
    while True:
        line = stream.readline().decode("utf-8")
        assert line, "поток закрылся раньше события"
        if line.startswith(":"):  # keepalive
            continue
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: "):
            return name, json.loads(line[6:])


def test_sse_starts_with_state_snapshot(server):
    url = f"http://127.0.0.1:{server.port}/events?token={server.token}"
    with urllib.request.urlopen(url, timeout=5) as r:
        assert "text/event-stream" in r.headers["Content-Type"]
        name, payload = _read_event(r)
        assert name == "state" and payload["status"] == "idle"


def test_sse_delivers_published_events(server):
    url = f"http://127.0.0.1:{server.port}/events?token={server.token}"
    with urllib.request.urlopen(url, timeout=5) as r:
        _read_event(r)  # снимок
        # публикуем из другого потока, как это делает вотчдог записи
        threading.Timer(
            0.05, lambda: server.state.bus.emit(events.RECORD_STARTED, folder="X")
        ).start()
        name, payload = _read_event(r)
        assert name == "record.started" and payload["folder"] == "X"


def test_sse_unsubscribes_on_disconnect(server):
    url = f"http://127.0.0.1:{server.port}/events?token={server.token}"
    r = urllib.request.urlopen(url, timeout=5)
    _read_event(r)
    r.close()
    # Издатель не должен копить подписчиков от закрытых панелей. Отписка
    # случается на первой неудачной записи, а первая запись в закрытый сокет
    # ещё проходит (FIN не RST) — поэтому пробуем до дедлайна.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and server.state.bus.subscribers():
        server.state.bus.emit(events.LOG, text="после закрытия")
        time.sleep(0.05)
    assert server.state.bus.subscribers() == 0


def test_sse_requires_token(server):
    url = f"http://127.0.0.1:{server.port}/events"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url, timeout=5)
    assert e.value.code == 401


# --- endpoint-файл --------------------------------------------------------


def test_endpoint_file_published_with_port_and_token(server, tmp_path):
    data = control.read_endpoint()
    assert data["port"] == server.port
    assert data["token"] == server.token
    assert data["pid"] == 4242
    assert control.client_url() == f"http://127.0.0.1:{server.port}/"


def test_endpoint_cleared_on_stop(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    srv = control.ControlServer(FakeState())
    srv.start(pid=7)
    assert control.read_endpoint() is not None
    srv.stop(pid=7)
    assert control.read_endpoint() is None


def test_foreign_endpoint_not_cleared(monkeypatch, tmp_path):
    """Файл мог перехватить другой резидент — чужую публикацию не трогаем."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    control.write_endpoint(1234, "чужой", pid=999)
    control.clear_endpoint(pid=7)
    assert control.read_endpoint()["pid"] == 999


def test_broken_endpoint_file_reads_as_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    control.endpoint_path().parent.mkdir(parents=True, exist_ok=True)
    control.endpoint_path().write_text("{сломано", encoding="utf-8")
    assert control.read_endpoint() is None
    assert control.client_url() is None


def test_alive_is_false_for_stale_endpoint(monkeypatch, tmp_path):
    """Файл от упавшего процесса не должен считаться живым API."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    with socket.socket() as s:  # занимаем и сразу отпускаем порт
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    control.write_endpoint(port, "т", pid=1)
    assert control.alive() is False


def test_alive_is_true_for_running_server(server):
    assert control.alive() is True


# --- клиент: request() и meet status --------------------------------------


def test_request_reaches_running_server(server):
    assert control.request("/state")["status"] == "idle"


def test_request_posts_payload(server):
    got = control.request("/settings", method="PATCH",
                          payload={"auto_record": {"enabled": True}})
    assert got["restart_required"] == ["auto_record"]


def test_request_without_resident_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="не запущен"):
        control.request("/state")


def test_request_on_stale_endpoint_says_not_responding(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    control.write_endpoint(port, "t", pid=1)
    with pytest.raises(RuntimeError, match="не отвечает"):
        control.request("/state")


def test_request_surfaces_server_error_text(server):
    with pytest.raises(RuntimeError, match="404"):
        control.request("/no-such-route")


def test_status_command_prints_state(server, capsys):
    from meet.cli import print_status

    print_status()
    out = capsys.readouterr().out
    assert "Записи нет." in out
    assert "Автозапись" in out and "Детектор" in out


def test_status_without_resident_exits_with_message(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    from meet.cli import print_status

    with pytest.raises(SystemExit, match="Статус недоступен"):
        print_status()


def test_status_prints_recording_details(server, capsys):
    server.state.recording = True
    from meet.cli import print_status

    print_status()
    out = capsys.readouterr().out
    assert "Идёт запись" in out and "2026-08-18_11-00" in out
    assert "mic.opus 0.25" in out


def test_processes_endpoint(server):
    assert _get(server, "/processes")["running"] == ["Zoom.exe"]


def test_devices_endpoint(server):
    got = _get(server, "/devices")
    assert got["system"]["name"] == "Колонки"
    # закрепление устройства сознательно не поддерживается: запись следит за
    # дефолтными endpoint'ами и переживает их смену
    assert got["pinning"] is False


def test_client_disconnect_is_not_an_error(server, monkeypatch):
    """Закрытая панель роняет соединение посреди запроса — это норма, а не сбой:
    иначе журнал резидента забивается traceback'ами на каждое закрытие."""
    logged = []
    server._httpd.log = logged.append
    try:
        raise ConnectionResetError("клиент ушёл")
    except ConnectionResetError:
        server._httpd.handle_error(None, ("127.0.0.1", 1))
    assert logged == []


def test_real_server_error_is_logged(server):
    logged = []
    server._httpd.log = logged.append
    try:
        raise RuntimeError("настоящий сбой")
    except RuntimeError:
        server._httpd.handle_error(None, ("127.0.0.1", 1))
    assert logged and "RuntimeError" in logged[0]


# --- регрессии из аудита 18.08.2026 -------------------------------------


def test_error_dict_becomes_4xx_not_200(server, monkeypatch):
    """Доменная ошибка {'error': ...} не должна выглядеть успехом: клиент
    бросает только на !ok, иначе не-выполненное действие сходит за успех."""
    server.state.recording = lambda rid: {"error": "записи нет"}
    got = _get(server, "/recordings/none/", expect=404)
    assert got["error"] == "записи нет"


def test_ok_dict_stays_200(server):
    """Обычный ответ (не только error) остаётся 200."""
    server.state.recording = lambda rid: {"id": "x", "ok": True}
    assert _get(server, "/recordings/x/")["id"] == "x"


def test_empty_file_no_range_is_200_not_416(server, tmp_path, monkeypatch):
    """Обычный GET пустой дорожки — пустой 200, а не 416 (Range не запрашивали)."""
    empty = tmp_path / "sys.opus"
    empty.write_bytes(b"")
    server.state.track_path = lambda rid, track: empty
    url = f"http://127.0.0.1:{server.port}/recordings/x/audio?track=sys"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {server.token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        assert r.read() == b""


def test_audio_range_served(server, tmp_path):
    """Range отдаёт кусок с 206 и Content-Range — без этого плеер не перематывает."""
    track = tmp_path / "sys.opus"
    track.write_bytes(bytes(range(256)) * 8)  # 2048 байт
    server.state.track_path = lambda rid, t: track
    url = f"http://127.0.0.1:{server.port}/recordings/x/audio?track=sys"
    req = urllib.request.Request(url, headers={"Range": "bytes=10-19"})
    req.add_header("Authorization", f"Bearer {server.token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 206
        assert r.headers["Content-Range"] == "bytes 10-19/2048"
        assert len(r.read()) == 10


# --- стабильный адрес: фиксированный порт + постоянный токен -------------


def test_token_persists_across_restarts(monkeypatch, tmp_path):
    """Постоянный токен: даже на фиксированном порту меняющийся токен ломал бы
    переподключение UI."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    first = control.persisted_token()
    second = control.persisted_token()
    assert first == second and len(first) > 10


def test_fallback_to_ephemeral_when_preferred_port_taken(monkeypatch, tmp_path):
    """Предпочтительный порт занят — API поднимается на эфемерном, а не падает."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    import socket

    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        taken = blocker.getsockname()[1]
        blocker.listen(1)
        srv = control.ControlServer(FakeState(), port=taken, fallback=True)
        port = srv.start(pid=1)
        try:
            assert port != taken and port > 0
            assert control.alive() is True
        finally:
            srv.stop(pid=1)


def test_no_fallback_raises_when_port_taken(monkeypatch, tmp_path):
    """Без fallback (тесты, явные вызовы) занятый порт — ошибка, не молчание."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    import socket

    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        taken = blocker.getsockname()[1]
        blocker.listen(1)
        srv = control.ControlServer(FakeState(), port=taken, fallback=False)
        try:
            import pytest
            with pytest.raises(OSError):
                srv.start(pid=1)
        finally:
            srv.stop(pid=1)


def test_voices_listing_route(server):
    assert _get(server, "/voices") == {"items": [{"name": "Демьян"}]}


def test_put_avatar_passes_raw_bytes_and_decodes_name(server):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/voices/%D0%94%D0%B5%D0%BC%D1%8C%D1%8F%D0%BD/avatar",
        data=b"\x89PNG....", method="PUT",
        headers={"Authorization": f"Bearer {server.token}",
                 "Content-Type": "image/png"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read()) == {"ok": True}
    assert ("avatar", "Демьян", 8) in server.state_obj.calls


def test_missing_avatar_is_404(server):
    assert _get(server, "/voices/x/avatar", expect=404) == {"error": "файла нет"}


def test_bad_avatar_is_400_and_server_keeps_serving(server):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/voices/x/avatar", data=b"bad bytes",
        method="PUT", headers={"Authorization": f"Bearer {server.token}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400
    assert json.loads(e.value.read()) == {"error": "не изображение"}
    assert _get(server, "/voices") == {"items": [{"name": "Демьян"}]}


def test_handler_oserror_is_500_not_silence(server):
    def boom():
        raise PermissionError("диск только для чтения")
    server.state_obj.settings = boom  # GET /settings вызывает state.settings()
    reply = _get(server, "/settings", expect=500)
    assert "только для чтения" in reply["error"]


def test_recording_id_in_path_is_url_decoded(server):
    # атрибут `recording` у FakeState — флаг; для этого теста подменяем его методом
    def lookup(rid):
        server.state_obj.calls.append(("recording", rid))
        return {"id": rid}
    server.state_obj.recording = lookup
    assert _get(server, "/recordings/a%20b") == {"id": "a b"}
    assert ("recording", "a b") in server.state_obj.calls


# --- ассистент ---------------------------------------------------------------


def test_summary_routes(server):
    assert _post(server, "/recordings/r%201/summary")["kind"] == "summary"
    assert ("summary", "r 1") in server.state_obj.calls
    assert _get(server, "/recordings/r1/summary")["markdown"] == "# Итоги"
    assert _get(server, "/recordings/нет/summary".replace("нет", "%D0%BD%D0%B5%D1%82"),
                expect=404) == {"error": "итогов нет"}


def test_summary_without_provider_is_409_with_text(server):
    server.state_obj.provider_ready = False
    got = _post(server, "/recordings/r1/summary", expect=409)
    assert got == {"error": "Подключите Claude Code или Codex в настройках"}


def test_ask_qa_notes_routes(server):
    assert _post(server, "/recordings/r1/ask", {"question": "что решили?"})["kind"] == "ask"
    assert ("ask", "r1", {"question": "что решили?"}) in server.state_obj.calls
    assert _get(server, "/recordings/r1/qa")["items"][0]["q"] == "а"
    assert _post(server, "/recordings/r1/notes", expect=400) == {
        "error": "Папка заметок не задана"}


def test_assistant_routes(server):
    assert _get(server, "/assistant")["checking"] is True
    got = _post(server, "/assistant/check", {"provider": "codex"})
    assert got == {"ok": True, "error": None, "provider": "codex"}
