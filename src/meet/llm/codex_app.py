"""Codex через `codex app-server` (0.6.2): ход агента-участника, в котором
Codex спрашивает человека через Meet.

`codex exec` запросы одобрения сам отклоняет (он — клиент app-server внутри
своего процесса), поэтому ход с вопросами идёт через `codex app-server`:
JSON-RPC 2.0 построчно по stdio, без поля "jsonrpc" (Codex 0.160.0; поля
сверены со схемой `codex app-server generate-json-schema`, выдержка — в
тестах). Процесс — на один ход, как у `codex exec`:

1. `initialize` → уведомление `initialized`;
2. `thread/start` (новый сеанс; системный промпт — в начале первого
   сообщения, как у exec) или `thread/resume` с id из `sessions.json` (те же
   сохранённые сеансы, что у exec);
3. `turn/start` с политикой по уровню хода (`turn_policy`): NONE —
   песочница только-чтение без сети и без вопросов (`never`); READ (❓) —
   только-чтение, но `on-request` (через одобрения идут вызовы MCP, ворота
   в этом ходе пропускают только чтение, без карточек); USER —
   `on-request`, проверяющий — человек (`approvalsReviewer: "user"`, без
   автопроверки Codex), песочница в режиме `auto` — запись в рабочие папки
   (`workspaceWrite`), в режиме `confirm` — только чтение: всё сверх чтения
   Codex просит одобрить;
4. запросы `item/commandExecution/requestApproval` и
   `item/fileChange/requestApproval` → `approver` (ворота Meet и карточки,
   `consent.codex_approval`) → `accept` / `acceptForSession` / `decline`;
   `mcpServer/elicitation/request` (одобрение вызова MCP или вопрос
   MCP-сервера) → те же ворота (`consent.codex_elicitation`; пометка
   инструмента `readOnlyHint` — из каталога `mcpServerStatus/list`) →
   `accept` / `decline`; прочие запросы сервера — отказ (вопросы
   пользователю, расширение прав);
5. `item/started` / `item/completed` → строки хода работы по мере хода
   (`codex.ToolStream`), решение ворот — событием `gate` в строку вызова;
6. ответ — текст последнего `agentMessage` (по ходу — дельты
   `item/agentMessage/delta` в `on_text`), конец — `turn/completed`.

«Стоп» и таймаут — `turn/interrupt`, через INTERRUPT_GRACE_S — гашение дерева
процессов. Пока ход не начался, любой сбой (процесс не запустился, нет
ответа на `initialize`, ошибка протокола, проверяющий не человек) —
`Unavailable`: вызывающий идёт прежним путём `codex exec`.

MCP-серверы пользователя (`launch_flags`): в ходе по репликам выключены, как
у exec, — заглушки `-c mcp_servers.<имя>=…` при запуске; в ходе по просьбе
(USER, ❓) включены с режимом «спрашивать каждый вызов»
(`default_tools_approval_mode="prompt"`, `codex.mcp_prompt_args`), так что
каждый вызов MCP проходит ворота Meet; сервер, которому режим ключом не
задать, — заглушкой. Выключить нельзя — None, ход через exec с
`--ignore-user-config`. В журналы — ничего из хода."""

import json
import os
import subprocess
import threading
import time

from meet.llm import codex as _codex
from meet.llm.base import (
    CANCELLED_ERROR, EMPTY_ERROR, TIMEOUT_ERROR, AgentReply, kill_tree, popen_kwargs, resume_failure,
)

