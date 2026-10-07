"""Живой режим под резидентом: дочерний `meet assist` и его жизненный цикл.

Ассистент (расшифровка окнами, дайджест, вопросы к модели) живёт **отдельным
процессом**: ему нужны aiohttp, модели распознавания и SDK провайдеров, а
резиденту — нет (минимальная установка для записи их не содержит). Поэтому
здесь только stdlib: subprocess, urllib и http.client.

Протокол с ребёнком (`python -m meet.cli assist --no-browser --port 0
--endpoint-file <data_dir>/live.json --out <папка записей> --parent-pid <pid>`):

* ребёнок атомарно пишет `{"port", "pid", "folder", "ready", "capturing",
  "stage"}` в файл эндпоинта, как только поднял свой сервер (за секунды), и
  переписывает его на каждом этапе старта: `stage` — что он сейчас делает
  («загружаю модель распознавания…»), `capturing` — звук уже пишется (своя
  запись или отвод чужой; для резидента это `active`), `ready` — модель
  распознавания загружена, ассистент слушает. Файл без `ready` (ребёнок
  старой версии) — готов сразу. Таймаут старта — не на весь старт, а на
  этап: идущий, хоть и медленный, старт (холодный диск, модель на
  процессоре) не убивают, застрявший — убивают с названием этапа в ошибке;
* `POST /stop` → `{"ok": true}` сразу, затем финализация (дорожки, снятие
  `.recording.lock`), удаление файла эндпоинта и выход с кодом 0. Застрявший
  вызов модели может держать процесс ещё до ~180 с уже после финализации —
  поэтому исчезнувший после `/stop` файл эндпоинта и есть сигнал «запись
  дописана»: остаток дерева добиваем (FINALIZE_GRACE_S), не дожидаясь. Не
  дописал к STOP_TIMEOUT_S — убиваем всё равно, но запись помечаем неполной;
* `--parent-pid` — pid резидента: умер он жёстко — ребёнок сам штатно
  дописывает запись и выходит. Если сирота всё же остался (ребёнок старой
  версии), новый резидент при старте находит его по файлу эндпоинта и шлёт
  `/stop` (`adopt_orphan`), а не удаляет файл;
* `GET /events` — SSE `state`/`qa`/`line` (у `line` — `id:`), понимает Last-Event-ID;
* POST'ы с чужим Origin ребёнок отвергает, а без Origin пускает — urllib
  Origin не ставит, и это нам и нужно.

Старт **не блокирует** вызывающего: `start()` сразу возвращает статус
`starting`, а ожидание эндпоинта, переход в `active` и наблюдение за процессом
идут в своём потоке и сообщаются в шину (`live.started`, `live.stopped`,
`live.failed`). Иначе HTTP-обработчик панели висел бы минуту загрузки модели.

Ассистент можно включить и посреди обычной записи (`start(..., attach=...)`):
тогда ребёнок (`--attach-to <папка записи> --tap-port <порт>`, токен отвода —
в окружении MEET_TAP_TOKEN) не берёт lock записи и не открывает устройства —
звук он получает из отвода резидента (`meet.pcm_tap`), пишет в папку идущей
записи только ленту и `live_state.json`. Остановка с `detach` («Выключить
ассистента», запись продолжается) помечает его сводку неполной. Расшифровку
такой записи ставит в очередь остановка самой записи, а не ассистента.

Вывод ребёнка уходит в `<data_dir>/logs/live.log` (не в pipe: непрочитанный
pipe заполнился бы и повесил ребёнка). Последняя непустая строка журнала —
текст ошибки, если ребёнок умер сам: «Подключите Claude Code…», «Запись уже
идёт…», последняя строка traceback'а. Любой конец ребёнка не по просьбе
(сбой до готовности, убийство по таймауту этапа, падение посреди встречи,
падение без единой строки — код вроде 0xC0000005) резидент дописывает в тот
же live.log строкой с причиной: молча исчезнувших ассистентов нет.

Старт, упавший до готовности, резидент один раз повторяет сам — кроме
ошибок, которые повтор не исправит (ребёнок выходит с кодом EXIT_FATAL:
нет провайдера, вход в Claude, занятая запись) и кроме своей записи, уже
начавшей писать дорожки (повтор начал бы другую папку). Подключённому к
записи для повтора нужен новый отвод (`attach["reopen"]`).
"""

import http.client
import json
import re
import os
import secrets
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
# Этап старта сменился: {"stage": текст или None, "ready": готов ли}.
LIVE_STAGE = "live.stage"
LIVE_STARTED = "live.started"
LIVE_STOPPING = "live.stopping"
LIVE_STOPPED = "live.stopped"
LIVE_FAILED = "live.failed"

ENDPOINT_NAME = "live.json"
LOG_NAME = "live.log"
LOG_MAX_BYTES = 1024 * 1024  # больше — прежний журнал уезжает в live.log.1

