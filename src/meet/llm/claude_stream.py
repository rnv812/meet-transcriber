"""Постоянный диалог с Claude Code: один процесс CLI на всю встречу.

Подсказкам живого ассистента нужен контекст всего разговора, а не только
последней минуты, и нужен быстро. Отдельный вызов на каждый тик — это
секунда на старт процесса и весь разговор заново в каждом запросе. Здесь —
один процесс `claude -p --input-format stream-json --output-format
stream-json` на встречу: каждый тик — новое сообщение пользователя в том же
диалоге (только новые реплики), ответ приходит потоком, кусками текста.

Процесс:

* без инструментов (`--tools ""`), без настроек и MCP пользователя, без
  слэш-команд, один ход на сообщение;
* не сохраняет сеанс на диск (`--no-session-persistence`): разговор живёт
  только в памяти процесса и в историю Claude Code человека не попадает;
* рабочая папка — служебная пустая папка, не папка встречи: такой диалог
  нечем было бы «продолжить» из вкладки «Агент»;
* окружение — как у разовых вызовов (`llm.claude`): без ANTHROPIC_API_KEY и
  меток чужого сеанса, с прокси из настроек;
* привязан к процессу ассистента (`meet.procjob`): ассистент умер — умер и
  он; штатно его закрывает `close()` (конец stdin, затем — убить).

Сбой процесса или таймаут хода — ошибка ответа и мёртвый диалог: следующий
`send` поднимает новый процесс (вызывающий начинает его с затравки).

V4 (0.3.6, v4-design §3, §5.1, §12):

* **собеседник** (`responder=True`): инструменты чтения — `--restricted
  --tools Read,Grep,Glob --allowedTools Read,Grep,Glob --permission-mode
  dontAsk --add-dir <папка>… --max-turns 6`; слушатель — как раньше;
* **изображения**: `send(text, images=[…])` или `send(content=[блоки])` —
  блоки `{"type":"image","source":{"type":"base64",…}}` в `message.content`;
* **остановка хода**: `interrupt()` — `control_request` с `subtype:
  interrupt` (формат — `claude_agent_sdk` `Query.interrupt`), ждём
  `control_response`; ход кончается `result` с ошибкой — ответ `cancelled`.
  Нет подтверждения за INTERRUPT_WAIT_S — процесс убит и поднят заново;
* **нативное продолжение** (`persist=True`): сеанс сохраняется (без
  `--no-session-persistence`), новый — с нашим UUID (`--session-id=`),
  после перезапуска процесса и с `resume=<id>` — `--resume=<id>`. Id —
  `session_id`. Продолжить не вышло — ответ `resume_failed`, диалог
  забывает id: следующий `send` начнёт новый сеанс (вызывающий — с
  затравкой из журнала).
"""

import asyncio
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from meet import netproxy, procjob
from meet.llm import detect
from meet.llm.base import (
    CANCELLED_ERROR, TIMEOUT_ERROR, AgentReply, drop_session_markers, is_uuid, resume_failure,
)
from meet.llm.claude import text_delta, user_content

STDERR_TAIL = 2000
CLOSE_WAIT_S = 2.0
WORKDIR = "meet-live"
# Сколько ждать `control_response` на остановку хода (v4-design §3.4).
INTERRUPT_WAIT_S = 5.0
# Собеседник: только чтение (claude 2.1.292 --help: --restricted убирает
# инструменты, выполняющие код, и WebFetch; файлы — только в рабочих папках и
# --add-dir; --permission-mode dontAsk — не спрашивать, а отказывать).
RESPONDER_TOOLS = "Read,Grep,Glob"
RESPONDER_MAX_TURNS = 6
# Что пишет CLI, когда сеанса для --resume нет (2.1.292, проверено без модели:
# пустая папка настроек, без входа): stderr и `errors` у result, код выхода 1.
_RESUME_MISSING = "no conversation found"


def workdir(name: str = WORKDIR) -> Path:
    """Служебная рабочая папка процессов модели (пустая, во временной папке
    системы: у ассистента свой корень временных файлов, а папка «Спросить»
    должна быть той же от встречи к встрече — по ней Claude Code ведёт сессии)."""
    from meet import tempdirs

    path = tempdirs.system_temp() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_cli() -> list[str] | None:
    """Команда запуска найденного Claude Code CLI; не найден — None."""
    exe = detect.find_claude()
    return [exe] if exe else None


