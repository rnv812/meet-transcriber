"""Тесты control API: реальный сервер на эфемерном порту + urllib.

Утиное состояние (FakeState) — по образцу tests/test_assist_web.py: слой
проверяется без трея, записи и аудио.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
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
                "mic": {"name": "Микрофон"}, "pinning": True,
                "inputs": [{"name": "Микрофон", "default": True}],
                "outputs": [{"name": "Колонки", "default": True}]}

    def test_device(self, body):
        if body.get("name") == "сломан":
            raise control.Unavailable("Не удалось проверить устройство: Устройство не ответило")
        if self.recording:
            raise control.Conflict("Идёт запись — проверка устройства недоступна")
        self.calls.append(("device-test", body))
        return {"ok": True, "peak": 0.5, "device": body.get("name") or "Микрофон"}

    def update_recording(self, rid, body):
        self.calls.append(("update", rid, body))
        return {"id": rid, "title": body.get("title")}

    def recordings(self, limit=200, q=None, filters=None):
        self.calls.append(("recordings", limit, q))
        self.filters = filters
        return {"items": []}

    def search(self, q, limit=200, filters=None):
        self.calls.append(("search", q, limit))
        self.filters = filters
        return {"items": [{"id": "r1", "hits": [], "total": 0}]}

    def groups(self, q="", filters=None):
        self.calls.append(("groups", q))
        self.filters = filters
        return {"groups": [], "unknown": []}

    def create_group(self, body):
        self.calls.append(("create_group", body))
        return {"id": "g-1", **body}

    def patch_group(self, gid, body):
        self.calls.append(("patch_group", gid, body))
        return {"error": "группы нет"} if gid == "g-none" else {"id": gid, **body}

    def delete_group(self, gid):
        self.calls.append(("delete_group", gid))
        return {"group": {"id": gid}, "index": 0}

    def order_groups(self, body):
        self.calls.append(("order_groups", body))
        return {"groups": []}

    def group_members(self, gid, body):
        self.calls.append(("group_members", gid, body))
        if gid == "g-none":
            raise control.BadRequest("группы нет")
        return {"changed": body.get("add", []), "failed": []}

    def facets(self, q="", filters=None):
        self.calls.append(("facets", q))
        self.filters = filters
        return {"total": 0}

    def participants(self, q="", limit=20):
        self.calls.append(("participants", q, limit))
        return [{"name": "Анна", "meetings": 2, "last_at": None, "owner": False}]

    def categories(self, q="", filters=None):
        self.calls.append(("categories", q))
        self.filters = filters
        return {"categories": [], "defaults": [], "counts": {}, "none": 0}

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

    def speakers(self, rid):
        self.calls.append(("speakers", rid))
        return {"speakers": [], "history": [], "pos": 0}

    def speakers_apply(self, rid, body):
        self.calls.append(("speakers_apply", rid, body))
        if body.get("bad"):
            raise control.BadRequest("нечего применять")
        return {"pos": 1}

    def speakers_split_prepare(self, rid, body):
        self.calls.append(("speakers_split_prepare", rid, body))
        return {"ready": True}

    def speakers_split_preview(self, rid, body):
        self.calls.append(("speakers_split_preview", rid, body))
        return {"groups": []}

    def speakers_split_apply(self, rid, body):
        self.calls.append(("speakers_split_apply", rid, body))
        return {"pos": 3}

    def speakers_threshold(self, rid, body):
        self.calls.append(("speakers_threshold", rid, body))
        return {"changes": []}

    def speakers_threshold_apply(self, rid, body):
        self.calls.append(("speakers_threshold_apply", rid, body))
        return {"pos": 4}

    def speakers_split_turn(self, rid, body):
        self.calls.append(("speakers_split_turn", rid, body))
        return {"pos": 5}

    def speakers_rediarize(self, rid, body):
        self.calls.append(("speakers_rediarize", rid, body))
        return {"job": {"id": "r1"}}

    def speakers_rediarized(self, rid):
        self.calls.append(("speakers_rediarized", rid))
        return {"error": "нового разделения нет"}

    def speakers_rediarize_apply(self, rid):
        self.calls.append(("speakers_rediarize_apply", rid))
        return {"pos": 6}

    def speakers_rediarize_discard(self, rid):
        self.calls.append(("speakers_rediarize_discard", rid))
        return {"ok": True}

    def speakers_relabel(self, rid, body):
        self.calls.append(("speakers_relabel", rid, body))
        return {"pos": 2}

    def speakers_undo(self, rid, body=None):
        self.calls.append(("speakers_undo", rid, body))
        raise control.Conflict("уже нельзя")

    def speakers_redo(self, rid):
        self.calls.append(("speakers_redo", rid))
        return {"pos": 1}

    def speakers_revert(self, rid, body):
        self.calls.append(("speakers_revert", rid, body))
        return {"pos": 0}

    def set_auto_record(self, body):
        self.calls.append(("auto", body))
        return {"status": "idle", "auto_record": {"enabled": body["enabled"]}}


    # --- ассистент ---

    provider_ready = True

    def make_summary(self, rid, body=None):
        if not self.provider_ready:
            raise control.Conflict("Подключите Claude Code, Codex или OpenCode в настройках")
        self.calls.append(("summary", rid))
        if body:
            self.calls.append(("summary-body", rid, body))
        return {"id": "j1", "kind": "summary"}

    def summary(self, rid):
        if rid == "нет":
            return {"error": "итогов нет"}
        return {"markdown": "# Итоги", "created_at": 1.0}

    def live_draft(self, rid):
        if rid == "нет":
            return {"error": "черновика нет"}
        return {"summary": {"topic": "Т"}, "hints": [], "markdown": "**Тема:** Т"}

    def ask(self, rid, body):
        self.calls.append(("ask", rid, body))
        return {"id": "j2", "kind": "ask"}

    def qa(self, rid):
        return {"items": [{"q": "а", "a": "б"}]}

    meetings_dir = None

    def kb_export(self, rid):
        if not self.meetings_dir:
            raise control.BadRequest("Папка для встреч не задана")
        self.calls.append(("kb-export", rid))
        return {"path": f"{self.meetings_dir}/2026-09-30 - Планирование спринта",
                "files": ["Транскрипт.md"]}

    def export_preview(self, params):
        self.calls.append(("preview", params))
        return {"folder": "2026-09-30 - Планирование спринта", "files": ["Транскрипт.md"],
                "error": None}

    def agent_context(self, rid, body=None):
        if rid == "нет":
            return {"error": "записи нет"}
        self.calls.append(("agent-context", rid))
        self.agent_body = body
        return {"folder": f"D:/rec/{rid}", "files": ["transcript.md"]}

    def agent_files(self, rid):
        if rid == "нет":
            return {"error": "записи нет"}
        self.calls.append(("agent-files", rid))
        return {"files": ["transcript.md", "summary.md"], "live": False}

    def assistant(self, probe_local=True):
        self.calls.append(("assistant", probe_local))
        return {"provider": None, "checking": True, "setting": "auto",
                "available": {}, "knowledge_dir": None}

    def check_provider(self, body):
        self.calls.append(("check", body))
        return {"ok": True, "error": None, "provider": body.get("provider")}

    def local_models(self, body):
        self.calls.append(("local-models", body))
        return {"ok": True, "models": [{"id": "qwen3:8b"}]}

    # --- живой режим ---

    live_active = False
    live_last_id = "нет"

    def live_start(self, body=None):
        if body:
            self.calls.append(("live.start.body", body))
        if not self.provider_ready:
            raise control.Conflict("Подключите Claude Code, Codex или OpenCode в настройках")
        if self.recording:
            raise control.BadRequest("Идёт обычная запись")
        self.calls.append("live.start")
        return {"ok": True, "active": False, "starting": True, "folder": None,
                "error": None}

    def live_stop(self):
        self.calls.append("live.stop")
        return {"ok": True, "action": "stopping"}

    def live_ask(self, body):
        self.calls.append(("live.ask", body))
        return {"answer": "ответ"}

    def live_task(self, body):
        self.calls.append(("live.task", body))
        return {"ok": True}

    def live_hint(self, body):
        self.calls.append(("live.hint", body))
        return {"ok": True, "changed": True}

    def live_events(self, last_event_id=None):
        if not self.live_active:
            raise control.Conflict("Ассистент не запущен")
        self.live_last_id = last_event_id
        return FakeLiveStream()


class FakeLiveStream:
    """Ретранслятор: два события и конец потока."""

    def __init__(self) -> None:
        self.blocks = [b'event: state\ndata: {"status": null}\n\n',
                       'event: line\nid: 3\ndata: {"text": "а"}\n\n'.encode("utf-8"),
                       b""]
        self.closed = False

    def get(self, timeout=None):
        return self.blocks.pop(0) if self.blocks else b""

    def close(self):
        self.closed = True


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


def test_category_filter_reaches_list_and_search(server):
    _get(server, "/recordings?categories=daily,_none")
    assert server.state_obj.filters == {"categories": ["daily,_none"]}
    _get(server, "/search?q=cmdb&categories=retro")
    assert server.state_obj.filters == {"categories": ["retro"]}


def test_library_filter_reaches_list_search_and_counts(server):
    query = ("groups=g-1,g-2&people=%D0%90%D0%BD%D0%BD%D0%B0&people=Bob&from=2026-09-01&to=2026-09-30"
             "&has=summary&lacks=analysis&min_s=60&max_s=3600&in=title&token=t&limit=5000")
    want = {"groups": ["g-1,g-2"], "people": ["Анна", "Bob"], "from": ["2026-09-01"],
            "to": ["2026-09-30"], "has": ["summary"], "lacks": ["analysis"], "min_s": ["60"],
            "max_s": ["3600"], "in": ["title"]}
    _get(server, f"/recordings?{query}")
    assert server.state_obj.filters == want
    assert ("recordings", 5000, "") in server.state_obj.calls
    _get(server, f"/search?q=x&{query}")
    assert server.state_obj.filters == want
    _get(server, f"/categories?q=x&{query}")
    assert server.state_obj.filters == want


def test_full_text_search_route(server):
    got = _get(server, "/search?q=%22%D0%BF%D0%BB%D0%B0%D0%BD%22%20%D0%B1%D1%8E%D0%B4%D0%B6%D0%B5%D1%82&limit=5")
    assert got["items"][0]["id"] == "r1"
    assert ("search", '"план" бюджет', 5) in server.state_obj.calls


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


def _live_snapshot(server, **live):
    base = {"active": False, "starting": False, "stopping": False, "folder": None,
            "error": None, "started_at": None}
    server.state.snapshot = lambda: {"status": "idle", "folder": None,
                                     "live": {**base, **live}}


def test_status_prints_live_recording(server, capsys):
    """Запись с ассистентом — тоже запись: «Записи нет.» тут было бы враньём
    (snapshot.status во время живого режима остаётся idle)."""
    import time as _time

    from meet.cli import print_status

    _live_snapshot(server, active=True, folder="C:/rec/2026-10-01_10-00",
                   started_at=_time.time() - 65)
    print_status()
    out = capsys.readouterr().out
    assert "Записи нет." not in out
    assert "Идёт запись с ассистентом: C:/rec/2026-10-01_10-00" in out
    assert "Длительность: 01:0" in out


def test_status_prints_live_starting_and_stopping(server, capsys):
    from meet.cli import print_status

    _live_snapshot(server, starting=True)
    print_status()
    assert "Ассистент запускается" in capsys.readouterr().out
    _live_snapshot(server, active=True, stopping=True, folder="C:/rec/x",
                   started_at=None)
    print_status()
    out = capsys.readouterr().out
    assert "Идёт запись с ассистентом: C:/rec/x" in out
    assert "останавливается" in out


def test_status_prints_last_live_error_when_idle(server, capsys):
    from meet.cli import print_status

    _live_snapshot(server, error="Ассистент завершился (код 1)")
    print_status()
    out = capsys.readouterr().out
    assert "Записи нет." in out
    assert "Ассистент завершился (код 1)" in out


def test_live_stop_command_posts_to_resident(server, capsys):
    from meet import cli

    assert cli.main(["live-stop"]) in (None, 0)
    assert server.state.calls[-1] == "live.stop"
    assert "останавливается" in capsys.readouterr().out


def test_live_stop_command_when_not_live(server, capsys):
    from meet import cli

    server.state.live_stop = lambda: {"ok": False, "action": "not-live"}
    assert cli.main(["live-stop"]) == 1
    assert "не запущен" in capsys.readouterr().out


def _raw_post(srv, path: str, body: bytes, *, token: str | None = None) -> bytes:
    """POST сырым сокетом, как его шлёт urllib: Connection: close, заголовки и
    тело — двумя send() (так делает http.client). Перед чтением ответа —
    пауза: к этому моменту сервер уже ответил и закрыл соединение."""
    head = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
            f"Authorization: Bearer {token or srv.token}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n")
    with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as sock:
        sock.sendall(head.encode("utf-8"))
        time.sleep(0.05)
        sock.sendall(body)
        time.sleep(0.3)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


@pytest.mark.parametrize("token", [None, "wrong"])
@pytest.mark.parametrize("size", [0, 200_000])
def test_unread_request_body_does_not_reset_the_connection(server, token, size):
    """Маршрут, которому тело не нужно (`POST /live/stop` от `meet live-stop`
    шлёт `{}`), всё равно его дочитывает. Иначе сервер закрывал сокет с
    непрочитанными байтами, Windows отвечала RST вместо FIN, и клиент терял
    уже отправленный ответ (WinError 10053/10054) — тот самый «флакающий»
    test_live_stop_command_when_not_live в полном прогоне."""
    server.state.live_stop = lambda: {"ok": False, "action": "not-live"}
    body = b"{}" if not size else b'{"x": "' + b"a" * size + b'"}'
    reply = _raw_post(server, "/live/stop", body, token=token)
    assert reply.startswith(b"HTTP/1.1 200" if token is None else b"HTTP/1.1 401")
    assert reply.rstrip().endswith(b"}")


def test_live_stop_command_without_resident(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    from meet import cli

    with pytest.raises(SystemExit, match="Не удалось остановить ассистента"):
        cli.main(["live-stop"])


def test_processes_endpoint(server):
    assert _get(server, "/processes")["running"] == ["Zoom.exe"]


def test_devices_endpoint(server):
    got = _get(server, "/devices")
    assert got["system"]["name"] == "Колонки"
    assert got["pinning"] is True
    assert got["inputs"] == [{"name": "Микрофон", "default": True}]


def test_device_test_endpoint(server):
    got = _post(server, "/devices/test", {"kind": "mic", "name": "USB-микрофон"})
    assert got["peak"] == 0.5 and got["device"] == "USB-микрофон"
    assert ("device-test", {"kind": "mic", "name": "USB-микрофон"}) in server.state.calls


def test_device_test_refused_while_recording(server):
    server.state.recording = True
    got = _post(server, "/devices/test", {"kind": "mic", "name": None}, expect=409)
    assert "Идёт запись" in got["error"]


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


def test_audio_token_in_query_like_the_app_player(server, tmp_path):
    """<audio src> в WebView2 не ставит заголовков: токен — в query, Range — как обычно.
    Плеер карточки просит `track=playback` (сведённые стороны звонка)."""
    track = tmp_path / "playback.opus"
    track.write_bytes(bytes(range(256)) * 8)
    asked = []
    server.state.track_path = lambda rid, t: asked.append((rid, t)) or track
    query = urllib.parse.urlencode({"track": "playback", "token": server.token})
    url = f"http://127.0.0.1:{server.port}/recordings/2026-09-30_10-00/audio?{query}"
    req = urllib.request.Request(url, headers={"Range": "bytes=0-", "Origin": "http://tauri.localhost"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 206
        assert r.headers["Content-Type"] == "audio/ogg"
        assert r.headers["Content-Range"] == "bytes 0-2047/2048"
        assert r.headers["Accept-Ranges"] == "bytes"
        assert len(r.read()) == 2048
    assert asked == [("2026-09-30_10-00", "playback")]


@pytest.mark.parametrize("asked, expected", [
    ("bytes=0-", "bytes 0-2097151/5242880"),            # открытый запрос плеера
    ("bytes=100-4000000", "bytes 100-2097251/5242880"),  # слишком длинный кусок
    ("bytes=5000000-", "bytes 5000000-5242879/5242880"),  # хвост короче лимита — целиком
])
def test_audio_range_is_served_in_bounded_chunks(server, tmp_path, asked, expected):
    """Ответ на Range — не больше 2 МБ: файл открыт недолго, удаление записи его не ждёт."""
    track = tmp_path / "playback.opus"
    track.write_bytes(bytes(5 * 1024 * 1024))
    server.state.track_path = lambda rid, t: track
    url = f"http://127.0.0.1:{server.port}/recordings/x/audio?track=playback&token={server.token}"
    req = urllib.request.Request(url, headers={"Range": asked})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 206
        assert r.headers["Content-Range"] == expected
        first, last = expected.split()[1].split("/")[0].split("-")
        assert len(r.read()) == int(last) - int(first) + 1 == int(r.headers["Content-Length"])


def test_audio_stream_marks_the_folder_busy(server, tmp_path, monkeypatch):
    """Пока дорожка отдаётся, папка занята — удаление записи подождёт."""
    from contextlib import contextmanager

    from meet import playback

    track = tmp_path / "rec" / "playback.opus"
    track.parent.mkdir()
    track.write_bytes(b"x" * 100)
    used = []

    @contextmanager
    def using(folder):
        used.append(folder)
        yield

    monkeypatch.setattr(playback, "using", using)
    server.state.track_path = lambda rid, t: track
    url = f"http://127.0.0.1:{server.port}/recordings/x/audio?track=playback&token={server.token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-"}), timeout=5) as r:
        r.read()
    assert used == [track.parent]


def test_audio_without_token_is_refused(server, tmp_path):
    server.state.track_path = lambda rid, t: tmp_path / "x.opus"
    url = f"http://127.0.0.1:{server.port}/recordings/x/audio?track=playback"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url, timeout=5)
    assert e.value.code == 401


def test_audio_mix_failure_is_503(server):
    def broken(rid, t):
        raise control.Unavailable("ffmpeg не найден — дорожки записи не сведены для плеера")

    server.state.track_path = broken
    url = f"http://127.0.0.1:{server.port}/recordings/x/audio?track=playback&token={server.token}"
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url, timeout=5)
    assert e.value.code == 503


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


def test_second_resident_on_the_same_port_falls_back(monkeypatch, tmp_path):
    """Два резидента (разные папки данных) с одним предпочтительным портом:
    второй не должен «разделить» порт с первым (на Windows SO_REUSEADDR это
    позволяет, и запросы уходили бы не тому) — он берёт другой."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        preferred = probe.getsockname()[1]
    first = control.ControlServer(FakeState(), port=preferred, fallback=True)
    second = control.ControlServer(FakeState(), port=preferred, fallback=True)
    try:
        assert first.start(publish=False) == preferred
        port = second.start(publish=False)
        assert port != preferred and port > 0
    finally:
        second.stop(pid=1)
        first.stop(pid=1)


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
    # Модель для одного действия — телом запроса (U3).
    _post(server, "/recordings/r1/summary", {"provider": "claude-code"})
    assert ("summary-body", "r1", {"provider": "claude-code"}) in server.state_obj.calls
    assert _get(server, "/recordings/r1/summary")["markdown"] == "# Итоги"
    assert _get(server, "/recordings/r1/live-draft")["markdown"] == "**Тема:** Т"
    assert _get(server, "/recordings/%D0%BD%D0%B5%D1%82/live-draft", expect=404) == {"error": "черновика нет"}
    assert _get(server, "/recordings/нет/summary".replace("нет", "%D0%BD%D0%B5%D1%82"),
                expect=404) == {"error": "итогов нет"}


