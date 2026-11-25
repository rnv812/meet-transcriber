"""Поддельный `agy acp` (Agent Client Protocol: JSON-RPC 2.0 построчно по
stdio, как Antigravity CLI 1.5.11) — для тестов `meet.llm.antigravity`.
Модель не зовёт.

Переменные окружения:
  FAKE_AGY_LOG    — файл JSONL: первая строка `{"argv": [...], "pid": …,
                    "settings": <разовые настройки или null>}`, затем каждое
                    сообщение клиента как есть (`{"in": {...}}`);
  FAKE_AGY_INIT   — `deaf` (на `initialize` не отвечает), `error` (-32601),
                    `exit` (сразу выходит с кодом 3);
  FAKE_AGY_AUTH=1 — сеанс без входа: `session/new`/`load`/`resume` →
                    -32000 «Authentication required», как у agy без входа;
  FAKE_AGY_KNOWN  — id сеансов через запятую, которые продолжаются
                    (`session/resume`, `session/load`); иначе — ошибка;
  FAKE_AGY_OTHER=1 — продолжает, но отвечает другим id (OTHER_SID);
  FAKE_AGY_MODE   — текущий режим нового сеанса (по умолчанию `default`);
  FAKE_AGY_MODELS — что сообщить о моделях в ответе `session/new` (и
                    `session/set_model`): `acp` — поле `models` (ACP
                    `currentModelId`/`availableModels`), `config` —
                    `configOptions` (категории `model`, `thought_level`, `mode`,
                    значения модели — группами), `both` — оба, `nothought` —
                    `configOptions` без уровня; пусто — ничего;
  FAKE_AGY_SETCFG=error — `session/set_config_option` отвечает ошибкой.

Ход (`session/prompt`) — по строкам текста первого блока:
  EXEC <команда>  — `tool_call` (kind execute) и `session/request_permission`;
  READ <путь>     — то же для чтения (kind read, locations);
  EDIT <путь>     — то же для правки (kind edit);
  MCP <сервер> <инструмент> <json> [ro] — вызов MCP (title `mcp(сервер/инструмент)`,
                    `_meta.readOnlyHint` — если `ro`);
  RAW <json>      — запрос разрешения с этим toolCall как есть;
  HANG            — ждёт `session/cancel`, затем ответ `cancelled`;
  FAIL <текст>    — ошибка ответа на `session/prompt`;
  SAY <текст>     — текст модели двумя `agent_message_chunk` (по ходу хода);
  THINK           — `agent_thought_chunk`;
  SELFCANCEL      — ответ `stopReason: cancelled` без `session/cancel`;
  EMPTY           — ход без текста модели.
Ответ модели — `agent_message_chunk`: JSON-список выбранных optionId (или
`cancelled`) по запросам хода, «ok» — если запросов не было (с SAY или
EMPTY — без него); затем ответ `session/prompt` со `stopReason: end_turn`.
"""

import json
import os
import sys

