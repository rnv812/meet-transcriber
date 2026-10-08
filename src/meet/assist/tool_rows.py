"""Ход работы ассистента в чате — «как в Claude CLI» (0.4, спец. §3).

Каждый вызов инструмента агента — строка ленты у его хода, по мере
выполнения. Источник — события `claude_stream` (`send(on_event=…)`:
`tool_use`, `gate`, `tool_result`, `hook`), у Codex и OpenCode — события их
вывода после вызова (`codex.tool_events`, `opencode.tool_events`), в том же
виде.

Запись журнала — `kind: "tool"`, `event: "call"` (запросы `read` / `search` /
`list` запасного пути — те же `kind: "tool"`, но `event: request|result`,
в ленте их нет). Поля строки:

* `tool_use_id` — id вызова (по нему патчи и карточка согласия, у которой
  тот же `tool_use_id`, встаёт в эту строку);
* `name`, `server`, `tool` — как у CLI (`mcp__сервер__инструмент` разобран);
* `view` — вид (`shell`, `read`, `search`, `edit`, `web`, `mcp`, `skill`,
  `agent`, `hook`, `other`), `label` — подпись («Bash», «Чтение», «Правка»,
  «MCP», «Навык», «Веб»…), `summary` — суть одной строкой (команда, путь,
  `сервер · инструмент` и краткие аргументы), у правки — `added`/`removed`
  (строк «+N −M»);
* `input_preview` — аргументы вызова, не длиннее INPUT_PREVIEW_MAX байт;
* `status` — `running` → `done` / `error` (с `error` — первой строкой
  ошибки) / `denied` (запрет ворот или отказ человека);
* `output_preview` — вывод, не длиннее OUTPUT_PREVIEW_MAX байт (`truncated`),
  `duration_ms`;
* `gate` — решение ворот Meet: `{decision, label}` («разрешено
  автоматически», «спросил вас · разрешено», «запрещено: причина»);
* `reply` — id реплики агента этого хода (окно группирует строки под ней),
  `parent` — вызов подагента, `t` — секунды встречи.

Вывод команд хранится только в журнале встречи (`chat.jsonl` — журнал
пользователя, как и сами сообщения); в журнал процесса из него не пишется
ничего (CLAUDE.md). Секреты из вывода автоматически не вырезаются.

`ToolRows.apply` — блокирующий (журнал): из цикла событий — через поток
журнала участника, по порядку событий.
"""

import json

INPUT_PREVIEW_MAX = 2 * 1024        # байт UTF-8
OUTPUT_PREVIEW_MAX = 64 * 1024      # байт UTF-8
SUMMARY_MAX = 300                   # символов краткой строки
ERROR_LINE_MAX = 300
_MCP_ARGS_MAX = 80

CALL = "call"
RUNNING, DONE, ERROR, DENIED = "running", "done", "error", "denied"

_READ = {"Read": "Чтение", "NotebookRead": "Чтение", "LS": "Список"}
_SEARCH = {"Grep": "Поиск", "Glob": "Поиск файлов"}
_EDIT = ("Edit", "MultiEdit", "Write", "NotebookEdit", "apply_patch")
_SHELL = ("Bash", "PowerShell")

GATE_LABELS = {"auto": "разрешено автоматически", "allowed": "разрешено автоматически"}


def _one_line(text, limit: int = SUMMARY_MAX) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:max(limit - 1, 0)].rstrip() + "…"


def _cut_bytes(text: str, limit: int) -> tuple[str, bool]:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text, False
    return data[:limit].decode("utf-8", errors="ignore"), True


def _lines(text) -> int:
    text = str(text or "")
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _brief_args(data: dict) -> str:
    """Краткие аргументы MCP: «ключ: значение» первых простых полей."""
    parts = []
    for key, value in (data or {}).items():
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            parts.append(f"{key}: {_one_line(value, 40)}")
        if sum(len(p) for p in parts) >= _MCP_ARGS_MAX:
            break
    return _one_line(", ".join(parts), _MCP_ARGS_MAX)


