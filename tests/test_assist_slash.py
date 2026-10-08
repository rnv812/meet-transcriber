"""Слэш-команды чата с ассистентом (0.4, спец. §6): разбор, команды Meet у
трёх провайдеров, ход-команда CLI дословно с уровнем USER, `/clear` без
переписки в затравке, «Продолжить разговор». Диалог и runner поддельные,
модель не зовётся."""

import asyncio

import pytest
import test_assist_participant as tap
from test_assist_participant import SILENT, FakeRunner, _make, agents, publish, run

from meet.assist import slash
from meet.assist.chatlog import SESSIONS_JSON, chat_dir
from meet.llm import consent
from meet.llm.base import AgentReply
from meet.llm.claude_stream import ControlError


class CtlConv(tap.FakeConversation):
    """Диалог Claude Code с управлением (`control`): команды и модели из
    `initialize`, MCP-серверы, смена модели; уровень хода — из ворот."""

    def __init__(self, script, ids, **kwargs):
        super().__init__(script, ids, **kwargs)
        # Как ответ `initialize` и `system/init` на 2.1.293 (пробник): навык пользователя —
        # и в `commands`, и в `slash_commands`, и в `skills`.
        self.commands = [{"name": "context", "description": "Заполнение контекста", "hint": ""},
                         {"name": "review", "description": "Ревью", "hint": "[PR]"},
                         {"name": "summeet", "description": "Итоги встречи по транскрипту", "hint": "[файл]"}]
        self.models = [{"value": "opus", "displayName": "Opus"}, {"value": "sonnet", "displayName": "Sonnet"}]
        self.slash_commands = ["context", "review", "my-deploy", "summeet"]
        self.skills = ["summeet"]
        self.mcp_servers = [{"name": "team-jira", "status": "failed", "error": "Connection closed"},
                            {"name": "gitlab", "status": "connected"}]
        self.controls = []
        self.levels = []

    async def send(self, text, *, images=(), on_text=None, timeout_s=90.0, on_event=None):
        gate = self.kwargs.get("gate")
        self.levels.append(gate.level if gate is not None else None)
        return await super().send(text, images=images, on_text=on_text, timeout_s=timeout_s)

    async def mcp_status(self, *, settle_s=0.0):
        self.controls.append("mcp_status")
        return [dict(s) for s in self.mcp_servers]

    async def mcp_reconnect(self, name=None):
        self.controls.append(("reconnect", name))
        self.mcp_servers[0] = {"name": "team-jira", "status": "connected"}
        return {"results": {name or "team-jira": None}, "servers": [dict(s) for s in self.mcp_servers],
                "restarted": False, "context": True}

    async def mcp_toggle(self, name, enabled):
        self.controls.append(("toggle", name, enabled))
        return [dict(s) for s in self.mcp_servers]

    async def set_model(self, model):
        if model == "nope":
            raise ControlError("set_model", "Unable to validate model nope")
        self.controls.append(("set_model", model))
        return f"claude-{model}-5"


@pytest.fixture(autouse=True)
def _ctl_conv(monkeypatch):
    monkeypatch.setattr(tap, "FakeConversation", CtlConv)


