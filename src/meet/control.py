"""Control API резидента: состояние записи, команды, настройки, диагностика.

Это тот слой, из-за отсутствия которого UI был невозможен: состояние записи
выяснялось сверкой pid в двух lock-файлах, а «стоп» передавался записью слова в
файл `command`. Здесь то же самое становится явным протоколом.

Владелец записи при этом не меняется: сервер живёт **внутри** резидентного трея,
в отдельном потоке, и только зовёт его методы. Второго владельца аудио не
появляется — иначе была бы гонка за устройство и за lock.

Почему stdlib `http.server`, а не aiohttp: резидент обязан оставаться без новых
зависимостей. Минимальная установка для записи на второй машине — один
`pyaudiowpatch` (README), и панель должна работать и там. Нагрузка здесь — панель
и окно настроек, то есть единицы соединений.

Доступ: bind только на 127.0.0.1, обязательный bearer-токен, проверка Origin.
Открытый локальный сервер означал бы, что любая открытая веб-страница может
включить запись микрофона.

IMPORTANT: токен принимается и в query-параметре `?token=`, не только в
заголовке. Браузерный EventSource не умеет ставить заголовки, а SSE — основной
канал состояния для панели.

Состояние — утиный объект (в тестах FakeState, в бою адаптер трея) со методами:

    bus                                  шина событий (meet.events.EventBus)
    snapshot() -> dict                   что показывать: статус, папка, уровни
    start_recording() -> dict            команды панели; возвращают, что вышло
    stop_recording(discard=False) -> dict
    adopt_recording() -> dict            автозапись → ручная (человек взял её)
    diagnostics(lines=200) -> dict       хвосты журналов для экрана диагностики
    settings() -> dict                   текущие настройки как есть
    patch_settings(updates) -> dict      частичное обновление, возвращает новые
    processes() -> dict                  запущенные процессы для выбора клиента
    devices() -> dict                    микрофоны и выводы для настроек «Звук»
    test_device(body) -> dict            ~2 с с устройства → пик (409 при записи)
    recordings(limit, q) -> dict         библиотека записей (q — поиск)
    delete_recording(id) -> dict         удалить запись
    get_hotwords() / put_hotwords(body)  список слов распознавания
    person(name) -> dict                 карточка человека
    recording(id) -> dict                одна запись
    transcript(id) -> dict               структурный транскрипт для редактора
    save_transcript(id, data) -> dict     правки редактора
    name_speakers(id, mapping) -> dict    имена спикеров + запись голосов в базу
    transcribe(id, options) -> dict       поставить расшифровку в очередь
    jobs() -> dict                        очередь задач
    submit_job(body) -> dict              новая задача
    cancel_job(id) -> dict                снять задачу
    track_path(id, track) -> Path | None  файл для плеера (playback — sys+mic вместе)
    engine() -> dict                      что установлено для расшифровки
    install_engine(options) -> dict       поставить движок задачей
    models() -> dict                      каталог моделей и что уже скачано
    download_model(body) -> dict          скачать модель задачей
    make_summary(id) -> dict              итоги записи задачей (409 без провайдера)
    summary(id) -> dict                   готовые итоги {"markdown", "created_at"}
    ask(id, body) -> dict                 вопрос по записи задачей
    qa(id) -> dict                        прошлые вопросы и ответы
    kb_export(id) -> dict                 выгрузить встречу в базу знаний (400 без папки)
    export_preview(params) -> dict        как назовётся папка встречи по шаблону
    assistant() -> dict                   кто отвечает, что установлено, папки
    check_provider(body) -> dict          проверить провайдера коротким вызовом
    live_start() -> dict                  живой режим (409 без провайдера, 400 при записи)
    live_stop() -> dict                   остановить живой режим (ответ сразу)
    live_ask(body) / live_task(body)      прокси к /ask и /task ассистента
    live_events(last_event_id) -> stream  поток ассистента: get(timeout), close()
"""

import json
import os
import queue
import re
import secrets
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from meet import paths