def test_summary_without_provider_is_409_with_text(server):
    server.state_obj.provider_ready = False
    got = _post(server, "/recordings/r1/summary", expect=409)
    assert got == {"error": "Подключите Claude Code, Codex или OpenCode в настройках"}


def test_ask_qa_notes_routes(server):
    assert _post(server, "/recordings/r1/ask", {"question": "что решили?"})["kind"] == "ask"
    assert ("ask", "r1", {"question": "что решили?"}) in server.state_obj.calls
    assert _get(server, "/recordings/r1/qa")["items"][0]["q"] == "а"
    assert _post(server, "/recordings/r1/notes", expect=400) == {
        "error": "Папка для встреч не задана"}


def test_kb_export_routes(server):
    assert _post(server, "/recordings/r1/kb-export", expect=400) == {
        "error": "Папка для встреч не задана"}
    server.state_obj.meetings_dir = "D:/kb"
    got = _post(server, "/recordings/r1/kb-export")
    assert got == {"path": "D:/kb/2026-09-30 - Планирование спринта", "files": ["Транскрипт.md"]}
    # «В заметки» из прежнего окна — та же выгрузка.
    assert _post(server, "/recordings/r2/notes")["files"] == ["Транскрипт.md"]
    assert ("kb-export", "r2") in server.state_obj.calls