INIT_TIMEOUT_S = 20.0        # ответ на initialize
REQUEST_TIMEOUT_S = 60.0     # ответы thread/start, thread/resume, turn/start
INTERRUPT_GRACE_S = 3.0      # после turn/interrupt — и дерево процессов гасится
CLOSE_GRACE_S = 1.0          # после хода процесс сам выходит по закрытому stdin
_POLL_S = 0.1
_ERR_LIMIT = 500
_STDERR_TAIL = 64 * 1024
# Ошибки JSON-RPC, которые значат «протокол не тот»: разбор, нет метода, не те параметры.
_PROTOCOL = (-32700, -32601, -32602)
HINTS_TIMEOUT_S = 10.0       # каталог инструментов MCP (`mcpServerStatus/list`) для readOnlyHint
COMMAND_APPROVAL = "item/commandExecution/requestApproval"
FILE_APPROVAL = "item/fileChange/requestApproval"
ELICITATION = "mcpServer/elicitation/request"
# Тип элемента app-server → тип события `codex exec --json` (`codex.ToolStream`).
_ITEM_TYPES = {"commandExecution": "command_execution", "fileChange": "file_change",
               "mcpToolCall": "mcp_tool_call", "webSearch": "web_search"}


class Unavailable(Exception):
    """app-server для этого хода не годится (ход ещё не начался): `reason` —
    коротко, без текста встречи."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _RpcError(Exception):
    def __init__(self, code, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _NoAnswer(Exception):
    """Ответа нет: срок вышел или процесс закрылся."""


def command(exe: str, flags) -> list[str]:
    """Командная строка сервера (тесты подменяют на поддельный)."""
    return [exe, "app-server", *flags]


def launch_flags(access: str, servers, config=None) -> list[str] | None:
    """Флаги запуска. MCP-серверы пользователя: в ходе по репликам (NONE) —
    выключены заглушками (`codex.mcp_stub_args`), в ходе по просьбе (USER,
    ❓ — READ) — включены, и Codex спрашивает о каждом вызове
    (`codex.mcp_prompt_args`, `config` — `codex.mcp_config()`); не
    выключить — None. Приложения, браузер и управление компьютером выключены
    всегда; в ходах NONE и READ — без веб-поиска."""
    stubs = (_codex.mcp_prompt_args(servers, config) if access in ("user", "read")
             else _codex.mcp_stub_args(servers))
    if stubs is None:
        return None
    out = list(stubs)
    for feature in _codex._NONE_FEATURES:
        out += ["--disable", feature]
    if access != "user":
        out += ["-c", "tools.web_search=false"]
    return out


def turn_policy(access: str, mode: str, work_dirs=()) -> dict:
    """Песочница и одобрения хода по уровню (`access`) и режиму ассистента
    (`mode`): ключи `approvalPolicy`, `approvalsReviewer`, `sandbox` (режим
    для `thread/*`), `sandboxPolicy` (для `turn/start`). ❓ (READ) —
    только-чтение, но с одобрениями: через них идут вызовы MCP, а ворота
    Meet в этом ходе пропускают только чтение, без карточек. `yolo` (режим
    YOLO встречи, `consent.MODE_YOLO`) — ход без песочницы (`dangerFullAccess`:
    любые файлы и сеть), редкие одобрения ворота принимают сами."""
    read_only = {"type": "readOnly", "networkAccess": False}
    if mode == "yolo":
        return {"approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": "danger-full-access",
                "sandboxPolicy": {"type": "dangerFullAccess"}}
    if access == "read":
        return {"approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": "read-only",
                "sandboxPolicy": read_only}
    if access != "user":
        return {"approvalPolicy": "never", "approvalsReviewer": "user", "sandbox": "read-only",
                "sandboxPolicy": read_only}
    if mode == "confirm":
        return {"approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": "read-only",
                "sandboxPolicy": read_only}
    roots = [os.path.abspath(str(d)) for d in work_dirs or () if d and os.path.isdir(str(d))]
    return {"approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": "workspace-write",
            "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": roots, "networkAccess": False,
                              "excludeTmpdirEnvVar": False, "excludeSlashTmp": False}}


def _client_version() -> str:
    try:
        from importlib.metadata import version

        return version("meet-transcriber")
    except Exception:
        return "0"


class _Peer:
    """Процесс app-server: запись построчно под замком, чтение stdout в своём
    потоке (ответы — ждущим запросам, уведомления — `on_notify` по порядку,
    запросы сервера — `on_request` каждый в своём потоке: ответ на них может
    ждать человека), от stderr — хвост."""

    def __init__(self, proc, *, on_notify, on_request) -> None:
        self.proc = proc
        self._on_notify = on_notify
        self._on_request = on_request
        self._wlock = threading.Lock()
        self._lock = threading.Lock()
        self._next = 0
        self._waiting: dict[int, list] = {}
        self._err: list[str] = []
        self.closed = threading.Event()
        self._threads = [threading.Thread(target=fn, name=f"codex-app-{fn.__name__}", daemon=True)
                         for fn in (self._read_out, self._read_err)]
        for t in self._threads:
            t.start()

    def write(self, msg: dict) -> bool:
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._wlock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def _slot(self) -> tuple[int, list]:
        with self._lock:
            self._next += 1
            slot = [threading.Event(), None]
            self._waiting[self._next] = slot
            return self._next, slot

    def call(self, method: str, params: dict, timeout: float) -> dict:
        rid, slot = self._slot()
        if not self.write({"id": rid, "method": method, "params": params}) or not slot[0].wait(max(timeout, 0)):
            with self._lock:
                self._waiting.pop(rid, None)
            raise _NoAnswer(method)
        msg = slot[1]
        if msg is None:
            raise _NoAnswer(method)
        if "error" in msg:
            err = msg["error"] if isinstance(msg["error"], dict) else {}
            raise _RpcError(err.get("code"), str(err.get("message") or ""))
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    def send_request(self, method: str, params: dict) -> None:
        """Запрос без ожидания ответа (`turn/interrupt` из «Стоп»)."""
        rid, _slot = self._slot()
        self.write({"id": rid, "method": method, "params": params})

    def notify(self, method: str) -> None:
        self.write({"method": method})

    def respond(self, rid, result: dict | None = None, error: dict | None = None) -> None:
        self.write({"id": rid, "error": error} if error is not None else {"id": rid, "result": result or {}})

    def _read_out(self) -> None:
        try:
            for raw in self.proc.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    threading.Thread(target=self._on_request, args=(msg,), name="codex-app-request",
                                     daemon=True).start()
                elif "method" in msg:
                    try:
                        self._on_notify(msg)
                    except Exception:
                        pass
                elif "id" in msg:
                    with self._lock:
                        slot = self._waiting.pop(msg["id"], None)
                    if slot is not None:
                        slot[1] = msg
                        slot[0].set()
        except (OSError, ValueError):
            pass
        finally:
            self.closed.set()
            with self._lock:
                waiting, self._waiting = list(self._waiting.values()), {}
            for slot in waiting:
                slot[0].set()

    def _read_err(self) -> None:
        try:
            for raw in self.proc.stderr:
                self._err.append(raw.decode("utf-8", errors="replace"))
                if sum(len(t) for t in self._err) > _STDERR_TAIL * 2:
                    self._err[:] = ["".join(self._err)[-_STDERR_TAIL:]]
        except (OSError, ValueError):
            pass

    def stderr_tail(self) -> str:
        return "".join(self._err)[-_STDERR_TAIL:].strip()

    def join(self, timeout: float) -> None:
        for t in self._threads:
            t.join(timeout=timeout)


class _Turn:
    """Состояние хода: элементы, строки хода работы, текст, конец.
    `on_text(кусок)` — текст модели по ходу (`item/agentMessage/delta`; без
    дельт — целиком по `item/completed`), `on_text(None)` — началось новое
    сообщение модели; зовётся из потока чтения."""

    def __init__(self, approver, emit, on_text=None) -> None:
        self.approver = approver
        self.emit = emit or (lambda _e: None)
        self.on_text = on_text
        self._message: str | None = None       # id сообщения, текст которого идёт по ходу
        self._streamed: set[str] = set()
        self.peer: _Peer | None = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.items: dict[str, dict] = {}
        self.tools = _codex.ToolStream()
        self.text = ""
        self.errors: list[str] = []
        self.result: dict | None = None
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.hints: dict[str, dict[str, bool]] = {}           # сервер MCP → {инструмент: readOnlyHint}
        self.mcp_decisions: list[tuple[str, object]] = []    # решения ворот до `item/started` вызова MCP

    def _emit_all(self, events) -> None:
        for event in events:
            self.emit(event)

    def _say(self, iid, piece: str) -> None:
        """Кусок текста сообщения `iid` по ходу; другое сообщение — сначала None."""
        if self.on_text is None or not piece:
            return
        try:
            if self._message is not None and iid != self._message:
                self.on_text(None)
            self._message = iid
            self.on_text(piece)
        except Exception:
            pass

    def notify(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if method == "item/agentMessage/delta":
            iid = params.get("itemId")
            if isinstance(iid, str) and isinstance(params.get("delta"), str):
                self._streamed.add(iid)
                self._say(iid, params["delta"])
            return
        if method in ("item/started", "item/completed"):
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            iid = item.get("id")
            if isinstance(iid, str):
                with self.lock:
                    self.items[iid] = item
            if method == "item/completed" and item.get("type") == "agentMessage":
                self.text = str(item.get("text") or "")
                if iid not in self._streamed:
                    self._say(iid, self.text)
            kind = "item.started" if method == "item/started" else "item.completed"
            self._emit_all(self.tools.item(kind, _exec_shape(item)))
            if method == "item/started" and item.get("type") == "mcpToolCall" and isinstance(iid, str):
                self._mcp_gate_row(item, iid)
        elif method == "error":
            err = params.get("error") if isinstance(params.get("error"), dict) else {}
            if not params.get("willRetry") and err.get("message"):
                self.errors.append(str(err["message"]))
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if self.turn_id is None or turn.get("id") in (None, self.turn_id):
                self.result = turn
                self.done.set()

    def request(self, msg: dict) -> None:
        """Запрос сервера (в своём потоке): одобрения — воротам Meet, прочее — отказ."""
        peer = self.peer
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if peer is None:
            return
        if method in (COMMAND_APPROVAL, FILE_APPROVAL):
            iid = params.get("itemId") if isinstance(params.get("itemId"), str) else None
            with self.lock:
                item = dict(self.items.get(iid) or {}) if iid else {}
            answer, decision = "decline", None
            if self.approver is not None:
                try:
                    answer, decision = self.approver(method, params, item)
                except Exception:
                    answer, decision = "decline", None
            if decision is not None and iid:
                from meet.llm.claude_stream import _gate_event

                name = "Bash" if method == COMMAND_APPROVAL else "apply_patch"
                self.emit(_gate_event(decision, name, iid, "codex"))
            peer.respond(rid, {"decision": answer if answer in ("accept", "acceptForSession") else "decline"})
        elif method == ELICITATION:
            self._elicitation(peer, rid, params)
        elif method == "item/tool/requestUserInput":
            peer.respond(rid, {"answers": {}})
        elif method in ("execCommandApproval", "applyPatchApproval"):
            peer.respond(rid, {"decision": {"denied": {"rejection": "Meet этот запрос не поддерживает"}}})
        else:
            peer.respond(rid, error={"code": -32601, "message": "Meet этот запрос не поддерживает"})

    def _elicitation(self, peer: "_Peer", rid, params: dict) -> None:
        """MCP-вопрос: одобрение вызова MCP (`_meta.codex_approval_kind =
        "mcp_tool_call"`) или вопрос сервера пользователю → `approver` (ворота
        Meet: `consent.codex_elicitation`, в контексте — `readOnlyHint`
        инструмента) → `accept` / `decline`. Решение по вызову ляжет в его
        строку, когда придёт `item/started` (вызов MCP начинается после
        одобрения)."""
        from meet.llm import consent

        call = None
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        if meta.get("codex_approval_kind") == "mcp_tool_call":
            call = consent.codex_mcp_call(params)
        context = {"readOnlyHint": self._read_only(*consent.mcp_parts(call[0])) if call else None}
        answer, decision = "decline", None
        if self.approver is not None:
            try:
                answer, decision = self.approver(ELICITATION, params, context)
            except Exception:
                answer, decision = "decline", None
        if call is not None and decision is not None:
            with self.lock:
                self.mcp_decisions.append((call[0], decision))
        if answer in ("accept", "acceptForSession"):
            peer.respond(rid, {"action": "accept", "content": {}, "_meta": None})
        else:
            peer.respond(rid, {"action": "decline", "content": None, "_meta": None})

    def _read_only(self, server: str, tool: str) -> bool | None:
        """`readOnlyHint` инструмента MCP: из элемента вызова, если он уже
        пришёл, иначе из каталога сервера (`mcpServerStatus/list`, один раз на
        сервер за ход); неизвестно — None (решают ворота, как без пометки)."""
        with self.lock:
            for item in self.items.values():
                if (item.get("type") == "mcpToolCall" and item.get("server") == server
                        and item.get("tool") == tool and isinstance(item.get("readOnlyHint"), bool)):
                    return item["readOnlyHint"]
            hints = self.hints.get(server)
        if hints is None:
            hints = {}
            peer = self.peer
            try:
                result = peer.call("mcpServerStatus/list", {"threadId": self.thread_id, "serverName": server,
                                                            "detail": "toolsAndAuthOnly"},
                                   HINTS_TIMEOUT_S) if peer is not None else {}
            except (_NoAnswer, _RpcError):
                result = {}
            for status in result.get("data") or ():
                if not isinstance(status, dict) or status.get("name") != server:
                    continue
                for name, spec in (status.get("tools") or {}).items() if isinstance(status.get("tools"), dict) \
                        else ():
                    notes = spec.get("annotations") if isinstance(spec, dict) else None
                    hint = notes.get("readOnlyHint") if isinstance(notes, dict) else None
                    if isinstance(hint, bool):
                        hints[str(spec.get("name") or name)] = hint
            with self.lock:
                self.hints[server] = hints
        return hints.get(tool)

    def _mcp_gate_row(self, item: dict, iid: str) -> None:
        name = f"mcp__{item.get('server')}__{item.get('tool')}"
        with self.lock:
            found = next((k for k, (tool, _d) in enumerate(self.mcp_decisions) if tool == name), None)
            decision = self.mcp_decisions.pop(found)[1] if found is not None else None
        if decision is not None:
            from meet.llm.claude_stream import _gate_event

            self.emit(_gate_event(decision, name, iid, "codex"))

    def abort_rows(self) -> None:
        self._emit_all(self.tools.abort())


def _exec_shape(item: dict) -> dict:
    """Элемент app-server → элемент событий `codex exec --json` (для `ToolStream`)."""
    kind = _ITEM_TYPES.get(item.get("type"))
    if kind is None:
        return {}
    status = item.get("status")
    out = {"type": kind, "id": item.get("id"),
           "status": "failed" if status in ("failed", "declined") else status}
    if kind == "command_execution":
        out.update(command=item.get("command"), aggregated_output=item.get("aggregatedOutput"),
                   exit_code=item.get("exitCode"))
    elif kind == "file_change":
        out["changes"] = [{"path": c.get("path"),
                           "kind": (c.get("kind") or {}).get("type") if isinstance(c.get("kind"), dict)
                           else c.get("kind")}
                          for c in item.get("changes") or () if isinstance(c, dict)]
    elif kind == "mcp_tool_call":
        out.update(server=item.get("server"), tool=item.get("tool"), arguments=item.get("arguments"),
                   result=item.get("result"), error=item.get("error"))
    elif kind == "web_search":
        out["query"] = item.get("query")
    return out


def _inputs(text: str, images) -> list[dict]:
    return [{"type": "text", "text": text, "text_elements": []},
            *({"type": "localImage", "path": str(p)} for p in images or ())]


def run_turn(cmd: list[str], *, env: dict | None, cwd: str, text: str, images=(), resume: str | None = None,
             keep_session: bool = False, policy: dict, model: str | None = None, effort: str | None = None,
             approver=None, emit=None, timeout_s: float = 180.0, call=None, on_text=None) -> AgentReply:
    """Один ход через app-server (блокирующее; см. модуль). Не подошёл до
    начала хода — `Unavailable`. `approver(метод, params, элемент) -> (ответ,
    решение ворот)`; `emit` — события хода работы (`tool_use`, `gate`,
    `tool_result`); `on_text` — текст модели по ходу (`_Turn`); `call` —
    `codex._Call` для «Стоп» из цикла событий."""
    deadline = time.monotonic() + timeout_s
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, **popen_kwargs())
    except OSError as e:
        raise Unavailable(f"не запустился ({type(e).__name__})") from None
    turn = _Turn(approver, emit, on_text)
    peer = _Peer(proc, on_notify=turn.notify, on_request=turn.request)
    turn.peer = peer
    stopper = _Stopper(peer, proc, turn)
    if call is not None:
        call.proc = proc
        call.stop = stopper.stop
        if call.cancelled:
            stopper.stop()
    try:
        return _drive(peer, turn, stopper, cwd=cwd, text=text, images=images, resume=resume,
                      keep_session=keep_session, policy=policy, model=model, effort=effort,
                      deadline=deadline, call=call)
    finally:
        _close(peer, proc)


class _Stopper:
    """«Стоп» и таймаут: сеанс уже есть — `turn/interrupt` (как только
    известен id хода), через INTERRUPT_GRACE_S — дерево процессов; сеанса ещё
    нет — сразу дерево."""

    def __init__(self, peer: _Peer, proc, turn: _Turn) -> None:
        self.peer, self.proc, self.turn = peer, proc, turn
        self.stopped = False
        self._interrupted = False
        self._lock = threading.Lock()

    def stop(self) -> None:
        with self._lock:
            if self.stopped:
                return
            self.stopped = True
        if self.turn.thread_id is None:
            kill_tree(self.proc)
            return
        timer = threading.Timer(INTERRUPT_GRACE_S, kill_tree, args=(self.proc,))
        timer.daemon = True
        timer.start()
        self.interrupt()

    def interrupt(self) -> None:
        """`turn/interrupt` — один раз, когда остановлено и id хода известен."""
        with self._lock:
            if not self.stopped or self._interrupted or not self.turn.turn_id:
                return
            self._interrupted = True
        self.peer.send_request("turn/interrupt", {"threadId": self.turn.thread_id, "turnId": self.turn.turn_id})


def _left(deadline: float, cap: float) -> float:
    return max(0.0, min(cap, deadline - time.monotonic()))


def _drive(peer: _Peer, turn: _Turn, stopper: _Stopper, *, cwd, text, images, resume, keep_session, policy,
           model, effort, deadline: float, call) -> AgentReply:
    cancelled = (lambda: bool(call is not None and call.cancelled))
    # 1. Рукопожатие.
    try:
        peer.call("initialize", {"clientInfo": {"name": "meet", "title": "Meet", "version": _client_version()},
                                 "capabilities": {"experimentalApi": False, "requestAttestation": False}},
                  _left(deadline, INIT_TIMEOUT_S))
    except _NoAnswer:
        if cancelled():
            return AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=resume)
        raise Unavailable("нет ответа на initialize") from None
    except _RpcError as e:
        raise Unavailable(f"ошибка {e.code} в initialize") from None
    peer.notify("initialized")
    # 2. Сеанс.
    thread_params = {"cwd": cwd, "approvalPolicy": policy["approvalPolicy"],
                     "approvalsReviewer": policy["approvalsReviewer"], "sandbox": policy["sandbox"],
                     **({"model": model} if model else {})}
    method = "thread/resume" if resume else "thread/start"
    params = ({"threadId": resume, **thread_params, "excludeTurns": True} if resume
              else {**thread_params, "ephemeral": not keep_session})
    try:
        result = peer.call(method, params, _left(deadline, REQUEST_TIMEOUT_S))
    except _NoAnswer:
        if cancelled():
            return AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=resume)
        raise Unavailable(f"нет ответа на {method}") from None
    except _RpcError as e:
        if resume and e.code not in _PROTOCOL:
            return resume_failure(e.message[-_ERR_LIMIT:] or f"Codex не продолжил сеанс {resume}")
        raise Unavailable(f"ошибка {e.code} в {method}") from None
    thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
    tid = thread.get("id") if isinstance(thread.get("id"), str) else None
    if not tid:
        raise Unavailable(f"нет id сеанса в ответе {method}")
    if resume and tid.lower() != resume.lower():
        return resume_failure(f"Codex начал другой сеанс ({tid}) вместо {resume}")
    if result.get("approvalsReviewer") != "user":
        # Одобрения решал бы не человек, а автопроверка Codex — такой ход не годится.
        raise Unavailable("проверяющий одобрений — не пользователь")
    turn.thread_id = tid
    sid = tid if (keep_session or resume) else None
    # 3. Ход.
    turn_params = {"threadId": tid, "input": _inputs(text, images), "cwd": cwd,
                   "approvalPolicy": policy["approvalPolicy"], "approvalsReviewer": policy["approvalsReviewer"],
                   "sandboxPolicy": policy["sandboxPolicy"],
                   **({"model": model} if model else {}), **({"effort": effort} if effort else {})}
    try:
        started = peer.call("turn/start", turn_params, _left(deadline, REQUEST_TIMEOUT_S))
    except _NoAnswer:
        if cancelled():
            return AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=sid)
        raise Unavailable("нет ответа на turn/start") from None
    except _RpcError as e:
        if e.code in _PROTOCOL:
            raise Unavailable(f"ошибка {e.code} в turn/start") from None
        return AgentReply(text="", error=e.message[-_ERR_LIMIT:] or "Codex не начал ход", session_id=sid)
    started_turn = started.get("turn") if isinstance(started.get("turn"), dict) else {}
    turn.turn_id = started_turn.get("id") if isinstance(started_turn.get("id"), str) else None
    stopper.interrupt()          # «Стоп» пришёл, пока ход начинался
    # 4. Ожидание конца хода.
    timed_out = False
    while not turn.done.wait(_POLL_S):
        if peer.closed.is_set() and not turn.done.is_set():
            break
        if cancelled() and not stopper.stopped:
            stopper.stop()
        if time.monotonic() > deadline and not timed_out:
            timed_out = True
            stopper.stop()
        if stopper.stopped and time.monotonic() > deadline + INTERRUPT_GRACE_S + 1:
            break
    turn.abort_rows()
    if cancelled():
        return AgentReply(text="", error=CANCELLED_ERROR, cancelled=True, session_id=sid)
    if timed_out:
        return AgentReply(text="", error=TIMEOUT_ERROR, session_id=sid)
    final = turn.result or {}
    status = final.get("status")
    if not turn.done.is_set():
        err = peer.stderr_tail() or "\n".join(turn.errors[-3:])
        return AgentReply(text="", error=err[-_ERR_LIMIT:] or "Codex завершился посреди хода", session_id=sid)
    if status == "completed":
        if not turn.text.strip():
            return AgentReply(text="", error=EMPTY_ERROR, session_id=sid)
        return AgentReply(text=turn.text.strip(), session_id=sid)
    error = final.get("error") if isinstance(final.get("error"), dict) else {}
    message = str(error.get("message") or "") or "\n".join(turn.errors[-3:]) or (
        "ход прерван" if status == "interrupted" else "ход не удался")
    return AgentReply(text="", error=message[-_ERR_LIMIT:], session_id=sid)


def _close(peer: _Peer, proc, grace: float = CLOSE_GRACE_S) -> None:
    """Конец хода: stdin закрыт — сервер выходит сам; не вышел за `grace`
    секунд — дерево процессов гасится."""
    try:
        proc.stdin.close()
    except (OSError, ValueError):
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            pass
    peer.join(timeout=5)
