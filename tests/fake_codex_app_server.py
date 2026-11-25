"""Поддельный `codex app-server` (JSON-RPC построчно по stdio, без поля
"jsonrpc", как Codex 0.160.0) — для тестов `meet.llm.codex_app`. Модель не
зовёт.

Переменные окружения:
  FAKE_CODEX_LOG     — файл JSONL: первая строка `{"argv": [...], "pid": …}`, затем каждое
                       сообщение клиента как есть (`{"in": {...}}`);
  FAKE_CODEX_INIT    — `deaf` (на `initialize` не отвечает), `error`
                       (ошибка -32601), `exit` (сразу выходит с кодом 3);
  FAKE_CODEX_KNOWN   — id сеансов через запятую, которые `thread/resume`
                       продолжает (иначе — ошибка «no rollout found», как у
                       Codex); FAKE_CODEX_OTHER=1 — продолжает, но с другим id (THREAD);
  FAKE_CODEX_REVIEWER — что ответить в `approvalsReviewer` (по умолчанию —
                       то, что прислал клиент);
  FAKE_CODEX_TOOLS   — JSON `{сервер: {инструмент: readOnlyHint}}` для ответа на
                       `mcpServerStatus/list` (аннотации инструментов MCP);
  FAKE_CODEX_MODELS  — JSON: список страниц ответа `model/list` (каждая — список
                       записей `Model`); страница N+1 — по `cursor` "pN"; нет —
                       одна страница MODELS; `error` — ошибка -32601.

Ход (`turn/start`) — по строкам текста первого ввода:
  CMD <команда>      — `item/started` (commandExecution), запрос
                       `item/commandExecution/requestApproval`, ожидание
                       ответа, `serverRequest/resolved`, `item/completed`
                       (`completed` при accept*, иначе `declined`);
  PATCH <json>       — то же для `fileChange` (`changes` из json) и
                       `item/fileChange/requestApproval`;
  RUN <команда>      — команда без запроса: `item/started` и `item/completed`;
  ASK <метод>        — серверный запрос другого вида (`mcpServer/elicitation/request`…);
  MCP <сервер> <инструмент> <json> — вызов MCP: одобрение, как у Codex 0.160.0, —
                       `mcpServer/elicitation/request` с `_meta.codex_approval_kind =
                       "mcp_tool_call"`, затем `item/started` и `item/completed`
                       (`mcpToolCall`; `failed`, если одобрения нет);
  ELICIT <json>      — вопрос MCP-сервера пользователю (`mcpServer/elicitation/request`
                       с параметрами из json);
  HANG               — ждёт `turn/interrupt`, затем `turn/completed` со
                       статусом `interrupted`;
  FAIL <текст>       — `turn/completed` со статусом `failed`.
Ответ модели — `agentMessage`: JSON-список решений клиента по запросам хода
(или «ok», если запросов не было), затем `turn/completed`.
"""

import json
import os
import sys

THREAD = "11111111-2222-3333-4444-555555555555"


