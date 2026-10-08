"""Ход работы ассистента в чате (0.4, спец. §3 «как в Claude CLI»): строки
вызовов инструментов — записи журнала `kind: "tool"`, `event: "call"`,
патчи по `tool_use_id`; вид и краткая строка вызова; решение ворот;
пределы `input_preview` (2 КБ) и `output_preview` (64 КБ); Codex и OpenCode —
из их событий. Модель не зовётся."""

import json

from meet.assist import tool_rows as tr
from meet.assist.chatlog import ChatLog, visible_in_feed
from meet.llm import codex, opencode


def _log(tmp_path):
    return ChatLog(tmp_path / "rec", log=lambda _m: None)


def _use(tid, name, data, server=None, tool=None):
    return {"type": "tool_use", "id": tid, "name": name, "server": server, "tool": tool or name,
            "input": data, "parent": None}


def _rows(chat):
    return [m for m in chat.messages() if m["kind"] == "tool" and m.get("event") == "call"]


# --- вид и краткая строка ---


def test_summary_of_each_kind_of_call():
    assert tr.describe("Bash", {"command": "sed -n '1,40p' a.md\n | cut -c1-80"}) == {
        "view": "shell", "label": "Bash", "summary": "sed -n '1,40p' a.md | cut -c1-80"}
    assert tr.describe("Read", {"file_path": "C:/rec/transcript.md"})["label"] == "Чтение"
    assert tr.describe("Read", {"file_path": "C:/rec/transcript.md"})["summary"] == "C:/rec/transcript.md"
    grep = tr.describe("Grep", {"pattern": "запуск", "path": "C:/kb"})
    assert grep["view"] == "search" and grep["summary"] == "запуск — C:/kb"
    edit = tr.describe("Edit", {"file_path": "C:/kb/План.md", "old_string": "a\nb", "new_string": "c\nd\ne"})
    assert edit == {"view": "edit", "label": "Правка", "summary": "C:/kb/План.md", "added": 3, "removed": 2}
    write = tr.describe("Write", {"file_path": "C:/kb/new.md", "content": "1\n2\n"})
    assert write["added"] == 2 and write["removed"] == 0
    multi = tr.describe("MultiEdit", {"file_path": "x", "edits": [{"old_string": "a", "new_string": "b\nc"}] * 2})
    assert (multi["added"], multi["removed"]) == (4, 2)
    mcp = tr.describe("mcp__team-jira__jira_get_issue", {"issue_key": "ABC-12"}, server="team-jira",
                      tool="jira_get_issue")
    assert mcp == {"view": "mcp", "label": "MCP", "summary": "team-jira · jira_get_issue issue_key: ABC-12"}
    assert tr.describe("Skill", {"skill": "summeet"}) == {"view": "skill", "label": "Навык", "summary": "summeet"}
    assert tr.describe("WebFetch", {"url": "https://example.com/a", "prompt": "x"})["summary"] == "https://example.com/a"
    assert tr.describe("WebSearch", {"query": "pyannote"})["label"] == "Веб-поиск"
    assert tr.describe("Task", {"description": "найти тесты"})["label"] == "Подагент"
    other = tr.describe("TodoWrite", {"todos": [1, 2]})
    assert other["view"] == "other" and other["label"] == "TodoWrite"


def test_previews_are_capped_in_utf8_bytes():
    big = "я" * 5000
    preview = tr.input_preview("Bash", {"command": big})
    assert len(preview.encode("utf-8")) <= tr.INPUT_PREVIEW_MAX and preview.endswith("…")
    out, cut = tr.output_preview("ж" * 40_000)
    assert cut and len(out.encode("utf-8")) <= tr.OUTPUT_PREVIEW_MAX
    assert tr.output_preview("ok") == ("ok", False)


def test_gate_labels():
    assert tr.gate_view({"decision": "auto"}) == {"decision": "auto", "label": "разрешено автоматически"}
    assert tr.gate_view({"decision": "allowed"})["label"] == "разрешено автоматически"
    assert tr.gate_view({"decision": "approved"})["label"] == "спросил вас · разрешено"
    assert tr.gate_view({"decision": "approved", "grant": True})["label"] == "спросил вас · до конца встречи"
    assert tr.gate_view({"decision": "declined", "why": "declined"})["label"] == "спросил вас · отклонено"
    assert tr.gate_view({"decision": "denied", "why": "sensitive"})["label"] == "запрещено: закрытые данные"
    assert tr.gate_view({"decision": "denied", "why": ""})["label"] == "запрещено"


# --- журнал: строка и её патчи ---