def test_agent_context_route(server):
    got = _post(server, "/recordings/r1/agent-context")
    assert got == {"folder": "D:/rec/r1", "files": ["transcript.md"]}
    assert ("agent-context", "r1") in server.state_obj.calls
    assert server.state_obj.agent_body == {}
    _post(server, "/recordings/r1/agent-context", {"provider": "codex"})
    assert server.state_obj.agent_body == {"provider": "codex"}
    assert _post(server, "/recordings/%D0%BD%D0%B5%D1%82/agent-context", expect=404) == {
        "error": "записи нет"}


def test_agent_files_route(server):
    got = _get(server, "/recordings/r1/agent-context")
    assert got == {"files": ["transcript.md", "summary.md"], "live": False}
    assert ("agent-files", "r1") in server.state_obj.calls
    assert _get(server, "/recordings/%D0%BD%D0%B5%D1%82/agent-context", expect=404) == {
        "error": "записи нет"}


def test_export_preview_route_passes_query(server):
    template = urllib.parse.quote("{year}/{date} - {title}")
    got = _get(server, f"/export/preview?folder_template={template}&include_srt=true")
    assert got["error"] is None and got["folder"] == "2026-09-30 - Планирование спринта"
    assert ("preview", {"folder_template": "{year}/{date} - {title}",
                        "include_srt": "true"}) in server.state_obj.calls


