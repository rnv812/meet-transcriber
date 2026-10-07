"""Чат записи после встречи (V4 задача 8): вложения резидентом (файл, вставленная
картинка, «×» до отправки), кнопки и реакции, документы базы знаний для
чипов-источников окна, папка базы знаний группы (`PATCH /groups/{id}`).

Разбор, пределы и квоты — те же, что во время встречи
(`participant.parse_attachment` → `attachments.save` / `materials.add`).
Конфиг и записи — во временной папке (фикстуры `test_chat_resident`)."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from meet import control, groups, jobs, materials, tray_control
from meet.assist import attachments, participant
from meet.assist.chatlog import ChatLog

from test_chat_api import _png
from test_chat_resident import (RID, RouteState, _call, _events, _write_config, app,  # noqa: F401
                                server, state)


def _folder(tmp_path):
    return tmp_path / "recordings" / RID


def _config(tmp_path) -> dict:
    return json.loads((tmp_path / "meet" / "config.json").read_text(encoding="utf-8"))


def _doc(tmp_path, name="План запуска.md", text="# План\n\nЗапуск 15 ноября.\n"):
    path = tmp_path / "docs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- вложения после встречи --------------------------------------------------------------


def test_attach_a_document_after_the_meeting(state, app, tmp_path):
    got = _events(app)
    doc = _doc(tmp_path)
    out = state.recording_chat_attach(RID, {"path": str(doc)})
    assert out["status"] == "ready" and out["id"] == "a1"
    record = out["attachment"]
    assert record["kind"] == "attachment" and record["type"] == "doc"
    assert record["name"] and record["source"] == str(doc)
    # Текст — рядом в папке встречи, материал — в assistant/materials; исходник не копируется.
    dump = _folder(tmp_path) / "assistant" / "materials" / "a1.txt"
    assert record["path"] == str(dump) and "15 ноября" in dump.read_text(encoding="utf-8")
    assert materials.read(_folder(tmp_path), "a1") is not None
    assert any(e.kind == "chat.updated" and e.data["id"] == RID for e in got)
    # В ленте: запись вложения есть (окно покажет её у сообщения), сообщение ссылается на id.
    sent = state.continue_chat(RID, {"text": "Что в плане?", "attachments": ["a1"]})
    assert sent["message"]["attachments"] == ["a1"] and sent["job"]["kind"] == jobs.CHAT
    feed = state.recording_chat(RID)["messages"]
    assert [m["id"] for m in feed] == ["a1", "m1"]


def test_paste_an_image_after_the_meeting(state, tmp_path):
    out = state.recording_chat_paste(RID, _png(), "image/png; charset=binary", "%D1%81%D0%BA%D1%80%D0%B8%D0%BD.png")
    assert out["status"] == "ready"
    record = out["attachment"]
    assert record["type"] == "image" and record["name"] == "скрин.png"
    saved = record["path"]
    assert saved.startswith(str(_folder(tmp_path) / "assistant" / "files"))
    assert (_folder(tmp_path) / "assistant" / "files").is_dir()


def test_image_note_when_the_model_has_no_vision(state, tmp_path):
    shot = tmp_path / "docs" / "shot.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(_png())
    out = state.recording_chat_attach(RID, {"path": str(shot), "provider": "openai-compatible"})
    assert out["attachment"]["vision"] is False and out["attachment"]["note"]
    out = state.recording_chat_attach(RID, {"path": str(shot), "provider": "codex"})
    assert out["attachment"]["vision"] is True and "note" not in out["attachment"]


def test_attach_checks_like_the_live_route(state, tmp_path):
    for path in ("rel/x.md", "//server/share/x.md", "\\\\server\\x.md", str(tmp_path / "нет.md"), None,
                 "C:/x\x00.md"):
        with pytest.raises(control.BadRequest):
            state.recording_chat_attach(RID, {"path": path})
    with pytest.raises(control.BadRequest):
        state.recording_chat_paste(RID, b"%PDF", "application/pdf")
    with pytest.raises(control.BadRequest):
        state.recording_chat_paste(RID, b"", "image/png")
    assert state.recording_chat_attach("../..", {"path": str(_doc(tmp_path))}) == {"error": "записи нет"}
    assert not (_folder(tmp_path) / "assistant").exists()


def test_attach_that_does_not_parse_is_a_failed_record(state, tmp_path):
    out = state.recording_chat_paste(RID, b"not an image", "image/png", "x.png")
    assert out["status"] == "failed" and out["error"]
    assert out["attachment"]["note"].startswith("не разобрано")


def test_attach_keeps_the_session_quota(state, tmp_path):
    # Разборщик — отдельный процесс: квоту набираем файлами, а не подменой предела.
    files = _folder(tmp_path) / "assistant" / "files"
    files.mkdir(parents=True)
    for k in range(attachments.MAX_PER_SESSION - 1):
        (files / f"x{k}.png").write_bytes(b"x")
    assert state.recording_chat_paste(RID, _png(), "image/png")["status"] == "ready"
    second = state.recording_chat_paste(RID, _png(), "image/png")
    assert second["status"] == "failed" and second["error"] == attachments.TOO_MANY


def test_attach_is_gated_like_continue_chat(state, tmp_path):
    doc = _doc(tmp_path)
    state.live.folder = _folder(tmp_path)
    with pytest.raises(control.Conflict, match="живой режим"):
        state.recording_chat_attach(RID, {"path": str(doc)})
    with pytest.raises(control.Conflict):
        state.recording_chat_paste(RID, _png(), "image/png")
    state.live.folder = None
    _write_config(tmp_path, {**_config(tmp_path), "assist": {"participant": False}})
    with pytest.raises(control.Conflict, match="выключен в настройках"):
        state.recording_chat_attach(RID, {"path": str(doc)})
    assert state.recording_chat(RID)["enabled"] is False
    assert not (_folder(tmp_path) / "assistant").exists()


def _fake_worker(monkeypatch, code: str):
    """Разборщик вложения — свой скрипт вместо `meet.assist.attach_worker`
    (аргумент — папка встречи)."""
    def argv(folder, item, vision):
        return [sys.executable, "-c", code, str(folder)]

    monkeypatch.setattr(tray_control, "_attach_worker_argv", argv)


REAL_ARGV = tray_control._attach_worker_argv
SLOW_WORKER = """
import pathlib, sys, time
d = pathlib.Path(sys.argv[1]) / "assistant" / "materials"
d.mkdir(parents=True, exist_ok=True)
(d / "a1.json").write_text("{}")
(d / "a1.txt").write_text("x")
(d / "started").write_text("")
time.sleep(60)
"""


def test_attach_is_parsed_in_a_child_process(state, tmp_path, monkeypatch):
    seen = {}
    real = tray_control._attach_worker_argv

    def argv(folder, item, vision):
        seen["argv"] = real(folder, item, vision)
        return seen["argv"]

    monkeypatch.setattr(tray_control, "_attach_worker_argv", argv)
    doc = _doc(tmp_path)
    assert state.recording_chat_attach(RID, {"path": str(doc)})["status"] == "ready"
    assert seen["argv"][1:4] == ["-m", "meet.assist.attach_worker", str(_folder(tmp_path))]
    assert f"--path={doc}" in seen["argv"]
    state.recording_chat_paste(RID, _png(), "image/png", "shot.png")
    assert "--name=shot.png" in seen["argv"]


def test_attach_timeout_kills_the_parser_and_leaves_no_orphan(state, tmp_path, monkeypatch):
    monkeypatch.setattr(tray_control, "CHAT_ATTACH_TIMEOUT_S", 3.0)
    _fake_worker(monkeypatch, SLOW_WORKER)
    mats = _folder(tmp_path) / "assistant" / "materials"
    with pytest.raises(control.Unavailable, match="слишком долго"):
        state.recording_chat_attach(RID, {"path": str(_doc(tmp_path))})
    # Разборщик успел положить материал (метка «started») — и после отказа его нет.
    assert not (mats / "a1.json").exists() and not (mats / "a1.txt").exists()
    assert not (mats / "started").exists()
    assert state.recording_chat(RID)["messages"] == []
    # Место свободно: следующий разбор идёт.
    monkeypatch.setattr(tray_control, "_attach_worker_argv", REAL_ARGV)
    assert state.recording_chat_attach(RID, {"path": str(_doc(tmp_path))})["status"] == "ready"


def test_one_parse_at_a_time(state, tmp_path, monkeypatch):
    monkeypatch.setattr(tray_control, "CHAT_ATTACH_TIMEOUT_S", 4.0)
    _fake_worker(monkeypatch, SLOW_WORKER)
    started = _folder(tmp_path) / "assistant" / "materials" / "started"
    first = {}

    def run():
        try:
            state.recording_chat_attach(RID, {"path": str(_doc(tmp_path))})
        except Exception as e:   # срок выйдет — 503
            first["error"] = e

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 10
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert started.exists()
    with pytest.raises(control.Unavailable, match="Разбирается другой файл"):
        state.recording_chat_paste(RID, _png(), "image/png")
    worker.join(15)
    assert isinstance(first.get("error"), control.Unavailable)


def test_parser_crash_is_a_failed_record_without_files(state, tmp_path, monkeypatch):
    _fake_worker(monkeypatch, """