ENDPOINT_NAME = "daemon.json"

# Откуда разрешено ходить. tauri://localhost и (http|https)://tauri.localhost —
# оболочка приложения, localhost/127.0.0.1 — dev-сервер фронта и браузер. Запрос
# без Origin (curl, CLI) проходит: там нет чужой страницы, от которой мы
# защищаемся, а токен всё равно обязателен.
#
# IMPORTANT: сравнение точное (схема и хост целиком, порт по желанию), не по
# префиксу: префикс «http://localhost» пропускал http://localhost.evil.com.
ALLOWED_ORIGINS = frozenset({
    "tauri://localhost",
    "http://localhost",
    "http://127.0.0.1",
    "http://tauri.localhost",
    "https://tauri.localhost",
})
_ORIGIN_RE = re.compile(r"(?P<base>[a-z][a-z0-9+.-]*://[^:/?#\s]+)(?::\d{1,5})?")

# Плееру — кусками: ответ на Range не длиннее этого (файл открыт недолго),
# запись в сокет не дольше этого (плеер, переставший читать, файл не держит).
AUDIO_CHUNK = 2 * 1024 * 1024
AUDIO_WRITE_TIMEOUT_S = 15.0
SSE_KEEPALIVE_S = 15.0  # комментарий-пинг, чтобы прокси и клиент не заснули
SSE_QUEUE_MAX = 1000  # переполнение = клиент не читает; такого выкидываем

# Предпочтительный порт API. Фиксированный, чтобы адрес не менялся при каждом
# перезапуске резидента (иначе UI дёргает переподключение). 8765 занят
# страницей `meet assist`, поэтому берём следующий. Занят и он — откат на
# эфемерный, а фактический порт всё равно публикуется в daemon.json.
PREFERRED_PORT = 8766
TOKEN_NAME = "api.token"


def persisted_token() -> str:
    """Токен API, постоянный между запусками резидента.

    Меняющийся токен ломал переподключение UI не меньше, чем меняющийся порт:
    даже на фиксированном порту клиент был обязан перечитывать daemon.json.
    Храним один раз в `%LOCALAPPDATA%/meet/api.token` (папка и так приватна
    пользователю) и переиспользуем."""
    path = paths.data_dir() / TOKEN_NAME
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError:
        pass  # не записался — просто будет разовый токен на эту сессию
    return token


def endpoint_path() -> Path:
    """Где UI ищет адрес и токен резидента."""
    return paths.data_dir() / ENDPOINT_NAME


def write_endpoint(port: int, token: str, pid: int) -> Path:
    """Опубликовать адрес API. Файл лежит в LOCALAPPDATA пользователя, то есть
    и так недоступен другим пользователям машины; отдельных ACL не ставим."""
    path = endpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"port": port, "token": token, "pid": pid}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def read_endpoint() -> dict | None:
    """Адрес и токен резидента; нет файла или мусор — None."""
    try:
        data = json.loads(endpoint_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("port") else None


def clear_endpoint(pid: int | None = None) -> None:
    """Снять свою публикацию. Чужую не трогаем: за время работы файл мог
    перехватить другой резидент (та же логика, что у tray.lock)."""
    data = read_endpoint()
    if data is None:
        return
    if pid is not None and data.get("pid") != pid:
        return
    try:
        endpoint_path().unlink(missing_ok=True)
    except OSError:
        pass


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True  # CLI/curl: чужой страницы нет, а токен всё равно нужен
    match = _ORIGIN_RE.fullmatch(origin)
    return match is not None and match.group("base") in ALLOWED_ORIGINS


class _QuietServer(ThreadingHTTPServer):
    """HTTP-сервер, который не печатает traceback на закрытую панель.

    Клиент, закрывший вкладку или окно, роняет соединение посреди запроса —
    `socketserver` считает это ошибкой обработки и валит полный traceback в
    stderr. Для SSE это происходит при каждом закрытии панели, и в журнале
    резидента (а под треем — в никуда) копится мусор, за которым не видно
    настоящих сбоев.
    """

    log = staticmethod(lambda message: None)

    def handle_error(self, request, client_address) -> None:
        import sys
        import traceback

        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError,
                              BrokenPipeError, TimeoutError)):
            return  # клиент ушёл — это норма, а не сбой
        self.log(f"control API: {traceback.format_exc(limit=3).strip()}")