def test_assistant_routes(server):
    assert _get(server, "/assistant")["checking"] is True
    # Запуск агента в терминале не ждёт проверки локальной модели.
    assert _get(server, "/assistant?local=0")["checking"] is True
    assert [c for c in server.state_obj.calls if c[0] == "assistant"] == [
        ("assistant", True), ("assistant", False)]
    got = _post(server, "/assistant/check", {"provider": "codex"})
    assert got == {"ok": True, "error": None, "provider": "codex"}
    got = _post(server, "/assistant/local-models", {"base_url": "http://localhost:11434/v1"})
    assert got["models"] == [{"id": "qwen3:8b"}]
    assert ("local-models", {"base_url": "http://localhost:11434/v1"}) in server.state_obj.calls


# --- живой режим -------------------------------------------------------------


def test_live_routes_reach_state(server):
    assert _post(server, "/live/start")["starting"] is True
    assert _post(server, "/live/stop")["action"] == "stopping"
    assert _post(server, "/live/ask", {"question": "что решили?"}) == {"answer": "ответ"}
    assert _post(server, "/live/task", {"task": "Ревью"}) == {"ok": True}
    assert _post(server, "/live/hint", {"id": "h1", "action": "pin"}) == {"ok": True, "changed": True}
    calls = server.state_obj.calls
    assert calls == ["live.start", "live.stop", ("live.ask", {"question": "что решили?"}),
                     ("live.task", {"task": "Ревью"}),
                     ("live.hint", {"id": "h1", "action": "pin"})]