def log(entry) -> None:
    path = os.environ.get("FAKE_CODEX_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    msg = json.loads(line)
    log({"in": msg})
    return msg


class Server:
    def __init__(self) -> None:
        self.serial = 0
        self.interrupted = False
        self.backlog: list[dict] = []

    def answer_misc(self, msg: dict) -> None:
        """Запрос клиента посреди хода: остановка и статус MCP — ответ сразу."""
        if msg.get("method") == "turn/interrupt":
            self.interrupted = True
            send({"id": msg["id"], "result": {}})
        elif msg.get("method") == "mcpServerStatus/list":
            send({"id": msg["id"], "result": mcp_status(msg.get("params") or {})})

    def wait_response(self, rid: str) -> dict | None:
        while True:
            msg = read()
            if msg is None:
                return None
            if msg.get("id") == rid and "method" not in msg:
                return msg
            self.answer_misc(msg)

    def request(self, method: str, params: dict) -> dict | None:
        self.serial += 1
        rid = f"srv-{self.serial}"
        send({"id": rid, "method": method, "params": params})
        got = self.wait_response(rid)
        send({"method": "serverRequest/resolved", "params": {"threadId": THREAD, "requestId": rid}})
        return got

    def turn(self, msg: dict, thread: str) -> None:
        params = msg["params"]
        turn_id = "turn-1"
        send({"id": msg["id"], "result": {"turn": {"id": turn_id, "items": [], "itemsView": "notLoaded",
                                                   "status": "inProgress", "error": None, "startedAt": None,
                                                   "completedAt": None, "durationMs": None}}})
        text = "".join(i.get("text", "") for i in params["input"] if i.get("type") == "text")
        decisions = []
        status, error = "completed", None
        n = -1
        for line in text.splitlines():
            head, _, rest = line.partition(" ")
            if head not in ("CMD", "RUN", "PATCH", "ASK", "MCP", "ELICIT", "HANG", "FAIL"):
                continue
            n += 1
            iid = f"item-{n}"
            base = {"threadId": thread, "turnId": turn_id}
            if head in ("CMD", "RUN"):
                item = {"type": "commandExecution", "id": iid, "command": rest, "cwd": params.get("cwd") or "",
                        "processId": None, "source": "agent", "status": "inProgress", "commandActions": [],
                        "aggregatedOutput": None, "exitCode": None, "durationMs": None}
                send({"method": "item/started", "params": {**base, "item": item, "startedAtMs": 1}})
                ok = True
                if head == "CMD":
                    got = self.request("item/commandExecution/requestApproval",
                                       {**base, "itemId": iid, "startedAtMs": 1, "environmentId": None,
                                        "kind": "command", "command": rest, "cwd": params.get("cwd") or ""})
                    decision = (got or {}).get("result", {}).get("decision")
                    decisions.append(decision)
                    ok = decision in ("accept", "acceptForSession")
                done = {**item, "status": "completed" if ok else "declined",
                        "aggregatedOutput": "вывод" if ok else None, "exitCode": 0 if ok else None}
                send({"method": "item/completed", "params": {**base, "item": done, "completedAtMs": 2}})
            elif head == "PATCH":
                item = {"type": "fileChange", "id": iid, "changes": json.loads(rest), "status": "inProgress"}
                send({"method": "item/started", "params": {**base, "item": item, "startedAtMs": 1}})
                got = self.request("item/fileChange/requestApproval",
                                   {**base, "itemId": iid, "startedAtMs": 1, "reason": None, "grantRoot": None})
                decision = (got or {}).get("result", {}).get("decision")
                decisions.append(decision)
                ok = decision in ("accept", "acceptForSession")
                send({"method": "item/completed", "params": {
                    **base, "item": {**item, "status": "completed" if ok else "declined"}, "completedAtMs": 2}})
            elif head == "MCP":
                server, tool, args = rest.split(" ", 2)
                args = json.loads(args)
                got = self.request("mcpServer/elicitation/request", {
                    **base, "serverName": server, "mode": "form",
                    "message": f'Allow the {server} MCP server to run tool "{tool}"?',
                    "requestedSchema": {"type": "object", "properties": {}},
                    "_meta": {"codex_approval_kind": "mcp_tool_call", "persist": ["session", "always"],
                              "tool_name": tool, "tool_params": args}})
                action = (got or {}).get("result", {}).get("action")
                decisions.append(action)
                hint = read_only_hint(server, tool)
                item = {"type": "mcpToolCall", "id": iid, "server": server, "tool": tool, "arguments": args,
                        "status": "inProgress", "readOnlyHint": hint, "result": None, "error": None,
                        "durationMs": None}
                send({"method": "item/started", "params": {**base, "item": item, "startedAtMs": 1}})
                done = ({**item, "status": "completed", "result": {"content": [{"type": "text", "text": "ок"}]}}
                        if action == "accept" else
                        {**item, "status": "failed", "error": {"message": "user rejected MCP tool call"}})
                send({"method": "item/completed", "params": {**base, "item": done, "completedAtMs": 2}})
            elif head == "ELICIT":
                got = self.request("mcpServer/elicitation/request", {**base, **json.loads(rest)})
                decisions.append((got or {}).get("result"))
            elif head == "ASK":
                got = self.request(rest, {**base, "itemId": iid})
                decisions.append(got.get("result") if got and "result" in got else {"error": (got or {}).get("error")})
            elif head == "HANG":
                while not self.interrupted:
                    msg2 = read()
                    if msg2 is None:
                        return
                    self.answer_misc(msg2)
                status = "interrupted"
                break
            elif head == "FAIL":
                status, error = "failed", {"message": rest, "codexErrorInfo": None, "additionalDetails": None}
                break
        if status == "completed":
            reply = json.dumps(decisions, ensure_ascii=False) if decisions else "ok"
            send({"method": "item/completed", "params": {
                "threadId": thread, "turnId": turn_id, "completedAtMs": 3,
                "item": {"type": "agentMessage", "id": "msg-1", "text": reply, "phase": None,
                         "memoryCitation": None, "delivery": None, "questions": None}}})
        send({"method": "turn/completed", "params": {"threadId": thread, "turn": {
            "id": turn_id, "items": [], "itemsView": "notLoaded", "status": status, "error": error,
            "startedAt": None, "completedAt": None, "durationMs": None}}})


def _tools() -> dict:
    return json.loads(os.environ.get("FAKE_CODEX_TOOLS") or "{}")


def read_only_hint(server: str, tool: str):
    return _tools().get(server, {}).get(tool)


def mcp_status(params: dict) -> dict:
    data = []
    for server, tools in _tools().items():
        if params.get("serverName") not in (None, server):
            continue
        data.append({"name": server, "authStatus": "unsupported", "resources": [], "resourceTemplates": [],
                     "tools": {name: {"name": name, "inputSchema": {"type": "object"},
                                      "annotations": {"readOnlyHint": hint}} for name, hint in tools.items()}})
    return {"data": data, "nextCursor": None}


def thread_result(params: dict, tid: str) -> dict:
    reviewer = os.environ.get("FAKE_CODEX_REVIEWER") or params.get("approvalsReviewer") or "auto_review"
    mode = params.get("sandbox") or "read-only"
    sandbox = {"type": "readOnly", "networkAccess": False} if mode == "read-only" else {
        "type": "workspaceWrite", "writableRoots": [], "networkAccess": False,
        "excludeTmpdirEnvVar": False, "excludeSlashTmp": False}
    return {"thread": {"id": tid, "turns": []}, "model": "gpt-test", "modelProvider": "openai",
            "approvalPolicy": params.get("approvalPolicy") or "on-request", "approvalsReviewer": reviewer,
            "sandbox": sandbox, "cwd": params.get("cwd") or ""}


MODELS = [
    {"id": "gpt-6.1-terra", "model": "gpt-6.1-terra", "displayName": "GPT-6.1 Terra", "description": "Быстрая",
     "hidden": False, "isDefault": True, "defaultReasoningEffort": "low",
     "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "Fast"},
                                   {"reasoningEffort": "high", "description": "Deep"},
                                   {"reasoningEffort": "ultra", "description": "Deepest"}]},
    {"id": "gpt-reserve", "model": "gpt-reserve", "displayName": "Reserve", "description": "", "hidden": True,
     "isDefault": False, "defaultReasoningEffort": "medium",
     "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": ""}]},
]