class ControlServer:
    """HTTP+SSE поверх состояния резидента. Живёт в своём потоке-демоне.

    Порт по умолчанию эфемерный (0): 8765 занят страницей `meet assist`, а
    фиксированный порт у резидента означал бы конфликт с чем угодно ещё.
    """

    def __init__(self, state, port: int = 0, token: str | None = None,
                 log=None, fallback: bool = False) -> None:
        self.state = state
        self.token = token or secrets.token_urlsafe(24)
        self.log = log or (lambda msg: None)
        self._port = port
        # fallback: если предпочтительный порт занят — откатиться на эфемерный,
        # а не остаться без API. Резидент это включает, тесты — нет.
        self._fallback = fallback
        self._httpd = self._make_server(port)
        self._thread: threading.Thread | None = None
        self._bound = False

    def _make_server(self, port: int) -> "_QuietServer":
        httpd = _QuietServer(
            ("127.0.0.1", port), _make_handler(self), bind_and_activate=False
        )
        httpd.daemon_threads = True  # висящий SSE не держит выход трея
        httpd.log = self.log
        return httpd

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self, *, publish: bool = True, pid: int | None = None) -> int:
        """Поднять сервер. Предпочтительный порт занят — откат на эфемерный
        (если разрешён), а не падение: адрес всё равно публикуется. Совсем не
        поднялся — резидент живёт без API, запись важнее панели."""
        try:
            self._httpd.server_bind()
            self._httpd.server_activate()
        except OSError as e:
            self._httpd.server_close()
            if self._fallback and self._port != 0:
                self.log(f"порт {self._port} занят — беру эфемерный ({e})")
                self._httpd = self._make_server(0)
                try:
                    self._httpd.server_bind()
                    self._httpd.server_activate()
                except OSError as e2:
                    self._httpd.server_close()
                    raise OSError(f"control API не поднялся: {e2}") from e2
            else:
                raise OSError(f"control API не поднялся: {e}") from e
        self._bound = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="meet-control", daemon=True
        )
        self._thread.start()
        if publish:
            write_endpoint(self.port, self.token, pid if pid is not None else os.getpid())
        self.log(f"control API: http://127.0.0.1:{self.port}/")
        return self.port

    def stop(self, *, pid: int | None = None) -> None:
        if self._bound:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            self._httpd.server_close()
            self._bound = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        clear_endpoint(pid)


# Сентинел: маршрут сам записал ответ потоком (SSE, Range-аудио, файл).
_STREAMED = object()


def _status_of(result) -> tuple[int, dict | None]:
    """Доменную ошибку `{"error": ...}` отдаём как 404, а не 200: клиент
    бросает только на !ok, и иначе действие, которое не выполнилось (нет
    записи, нет папки), выглядело бы как успех — панель думала бы, что
    расшифровка пошла."""
    if isinstance(result, dict) and set(result) == {"error"}:
        return 404, result
    return 200, result