def test_live_start_without_provider_is_409(server):
    server.state_obj.provider_ready = False
    assert _post(server, "/live/start", expect=409) == {
        "error": "Подключите Claude Code, Codex или OpenCode в настройках"}


def test_live_start_during_recording_is_400(server):
    server.state_obj.recording = True
    assert _post(server, "/live/start", expect=400) == {"error": "Идёт обычная запись"}


def test_live_events_relays_stream_and_last_event_id(server):
    server.state_obj.live_active = True
    url = f"http://127.0.0.1:{server.port}/live/events?token={server.token}"
    req = urllib.request.Request(url, headers={"Last-Event-ID": "2"})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert "text/event-stream" in r.headers["Content-Type"]
        body = r.read().decode("utf-8")  # поток кончился вместе с живым режимом
    assert body == ('event: state\ndata: {"status": null}\n\n'
                    'event: line\nid: 3\ndata: {"text": "а"}\n\n')
    assert server.state_obj.live_last_id == "2"


def test_live_events_without_live_is_409(server):
    _get(server, "/live/events", expect=409)


def test_device_test_probe_failure_is_503_with_text(server):
    got = _post(server, "/devices/test", {"kind": "mic", "name": "сломан"}, expect=503)
    assert got["error"] == "Не удалось проверить устройство: Устройство не ответило"