# До первого файла эндпоинта: интерпретатор, импорты, свой веб-сервер ребёнка.
START_TIMEOUT_S = 60.0
# Этап старта (загрузка модели, проверка входа) не сменился за это время —
# старт застрял. Холодная модель Whisper на процессоре с медленного диска —
# десятки секунд, поэтому с запасом.
STAGE_TIMEOUT_S = 120.0
START_MAX_S = 300.0  # весь старт целиком, как бы ни менялись этапы
MAX_START_ATTEMPTS = 2  # упавший до готовности старт — ещё одна попытка
# Код выхода ребёнка «повтор не поможет» (meet.assist.app.EXIT_FATAL). Не 3:
# 3 — код abort()/std::terminate у MSVC CRT, им падают и нативные библиотеки.
EXIT_FATAL = 78
STAGE_RETRY = "повторяю запуск после ошибки…"
STOP_TIMEOUT_S = 90.0  # финализация дорожек; дальше — убийство дерева
# Выход резидента (/shutdown, смерть оболочки, «Выход», обновление): запись к
# этому моменту уже сохранена её остановкой (ассистент резидента всегда
# подключён к записи), осталось лишь дописать хвост ленты и сводку. Даём ему
# столько, потом убиваем. Оболочка ждёт ответа /shutdown 70 с (api.rs,
# LONG_TIMEOUT): остановка записи (join до 60 с — в худшем случае, обычно
# доли секунды) + 10 + JOIN_SLACK_S укладываются в это с запасом.
SHUTDOWN_WAIT_S = 10.0
# Сверх дедлайна stop(wait=True) ждёт поток наблюдения ещё столько (убийство
# дерева и событие).
JOIN_SLACK_S = 5.0
STOPPED_MARK = "Остановлено:"  # run_assist печатает после удачного engine.stop()
# Файл эндпоинта исчез после /stop — ребёнок дописал запись (run_assist убирает
# его после engine.stop()). Даём ему столько, чтобы выйти самому и успеть
# напечатать ошибку финализации; дальше добиваем зависший вызов модели.
FINALIZE_GRACE_S = 2.0
STOP_ATTEMPTS = 2  # /stop не дошёл — ещё одна попытка, потом только дедлайн
TAP_TOKEN_ENV = "MEET_TAP_TOKEN"
# Токен доверенного вызывающего для маршрутов чата ребёнка (`POST /chat/attach`
# — путь к файлу): ребёнку в окружении, в заголовке `X-Meet-Token` — от нас.
CONTROL_TOKEN_ENV = "MEET_ASSIST_TOKEN"
CONTROL_TOKEN_HEADER = "X-Meet-Token"
# Чем кончился последний запуск (`status()["ended_by"]`): остановилась запись,
# к которой ассистент был подключён; его выключили («Выключить ассистента»);
# упал сам; остановили обычный ассистент. Трей по этому молчит, когда
# ассистент кончился вместе с записью (о ней — своё уведомление).
ENDED_RECORDING = "recording"
ENDED_DETACH = "detach"
ENDED_CRASH = "crash"
ENDED_STOP = "stop"  # токен отвода звука — ребёнку в окружении, не в argv
# Сводка ассистента в папке записи (meet.assist.live_state.LIVE_STATE_JSON) и
# причина неполноты, которую ставит резидент: ассистента убили, не дав ему
# финального прохода (хвост оборван новым ассистентом, дедлайном или выходом).
LIVE_STATE_JSON = "live_state.json"
TAIL_CUT = "tail_cut"
POLL_S = 0.1
REQUEST_TIMEOUT_S = 5.0
ASK_TIMEOUT_S = 240.0  # вопрос — вызов модели (у ребёнка до 180 с) плюс дослив окна
RELAY_THREAD = "meet-live-relay"
# `voices` — подписи голосов задним числом и спрятанные дубли (meet.assist.web);
# `chat_snapshot`, `chat`, `chat_partial`, `agent` — чат агента-участника (V4).
RELAYED_EVENTS = ("state", "line", "qa", "qa_partial", "voices",
                  "chat_snapshot", "chat", "chat_partial", "agent")
# Разбор файла, приложенного к чату (`/chat/attach`): у ребёнка до 90 с.
ATTACH_TIMEOUT_S = 100.0
ERROR_MAX_CHARS = 300
# Быстрые действия вопросов (`meet.assist.qa.QUICK`): «Что я пропустил?»,
# «Какие решения уже приняты?», «Что мне ответить?», «Кратко за 1 минуту».
QUICK_ACTIONS = ("missed", "decisions", "reply", "brief")
# `restore` — «Вернуть» в панели сразу после «Скрыть» (assist.live_state.restore).
HINT_ACTIONS = ("pin", "unpin", "dismiss", "restore")


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


def _sweep_temp() -> None:
    """Временные папки (meet.tempdirs) уже умерших процессов: ассистента,
    которого убили, задач. Сбой уборки — не повод мешать живому режиму."""
    from meet import tempdirs

    try:
        tempdirs.sweep_temp()
    except Exception:
        pass


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


def _fallback_of(info: dict) -> list[dict]:
    """`devices_fallback` из файла эндпоинта — только понятные записи."""
    items = info.get("devices_fallback")
    if not isinstance(items, list):
        return []
    return [{"kind": item.get("kind"), "name": str(item.get("name") or ""),
             "device": item.get("device")}
            for item in items
            if isinstance(item, dict) and item.get("kind") in ("mic", "output")]


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