def build_command(cli: list[str], *, system_prompt: str, model: str | None = None,
                  thinking: str | None = None, effort: str | None = None,
                  responder: bool = False, add_dirs=(), max_turns: int | None = None,
                  session_id: str | None = None, resume: str | None = None) -> list[str]:
    """Командная строка постоянного процесса (флаги сверены с claude 2.1.292
    `--help`; `--max-turns` и `--thinking` в справке не показаны — их
    передаёт и claude_agent_sdk).

    Слушатель (по умолчанию) — без инструментов, один ход. Собеседник
    (`responder`) — чтение в `add_dirs`, до RESPONDER_MAX_TURNS ходов.
    Сеанс: без `session_id`/`resume` — не сохраняется; `session_id` — новый
    сохраняемый с этим UUID; `resume` — продолжить сохранённый. Значения —
    через `=`: id не станет отдельным флагом (так же делает SDK)."""
    cmd = [*cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--include-partial-messages",
           "--system-prompt", system_prompt]
    if responder:
        cmd += ["--restricted", "--tools", RESPONDER_TOOLS, "--allowedTools", RESPONDER_TOOLS,
                "--permission-mode", "dontAsk"]
        for d in add_dirs or ():
            if d:
                cmd += ["--add-dir", str(d)]
    else:
        cmd += ["--tools", ""]
    cmd += ["--setting-sources", "", "--strict-mcp-config"]
    if resume:
        cmd.append(f"--resume={resume}")
    elif session_id:
        cmd.append(f"--session-id={session_id}")
    else:
        cmd.append("--no-session-persistence")
    turns = max_turns or (RESPONDER_MAX_TURNS if responder else 1)
    cmd += ["--disable-slash-commands", "--max-turns", str(turns)]
    if model:
        cmd += ["--model", model]
    if thinking:
        cmd += ["--thinking", thinking]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def child_env(proxy: str | None) -> dict:
    env = netproxy.child_env(proxy)
    env.pop("ANTHROPIC_API_KEY", None)  # подписка, а не ключ (как у llm.claude)
    drop_session_markers(env)
    return env