def test_speakers_panel_routes(server):
    rid = "2026-09-30_16-04"
    assert _get(server, f"/recordings/{rid}/speakers")["pos"] == 0
    body = {"ops": [{"type": "reset", "label": "Анна"}], "remember": {}}
    assert _post(server, f"/recordings/{rid}/speakers/apply", body) == {"pos": 1}
    _post(server, f"/recordings/{rid}/speakers/apply", {"bad": True}, expect=400)
    _post(server, f"/recordings/{rid}/speakers/undo", {"expect_step": "s1"}, expect=409)
    assert _post(server, f"/recordings/{rid}/speakers/redo", {}) == {"pos": 1}
    assert _post(server, f"/recordings/{rid}/speakers/revert", {"to_step_id": "a1"}) == {"pos": 0}
    turn = {"idx": [3], "labels": ["Анна"], "count": 9, "to": None}
    assert _post(server, f"/recordings/{rid}/speakers/relabel", turn) == {"pos": 2}
    calls = [c for c in server.state_obj.calls if isinstance(c, tuple) and c[0].startswith("speakers")]
    assert calls == [("speakers", rid), ("speakers_apply", rid, body),
                     ("speakers_apply", rid, {"bad": True}), ("speakers_undo", rid, {"expect_step": "s1"}),
                     ("speakers_redo", rid), ("speakers_revert", rid, {"to_step_id": "a1"}),
                     ("speakers_relabel", rid, turn)]


