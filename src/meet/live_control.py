"""Живой режим под резидентом: дочерний `meet assist` и его жизненный цикл.

Ассистент (расшифровка окнами, дайджест, вопросы к модели) живёт **отдельным
процессом**: ему нужны aiohttp, модели распознавания и SDK провайдеров, а
резиденту — нет (минимальная установка для записи их не содержит). Поэтому
здесь только stdlib: subprocess, urllib и http.client.

Протокол с ребёнком (`python -m meet.cli assist --no-browser --port 0
--endpoint-file <data_dir>/live.json --out <папка записей>`):

* ребёнок атомарно пишет `{"port", "pid", "folder"}` в файл эндпоинта, когда
  его сервер поднялся (модели к этому моменту загружены — до минуты);
* `POST /stop` → `{"ok": true}` сразу, затем финализация (дорожки, снятие
  `.recording.lock`) и выход с кодом 0. Застрявший вызов модели может держать
  процесс ещё до ~180 с уже после финализации — поэтому через STOP_TIMEOUT_S
  дерево процессов убивается: после финализации это ничего не теряет;
* `GET /events` — SSE `state`/`line` с `id:`, понимает Last-Event-ID;
* POST'ы с чужим Origin ребёнок отвергает, а без Origin пускает — urllib
  Origin не ставит, и это нам и нужно.

Старт **не блокирует** вызывающего: `start()` сразу возвращает статус
`starting`, а ожидание эндпоинта, переход в `active` и наблюдение за процессом
идут в своём потоке и сообщаются в шину (`live.started`, `live.stopped`,
`live.failed`). Иначе HTTP-обработчик панели висел бы минуту загрузки модели.

Вывод ребёнка уходит в `<data_dir>/logs/live.log` (не в pipe: непрочитанный
pipe заполнился бы и повесил ребёнка). Последняя непустая строка журнала —
текст ошибки, если ребёнок умер сам: «Подключите Claude Code…», «Запись уже
идёт…», последняя строка traceback'а.
"""

import http.client
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from meet import paths

LIVE_STARTING = "live.starting"
LIVE_STARTED = "live.started"
LIVE_STOPPING = "live.stopping"
LIVE_STOPPED = "live.stopped"
LIVE_FAILED = "live.failed"

ENDPOINT_NAME = "live.json"
LOG_NAME = "live.log"
LOG_MAX_BYTES = 1024 * 1024  # больше — прежний журнал уезжает в live.log.1

START_TIMEOUT_S = 60.0  # модель распознавания грузится до минуты
STOP_TIMEOUT_S = 90.0  # финализация дорожек; дальше — убийство дерева
POLL_S = 0.1
REQUEST_TIMEOUT_S = 5.0
ASK_TIMEOUT_S = 240.0  # вопрос — вызов модели (у ребёнка до 180 с) плюс дослив окна
RELAY_THREAD = "meet-live-relay"
RELAYED_EVENTS = ("state", "line")
ERROR_MAX_CHARS = 300


class LiveError(Exception):
    """Ребёнок отказал по существу (пустой вопрос и т.п.) — 400 для API."""


class LiveBusy(LiveError):
    """Живой режим уже запущен или запускается."""


class LiveNotRunning(Exception):
    """Живой режим не запущен — проксировать некуда (409 для API)."""


def endpoint_path() -> Path:
    return paths.data_dir() / ENDPOINT_NAME


def log_path() -> Path:
    return paths.logs_dir() / LOG_NAME


def _descends_from(pid, root: int) -> bool:
    """pid — наш ребёнок или его потомок.

    IMPORTANT: потомок — не экзотика. `python.exe` venv'а на Windows — это
    лаунчер, который запускает настоящий интерпретатор дочерним процессом:
    pid из файла эндпоинта (os.getpid() ребёнка) тогда не равен Popen.pid."""
    if not isinstance(pid, int):
        return False
    if pid == root:
        return True
    try:
        import psutil

        return any(parent.pid == root for parent in psutil.Process(pid).parents())
    except Exception:
        return False


def _read_endpoint(path: Path, pid: int) -> dict | None:
    """Эндпоинт именно нашего ребёнка. Чужой pid — файл от прошлого,
    убитого запуска: его порт давно никому не принадлежит."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("port"):
        return None
    if not _descends_from(data.get("pid"), pid):
        return None
    return data


def _spawn_process(argv: list[str], log_file):
    """Запустить ребёнка без окна консоли, весь вывод — в журнал."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _kill_tree(process) -> None:
    # Тот же помощник, что у очереди задач (psutil, откат на kill). Импорт
    # здесь, а не наверху: jobs — модуль очереди, а не зависимость протокола.
    from meet.jobs import _kill_tree as kill

    kill(process)


