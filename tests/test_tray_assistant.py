"""Ассистент в резиденте: итоги, вопросы, «В заметки», выбор провайдера.

Модель здесь не вызывается: задачи ставятся в очередь с подменённым spawn, а
`llm.resolve` и `detect.available` подменяются monkeypatch'ем.
"""

import json
import subprocess
import threading
import time

import pytest

import meet.llm as llm
from meet import control, jobs, library, tray, tray_control
from meet.llm import detect

RID = "2026-09-30_16-04"


def _write_config(root, data: dict) -> None:
    path = root / "meet" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _set_config(tmp_path, **assistant) -> None:
    _write_config(tmp_path, {
        "auto_record": {"enabled": False, "processes": []},
        "recording": {"out_dir": str(tmp_path / "recordings"),
                      "voices_dir": str(tmp_path / "voices")},
        "llm": {"provider": assistant.pop("provider", "auto")},
        "assistant": {"knowledge_dir": None, "notes_dir": None, **assistant},
    })


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    _set_config(tmp_path)
    folder = tmp_path / "recordings" / RID
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "title": "Планёрка", "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Демьян", "text": "Начнём."}]})
    return tray.TrayApp()


def _blocked(app):
    release = threading.Event()

    def spawn(job, on_line):
        release.wait(timeout=5)
        return 0

    return jobs.JobQueue(app.bus, spawn=spawn), release


@pytest.fixture
def state(app):
    queue, release = _blocked(app)
    llm_queue, llm_release = _blocked(app)
    st = tray_control.TrayControl(app, queue=queue, llm_queue=llm_queue)
    try:
        yield st
    finally:
        release.set()
        llm_release.set()
        queue.stop()
        llm_queue.stop()


def _installed(monkeypatch, **found):
    def available(base_url=None, probe_local=True):
        return {name: {"found": found.get(name.replace("-", "_"), False)}
                for name in llm.PROVIDERS}
    monkeypatch.setattr(detect, "available", available)


# --- итоги и вопросы ---------------------------------------------------------


def test_summary_without_any_provider_is_409_text(state, monkeypatch):
    _installed(monkeypatch)
    with pytest.raises(control.Conflict, match="Подключите Claude Code, Codex или OpenCode"):
        state.make_summary(RID)
    assert state.llm_queue.listing() == []


def test_summary_with_explicit_missing_provider_is_409(state, monkeypatch, tmp_path):
    _set_config(tmp_path, provider="codex")
    _installed(monkeypatch, claude_code=True)
    with pytest.raises(control.Conflict):
        state.make_summary(RID)


def test_summary_goes_to_the_llm_queue_once(state, monkeypatch):
    _installed(monkeypatch, codex=True)
    first = state.make_summary(RID)
    second = state.make_summary(RID)
    assert first["kind"] == jobs.SUMMARY and second["id"] == first["id"]
    assert len(state.llm_queue.listing()) == 1
    assert state.queue.listing() == []


def test_summary_of_unknown_recording(state, monkeypatch):
    _installed(monkeypatch, codex=True)
    assert "error" in state.make_summary("../..")


def test_ask_queues_question(state, monkeypatch):
    _installed(monkeypatch, codex=True)
    job = state.ask(RID, {"question": "  что решили?  "})
    assert job["kind"] == jobs.ASK
    assert state.llm_queue.get(job["id"]).options == {"question": "что решили?"}


@pytest.mark.parametrize("body", [{}, {"question": "   "}, {"question": "x" * 5000},
                                  {"question": 5}])
def test_ask_needs_a_sane_question(state, monkeypatch, body):
    _installed(monkeypatch, codex=True)
    with pytest.raises(control.BadRequest):
        state.ask(RID, body)


def test_ask_without_provider_is_409(state, monkeypatch):
    _installed(monkeypatch)
    with pytest.raises(control.Conflict):
        state.ask(RID, {"question": "что решили?"})