def test_speaker_split_and_threshold_routes(server):
    rid = "2026-09-30_16-04"
    base = f"/recordings/{rid}/speakers"
    assert _post(server, f"{base}/split/prepare", {"label": "Спикер 2"}) == {"ready": True}
    assert _post(server, f"{base}/split/preview", {"label": "Спикер 2", "k": 2}) == {"groups": []}
    assert _post(server, f"{base}/split/apply", {"label": "Спикер 2", "groups": []}) == {"pos": 3}
    assert _post(server, f"{base}/threshold", {"value": 0.7}) == {"changes": []}
    assert _post(server, f"{base}/threshold/apply", {"value": 0.7}) == {"pos": 4}
    calls = [c[0] for c in server.state_obj.calls if isinstance(c, tuple) and c[0].startswith("speakers_")]
    assert calls[-5:] == ["speakers_split_prepare", "speakers_split_preview", "speakers_split_apply",
                          "speakers_threshold", "speakers_threshold_apply"]


def test_split_turn_and_rediarize_routes(server):
    rid = "2026-09-30_16-04"
    base = f"/recordings/{rid}/speakers"
    assert _post(server, f"{base}/split-turn", {"turn": [1], "at": 1, "char": 3}) == {"pos": 5}
    assert _post(server, f"{base}/rediarize", {"num_speakers": 4}) == {"job": {"id": "r1"}}
    _get(server, f"{base}/rediarize", expect=404)
    assert _post(server, f"{base}/rediarize/apply", {}) == {"pos": 6}
    assert _post(server, f"{base}/rediarize", method="DELETE") == {"ok": True}
    calls = [c[0] for c in server.state_obj.calls if isinstance(c, tuple) and c[0].startswith("speakers_")]
    assert calls[-5:] == ["speakers_split_turn", "speakers_rediarize", "speakers_rediarized",
                          "speakers_rediarize_apply", "speakers_rediarize_discard"]