def test_tool_use_gate_result_become_one_row_patched_by_id(tmp_path):
    chat = _log(tmp_path)
    rows = tr.ToolRows()
    ev = rows.apply(chat, _use("toolu_1", "Bash", {"command": "git status"}), reply="m3", t=12.5)
    assert ev and ev[0]["op"] == "add"
    row = _rows(chat)[0]
    assert row["tool_use_id"] == "toolu_1" and row["status"] == "running" and row["reply"] == "m3"
    assert row["label"] == "Bash" and row["summary"] == "git status" and row["t"] == 12.5
    assert row["input_preview"] == "git status"
    assert visible_in_feed(row)
    rows.apply(chat, {"type": "gate", "id": "toolu_1", "name": "Bash", "via": "hook", "decision": "auto",
                      "reason": "", "why": ""})
    rows.apply(chat, {"type": "tool_result", "id": "toolu_1", "ok": True, "output": "On branch main\n",
                      "truncated": False, "duration_ms": 420, "parent": None})
    row = _rows(chat)[0]
    assert row["status"] == "done" and row["duration_ms"] == 420
    assert row["output_preview"] == "On branch main\n" and row["gate"]["label"] == "разрешено автоматически"
    # Повтор того же tool_use — без второй строки.
    assert rows.apply(chat, _use("toolu_1", "Bash", {"command": "git status"})) == []
    assert len(_rows(chat)) == 1


def test_error_keeps_the_first_line_and_a_gate_denial_stays_denied(tmp_path):
    chat = _log(tmp_path)
    rows = tr.ToolRows()
    rows.apply(chat, _use("a", "Bash", {"command": "npm test"}))
    rows.apply(chat, {"type": "tool_result", "id": "a", "ok": False, "output": "\nError: boom\nat x\n",
                      "truncated": False, "duration_ms": 5})
    rows.apply(chat, _use("b", "Bash", {"command": "rm -rf C:/x"}))
    rows.apply(chat, {"type": "gate", "id": "b", "name": "Bash", "decision": "declined", "why": "declined",
                      "reason": "Пользователь отклонил"})
    rows.apply(chat, {"type": "tool_result", "id": "b", "ok": False, "output": "Пользователь отклонил",
                      "truncated": False, "duration_ms": 1})
    a, b = _rows(chat)
    assert a["status"] == "error" and a["error"] == "Error: boom"
    assert b["status"] == "denied" and b["gate"]["label"] == "спросил вас · отклонено"


def test_result_and_gate_without_a_row_are_ignored_and_hooks_get_their_own_row(tmp_path):
    chat = _log(tmp_path)
    rows = tr.ToolRows()
    assert rows.apply(chat, {"type": "tool_result", "id": "nope", "ok": True, "output": ""}) == []
    assert rows.apply(chat, {"type": "gate", "id": None, "decision": "auto"}) == []
    rows.apply(chat, {"type": "hook", "hook_id": "h1", "name": "PreToolUse:Bash", "event": "PreToolUse",
                      "status": "started", "outcome": None, "exit_code": None, "output": ""})
    rows.apply(chat, {"type": "hook", "hook_id": "h1", "name": "PreToolUse:Bash", "event": "PreToolUse",
                      "status": "done", "outcome": "success", "exit_code": 0, "output": "ok"})
    (hook,) = _rows(chat)
    assert hook["view"] == "hook" and hook["label"] == "Хук" and hook["summary"] == "PreToolUse:Bash"
    assert hook["status"] == "done"


def test_rows_are_compact_in_the_seed_and_one_line_in_assistant_chat_md(tmp_path):
    chat = _log(tmp_path)
    rows = tr.ToolRows()
    rows.apply(chat, _use("a", "Read", {"file_path": "C:/rec/transcript.md"}))
    rows.apply(chat, {"type": "tool_result", "id": "a", "ok": True, "output": "СЕКРЕТНЫЙ ВЫВОД " * 50})
    context = chat.context(4000)
    assert "Чтение" in context and "C:/rec/transcript.md" in context
    assert "СЕКРЕТНЫЙ ВЫВОД" not in context          # вывод модель уже видела — в затравку не идёт
    md = chat.render_md()
    assert "Чтение" in md and "СЕКРЕТНЫЙ ВЫВОД" not in md


# --- Codex и OpenCode: что дают их события ---


