"""Отвод звука идущей записи (PCM tap) для ассистента, включённого посреди неё.

Обычную запись ведёт резидент (`meet.recorder` в своём потоке). Ассистент —
дочерний `meet assist` — второй раз устройства не открывает: он получает тот
же поток, что уходит в ffmpeg дорожек, через локальный сокет.

* `TapHub` живёт в резиденте рядом с записью. Аудио-callback дорожки отдаёт
  ему ровно те байты, что пишет в файл (после приведения к формату дорожки), и
  доливку тишины — с позицией в байтах от начала дорожки. Хаб только кладёт
  ссылку на `bytes` в очередь подписчика: без ожидания, без копий, любой сбой
  глотается. Файлы записи от отвода не зависят ни на бит.
* `TapServer` — сокет на 127.0.0.1 с одноразовым токеном и одним клиентом:
  заголовок JSON-строкой (форматы и позиции дорожек), затем кадры
  `FRAME` + данные. Конец записи — конец потока (EOF).
* `TapClient` — сторона ассистента: подключиться, прочитать заголовок, читать
  кадры.

Клиент не успевает читать — очередь ограничена (QUEUE_MAX_BYTES): старые кадры
выбрасываются, а клиент по позиции следующего кадра видит дыру и доливает её
тишиной. Ассистент упал — сокет рвётся, подписчик снимается; запись идёт.

Только stdlib: резиденту для записи numpy не нужен.
"""

import hmac
import json
import secrets
import socket
import struct
import threading
from collections import deque

# Кадр: номер дорожки, флаги, позиция начала данных (байты от начала дорожки
# в её формате), длина данных.
FRAME = struct.Struct("<BBQI")
FLAG_PAD = 1  # доливка тишины по часам, а не звук устройства
# ~40 с звука обеих дорожек 48 кГц стерео: клиент, который не читает дольше,
# теряет старые кадры (дыра → тишина у клиента), а память резидента не растёт.
QUEUE_MAX_BYTES = 16 * 1024 * 1024
TOKEN_MAX = 128
HELLO_TIMEOUT_S = 5.0  # клиент присылает токен сразу после подключения
READY_TIMEOUT_S = 5.0  # дорожки записи открываются за доли секунды
TAKE_WAIT_S = 1.0
VERSION = 1


class TapError(Exception):
    """Отвод недоступен: запись не идёт, токен не тот, резидент отказал."""


class _Subscriber:
    """Очередь кадров одного клиента. `offer` зовут из аудио-callback'а под
    локом хаба: короткий лок и append — и ничего больше."""

    def __init__(self, max_bytes: int = QUEUE_MAX_BYTES) -> None:
        self._max = max_bytes
        self._queue: deque = deque()
        self._bytes = 0
        self._cond = threading.Condition()
        self._done = False
        self.dropped = 0

    def offer(self, index: int, flags: int, pos: int, data: bytes) -> None:
        with self._cond:
            if self._done:
                return
            self._queue.append((index, flags, pos, data))
            self._bytes += len(data)
            while self._bytes > self._max and len(self._queue) > 1:
                _, _, _, old = self._queue.popleft()
                self._bytes -= len(old)
                self.dropped += 1
            self._cond.notify()

    def finish(self) -> None:
        with self._cond:
            self._done = True
            self._cond.notify_all()

    def take(self, timeout: float = TAKE_WAIT_S) -> list | None:
        """Всё накопленное (может быть пусто — тишина в ожидании); None — конец
        записи и очередь пуста."""
        with self._cond:
            if not self._queue and not self._done:
                self._cond.wait(timeout)
            if not self._queue:
                return None if self._done else []
            items = list(self._queue)
            self._queue.clear()
            self._bytes = 0
            return items