def test_get_summary_and_qa(state, tmp_path):
    assert state.summary(RID) == {"error": "итогов нет"}
    folder = tmp_path / "recordings" / RID
    (folder / "summary.md").write_text("# Итоги — x\n", encoding="utf-8")
    library.write_meta(folder, {"summary_at": 123.0})
    assert state.summary(RID) == {"markdown": "# Итоги — x\n", "created_at": 123.0}
    assert state.qa(RID) == {"items": []}
    (folder / "qa.jsonl").write_text('{"q": "а", "a": "б", "at": 1.0, "provider": "x"}\n',
                                     encoding="utf-8")
    assert state.qa(RID)["items"][0]["q"] == "а"


# --- очереди -------------------------------------------------------------------


def test_jobs_lists_both_queues_and_cancel_finds_either(state, monkeypatch):
    _installed(monkeypatch, codex=True)
    summary = state.make_summary(RID)
    transcribe = state.transcribe(RID)
    ids = [item["id"] for item in state.jobs()["items"]]
    assert ids == [summary["id"], transcribe["id"]]
    assert state.cancel_job(summary["id"]) == {"ok": True}
    assert state.llm_queue.get(summary["id"]).state == jobs.CANCELLED
    assert state.cancel_job("нет-такой") == {"ok": False}


# --- провайдер -----------------------------------------------------------------