def _make_handler(server: ControlServer):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1 — иначе часть клиентов рвёт keep-alive на каждом запросе.
        protocol_version = "HTTP/1.1"
        server_version = "meet-control"
        # Ссылка на владельца: маршруты объявлены модулем (читаются одним
        # списком), а состояние и токен живут в ControlServer.
        _control_server = server

        # --- инфраструктура ответа ---

        def log_message(self, fmt, *args) -> None:
            # Стандартный лог пишет в stderr, которого под pythonw нет; и он
            # шумит на каждый опрос состояния. Сообщения идут в журнал трея.
            pass

        def _send(self, status: int, payload: dict | None = None) -> None:
            body = b""
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            origin = self.headers.get("Origin")
            if origin and _origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "authorization,content-type")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _authorized(self, params: dict) -> bool:
            header = self.headers.get("Authorization", "")
            given = ""
            if header.startswith("Bearer "):
                given = header[7:].strip()
            elif params.get("token"):
                given = params["token"][0]
            return bool(given) and secrets.compare_digest(given, server.token)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0:
                return {}
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise BadRequest("ожидается JSON (UTF-8)")
            return data if isinstance(data, dict) else {}

        def _raw_body(self, limit: int = 10 * 1024 * 1024) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > limit:
                raise BadRequest("пустое или слишком большое тело (до 10 МБ)")
            return self.rfile.read(length)

        def _send_file(self, path, content_type: str) -> None:
            if path is None or not path.exists():
                self._send(404, {"error": "файла нет"})
                return
            data = path.read_bytes()  # ошибка диска -> 500 из _dispatch
            try:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                origin = self.headers.get("Origin")
                if origin and _origin_allowed(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.end_headers()
                self.wfile.write(data)
            except ConnectionError:
                pass  # клиент оборвал соединение посреди ответа

        # --- маршруты ---

        def do_OPTIONS(self) -> None:  # preflight фронта на dev-сервере
            self._send(204)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            params = parse_qs(url.query)
            if not _origin_allowed(self.headers.get("Origin")):
                self._send(403, {"error": "origin не разрешён"})
                return
            if not self._authorized(params):
                self._send(401, {"error": "нужен токен из daemon.json"})
                return
            path = url.path.rstrip("/") or "/"
            try:
                result = self._handle(method, path, params)
            except Conflict as e:
                self._send_safely(409, {"error": str(e)})
                return
            except BadRequest as e:
                self._send_safely(400, {"error": str(e)})
                return
            except Unavailable as e:
                self._send_safely(503, {"error": str(e)})
                return
            except Exception as e:
                # Резидент не должен падать из-за запроса от UI; ошибка диска
                # и т.п. — это 500 с текстом, а не молчаливый обрыв соединения.
                server.log(f"control API: {type(e).__name__}: {e}")
                self._send_safely(500, {"error": f"{type(e).__name__}: {e}"})
                return
            if result is _STREAMED:  # обработчик сам отдал байты
                return
            self._send_safely(*_status_of(result))

        def _send_safely(self, status: int, payload: dict | None = None) -> None:
            """Запись ответа: обрыв соединения клиентом — норма, не ошибка.
            OSError ловим только здесь, вокруг записи в сокет."""
            try:
                self._send(status, payload)
            except (ConnectionError, OSError):
                # Клиент закрыл вкладку посреди ответа; traceback только шумел бы.
                pass

        def _handle(self, method: str, path: str, params: dict):
            """Маршрутизация. Возвращает результат обработчика (dict) или
            _STREAMED, если ответ уже записан потоком."""
            route = (method, path)
            if route == ("GET", "/events"):
                self._stream_events()
                return _STREAMED
            if route == ("GET", "/live/events"):
                self._relay_live()
                return _STREAMED
            audio = re.match(r"^/recordings/([^/]+)/audio$", path)
            if method == "GET" and audio:
                # Не JSON: дорожка отдаётся байтами, с поддержкой Range —
                # без него плеер не умеет перематывать.
                self._stream_audio(unquote(audio.group(1)), params)
                return _STREAMED
            avatar = re.match(r"^/voices/([^/]+)/avatar$", path)
            if method == "GET" and avatar:
                self._send_file(server.state.avatar_path(unquote(avatar.group(1))),
                                "image/png")
                return _STREAMED
            handler = _ROUTES.get(route)
            if handler is not None:
                return handler(self, params)
            for pattern_method, pattern, pattern_handler in _PATTERNS:
                match = pattern.match(path)
                if match and pattern_method == method:
                    return pattern_handler(self, params, *match.groups())
            return {"error": f"нет маршрута {method} {path}"}

        def _stream_events(self) -> None:
            """SSE-поток событий записи и расшифровки.

            Очередь на клиента: издатель (вотчдог записи) не должен ждать, пока
            медленный клиент прочитает. Переполнение очереди означает, что
            клиент не читает вовсе — тогда честнее закрыть поток, чем расти в
            памяти резидента.
            """
            outbox: queue.Queue = queue.Queue(maxsize=SSE_QUEUE_MAX)
            overflow = threading.Event()

            def deliver(event) -> None:
                # Переполнение = клиент отстал. Молча терять события нельзя
                # (можно потерять record.stopped или job.done), поэтому взводим
                # флаг: поток отдаст свежий снимок и продолжит, а не потеряет
                # состояние навсегда. queue.Full глотает EventBus — ловим сами.
                try:
                    outbox.put_nowait(event)
                except queue.Full:
                    overflow.set()

            unsubscribe = server.state.bus.subscribe(deliver)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            origin = self.headers.get("Origin")
            if origin and _origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.end_headers()
            self.close_connection = True
            try:
                # Первым делом — снимок состояния: панель не должна ждать
                # первого события, чтобы что-то показать.
                self._write_sse("state", server.state.snapshot())
                while True:
                    if overflow.is_set():
                        # Клиент отставал и часть событий потеряна — отдаём
                        # свежий снимок, чтобы состояние снова стало верным.
                        overflow.clear()
                        self._write_sse("state", server.state.snapshot())
                    try:
                        event = outbox.get(timeout=SSE_KEEPALIVE_S)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self._write_sse(event.kind, event.to_dict())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                    OSError, queue.Full):
                pass  # клиент закрыл панель — штатный конец потока
            finally:
                unsubscribe()

        def _relay_live(self) -> None:
            """SSE ассистента, ретранслированный клиенту панели.

            Подписку открываем до заголовков: живого режима нет — 409 из
            _dispatch, а не пустой поток. Конец потока ассистента (остановка,
            падение) закрывает и наш; ушедший клиент замечаем на keepalive и
            закрываем соединение с ассистентом — читающий поток не утекает.
            Last-Event-ID клиента уходит ассистенту: переподключившаяся панель
            получает только пропущенные строки."""
            stream = server.state.live_events(self.headers.get("Last-Event-ID"))
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                origin = self.headers.get("Origin")
                if origin and _origin_allowed(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.end_headers()
                self.close_connection = True
                while True:
                    block = stream.get(timeout=SSE_KEEPALIVE_S)
                    if block is None:
                        self.wfile.write(b": keepalive\n\n")
                    elif not block:
                        break  # живой режим кончился
                    else:
                        self.wfile.write(block)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                    OSError):
                pass  # клиент закрыл панель — штатный конец потока
            finally:
                stream.close()

        def _stream_audio(self, recording_id: str, params: dict) -> None:
            """Отдать дорожку записи плееру карточки.

            С поддержкой `Range`: без неё браузерный плеер не перематывает и на
            длинной встрече качает файл целиком ради секунды в середине.

            IMPORTANT: ответ на Range — не больше `AUDIO_CHUNK` байт (206 с
            честным Content-Range; плеер дозапросит дальше), запись в сокет — с
            таймаутом. Открытый `bytes=0-` держал бы файл открытым, пока
            плеер не дочитает или не бросит соединение, а на Windows открытый
            файл не удалить: «Удалить» на открытой карточке сносил запись
            наполовину. Папка помечена занятой (`playback.using`) на время
            отдачи — удаление её дождётся.
            """
            from meet import playback

            track = (params.get("track") or ["sys"])[0]
            path = server.state.track_path(recording_id, track)
            if path is None:
                self._send(404, {"error": "дорожки нет"})
                return
            with playback.using(path.parent):
                self._send_audio(path)

        def _send_audio(self, path) -> None:
            size = path.stat().st_size
            start, end = 0, size - 1
            status = 200
            raw_range = self.headers.get("Range", "")
            match = re.match(r"bytes=(\d*)-(\d*)", raw_range)
            if match and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                    if match.group(2):
                        end = min(int(match.group(2)), size - 1)
                else:  # суффиксный запрос: последние N байт
                    start = max(0, size - int(match.group(2)))
                end = min(end, start + AUDIO_CHUNK - 1)
                status = 206
            # 416 уместен только для запроса с Range (status 206). Обычный GET
            # пустого файла (0 байт) должен вернуть пустой 200, а не 416.
            if status == 206 and start >= size:
                self._send(416, {"error": "запрошен кусок за концом файла"})
                return
            length = max(0, end - start + 1)
            previous = self.connection.gettimeout()
            self.connection.settimeout(AUDIO_WRITE_TIMEOUT_S)
            try:
                self.send_response(status)
                self.send_header("Content-Type", _audio_type(path))
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                origin = self.headers.get("Origin")
                if origin and _origin_allowed(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.end_headers()
                with path.open("rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(64 * 1024, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (ConnectionError, TimeoutError):
                # Плеер закрыл запрос/перемотал или перестал читать — норма.
                # Ошибки диска (OSError не из сокета) наружу: это 500, а не тишина.
                self.close_connection = True
            finally:
                try:
                    self.connection.settimeout(previous)
                except OSError:
                    pass

        def _write_sse(self, name: str, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False)
            self.wfile.write(f"event: {name}\ndata: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

    return Handler


class BadRequest(Exception):
    """400: запрос понятен по маршруту, но не по содержимому."""


class Conflict(Exception):
    """409: действие сейчас невозможно (например, не подключена модель)."""


class Unavailable(Exception):
    """503: нужное сейчас недоступно (устройство не ответило на проверку)."""


_BadRequest = BadRequest


# Маршруты вынесены из класса, чтобы читались одним списком. Каждый получает
# handler и разобранные query-параметры, возвращает то, что уйдёт в JSON.
_ROUTES = {
    ("POST", "/recordings/import"): lambda h, p: _server_of(h).state.import_file(
        h._body()
    ),
    ("POST", "/shutdown"): lambda h, p: _server_of(h).state.shutdown(),
    ("GET", "/"): lambda h, p: {"ok": True, "api": "meet-control"},
    ("GET", "/state"): lambda h, p: _server_of(h).state.snapshot(),
    ("GET", "/diagnostics"): lambda h, p: _server_of(h).state.diagnostics(
        lines=_int_param(p, "lines", 200)
    ),
    ("GET", "/settings"): lambda h, p: _server_of(h).state.settings(),
    ("GET", "/processes"): lambda h, p: _server_of(h).state.processes(),
    ("GET", "/devices"): lambda h, p: _server_of(h).state.devices(),
    ("POST", "/devices/test"): lambda h, p: _server_of(h).state.test_device(h._body()),
    ("PATCH", "/settings"): lambda h, p: _server_of(h).state.patch_settings(h._body()),
    ("POST", "/recording/start"): lambda h, p: _server_of(h).state.start_recording(),
    ("POST", "/recording/stop"): lambda h, p: _server_of(h).state.stop_recording(),
    ("POST", "/recording/cancel"): lambda h, p: _server_of(h).state.stop_recording(
        discard=True
    ),
    ("POST", "/recording/adopt"): lambda h, p: _server_of(h).state.adopt_recording(),
    ("POST", "/auto-record"): lambda h, p: _server_of(h).state.set_auto_record(h._body()),
    ("GET", "/recordings"): lambda h, p: _server_of(h).state.recordings(
        limit=_int_param(p, "limit", 200), q=(p.get("q") or [""])[0]
    ),
    ("GET", "/hotwords"): lambda h, p: _server_of(h).state.get_hotwords(),
    ("PUT", "/hotwords"): lambda h, p: _server_of(h).state.put_hotwords(h._body()),
    ("GET", "/voices"): lambda h, p: _server_of(h).state.people(),
    ("GET", "/jobs"): lambda h, p: _server_of(h).state.jobs(),
    ("GET", "/engine"): lambda h, p: _server_of(h).state.engine(),
    ("GET", "/models"): lambda h, p: _server_of(h).state.models(),
    ("GET", "/hf/status"): lambda h, p: _server_of(h).state.hf_status(),
    ("POST", "/hf/token"): lambda h, p: _server_of(h).state.set_hf_token(h._body()),
    ("DELETE", "/hf/token"): lambda h, p: _server_of(h).state.clear_hf_token(),
    ("POST", "/hf/check"): lambda h, p: _server_of(h).state.check_hf_token(h._body()),
    ("POST", "/models/download"): lambda h, p: _server_of(h).state.download_model(
        h._body()
    ),
    ("POST", "/engine/install"): lambda h, p: _server_of(h).state.install_engine(
        h._body()
    ),
    ("POST", "/jobs"): lambda h, p: _server_of(h).state.submit_job(h._body()),
    ("GET", "/assistant"): lambda h, p: _server_of(h).state.assistant(),
    ("POST", "/assistant/check"): lambda h, p: _server_of(h).state.check_provider(
        h._body()
    ),
    ("POST", "/live/start"): lambda h, p: _server_of(h).state.live_start(),
    ("POST", "/live/stop"): lambda h, p: _server_of(h).state.live_stop(),
    ("POST", "/live/ask"): lambda h, p: _server_of(h).state.live_ask(h._body()),
    ("POST", "/live/task"): lambda h, p: _server_of(h).state.live_task(h._body()),
    ("GET", "/export/preview"): lambda h, p: _server_of(h).state.export_preview(
        {k: v[0] for k, v in p.items() if k != "token" and v}
    ),
}

# Маршруты с параметром в пути. Регулярка, а не роутер: их считаные штуки, и
# лишний слой абстракции здесь читался бы хуже, чем сам список.
_PATTERNS = (
    ("GET", re.compile(r"^/recordings/([^/]+)$"),
     lambda h, p, rid: _server_of(h).state.recording(unquote(rid))),
    ("GET", re.compile(r"^/recordings/([^/]+)/transcript$"),
     lambda h, p, rid: _server_of(h).state.transcript(unquote(rid))),
    ("PUT", re.compile(r"^/recordings/([^/]+)/transcript$"),
     lambda h, p, rid: _server_of(h).state.save_transcript(unquote(rid), h._body())),
    ("GET", re.compile(r"^/recordings/([^/]+)/export$"),
     lambda h, p, rid: _server_of(h).state.export(
         unquote(rid), (p.get("format") or ["md"])[0])),
    ("PATCH", re.compile(r"^/recordings/([^/]+)$"),
     lambda h, p, rid: _server_of(h).state.update_recording(unquote(rid), h._body())),
    ("POST", re.compile(r"^/recordings/([^/]+)/speakers$"),
     lambda h, p, rid: _server_of(h).state.name_speakers(unquote(rid), h._body())),
    ("POST", re.compile(r"^/recordings/([^/]+)/transcribe$"),
     lambda h, p, rid: _server_of(h).state.transcribe(unquote(rid), h._body())),
    ("POST", re.compile(r"^/recordings/([^/]+)/summary$"),
     lambda h, p, rid: _server_of(h).state.make_summary(unquote(rid))),
    ("GET", re.compile(r"^/recordings/([^/]+)/summary$"),
     lambda h, p, rid: _server_of(h).state.summary(unquote(rid))),
    ("POST", re.compile(r"^/recordings/([^/]+)/ask$"),
     lambda h, p, rid: _server_of(h).state.ask(unquote(rid), h._body())),
    ("GET", re.compile(r"^/recordings/([^/]+)/qa$"),
     lambda h, p, rid: _server_of(h).state.qa(unquote(rid))),
    ("POST", re.compile(r"^/recordings/([^/]+)/kb-export$"),
     lambda h, p, rid: _server_of(h).state.kb_export(unquote(rid))),
    # «В заметки» прежнего окна — та же выгрузка в базу знаний.
    ("POST", re.compile(r"^/recordings/([^/]+)/notes$"),
     lambda h, p, rid: _server_of(h).state.kb_export(unquote(rid))),
    ("DELETE", re.compile(r"^/jobs/([^/]+)$"),
     lambda h, p, job_id: _server_of(h).state.cancel_job(job_id)),
    ("GET", re.compile(r"^/voices/([^/]+)/sample$"),
     lambda h, p, n: _server_of(h).state.person_sample(unquote(n))),
    ("PUT", re.compile(r"^/voices/([^/]+)/avatar$"),
     lambda h, p, n: _server_of(h).state.set_avatar(unquote(n), h._raw_body())),
    ("DELETE", re.compile(r"^/voices/([^/]+)/avatar$"),
     lambda h, p, n: _server_of(h).state.person_action(unquote(n), "clear-avatar")),
    ("POST", re.compile(r"^/voices/([^/]+)/rename$"),
     lambda h, p, n: _server_of(h).state.person_action(unquote(n), "rename", h._body())),
    ("POST", re.compile(r"^/voices/([^/]+)/merge$"),
     lambda h, p, n: _server_of(h).state.person_action(unquote(n), "merge", h._body())),
    ("DELETE", re.compile(r"^/recordings/([^/]+)$"),
     lambda h, p, rid: _server_of(h).state.delete_recording(unquote(rid))),
    ("GET", re.compile(r"^/voices/([^/]+)$"),
     lambda h, p, n: _server_of(h).state.person(unquote(n))),
    ("DELETE", re.compile(r"^/voices/([^/]+)$"),
     lambda h, p, n: _server_of(h).state.person_action(unquote(n), "delete")),
)

_AUDIO_TYPES = {
    ".opus": "audio/ogg", ".ogg": "audio/ogg", ".wav": "audio/wav",
    ".flac": "audio/flac", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
}


def _audio_type(path: Path) -> str:
    return _AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _server_of(handler) -> ControlServer:
    """ControlServer, которому принадлежит хендлер (замыкание _make_handler)."""
    return handler._control_server


def _int_param(params: dict, name: str, default: int) -> int:
    try:
        return int(params[name][0])
    except (KeyError, IndexError, ValueError):
        return default


def client_url(endpoint: dict | None = None) -> str | None:
    """Готовый адрес API для UI и CLI: None — резидент не публиковал себя."""
    data = endpoint or read_endpoint()
    if not data:
        return None
    return f"http://127.0.0.1:{data['port']}/"


def request(path: str, method: str = "GET", payload: dict | None = None,
            timeout: float = 5.0) -> dict:
    """Запрос к резиденту от имени CLI или скрипта.

    Резидента нет — RuntimeError с понятным текстом, а не пустой ответ: «трей не
    запущен» это нормальная ситуация, о которой надо сказать вслух.
    """
    data = read_endpoint()
    if not data:
        raise RuntimeError("резидент не запущен (нет daemon.json)")
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{data['port']}{path}", data=body, method=method
    )
    req.add_header("Authorization", f"Bearer {data.get('token', '')}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        raise RuntimeError(f"резидент ответил {e.code}: {detail or e.reason}") from e
    except OSError as e:
        # Файл мог остаться от упавшего резидента — та же болезнь, что у lock'ов.
        raise RuntimeError(f"резидент не отвечает: {e}") from e
    return json.loads(raw) if raw.strip() else {}


def alive(endpoint: dict | None = None, timeout: float = 0.5) -> bool:
    """Отвечает ли резидент. Проверяем соединением, а не только файлом: файл
    мог остаться от упавшего процесса (та же болезнь, что у lock-файлов)."""
    data = endpoint or read_endpoint()
    if not data:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(data["port"])), timeout):
            return True
    except OSError:
        return False