class _Feed:
    """Сторона записи одной сессии (`TapHub.begin`). Хаб живёт дольше записи:
    поток прошлой записи, который ещё дописывает дорожки (join истёк), своими
    `push`/`end` не должен ни испортить позиции новой записи, ни оборвать её
    подписчиков — вызовы чужой сессии хаб молча пропускает."""

    def __init__(self, hub: "TapHub", generation: int) -> None:
        self._hub = hub
        self._gen = generation

    def configure(self, index: int, name: str, rate: int, channels: int) -> None:
        self._hub.configure(index, name, rate, channels, generation=self._gen)

    def push(self, index: int, data: bytes, pos: int, pad: bool = False) -> None:
        self._hub.push(index, data, pos, pad, generation=self._gen)

    def end(self) -> None:
        self._hub.end(generation=self._gen)


class TapHub:
    """Точка, куда запись отдаёт звук дорожек. Без подписчиков `push` — лок и
    пустой цикл. Сессия записи: `begin(n)` → `configure()` каждой дорожки →
    `push()`… → `end()` (через `_Feed` сессии, который вернул `begin`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Condition(self._lock)
        self._tracks: dict[int, dict] = {}
        self._expected = 0
        self._open = False
        self._subs: list[_Subscriber] = []
        self._generation = 0

    # --- сторона записи ---------------------------------------------------

    def _current(self, generation: int | None) -> bool:
        return generation is None or generation == self._generation

    def begin(self, tracks: int) -> _Feed:
        with self._lock:
            for sub in self._subs:
                sub.finish()
            self._subs = []
            self._tracks = {}
            self._expected = int(tracks)
            self._open = True
            self._generation += 1
            return _Feed(self, self._generation)

    def configure(self, index: int, name: str, rate: int, channels: int, *,
                  generation: int | None = None) -> None:
        """Формат дорожки — у ffmpeg он один на всю запись."""
        with self._lock:
            if not self._current(generation):
                return
            self._tracks[index] = {"index": index, "name": name, "rate": int(rate),
                                   "channels": int(channels), "next": 0}
            self._ready.notify_all()

    def push(self, index: int, data: bytes, pos: int, pad: bool = False, *,
             generation: int | None = None) -> None:
        """Из аудио-callback'а: никогда не бросает и не ждёт."""
        try:
            with self._lock:
                if not self._current(generation):
                    return
                track = self._tracks.get(index)
                if track is not None:
                    track["next"] = pos + len(data)
                for sub in self._subs:
                    sub.offer(index, FLAG_PAD if pad else 0, pos, data)
        except Exception:
            pass

    def end(self, *, generation: int | None = None) -> None:
        """Запись кончилась: у подписчиков — EOF, когда дочитают."""
        with self._lock:
            if not self._current(generation):
                return
            self._open = False
            for sub in self._subs:
                sub.finish()
            self._subs = []
            self._ready.notify_all()

    # --- сторона ассистента -----------------------------------------------

    def active(self) -> bool:
        with self._lock:
            return self._open

    def subscribe(self, timeout: float = READY_TIMEOUT_S) -> tuple[_Subscriber, list[dict]]:
        """Подписаться с текущего места. → (подписчик, дорожки с `pos` —
        позицией, с которой пойдут его кадры). Запись не идёт — TapError."""
        with self._lock:
            ready = self._ready.wait_for(
                lambda: not self._open or len(self._tracks) >= max(1, self._expected), timeout)
            if not self._open:
                raise TapError("Запись не идёт")
            if not ready:
                raise TapError("Дорожки записи ещё не открылись")
            sub = _Subscriber()
            self._subs.append(sub)
            tracks = [{"index": t["index"], "name": t["name"], "rate": t["rate"],
                       "channels": t["channels"], "pos": t["next"]}
                      for t in sorted(self._tracks.values(), key=lambda t: t["index"])]
            return sub, tracks

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
        sub.finish()

    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