def _wait(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "не дождались"
        time.sleep(0.02)


def test_assistant_resolves_in_background_and_caches(state, monkeypatch, tmp_path):
    _installed(monkeypatch, codex=True)
    gate = threading.Event()
    calls = []

    def resolve(cfg):
        calls.append(cfg.llm.provider)
        gate.wait(timeout=5)
        return "codex", None

    monkeypatch.setattr(llm, "resolve", resolve)
    first = state.assistant()  # не ждёт проверки входа
    assert first["provider"] is None and first["checking"] is True
    assert first["setting"] == "auto"
    assert first["available"]["codex"]["found"] is True
    assert first["knowledge_dir"] is None and "notes_dir" not in first
    gate.set()
    _wait(lambda: state.assistant()["checking"] is False)
    assert state.assistant()["provider"] == "codex"
    assert calls == ["auto"]  # закэшировано

    # Смена настройки сбрасывает кэш: старый ответ не выдаётся за новый.
    _set_config(tmp_path, provider="claude-code")
    again = state.assistant()
    assert again["provider"] is None and again["checking"] is True
    _wait(lambda: state.assistant()["checking"] is False)
    assert calls == ["auto", "claude-code"]


def test_provider_cache_expires_but_serves_stale_value(monkeypatch):
    now = [0.0]
    answers = iter(["codex", "claude-code"])
    cache = tray_control.ProviderCache(resolve=lambda cfg: (next(answers), None),
                                       ttl=60.0, clock=lambda: now[0])
    cfg = type("Cfg", (), {"llm": type("L", (), {"provider": "auto",
                                                 "base_url": "u"})()})()
    cache.get(cfg)
    _wait(lambda: cache.get(cfg) == ("codex", False))
    now[0] = 61.0
    assert cache.get(cfg) == ("codex", True)  # устарело — отдаём прежнее и проверяем
    _wait(lambda: cache.get(cfg) == ("claude-code", False))


def test_provider_cache_survives_resolve_failure():
    def boom(cfg):
        raise OSError("нет")

    cache = tray_control.ProviderCache(resolve=boom)
    cfg = type("Cfg", (), {"llm": type("L", (), {"provider": "auto",
                                                 "base_url": "u"})()})()
    cache.get(cfg)
    _wait(lambda: cache.get(cfg) == (None, False))


def test_check_provider_runs_check_subprocess(state, monkeypatch):
    from meet import netproxy

    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(netproxy, "read_registry",
                        lambda: {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:3067"})
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["timeout"] = argv, kwargs.get("timeout")
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            argv, 1, stdout='шум\n{"ok": false, "error": "не авторизован", '
                            '"provider": "codex"}\n', stderr="")

    monkeypatch.setattr(tray_control.subprocess, "run", fake_run)
    got = state.check_provider({"provider": "codex"})
    assert got == {"ok": False, "error": "не авторизован", "provider": "codex"}
    assert seen["argv"][1:] == ["-m", "meet.llm.check", "codex"]
    assert seen["timeout"] == 90
    # Системный прокси — ребёнку переменными (Claude Code/Codex его сами не видят).
    assert {k.upper(): v for k, v in seen["env"].items()}["HTTPS_PROXY"] == "http://127.0.0.1:3067"


def test_check_provider_timeout_is_an_answer(state, monkeypatch):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 90)

    monkeypatch.setattr(tray_control.subprocess, "run", slow)
    got = state.check_provider({"provider": "auto"})
    assert got["ok"] is False and got["provider"] == "auto" and got["error"]


def test_check_provider_rejects_unknown(state):
    with pytest.raises(control.BadRequest):
        state.check_provider({"provider": "rm -rf"})


def test_delete_refused_while_model_works_on_recording(state, monkeypatch, tmp_path):
    """Подпроцесс задачи (и CLI модели) живут с cwd в папке записи: rmtree
    снёс бы файлы и упал на папке, а задача дописала бы итоги в призрак."""
    _installed(monkeypatch, codex=True)
    state.make_summary(RID)
    with pytest.raises(control.BadRequest, match="Идёт работа модели"):
        state.delete_recording(RID)
    assert (tmp_path / "recordings" / RID / "transcript.json").exists()


def test_delete_refused_while_question_is_queued(state, monkeypatch):
    _installed(monkeypatch, codex=True)
    state.ask(RID, {"question": "что решили?"})
    with pytest.raises(control.BadRequest, match="Идёт работа модели"):
        state.delete_recording(RID)


@pytest.mark.parametrize("action", [
    lambda st: st.make_summary(RID),
    lambda st: st.ask(RID, {"question": "что решили?"}),
])
def test_summary_and_ask_wait_for_transcription(state, monkeypatch, action):
    """Итоги по транскрипту, который вот-вот перепишет расшифровка, устарели
    бы сразу."""
    _installed(monkeypatch, codex=True)
    state.transcribe(RID)
    with pytest.raises(control.Conflict, match="Дождитесь окончания расшифровки"):
        action(state)
    assert state.llm_queue.listing() == []


def test_invalidate_during_refresh_is_not_lost():
    """Человек вошёл в CLI, пока шла проверка: её ответ (снятый до входа) не
    должен закэшироваться как свежий."""
    gate = threading.Event()
    answers = ["старый", "новый"]

    def resolve(cfg):
        gate.wait(timeout=5)
        return answers.pop(0), None

    cache = tray_control.ProviderCache(resolve=resolve)
    cfg = type("Cfg", (), {"llm": type("L", (), {"provider": "auto",
                                                 "base_url": "u"})()})()
    assert cache.get(cfg) == (None, True)
    cache.invalidate()  # проверка ещё идёт
    gate.set()
    _wait(lambda: answers == ["новый"] and not cache._running)
    assert cache.get(cfg) == ("старый", True)  # устаревшее, идёт пересчёт
    _wait(lambda: cache.get(cfg) == ("новый", False))


def test_assistant_describes_proxy(state, monkeypatch):
    """Окно показывает, какой прокси получат Claude Code и Codex."""
    from meet import netproxy

    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(netproxy, "read_registry",
                        lambda: {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:3067"})
    _installed(monkeypatch, codex=True)
    monkeypatch.setattr(llm, "resolve", lambda cfg: ("codex", None))
    assert state.assistant()["proxy"] == {
        "mode": "system", "effective": "http://127.0.0.1:3067", "source": "system",
        "system": "http://127.0.0.1:3067"}


def test_live_draft_of_a_recording(state, tmp_path):
    assert state.live_draft(RID) == {"error": "черновика нет"}
    folder = tmp_path / "recordings" / RID
    (folder / "live_state.json").write_text(json.dumps({
        "version": 2, "saved_at": 5.0,
        "summary": {"topic": "Планёрка", "points": [], "decisions": [{"id": "d1", "text": "Срок — пятница"}],
                    "tasks": [], "open_questions": []},
        "hints": [{"id": "h1", "kind": "risk", "text": "Нет владельца"}],
    }, ensure_ascii=False), encoding="utf-8")
    draft = state.live_draft(RID)
    assert draft["summary"]["topic"] == "Планёрка" and draft["hints"][0]["id"] == "h1"
    assert "- Срок — пятница" in draft["markdown"]
    assert state.live_draft("нет-такой") == {"error": "черновика нет"}