def describe(name: str, data=None, *, server: str | None = None, tool: str | None = None) -> dict:
    """Вид вызова для строки ленты: `view`, `label`, `summary` (и у правки —
    `added`/`removed`)."""
    data = data if isinstance(data, dict) else {}
    name = str(name or "")
    if name in _SHELL:
        return {"view": "shell", "label": name, "summary": _one_line(data.get("command"))}
    if name in _READ:
        path = data.get("file_path") or data.get("notebook_path") or data.get("path") or ""
        return {"view": "read", "label": _READ[name], "summary": _one_line(path)}
    if name in _SEARCH:
        what = data.get("pattern") or data.get("query") or ""
        where = data.get("path") or ""
        return {"view": "search", "label": _SEARCH[name],
                "summary": _one_line(f"{what} — {where}" if where else what)}
    if name in _EDIT:
        return {"view": "edit", "label": "Правка", **_edit_summary(name, data)}
    if name == "WebFetch":
        return {"view": "web", "label": "Веб", "summary": _one_line(data.get("url"))}
    if name == "WebSearch":
        return {"view": "web", "label": "Веб-поиск", "summary": _one_line(data.get("query"))}
    if name.startswith("mcp__") or server:
        if not server:
            from meet.llm.claude_stream import mcp_name

            server, tool = mcp_name(name)
        args = _brief_args(data)
        head = f"{server} · {tool or name}"
        return {"view": "mcp", "label": "MCP", "summary": _one_line(f"{head}  {args}" if args else head)}
    if name == "Skill":
        what = data.get("skill") or data.get("command") or data.get("name") or ""
        return {"view": "skill", "label": "Навык", "summary": _one_line(what)}
    if name in ("Task", "Agent"):
        return {"view": "agent", "label": "Подагент",
                "summary": _one_line(data.get("description") or data.get("prompt") or "")}
    return {"view": "other", "label": name or "инструмент", "summary": _brief_args(data)}


def _edit_summary(name: str, data: dict) -> dict:
    if name == "apply_patch":
        paths = [str(c.get("path")) for c in data.get("changes") or () if isinstance(c, dict) and c.get("path")]
        head = paths[0] if paths else ""
        more = f" (+ ещё {len(paths) - 1})" if len(paths) > 1 else ""
        return {"summary": _one_line(head + more)}
    path = data.get("file_path") or data.get("notebook_path") or ""
    if name == "Write":
        added, removed = _lines(data.get("content")), 0
    elif name == "MultiEdit":
        edits = [e for e in data.get("edits") or () if isinstance(e, dict)]
        added = sum(_lines(e.get("new_string")) for e in edits)
        removed = sum(_lines(e.get("old_string")) for e in edits)
    elif name == "NotebookEdit":
        added, removed = _lines(data.get("new_source")), 0
    else:
        added, removed = _lines(data.get("new_string")), _lines(data.get("old_string"))
    return {"summary": _one_line(path), "added": added, "removed": removed}


def input_preview(name: str, data) -> str:
    """Аргументы вызова для раскрытой строки: команда — как есть, прочее —
    JSON; не длиннее INPUT_PREVIEW_MAX байт (обрезано — «…»)."""
    data = data if isinstance(data, dict) else {}
    if name in _SHELL and isinstance(data.get("command"), str):
        text = data["command"]
    else:
        try:
            text = json.dumps(data, ensure_ascii=False, indent=1)
        except (TypeError, ValueError):
            text = str(data)
    cut, over = _cut_bytes(text, INPUT_PREVIEW_MAX - len("…".encode("utf-8")))
    return cut + "…" if over else text


def output_preview(text) -> tuple[str, bool]:
    """Вывод вызова → (не длиннее OUTPUT_PREVIEW_MAX байт, обрезан ли)."""
    return _cut_bytes(str(text or ""), OUTPUT_PREVIEW_MAX)


def gate_view(event: dict) -> dict:
    """Решение ворот (`claude_stream._gate_event`) → `{decision, label}`."""
    decision = str(event.get("decision") or "")
    if decision in GATE_LABELS:
        label = GATE_LABELS[decision]
    elif decision == "approved":
        label = "спросил вас · до конца встречи" if event.get("grant") else "спросил вас · разрешено"
    elif decision == "declined":
        label = "спросил вас · " + ("нет ответа" if event.get("why") == "timeout" else "отклонено")
    else:
        from meet.llm.consent import why_label

        why = why_label(str(event.get("why") or ""))
        label = f"запрещено: {why}" if why else "запрещено"
    return {"decision": decision, "label": label}