class TapServer:
    """Сокет отвода для одного ассистента. Порт — `port`, токен — `token`
    (одноразовый, сравнение за постоянное время). Подключения без верного
    токена закрываются; первое верное обслуживается до конца записи или
    обрыва, после чего сервер закрывается сам."""

    def __init__(self, hub: TapHub, *, host: str = "127.0.0.1", log=None) -> None:
        self.hub = hub
        self.token = secrets.token_hex(16)
        self._log = log or (lambda message: None)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind((host, 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._closed = threading.Event()
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()
        self.served = threading.Event()  # клиент подключился и получил заголовок
        self._thread = threading.Thread(target=self._serve, name="meet-tap", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            while not self._closed.is_set():
                try:
                    conn, _ = self._sock.accept()
                except OSError:
                    return  # сокет закрыли (close())
                if self._hello(conn):
                    self._stream(conn)
                    return
        finally:
            self.close()

    def _hello(self, conn: socket.socket) -> bool:
        """Токен первой строкой. Неверный — закрыть и ждать следующего."""
        try:
            conn.settimeout(HELLO_TIMEOUT_S)
            raw = b""
            while b"\n" not in raw and len(raw) <= TOKEN_MAX:
                chunk = conn.recv(TOKEN_MAX + 1)
                if not chunk:
                    break
                raw += chunk
            given = raw.split(b"\n", 1)[0].strip().decode("ascii", errors="replace")
            if given and hmac.compare_digest(given, self.token):
                return True
            self._log("отвод звука: подключение с неверным токеном — закрыто")
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass
        return False

    @staticmethod
    def _send_line(conn: socket.socket, data: dict) -> None:
        conn.sendall(json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n")

    def _stream(self, conn: socket.socket) -> None:
        with self._lock:
            if self._closed.is_set():
                conn.close()
                return
            self._conn = conn
        sub = None
        try:
            conn.settimeout(None)
            try:
                sub, tracks = self.hub.subscribe()
            except TapError as e:
                self._send_line(conn, {"error": str(e)})
                return
            self._send_line(conn, {"version": VERSION, "tracks": tracks})
            self.served.set()
            while not self._closed.is_set():
                items = sub.take()
                if items is None:
                    return  # запись кончилась — EOF клиенту
                for index, flags, pos, data in items:
                    conn.sendall(FRAME.pack(index, flags, pos, len(data)))
                    conn.sendall(data)
        except OSError:
            pass  # клиент ушёл (ассистент выключен или упал) — запись не касается
        finally:
            if sub is not None:
                self.hub.unsubscribe(sub)
                if sub.dropped:
                    self._log(f"отвод звука: ассистент не успевал читать — "
                              f"пропущено кадров: {sub.dropped}")
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            conn = self._conn
        try:
            self._sock.close()
        except OSError:
            pass
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)


class TapClient:
    """Сторона ассистента: подключиться к отводу резидента."""

    def __init__(self, port: int, token: str, *, host: str = "127.0.0.1",
                 timeout: float = READY_TIMEOUT_S + HELLO_TIMEOUT_S) -> None:
        self._sock = socket.create_connection((host, int(port)), timeout=timeout)
        try:
            self._sock.sendall(token.encode("ascii") + b"\n")
            self._file = self._sock.makefile("rb")
            line = self._file.readline(64 * 1024)
            if not line:
                raise TapError("Резидент закрыл отвод звука")
            header = json.loads(line.decode("utf-8"))
            if not isinstance(header, dict):
                raise TapError("Непонятный ответ отвода звука")
            if header.get("error"):
                raise TapError(str(header["error"]))
            tracks = header.get("tracks")
            if not isinstance(tracks, list) or not tracks:
                raise TapError("Отвод звука без дорожек")
            self.tracks: list[dict] = tracks
            # Дальше чтение без таймаута: тишину доливает запись, а разбудит
            # блокированное чтение close().
            self._sock.settimeout(None)
        except BaseException:
            self._sock.close()
            raise

    def _exact(self, n: int) -> bytes | None:
        data = self._file.read(n)
        if data is None or len(data) < n:
            return None
        return data

    def frames(self):
        """(дорожка, доливка тишины, позиция в байтах, данные) до конца записи
        или close()."""
        while True:
            try:
                head = self._exact(FRAME.size)
                if head is None:
                    return
                index, flags, pos, size = FRAME.unpack(head)
                data = self._exact(size) if size else b""
                if data is None:
                    return
            except (OSError, ValueError):
                return
            yield index, bool(flags & FLAG_PAD), pos, data

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