def _open_log() -> tuple:
    """Журнал ребёнка: дописываем, большой — откладываем в .1. Возвращает
    (файл, путь, смещение начала этого запуска)."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name(LOG_NAME + ".1"))
    except OSError:
        pass
    f = open(path, "ab")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    f.write(f"\n=== {stamp} старт ассистента ===\n".encode("utf-8"))
    f.flush()
    return f, path, f.tell()


def _last_line(path: Path | None, offset: int) -> str | None:
    """Последняя непустая строка вывода этого запуска."""
    if path is None:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:ERROR_MAX_CHARS] if lines else None


class LiveControl:
    """Один дочерний `meet assist` под присмотром резидента.

    `spawn(argv, log_file) -> Popen` и `kill(process)` подменяются в тестах;
    таймауты — тоже, чтобы тесты не ждали минуту."""

    def __init__(self, bus, spawn=None, *, kill=None, log=None,
                 start_timeout: float = START_TIMEOUT_S,
                 stop_timeout: float = STOP_TIMEOUT_S, poll_s: float = POLL_S) -> None:
        self.bus = bus
        self._spawn = spawn or _spawn_process
        self._kill = kill or _kill_tree
        self._log = log or (lambda message: None)
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._process = None
        self._thread: threading.Thread | None = None
        self._active = False
        self._port: int | None = None
        self._child_pid: int | None = None  # pid из файла эндпоинта
        self._folder: str | None = None
        self._error: str | None = None
        self._started_at: float | None = None
        self._stop_requested = False
        self._stop_deadline: float | None = None
        self._streams: set = set()

    # --- что показывать -------------------------------------------------

    def status(self) -> dict:
        """{"active", "starting", "stopping", "folder", "error", "started_at"}:
        error — почему упал последний запуск (сбрасывается следующим стартом),
        started_at — стенное время старта для секундомера панели."""
        with self._lock:
            return self._status_unlocked()

    def busy(self) -> bool:
        """Идёт или поднимается — вторая запись сейчас невозможна."""
        with self._lock:
            return self._process is not None

    # --- старт и стоп ---------------------------------------------------

    def start(self, out_root) -> dict:
        """Запустить ассистента. Не ждёт загрузки модели: статус `starting`
        сразу, дальше — события `live.started` / `live.failed`."""
        with self._lock:
            if self._process is not None:
                raise LiveBusy("Ассистент уже запущен")
            endpoint = endpoint_path()
            try:
                endpoint.unlink(missing_ok=True)  # от убитого прошлого запуска
            except OSError:
                pass
            argv = [sys.executable, "-m", "meet.cli", "assist", "--no-browser",
                    "--port", "0", "--endpoint-file", str(endpoint),
                    "--out", str(out_root)]
            log_file = None
            try:
                log_file, path, offset = _open_log()
                process = self._spawn(argv, log_file)
            except OSError as e:
                self._error = f"Не удалось запустить ассистента: {e}"
                process = None
            finally:
                if log_file is not None:
                    log_file.close()  # у ребёнка свой дескриптор
            if process is not None:
                self._process = process
                self._active = False
                self._port = None
                self._child_pid = None
                self._folder = None
                self._error = None
                self._started_at = time.time()
                self._stop_requested = False
                self._stop_deadline = None
                self._thread = threading.Thread(
                    target=self._watch, args=(process, endpoint, path, offset),
                    name="meet-live", daemon=True)
            thread, error = self._thread, self._error
            reply = self._status_unlocked()
        # События — вне лока (подписчики шины вправе спросить status()), и
        # `live.starting` — раньше потока: его `started`/`failed` не должны
        # обогнать начало.
        if process is None:
            self._log(f"ассистент: {error}")
            self.bus.emit(LIVE_FAILED, error=error, folder=None)
            return {"ok": False, **reply}
        self._log(f"ассистент запускается (pid {process.pid})")
        self.bus.emit(LIVE_STARTING)
        thread.start()
        return {"ok": True, **reply}

    def _status_unlocked(self) -> dict:
        running = self._process is not None
        return {"active": self._active, "starting": running and not self._active,
                "stopping": running and self._stop_requested, "folder": self._folder,
                "error": self._error, "started_at": self._started_at}

    def stop(self, wait: bool = False) -> dict:
        """Штатная остановка: `POST /stop`, через STOP_TIMEOUT_S — убийство
        дерева. `wait=True` ждёт выхода (выход резидента); без него ответ
        сразу, а конец — событием `live.stopped`."""
        with self._lock:
            process, thread = self._process, self._thread
            if process is None:
                return {"ok": False, "action": "not-live", **self._status_unlocked()}
            first = not self._stop_requested
            self._stop_requested = True
            port = self._port
            if first:
                self._stop_deadline = time.monotonic() + self._stop_timeout
        if first:
            self.bus.emit(LIVE_STOPPING)
            if port is None:
                # Ещё грузит модель: записи нет, ждать минуту незачем.
                self._log("ассистент остановлен до старта")
                self._kill(process)
            else:
                try:
                    self._request(port, "/stop", {}, REQUEST_TIMEOUT_S)
                    self._log("ассистенту отправлена остановка")
                except Exception as e:  # не отвечает — значит, только убить
                    self._log(f"ассистент не принял остановку ({e}) — убиваю")
                    self._kill(process)
        if wait and thread is not None and thread is not threading.current_thread():
            # start() запускает поток сразу после лока — дождаться этого мига.
            began = time.monotonic()
            while thread.ident is None and time.monotonic() - began < 2.0:
                time.sleep(0.01)
            if thread.ident is not None:
                thread.join(timeout=self._stop_timeout + 15.0)
        return {"ok": True, "action": "stopping", **self.status()}

    # --- наблюдение -----------------------------------------------------

    def _watch(self, process, endpoint: Path, path: Path, offset: int) -> None:
        error = self._await_endpoint(process, endpoint)
        killed = False
        while True:
            try:
                code = process.wait(timeout=self._poll_s)
                break
            except subprocess.TimeoutExpired:
                with self._lock:
                    deadline = self._stop_deadline
                if not killed and deadline is not None and time.monotonic() >= deadline:
                    self._log(f"ассистент не вышел за {self._stop_timeout:.0f} с — "
                              "убиваю дерево процессов")
                    self._kill(process)
                    killed = True
        self._drop_endpoint(endpoint, {process.pid, self._child_pid})
        with self._lock:
            stopped = self._stop_requested or (code == 0 and self._active
                                               and error is None)
            folder = self._folder
            if not stopped:
                error = error or _last_line(path, offset) or \
                    f"Ассистент завершился (код {code})"
            self._process = None
            self._thread = None
            self._active = False
            self._port = None
            self._folder = None
            self._started_at = None
            self._stop_requested = False
            self._stop_deadline = None
            self._error = None if stopped else error
            streams = list(self._streams)
        for stream in streams:
            stream.close()
        if stopped:
            self._log(f"ассистент остановлен: {folder}")
            self.bus.emit(LIVE_STOPPED, folder=folder)
        else:
            self._log(f"ассистент упал (код {code}): {error}")
            self.bus.emit(LIVE_FAILED, error=error, folder=folder)

    @staticmethod
    def _drop_endpoint(endpoint: Path, pids: set) -> None:
        """Убитый ребёнок не успел убрать свой файл эндпоинта — убираем сами.
        Сверяем pid уже без psutil: процесса к этому моменту нет."""
        try:
            data = json.loads(endpoint.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("pid") in pids:
                endpoint.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def _await_endpoint(self, process, endpoint: Path) -> str | None:
        """Ждать, пока ребёнок опубликует свой порт. Возвращает текст ошибки
        таймаута или None (дождались, вышел сам или его остановили)."""
        deadline = time.monotonic() + self._start_timeout
        while process.poll() is None:
            with self._lock:
                if self._stop_requested:
                    return None
            info = _read_endpoint(endpoint, process.pid)
            if info is not None:
                with self._lock:
                    if self._stop_requested:
                        return None  # остановлен до старта — его уже убивают
                    self._active = True
                    self._port = int(info["port"])
                    self._child_pid = info["pid"]
                    self._folder = str(info.get("folder") or "") or None
                    folder = self._folder
                self._log(f"ассистент слушает встречу: {folder}")
                self.bus.emit(LIVE_STARTED, folder=folder)
                return None
            if time.monotonic() >= deadline:
                self._kill(process)
                return f"Ассистент не запустился за {self._start_timeout:.0f} с"
            time.sleep(self._poll_s)
        return None

    # --- прокси к ребёнку -----------------------------------------------

    def _active_port(self) -> int:
        with self._lock:
            if not self._active or self._port is None or self._stop_requested:
                raise LiveNotRunning("Ассистент не запущен")
            return self._port

    @staticmethod
    def _request(port: int, path: str, payload: dict, timeout: float) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            try:
                text = e.read().decode("utf-8", errors="replace").strip()
            finally:
                e.close()
            if 400 <= e.code < 500:
                raise LiveError(text or f"ассистент ответил {e.code}") from None
            raise RuntimeError(f"ассистент ответил {e.code}: {text}") from None
        except OSError as e:
            raise RuntimeError(f"ассистент не отвечает: {e}") from None
        return json.loads(raw) if raw.strip() else {}

    def ask(self, question: str) -> dict:
        return self._request(self._active_port(), "/ask", {"question": question},
                             ASK_TIMEOUT_S)

    def task(self, text: str) -> dict:
        self._request(self._active_port(), "/task", {"task": text}, REQUEST_TIMEOUT_S)
        return {"ok": True}

    def open_events(self, last_event_id: str | None = None) -> "LiveStream":
        """Подписка на поток ассистента для ретрансляции клиенту панели."""
        port = self._active_port()
        stream = LiveStream(port, last_event_id, on_close=self._forget)
        with self._lock:
            alive = self._active and self._port == port
            if alive:
                self._streams.add(stream)
        if not alive:  # живой режим кончился, пока подключались
            stream.close()
            raise LiveNotRunning("Ассистент не запущен")
        return stream

    def _forget(self, stream) -> None:
        with self._lock:
            self._streams.discard(stream)


_EOF = b""


class LiveStream:
    """SSE ассистента, разобранный на события `state`/`line`.

    Читает отдельный поток: чтение сокета блокируется, пока ассистент молчит,
    а обработчику панели нужно и слать keepalive, и замечать, что клиент ушёл.
    `get(timeout)` → байты события, None (тишина) или b"" (поток кончился).
    `close()` рвёт соединение с ассистентом (shutdown будит блокированный
    recv) и дожидается читающего потока — ничего не утекает."""

    def __init__(self, port: int, last_event_id: str | None, on_close=None) -> None:
        self._on_close = on_close
        self._queue: queue.Queue = queue.Queue()
        self._closed = threading.Event()
        self._close_lock = threading.Lock()
        self._conn =http.client.HTTPConnection("127.0.0.1", port,
                                                timeout=REQUEST_TIMEOUT_S)
        headers = {"Accept": "text/event-stream"}
        if last_event_id:
            headers["Last-Event-ID"] = str(last_event_id)
        try:
            self._conn.request("GET", "/events", headers=headers)
            self._sock = self._conn.sock
            self._response = self._conn.getresponse()
            if self._response.status != 200:
                raise RuntimeError(f"ассистент ответил {self._response.status}")
            # Дальше чтение без таймаута: тишина ассистента — норма, а
            # разбудит блокированное чтение shutdown из close().
            self._sock.settimeout(None)
        except Exception:
            self._conn.close()
            raise
        self._thread = threading.Thread(target=self._read, name=RELAY_THREAD,
                                        daemon=True)
        self._thread.start()

    def _read(self) -> None:
        block: list[bytes] = []
        name = None
        try:
            while not self._closed.is_set():
                line = self._response.readline()
                if not line:
                    break
                if line.strip(b"\r\n") == b"":
                    if block and name in RELAYED_EVENTS:
                        self._queue.put(b"".join(block) + b"\n")
                    block, name = [], None
                    continue
                if line.startswith(b":"):
                    continue  # комментарий-пинг: клиенту ни к чему
                if line.startswith(b"event:"):
                    name = line[6:].strip().decode("utf-8", errors="replace")
                block.append(line)
        except (OSError, ValueError, http.client.HTTPException):
            pass  # оборвали из close() или ассистент умер
        finally:
            self._queue.put(_EOF)

    def get(self, timeout: float | None = None) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            if self._closed.is_set() and not self._thread.is_alive():
                return _EOF
            return None

    def close(self) -> None:
        # Зовут двое: обработчик панели и LiveControl при конце живого режима.
        with self._close_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        try:
            self._response.close()
        finally:
            self._conn.close()
        if self._on_close is not None:
            self._on_close(self)