async def _settle(h):
    """Команды Meet идут фоном: дождаться их."""
    for _ in range(200):
        pending = [t for t in h.p._background if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _lines(h, name=None):
    return [m for m in h.chat.messages()
            if m["kind"] == "system" and m.get("card") == "command" and (name is None or m.get("command") == name)]


async def _post(h, text):
    out = await h.p.post_user_message(text)
    await _settle(h)
    return out


# --- разбор ---


def test_parse():
    assert slash.parse("/mcp") == slash.Command("mcp", "")
    assert slash.parse("  /mcp reconnect team-jira ") == slash.Command("mcp", "reconnect team-jira")
    assert slash.parse("/model\nsonnet") == slash.Command("model", "sonnet")
    assert slash.parse("/my-deploy:prod now") == slash.Command("my-deploy:prod", "now")
    assert slash.parse("//mcp") is None and slash.unescape("//mcp") == "/mcp"
    assert slash.parse("/home/x") is None             # путь, а не команда
    assert slash.parse("/") is None and slash.parse("/1abc") is None
    assert slash.parse("скажи /mcp") is None
    assert slash.parse("/mcp", attachments=["a1"]) is None
    assert slash.parse(None) is None


# --- команды Meet (Claude Code) ---


def test_help_lists_meet_and_cli_commands_without_a_model_turn(tmp_path):
    h = _make(tmp_path, freedom=True)

    async def main():
        await h.p.start()
        out = await _post(h, "/help")
        await h.p.shutdown()
        return out

    out = run(main())
    assert out["command"] == "help" and out["queued"] is False
    user = [m for m in h.chat.messages() if m["kind"] == "user"][0]
    assert user["via"] == "command" and user["text"] == "/help"
    (line,) = _lines(h, "help")
    names = [(i["name"], i["source"]) for i in line["items"]]
    assert ("mcp", "meet") in names and ("context", "cli") in names
    assert "/model [модель]" in line["text"] and "/review [PR]" in line["text"]
    assert line["re"] == user["id"]
    assert h.made[0].sent == []                          # модель не звали
    view = h.p.view()["commands"]
    assert {c["name"] for c in view} >= {"help", "mcp", "clear", "model", "compact", "context", "review"}


def test_mcp_shows_servers_and_reconnects(tmp_path):
    h = _make(tmp_path, freedom=True)

    async def main():
        await h.p.start()
        await _post(h, "/mcp")
        await _post(h, "/mcp reconnect team-jira")
        await _post(h, "/mcp disable gitlab")
        await _post(h, "/mcp frobnicate")
        await h.p.shutdown()

    run(main())
    status, reconnect, toggle, usage = _lines(h, "mcp")
    assert "team-jira — ошибка: Connection closed" in status["text"] and "gitlab — подключён" in status["text"]
    assert status["servers"][0]["status"] == "failed"
    assert "team-jira — переподключён" in reconnect["text"]
    assert ("reconnect", "team-jira") in h.made[0].controls
    assert ("toggle", "gitlab", False) in h.made[0].controls and "gitlab — выключен" in toggle["text"]
    assert usage["text"].startswith("Так: /mcp")
    assert h.made[0].sent == []
    assert not any("team-jira" in line for line in h.logs)    # в журнале процесса — без аргументов


def test_model_lists_sets_and_reports_cli_errors(tmp_path):
    h = _make(tmp_path, freedom=True, model="opus")

    async def main():
        await h.p.start()
        await _post(h, "/model")
        await _post(h, "/model sonnet")
        await _post(h, "/model nope")
        await h.p.shutdown()

    run(main())
    show, done, bad = _lines(h, "model")
    assert "Можно: opus, sonnet" in show["text"] and "В настройках: opus" in show["text"]
    assert done["text"] == "Модель до конца сессии: claude-sonnet-5"
    assert h.p.view()["model_override"] == "sonnet" and h.p.view()["model_configured"] == "opus"
    assert bad["level"] == "error" and "Unable to validate model nope" in bad["text"]


def test_clear_starts_a_new_session_seeded_without_the_chat(tmp_path):
    h = _make(tmp_path, script=[tap.say("Привет, слушаю"), SILENT], freedom=True)

    async def main():
        await h.p.start()
        await h.p.post_user_message("Ты тут?")
        await h.p.tick()
        await _post(h, "/clear")
        await h.p.post_user_message("Начнём заново")
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    first, second = h.made[0], h.made[1]
    assert first.closed and second.kwargs.get("resume") is None
    seed = second.sent[0][0]
    assert "Начнём заново" in seed and "Ты тут?" not in seed and "Привет, слушаю" not in seed
    assert "Новый разговор с агентом" in _lines(h, "clear")[0]["text"]
    sessions = (chat_dir(h.folder) / SESSIONS_JSON).read_text(encoding="utf-8")
    assert "sess-1" in sessions                                   # прежний — в past
    assert h.p.view()["session"] == "new"


def test_unknown_and_terminal_commands_are_lines_without_a_turn(tmp_path):
    h = _make(tmp_path, freedom=True)

    async def main():
        await h.p.start()
        await _post(h, "/foo bar baz")
        await _post(h, "/login")
        await h.p.shutdown()

    run(main())
    unknown, login = _lines(h)
    assert unknown["text"] == "Нет команды /foo — /help" and unknown["unknown"] == "/foo bar baz"
    assert login["text"] == "/login isn't available in this environment."
    assert h.made[0].sent == []


def test_escaped_slash_is_plain_text_for_the_agent(tmp_path):
    h = _make(tmp_path, script=[SILENT], freedom=True)

    async def main():
        await h.p.start()
        await h.p.post_user_message("//mcp — это что за команда?")
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert _lines(h) == []
    text = h.made[0].sent[0][0]
    assert "написал тебе:\n/mcp — это что за команда?" in text   # в ходе — без экранирования


# --- ход-команда CLI ---


def test_cli_command_goes_verbatim_as_a_user_level_turn(tmp_path):
    h = _make(tmp_path, script=[AgentReply(text="## Context\n12k tokens"), SILENT], freedom=True)

    async def main():
        await h.p.start()
        publish(h, 10, "Олег", "Обсуждаем релиз")
        await _post(h, "/context")
        await h.p.tick()
        h.clock.t = 100
        await h.p.tick()           # дальше — обычный ход: реплики и затравка
        await h.p.shutdown()

    run(main())
    conv = h.made[0]
    assert conv.sent[0][0] == "/context"                     # дословно, без затравки и дельты
    assert conv.levels[0] == consent.USER
    reply = [m for m in agents(h) if m.get("via") == "command"][0]
    assert reply["status"] == "shown" and reply["text"] == "## Context\n12k tokens"
    assert reply["re"] == [m for m in h.chat.messages() if m["kind"] == "user"][0]["id"]
    later = conv.sent[1][0]
    assert "Обсуждаем релиз" in later and "Сейчас на встрече" in later   # затравка после хода-команды


def test_user_skill_is_a_command_shown_apart_and_passed_through(tmp_path):
    h = _make(tmp_path, script=[AgentReply(text="Итоги: …")], freedom=True)

    async def main():
        await h.p.start()
        await _post(h, "/help")
        await _post(h, "/summeet встреча.md")
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    view = {c["name"]: c for c in h.p.view()["commands"]}
    assert view["summeet"]["source"] == "skill" and view["summeet"]["hint"] == "[файл]"
    assert view["context"]["source"] == "cli"
    (line,) = _lines(h, "help")
    assert "Навыки:\n/summeet [файл] — Итоги встречи по транскрипту" in line["text"]
    assert h.made[0].sent[0][0] == "/summeet встреча.md" and h.made[0].levels[0] == consent.USER


def test_claude_stream_reads_skills_from_init():
    from meet.llm.claude_stream import Conversation

    conv = Conversation(system_prompt="x")
    conv._observe({"type": "system", "subtype": "init", "slash_commands": ["compact", "summeet"],
                   "skills": ["summeet", {"name": "meet-probe-skill"}, ""]}, [])
    assert conv.skills == ["summeet", "meet-probe-skill"] and "summeet" in conv.slash_commands


def test_view_has_mcp_servers_and_models_for_argument_completion(tmp_path):
    h = _make(tmp_path, freedom=True)

    async def main():
        await h.p.start()
        await _post(h, "/mcp")
        before = h.p.view()["mcp_servers"]
        await _post(h, "/mcp reconnect team-jira")
        await h.p.shutdown()
        return before

    before = run(main())
    assert before == [{"name": "team-jira", "status": "failed"}, {"name": "gitlab", "status": "connected"}]
    assert h.p.view()["mcp_servers"][0] == {"name": "team-jira", "status": "connected"}   # после переподключения
    assert h.p.view()["models"] == [{"value": "opus", "label": "Opus"}, {"value": "sonnet", "label": "Sonnet"}]


def test_compact_reports_the_boundary(tmp_path):
    reply = AgentReply(text="", compacted={"trigger": "manual", "pre_tokens": 48211, "post_tokens": 3100})
    h = _make(tmp_path, script=[SILENT, reply], freedom=True)

    async def main():
        await h.p.start()
        await h.p.post_user_message("привет")
        await h.p.tick()
        await _post(h, "/compact оставь решения")
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert h.made[0].sent[1][0] == "/compact оставь решения"
    assert any(m["text"] == "Контекст сжат (было 48211 токенов)" for m in _lines(h))
    assert all(m.get("status") != "shown" for m in agents(h) if m.get("via") == "command")


# --- Codex ---


def test_codex_supports_what_is_meaningful(tmp_path, monkeypatch):
    from meet.llm import codex

    monkeypatch.setattr(codex, "mcp_servers", lambda *a, **k: [{"name": "jira", "enabled": True}])
    runner = FakeRunner([SILENT])
    h = _make(tmp_path, provider="codex", runner=runner, freedom=True)

    async def main():
        await h.p.start()
        await _post(h, "/compact")
        await _post(h, "/mcp")
        await _post(h, "/model gpt-5.5")
        await _post(h, "/context")
        await h.p.post_user_message("что дальше?")
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    compact, mcp, model, context = _lines(h)
    assert compact["text"] == "/compact недоступно для Codex"
    assert "jira — подключён" in mcp["text"] and "выключены" in mcp["text"]
    assert model["text"] == "Модель для следующих ходов: gpt-5.5"
    assert context["text"] == "Нет команды /context — /help"
    assert runner.calls[0][1]["model"] == "gpt-5.5"
    assert {c["name"] for c in h.p.view()["commands"]} == {"help", "mcp", "clear", "model"}


# --- «Продолжить разговор» после встречи ---


def test_after_meeting_command_is_answered_by_the_same_rules(tmp_path):
    from meet.assist.participant import answered

    h = _make(tmp_path, script=[AgentReply(text="Ревью: всё хорошо")], freedom=True, after_meeting=True)

    async def main():
        await h.p.start()
        mid = h.chat.append("user", text="/help", after_meeting=True).message["id"]
        await h.p.queue_existing(mid)
        rid = h.chat.append("user", text="/review 12", after_meeting=True).message["id"]
        await h.p.queue_existing(rid)
        await h.p.tick()
        await h.p.shutdown()
        return mid, rid

    mid, rid = run(main())
    messages = h.chat.messages()
    assert answered(messages, mid) and answered(messages, rid)
    assert [m for m in messages if m["id"] == mid][0]["via"] == "command"
    assert h.made[0].sent[0][0] == "/review 12"
