"""Модель из настроек (`llm.model`) доходит до КАЖДОГО вызова модели.

Правило: везде работает настроенный агент. Каждый вызывающий — итоги,
вопросы, анализ, улучшение, название, тики и вопросы живого ассистента —
берёт runner у настоящего `llm.resolve`; в тесте подменены только поиск CLI
(`detect`) и сам вызов Claude (`claude.run`). Модель и сеть не трогаются. Люди и реплики выдуманы.
"""

import asyncio
import json

import pytest

from meet import job_worker, library, titles
from meet.llm import claude, detect
from meet.llm.base import AgentReply

MODEL = "opus"

@pytest.fixture
def world(tmp_path, monkeypatch):
    """Настройки с `llm.model = opus`, найденный и авторизованный Claude Code,
    подменённый вызов модели (запоминает модель и system prompt)."""
    data = tmp_path / "data"
    rec, voices = tmp_path / "rec", tmp_path / "voices"
    data.mkdir()
    rec.mkdir()
    (data / "config.json").write_text(json.dumps({
        "llm": {"provider": "claude-code", "model": MODEL},
        "recording": {"out_dir": str(rec), "voices_dir": str(voices)},
    }), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    monkeypatch.setattr(detect, "find_claude", lambda: "C:/bin/claude.exe")
    monkeypatch.setattr(detect, "logged_in", lambda name, path: (True, None))
    calls: list[dict] = []
    replies: dict[str, str] = {}

    async def fake_run(prompt, **kw):
        calls.append({"model": kw.get("model"), "system": kw.get("system_prompt")})
        return AgentReply(text=replies.get("text", "ответ модели"))

    monkeypatch.setattr(claude, "run", fake_run)
    folder = rec / "2026-09-30_16-04"
    folder.mkdir()
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Демьян", "text": "Начнём со сроков."}]})
    return {"calls": calls, "replies": replies, "folder": folder, "rec": rec, "voices": voices}


def _summary(w):
    job_worker.main(["summary", str(w["folder"])])


def _ask(w):
    job_worker.main(["ask", str(w["folder"]), "--question=что решили?"])


def _analyze(w):
    job_worker.main(["analyze", str(w["folder"])])


def _improve(w):
    w["replies"]["text"] = '{"replacements": []}'
    job_worker.main(["improve", str(w["folder"])])


def _title(w):
    titles.main([str(w["folder"])])


def _live(w, monkeypatch, *, act):
    """Живой ассистент целиком (`run_assist`) с настоящими Digester и
    QAService; звук и распознавание подменены. `act` — что сделать с
    готовыми сервисами: тик или вопрос."""
    from meet import settings
    from meet.assist import app as app_mod

    made = {}
    real_digester, real_qa = app_mod.Digester, app_mod.QAService

    def digester(*a, **kw):
        made["digester"] = real_digester(*a, **kw)
        return made["digester"]

    def qa(*a, **kw):
        made["qa"] = real_qa(*a, **kw)
        return made["qa"]

    class FakeEngine:
        def __init__(self, out_dir, transcriber, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def process_window(self):
            pass

    # Проверка CLI и входа до захвата звука (`_claude_login_problem`) ищет
    # настоящий claude: на машине разработчика он есть, в CI — нет. Тест — про
    # модель вызовов, не про установку: вход считаем выполненным.
    monkeypatch.setattr(app_mod, "_claude_login_problem", lambda: None)

    async def fake_check_auth(proxy=None, model=None):
        # Проверка входа перед стартом — тоже вызов модели, и тоже настроенной.
        w["calls"].append({"model": model, "system": "проверка входа"})
        return None

    class FakeConversation:
        """Постоянный диалог подсказок (claude_stream): модель — его параметр."""

        stateful = alive = True

        def __init__(self, *, system_prompt, model=None, **kw):
            self.model, self.turns, self.context_tokens = model, 0, 0

        async def send(self, text, *, on_text=None, timeout_s=90.0):
            self.turns += 1
            w["calls"].append({"model": self.model, "system": "диалог подсказок"})
            return AgentReply(text='{"op":"none"}')

        def close(self):
            pass

    async def fake_main(state, port, **kw):
        state.bus.publish("[00:00:05] Демьян: Давайте зафиксируем сроки релиза и ответственных за него.")

    monkeypatch.setattr(app_mod, "Digester", digester)
    monkeypatch.setattr(app_mod, "QAService", qa)
    monkeypatch.setattr(app_mod, "check_auth", fake_check_auth)
    monkeypatch.setattr(app_mod, "_main", fake_main)
    monkeypatch.setattr("meet.llm.claude_stream.Conversation", FakeConversation)
    monkeypatch.setattr("meet.asr.Transcriber", lambda: object())
    monkeypatch.setattr("meet.live.LiveEngine", FakeEngine)
    app_mod.run_assist(out_root=str(w["rec"] / "live"), no_voices=True, open_browser=False,
                       port=0, cfg=settings.load())
    w["replies"]["text"] = '{"ops": []}'
    if act == "tick":
        asyncio.run(made["digester"].tick_once())
        assert any(c["system"] == "диалог подсказок" for c in w["calls"])
    else:
        asyncio.run(made["qa"].ask("какой срок?"))


CALLERS = [
    ("summary", "итоги", _summary, 1),
    ("ask", "вопрос по записи", _ask, 1),
    ("analyze", "анализ встречи", _analyze, 1),
    ("improve", "улучшение расшифровки", _improve, 1),
    ("title", "название записи", _title, 1),
    ("live-tick", "тики живого ассистента: подсказки и сводка", "tick", 2),
    ("live-qa", "вопрос во время встречи", "qa", 1),
]


@pytest.mark.parametrize("name,call,min_calls", [c[1:] for c in CALLERS], ids=[c[0] for c in CALLERS])
def test_configured_model_reaches_every_caller(world, monkeypatch, name, call, min_calls):
    if isinstance(call, str):
        _live(world, monkeypatch, act=call)
    else:
        call(world)
    calls = world["calls"]
    assert len(calls) >= min_calls, f"{name}: модель не вызывалась"
    assert {c["model"] for c in calls} == {MODEL}, f"{name}: {calls}"