class Conversation:
    """Диалог с Claude Code в одном процессе. `stateful` — модель помнит
    прежние сообщения: вызывающий шлёт только новое.

    `cli` — команда запуска CLI (по умолчанию найденный claude.exe; тесты
    подставляют поддельный). `turns` — сколько ходов в этом процессе,
    `context_tokens` — размер контекста по последнему ответу (вход с кэшем +
    выход): по ним вызывающий решает, когда начать диалог заново.

    `responder` — голова-собеседник (инструменты чтения в `add_dirs`).
    `persist` — сеанс сохраняется и продолжается нативно (`session_id`);
    `resume` — id сохранённого сеанса, с которого начать (из
    `assistant/sessions.json`), подразумевает `persist`. `has_context` —
    следующий `send` продолжит прежний разговор (живой процесс с ходами или
    сохранённый сеанс): затравка не нужна."""

    stateful = True

    def __init__(self, *, system_prompt: str, model: str | None = None,
                 thinking: str | None = None, effort: str | None = None,
                 proxy: str | None = None, cwd: str | Path | None = None,
                 cli: list[str] | None = None, popen=subprocess.Popen, log=None,
                 responder: bool = False, add_dirs=(), max_turns: int | None = None,
                 persist: bool = False, resume: str | None = None) -> None:
        self._system = system_prompt
        self._model = model
        self._thinking = thinking
        self._effort = effort
        self._proxy = proxy
        self._cwd = cwd
        self._cli = cli
        self._popen = popen
        self._log = log or (lambda _line: None)
        self._responder = responder
        self._add_dirs = tuple(add_dirs or ())
        self._max_turns = max_turns
        self._persist = bool(persist or resume)
        # Id сеанса (persist) и есть ли он уже на диске: тогда процесс
        # поднимается с --resume, иначе — новый сеанс с новым id.
        self.session_id: str | None = resume if self._persist else None
        self._saved = bool(resume)
        self._resuming = False
        self._proc = None
        self._queue: asyncio.Queue | None = None
        self._stderr: list[str] = []
        self._lock = asyncio.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._requests = 0
        self._busy = False
        self._cancel = False
        self.turns = 0
        self.context_tokens = 0
        self.spawns = 0
        self.last_first_text_s: float | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def has_context(self) -> bool:
        """Следующий `send` продолжит разговор (затравка не нужна)."""
        if self.alive and self.turns > 0:
            return True
        return self._persist and self._saved and self.session_id is not None

    def forget_session(self) -> None:
        """Забыть сохранённый сеанс: следующий процесс начнёт новый."""
        self._saved = False
        self.session_id = None

    # --- процесс ---

    def _spawn(self) -> str | None:
        """Поднять процесс; None — ок, иначе текст ошибки."""
        cli = self._cli if self._cli is not None else default_cli()
        if not cli:
            return detect.claude_not_found()
        session_id = resume = None
        self._resuming = False
        if self._persist:
            if self._saved and self.session_id:
                resume, self._resuming = self.session_id, True
            else:
                self.session_id = session_id = str(uuid.uuid4())
        cmd = build_command(cli, system_prompt=self._system, model=self._model,
                            thinking=self._thinking, effort=self._effort,
                            responder=self._responder, add_dirs=self._add_dirs,
                            max_turns=self._max_turns, session_id=session_id, resume=resume)
        cwd = str(self._cwd) if self._cwd else str(workdir())
        try:
            proc = self._popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=child_env(self._proxy), cwd=cwd,
                               **procjob.popen_kwargs())
        except OSError as e:
            return f"не удалось запустить Claude Code: {e}"
        procjob.bind_to_this_process(proc.pid)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        self._proc, self._queue = proc, queue
        self._stderr = []
        self.turns = 0
        self.context_tokens = 0
        self.spawns += 1

        def call(fn, *args) -> None:
            try:
                loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:
                pass  # цикл закрыт — читать некому

        def read_out() -> None:
            try:
                for raw in proc.stdout:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    kind = msg.get("type")
                    if kind == "control_response":
                        call(self._on_control_response, msg)
                    elif kind == "control_request":
                        self._refuse_control(proc, msg)
                    else:
                        call(queue.put_nowait, msg)
            except (OSError, ValueError):
                pass
            finally:
                call(queue.put_nowait, None)

        def read_err() -> None:
            try:
                for raw in proc.stderr:
                    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                    self._stderr.append(text)
                    if sum(len(t) for t in self._stderr) > STDERR_TAIL * 2:
                        self._stderr = ["".join(self._stderr)[-STDERR_TAIL:]]
            except (OSError, ValueError):
                pass

        threading.Thread(target=read_out, name="claude-stream-out", daemon=True).start()
        threading.Thread(target=read_err, name="claude-stream-err", daemon=True).start()
        how = (f", продолжение сеанса {resume}" if resume
               else f", сеанс {session_id}" if session_id else "")
        self._log(f"диалог подсказок: процесс Claude Code запущен (pid {proc.pid}{how})")
        return None

    def _stderr_tail(self) -> str:
        return "".join(self._stderr)[-STDERR_TAIL:].strip()

    def _write_line(self, proc, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:  # ход, остановка и отказ читателя пишут из разных потоков
            _write(proc, data)

    def _on_control_response(self, msg: dict) -> None:
        response = msg.get("response") if isinstance(msg.get("response"), dict) else {}
        fut = self._pending.pop(str(response.get("request_id")), None)
        if fut is not None and not fut.done():
            fut.set_result(response)

    def _refuse_control(self, proc, msg: dict) -> None:
        """Запрос от CLI (разрешение инструмента и т. п.): хозяина-SDK у нас
        нет — отказываем, чтобы ход не ждал ответа вечно. При dontAsk таких
        запросов быть не должно."""
        request = msg.get("request") if isinstance(msg.get("request"), dict) else {}
        rid = msg.get("request_id")
        if request.get("subtype") == "can_use_tool":
            response = {"subtype": "success", "request_id": rid,
                        "response": {"behavior": "deny", "message": "инструмент не разрешён"}}
        else:
            response = {"subtype": "error", "request_id": rid,
                        "error": f"не поддерживается: {request.get('subtype')}"}
        try:
            self._write_line(proc, {"type": "control_response", "response": response})
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        """Закрыть диалог: конец stdin (процесс выходит сам), не вышел за
        CLOSE_WAIT_S — убить. Повторный вызов безопасен."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=CLOSE_WAIT_S)
        except subprocess.TimeoutExpired:
            procjob.kill_tree(proc)
            try:
                proc.wait(timeout=CLOSE_WAIT_S)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass

    def kill(self) -> None:
        """Убить сразу (таймаут хода: что процесс напишет дальше — неизвестно)."""
        proc, self._proc = self._proc, None
        if proc is not None:
            procjob.kill_tree(proc)

    def _kill_if(self, proc) -> None:
        """Убить `proc`; текущим он перестаёт быть, только если ещё им был
        (его мог сменить перезапуск после остановки)."""
        if self._proc is proc:
            self.kill()
        else:
            procjob.kill_tree(proc)

    # --- ход ---

    async def send(self, text: str | None = None, *, content: list | None = None,
                   images=(), on_text=None, timeout_s: float = 90.0) -> AgentReply:
        """Одно сообщение пользователя → ответ. `on_text(кусок)` — текст по
        мере генерации; `on_text(None)` — модель начала новое сообщение после
        инструмента (собеседник): показанный текст был пояснением. Ошибки — в
        AgentReply.error (процесс тогда закрыт).

        Сообщение — `text` и изображения `images` (пути: блоки base64 перед
        текстом) или готовые блоки `content` (список словарей Anthropic API).
        Изображение не прочитать — ошибка, процесс не трогается."""
        if content is None:
            try:
                content = user_content(text or "", images)
            except (OSError, ValueError) as e:
                return AgentReply(text="", error=f"изображение не отправить: {e}")
        async with self._lock:
            self._busy, self._cancel = True, False
            try:
                return await self._send(content, on_text, timeout_s)
            finally:
                self._busy = False

    async def interrupt(self, *, wait_s: float = INTERRUPT_WAIT_S) -> bool:
        """Остановить идущий ход («Стоп»). Ход завершится ответом
        `cancelled` (error = CANCELLED_ERROR) с тем текстом, что успел прийти.

        True — CLI подтвердил (`control_response`), процесс живёт дальше;
        хода нет — тоже True. False — подтверждения нет за `wait_s` (или CLI
        ответил ошибкой): процесс убит и поднят заново (`persist` — с
        `--resume`, иначе вызывающему нужна затравка: `has_context` — False)."""
        proc = self._proc
        if not self._busy or proc is None or proc.poll() is not None:
            return True
        self._cancel = True
        self._requests += 1
        rid = f"req_{self._requests}_{os.urandom(4).hex()}"
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        confirmed = False
        try:
            await asyncio.to_thread(self._write_line, proc, {
                "type": "control_request", "request_id": rid, "request": {"subtype": "interrupt"}})
            response = await asyncio.wait_for(fut, wait_s)
            confirmed = response.get("subtype") != "error"
        except (OSError, ValueError, asyncio.TimeoutError):
            confirmed = False
        finally:
            self._pending.pop(rid, None)
        if confirmed:
            return True
        self._log(f"диалог: Claude Code не подтвердил остановку за {wait_s:g} с — перезапуск")
        await asyncio.to_thread(self._kill_if, proc)
        async with self._lock:  # ход дочитал конец потока и вернул ответ
            if not self.alive:
                error = self._spawn()
                if error:
                    self._log(f"диалог: перезапуск не удался: {error}")
        return False

    def _resume_failed(self, detail: str) -> AgentReply:
        sid = self.session_id
        self.forget_session()
        self._log(f"диалог: сеанс {sid} не продолжить: {detail}")
        return resume_failure(detail)

    async def _send(self, content, on_text, timeout_s: float) -> AgentReply:
        if not self.alive:
            if self._proc is not None:
                await asyncio.to_thread(self.kill)
            if self._persist and self._saved and not is_uuid(self.session_id):
                return self._resume_failed(f"неверный id сеанса: {self.session_id!r}")
            error = self._spawn()
            if error:
                return AgentReply(text="", error=error)
        proc, queue = self._proc, self._queue
        resuming = self._resuming
        started = time.monotonic()
        try:
            await asyncio.to_thread(self._write_line, proc, {
                "type": "user", "message": {"role": "user", "content": content}})
        except (OSError, ValueError) as e:
            await asyncio.sleep(0.2)  # дать CLI дописать stderr («сеанс не найден» и т. п.)
            error = self._stderr_tail() or f"{type(e).__name__}: {e}"
            await asyncio.to_thread(self._kill_if, proc)
            if resuming:
                return self._resume_failed(error)
            return AgentReply(text="", error=netproxy.with_hint(f"процесс Claude Code не принял запрос: {error}"))
        parts: list[str] = []
        streamed = False
        began = False  # был system/init: CLI начал ход, сеанс найден
        self.last_first_text_s = None
        deadline = started + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await asyncio.to_thread(self._kill_if, proc)
                return AgentReply(text="".join(parts).strip(), error=TIMEOUT_ERROR)
            try:
                msg = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                await asyncio.sleep(0.05)  # хвост stderr
                detail = self._stderr_tail() or "процесс завершился"
                await asyncio.to_thread(self._kill_if, proc)
                if self._cancel:
                    return AgentReply(text="".join(parts).strip(), error=CANCELLED_ERROR, cancelled=True)
                if resuming and (not began or _RESUME_MISSING in detail.lower()):
                    return self._resume_failed(detail)
                return AgentReply(text="".join(parts).strip(),
                                  error=netproxy.with_hint(f"Claude Code: {detail}"))
            kind = msg.get("type")
            if kind == "system" and msg.get("subtype") == "init":
                began = True
                self._resuming = False  # сеанс найден: дальше сбои этого процесса — обычные
                if self._persist:
                    self.session_id = str(msg.get("session_id") or self.session_id)
                    self._saved = True
            elif kind == "stream_event":
                event = msg.get("event")
                if isinstance(event, dict) and event.get("type") == "message_start" and streamed:
                    # Новое сообщение модели после инструмента (собеседник):
                    # прежний текст был пояснением, ответ — дальше.
                    parts.clear()
                    streamed = False
                    if on_text is not None:
                        _safe_call(on_text, None, self._log)
                    continue
                delta = text_delta(event)
                if delta:
                    if self.last_first_text_s is None:
                        self.last_first_text_s = time.monotonic() - started
                    streamed = True
                    parts.append(delta)
                    if on_text is not None:
                        _safe_call(on_text, delta, self._log)
            elif kind == "assistant" and not streamed:
                for block in (msg.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(str(block.get("text") or ""))
            elif kind == "result":
                self.turns += 1
                usage = msg.get("usage") or {}
                self.context_tokens = sum(int(usage.get(k) or 0) for k in (
                    "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                    "output_tokens"))
                result = msg.get("result")
                body = "".join(parts).strip() or (str(result).strip() if result else "")
                if self._cancel:
                    return AgentReply(text=body, error=CANCELLED_ERROR, cancelled=True)
                if msg.get("is_error"):
                    errors = [str(e) for e in msg.get("errors") or [] if e]
                    detail = "; ".join(errors) or str(result or msg.get("subtype") or "ошибка Claude Code")
                    if resuming and (not began or _RESUME_MISSING in detail.lower()):
                        await asyncio.to_thread(self._kill_if, proc)
                        return self._resume_failed(detail)
                    return AgentReply(text=body, error=netproxy.with_hint(detail))
                self._resuming = False
                return AgentReply(text=body)


def _safe_call(on_text, piece, log) -> None:
    try:
        on_text(piece)
    except Exception as e:  # показ не обрывает ход
        log(f"диалог подсказок: сбой обработчика ({type(e).__name__})")


def _write(proc, data: bytes) -> None:
    proc.stdin.write(data)
    proc.stdin.flush()