SID = "a1b2c3d4-0000-4000-8000-000000000001"
OTHER_SID = "a1b2c3d4-0000-4000-8000-0000000000ff"
OPTIONS = [{"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
           {"optionId": "allow-always", "name": "Always allow", "kind": "allow_always"},
           {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"}]


def log(entry) -> None:
    path = os.environ.get("FAKE_AGY_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    msg = json.loads(line)
    log({"in": msg})
    return msg


def _settings(argv: list[str]):
    """Разовые настройки, которые agy прочёл бы: `<gemini-dir>/antigravity-cli/<cli_dir_name>/settings.json`."""
    try:
        home = argv[argv.index("--gemini-dir") + 1]
        run = argv[argv.index("--cli_dir_name") + 1]
    except (ValueError, IndexError):
        return None
    path = os.path.join(home, "antigravity-cli", run, "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


MODELS = {"currentModelId": "gemini-3-pro",
          "availableModels": [{"modelId": "gemini-3-pro", "name": "Gemini 3 Pro", "description": "умная"},
                              {"modelId": "gemini-3-flash", "name": "Gemini 3 Flash"},
                              {"modelId": "bad name&", "name": "негодная"}]}
THOUGHT = {"id": "thinking_level", "name": "Thinking", "category": "thought_level", "type": "select",
           "currentValue": "high",
           "options": [{"value": "low", "name": "Low"}, {"value": "high", "name": "High", "description": "дольше"}]}


def config_options(models: str) -> list:
    out = [{"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": "gemini-3-pro",
            "options": [{"group": "google", "name": "Google", "options": [
                {"value": "gemini-3-pro", "name": "Gemini 3 Pro"},
                {"value": "gemini-3-flash", "name": "Gemini 3 Flash", "description": "быстрая"}]},
                        {"value": "claude-x", "name": "Claude X"}]},
           {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": "default",
            "options": [{"value": "default", "name": "Default"}]}]
    if models != "nothought":
        out.append(THOUGHT)
    return out


def model_fields(models: str) -> dict:
    out = {}
    if models in ("acp", "both"):
        out["models"] = MODELS
    if models in ("config", "both", "nothought"):
        out["configOptions"] = config_options(models)
    return out


class Server:
    def __init__(self) -> None:
        self.serial = 0
        self.cancelled = False
        self.sid = SID

    def update(self, what: str, fields: dict | None = None, **more) -> None:
        send({"method": "session/update", "params": {"sessionId": self.sid,
                                                     "update": {"sessionUpdate": what, **(fields or {}), **more}}})

    def wait_response(self, rid: str) -> dict | None:
        while True:
            msg = read()
            if msg is None:
                return None
            if msg.get("id") == rid and "method" not in msg:
                return msg
            if msg.get("method") == "session/cancel":
                self.cancelled = True

    def permission(self, call: dict) -> str:
        self.serial += 1
        rid = f"srv-{self.serial}"
        send({"id": rid, "method": "session/request_permission",
              "params": {"sessionId": self.sid, "toolCall": call, "options": OPTIONS}})
        got = self.wait_response(rid) or {}
        outcome = (got.get("result") or {}).get("outcome") or {}
        if outcome.get("outcome") == "selected":
            return str(outcome.get("optionId"))
        return "cancelled"

    def prompt(self, msg: dict) -> None:
        params = msg["params"]
        text = "".join(b.get("text", "") for b in params.get("prompt") or () if b.get("type") == "text")
        decisions = []
        n = -1
        quiet = False
        for line in text.splitlines():
            head, _, rest = line.partition(" ")
            if head == "SAY":
                half = len(rest) // 2
                for piece in (rest[:half], rest[half:]):
                    self.update("agent_message_chunk", content={"type": "text", "text": piece})
                quiet = True
                continue
            if head == "THINK":
                self.update("agent_thought_chunk", content={"type": "text", "text": "думаю"})
                continue
            if head == "SELFCANCEL":
                send({"id": msg["id"], "result": {"stopReason": "cancelled"}})
                return
            if head == "EMPTY":
                quiet = True
                continue
            if head not in ("EXEC", "READ", "EDIT", "MCP", "RAW", "HANG", "FAIL"):
                continue
            n += 1
            cid = f"call-{n}"
            if head == "FAIL":
                send({"id": msg["id"], "error": {"code": -32603, "message": rest}})
                return
            if head == "HANG":
                while not self.cancelled:
                    got = read()
                    if got is None:
                        return
                    if got.get("method") == "session/cancel":
                        self.cancelled = True
                send({"id": msg["id"], "result": {"stopReason": "cancelled"}})
                return
            if head == "EXEC":
                call = {"toolCallId": cid, "title": rest, "kind": "execute", "status": "pending",
                        "rawInput": {"command": rest}}
            elif head == "READ":
                call = {"toolCallId": cid, "title": f"Read {rest}", "kind": "read", "status": "pending",
                        "locations": [{"path": rest}], "rawInput": {"path": rest}}
            elif head == "EDIT":
                call = {"toolCallId": cid, "title": f"Edit {rest}", "kind": "edit", "status": "pending",
                        "locations": [{"path": rest}],
                        "content": [{"type": "diff", "path": rest, "oldText": "", "newText": "новое"}]}
            elif head == "MCP":
                server, tool, args = rest.split(" ", 2)
                ro = args.endswith(" ro")
                args = json.loads(args[:-3] if ro else args)
                call = {"toolCallId": cid, "title": f"mcp({server}/{tool})", "kind": "other",
                        "status": "pending", "rawInput": args}
                if ro:
                    call["_meta"] = {"readOnlyHint": True}
            else:
                call = json.loads(rest)
                cid = call.get("toolCallId") or cid
            self.update("tool_call", call)
            decision = self.permission(call)
            decisions.append(decision)
            ok = decision.startswith("allow")
            self.update("tool_call_update", toolCallId=cid, status="completed" if ok else "failed",
                        content=[{"type": "content", "content": {"type": "text",
                                                                 "text": "вывод" if ok else "отклонено"}}])
        if not quiet:
            answer = json.dumps(decisions, ensure_ascii=False) if decisions else "ok"
            self.update("agent_message_chunk", content={"type": "text", "text": answer})
        send({"id": msg["id"], "result": {"stopReason": "end_turn"}})


def main() -> int:
    log({"argv": sys.argv[1:], "pid": os.getpid(), "settings": _settings(sys.argv[1:])})
    init = os.environ.get("FAKE_AGY_INIT", "")
    if init == "exit":
        return 3
    server = Server()
    known = {s for s in os.environ.get("FAKE_AGY_KNOWN", "").split(",") if s}
    auth = os.environ.get("FAKE_AGY_AUTH") == "1"
    mode = os.environ.get("FAKE_AGY_MODE", "default")
    modes = {"currentModeId": mode, "availableModes": [{"id": m, "name": m} for m in
                                                        ("default", "accept-edits", "plan", "auto")]}
    while True:
        msg = read()
        if msg is None:
            return 0
        method = msg.get("method")
        if "id" not in msg:
            if method == "session/cancel":
                server.cancelled = True
            continue
        if method == "initialize":
            if init == "deaf":
                continue
            if init == "error":
                send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})
                continue
            send({"id": msg["id"], "result": {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": True},
                                      "sessionCapabilities": {"resume": {}, "close": {}, "load": {},
                                                              "additionalDirectories": {}}},
                "agentInfo": {"name": "Antigravity ACP Server", "version": "1.5.11"},
                "authMethods": [{"id": "oauth-personal", "name": "Log in with Google"}]}})
        elif method in ("session/new", "session/load", "session/resume"):
            if auth:
                send({"id": msg["id"], "error": {"code": -32000, "message": "Authentication required"}})
                continue
            if method == "session/new":
                server.sid = SID
                send({"id": msg["id"], "result": {"sessionId": SID, "modes": modes,
                                                  **model_fields(os.environ.get("FAKE_AGY_MODELS", ""))}})
                continue
            wanted = (msg.get("params") or {}).get("sessionId")
            if wanted not in known:
                send({"id": msg["id"], "error": {
                    "code": -32603, "message": f"Failed to read session {wanted}; retry, or load it with session/load"}})
                continue
            server.sid = OTHER_SID if os.environ.get("FAKE_AGY_OTHER") == "1" else wanted
            send({"id": msg["id"], "result": {"sessionId": server.sid,
                                              "modes": {**modes, "currentModeId": "default"}}})
        elif method == "session/set_model":
            fields = model_fields(os.environ.get("FAKE_AGY_MODELS", ""))
            send({"id": msg["id"], "result": {k: v for k, v in fields.items() if k == "configOptions"}})
        elif method == "session/set_config_option":
            if os.environ.get("FAKE_AGY_SETCFG") == "error":
                send({"id": msg["id"], "error": {"code": -32602, "message": "Invalid value"}})
            else:
                send({"id": msg["id"], "result": {"configOptions": config_options(
                    os.environ.get("FAKE_AGY_MODELS", ""))}})
        elif method in ("session/set_mode", "session/close"):
            send({"id": msg["id"], "result": {}})
        elif method == "session/prompt":
            server.prompt(msg)
        else:
            send({"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}})


if __name__ == "__main__":
    sys.exit(main())
