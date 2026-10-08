"""Поддельный Claude Code CLI в режиме stream-json — для тестов постоянного
диалога (`meet.llm.claude_stream`). Настоящую модель не зовёт.

Режим — переменная FAKE_CLAUDE_MODE:
  ok     — на каждое сообщение: init (один раз), куски текста, assistant (с
           uuid), result с usage;
  crash  — на втором сообщении процесс падает;
  hang   — на сообщение не отвечает;
  slow   — на сообщение: init и первый кусок текста, а result — только после
           остановки (`control_request` interrupt → `control_response`, затем
           result с ошибкой, как у claude 2.1.292);
  deaf   — как slow, но на остановку не отвечает вовсе;
  finish — как slow, но на остановку ход «успевает» закончиться: сначала
           удачный result с полным текстом, потом `control_response`;
  tool   — перед ответом спрашивает разрешение инструмента (`control_request`
           can_use_tool) и ждёт ответа хозяина; затем текст пояснения, новое
           сообщение модели и ответ.
  gate   — ворота согласия (0.3.7): на `initialize` отвечает успехом (с
           FAKE_CLAUDE_NO_INIT=1 — молчит); строки сообщения «TOOL {json}»
           (`name`, `input`, `ask`) — по каждой хук PreToolUse
           (`hook_callback`), а если хук не отказал и `ask` — ещё
           `can_use_tool`; ответ модели — JSON-список исходов
           (`{"name","result":"allowed"|"denied","reason"}`). В init —
           `mcp_servers` из FAKE_CLAUDE_MCP (имена через запятую, у имени
           может быть `:статус`), `permissionMode`, `slash_commands`.
           0.4 (как claude 2.1.293, сверено пробником): ответ `initialize` —
           `commands`, `models`, `current_permission_mode` (режим из
           `--permission-mode`; FAKE_CLAUDE_PERMISSION_MODE — подменить: автомод
           недоступен); control-запросы `mcp_status` (`mcpServers`),
           `mcp_reconnect` `{serverName}` (FAKE_CLAUDE_NO_RECONNECT=1 —
           «Unsupported control request subtype»), `mcp_toggle`, `set_model`
           (`haiku` в автомоде — `system/status` с `permissionMode: default`),
           `set_permission_mode`; неизвестный подтип — ошибка, как у CLI.
           Сообщение «/compact» — `system/status`, `system/compact_boundary`
           (`pre_tokens` 12345, `post_tokens` 900), повтор
           `<local-command-stdout>` и result без текста модели. Сообщение
           «/fail» — `<local-command-stderr>` и пустой result.
FAKE_CLAUDE_REJECT_IMAGES=1 — сообщение с блоком image отвергается, как API
(result с ошибкой «…image… Could not process image»).
`--resume=<id>`: id из FAKE_CLAUDE_KNOWN (через запятую) продолжается, иначе —
как настоящий CLI: «No conversation found with session ID» в stderr, result с
ошибкой, код 1 — сразу, до чтения stdin. С `--fork-session` — новый id сеанса.
FAKE_CLAUDE_LOG — файл, куда пишутся argv, cwd, окружение (JSON), сообщения
и uuid ответов модели (`{"assistant_uuid": …}`).
`model` в init — как у настоящего CLI: псевдоним `--model` — полное имя
(`MODELS`), без `--model` — модель CLI по умолчанию (самая новая, Fable);
FAKE_CLAUDE_INIT_MODEL — подменить (CLI запустил не ту модель).
"""

import json
import os
import sys
import uuid

# Псевдонимы claude 2.1.292 (`--help`: «an alias for the latest model»).
MODELS = {"fable": "claude-fable-5-1", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-4-5",
          "haiku": "claude-haiku-4-5"}
CLI_DEFAULT = MODELS["fable"]


def _arg(argv: list[str], name: str) -> str | None:
    for a in argv:
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None