import pathlib, sys
d = pathlib.Path(sys.argv[1]) / "assistant" / "files"
d.mkdir(parents=True, exist_ok=True)
(d / "a1.png").write_bytes(b"x")
sys.exit(3)
""")
    out = state.recording_chat_paste(RID, _png(), "image/png", "shot.png")
    assert out["status"] == "failed" and out["error"]
    assert not (_folder(tmp_path) / "assistant" / "files" / "a1.png").exists()


def test_attach_waits_for_transcription(state, tmp_path):
    job = state.queue.submit(jobs.TRANSCRIBE, str(_folder(tmp_path)), {})
    try:
        for call in (lambda: state.recording_chat_attach(RID, {"path": str(_doc(tmp_path))}),
                     lambda: state.continue_chat(RID, {"text": "что решили?"}),
                     lambda: state.recording_chat_paste(RID, _png(), "image/png")):
            with pytest.raises(control.Conflict, match="ещё расшифровывается"):
                call()
    finally:
        state.queue.cancel(job.id)


def test_after_meeting_routes_refuse_a_recording_in_progress_and_unknown_live(state, tmp_path, monkeypatch):
    reply = _agent_reply(tmp_path)
    folder = _folder(tmp_path)
    monkeypatch.setattr(state, "_busy_now", lambda: {state._key(folder)})
    calls = (
        lambda: state.recording_chat_attach(RID, {"path": str(_doc(tmp_path))}),
        lambda: state.recording_chat_paste(RID, _png(), "image/png"),
        lambda: state.recording_chat_remove(RID, "a1"),
        lambda: state.recording_chat_click(RID, reply["id"], {"label": "Да"}),
        lambda: state.recording_chat_react(RID, reply["id"], {"emoji": "👍"}),
    )
    for call in calls:
        with pytest.raises(control.Conflict, match="Запись ещё идёт"):
            call()
    monkeypatch.setattr(state, "_busy_now", lambda: set())

    def broken():
        raise RuntimeError("ребёнок не отвечает")

    state.live.status = broken
    for call in calls:
        with pytest.raises(control.Unavailable, match="живой режим"):
            call()


def test_remove_before_sending_after_the_meeting(state, tmp_path):
    out = state.recording_chat_paste(RID, _png(), "image/png")
    saved = out["attachment"]["path"]
    assert state.recording_chat_remove(RID, out["id"]) == {"ok": True, "removed": True}
    assert not Path(saved).exists()
    assert all(m["id"] != out["id"] for m in state.recording_chat(RID)["messages"])
    assert state.recording_chat_remove(RID, out["id"]) == {"ok": True, "removed": False}
    # Отправленное убрать нельзя.
    doc = state.recording_chat_attach(RID, {"path": str(_doc(tmp_path))})
    state.continue_chat(RID, {"text": "вот", "attachments": [doc["id"]]})
    with pytest.raises(control.BadRequest, match="отправлено"):
        state.recording_chat_remove(RID, doc["id"])
    for bad in ("m1", "../a1", "a"):
        with pytest.raises(control.BadRequest):
            state.recording_chat_remove(RID, bad)


# --- кнопки и реакции после встречи --------------------------------------------------------


def _agent_reply(tmp_path, **fields):
    log = ChatLog(_folder(tmp_path))
    log.append("user", text="Что решили?", after_meeting=True)
    return log.append("agent", text="Срок — 15 ноября.", status="shown", mode="reply", re="m1",
                      **fields).message


def test_click_a_button_after_the_meeting(state, app, tmp_path):
    reply = _agent_reply(tmp_path, buttons=["Подробнее", "Не надо"])
    got = _events(app)
    out = state.recording_chat_click(RID, reply["id"], {"label": "Подробнее", "client_id": "k-9"})
    message = out["message"]
    assert message["via"] == "button" and message["re"] == reply["id"] and message["text"] == "Подробнее"
    assert out["job"]["kind"] == jobs.CHAT
    assert state.llm_queue.get(out["job"]["id"]).options == {"message": message["id"]}
    assert any(e.kind == "chat.updated" for e in got)
    again = state.recording_chat_click(RID, reply["id"], {"label": "Подробнее", "client_id": "k-9"})
    assert again["duplicate"] is True and again["message"]["id"] == message["id"]
    with pytest.raises(control.BadRequest, match="нет кнопки"):
        state.recording_chat_click(RID, reply["id"], {"label": "Глянь"})
    with pytest.raises(control.BadRequest):
        state.recording_chat_click(RID, "../m2", {"label": "Подробнее"})


def test_react_after_the_meeting(state, app, tmp_path):
    reply = _agent_reply(tmp_path)
    got = _events(app)
    assert state.recording_chat_react(RID, reply["id"], {"emoji": "👍"}) == {"ok": True, "changed": True}
    feed = {m["id"]: m for m in state.recording_chat(RID)["messages"]}
    assert "👍" in feed[reply["id"]]["reactions"]
    assert any(e.kind == "chat.updated" for e in got)
    assert state.recording_chat_react(RID, reply["id"], {"emoji": "👍", "on": True})["changed"] is False
    assert state.recording_chat_react(RID, reply["id"], {"emoji": "👎"}) == {"ok": True, "changed": True}
    assert state.llm_queue.listing() == []          # 👍 и 👎 — только в журнал
    with pytest.raises(control.BadRequest):
        state.recording_chat_react(RID, reply["id"], {"emoji": "🔥"})
    with pytest.raises(control.BadRequest, match="реплики агента"):
        state.recording_chat_react(RID, "m1", {"emoji": "👍"})
    state.live.folder = _folder(tmp_path)
    with pytest.raises(control.Conflict):
        state.recording_chat_react(RID, reply["id"], {"emoji": "❓"})


def test_question_reaction_after_the_meeting_asks_the_agent_to_explain(state, tmp_path):
    reply = _agent_reply(tmp_path)
    out = state.recording_chat_react(RID, reply["id"], {"emoji": "❓"})
    assert out["changed"] is True and out["job"]["kind"] == jobs.CHAT
    message = out["message"]
    assert message["via"] == "reaction" and message["re"] == reply["id"] and message["text"] == tray_control.EXPLAIN_TEXT
    assert state.llm_queue.get(out["job"]["id"]).options == {"message": message["id"]}
    # Снять ❓ — без нового вопроса и задачи.
    off = state.recording_chat_react(RID, reply["id"], {"emoji": "❓"})
    assert off == {"ok": True, "changed": True}
    assert len(state.llm_queue.listing()) == 1


def test_question_reaction_reaches_the_agent_as_a_request_to_explain(tmp_path, monkeypatch):
    from meet import job_worker, library

    from test_chat_jobs import Runner

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    folder = tmp_path / "recordings" / RID
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "title": "Планёрка", "segments": [
        {"start": 0.0, "end": 2.0, "speaker": "Демьян", "text": "Релиз переносим на пятницу."}]})
    log = ChatLog(folder)
    log.append("agent", text="Риск — у биллинга нет владельца.", status="shown", mode="proactive")
    asked = log.append("user", text=tray_control.EXPLAIN_TEXT, via="reaction", re="m1", after_meeting=True)
    runner = Runner('{"say": "Это из этапа 2 плана: там не назначен ответственный."}')
    assert job_worker._chat(str(folder), asked.message["id"], runner=runner, provider="codex") == 0
    prompt = runner.calls[0][0]
    assert participant.EXPLAIN_REQUEST in prompt and "у биллинга нет владельца" in prompt
    answer = [m for m in log.messages() if m["kind"] == "agent"][-1]
    assert answer["re"] == asked.message["id"] and answer["status"] == "shown"


def test_control_routes_for_the_chat_after_the_meeting(server):
    calls = server.state_obj.calls
    _call(server, f"/recordings/{RID}/chat/attach", {"path": "C:/x.md"})
    assert calls[-1] == ("recording_chat_attach", RID, {"path": "C:/x.md"})
    _call(server, f"/recordings/{RID}/chat/paste", raw=b"\x89PNG", headers={
        "Content-Type": "image/png", "X-File-Name": "%D1%84.png"})
    assert calls[-1] == ("recording_chat_paste", RID, b"\x89PNG", "image/png", "%D1%84.png")
    _call(server, f"/recordings/{RID}/chat/attachments/a3/remove", {})
    assert calls[-1] == ("recording_chat_remove", RID, "a3")
    _call(server, f"/recordings/{RID}/chat/m3/click", {"label": "Глянь"})
    assert calls[-1] == ("recording_chat_click", RID, "m3", {"label": "Глянь"})
    _call(server, f"/recordings/{RID}/chat/m3/react", {"emoji": "👍"})
    assert calls[-1] == ("recording_chat_react", RID, "m3", {"emoji": "👍"})


def test_after_meeting_paste_over_10_mb_is_refused(server):
    import urllib.error

    with pytest.raises(urllib.error.HTTPError) as e:
        _call(server, f"/recordings/{RID}/chat/paste", raw=b"0" * (10 * 1024 * 1024 + 1),
              headers={"Content-Type": "image/png"})
    assert e.value.code == 400
    assert not server.state_obj.calls


# --- документы базы знаний для чипов-источников ----------------------------------------


def test_kb_docs_lists_the_kb_without_exclusions(state, tmp_path):
    assert state.kb_docs() == {"root": None, "docs": [], "more": False}
    kb = tmp_path / "kb"
    for rel in ("Проекты/Альфа/План.md", "Личное/Дневник.md", "Заметки.txt", ".trash/old.md", "pic.png"):
        path = kb / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    _write_config(tmp_path, {**_config(tmp_path), "assistant": {"knowledge_dir": str(kb)}})
    out = state.kb_docs()
    assert out["root"] == str(kb) and out["more"] is False
    assert sorted(out["docs"]) == ["Заметки.txt", "Проекты/Альфа/План.md"]


def test_state_tells_the_shell_where_the_kb_is(state, tmp_path):
    assert state.snapshot()["knowledge_dir"] is None
    kb = tmp_path / "kb"
    kb.mkdir()
    _write_config(tmp_path, {**_config(tmp_path), "assistant": {"knowledge_dir": str(kb)}})
    assert state.snapshot()["knowledge_dir"] == str(kb)


# --- папка базы знаний группы: PATCH /groups/{id} -------------------------------------------


def test_patch_group_kb_folder(state, tmp_path):
    kb = tmp_path / "kb"
    (kb / "Проекты" / "Альфа").mkdir(parents=True)
    _write_config(tmp_path, {**_config(tmp_path), "assistant": {"knowledge_dir": str(kb)}})
    gid = state.create_group({"name": "Альфа"})["id"]
    got = state.patch_group(gid, {"kb_folder": "проекты\\альфа"})
    assert got["kb_folder"] == "Проекты/Альфа"            # регистр — как на диске
    listed = {g["id"]: g for g in state.groups()["groups"]}
    assert listed[gid]["kb_folder"] == "Проекты/Альфа"
    # Название и цвет — как раньше, папка остаётся.
    state.patch_group(gid, {"name": "Альфа-2"})
    assert groups.kb_folder(groups.load(state._root())[0]) == "Проекты/Альфа"
    for bad in ("C:/Проекты", "/abs", "../вне", "a/../b"):
        with pytest.raises(control.BadRequest):
            state.patch_group(gid, {"kb_folder": bad})
    state.patch_group(gid, {"kb_folder": None})
    assert "kb_folder" not in {g["id"]: g for g in state.groups()["groups"]}[gid]
    missing = state.patch_group("g-нет", {"kb_folder": "x"})
    assert missing == {"error": "группы нет"}


def test_patch_group_route_passes_kb_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    class St(RouteState):
        def __getattr__(self, name):
            if name == "patch_group":
                def call(*args):
                    self.calls.append((name, *args))
                    return {"ok": True}
                return call
            return super().__getattr__(name)

    st = St()
    srv = control.ControlServer(st)
    srv.start(pid=4344)
    try:
        _call(srv, "/groups/g1", {"kb_folder": "Проекты/Альфа"}, method="PATCH")
        assert st.calls[-1] == ("patch_group", "g1", {"kb_folder": "Проекты/Альфа"})
    finally:
        srv.stop(pid=4344)


def test_agent_tells_the_window_whether_the_map_has_the_kb():
    from meet.assist import kb_prep

    assert participant._map_has_kb(kb_prep.MAP_KB_HEAD + "\n- План.md") is True
    assert participant._map_has_kb("Прошлые встречи группы «Альфа» (путь · дата · название):\n- meet:x") is False
    assert participant._map_has_kb("") is False