def _endpoint_is(path: Path, pid: int) -> bool:
    """Файл эндпоинта ещё лежит и он — этого ребёнка (pid уже известен,
    psutil не нужен). Ребёнок убирает его, только когда дописал запись."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True  # недочитали (замена файла) — считаем, что ещё на месте
    return isinstance(data, dict) and data.get("pid") == pid


def _is_assist_process(pid) -> bool:
    """pid — живой `meet assist` в режиме ребёнка (или процесс внутри него:
    лаунчер venv'а запускает настоящий интерпретатор дочерним). Признак —
    `assist` и `--endpoint-file` в командной строке; psutil нет — не знаем,
    считаем, что нет (файл тогда уберётся как протухший)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import psutil

        proc = psutil.Process(pid)
        for p in [proc, *proc.parents()]:
            cmd = " ".join(p.cmdline())
            if "assist" in cmd and "--endpoint-file" in cmd:
                return True
    except Exception:
        return False
    return False


def _orphan_endpoint(path: Path) -> dict | None:
    """Файл эндпоинта, чей ребёнок ещё жив (резидент, который его запустил,
    умер жёстко). None — файла нет или он протух."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("port"):
        return None
    return data if _is_assist_process(data.get("pid")) else None


def _spawn_process(argv: list[str], log_file, extra_env: dict | None = None):
    """Запустить ребёнка без окна консоли, весь вывод — в журнал.

    Приоритет — обычный, и у ассистента, подключённого к обычной записи:
    с пониженным его старт (импорты, загрузка моделей) на процессоре, занятом
    звонком, растягивался за минуту. Подключённый понижает себя сам, когда
    готов (`meet.assist.app._lower_priority`): его догонялка грузит процессор
    минутами, а запись резидента и звонок тормозить не должны."""
    from meet import netproxy

    # Прокси из настроек: ребёнок зовёт Claude Code/Codex (см. meet.netproxy).
    env = {**netproxy.settings_env(), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
           **(extra_env or {})}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=flags,
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


def _run_output(path: Path | None, offset: int) -> str | None:
    """Вывод ребёнка за этот запуск (журнал с `offset`)."""
    if path is None:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _log_has(path: Path | None, offset: int, mark: str) -> bool:
    return mark in (_run_output(path, offset) or "")


def _last_line(path: Path | None, offset: int) -> str | None:
    """Последняя непустая строка вывода этого запуска."""
    text = _run_output(path, offset)
    if text is None:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:ERROR_MAX_CHARS] if lines else None


# Последняя строка вывода — ошибка: исключение Python («RuntimeError: …»,
# «gigaam.Unavailable: …» после Traceback) или текст отказа (код EXIT_FATAL).
_EXCEPTION_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b.*:")
TRACEBACK_MARK = "Traceback (most recent call last)"
# Последняя строка ребёнка при непредвиденном исключении (meet.assist.app.CRASH_MARK).
CRASH_MARK = "Ассистент упал: "


def _child_error(path: Path | None, offset: int, code) -> str:
    """Причина конца ребёнка для окна и уведомления. Последняя строка его
    вывода — только если она и есть ошибка: иначе это строка хода работы
    («старт: модель распознавания загружена — за 19.5 с»), а процесс упал
    молча (нативный сбой, снят диспетчером задач, нехватка памяти)."""
    text = _run_output(path, offset) or ""
    raw = [line.rstrip() for line in text.splitlines() if line.strip()]
    raw = [line for line in raw if not line.startswith("--- ")]  # строки резидента
    last = raw[-1].strip()[:ERROR_MAX_CHARS] if raw else None
    if last is not None:
        if last.startswith(CRASH_MARK):
            return last  # своя строка ребёнка: исключение одной строкой
        if code == EXIT_FATAL or _EXCEPTION_LINE.match(last) or _ends_traceback(raw):
            return last
    return f"Ассистент аварийно завершился (код {_code_text(code)})"


def _ends_traceback(lines: list[str]) -> bool:
    """Последняя строка — конец последнего traceback'а: после его заголовка
    идут только строки стека (с отступом), а за ними — она. Traceback где-то
    раньше, а потом строки хода работы — не в счёт."""
    starts = [i for i, line in enumerate(lines) if line.startswith(TRACEBACK_MARK)]
    if not starts:
        return False
    block = lines[starts[-1] + 1:-1]
    return all(line[:1] in (" ", "\t") for line in block) and lines[-1][:1] not in (" ", "\t")


def recording_goes_on(reason: str, *, ready: bool) -> str:
    """Сбой ассистента, подключённого к записи, — словами окна и трея: запись
    идёт дальше. Без двойного «Ассистент не запустился» в одной строке."""
    reason = (reason or "").strip()
    if "запись продолжается" in reason:
        return reason
    for head in ("Ассистент не запустился:", "Ассистент упал:"):
        if reason.startswith(head):
            reason = reason[len(head):].strip()
            break
    else:
        if reason.startswith("Ассистент "):
            # «Ассистент не запустился за 121 с: этап …», «… аварийно завершился (код …)».
            return f"{reason} — запись продолжается"
    what = "упал" if ready else "не запустился"
    return f"Ассистент {what} — запись продолжается: {reason}"


def _code_text(code) -> str:
    """Код выхода для человека: отрицательный или огромный (исключение
    Windows вроде 0xC0000005 — нарушение доступа) — шестнадцатеричным."""
    if isinstance(code, int) and (code < 0 or code > 0xFFFF):
        return f"0x{code & 0xFFFFFFFF:08X}"
    return str(code)


def _mark_cut(folder) -> bool:
    """Сводку убитого ассистента — неполной, как её пометил бы его финальный
    проход (`partial`, `partial_reasons`): то, что он сохранил по ходу встречи,
    цело, а последних реплик в ней нет. Сводки нет — не создаём (черновик из
    ничего хуже никакого). Процесса к этому моменту нет — гонки за файл нет.
    → пометили ли."""
    path = Path(folder) / LIVE_STATE_JSON
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    reasons = data.get("partial_reasons")
    reasons = [r for r in reasons if isinstance(r, str)] if isinstance(reasons, list) else []
    if TAIL_CUT not in reasons:
        reasons.append(TAIL_CUT)
    data["partial"] = True
    data["partial_reasons"] = reasons
    tmp = path.with_name(f"{path.name}.{os.getpid()}.cut.tmp")
    try:
        # Атомарно, как сам ассистент (live_state.save): итоги читают его когда угодно.
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def _note_log(path: Path | None, text: str) -> None:
    """Строка резидента в live.log: ребёнок мог умереть, не написав ни слова
    (убит по таймауту, упал в машинном коде), — причина остаётся рядом с его
    выводом. Сбой записи журнала — не повод мешать остальному."""
    if path is None:
        return
    stamp = time.strftime("%H:%M:%S")
    try:
        with open(path, "ab") as f:
            f.write(f"--- {stamp} резидент: {text}\n".encode("utf-8"))
    except OSError:
        pass


class LiveControl:
    """Один дочерний `meet assist` под присмотром резидента.

    `spawn(argv, log_file) -> Popen` и `kill(process)` подменяются в тестах;
    таймауты — тоже, чтобы тесты не ждали минуту.

    Порядок событий. Каждый переход состояния и его событие идут под
    `_emit_lock` (реентерабельным: подписчик вправе сам дёрнуть start/stop):
    иначе `live.stopped` прошлого запуска мог бы прийти после `live.starting`
    следующего, а `live.stopping` — после `live.stopped`. Шина при этом
    вызывается вне `_lock` состояния — подписчики спрашивают status()."""

    def __init__(self, bus, spawn=None, *, kill=None, log=None,
                 start_timeout: float = START_TIMEOUT_S,
                 stop_timeout: float = STOP_TIMEOUT_S,
                 finalize_grace: float = FINALIZE_GRACE_S,
                 poll_s: float = POLL_S,
                 stage_timeout: float = STAGE_TIMEOUT_S,
                 start_max: float = START_MAX_S,
                 max_attempts: int = MAX_START_ATTEMPTS) -> None:
        self.bus = bus
        self._spawn = spawn or _spawn_process
        self._kill = kill or _kill_tree
        # Токен доверенного вызывающего для чата ребёнка (на жизнь резидента).
        self._control_token = secrets.token_urlsafe(24)
        self._log = log or (lambda message: None)
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._finalize_grace = finalize_grace
        self._poll_s = poll_s
        self._stage_timeout = stage_timeout
        self._start_max = start_max
        self._max_attempts = max(1, int(max_attempts))
        self._ready = False  # модель загружена, ассистент слушает
        self._stage: str | None = None  # что ребёнок делает, пока не готов
        self._out_root = None
        self._attempt = 0  # какая это попытка старта (повтор после сбоя — вторая)
        # Старт не уложился в таймаут, а звук уже писался: остановлен штатно,
        # причина — эта (см. _await_ready).
        self._timeout_error: str | None = None
        # Запись остановлена — ассистент дописывает хвост в фоне.
        self._tail = False
        # Убит сразу (abort): запись отменена, ошибки нет.
        self._aborted = False
        self._lock = threading.Lock()
        self._emit_lock = threading.RLock()
        self._process = None
        self._thread: threading.Thread | None = None
        self._active = False
        self._port: int | None = None
        self._child_pid: int | None = None  # pid из файла эндпоинта
        self._folder: str | None = None
        self._error: str | None = None
        self._error_at: float | None = None
        # К какой записи относится `_error` (папка): хвост прошлой записи
        # может упасть, когда уже идёт другая, — окно не показывает чужое.
        self._error_folder: str | None = None
        self._started_at: float | None = None
        # Выбранное в настройках устройство не нашлось — ассистент пишет с
        # системного (из файла эндпоинта ребёнка): [{"kind", "name", "device"}].
        self._fallback: list[dict] = []
        self._stop_requested = False
        self._stop_deadline: float | None = None
        self._streams: set = set()
        # Подключён к идущей обычной записи: {"folder", "server", "started_at"}
        # (server — meet.pcm_tap.TapServer, его закрываем в конце). None — сам пишет.
        self._attach: dict | None = None
        self._detach = False  # остановка — «Выключить ассистента», запись идёт дальше
        self._reason: str | None = None  # с чем просили остановиться (ENDED_*)
        self._ended_by: str | None = None  # чем кончился последний запуск

    # --- что показывать -------------------------------------------------

    def status(self) -> dict:
        """{"active", "starting", "stopping", "ready", "stage", "folder",
        "error", "started_at", "attached"}: active — звук уже пишется (своя
        запись или отвод чужой); ready — модель загружена, ассистент слушает;
        stage — этап старта, пока не готов («загружаю модель распознавания…»;
        None — ещё неизвестен); error — почему упал (или остановился с
        ошибкой) последний запуск, сбрасывается следующим стартом;
        error_folder — папка записи, к которой она относится (None — ни к
        какой: не запустился до папки); started_at —
        стенное время начала захвата звука (`live.started`; до него — None),
        для секундомера панели, а у подключённого к записи — начало самой
        записи; attached — включён посреди обычной записи."""
        with self._lock:
            return self._status_unlocked()

    def devices_fallback(self) -> list[dict]:
        """Подмены устройств у идущего ассистента; не идёт — пусто."""
        with self._lock:
            return [dict(f) for f in self._fallback] if self._active and not self._tail else []

    def busy(self) -> bool:
        """Идёт или поднимается — вторая запись сейчас невозможна. Ассистент,
        чья запись уже остановлена и сохранена (дописывает хвост в фоне), —
        не помеха: он не держит ни устройств, ни lock записи."""
        with self._lock:
            return self._process is not None and not self._tail

    def attached(self) -> bool:
        """Идёт (или поднимается) ассистент, подключённый к идущей записи."""
        with self._lock:
            return self._process is not None and self._attach is not None and not self._tail

    def finishing(self) -> bool:
        """Запись остановлена, а её ассистент дописывает хвост в фоне."""
        with self._lock:
            return self._process is not None and self._tail

    def _status_unlocked(self) -> dict:
        # Хвост ассистента остановленной записи — уже не «идёт»: окно и трей
        # видят простой и дают начать новую запись; папку держим (`folder`),
        # чтобы её не удалили и не склеили, пока он пишет туда.
        running = self._process is not None and not self._tail
        tail = self._process is not None and self._tail
        return {"active": running and self._active, "starting": running and not self._active,
                "stopping": running and self._stop_requested,
                "ready": running and self._ready,
                "stage": self._stage if running and not self._ready else None,
                "folder": self._folder,
                "error": self._error, "error_at": self._error_at,
                "error_folder": self._error_folder if self._error else None,
                "started_at": self._started_at if running else None,
                "attached": running and self._attach is not None,
                "finishing": tail,
                # Подключён с самого начала «Записи с ассистентом» (а не
                # включён посреди обычной записи): трей о готовности молчит.
                "with_recording": running and bool((self._attach or {}).get("with_recording")),
                "ended_by": None if running else (ENDED_RECORDING if tail else self._ended_by)}

    def finish_after_recording(self) -> None:
        """Запись, к которой подключён ассистент, остановлена и сохранена.
        Его последний проход (хвост ленты, сводка — в папку той записи) идёт в
        фоне, до STOP_TIMEOUT_S; он ничего не блокирует: ни новую запись, ни
        автозапись. Новый ассистент сначала его обрывает (`end_tail`)."""
        with self._emit_lock:
            with self._lock:
                if self._process is None or self._attach is None or self._tail:
                    return
                self._tail = True
            self._log("запись сохранена — ассистент дописывает сводку в фоне")
            self.stop(reason=ENDED_RECORDING)

    def end_tail(self, timeout: float = 5.0) -> None:
        """Хвост прошлого ассистента мешает новому (один ребёнок за раз):
        дать ему `timeout` секунд, потом убить. Что он успел — в папке."""
        if self.finishing():
            self._log("новый ассистент — обрываю хвост прошлого")
            self.stop(wait=True, timeout=timeout)

    def abort(self, reason: str | None = None) -> None:
        """Убить ассистента сразу (запись отменена — её папку удаляют): ни
        /stop, ни ожидания сводки. Ждём только конца процесса."""
        with self._emit_lock:
            with self._lock:
                process, thread = self._process, self._thread
                if process is None:
                    return
                first = not self._stop_requested
                self._stop_requested = True
                self._aborted = True
                if first:
                    self._reason = reason
                self._stop_deadline = time.monotonic()
            if first:
                self.bus.emit(LIVE_STOPPING)
        self._log("ассистент остановлен сразу (запись отменена)")
        self._kill(process)
        if thread is not None and thread is not threading.current_thread() \
                and thread.ident is not None:
            thread.join(timeout=JOIN_SLACK_S)

    # --- старт и стоп ---------------------------------------------------

    def start(self, out_root, attach: dict | None = None) -> dict:
        """Запустить ассистента. Не ждёт загрузки модели: статус `starting`
        сразу, дальше — события `live.started` / `live.failed`.

        `attach` — включить посреди обычной записи: {"folder": папка записи,
        "server": TapServer отвода звука, "started_at": стенное время начала
        записи}. Сервер отвода закрывается, когда ассистент кончился (или не
        запустился)."""
        # Временные папки убитых раньше процессов (ассистент, задачи) — до старта.
        _sweep_temp()
        try:
            return self._start(out_root, attach)
        except BaseException:
            self._close_server(attach)
            raise

    @staticmethod
    def _close_server(attach: dict | None) -> None:
        server = (attach or {}).get("server")
        if server is not None:
            try:
                server.close()
            except Exception:
                pass

    def _start(self, out_root, attach: dict | None) -> dict:
        with self._emit_lock:
            with self._lock:
                if self._process is not None:
                    raise LiveBusy("Ассистент уже запущен")
                endpoint = endpoint_path()
                orphan = _orphan_endpoint(endpoint)
                if orphan is not None:
                    # Ребёнок прошлого резидента ещё пишет (или дописывает)
                    # встречу и держит lock записи: его файл не трогаем.
                    self._stop_orphan(orphan)
                    raise LiveBusy("Прошлый ассистент ещё дописывает запись — "
                                   "попробуйте через минуту")
                try:
                    endpoint.unlink(missing_ok=True)  # от убитого прошлого запуска
                except OSError:
                    pass
                argv = self._argv(out_root, attach)
                log_file = None
                try:
                    log_file, path, offset = _open_log()
                    process = self._spawn_with(argv, log_file, attach)
                except OSError as e:
                    self._error = f"Не удалось запустить ассистента: {e}"
                    self._error_at = time.time()
                    self._error_folder = str(attach["folder"]) if attach is not None else None
                    self._ended_by = ENDED_CRASH
                    process = None
                finally:
                    if log_file is not None:
                        log_file.close()  # у ребёнка свой дескриптор
                if process is not None:
                    self._process = process
                    self._active = False
                    self._port = None
                    self._child_pid = None
                    # Подключённый пишет в папку идущей записи — она известна сразу.
                    self._folder = str(attach["folder"]) if attach is not None else None
                    self._fallback = []
                    self._error = None
                    self._error_at = None
                    self._error_folder = None
                    self._started_at = None  # с live.started: прогрев модели не в счёт
                    self._stop_requested = False
                    self._stop_deadline = None
                    self._attach = attach
                    self._detach = False
                    self._reason = None
                    self._ended_by = None
                    self._ready = False
                    self._stage = None
                    self._timeout_error = None
                    self._tail = False
                    self._aborted = False
                    self._out_root = out_root
                    self._attempt = 1
                    self._thread = threading.Thread(
                        target=self._watch, args=(process, endpoint, path, offset),
                        name="meet-live", daemon=True)
                thread, error = self._thread, self._error
                reply = self._status_unlocked()
            if process is None:
                self._close_server(attach)
                self._log(f"ассистент: {error}")
                self.bus.emit(LIVE_FAILED, error=error, folder=None,
                              attached=attach is not None)
                return {"ok": False, **reply}
            self._log(f"ассистент запускается (pid {process.pid})"
                      + (f", подключается к записи {attach['folder']}" if attach else ""))
            self.bus.emit(LIVE_STARTING)
            # Поток — после `live.starting`: его started/failed не обгонят начало.
            thread.start()
        return {"ok": True, **reply}

    @staticmethod
    def _argv(out_root, attach: dict | None) -> list[str]:
        argv = [sys.executable, "-m", "meet.cli", "assist", "--no-browser",
                "--port", "0", "--endpoint-file", str(endpoint_path()),
                "--out", str(out_root), "--parent-pid", str(os.getpid())]
        if attach is not None:
            argv += ["--attach-to", str(attach["folder"]),
                     "--tap-port", str(attach["server"].port)]
        return argv

    def _spawn_with(self, argv: list[str], log_file, attach: dict | None):
        env = {CONTROL_TOKEN_ENV: self._control_token}
        if attach is not None:
            env[TAP_TOKEN_ENV] = attach["server"].token
        return self._spawn(argv, log_file, env)

    def adopt_orphan(self) -> bool:
        """Старт резидента: ребёнок прошлого (умершего жёстко) резидента ещё
        жив — штатно остановить его (`/stop` в фоне: старт не ждёт), файл
        эндпоинта он уберёт сам. Протухший файл — убрать. True — сирота был."""
        endpoint = endpoint_path()
        orphan = _orphan_endpoint(endpoint)
        if orphan is None:
            try:
                endpoint.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        self._log(f"ассистент прошлого запуска ещё жив (pid {orphan.get('pid')}, "
                  f"{orphan.get('folder')}) — останавливаю")
        self._stop_orphan(orphan)
        return True

    def _stop_orphan(self, orphan: dict) -> None:
        threading.Thread(target=self._send_stop, args=(int(orphan["port"]),),
                         name="meet-live-orphan", daemon=True).start()

    def stop(self, wait: bool = False, timeout: float | None = None,
             detach: bool = False, reason: str | None = None) -> dict:
        """Штатная остановка: `POST /stop` (не дошёл — ещё раз), конец — когда
        ребёнок дописал запись (исчез файл эндпоинта) или вышел. Не уложился
        в STOP_TIMEOUT_S (или в `timeout`, если он короче) — убийство дерева.

        `wait=True` — ждать конца (выход резидента, /shutdown); без него ответ
        сразу, а конец — событием `live.stopped`. `detach` — ассистента,
        подключённого к записи, выключают, а запись идёт дальше: его сводка
        помечается неполной. `reason` — ENDED_* для `status()["ended_by"]`."""
        with self._emit_lock:
            with self._lock:
                process, thread = self._process, self._thread
                if process is None:
                    return {"ok": False, "action": "not-live", **self._status_unlocked()}
                first = not self._stop_requested
                self._stop_requested = True
                if first:
                    self._detach = bool(detach and self._attach is not None)
                    self._reason = ENDED_DETACH if self._detach else reason
                detaching = self._detach
                port = self._port
                now = time.monotonic()
                deadline = now + self._stop_timeout
                if timeout is not None:
                    deadline = min(deadline, now + timeout)
                if self._stop_deadline is not None:
                    deadline = min(deadline, self._stop_deadline)
                self._stop_deadline = deadline
            if first:
                self.bus.emit(LIVE_STOPPING)
        if first:
            if port is None:
                # Ребёнок мог опубликовать порт в паузе опроса — прочитать ещё раз.
                info = _read_endpoint(endpoint_path(), process.pid)
                if info is not None:
                    port = int(info["port"])
                    with self._lock:
                        if self._port is None:
                            self._port, self._child_pid = port, info["pid"]
            if port is None:
                # Ещё не поднял даже веб: ни записи, ни сводки — ждать незачем.
                self._log("ассистент остановлен до старта")
                self._kill(process)
            else:
                self._send_stop(port, detaching)
        if wait and thread is not None and thread is not threading.current_thread():
            # start() запускает поток сразу после лока — дождаться этого мига.
            began = time.monotonic()
            while thread.ident is None and time.monotonic() - began < 2.0:
                time.sleep(0.01)
            if thread.ident is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()) + JOIN_SLACK_S)
        return {"ok": True, "action": "stopping", **self.status()}

    def _send_stop(self, port: int, detach: bool = False) -> None:
        """`POST /stop` с одной повторной попыткой. Не дошёл и он — не убиваем
        сразу: ребёнок мог быть занят, а убийство до финализации теряет хвост
        записи. Дальше решает дедлайн остановки."""
        for attempt in range(1, STOP_ATTEMPTS + 1):
            try:
                self._request(port, "/stop", {"detach": True} if detach else {},
                              REQUEST_TIMEOUT_S)
                self._log("ассистенту отправлена остановка")
                return
            except Exception as e:
                self._log(f"ассистент не принял остановку (попытка {attempt}): {e}")
        self._log("остановка не дошла — жду дедлайна, потом убью дерево процессов")

    # --- наблюдение -----------------------------------------------------

    def _watch(self, process, endpoint: Path, path: Path, offset: int) -> None:
        error = code = None
        killed = finalized = False
        try:
            while True:
                error, code, killed, finalized = self._follow(process, endpoint)
                retry = self._retry(process, endpoint, path, offset, error, code, killed)
                if retry is None:
                    break
                process, path, offset = retry
        except BaseException as e:  # noqa: BLE001 — поток наблюдения не умирает молча
            # Иначе _process остался бы занят до перезапуска резидента, а
            # окно не узнало бы ничего: убить ребёнка и сообщить.
            self._log(f"ассистент: сбой наблюдения: {type(e).__name__}: {e}")
            try:
                self._kill(process)
                process.wait(timeout=10)
            except Exception:
                pass
            error = f"Сбой резидента при наблюдении за ассистентом: {type(e).__name__}: {e}"
            code = process.poll()
        self._finish(process, endpoint, path, offset, error, code, killed, finalized)

    def _follow(self, process, endpoint: Path) -> tuple:
        """Один ребёнок от старта до выхода. → (ошибка старта, код выхода,
        убит ли по дедлайну, дописал ли запись после /stop)."""
        error = self._await_ready(process, endpoint)
        killed = False  # убит по дедлайну, не дописав запись
        finalized_at = None  # когда после /stop исчез файл эндпоинта
        while True:
            try:
                code = process.wait(timeout=self._poll_s)
                break
            except subprocess.TimeoutExpired:
                pass
            if killed:
                continue
            with self._lock:
                deadline, stopping = self._stop_deadline, self._stop_requested
                child_pid = self._child_pid
            now = time.monotonic()
            if finalized_at is None and stopping and child_pid is not None \
                    and not _endpoint_is(endpoint, child_pid):
                finalized_at = now
                continue
            if finalized_at is not None:
                if now - finalized_at >= self._finalize_grace:
                    # Запись дописана, а процесс держит застрявший вызов
                    # модели (до 180 с) — он больше ничего не сохранит.
                    self._log("ассистент дописал запись, но не вышел — добиваю")
                    self._kill(process)
                    killed = True
                continue
            if deadline is not None and now >= deadline:
                self._log("ассистент не дописал запись к дедлайну — "
                          "убиваю дерево процессов")
                self._kill(process)
                killed = True
        return error, code, killed, finalized_at is not None

    def _retry(self, process, endpoint: Path, path: Path, offset: int,
               error: str | None, code, killed: bool):
        """Старт упал до готовности — одна повторная попытка. → (новый
        процесс, журнал, смещение) или None — не повторяем: просили
        остановиться, ассистент уже слушал (падение посреди встречи — не
        старт), попытки кончились, ошибка из тех, что повтор не исправит
        (EXIT_FATAL), своя запись уже пишет дорожки (повтор начал бы другую
        папку), у подключённого к записи нет нового отвода."""
        with self._emit_lock:
            with self._lock:
                if self._stop_requested or self._ready or self._attempt >= self._max_attempts:
                    return None
                if code == EXIT_FATAL and not killed:
                    return None
                attach = self._attach
                if attach is None and self._active:
                    return None
                child_pid = self._child_pid
            reason = error or _child_error(path, offset, code)
            if attach is not None:
                reopen = attach.get("reopen")
                if reopen is None:
                    return None
                self._close_server(attach)  # отвод одноразовый: старый уже закрыт или не нужен
                try:
                    server = reopen()
                except Exception as e:
                    self._log(f"ассистент: отвод звука для повтора не открылся: {e}")
                    return None
                if server is None:
                    return None
                attach = {**attach, "server": server}
            self._drop_endpoint(endpoint, {process.pid, child_pid})
            _note_log(path, f"ассистент не запустился (код {_code_text(code)}): {reason} "
                            "— повторяю запуск")
            self._log(f"ассистент не запустился: {reason} — повторяю запуск")
            log_file = None
            try:
                log_file, new_path, new_offset = _open_log()
                new = self._spawn_with(self._argv(self._out_root, attach), log_file, attach)
            except OSError as e:
                self._log(f"ассистент: повторный запуск не удался: {e}")
                _note_log(path, f"повторный запуск не удался: {e}")
                self._close_server(attach)
                with self._lock:
                    self._attach = attach  # закроет _finish (уже закрыт — не страшно)
                return None
            finally:
                if log_file is not None:
                    log_file.close()
            with self._lock:
                self._process = new
                self._attach = attach
                self._attempt += 1
                self._active = False
                self._ready = False
                self._stage = STAGE_RETRY
                self._port = None
                self._child_pid = None
                self._folder = str(attach["folder"]) if attach is not None else None
                self._fallback = []
                self._started_at = None
            self.bus.emit(LIVE_STAGE, stage=STAGE_RETRY, ready=False)
        return new, new_path, new_offset

    def _finish(self, process, endpoint: Path, path: Path, offset: int,
                error: str | None, code, killed: bool, finalized: bool) -> None:
        self._drop_endpoint(endpoint, {process.pid, self._child_pid})
        _sweep_temp()
        with self._lock:
            cut_folder = self._folder if (killed and not finalized and self._attach is not None
                                          and not self._aborted) else None
        if cut_folder and _mark_cut(cut_folder):
            # До событий: кто их ждёт (название, итоги), читает уже с пометкой.
            _note_log(path, "ассистент убит до финального прохода — сводка помечена неполной")
            self._log(f"сводка ассистента помечена неполной (хвост оборван): {cut_folder}")
        with self._emit_lock:
            with self._lock:
                stop_requested = self._stop_requested
                active = self._active
                ready = self._ready
                folder = self._folder
                attach, detached, reason = self._attach, self._detach, self._reason
                timeout_error, self._timeout_error = self._timeout_error, None
                aborted, self._aborted = self._aborted, False
                if stop_requested or (code == 0 and active and error is None):
                    kind = LIVE_STOPPED
                    if not active:
                        error, complete = None, False  # остановлен до старта
                    elif killed and not finalized and attach is not None:
                        # Запись ведёт резидент — она цела; не успела сводка.
                        error = ("Ассистент не успел сохранить сводку — процесс убит, "
                                 "запись не затронута")
                        complete = False
                    elif killed and not finalized:
                        error = ("Ассистент не дописал запись за отведённое время — "
                                 "процесс убит, запись может быть неполной")
                        complete = False
                    elif not killed and code != 0:
                        # Вышел сам, но с ошибкой (например, упал финальный
                        # проход): дорожки на диске, запись оставляем и
                        # расшифровываем, причину показываем — из журнала.
                        error = _child_error(path, offset, code)
                        complete = True
                    elif killed and not _log_has(path, offset, STOPPED_MARK):
                        # Дописал (убрал эндпоинт), но «Остановлено:» не
                        # напечатал — engine.stop() упал, а процесс держал
                        # вызов модели и был добит: причину не прячем.
                        error, complete = _last_line(path, offset), True
                    else:
                        error, complete = None, True
                    if aborted:
                        error, complete = None, False  # запись отменена — сводка не нужна
                    if timeout_error:
                        # Остановлен резидентом: старт не уложился в таймаут.
                        error = timeout_error
                        if attach is not None:
                            kind, complete = LIVE_FAILED, False  # запись — резидента, идёт
                else:
                    kind = LIVE_FAILED
                    complete = False
                    error = error or _child_error(path, offset, code)
                if kind == LIVE_FAILED and attach is not None:
                    # Запись ведёт резидент, она идёт дальше — так и говорим.
                    error = recording_goes_on(error or "", ready=ready)
                # Причина — и в live.log (до смены состояния: кто дождался
                # конца, читает журнал уже с ней): ребёнок мог не написать ни слова.
                if kind == LIVE_FAILED:
                    what = "завершился посреди встречи" if ready else "не запустился"
                    _note_log(path, f"ассистент {what} (код {_code_text(code)}): {error}")
                elif error:
                    _note_log(path, f"ассистент остановлен с ошибкой: {error}")
                self._process = None
                self._thread = None
                self._tail = False
                self._active = False
                self._ready = False
                self._stage = None
                self._port = None
                self._folder = None
                self._fallback = []
                self._started_at = None
                self._stop_requested = False
                self._stop_deadline = None
                self._error = error
                # Когда появилась: окно отличает новую ошибку от прежней с тем же текстом.
                self._error_at = time.time() if error else None
                self._error_folder = folder if error else None
                if kind == LIVE_FAILED or timeout_error:
                    self._ended_by = ENDED_CRASH
                elif detached:
                    self._ended_by = ENDED_DETACH
                elif attach is not None:
                    # Подключённый кончается вместе с записью: её остановка
                    # (просьба резидента или конец отвода — вышел сам).
                    self._ended_by = ENDED_RECORDING
                else:
                    self._ended_by = reason or ENDED_STOP
                ended_by = self._ended_by
                self._attach = None
                self._detach = False
                self._reason = None
                streams = list(self._streams)
            for stream in streams:
                stream.close()
            self._close_server(attach)
            attached = attach is not None
            if kind == LIVE_STOPPED:
                self._log(f"ассистент {'выключен' if detached else 'остановлен'}: {folder}"
                          + (f" ({error})" if error else ""))
                # discarded — запись отменена (abort): её папку удаляют.
                self.bus.emit(LIVE_STOPPED, folder=folder, error=error,
                              complete=complete, attached=attached, detached=detached,
                              ended_by=ended_by, discarded=aborted)
            else:
                self._log(f"ассистент упал (код {_code_text(code)}): {error}")
                self.bus.emit(LIVE_FAILED, error=error, folder=folder, attached=attached,
                              ended_by=ended_by)

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

    def _await_ready(self, process, endpoint: Path) -> str | None:
        """Ждать, пока ребёнок будет готов, следя за этапами его старта.
        Возвращает текст ошибки таймаута или None (готов, вышел сам или его
        остановили).

        Первый файл эндпоинта — не дольше start_timeout; дальше каждый новый
        этап (или захват звука) даёт ещё stage_timeout, но весь старт — не
        дольше start_max."""
        began = time.monotonic()
        deadline = began + min(self._start_timeout, self._start_max)
        seen = None  # (ready, capturing, stage) последнего прочитанного файла
        while process.poll() is None:
            with self._lock:
                if self._stop_requested:
                    return None
            info = _read_endpoint(endpoint, process.pid)
            if info is not None:
                view = self._apply_endpoint(info)
                if view is None or view[0]:
                    return None  # остановлен до старта или готов
                if view != seen:
                    seen = view
                    deadline = min(began + self._start_max,
                                   time.monotonic() + self._stage_timeout)
            if time.monotonic() >= deadline:
                with self._lock:
                    stage = self._stage
                    graceful = self._port is not None and self._active
                waited = time.monotonic() - began
                text = f"Ассистент не запустился за {waited:.0f} с"
                if stage:
                    text += f": этап «{stage.rstrip('…. ')}» не закончился"
                if graceful:
                    # Звук уже пишется: не убиваем, а штатно останавливаем
                    # (/stop) — запись дописывается; убьёт только дедлайн остановки.
                    with self._lock:
                        self._timeout_error = text
                    self._log(f"{text} — останавливаю штатно")
                    self.stop()
                    return None
                self._kill(process)
                return text
            time.sleep(self._poll_s)
        return None

    def _apply_endpoint(self, info: dict) -> tuple | None:
        """Файл эндпоинта ребёнка → состояние и события. → (готов, пишет ли
        звук, этап) или None — остановка уже запрошена."""
        ready = info.get("ready", True) is not False  # без поля — старый ребёнок, готов
        capturing = bool(info.get("capturing", ready)) or ready
        stage = None if ready else (str(info.get("stage") or "").strip() or None)
        with self._emit_lock:
            with self._lock:
                if self._stop_requested:
                    return None
                if self._port is None:
                    # Порт — сразу: остановка во время загрузки штатная (/stop).
                    self._port = int(info["port"])
                    self._child_pid = info["pid"]
                started = capturing and not self._active
                if started:
                    self._active = True
                    # Подключённый к записи: секундомер панели — от начала записи.
                    attach_started = (self._attach or {}).get("started_at")
                    self._started_at = attach_started if isinstance(
                        attach_started, (int, float)) else time.time()
                    self._folder = str(info.get("folder") or "") or self._folder
                if self._active:
                    self._fallback = _fallback_of(info)
                changed = stage != self._stage or (ready and not self._ready)
                self._stage = stage
                if ready:
                    self._ready = True
                folder = self._folder
            if started:
                self._log(f"ассистент пишет звук: {folder}")
                self.bus.emit(LIVE_STARTED, folder=folder)
            if changed:
                if ready:
                    self._log(f"ассистент слушает встречу: {folder}")
                elif stage:
                    self._log(f"ассистент запускается: {stage}")
                self.bus.emit(LIVE_STAGE, stage=stage, ready=ready)
        return ready, capturing, stage

    # --- прокси к ребёнку -----------------------------------------------

    def _active_port(self) -> int:
        with self._lock:
            if not self._active or self._port is None or self._stop_requested:
                raise LiveNotRunning("Ассистент не запущен")
            return self._port

    @staticmethod
    def _request(port: int, path: str, payload: dict | None, timeout: float, *,
                 method: str = "POST", raw: bytes | None = None,
                 content_type: str = "application/json", headers: dict | None = None) -> dict:
        """Запрос к ребёнку: JSON (`payload`) или сырое тело (`raw`); ответ —
        JSON. 4xx — LiveError с текстом ребёнка, остальное — RuntimeError."""
        if raw is not None:
            body = raw
        elif payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body = None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                     method=method)
        if body is not None:
            req.add_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
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

    def ask(self, question: str, quick: str | None = None,
            since_t: float | None = None) -> dict:
        """Вопрос ассистенту; `quick` — быстрое действие, `since_t` — с какой
        секунды записи считать «пропущенное»."""
        payload: dict = {"question": question}
        if quick is not None:
            payload["quick"] = quick
        if since_t is not None:
            payload["since_t"] = since_t
        return self._request(self._active_port(), "/ask", payload, ASK_TIMEOUT_S)

    def hint(self, hint_id: str, action: str) -> dict:
        """Закрепить, открепить или скрыть подсказку ассистента."""
        return self._request(self._active_port(), "/hint",
                             {"id": hint_id, "action": action}, REQUEST_TIMEOUT_S)

    def task(self, text: str) -> dict:
        self._request(self._active_port(), "/task", {"task": text}, REQUEST_TIMEOUT_S)
        return {"ok": True}

    # --- чат агента-участника (V4) ---------------------------------------

    def chat(self, limit: int | None = None) -> dict:
        """Лента чата (`GET /chat`): `{"messages", "seq", "agent", "partial"}`."""
        path = "/chat" if limit is None else f"/chat?limit={int(limit)}"
        return self._request(self._active_port(), path, None, REQUEST_TIMEOUT_S, method="GET")

    def chat_post(self, payload: dict) -> dict:
        """Сообщение пользователя (`POST /chat`, ответ сразу — 202)."""
        return self._request(self._active_port(), "/chat", payload, REQUEST_TIMEOUT_S)

    def chat_paste(self, data: bytes, content_type: str, name: str | None = None) -> dict:
        """Вставленная картинка — тем же телом ребёнку (`POST /chat/paste`)."""
        from urllib.parse import quote

        headers = {"X-File-Name": quote(name, safe="")} if name else {}
        return self._request(self._active_port(), "/chat/paste", None, ATTACH_TIMEOUT_S,
                             raw=data, content_type=content_type, headers=headers)

    def chat_attach(self, path: str) -> dict:
        """Файл или папка с диска (`POST /chat/attach`, с токеном доверенного
        вызывающего); разбор — у ребёнка, до ATTACH_TIMEOUT_S."""
        return self._request(self._active_port(), "/chat/attach", {"path": path},
                             ATTACH_TIMEOUT_S,
                             headers={CONTROL_TOKEN_HEADER: self._control_token})

    def chat_click(self, mid: str, payload: dict) -> dict:
        return self._request(self._active_port(), f"/chat/{mid}/click", payload,
                             REQUEST_TIMEOUT_S)

    def chat_react(self, mid: str, payload: dict) -> dict:
        return self._request(self._active_port(), f"/chat/{mid}/react", payload,
                             REQUEST_TIMEOUT_S)

    def chat_stop(self, payload: dict) -> dict:
        return self._request(self._active_port(), "/chat/stop", payload, REQUEST_TIMEOUT_S)

    def chat_remove(self, aid: str) -> dict:
        """Вложение убрали из строки ввода до отправки."""
        return self._request(self._active_port(), f"/chat/attachments/{aid}/remove", {},
                             REQUEST_TIMEOUT_S)

    def agent_frequency(self, key: str) -> dict:
        """«Как часто писать» — агенту идущей встречи; в настройки сохраняет
        резидент сам (`persist: false`)."""
        return self._request(self._active_port(), "/agent/frequency",
                             {"frequency": key, "persist": False}, REQUEST_TIMEOUT_S,
                             method="PUT")

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
        self._conn = http.client.HTTPConnection("127.0.0.1", port,
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