def main() -> int:
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    reject_images = os.environ.get("FAKE_CLAUDE_REJECT_IMAGES") == "1"
    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    log = open(log_path, "a", encoding="utf-8") if log_path else None
    argv = sys.argv[1:]

    def note(obj) -> None:
        if log:
            log.write(json.dumps(obj, ensure_ascii=False) + "\n")
            log.flush()

    note({"argv": argv, "cwd": os.getcwd(), "pid": os.getpid(), "env": sorted(os.environ)})
    out = sys.stdout

    def emit(obj) -> None:
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    resume = _arg(argv, "--resume")
    session = resume or _arg(argv, "--session-id") or str(uuid.uuid4())
    if resume is not None:
        known = [k for k in os.environ.get("FAKE_CLAUDE_KNOWN", "").split(",") if k]
        if resume not in known:
            text = f"No conversation found with session ID: {resume}"
            sys.stderr.write(text + "\n")
            sys.stderr.flush()
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "num_turns": 0, "session_id": resume, "errors": [text]})
            return 1
        if "--fork-session" in argv:
            session = str(uuid.uuid4())
            note({"fork": {"from": resume, "to": session}})

    def result(n: int, text: str | None, error: bool = False, errors=None) -> None:
        emit({"type": "result", "subtype": "error_during_execution" if error else "success",
              "is_error": error, "result": text, "session_id": session,
              **({"errors": errors} if errors else {}),
              "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000 * n,
                        "cache_creation_input_tokens": 200, "output_tokens": 30}})

    def delta(piece: str) -> None:
        emit({"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}})

    def assistant(text: str) -> None:
        aid = str(uuid.uuid4())
        note({"assistant_uuid": aid})
        emit({"type": "assistant", "uuid": aid, "session_id": session,
              "message": {"content": [{"type": "text", "text": text}]}})

    n = 0
    pending = False  # ход начат и ждёт остановки (slow/deaf/finish)
    hook_ids: list[str] = []
    asked = 0

    def ask_host(request: dict) -> dict:
        """Запрос хозяину (`control_request`) и его ответ из stdin."""
        nonlocal asked
        asked += 1
        emit({"type": "control_request", "request_id": f"cli_{asked}", "request": request})
        while True:
            reply = json.loads(sys.stdin.readline())
            note({"message": reply})
            # Пока ждём ответа, хозяин может слать свои control-запросы (0.4: /mcp во время хода).
            if reply.get("type") == "control_request" and control(reply):
                continue
            return ((reply.get("response") or {}).get("response")) or {}

    def gate_turn(content) -> list:
        text = content if isinstance(content, str) else "\n".join(
            str(b.get("text") or "") for b in content or [] if isinstance(b, dict))
        calls = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("TOOL ")]
        ids = [f"toolu_{n}_{uuid.uuid4().hex[:6]}" for n in range(len(calls))]
        if calls:   # ответ модели с вызовами инструментов (их id сверяет хозяин)
            emit({"type": "assistant", "uuid": str(uuid.uuid4()), "session_id": session, "message": {
                "content": [{"type": "tool_use", "id": i, "name": c["name"], "input": c.get("input") or {}}
                            for i, c in zip(ids, calls)]}})
        out = []

        def result_of(tid, denied):
            emit({"type": "user", "session_id": session, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "is_error": denied, "content": "x"}]}})

        for tid, call in zip(ids, calls):
            before = len(out)
            gate_one(tid, call, out)
            result_of(tid, out[before]["result"] == "denied")
        return out

    def gate_one(tid, call, out):
        if True:
            if call.get("skip_hook"):   # дыра: CLI не позвал хук
                out.append({"name": call["name"], "result": "allowed", "by": "no-hook"})
                return
            hook = ask_host({"subtype": "hook_callback", "callback_id": hook_ids[0] if hook_ids else "?",
                             "tool_use_id": tid,
                             "input": {"hook_event_name": "PreToolUse", "tool_name": call["name"],
                                       "tool_use_id": tid, "tool_input": call.get("input") or {}}})
            spec = hook.get("hookSpecificOutput") or {}
            if spec.get("permissionDecision") == "deny":
                out.append({"name": call["name"], "result": "denied", "by": "hook",
                            "reason": spec.get("permissionDecisionReason")})
                return
            # Как CLI: «allow» хука — без вопроса `can_use_tool` (проверено на 2.1.293).
            if call.get("ask") and spec.get("permissionDecision") != "allow":
                perm = ask_host({"subtype": "can_use_tool", "tool_name": call["name"], "tool_use_id": tid,
                                 "input": call.get("input") or {}})
                if perm.get("behavior") != "allow":
                    out.append({"name": call["name"], "result": "denied", "by": "can_use_tool",
                                "reason": perm.get("message")})
                    return
            out.append({"name": call["name"], "result": "allowed", "hook": spec.get("permissionDecision") or "{}"})


    permission_mode = os.environ.get("FAKE_CLAUDE_PERMISSION_MODE") or _arg(argv, "--permission-mode") or "default"
    mcp_state = {}
    for item in os.environ.get("FAKE_CLAUDE_MCP", "").split(","):
        if item:
            name, _, status = item.partition(":")
            mcp_state[name] = status or "connected"

    def server_list() -> list:
        return [{"name": k, "status": v, **({"error": "Connection closed"} if v == "failed" else {}),
                 "scope": "user", "source": "user"} for k, v in mcp_state.items()]

    def answer(msg, body=None, error=None) -> None:
        if error:
            emit({"type": "control_response", "response": {
                "subtype": "error", "request_id": msg.get("request_id"), "error": error}})
        else:
            emit({"type": "control_response", "response": {
                "subtype": "success", "request_id": msg.get("request_id"), **({"response": body} if body is not None
                                                                              else {})}})

    def control(msg) -> bool:
        """Control-запросы 0.4 (режим gate); False — не наш подтип."""
        nonlocal permission_mode
        req = msg.get("request") or {}
        sub = req.get("subtype")
        if sub == "mcp_status":
            answer(msg, {"mcpServers": server_list()})
        elif sub == "mcp_reconnect":
            name = req.get("serverName")
            if os.environ.get("FAKE_CLAUDE_NO_RECONNECT") == "1":
                answer(msg, error="Unsupported control request subtype: mcp_reconnect")
            elif name not in mcp_state:
                answer(msg, error=f"Server not found: {name}")
            elif os.environ.get("FAKE_CLAUDE_RECONNECT_FAILS") == "1":
                answer(msg, error="Connection closed")
            else:
                mcp_state[name] = "connected"
                answer(msg)
        elif sub == "mcp_toggle":
            if req.get("serverName") not in mcp_state:
                answer(msg, error=f"Server not found: {req.get('serverName')}")
            else:
                mcp_state[req["serverName"]] = "connected" if req.get("enabled") else "disabled"
                answer(msg)
        elif sub == "set_model":
            answer(msg)
            if req.get("model") == "haiku" and permission_mode == "auto":
                permission_mode = "default"
                emit({"type": "system", "subtype": "status", "status": None, "permissionMode": permission_mode,
                      "session_id": session})
        elif sub == "set_permission_mode":
            permission_mode = req.get("mode")
            answer(msg, {"mode": permission_mode})
        elif sub == "interrupt":
            return False
        else:
            answer(msg, error=f"Unsupported control request subtype: {sub}")
        return True

    for raw in sys.stdin:
        msg = json.loads(raw)
        note({"message": msg})
        if msg.get("type") == "control_request" and (msg.get("request") or {}).get("subtype") == "initialize":
            for matchers in ((msg["request"].get("hooks") or {}).get("PreToolUse") or []):
                hook_ids.extend(matchers.get("hookCallbackIds") or [])
            if os.environ.get("FAKE_CLAUDE_NO_INIT") == "1":
                continue
            emit({"type": "control_response", "response": {
                "subtype": "success", "request_id": msg.get("request_id"), "response": {
                    "commands": [{"name": "review", "description": "Review a PR", "argumentHint": "[pr]"},
                                 {"name": "context", "description": "Context usage", "argumentHint": ""}],
                    "models": [{"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus"},
                               {"value": "haiku", "resolvedModel": "claude-haiku-4-5", "displayName": "Haiku"}],
                    "current_permission_mode": permission_mode}}})
            continue
        if msg.get("type") == "control_request" and mode == "gate" and control(msg):
            continue
        if msg.get("type") == "control_request":
            if mode == "deaf":
                continue
            ack = {"type": "control_response", "response": {
                "subtype": "success", "request_id": msg.get("request_id"),
                "response": {"still_queued": []}}}
            if pending and mode == "finish":
                delta(" и конец")
                assistant("начало ответа и конец")
                result(n, "начало ответа и конец")
                emit(ack)
                pending = False
                continue
            emit(ack)
            if pending:
                emit({"type": "user", "message": {"role": "user", "content": [
                    {"type": "text", "text": "[Request interrupted by user]"}]}, "session_id": session})
                result(n, None, error=True)
                pending = False
            continue
        if msg.get("type") == "control_response":
            continue  # ответ хозяина на наш can_use_tool (записан в журнал выше)
        n += 1
        if mode == "crash" and n == 2:
            sys.stderr.write("fatal: something broke\n")
            return 3
        if mode == "hang":
            continue
        if n == 1:
            wanted = _arg(argv, "--model")
            model = (os.environ.get("FAKE_CLAUDE_INIT_MODEL")
                     or (MODELS.get(wanted, wanted) if wanted else CLI_DEFAULT))
            servers = [{"name": x["name"], "status": x["status"]} for x in server_list()]
            emit({"type": "system", "subtype": "init", "session_id": session, "model": model,
                  **({"mcp_servers": servers, "permissionMode": permission_mode,
                      "slash_commands": ["review", "context", "compact", "mcp"]} if mode == "gate" else {})})
        content = (msg.get("message") or {}).get("content")
        if reject_images and isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "image" for b in content):
            result(n, None, error=True, errors=[
                "API Error: 400 messages.0.content.0.image.source.base64: Could not process image"])
            continue
        if mode in ("slow", "deaf", "finish"):
            delta("начало ответа")
            pending = True
            continue
        if mode == "gate" and content == "/compact":
            emit({"type": "system", "subtype": "status", "status": "compacting", "session_id": session})
            emit({"type": "system", "subtype": "compact_boundary", "session_id": session, "compact_metadata": {
                "trigger": "manual", "pre_tokens": 12345, "post_tokens": 900, "duration_ms": 5}})
            emit({"type": "user", "message": {"role": "user", "content": "summary"}, "session_id": session,
                  "isSynthetic": True})
            emit({"type": "user", "message": {"role": "user", "content":
                  "<local-command-stdout>Compacted </local-command-stdout>"}, "session_id": session, "isReplay": True})
            emit({"type": "result", "subtype": "success", "is_error": False, "result": "", "num_turns": 0,
                  "session_id": session, "usage": {"input_tokens": 0, "output_tokens": 0}})
            continue
        if mode == "gate" and content == "/fail":
            emit({"type": "user", "message": {"role": "user", "content":
                  "<local-command-stderr>Shell command permission check failed</local-command-stderr>"},
                  "session_id": session})
            emit({"type": "result", "subtype": "success", "is_error": False, "result": "", "num_turns": 0,
                  "session_id": session, "usage": {"input_tokens": 0, "output_tokens": 0}})
            continue
        if mode == "gate":
            text = json.dumps(gate_turn(content), ensure_ascii=False)
            delta(text)
            assistant(text)
            result(n, text)
            continue
        if mode == "tool":
            emit({"type": "control_request", "request_id": "cli_1", "request": {
                "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "dir"}}})
            reply = json.loads(sys.stdin.readline())
            note({"message": reply})
            emit({"type": "stream_event", "event": {"type": "message_start"}})
            delta("посмотрю заметки")
            emit({"type": "stream_event", "event": {"type": "message_start"}})
            delta("ответ")
            result(n, "ответ")
            continue
        text = '{"op":"none"}\n'
        for piece in (text[:7], text[7:]):
            delta(piece)
        assistant(text)
        result(n, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