@pytest.mark.parametrize("token", [None, "wrong"])
def test_declared_but_missing_body_does_not_hold_the_handler(server, token, monkeypatch):
    """Клиент объявил тело и не прислал его: дочитывание ждёт не дольше
    DRAIN_TIMEOUT_S и закрывает соединение, а не держит поток обработчика
    вечно (и для отказа 401 тоже)."""
    monkeypatch.setattr(control, "DRAIN_TIMEOUT_S", 0.5)
    server.state.live_stop = lambda: {"ok": False, "action": "not-live"}
    head = (f"POST /live/stop HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n"
            f"Authorization: Bearer {token or server.token}\r\n"
            f"Content-Type: application/json\r\nContent-Length: 1000\r\n\r\n")
    with socket.create_connection(("127.0.0.1", server.port), timeout=3) as sock:
        sock.sendall(head.encode("utf-8"))
        started = time.monotonic()
        chunks = []
        while True:
            chunk = sock.recv(65536)   # без таймаута сервера — socket.timeout через 3 с
            if not chunk:
                break
            chunks.append(chunk)
    reply = b"".join(chunks)
    assert reply.startswith(b"HTTP/1.1 200" if token is None else b"HTTP/1.1 401")
    assert time.monotonic() - started < 2.5


def test_group_routes(server):
    calls = server.state_obj.calls
    assert _get(server, "/groups?q=x&people=%D0%90%D0%BD%D0%BD%D0%B0") == {"groups": [], "unknown": []}
    assert ("groups", "x") in calls and server.state_obj.filters == {"people": ["Анна"]}
    assert _post(server, "/groups", {"name": "Альфа"})["id"] == "g-1"
    assert _post(server, "/groups/g-1", {"name": "Б"}, method="PATCH") == {"id": "g-1", "name": "Б"}
    _post(server, "/groups/g-none", {"name": "Б"}, method="PATCH", expect=404)
    assert _post(server, "/groups/order", {"ids": ["g-1"]}, method="PUT") == {"groups": []}
    assert ("order_groups", {"ids": ["g-1"]}) in calls
    assert _post(server, "/groups/g-1/members", {"add": ["r1"]})["changed"] == ["r1"]
    _post(server, "/groups/g-none/members", {"add": ["r1"]}, expect=400)
    assert _post(server, "/groups/g-1", method="DELETE")["index"] == 0
    assert ("delete_group", "g-1") in calls


def test_participants_route(server):
    got = _get(server, "/participants?q=%D0%B0%D0%BD&limit=5")
    assert got == [{"name": "Анна", "meetings": 2, "last_at": None, "owner": False}]
    assert ("participants", "ан", 5) in server.state_obj.calls
    _get(server, "/participants")
    assert ("participants", "", 20) in server.state_obj.calls


def test_facets_route(server):
    assert _get(server, "/facets?q=x&groups=g-1&has=summary") == {"total": 0}
    assert ("facets", "x") in server.state_obj.calls
    assert server.state_obj.filters == {"groups": ["g-1"], "has": ["summary"]}