def _first_line(text) -> str:
    for line in str(text or "").splitlines():
        if line.strip():
            return _one_line(line, ERROR_LINE_MAX)
    return ""


class ToolRows:
    """Строки вызовов одной сессии: id вызова → id записи журнала. Работает в
    потоке журнала (по одному вызову, по порядку событий)."""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}
        self._status: dict[str, str] = {}

    def row_id(self, tool_use_id) -> str | None:
        return self._ids.get(str(tool_use_id)) if tool_use_id else None

    def apply(self, chatlog, event: dict, *, reply: str | None = None, t: float | None = None) -> list[dict]:
        """Событие хода работы → события журнала (для SSE `chat`)."""
        kind = event.get("type") if isinstance(event, dict) else None
        if kind == "tool_use":
            return self._use(chatlog, event, reply, t)
        if kind == "gate":
            return self._gate(chatlog, event)
        if kind == "tool_result":
            return self._result(chatlog, event)
        if kind == "hook":
            return self._hook(chatlog, event, reply, t)
        return []

    def _use(self, chatlog, event, reply, t) -> list[dict]:
        tid = str(event.get("id") or "")
        if not tid or tid in self._ids:
            return []
        name = str(event.get("name") or "")
        data = event.get("input") if isinstance(event.get("input"), dict) else {}
        fields = {"event": CALL, "tool_use_id": tid, "name": name, "server": event.get("server"),
                  "tool": event.get("tool") or name,
                  **describe(name, data, server=event.get("server"), tool=event.get("tool")),
                  "input_preview": input_preview(name, data), "status": RUNNING}
        if reply:
            fields["reply"] = reply
        if event.get("parent"):
            fields["parent"] = str(event["parent"])
        if t is not None:
            fields["t"] = t
        added = chatlog.append("tool", **fields)
        self._ids[tid] = added.message["id"]
        self._status[tid] = RUNNING
        return [added.event] if added.event else []

    def _patch(self, chatlog, tid: str, changes: dict) -> list[dict]:
        mid = self._ids.get(tid)
        if mid is None:
            return []
        ev = chatlog.patch(mid, changes)
        if "status" in changes:
            self._status[tid] = changes["status"]
        return [ev] if ev else []

    def _gate(self, chatlog, event) -> list[dict]:
        tid = str(event.get("id") or "")
        if not tid:
            return []
        changes: dict = {"gate": gate_view(event)}
        if event.get("decision") in ("denied", "declined"):
            changes["status"] = DENIED
        return self._patch(chatlog, tid, changes)

    def _result(self, chatlog, event) -> list[dict]:
        tid = str(event.get("id") or "")
        if tid not in self._ids:
            return []
        output, cut = output_preview(event.get("output"))
        changes: dict = {"output_preview": output, "truncated": bool(cut or event.get("truncated"))}
        if isinstance(event.get("duration_ms"), (int, float)):
            changes["duration_ms"] = int(event["duration_ms"])
        if self._status.get(tid) != DENIED:
            ok = bool(event.get("ok"))
            changes["status"] = DONE if ok else ERROR
            if not ok:
                changes["error"] = _first_line(output) or "ошибка"
        return self._patch(chatlog, tid, changes)

    def _hook(self, chatlog, event, reply, t) -> list[dict]:
        hid = f"hook:{event.get('hook_id') or ''}"
        if hid == "hook:":
            return []
        status = event.get("status")
        if hid not in self._ids:
            fields = {"event": CALL, "tool_use_id": hid, "name": "hook", "server": None, "tool": "hook",
                      "view": "hook", "label": "Хук", "summary": _one_line(event.get("name") or event.get("event")),
                      "input_preview": "", "status": RUNNING}
            if reply:
                fields["reply"] = reply
            if t is not None:
                fields["t"] = t
            added = chatlog.append("tool", **fields)
            self._ids[hid] = added.message["id"]
            self._status[hid] = RUNNING
            out = [added.event] if added.event else []
        else:
            out = []
        if status == "done":
            ok = event.get("outcome") in (None, "success") and event.get("exit_code") in (None, 0)
            output, cut = output_preview(event.get("output"))
            changes = {"status": DONE if ok else ERROR, "output_preview": output, "truncated": cut}
            if not ok:
                changes["error"] = _first_line(output) or str(event.get("outcome") or "ошибка")
            out += self._patch(chatlog, hid, changes)
        return out
