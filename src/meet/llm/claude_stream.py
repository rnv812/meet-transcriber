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
"""

import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

from meet import netproxy, procjob
from meet.llm import detect
from meet.llm.base import TIMEOUT_ERROR, AgentReply, drop_session_markers
from meet.llm.claude import text_delta

STDERR_TAIL = 2000
CLOSE_WAIT_S = 2.0
WORKDIR = "meet-live"


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
                  thinking: str | None = None, effort: str | None = None) -> list[str]:
    """Командная строка постоянного процесса (флаги сверены с claude 2.1.28x)."""
    cmd = [*cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
           "--verbose", "--include-partial-messages",
           "--system-prompt", system_prompt,
           "--tools", "", "--setting-sources", "", "--strict-mcp-config",
           "--no-session-persistence", "--disable-slash-commands", "--max-turns", "1"]
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
    выход): по ним вызывающий решает, когда начать диалог заново."""

    stateful = True

    def __init__(self, *, system_prompt: str, model: str | None = None,
                 thinking: str | None = None, effort: str | None = None,
                 proxy: str | None = None, cwd: str | Path | None = None,
                 cli: list[str] | None = None, popen=subprocess.Popen, log=None) -> None:
        self._system = system_prompt
        self._model = model
        self._thinking = thinking
        self._effort = effort
        self._proxy = proxy
        self._cwd = cwd
        self._cli = cli
        self._popen = popen
        self._log = log or (lambda _line: None)
        self._proc = None
        self._queue: asyncio.Queue | None = None
        self._stderr: list[str] = []
        self._lock = asyncio.Lock()
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

    # --- процесс ---

    def _spawn(self) -> str | None:
        """Поднять процесс; None — ок, иначе текст ошибки."""
        cli = self._cli if self._cli is not None else default_cli()
        if not cli:
            return detect.claude_not_found()
        cmd = build_command(cli, system_prompt=self._system, model=self._model,
                            thinking=self._thinking, effort=self._effort)
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

        def put(item) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                pass  # цикл закрыт — читать некому

        def read_out() -> None:
            try:
                for raw in proc.stdout:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        put(msg)
            except (OSError, ValueError):
                pass
            finally:
                put(None)

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
        self._log(f"диалог подсказок: процесс Claude Code запущен (pid {proc.pid})")
        return None

    def _stderr_tail(self) -> str:
        return "".join(self._stderr)[-STDERR_TAIL:].strip()

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

    # --- ход ---

    async def send(self, text: str, *, on_text=None, timeout_s: float = 90.0) -> AgentReply:
        """Одно сообщение пользователя → ответ. `on_text(кусок)` — текст по
        мере генерации. Ошибки — в AgentReply.error (процесс тогда закрыт)."""
        async with self._lock:
            return await self._send(text, on_text, timeout_s)

    async def _send(self, text: str, on_text, timeout_s: float) -> AgentReply:
        if not self.alive:
            if self._proc is not None:
                await asyncio.to_thread(self.kill)
            error = self._spawn()
            if error:
                return AgentReply(text="", error=error)
        proc, queue = self._proc, self._queue
        line = json.dumps({"type": "user", "message": {"role": "user", "content": text}},
                          ensure_ascii=False) + "\n"
        started = time.monotonic()
        try:
            await asyncio.to_thread(_write, proc, line.encode("utf-8"))
        except (OSError, ValueError) as e:
            error = self._stderr_tail() or f"{type(e).__name__}: {e}"
            await asyncio.to_thread(self.kill)
            return AgentReply(text="", error=netproxy.with_hint(f"процесс Claude Code не принял запрос: {error}"))
        parts: list[str] = []
        streamed = False
        self.last_first_text_s = None
        deadline = started + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await asyncio.to_thread(self.kill)
                return AgentReply(text="".join(parts).strip(), error=TIMEOUT_ERROR)
            try:
                msg = await asyncio.wait_for(queue.get(), remaining)
            except asyncio.TimeoutError:
                continue
            if msg is None:
                detail = self._stderr_tail() or "процесс завершился"
                await asyncio.to_thread(self.kill)
                return AgentReply(text="".join(parts).strip(),
                                  error=netproxy.with_hint(f"Claude Code: {detail}"))
            kind = msg.get("type")
            if kind == "stream_event":
                delta = text_delta(msg.get("event"))
                if delta:
                    if self.last_first_text_s is None:
                        self.last_first_text_s = time.monotonic() - started
                    streamed = True
                    parts.append(delta)
                    if on_text is not None:
                        try:
                            on_text(delta)
                        except Exception as e:  # показ не обрывает ход
                            self._log(f"диалог подсказок: сбой обработчика ({type(e).__name__})")
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
                if msg.get("is_error"):
                    return AgentReply(text=body, error=netproxy.with_hint(
                        str(result or msg.get("subtype") or "ошибка Claude Code")))
                return AgentReply(text=body)


def _write(proc, data: bytes) -> None:
    proc.stdin.write(data)
    proc.stdin.flush()