def test_codex_items_become_tool_events():
    lines = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "bash -lc 'git status'",
                                            "aggregated_output": "clean\n", "exit_code": 0, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "i2", "type": "file_change", "status": "completed",
                                            "changes": [{"path": "C:/kb/a.md", "kind": "update"},
                                                        {"path": "C:/kb/b.md", "kind": "add"}]}},
        {"type": "item.completed", "item": {"id": "i3", "type": "mcp_tool_call", "server": "jira", "tool": "get",
                                            "status": "failed", "error": {"message": "нет доступа"}}},
        {"type": "item.completed", "item": {"id": "i4", "type": "web_search", "query": "meet"}},
        {"type": "item.completed", "item": {"id": "i5", "type": "agent_message", "text": "готово"}},
    ]
    events = codex.tool_events("\n".join(json.dumps(x) for x in lines))
    uses = [e for e in events if e["type"] == "tool_use"]
    results = {e["id"]: e for e in events if e["type"] == "tool_result"}
    assert [u["name"] for u in uses] == ["Bash", "apply_patch", "mcp__jira__get", "WebSearch"]
    assert uses[0]["input"] == {"command": "bash -lc 'git status'"}
    assert results["i1"]["ok"] and results["i1"]["output"] == "clean\n"
    assert results["i3"]["ok"] is False and "нет доступа" in results["i3"]["output"]
    assert tr.describe(uses[1]["name"], uses[1]["input"])["summary"] == "C:/kb/a.md (+ ещё 1)"


def test_opencode_tool_use_parts_become_tool_events():
    line = {"type": "tool_use", "sessionID": "s", "part": {
        "tool": "edit", "callID": "c1",
        "state": {"status": "completed", "input": {"filePath": "C:/kb/a.md", "oldString": "a", "newString": "b\nc"},
                  "output": "ok", "time": {"start": 1000, "end": 1600}}}}
    bad = {"type": "tool_use", "part": {"tool": "bash", "callID": "c2",
                                        "state": {"status": "error", "input": {"command": "rm x"}, "error": "denied"}}}
    events = opencode.tool_events("\n".join(json.dumps(x) for x in (line, bad)))
    use, res, use2, res2 = events
    assert use["name"] == "Edit" and use["input"] == {"file_path": "C:/kb/a.md", "old_string": "a", "new_string": "b\nc"}
    assert res["ok"] and res["duration_ms"] == 600
    assert use2["name"] == "Bash" and res2["ok"] is False and res2["output"] == "denied"


# --- M4: маскирование секретов перед записью в журнал ---


def test_mask_secrets_hides_obvious_tokens():
    m = tr.mask_secrets
    assert "sk-" not in m("key: sk-abcDEF012345678901234567890123")
    assert "sk-ant-" not in m("ANTHROPIC=sk-ant-api03-ABCdef_012-345xyz")
    assert "ghp_" not in m("ghp_0123456789abcdefABCDEF0123456789abcd")
    assert "gho_" not in m("token gho_0123456789abcdefABCDEF0123456789abcd")
    assert "github_pat_" not in m("github_pat_11ABCDE0000aZ_abcdefghijKLMNOP0123456789abcdefghij")
    assert "xoxb-" not in m("xoxb-123456789012-ABCDEFabcdef0123456789")
    assert "AKIA" not in m("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
    masked = m("Authorization: Bearer eyJabc.def.ghiJKLmnop0123456789")
    assert "eyJabc" not in masked and "Bearer" in masked
    for line in ("password=hunter2very", "PASSWORD = s3cr3t-value", "token: abc123DEFxyz",
                 "api_key=AKIAZZ99longvalue", "API-KEY: zzz999longvalue", "apikey=qwertykeyvalue1"):
        out = m(line)
        assert "скрыто" in out, line
    assert "BEGIN" not in m("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\ndef\n-----END OPENSSH PRIVATE KEY-----")


def test_mask_leaves_ordinary_text_alone():
    for text in ("On branch main\nnothing to commit", "const token = parseToken(req)",
                 "задача ABC-123 закрыта", "cat file.txt | head -n 5", ""):
        assert tr.mask_secrets(text) == text


def test_output_and_input_previews_are_masked():
    out, _cut = tr.output_preview("export GITHUB_TOKEN=ghp_0123456789abcdefABCDEF0123456789abcd\n")
    assert "ghp_" not in out and "скрыто" in out
    pv = tr.input_preview("Bash", {"command": "curl -H 'Authorization: Bearer secret012345token' https://x"})
    assert "secret012345token" not in pv


def test_masked_secret_in_a_tool_result_is_not_stored(tmp_path):
    chat = _log(tmp_path)
    rows = tr.ToolRows()
    rows.apply(chat, _use("toolu_s", "Bash", {"command": "cat .env"}))
    rows.apply(chat, {"type": "tool_result", "id": "toolu_s", "ok": True,
                      "output": "OPENAI_API_KEY=sk-ABCdef012345678901234567890123\n", "parent": None})
    raw = (tmp_path / "rec" / "assistant" / "chat.jsonl").read_text(encoding="utf-8")
    assert "sk-ABCdef012345678901234567890123" not in raw
    assert _rows(chat)[0]["output_preview"].count("скрыто") >= 1