def model_list(params: dict):
    raw = os.environ.get("FAKE_CODEX_MODELS")
    if raw == "error":
        return None
    pages = json.loads(raw) if raw else [MODELS]
    cursor = params.get("cursor")
    index = int(cursor[1:]) if isinstance(cursor, str) and cursor.startswith("p") else 0
    return {"data": pages[index], "nextCursor": f"p{index + 1}" if index + 1 < len(pages) else None}


def main() -> int:
    log({"argv": sys.argv[1:], "pid": os.getpid()})
    init = os.environ.get("FAKE_CODEX_INIT", "")
    if init == "exit":
        return 3
    server = Server()
    thread = None
    while True:
        msg = read()
        if msg is None:
            return 0
        method = msg.get("method")
        if "id" not in msg:
            continue      # уведомление клиента (initialized) или ответ не к месту
        if method == "initialize":
            if init == "deaf":
                continue
            if init == "error":
                send({"id": msg["id"], "error": {"code": -32601, "message": "method not found"}})
                continue
            send({"id": msg["id"], "result": {"userAgent": "fake/0.160.0", "codexHome": "C:/x",
                                              "platformFamily": "windows", "platformOs": "windows"}})
        elif method == "thread/start":
            thread = THREAD
            send({"id": msg["id"], "result": thread_result(msg["params"], thread)})
            send({"method": "thread/started", "params": {"thread": {"id": thread}}})
        elif method == "thread/resume":
            want = msg["params"]["threadId"]
            known = [x for x in os.environ.get("FAKE_CODEX_KNOWN", "").split(",") if x]
            if want not in known:
                send({"id": msg["id"], "error": {
                    "code": -32600, "message": f"thread/resume failed: no rollout found for thread id {want}"}})
                continue
            thread = THREAD if os.environ.get("FAKE_CODEX_OTHER") else want
            send({"id": msg["id"], "result": thread_result(msg["params"], thread)})
        elif method == "turn/start":
            server.turn(msg, thread or THREAD)
        elif method == "model/list":
            got = model_list(msg.get("params") or {})
            if got is None:
                send({"id": msg["id"], "error": {"code": -32601, "message": "unknown method model/list"}})
            else:
                send({"id": msg["id"], "result": got})
        elif method == "turn/interrupt":
            send({"id": msg["id"], "result": {}})
        else:
            send({"id": msg["id"], "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    sys.exit(main())
