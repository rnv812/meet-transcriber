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
           `mcp_servers` из FAKE_CLAUDE_MCP (имена через запятую).
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
        reply = json.loads(sys.stdin.readline())
        note({"message": reply})
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
            if call.get("ask"):
                perm = ask_host({"subtype": "can_use_tool", "tool_name": call["name"], "tool_use_id": tid,
                                 "input": call.get("input") or {}})
                if perm.get("behavior") != "allow":
                    out.append({"name": call["name"], "result": "denied", "by": "can_use_tool",
                                "reason": perm.get("message")})
                    return
            out.append({"name": call["name"], "result": "allowed"})


    for raw in sys.stdin:
        msg = json.loads(raw)
        note({"message": msg})
        if msg.get("type") == "control_request" and (msg.get("request") or {}).get("subtype") == "initialize":
            for matchers in ((msg["request"].get("hooks") or {}).get("PreToolUse") or []):
                hook_ids.extend(matchers.get("hookCallbackIds") or [])
            if os.environ.get("FAKE_CLAUDE_NO_INIT") == "1":
                continue
            emit({"type": "control_response", "response": {
                "subtype": "success", "request_id": msg.get("request_id"), "response": {"commands": []}}})
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
            servers = [{"name": x, "status": "connected"}
                       for x in os.environ.get("FAKE_CLAUDE_MCP", "").split(",") if x]
            emit({"type": "system", "subtype": "init", "session_id": session, "model": model,
                  **({"mcp_servers": servers} if mode == "gate" else {})})
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
