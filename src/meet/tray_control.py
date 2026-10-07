"""Адаптер: резидентный трей как состояние control API.

Своей логики записи здесь нет — только перевод команд панели в методы трея и
сборка снимка состояния. Владелец записи остаётся один, и это трей: панель
ничего не пишет сама, иначе появился бы второй претендент на устройство и на
`.recording.lock`.

Снимок сознательно плоский и самодостаточный: панель должна уметь нарисоваться
по одному ответу, без дополнительных запросов.
"""

import functools
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from meet import (engine, events, gpu_lock, hotwords, jobs, library, live_control, paths,
                  settings, temp_meeting, watch)

# Источник записи. Константы живут здесь, а не в tray.py: адаптер не должен
# зависеть от модуля, который тянет pystray, — наоборот, tray импортирует их
# отсюда.
AUTO = "auto"
MANUAL = "manual"
LIVE = "live"  # запись с ассистентом (дочерний `meet assist`)

# Какой `source` пишется в meta.json записи по тому, кто её начал.
_META_SOURCE = {AUTO: "auto", LIVE: "live"}

# Поля, смена которых требует перезапуска резидента: секция auto_record читается
# один раз при старте (грейс запечён в Watcher, процессы — в Signals). Панель
# должна честно сказать это пользователю, а не делать вид, что применила.
RESTART_REQUIRED_SECTIONS = ("auto_record",)

TAIL_DEFAULT = 200
# Новый ассистент, а хвост прошлого (его запись уже сохранена) ещё пишется:
# столько ждём, потом обрываем — один ребёнок за раз.
TAIL_CUT_WAIT_S = 5.0
# Почему папку нельзя удалить или объединить, пока в неё пишет хвост ассистента.
LIVE_TAIL_BUSY = "ассистент дописывает сводку этой встречи — подождите немного"
# «Запись с ассистентом»: столько ждём, пока у только что начатой записи
# откроются устройства (её `record.started`), чтобы подключить к ней ассистента.
# Оболочка ждёт ответа /live/start и /live/attach 30 с (api.rs, LIVE_TIMEOUT):
# проверка провайдера (~1 с) + это + обрыв хвоста (TAIL_CUT_WAIT_S +
# live_control.JOIN_SLACK_S) + запуск ребёнка — около 22 с, с запасом.
LIVE_RECORD_WAIT_S = 10.0
# Временную встречу сохраняют как обычную: столько ждём хвост ассистента
# (он пишет в её папку), прежде чем перенести её в библиотеку. Оболочка ждёт
# ответа /recording/stop 70 с (api.rs, LONG_TIMEOUT).
KEEP_SETTLE_S = 30.0
# Выход резидента: столько ждём фоновый перенос сохраняемой временной встречи.
KEEP_SHUTDOWN_S = 5.0

PACKAGE = "meet-transcriber"


@functools.lru_cache(maxsize=1)
def app_version() -> str | None:
    """Версия установленного пакета meet — она же версия приложения
    (build_release.ps1 держит их одинаковыми). Оболочка по ней узнаёт резидент
    прежней версии, оставшийся после обновления, и заменяет его своим.
    Пакет не установлен (запуск из исходников без pip install) — None."""
    try:
        return importlib.metadata.version(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None

# Список устройств меняется редко, а каждый запрос — подпроцесс: кэшируем,
# чтобы открытая страница настроек не плодила их пачками.
DEVICES_TTL_S = 15.0
DEVICE_PROBE_TIMEOUT_S = 20.0
# «Проверить» в настройках звука: столько секунд пишет подпроцесс.
DEVICE_CHECK_S = 2.0


def _run_probe(args: list[str], failed: str, timeout: float) -> dict:
    """Спросить подпроцесс meet.devices_probe; ответ — его строка JSON, сбой —
    `{failed: False, "error": ...}`.

    Текст сбоя — для человека: таймаут — «Устройство не ответило», прочее —
    только тип исключения (в их тексте — командная строка и пути)."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "meet.devices_probe", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            # Ребёнок и так печатает ASCII-JSON; кодировка — чтобы и строки
            # ошибок не зависели от унаследованной консоли.
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {failed: False, "error": "Устройство не ответило"}
    except (OSError, subprocess.SubprocessError) as e:
        return {failed: False, "error": type(e).__name__}
    line = (out.stdout or "").strip().splitlines()
    try:
        return json.loads(line[-1]) if line else {
            failed: False, "error": (out.stderr or "нет ответа")[:300]}
    except ValueError:
        return {failed: False, "error": (out.stdout or "")[:300]}


def _probe_devices() -> dict:
    """Спросить устройства у подпроцесса (см. meet.devices_probe)."""
    return _run_probe([], "available", DEVICE_PROBE_TIMEOUT_S)


def _check_device(kind: str, name: str | None) -> dict:
    """Пару секунд записать с устройства подпроцессом — пиковый уровень."""
    args = ["--check", kind, "--seconds", str(DEVICE_CHECK_S)]
    if name:
        args += ["--name", name]
    return _run_probe(args, "ok", DEVICE_PROBE_TIMEOUT_S + DEVICE_CHECK_S)


def _tail(path: Path, lines: int) -> list[str]:
    """Последние строки файла. Читаем целиком: watch.log ротируется по 1 МБ, а
    record.log живёт одну встречу — обе величины на порядки меньше того, где
    имело бы смысл читать с конца."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


# Выбор провайдера (`llm.resolve`) спрашивает CLI о входе — до 20 с на каждый.
# HTTP-обработчик этого ждать не должен: считаем в фоне и держим ответ минуту.
PROVIDER_TTL_S = 60.0
CHECK_TIMEOUT_S = 90
QUESTION_MAX_CHARS = 4000
# Чат агента-участника (V4): сообщение не длиннее; вложений-ссылок в нём не больше.
CHAT_TEXT_MAX = 8000
CHAT_ATTACHMENTS_MAX = 10
CHAT_CLIENT_ID_MAX = 100
CHAT_PATH_MAX = 1024
CHAT_PASTE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp",
                    "image/tiff")
_CHAT_MESSAGE_ID = re.compile(r"m\d{1,9}")
_CHAT_ATTACHMENT_ID = re.compile(r"a\d{1,9}")
# Событие шины: чат записи после встречи изменился ({"id": папка записи,
# "partial"?: {"id", "text"}}) — окно перечитывает GET /recordings/{id}/chat.
CHAT_UPDATED = jobs.CHAT_UPDATED
# Вложение к чату после встречи разбирается не дольше (как `/chat/attach` ребёнка).
CHAT_ATTACH_TIMEOUT_S = 90.0
# Пока идёт эта работа над записью, продолжить разговор нельзя: расшифровка
# перепишет текст, по которому отвечает агент (ревью after-chat, I3).
CHAT_BUSY_KINDS = (jobs.TRANSCRIBE, jobs.IMPORT, jobs.MERGE, jobs.REDIARIZE)
TRANSCRIBING = "Встреча ещё расшифровывается — продолжить разговор можно, когда она будет готова"
# ❓ после встречи — сообщение агенту (ревью after-chat, I2).
EXPLAIN_TEXT = "❓ Поясни это сообщение"
# Документов базы знаний для чипов-источников окна — не больше.
KB_DOCS_MAX = 5000
# Переписка с ассистентом для вкладки «Агент» (meet.assist.chatlog.ASSISTANT_CHAT_MD).
ASSISTANT_CHAT_MD = "assistant_chat.md"


# Разбор вложения после встречи: один за раз (ревью after-chat, I1).
_ATTACH_SLOT = threading.Semaphore(1)


def _attach_worker_argv(folder: Path, item, vision: bool) -> list[str]:
    """Команда разборщика вложения (`meet.assist.attach_worker`): тот же
    интерпретатор, что у резидента. Путь и имя — одним аргументом через «=»
    (ведущий дефис — не флаг); картинка — в stdin."""
    argv = [sys.executable, "-m", "meet.assist.attach_worker", str(folder), f"--vision={int(bool(vision))}"]
    if isinstance(item, dict):
        if item.get("name"):
            argv.append(f"--name={item['name']}")
    else:
        argv.append(f"--path={item}")
    return argv


def _attachment_files(folder: Path) -> set[Path]:
    """Файлы вложений встречи: материалы с текстом и картинки."""
    from meet import materials

    out: set[Path] = set()
    for sub in (materials.materials_dir(folder), materials.assistant_dir(folder) / materials.FILES_DIR):
        try:
            out.update(p for p in sub.iterdir() if p.is_file() and p.name != materials.IDS_FILE)
        except OSError:
            continue
    return out


def _parse_attachment_in_child(folder: Path, item, vision: bool, log=print) -> tuple[dict, bool]:
    """Разобрать вложение в коротком процессе `attach_worker` не дольше
    CHAT_ATTACH_TIMEOUT_S. → (поля записи журнала, разобрано ли). Срок
    вышел — процесс убит, файлы, которые он положил в папку встречи, убраны,
    503. Процесс упал или ответил не то — запись «не разобрано»."""
    from meet.assist import participant

    before = _attachment_files(folder)
    data = item.get("data") if isinstance(item, dict) else None
    try:
        process = subprocess.Popen(
            _attach_worker_argv(folder, item, vision), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=jobs._creationflags())
    except OSError as e:
        return participant.failed_attachment(item, f"разборщик не запустился: {e}"), False
    try:
        out, err = process.communicate(input=bytes(data or b""), timeout=CHAT_ATTACH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        jobs._kill_tree(process)
        process.communicate()
        _drop_new_files(folder, before, log)
        raise _unavailable("Файл разбирается слишком долго — приложите его частями или поменьше")
    try:
        reply = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        fields = reply["fields"]
        if not isinstance(fields, dict):
            raise TypeError("fields")
    except (ValueError, KeyError, IndexError, TypeError):
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or [f"код {process.returncode}"]
        log(f"чат после встречи: разборщик вложения упал ({folder.name}): {tail[0][:300]}")
        _drop_new_files(folder, before, log)
        return participant.failed_attachment(item, "разбор не удался"), False
    return fields, bool(reply.get("ok"))


def _drop_new_files(folder: Path, before: set[Path], log=print) -> None:
    """Файлы вложений, которых не было до разбора (разборщик убит или упал),
    — убрать: без записи журнала они съедали бы квоту и попадали в затравку.
    Разбор один за раз, а живого ребёнка у этой записи нет (проверено), так
    что новые файлы — его."""
    for path in _attachment_files(folder) - before:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            log(f"чат после встречи: файл брошенного разбора не убран ({path.name}: {e})")


class ProviderCache:
    """Кто ответит на вопрос (`llm.resolve`), посчитанный в фоновом потоке.

    `get(cfg)` никогда не ждёт проверки входа: отдаёт (провайдер, checking).
    Пока ответа нет или он устарел — запускает фоновый пересчёт и отдаёт
    прежний ответ (или None, если сменилась настройка) с checking=True."""

    def __init__(self, resolve=None, ttl: float = PROVIDER_TTL_S,
                 clock=time.monotonic) -> None:
        self._resolve = resolve
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._key = None
        self._value: str | None = None
        self._at: float | None = None
        self._running = False
        # Растёт на каждое invalidate(): ответ проверки, начатой до сброса
        # (человек вошёл в CLI, пока она шла), не должен стать «свежим».
        self._generation = 0

    @staticmethod
    def _key_of(cfg):
        return (cfg.llm.provider, cfg.llm.base_url, tuple(cfg.llm.enabled))

    def get(self, cfg) -> tuple[str | None, bool]:
        key = self._key_of(cfg)
        with self._lock:
            same = self._key == key and self._at is not None
            if same and self._clock() - self._at < self._ttl:
                return self._value, False
            if not self._running:
                self._running = True
                threading.Thread(target=self._refresh,
                                 args=(cfg, key, self._generation),
                                 name="meet-llm-resolve", daemon=True).start()
            return (self._value if same else None), True

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            if self._at is not None:
                self._at = float("-inf")  # прежний ответ — до пересчёта

    def _refresh(self, cfg, key, generation: int) -> None:
        try:
            resolve = self._resolve
            if resolve is None:
                from meet import llm

                resolve = llm.resolve
            name = resolve(cfg)[0]
        except Exception:
            name = None  # сбой проверки — «никто не ответит», а не падение потока
        with self._lock:
            self._key, self._value = key, name
            # Сброшено, пока шла проверка: ответ показываем, но как устаревший —
            # следующий get() проверит заново.
            fresh = generation == self._generation
            self._at = self._clock() if fresh else float("-inf")
            self._running = False


def _provider_installed(cfg, provider: str | None = None) -> bool:
    """Дешёвая проверка «есть кому ответить» (без проверки входа и без SDK).
    `provider` — модель, выбранная человеком: есть ли она сама (модель по
    умолчанию её не заменяет). Без него — модель по умолчанию; «Авто» — хоть
    один из своих кандидатов среди включённых.

    Задача всё равно спросит `llm.resolve` и, если вход не выполнен, упадёт с
    тем же текстом — это осознанно: ждать проверки входа в HTTP нельзя."""
    from meet.llm import AUTO_PROVIDERS, detect

    found = detect.available(cfg.llm.base_url, via_proxy=llm_pkg_route(cfg))
    choice = provider or cfg.llm.provider
    if choice == "auto":
        return any(found.get(name, {}).get("found") for name in AUTO_PROVIDERS if name in cfg.llm.enabled)
    return bool(found.get(choice, {}).get("found"))


def llm_pkg_route(cfg, via_proxy: bool | None = None):
    """`meet.llm.local_route` — путь к серверу локальной модели с прокси из
    `llm.proxy` (свой адрес резидент берёт из настроек, а не из своей среды)."""
    from meet.llm import local_route

    return local_route(cfg, via_proxy)


def _check_provider(provider: str) -> dict:
    """`python -m meet.llm.check <provider>` подпроцессом: вызов модели не
    должен жить в резиденте, а SDK провайдера — грузиться в него. Прокси —
    из настроек, как у задач (см. meet.netproxy)."""
    from meet import netproxy

    try:
        out = subprocess.run(
            [sys.executable, "-m", "meet.llm.check", provider],
            env=netproxy.settings_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=CHECK_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "provider": provider,
                "error": f"проверка не уложилась в {CHECK_TIMEOUT_S} с"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "provider": provider, "error": f"{type(e).__name__}: {e}"}
    lines = (out.stdout or "").strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        tail = (out.stderr or out.stdout or "нет ответа").strip()[-300:]
        return {"ok": False, "provider": provider, "error": tail}
    return data


def _suggest_title(folder: Path, provider: str | None = None) -> dict:
    """`python -m meet.titles <папка>` подпроцессом: вызов модели не живёт в
    резиденте (как проверка провайдера). `provider` — модель, выбранная
    человеком (`--provider`). → {"title", "from", "llm"?} или {"error"}."""
    from meet import netproxy

    try:
        out = subprocess.run(
            [sys.executable, "-m", "meet.titles", str(folder),
             *([f"--provider={provider}"] if provider else [])],
            env=netproxy.settings_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TITLE_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"модель не ответила за {TITLE_TIMEOUT_S} с"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    lines = (out.stdout or "").strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"error": (out.stderr or out.stdout or "нет ответа").strip()[-300:]}
    return data


def _job_provider(job) -> str | None:
    """Модель, выбранная человеком для задачи (`options.provider`); None — по умолчанию."""
    value = (getattr(job, "options", None) or {}).get("provider")
    return value if isinstance(value, str) and value else None


def _with_provider(options: dict, provider: str | None) -> dict:
    return {**options, "provider": provider} if provider else options


def _has_chat(folder: Path) -> bool:
    from meet.assist import chatlog

    return chatlog.has_chat(folder)


def _unavailable(text: str):
    """503 для API: занято на время (журнал чата под замком)."""
    from meet.control import Unavailable

    return Unavailable(text)


def _chat_client_id(body: dict) -> str | None:
    value = body.get("client_id")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > CHAT_CLIENT_ID_MAX:
        raise _bad_request(f"client_id — строка до {CHAT_CLIENT_ID_MAX} символов")
    return value


def _chat_mid(mid) -> str:
    if not isinstance(mid, str) or not _CHAT_MESSAGE_ID.fullmatch(mid):
        raise _bad_request("неизвестное сообщение")
    return mid


def _session_profile(body: dict | None) -> str | None:
    """`profile` из тела `/live/start`, `/live/attach`, `/live/profile`: ключ
    (`work` / `personal`) или подпись; нет поля — None; негодное — 400."""
    value = (body or {}).get("profile")
    if value is None:
        return None
    key = settings.profile_key(value)
    if key is None:
        raise _bad_request("profile — work или personal")
    return key


def _chat_message(body: dict | None) -> dict:
    """Тело сообщения чата: текст и/или id вложений (`a3`), `client_id`."""
    body = body or {}
    text = body.get("text", "")
    if not isinstance(text, str):
        raise _bad_request("text — строка")
    text = text.strip()
    if len(text) > CHAT_TEXT_MAX:
        raise _bad_request(f"сообщение длиннее {CHAT_TEXT_MAX} символов")
    refs = body.get("attachments") or []
    if (not isinstance(refs, list) or len(refs) > CHAT_ATTACHMENTS_MAX
            or not all(isinstance(r, str) and _CHAT_ATTACHMENT_ID.fullmatch(r) for r in refs)):
        raise _bad_request(f"attachments — до {CHAT_ATTACHMENTS_MAX} id вложений («a3»)")
    if not text and not refs:
        raise _bad_request("пустое сообщение")
    out: dict = {"text": text}
    if refs:
        out["attachments"] = list(dict.fromkeys(refs))
    cid = _chat_client_id(body)
    if cid:
        out["client_id"] = cid
    return out


def _chat_path(raw) -> str:
    """Путь вложения из окна: полный локальный путь существующего файла или
    папки; сетевые пути и пути устройств — нет."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > CHAT_PATH_MAX or "\x00" in raw:
        raise _bad_request("путь — непустая строка")
    text = raw.strip()
    if text.startswith(("\\\\", "//")):
        raise _bad_request("сетевые пути не принимаются")
    path = Path(text)
    if not path.is_absolute():
        raise _bad_request("нужен полный путь")
    try:
        exists = path.is_file() or path.is_dir()
    except OSError:
        exists = False
    if not exists:
        raise _bad_request(f"нет такого файла или папки: {path.name or text}")
    return str(path)


def _legacy_assistant(folder: Path) -> dict | None:
    """Прежний ассистент встречи до 0.3.6 (§8): подсказки из `live_state.json`
    и вопросы `qa.jsonl` — только для чтения. Нет ни того, ни другого — None."""
    from meet import assistant
    from meet.assist.live_state import load_saved

    saved = load_saved(folder) or {}
    hints = [h for h in saved.get("hints") or [] if isinstance(h, dict)]
    try:
        qa = assistant.read_qa(folder)
    except Exception:
        qa = []
    if not hints and not qa:
        return None
    return {"hints": hints, "qa": qa}


def _marked_provider(mark: dict) -> str | None:
    """Модель из отметки `pending_analysis`/`pending_improve` (выбор человека)."""
    from meet import llm

    value = mark.get("provider")
    return value if value in llm.PROVIDERS else None


def _origin(value) -> dict | None:
    """Происхождение результата {"provider", "model"} — только годное: известный
    провайдер, модель — строка разумной длины или None (приходит и из окна)."""
    from meet import llm

    if not isinstance(value, dict) or value.get("provider") not in llm.PROVIDERS:
        return None
    model = value.get("model")
    if model is not None and (not isinstance(model, str) or len(model) > 200):
        return None
    return {"provider": value["provider"], "model": model or None}


def _bad_request(text: str):
    """400 для API: ошибка ввода (имя, картинка), а не «не найдено»."""
    from meet.control import BadRequest

    return BadRequest(text)


def _conflict(text: str):
    """409 для API: действие сейчас невозможно (не подключена модель)."""
    from meet.control import Conflict

    return Conflict(text)


# Название записи длиннее не бывает: окно ограничивает поле тем же.
TITLE_MAX = 200
# Расшифровка для агента во вкладке «Агент» (Claude Code / Codex в папке записи).
AGENT_TRANSCRIPT_MD = "transcript.md"
# Лента живого режима (пишет meet.live во время записи с ассистентом) и разметка
# встречи (задача анализа): агент получает их, если они есть.
LIVE_TRANSCRIPT_MD = "live_transcript.md"
AGENT_ANALYSIS_JSON = "analysis.json"
# Метка в meta.json: в папке записи уже работал агент — {провайдер: {"at":
# когда, "id": id сеанса}}. По ней вкладка «Агент» предлагает «Продолжить
# прошлую»: Claude Code — `--resume <id>` (id задаёт оболочка при запуске,
# `--session-id`), Codex — `resume --last`, OpenCode — `--continue` (последний
# сеанс в папке встречи). Хранилище самих CLI не читаем.
AGENT_SESSIONS_META = "agent_sessions"
AGENT_PROVIDERS = ("claude-code", "codex", "opencode")
# Шапка transcript.md, пока точной расшифровки нет, а лента живого режима есть.
AGENT_LIVE_HEADER = (
    "# Черновая расшифровка живого режима\n\n"
    "Запись ещё идёт или расшифровывается: это лента живого ассистента, имена "
    "спикеров в ней могут быть неточными. Точная расшифровка заменит этот файл "
    "при следующем запуске агента.\n\n"
)

# Сколько «Удалить» ждёт, пока плеер и сведение отпустят файлы записи.
DELETE_WAIT_S = 3.0
# Сколько ответов «ссылки на Jira» помнить (по записи, тексту, итогам, анализу, настройкам).
JIRA_CACHE_MAX = 16

# События шины о записи вне задач очереди: окно перечитывает список и снимок.
RECORDING_PROCESSING = "recording.processing"  # {"id"}: началась обработка в фоне
RECORDING_UPDATED = "recording.updated"  # {"id"}: запись изменилась (обрезка, выгрузка)
# Группы встреч изменились (список, порядок, членство): одно событие на действие,
# {"op", "id"?}; окно перечитывает группы и список. Не recording.*: массовое
# «В группу» не должно давать по событию на запись.
GROUPS_CHANGED = "groups.changed"
# Анализ встречи готов, не удался или устарел (правка текста): {"id", "state"}.
# Очередь и ход самой задачи — обычные job.* (вид "analyze").
ANALYSIS_UPDATED = "analysis.updated"
# «Улучшить расшифровку»: предложение готово, не удалось или применено:
# {"id", "state"}. Ход самой задачи — обычные job.* (вид "improve").
IMPROVE_UPDATED = "improve.updated"
# «Предложить название» без свежего анализа — короткий вызов модели подпроцессом.
TITLE_TIMEOUT_S = 150
# Удаление и объединение снимают идущий анализ записи: столько ждём, пока его
# процесс (он работает в папке записи) завершится.
DROP_ANALYSIS_WAIT_S = 5.0
PROCESSING = "Запись ещё обрабатывается (обрезка ожидания после звонка) — подождите минуту"
# Удалить модель, пока она же качается, и качать, пока ставится движок, нельзя.
MODEL_DOWNLOADING = "модель сейчас скачивается — удалить её можно после загрузки"
ENGINE_INSTALLING = "идёт установка движка — модели можно скачать после неё"
MODELS_DOWNLOADING = "идут загрузки моделей — движок можно ставить после них"
OWNER_VOICE_BUSY = "записывается образец голоса или идёт его поиск по встречам — движок можно ставить после этого"
RESIDENT_STOPPING = "служба записи останавливается — скачайте модель после её перезапуска"
STORAGE_MOVING = "идёт перенос движка и моделей — скачайте модель после него"
# Восстановление после перезапуска берёт записи не старше этого.
RECOVER_DAYS = 7
# Транскрипт — только текст, спикеров ещё нет (`phase: "text"`, Р4): действия,
# которым нужны спикеры или окончательный текст, отказывают (409).
TEXT_ONLY_RUNNING = "Спикеры ещё не определены — дождитесь конца расшифровки"
TEXT_ONLY_STOPPED = "Спикеры ещё не определены: расшифровка прервалась — расшифруйте запись заново"


def _for_window(segment):
    if not isinstance(segment, dict) or "words" not in segment:
        return segment
    out = {k: v for k, v in segment.items() if k != "words"}
    if library.words_match(segment):
        out["has_words"] = True
    return out


def _in_room(segment, owners: set[str]) -> bool:
    """Реплика с микрофона не владельца — человек рядом с ним в комнате
    (meet.mic_split или правка спикеров): окно ставит у неё «в комнате»."""
    return (isinstance(segment, dict) and segment.get("track") == "mic" and segment.get("kind") != "break"
            and isinstance(segment.get("speaker"), str) and segment["speaker"] not in owners)


def _window_transcript(data: dict | None, folder: Path | None = None) -> dict | None:
    """Транскрипт для окна: сырые SPEAKER_XX — «Спикер N», слова с таймкодами
    убраны (их много), вместо них — `has_words` у сегмента; у реплик людей в
    комнате (микрофон, не владелец по segvoices.owner_labels) — `room`."""
    from meet import segvoices

    data = library.with_display_names(data)
    if not data or not isinstance(data.get("segments"), list):
        return data
    owners = segvoices.owner_labels(folder, data)
    segments = [_for_window(s) for s in data["segments"]]
    segments = [{**s, "room": True} if _in_room(s, owners) else s for s in segments]
    return {**data, "segments": segments}


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _agent_sessions(meta: dict) -> list[str]:
    """Агенты, уже работавшие в папке записи (метка `agent_sessions`), в
    порядке AGENT_PROVIDERS."""
    marks = meta.get(AGENT_SESSIONS_META)
    if not isinstance(marks, dict):
        return []
    return [name for name in AGENT_PROVIDERS if marks.get(name)]


def _agent_session_id(meta: dict, provider: str) -> str | None:
    """Id прошлого сеанса агента в папке (метка ранних сборок — просто время, без id)."""
    marks = meta.get(AGENT_SESSIONS_META)
    mark = marks.get(provider) if isinstance(marks, dict) else None
    sid = mark.get("id") if isinstance(mark, dict) else None
    return sid if isinstance(sid, str) and _SESSION_ID.fullmatch(sid) else None


# Id сеанса Claude Code — UUID (claude --help: «--session-id <uuid>»).
_SESSION_ID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def _without_marks(data: dict) -> dict:
    """Сохранение из редактора: служебное `has_words` окна — не в файл. Слов
    окно не видит, они остаются в words.json; у сегментов, текст или время
    которых поменяли, они при чтении больше не подставляются."""
    return {**data, "segments": [{k: v for k, v in s.items() if k != "has_words"} if isinstance(s, dict) else s
                                 for s in data["segments"]]}


class TrayControl:
    """Состояние для `meet.control.ControlServer` поверх объекта трея."""

    def __init__(self, tray, queue=None, llm_queue=None, live=None, downloads=None) -> None:
        self.tray = tray
        self.bus = tray.bus
        # Очередь задач живёт рядом с записью, в том же резиденте: расшифровка
        # идёт подпроцессом и не мешает ни записи, ни панели.
        self.queue = queue if queue is not None else jobs.JobQueue(self.bus)
        # Итоги и вопросы — своя очередь: GPU им не нужен, и ждать за часовой
        # расшифровкой ответ на вопрос было бы странно.
        self.llm_queue = llm_queue if llm_queue is not None else jobs.JobQueue(self.bus)
        # Загрузки моделей — по слоту на модель: разные качаются одновременно
        # (сеть и диск, не GPU), одна и та же — не дважды.
        self.downloads = downloads if downloads is not None else jobs.KeyedQueues(self.bus)
        # Перенос движка и моделей: пока оболочка переключает папку, новые
        # задачи и загрузки не ставятся (`storage.HOLD`, POST /storage/hold).
        from meet import storage

        for q in (self.queue, self.llm_queue, self.downloads):
            try:
                q.gate = storage.HOLD.gate
            except AttributeError:  # заглушка теста без атрибутов
                pass
        # Установка движка и загрузки моделей исключают друг друга (обе
        # стороны — под этим локом): подпроцесс загрузки грузит пакеты того
        # самого окружения, которое переставляет pip.
        self._engine_lock = threading.Lock()
        self._providers = ProviderCache()
        self._submit_lock = threading.Lock()
        self._levels: dict = {}
        self._devices_cache: dict | None = None
        self._devices_at = 0.0
        self._device_check = threading.Lock()  # одна проверка устройства за раз
        # Выбранное в настройках устройство не нашлось у идущей записи — по
        # роли: {"mic"|"output": {"kind", "name": выбранное, "device": взятое}}.
        # Вернулось — запись убирается; запись кончилась — всё.
        self._fallbacks: dict[str, dict] = {}
        # macOS: звук собеседников идущей записи не пишется —
        # {"notice", "permission"}; None — пишется (или не macOS).
        self._system_audio: dict | None = None
        # Последняя проверка доступа к Hugging Face (без токена): окно рисует
        # её по GET /hf/status, не дёргая сеть на каждый показ.
        self._hf_check: dict | None = None
        self.bus.subscribe(self._remember_levels)
        tray.on_saved = self._on_saved
        # Живой режим — тоже запись, но дочерним процессом: резидент его
        # запускает, останавливает и ретранслирует. Пока он идёт, трей вторую
        # запись не поднимает ни из меню, ни автозаписью (общий lock всё равно
        # не дал бы, но с уведомлением об ошибке и повторами).
        self.live = live if live is not None else live_control.LiveControl(
            self.bus, log=tray.log)
        tray.live_busy = self.live.busy
        # Ассистент, подключённый к записи: запись останавливается в момент
        # «Стоп» (отвод закрывается), а ему — только `/stop`, без ожидания.
        # Запись сохраняется сразу (источник, расшифровка, хук, название), а он
        # дописывает хвост ленты и сводку в её папку в фоне (≤ STOP_TIMEOUT_S).
        # Отменённую запись удаляют сразу — его перед этим убивают.
        tray.after_stop = self._finish_attached
        # Временная встреча, которую сохраняют как обычную: перед переносом в
        # библиотеку дождаться, пока ассистент допишет хвост в её папку.
        tray.settle_after_stop = self._settle_attached
        # Когда поднялся резидент: временные встречи старше — прошлого запуска
        # (сбой, убит), их убирает recover_in_background.
        self._born = time.time()
        self.bus.subscribe(self._on_live_event)
        # Выгрузка в базу знаний: по одной за раз (кнопка поверх автоматики не
        # должна писать в ту же папку одновременно). Последний сбой автоматики —
        # в снимке, оболочка показывает по нему уведомление; о каждой встрече —
        # не больше одного за жизнь резидента.
        self._kb_lock = threading.Lock()
        self._kb_failed: dict | None = None
        self._kb_reported: set[str] = set()
        # Записи, которые сейчас обрабатываются в фоне (обрезка хвоста):
        # ключ — путь без регистра, значение — путь для снимка.
        self._processing: dict[str, str] = {}
        self._processing_lock = threading.Lock()
        # Правки расшифровки — спикеров (панель «Спикеры») и текста
        # («Исправить…»): по одной за раз — шаги общей истории встречи и
        # образцы голосов не должны переплетаться.
        self._speakers_lock = threading.Lock()
        # Автоматический анализ встречи: записи, где он отложен (идёт запись или
        # живой режим), и записи, где его надо повторить после идущего
        # (расшифровку поменяли, пока он шёл). Ключ — путь без регистра.
        # Значение — (папка, просили ли вручную, выбранная модель или None).
        self._analysis_deferred: dict[str, tuple[Path, bool, str | None]] = {}
        # Повторить после идущего: ключ — запись, значение — (просили ли вручную
        # — тогда повтор не зависит от `analysis.auto`, модель идущей задачи).
        self._analysis_rerun: dict[str, tuple[bool, str | None]] = {}
        self._analysis_lock = threading.Lock()
        # «Улучшить расшифровку» автоматически после распознавания, отложенное до
        # конца записи или живого режима (модель во время встречи не зовём).
        # Значение — (папка, модель, выбранная человеком, или None).
        self._improve_deferred: dict[str, tuple[Path, str | None]] = {}
        # «Предложить название», которое уже считается: второй запрос по той же
        # записи ждёт его, а не платит за второй вызов модели.
        self._suggesting: dict[str, dict] = {}
        self._suggest_lock = threading.Lock()
        # Черновое название по теме ассистента (_live_title): по одному за раз.
        self._live_title_lock = threading.Lock()
        self.bus.subscribe(self._on_job_event)
        # Образец голоса владельца (мастер, настройки «Звук»): запись
        # подпроцессом устройств, разбор — задачей в своём слоте загрузок.
        from meet import owner_voice_control

        self.owner_takes = owner_voice_control.OwnerTakes(
            queue=self.downloads, bus=self.bus, voices=self._voices, log=tray.log,
            busy=lambda: bool(self.tray.recording) or self.live.busy(),
            installing=lambda: self.queue.active_for(
                str(paths.data_dir()), (jobs.INSTALL_ENGINE,)) is not None)

    @staticmethod
    def _background(fn, name: str = "meet-background") -> None:
        """Фоновая работа вне потока очереди задач (тесты подменяют на вызов)."""
        threading.Thread(target=fn, name=name, daemon=True).start()

    # --- отметки о записи: обрабатывается, ждёт расшифровки, изменилась ----

    @staticmethod
    def _key(folder: Path) -> str:
        try:
            return os.path.normcase(str(Path(folder).resolve()))
        except OSError:
            return os.path.normcase(str(folder))

    def _processing_now(self, folder: Path) -> bool:
        """Запись сейчас обрабатывается в фоне (обрезка хвоста): её дорожки
        подменяются, и ни расшифровать, ни удалить, ни объединить, ни слушать
        её нельзя."""
        with self._processing_lock:
            return self._key(folder) in self._processing

    def _set_processing(self, folder: Path, on: bool) -> None:
        with self._processing_lock:
            if on:
                self._processing[self._key(folder)] = str(folder)
            else:
                self._processing.pop(self._key(folder), None)
        self.bus.emit(RECORDING_PROCESSING if on else RECORDING_UPDATED, id=Path(folder).name)

    def _updated(self, folder: Path) -> None:
        """Запись изменилась вне задач очереди (обрезка, выгрузка, переименование
        папки в базе знаний, объединение завершено) — окно перечитает список."""
        try:
            self.bus.emit(RECORDING_UPDATED, id=Path(folder).name)
        except Exception:
            pass  # шина — не повод ронять фоновую работу

    def _text_only(self, folder: Path) -> str | None:
        """Почему по транскрипту записи нельзя то, что опирается на спикеров:
        это текст до них (`phase: "text"`). None — транскрипт окончательный
        (или его нет — об этом скажут сами действия)."""
        if library.transcript_phase(folder) != library.TEXT_PHASE:
            return None
        if self.queue.active_for(str(folder), jobs.FOLDER_KINDS) is not None:
            return TEXT_ONLY_RUNNING
        return TEXT_ONLY_STOPPED

    def _refuse_text_only(self, folder: Path) -> None:
        reason = self._text_only(folder)
        if reason:
            raise _conflict(reason)

    def _mark_pending(self, folder: Path, on: bool) -> None:
        """`pending_transcribe` в meta.json: задача над записью (расшифровка,
        импорт, объединение) поставлена, но не закончилась. Резидент, закрытый
        посреди неё (обновление, «Выход»), при следующем запуске поставит её
        снова (см. recover)."""
        try:
            if on:
                library.write_meta(folder, {"pending_transcribe": time.time()})
            elif "pending_transcribe" in library.read_meta(folder):
                library.update_meta(folder, lambda meta: {
                    k: v for k, v in meta.items() if k != "pending_transcribe"})
        except Exception as e:
            self.tray.log(f"отметка о задаче не записана ({Path(folder).name}): {e}")

    def _on_job_event(self, event) -> None:
        """Расшифровка или импорт готовы — выгрузить встречу в базу знаний, если
        включена автоматика; итоги готовы — выгрузить заново (итоги ложатся
        рядом), если включена автоматика или встречу уже выгружали вручную."""
        if event.kind not in (jobs.JOB_DONE, jobs.JOB_FAILED):
            return
        job = event.data.get("job") or {}
        kind, folder = job.get("kind"), job.get("folder")
        if (kind in jobs.FOLDER_KINDS and folder
                and not getattr(self.queue, "stopping", False)):
            # Задача кончилась (или её отменили) — повторять после перезапуска
            # нечего. Задачи, убитые остановкой резидента, отметку сохраняют.
            self._mark_pending(Path(folder), False)
        if kind == jobs.CHAT and folder:
            # Ответ после встречи готов или не удался — окно перечитает чат.
            if event.kind == jobs.JOB_FAILED:
                self._background(lambda: self._close_stuck_chat(Path(folder)), "meet-chat")
            self.bus.emit(CHAT_UPDATED, id=Path(folder).name)
        if kind == jobs.ANALYZE and folder:
            stopping = bool(getattr(self.llm_queue, "stopping", False))
            self._background(lambda: self._analysis_finished(Path(folder), job.get("state"),
                                                             job=job, stopping=stopping),
                             "meet-analysis")
        if kind == jobs.IMPROVE and folder:
            stopping = bool(getattr(self.llm_queue, "stopping", False))
            self._background(lambda: self._improve_finished(Path(folder), job.get("state"),
                                                            job=job, stopping=stopping),
                             "meet-improve")
        if self._analysis_deferred or self._improve_deferred:
            self._background(self._flush_deferred_analysis, "meet-analysis")
        if event.kind != jobs.JOB_DONE:
            return
        if kind == jobs.MERGE and folder:
            self._merge_sound_ready(Path(folder))
            return
        if kind not in (jobs.TRANSCRIBE, jobs.IMPORT, jobs.SUMMARY) or not folder:
            return
        path = Path(folder)
        if kind == jobs.SUMMARY:
            self._background(lambda: self._summary_title(path), "meet-title")
        if kind in (jobs.TRANSCRIBE, jobs.IMPORT):
            # Свести дорожки для плеера заранее, в фоне: первое «▶» — без ожидания.
            from meet import playback

            playback.schedule(path)
            # Анализ встречи — следом, если включён (объединённая встреча
            # приходит сюда же: после объединения идёт обычная расшифровка).
            self._background(lambda: self._auto_analyze(path), "meet-analysis")
            # Прежнее предложение «Улучшить расшифровку» — по старому тексту.
            self._background(lambda: self._auto_improve(path, transcribed=True), "meet-improve")
        merged_exported = finish_merge = False
        if kind == jobs.TRANSCRIBE:
            info = self._merge_info(path)
            if info is not None:
                merged_exported = bool(info.get("kb_exported"))
                finish_merge = info.get("state") != "done"
        ready = self._kb_wanted(kind, path, merged_exported)
        if ready or finish_merge:
            # Сначала выгрузка, потом удаление исходных: пока они в библиотеке,
            # их папки в базе знаний заняты, и объединённая встреча получит
            # свою, а не ляжет в папку удалённой части.
            def work() -> None:
                if ready:
                    self._auto_kb_export(path)
                if finish_merge:
                    self._finish_merge(path)

            self._background(work, "meet-kb-export")

    def _kb_wanted(self, kind: str, path: Path, merged_exported: bool) -> bool:
        """Выгружать ли встречу в базу знаний после задачи `kind`."""
        try:
            # Подписчик шины: исключение отсюда не должно доходить до очереди
            # задач — битый конфиг или недоступная папка только в журнал.
            cfg = settings.load().export
            if not cfg.meetings_dir:
                return False
            if kind == jobs.SUMMARY:
                from meet import kb_export

                wanted = cfg.auto_export or kb_export.previously_exported(path)
            else:
                # Объединённую встречу, части которой уже выгружались, —
                # выгрузить и её (прежние папки в базе знаний не трогаем).
                wanted = cfg.auto_export or merged_exported
            # Текст до спикеров не выгружаем: выгрузит конец расшифровки.
            return wanted and library.final_transcript(path) is not None
        except Exception as e:
            self.tray.log(f"выгрузка в базу знаний не проверена ({path.name}): "
                          f"{type(e).__name__}: {e}")
            return False

    def _auto_kb_export(self, folder: Path) -> None:
        from meet import kb_export

        if library.transcript_phase(folder) == library.TEXT_PHASE:
            # Переименование, категория, правка — посреди расшифровки или после
            # прерванной: в базу знаний уходит только окончательная.
            self.tray.log(f"выгрузка в базу знаний отложена — спикеры ещё не определены: {folder.name}")
            return
        try:
            with self._kb_lock:
                result = kb_export.export_recording(folder, settings.load())
        except Exception as e:
            error = str(e) or type(e).__name__
            self.tray.log(f"не удалось выгрузить встречу в базу знаний ({folder.name}): {error}")
            if not folder.is_dir():
                return  # запись удалили, пока шла выгрузка, — сообщать не о чем
            if folder.name not in self._kb_reported:
                self._kb_reported.add(folder.name)
                self._kb_failed = {"folder": folder.name, "error": error, "at": time.time()}
            return
        self.tray.log(f"встреча выгружена в базу знаний: {result['path']}")
        for note in result.get("notes") or []:
            self.tray.log(note)
        self._updated(folder)

    def _on_live_event(self, event) -> None:
        """Ассистент кончился — отложенный на время встречи анализ пора
        догнать. Запись, к которой он был подключён (в том числе «Запись с
        ассистентом»), сохраняет, ставит в расшифровку и отдаёт хуку остановка
        самой записи (`_on_saved`), а не ассистента.

        Хвост ассистента остановленной записи с ассистентом дописал последнюю
        сводку — черновое название ещё раз, по ней (`_live_title`)."""
        if event.kind in (live_control.LIVE_STOPPED, live_control.LIVE_FAILED) \
                and (self._analysis_deferred or self._improve_deferred):
            self._background(self._flush_deferred_analysis, "meet-analysis")
        data = event.data or {}
        folder = data.get("folder")
        if (event.kind == live_control.LIVE_STOPPED and folder and data.get("attached")
                and data.get("ended_by") == live_control.ENDED_RECORDING
                and not data.get("discarded")):
            path = Path(folder)

            def retitle() -> None:
                # Только запись с ассистентом (как при её остановке) и только
                # пока она есть: отменённую удаляют сразу.
                if path.is_dir() and \
                        library.read_meta(path).get("source") == _META_SOURCE[LIVE]:
                    self._live_title(path)

            self._background(retitle, "meet-title")

    def _on_saved(self, folder: str, source: str | None, full: bool) -> None:
        """Запись штатно сохранена: пометить, откуда она, и поставить в очередь.

        Короткий автозвонок (full=False) — чаще ложное срабатывание детектора
        (звук уведомления): его сохраняем, но GPU на него не тратим."""
        path = Path(folder)
        try:
            library.write_meta(path, {"source": _META_SOURCE.get(source, "record")})
        except Exception as e:
            # Пометка «откуда запись» — не повод не расшифровывать её.
            self.tray.log(f"meta.json не записан ({path.name}): {e}")
        if source == LIVE:
            # Тема, которую вёл ассистент, — черновое название, если включено
            # «Придумывать название». Сейчас — по live_state.json на миг
            # остановки: свой хвост (последнюю сводку) ассистент дописывает
            # после, в фоне, и тогда название уточняется ещё раз
            # (`_on_live_event`); итоги или анализ потом заменят черновое.
            self._background(lambda: self._live_title(path), "meet-title")
        title = getattr(self.tray, "recording_title", None) if source == AUTO else None
        if title:
            # Название звонка из окна браузера — только начальное: если запись
            # уже переименовали (пока она шла), не трогаем.
            try:
                library.update_meta(path, lambda meta: meta if meta.get("title")
                                    else {**meta, "title": title, "title_source": "site"})
            except Exception as e:
                self.tray.log(f"название записи не сохранено ({path.name}): {e}")
        transcribe = full and settings.load().recording.auto_transcribe
        call_end = getattr(self.tray, "call_end_at", None) if source == AUTO else None
        if call_end:
            # Автозапись дождалась конца ожидания: в хвосте — минуты фона.
            # Обрезаем в фоне (ffmpeg) и только потом ставим расшифровку.
            try:
                from meet import tail

                tail.write_call_end(path, call_end, self.tray.cfg["grace_minutes"] * 60.0,
                                    transcribe=transcribe)
            except Exception as e:
                self.tray.log(f"конец звонка не записан ({path.name}): {e}")
            else:
                self._start_trim(path, transcribe)
                return
        if transcribe:
            self._queue_transcription(path)
        if self._analysis_deferred or self._improve_deferred:
            self._background(self._flush_deferred_analysis, "meet-analysis")

    # --- восстановление после перезапуска ---------------------------------

    def recover_in_background(self) -> None:
        """Доделать прерванное прошлым выходом — в фоне: резидент сразу пишет
        и отвечает окну (см. recover)."""
        def work() -> None:
            self._sweep_temporary()
            try:
                swept = jobs.sweep_temp()
                if swept:
                    self.tray.log(f"удалены временные папки прерванных задач: {len(swept)}")
            except Exception as e:
                self.tray.log(f"временные папки задач не проверены: {type(e).__name__}: {e}")
            try:
                done = self.recover()
            except Exception as e:
                self.tray.log(f"восстановление после перезапуска не прошло: {type(e).__name__}: {e}")
                return
            if any(done.values()):
                self.tray.log("восстановлено после перезапуска: "
                              f"обрезка — {len(done['trim'])}, задачи — {len(done['queued'])}, "
                              f"удалены недописанные файлы — {len(done['cleaned'])}, "
                              f"возвращены папки — {len(done['restored'])}")

        self._background(work, "meet-recover")

    def _sweep_temporary(self) -> None:
        """Временные встречи прошлого запуска (резидент упал или убит посреди
        неё): удалить и забыть сеансы агента; отмеченные «Сохранить как
        обычную встречу» — перенести в библиотеку. Идущую — не трогать."""
        from meet import temp_meeting

        try:
            done = temp_meeting.sweep(
                before=self._born, library_root=self._root(), log=self.tray.log,
                skip=lambda: getattr(self.tray, "temp_session", None),
                active=lambda: self.tray._current_folder() if self.tray.recording else None)
        except Exception as e:
            self.tray.log(f"временные встречи прошлого запуска не проверены: {type(e).__name__}: {e}")
            return
        if done["wiped"]:
            self.tray.log(f"удалены временные встречи прошлого запуска: {len(done['wiped'])}")
        for folder in done["kept"]:
            # «Сохранить как обычную встречу» до сбоя: обработать, как обычную
            # сохранённую запись с ассистентом (расшифровка, черновое название).
            try:
                self._on_saved(str(folder), LIVE, True)
            except Exception as e:
                self.tray.log(f"сохранённая временная встреча не поставлена в обработку: "
                              f"{type(e).__name__}")
            self._updated(Path(folder))

    def _busy_now(self) -> set[str]:
        """Папки, в которые сейчас пишут (обычная запись, ассистент)."""
        out = set()
        if self.tray.recording:
            out.add(self._key(Path(self.tray._current_folder())))
        live_folder = self.live.status().get("folder")
        if live_folder:
            out.add(self._key(Path(live_folder)))
        return out

    @staticmethod
    def _started(folder: Path) -> float | None:
        started = library._started_at(folder.name)
        if started:
            try:
                return datetime.fromisoformat(started).timestamp()
            except ValueError:
                pass
        try:
            return folder.stat().st_mtime
        except OSError:
            return None

    def recover(self, now: float | None = None) -> dict:
        """Доделать то, что прервал прошлый выход резидента (обновление,
        «Выход», сбой), в записях не старше RECOVER_DAYS:

        * вернуть на место папки, застрявшие посреди проверки «можно ли
          удалить» (`.<id>.probing-…` → `<id>`): удалять их никто не просил;
        * удалить недоудалённые папки (`.<id>.deleting-…`) и брошенные `*.part`
          (недописанные дорожки обрезки и объединения: дорожками они не
          считаются, но занимают место) — первым проходом, пока ничего не
          запущено;
        * автозапись остановлена, а хвост ожидания не обрезан (`record.call_end`
          без `record.trimmed`) и расшифровки нет — обрезать и, если собирались
          и автоматическая расшифровка включена, поставить её;
        * задача над записью стояла в очереди или шла (`pending_transcribe`) —
          поставить снова (объединение, импорт — тем же путём, что «Расшифровать»).

        Возвращает {"trim": [...], "queued": [...], "cleaned": [...],
        "restored": [...]}."""
        import shutil

        from meet import tail

        now = time.time() if now is None else now
        cutoff = now - RECOVER_DAYS * 86400
        root = self._root()
        done: dict[str, list[str]] = {"trim": [], "queued": [], "cleaned": [],
                                      "restored": []}
        # До обхода записей: возвращённая папка — снова обычная запись.
        done["restored"] = library.restore_probes(root)
        for leftover in library.leftover_deletions(root):
            shutil.rmtree(leftover, ignore_errors=True)
            done["cleaned"].append(leftover.name)
        busy = self._busy_now()
        folders = []
        for folder in library.recording_folders(root):
            started = self._started(folder)
            if self._key(folder) in busy or started is None or started < cutoff:
                continue
            folders.append(folder)
            for part in folder.glob("*.part"):
                try:
                    part.unlink()
                    done["cleaned"].append(f"{folder.name}/{part.name}")
                except OSError:
                    pass
        auto_transcribe = settings.load().recording.auto_transcribe
        trims: list[tuple[Path, bool]] = []
        for folder in folders:
            # Текст до спикеров — ещё не расшифровка: ни обрезку, ни конец
            # объединения он не завершает.
            has_transcript = library.final_transcript(folder) is not None
            if tail.pending(folder) and not has_transcript:
                event = tail.call_end(folder) or {}
                wanted = bool(event.get("transcribe", True)) and auto_transcribe
                # Сразу «обрабатывается»: пока очередь до неё не дошла, её не
                # расшифруют и не удалят из-под ffmpeg.
                self._set_processing(folder, True)
                trims.append((folder, wanted))
                done["trim"].append(folder.name)
                continue
            meta = library.read_meta(folder)
            info = meta.get("merge")
            if (meta.get("source") == "merge" and has_transcript and isinstance(info, dict)
                    and info.get("state") == "merged"):
                # Расшифровано, а исходные не обработаны: выход пришёлся между ними.
                self._background(lambda f=folder: self._finish_merge(f), "meet-merge-finish")
            pending = meta.get("pending_transcribe")
            if not isinstance(pending, (int, float)) or isinstance(pending, bool):
                continue
            written = library.transcript_path(folder)
            # Транскрипт новее отметки — задача дошла до конца; текст до
            # спикеров (`phase: "text"`) — нет: прервана между фазами.
            if pending < cutoff or (has_transcript and written.is_file()
                                    and written.stat().st_mtime > pending):
                self._mark_pending(folder, False)  # старая или уже сделанная
                continue
            try:
                job = self.transcribe(folder.name)
            except Exception as e:
                self.tray.log(f"задача не восстановлена ({folder.name}): {e}")
                continue
            if isinstance(job, dict) and job.get("id"):
                done["queued"].append(folder.name)
                self.tray.log(f"задача восстановлена после перезапуска: {folder.name} "
                              f"({job.get('kind')}, {job['id']})")
        # Обрезки — по одной, в этом же (фоновом) потоке: несколько ffmpeg разом
        # после перезапуска только мешали бы записи.
        for folder, wanted in trims:
            self._trim_then_queue(folder, wanted)
        # Анализ, который ждал, шёл или был отложен при выходе (`pending_analysis`):
        # по всей библиотеке — его могли попросить и для старой записи (возраст
        # проверяется по самой отметке).
        for folder in library.recording_folders(root):
            mark = library.read_meta(folder).get("pending_analysis")
            if not isinstance(mark, dict):
                continue
            if self.queue.active_for(str(folder), jobs.FOLDER_KINDS):
                # Запись расшифровывается заново: анализ старого текста сразу
                # устарел бы — его поставит конец расшифровки.
                self._mark_analysis(folder, False)
                continue
            if self._resume_analysis(folder, mark, cutoff):
                done.setdefault("analysis", []).append(folder.name)
        # «Улучшить расшифровку», прерванное выходом (`pending_improve`).
        for folder in library.recording_folders(root):
            mark = library.read_meta(folder).get("pending_improve")
            if isinstance(mark, dict) and self._resume_improve(folder, mark, cutoff):
                done.setdefault("improve", []).append(folder.name)
        return done

    def _queue_transcription(self, path: Path) -> None:
        job, created = self._submit_once(jobs.TRANSCRIBE, path)
        if created:
            self.tray.log(f"расшифровка поставлена в очередь: {path.name} ({job.id})")
        else:
            self.tray.log(f"расшифровка уже в очереди: {path.name} ({job.id})")

    def _start_trim(self, path: Path, transcribe: bool) -> None:
        """Обрезка хвоста в фоне; пока она идёт, запись «обрабатывается»."""
        self._set_processing(path, True)
        self._background(lambda: self._trim_then_queue(path, transcribe), "meet-trim")

    def _trim_then_queue(self, path: Path, transcribe: bool) -> None:
        """Обрезать хвост автозаписи (meet.tail), затем — расшифровка. Сбой
        обрезки — запись остаётся целой и всё равно расшифровывается. Фоновый
        поток: любой сбой — строкой в журнал, отметка «обрабатывается»
        снимается в любом случае."""
        from meet import tail

        try:
            try:
                cut = tail.trim(path)
            except Exception as e:
                self.tray.log(f"хвост записи не обрезан ({path.name}): {e}")
            else:
                if cut is not None:
                    self.tray.log(f"ожидание после звонка обрезано: {path.name}, осталось {cut:.0f} с")
            self._set_processing(path, False)
            if transcribe:
                self._queue_transcription(path)
        except Exception as e:
            self.tray.log(f"обработка записи после остановки не завершена ({path.name}): "
                          f"{type(e).__name__}: {e}")
        finally:
            if self._processing_now(path):
                self._set_processing(path, False)

    def _remember_levels(self, event) -> None:
        """Последние уровни дорожек и подмены устройств — чтобы снимок
        состояния не ждал события."""
        if event.kind == events.RECORD_LEVEL:
            self._levels = event.data.get("levels") or {}
        elif event.kind == events.RECORD_DEVICE_FALLBACK:
            role = event.data.get("role")
            rest = {k: v for k, v in self._fallbacks.items() if k != role}
            self._fallbacks = {**rest, role: {
                "kind": role, "name": event.data.get("wanted"),
                "device": event.data.get("device")}}
        elif event.kind == events.RECORD_DEVICE_PINNED:
            role = event.data.get("role")
            self._fallbacks = {k: v for k, v in self._fallbacks.items() if k != role}
        elif event.kind == events.RECORD_SYSTEM_AUDIO:
            if event.data.get("state") == "missing":
                self._system_audio = {"notice": event.data.get("notice"),
                                      "permission": bool(event.data.get("permission"))}
            else:
                self._system_audio = None
        elif event.kind == events.RECORD_STOPPED:
            self._fallbacks = {}
            self._system_audio = None

    # --- что показывать -------------------------------------------------

    def snapshot(self) -> dict:
        tray = self.tray
        cfg = settings.load()
        meetings = cfg.export.meetings_dir
        recording = bool(tray.recording)
        elapsed = time.monotonic() - tray.started if recording and tray.started else 0.0
        return {
            "status": "recording" if recording else "idle",
            # Временная встреча (meet.temp_meeting): не сохранится — окно,
            # панели и трей помечают её. «Сохранить как обычную встречу» —
            # снова False (дальше это обычная запись).
            "temporary": bool(recording and getattr(tray, "temporary", False)
                              and not getattr(tray, "temporary_kept", False)),
            # «Остановить без сохранения»: чью историю ассистента удалить нечем
            # (агент вкладки «Агент» без известного id — Codex, OpenCode); окно
            # честно говорит об этом в вопросе. [] — удалится всё.
            "forget_gaps": self._forget_gaps(recording),
            # Версия резидента: оболочка другой версии штатно его заменит.
            "version": app_version(),
            "source": tray.source,
            "folder": tray._current_folder() if recording else None,
            "elapsed_s": round(elapsed, 1),
            # Название звонка из окна браузера (автозапись), пока запись идёт:
            # панель записи под значком в строке меню macOS показывает его над
            # таймером. Тот же источник и то же условие, что у названия
            # сохранённой записи (`_on_saved`); неизвестно — None.
            "title": self._call_title() if recording else None,
            "levels": dict(self._levels) if recording else {},
            # Выбранный в настройках микрофон или вывод не найден — эта запись
            # (или запись ассистента) идёт с системного: [{"kind", "name",
            # "device"}]. Окно показывает это у кнопки записи, трей — в подсказке.
            "devices_fallback": self._devices_fallback(recording),
            # macOS: звук собеседников не пишется (нет разрешения «Запись
            # экрана») — окно держит плашку с кнопкой настроек, пока идёт запись.
            "system_audio_missing": dict(self._system_audio)
            if recording and self._system_audio else None,
            # Свободное место под записи: UI предупреждает при < 5 ГБ до старта
            # записи, а не когда ffmpeg упрётся в полный диск посреди встречи.
            # None — диск недоступен (отключён, шара не отвечает).
            "disk_free_gb": engine._free_gb(cfg.recording.recordings),
            "auto_record": {
                "enabled": bool(tray.cfg["enabled"]),
                "processes": list(tray.cfg["processes"]),
                "grace_minutes": tray.cfg["grace_minutes"],
                "grace_seconds": tray.cfg["grace_minutes"] * 60.0,
                "state": getattr(tray.watcher, "state", None),
                # Сигналы детектора: None означает «ответить нечем» (ключа в
                # реестре нет, pycaw не встал) — это не то же самое, что «нет».
                "mic": tray._last_signals[0] if tray._last_signals else None,
                "render": tray._last_signals[1] if tray._last_signals else None,
                "browsers": list(tray.cfg.get("browsers") or []),
                # Звонок в браузере по последнему опросу: {"exe", "site"} или None.
                "browser": self._browser_call(),
            },
            "recordings_dir": str(cfg.recording.recordings),
            # Папка для встреч в базе знаний: оболочка открывает выгруженные
            # папки только внутри неё (и папки записей).
            "meetings_dir": str(meetings) if meetings else None,
            # База знаний: оболочка открывает документы из неё (open_material).
            "knowledge_dir": str(cfg.assistant.knowledge_dir) if cfg.assistant.knowledge_dir else None,
            # Последний сбой автоматической выгрузки {"folder", "error", "at"}:
            # новое `at` — уведомление «Не удалось выгрузить встречу…».
            "kb_export_failed": dict(self._kb_failed) if self._kb_failed else None,
            # Чем кончилась последняя запись ("saved" | "discarded" | "short"):
            # оболочка по нему не говорит «сохранена» об отменённой. Ключ есть
            # всегда — его наличие отличает этот резидент от старого.
            "last_stop": dict(tray.last_stop) if tray.last_stop else None,
            # По живости pid, а не по наличию файла: убитая расшифровка оставляет
            # протухший маркер, и панель показывала бы «GPU занят» вечно.
            "gpu_busy": gpu_lock.held_by_live_process(),
            # Запись с ассистентом: {"active", "starting", "stopping", "folder",
            # "error", "started_at"} (см. LiveControl.status). Обычная запись
            # при этом не идёт — status выше остаётся про неё.
            "live": self.live.status(),
            # Папки записей, которые сейчас обрабатываются в фоне (обрезка
            # ожидания после звонка): окно показывает «Обработка».
            "processing": self._processing_list(),
        }

    def _forget_gaps(self, recording: bool) -> list[str]:
        if not recording:
            return []
        try:
            folder = self.tray._current_folder()
            return temp_meeting.forget_gaps(Path(folder)) if Path(folder).is_dir() else []
        except Exception:
            return []

    def _call_title(self) -> str | None:
        """Название звонка для `/state.title`. В отличие от `_browser_call`,
        который заголовок окна намеренно не отдаёт, здесь он есть — но только
        у автозаписи и только пока она идёт: это то самое название, что станет
        у записи (`_on_saved`), а снимок доступен лишь по токену с localhost."""
        tray = self.tray
        return getattr(tray, "recording_title", None) if tray.source == AUTO else None

    def _processing_list(self) -> list[str]:
        with self._processing_lock:
            return sorted(self._processing.values())

    def _browser_call(self) -> dict | None:
        call = getattr(getattr(self.tray, "signals", None), "browser_call", None)
        return {"exe": call.get("exe"), "site": call.get("site")} if call else None

    def _devices_fallback(self, recording: bool) -> list[dict]:
        if recording:
            return [dict(f) for f in self._fallbacks.values()]
        try:
            return self.live.devices_fallback()
        except Exception:
            return []  # подмена LiveControl в тестах/старый — не повод ронять снимок

    # --- команды панели -------------------------------------------------

    def start_recording(self) -> dict:
        """Начать вручную. Если запись уже идёт автоматически — это то же
        нажатие «Начать запись» поверх автозаписи, что и в меню трея: человек
        берёт её под свою руку, автостоп отключается."""
        from meet import storage

        if storage.HOLD.held():
            raise _conflict(storage.HOLD_TEXT)
        if self.live.busy():  # пишет ассистент — вторая запись не нужна
            return {**self.snapshot(), "ok": False, "action": "already-recording"}
        if self.tray.start_recording(MANUAL):
            self.tray.log("запись запущена из панели")
            return {**self.snapshot(), "ok": True, "action": "started"}
        if self.tray.source == AUTO:
            return self.adopt_recording()
        return {**self.snapshot(), "ok": False, "action": "already-recording"}

    def stop_recording(self, *, discard: bool = False) -> dict:
        if not self.tray.recording:
            return {**self.snapshot(), "ok": False, "action": "not-recording"}
        folder = self.tray._current_folder()
        self.tray.log(f"остановка из панели{' с отменой' if discard else ''}")
        self.tray.stop_recording(discard=discard)
        # Снимок разворачивается первым, а folder ставится после: к этому моменту
        # запись уже остановлена и в снимке папки нет, а панели нужно показать,
        # что именно сохранилось.
        return {
            **self.snapshot(),
            "ok": True,
            "action": "cancelled" if discard else "stopped",
            "folder": folder,
        }

    def keep_recording(self) -> dict:
        """«Сохранить как обычную встречу» посреди временной: на «Стоп» она
        перенесётся в библиотеку и дальше — как обычная запись."""
        keep = getattr(self.tray, "keep_temporary", None)
        if keep is None or not keep():
            raise _bad_request("Временная встреча не идёт")
        return {**self.snapshot(), "ok": True, "action": "kept"}

    def shutdown(self) -> dict:
        """Выход по просьбе оболочки: идущая запись сохраняется штатно.

        Запись сохраняет её остановка (с ассистентом — тоже, сразу). Хвосту
        ассистента (лента, сводка) — до SHUTDOWN_WAIT_S, потом его убивают:
        после ответа оболочка даёт резиденту всего 10 с на выход и гасит его
        вместе с ребёнком."""
        if self.tray.recording:
            self.tray.stop_recording()
        self.live.stop(wait=True, timeout=live_control.SHUTDOWN_WAIT_S)
        # Временную встречу сохраняют как обычную — перенос в фоне; не успеет —
        # доделает следующий запуск (отметка `keep`).
        wait_kept = getattr(self.tray, "wait_kept", None)
        if wait_kept is not None:
            wait_kept(KEEP_SHUTDOWN_S)
        self.tray.request_exit()
        return {"ok": True}

    # --- живой режим (запись с ассистентом) -----------------------------

    def live_start(self, body: dict | None = None) -> dict:
        """Запись с ассистентом — это обычная запись резидента (`source:
        live`) и ассистент, подключённый к ней с первой секунды (тот же путь,
        что «Включить ассистента»). Ассистент не запустился или упал — запись
        идёт дальше как обычная, сохраняется и расшифровывается своей
        остановкой. Ответ сразу (`starting`), дальше — события `live.*`.
        `{"profile"?: "work" | "personal"}` — профиль сессии (0.3.7); нет —
        `assist.profile`.

        `{"temporary": true}` — временная встреча с ассистентом
        (`meet.temp_meeting`): запись вне библиотеки, на «Стоп» удаляется
        вместе с чатом и сеансами агента."""
        from meet import assistant

        _session_profile(body)          # негодный профиль — 400 до начала записи
        temporary = (body or {}).get("temporary") is True

        if self.tray.recording:
            raise _bad_request("Идёт обычная запись — включите ассистента в ней "
                               "(«Включить ассистента»)")
        if not _provider_installed(settings.load()):
            raise _conflict(assistant.NO_PROVIDER)
        if self.live.busy():
            raise _bad_request("Ассистент ещё запускается или останавливается — "
                               "попробуйте через несколько секунд")
        from meet import storage
        from meet.tray import RecordAttempt

        # Перенос движка удерживает резидент: запись (и ассистент в ней) не
        # начнётся — понятный отказ вместо «запись уже идёт». Атомарность —
        # в воротах самой записи (`TrayApp.start_recording`).
        if storage.HOLD.held():
            raise _conflict(storage.HOLD_TEXT)
        attempt = RecordAttempt()
        started = (self.tray.start_recording(LIVE, attempt=attempt, temporary=True) if temporary
                   else self.tray.start_recording(LIVE, attempt=attempt))
        if not started:
            raise _bad_request("Запись уже идёт")
        self.tray.log(f"{'временная встреча' if temporary else 'запись с ассистентом'}: "
                      f"запись пошла, подключаю ассистента")
        # Подключаем, когда у этой записи открылись устройства (её
        # `record.started`, доли секунды). Отвод и папка появляются раньше
        # устройств: по ним одним ассистент подключился бы к записи, которая
        # через миг падает («нет устройства»).
        deadline = time.monotonic() + LIVE_RECORD_WAIT_S
        while time.monotonic() < deadline and self.tray.recording and not attempt.done.is_set():
            if attempt.started.wait(0.05):
                break
        if attempt.error or (attempt.done.is_set() and not attempt.folder):
            # Не началась сама запись (lock занят, нет устройства): это её
            # ошибка, ассистенту подключаться не к чему.
            return {**self.live.status(), "ok": False,
                    "error": f"Запись не началась: {attempt.error or 'поток записи завершился'}"}
        if attempt.done.is_set() or not self.tray.recording or self._record_stopping():
            # Запись остановили, пока она открывалась (поток уже вернул папку
            # или ещё дописывает её, а флаг записи снимется через миг): ничего
            # не «продолжается».
            return {**self.live.status(), "ok": True, "action": "stopped"}
        if not attempt.started.is_set():
            # Устройства открываются дольше обычного: запись ещё может пойти
            # (или упасть — о ней скажет её собственная ошибка), ассистента
            # к ней не подключаем вслепую.
            error = (f"Ассистент не запустился: запись не открыла устройства за "
                     f"{LIVE_RECORD_WAIT_S:.0f} с — включите ассистента, когда она пойдёт")
            self.tray.log(error)
            return {**self.live.status(), "ok": False, "error": error}
        from meet.control import BadRequest, Conflict

        try:
            return self.live_attach(body, with_recording=True)
        except (BadRequest, Conflict) as e:
            if not self.tray.recording or self._record_stopping():
                return {**self.live.status(), "ok": True, "action": "stopped"}
            # Запись уже идёт и пусть идёт: ассистент — не повод её терять.
            error = live_control.recording_goes_on(str(e), ready=False)
            self.tray.log(error)
            return {**self.live.status(), "ok": False, "error": error}

    def _record_stopping(self) -> bool:
        """Запись останавливается: «Стоп» уже нажат, поток дописывает дорожки
        (флаг `recording` снимется, когда он закончит)."""
        return bool(getattr(self.tray, "stopping", False))

    def live_stop(self) -> dict:
        """Остановить запись с ассистентом (`source: live`) — это остановка
        самой записи: она сразу сохраняется и встаёт в расшифровку, а
        ассистент дописывает свой хвост после, в фоне (не ждём его).
        Ассистента, включённого посреди обычной записи, это
        только выключает: запись идёт дальше (как `live_detach`). Ассистент
        `meet assist`, пишущий сам, — `/stop` ему."""
        if self.tray.recording and getattr(self.tray, "source", None) == LIVE:
            threading.Thread(target=self.tray.stop_recording, name="meet-live-stop",
                             daemon=True).start()
            return {"ok": True, "action": "stopping", **self.live.status()}
        if self._live_attached():
            return self.live_detach()
        return self.live.stop()

    def live_attach(self, body: dict | None = None, with_recording: bool = False) -> dict:
        """«Включить ассистента» посреди обычной записи: ребёнок `meet assist`
        берёт звук из отвода записи (`meet.pcm_tap`), второй раз устройства не
        открывает и lock записи не трогает; сначала догоняет уже записанное
        (с дорожек на диске), дальше слушает вживую. Ответ сразу (`starting`).
        `{"profile"?: "work" | "personal"}` — профиль сессии; нет — из журнала
        встречи (ассистента в неё уже включали) или `assist.profile`."""
        from meet import assistant, pcm_tap

        profile = _session_profile(body)
        if not self.tray.recording:
            raise _bad_request("Запись не идёт — включить ассистента можно только во время записи")
        if getattr(self.tray, "stopping", False):
            raise _bad_request("Запись останавливается — ассистента в неё уже не включить")
        if self.live.busy():
            st = self.live.status()
            raise _bad_request("Ассистент уже включён" if st.get("attached") or st.get("active")
                               else "Ассистент ещё запускается или останавливается — "
                                    "попробуйте через несколько секунд")
        # «Запись с ассистентом» провайдера уже проверила: второй раз — лишняя
        # секунда к ответу (TCP к локальной модели), а у оболочки предел.
        if not with_recording and not _provider_installed(settings.load()):
            raise _conflict(assistant.NO_PROVIDER)
        hub = getattr(self.tray, "pcm_tap", None)
        if hub is None or not hub.active():
            raise _bad_request("Запись ещё не началась или уже останавливается — "
                               "попробуйте через несколько секунд")
        folder = Path(self.tray._current_folder())
        if not folder.is_dir():
            raise _bad_request("Папка записи ещё не создана — попробуйте через секунду")
        started = getattr(self.tray, "started", 0.0) or 0.0
        started_at = time.time() - max(0.0, time.monotonic() - started) if started else None
        server = pcm_tap.TapServer(hub, log=self.tray.log)

        def reopen():
            # Повтор упавшего старта (LiveControl): отвод одноразовый — новый,
            # пока запись идёт; кончилась — повторять некуда.
            if not hub.active() or getattr(self.tray, "stopping", False):
                return None
            return pcm_tap.TapServer(hub, log=self.tray.log)

        # Хвост ассистента прошлой записи ещё пишется — один ребёнок за раз.
        self.live.end_tail(TAIL_CUT_WAIT_S)
        if not self.tray.recording or self._record_stopping() or not hub.active():
            # Пока обрывали хвост (до 5 + 5 с), эту запись остановили: ребёнок
            # для неё уже не нужен (а остановка его не увидела бы и не ждала).
            server.close()
            raise _bad_request("Запись не идёт — её остановили, пока ассистент "
                               "прошлой записи дописывал сводку")
        try:
            reply = self.live.start(self._root(), attach={
                "folder": str(folder), "server": server, "started_at": started_at,
                "reopen": reopen, "with_recording": with_recording, "profile": profile})
        except live_control.LiveBusy as e:
            raise _bad_request(str(e))
        if reply.get("ok"):
            self.tray.log(f"ассистент включается посреди записи: {temp_meeting.loggable(folder)}")
        return reply

    def live_detach(self) -> dict:
        """«Выключить ассистента»: запись идёт дальше, его сводка остаётся в
        папке записи с пометкой «неполная». Ответ сразу."""
        if not self._live_attached():
            return {**self.live.status(), "ok": False, "action": "not-attached"}
        self.tray.log("ассистент выключается, запись продолжается")
        return self.live.stop(detach=True, reason=live_control.ENDED_DETACH)

    def _live_attached(self) -> bool:
        attached = getattr(self.live, "attached", None)  # подмена в тестах может не уметь
        try:
            return bool(attached and attached())
        except Exception:
            return False

    def _finish_attached(self, discard: bool = False) -> None:
        """Запись остановлена (или отменена): захват кончился в момент «Стоп»,
        отвод закрыт. Сохранение записи ассистента не ждёт: он дописывает
        хвост ленты и сводку в её папку в фоне (`finish_after_recording`, до
        STOP_TIMEOUT_S) и ничему не мешает. Отменённую запись удаляют сразу —
        ассистента перед этим убивают."""
        if not self._live_attached():
            return
        if discard:
            self.live.abort(reason=live_control.ENDED_RECORDING)
            return
        self.live.finish_after_recording()

    def _settle_attached(self) -> None:
        """Ассистент остановленной записи дописывает хвост в её папку —
        дождаться (не дольше KEEP_SETTLE_S): папку сейчас перенесут."""
        if self.live.finishing():
            self.live.stop(wait=True, timeout=KEEP_SETTLE_S)

    def _live_call(self, call, *args) -> dict:
        try:
            return call(*args)
        except live_control.LiveNotRunning as e:
            raise _conflict(str(e))
        except live_control.LiveError as e:
            raise _bad_request(str(e))

    def live_ask(self, body: dict | None) -> dict:
        """Вопрос ассистенту или быстрое действие (`quick`, тогда вопрос
        не нужен); `since_t` — с какой секунды записи «Что я пропустил?»."""
        body = body or {}
        question, quick, since = body.get("question"), body.get("quick"), body.get("since_t")
        if quick is not None and quick not in live_control.QUICK_ACTIONS:
            raise _bad_request("неизвестное быстрое действие")
        if since is not None and (isinstance(since, bool) or not isinstance(since, (int, float))
                                  or since < 0):
            raise _bad_request("since_t — секунды от начала записи")
        if question is None and quick is not None:
            question = ""
        if not isinstance(question, str) or (quick is None and not question.strip()):
            raise _bad_request("пустой вопрос")
        if len(question) > QUESTION_MAX_CHARS:
            raise _bad_request(f"вопрос длиннее {QUESTION_MAX_CHARS} символов")
        return self._live_call(self.live.ask, question.strip(), quick, since)

    def live_hint(self, body: dict | None) -> dict:
        """Действие с подсказкой: `{"id": "h3", "action": "pin|unpin|dismiss|restore"}`."""
        body = body or {}
        hint_id, action = body.get("id"), body.get("action")
        if not isinstance(hint_id, str) or not re.fullmatch(r"h\d{1,6}", hint_id):
            raise _bad_request("неизвестная подсказка")
        if action not in live_control.HINT_ACTIONS:
            raise _bad_request("действие с подсказкой: pin, unpin, dismiss или restore")
        return self._live_call(self.live.hint, hint_id, action)

    def live_task(self, body: dict | None) -> dict:
        task = (body or {}).get("task")
        if not isinstance(task, str) or not task.strip():
            raise _bad_request("пустая задача")
        return self._live_call(self.live.task, task.strip())

    def live_events(self, last_event_id: str | None = None):
        return self._live_call(self.live.open_events, last_event_id)

    # --- чат агента-участника: во время встречи — прокси к ребёнку --------

    def live_chat(self, params: dict | None = None) -> dict:
        """Лента чата идущей встречи (`GET /chat` ребёнка)."""
        limit = (params or {}).get("limit")
        if limit is not None:
            try:
                limit = max(0, min(int(limit), 5000))
            except (TypeError, ValueError):
                raise _bad_request("limit — число")
        return self._live_call(self.live.chat, limit)

    def live_chat_post(self, body: dict | None) -> dict:
        """Сообщение пользователя агенту: `{"text", "attachments"?: ["a3"],
        "client_id"?}`. Ответ сразу (ребёнок — 202), повтор с тем же
        `client_id` — то же сообщение."""
        return self._live_call(self.live.chat_post, _chat_message(body))

    def live_chat_paste(self, data: bytes, content_type: str | None,
                        name: str | None = None) -> dict:
        """Вставленная картинка (сырое тело до 10 МБ) — ребёнку тем же телом."""
        kind = (content_type or "").split(";")[0].strip().lower()
        if kind not in CHAT_PASTE_TYPES:
            raise _bad_request("нужна картинка: PNG, JPEG, WEBP, GIF, BMP или TIFF")
        if name is not None:
            from urllib.parse import unquote

            name = os.path.basename(unquote(name).replace("\\", "/"))[:200] or None
        return self._live_call(self.live.chat_paste, data, kind, name)

    def live_chat_attach(self, body: dict | None) -> dict:
        """Файл или папка с диска (выбор файла, перетаскивание в окне):
        `{"path"}`. Проверяет и резидент (полный локальный путь, существует),
        и ребёнок; ребёнку путь уходит с токеном доверенного вызывающего."""
        return self._live_call(self.live.chat_attach, _chat_path((body or {}).get("path")))

    def live_chat_click(self, mid: str, body: dict | None) -> dict:
        """Нажатие кнопки сообщения агента: `{"label", "client_id"?}`."""
        mid = _chat_mid(mid)
        body = body or {}
        label = body.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise _bad_request("label — надпись кнопки")
        payload = {"label": label}
        cid = _chat_client_id(body)
        if cid:
            payload["client_id"] = cid
        return self._live_call(self.live.chat_click, mid, payload)

    def live_chat_confirm(self, mid: str, body: dict | None) -> dict:
        """Карточка подтверждения Meet во время встречи: `{"allow": bool}` →
        ребёнку (ворота агента ждут решения там)."""
        mid = _chat_mid(mid)
        allow, meeting = (body or {}).get("allow"), (body or {}).get("meeting", False)
        if not isinstance(allow, bool) or not isinstance(meeting, bool):
            raise _bad_request("allow, meeting — true или false")
        return self._live_call(self.live.chat_confirm, mid, {"allow": allow, "meeting": meeting})

    def live_chat_revoke(self, mid: str, body: dict | None = None) -> dict:
        """Отозвать разрешение «до конца встречи» (× в шапке чата)."""
        return self._live_call(self.live.chat_revoke, _chat_mid(mid))

    def live_chat_react(self, mid: str, body: dict | None) -> dict:
        """Реакция на сообщение агента: `{"emoji": "👍|👎|❓", "on"?: bool|null}`."""
        mid = _chat_mid(mid)
        body = body or {}
        emoji, on = body.get("emoji"), body.get("on")
        if emoji not in ("👍", "👎", "❓"):
            raise _bad_request("реакция — 👍, 👎 или ❓")
        if on is not None and not isinstance(on, bool):
            raise _bad_request("on — true, false или null")
        return self._live_call(self.live.chat_react, mid, {"emoji": emoji, "on": on})

    def live_chat_remove(self, aid: str) -> dict:
        """Вложение убрали из строки ввода до отправки (`a<N>`): ни в ленте, ни
        у агента его не будет. Уже отправленное — 400 от ребёнка."""
        if not isinstance(aid, str) or not re.fullmatch(r"a\d{1,9}", aid):
            raise _bad_request("неизвестное вложение")
        return self._live_call(self.live.chat_remove, aid)

    def live_chat_stop(self, body: dict | None) -> dict:
        """«Стоп» у ответа, который пишется: `{"id"?}`."""
        mid = (body or {}).get("id")
        payload = {} if mid is None else {"id": _chat_mid(mid)}
        return self._live_call(self.live.chat_stop, payload)

    def agent_frequency(self, body: dict | None) -> dict:
        """«Как часто писать» (`{"frequency": less|normal|more|реже|обычно|
        чаще}`): в настройки ключом (`assist.frequency`) и агенту идущей
        встречи сразу, если он есть. → `{"frequency", "label", "live"}`."""
        key = settings.frequency_key((body or {}).get("frequency"))
        if key is None:
            raise _bad_request("frequency — less, normal или more (реже, обычно, чаще)")
        settings.patch({"assist": {"frequency": key}})
        live = False
        try:
            reply = self.live.agent_frequency(key)
            # Ребёнок без агента-участника («Только сводка») отвечает live: false.
            live = isinstance(reply, dict) and reply.get("live") is True
        except live_control.LiveNotRunning:
            pass
        except (live_control.LiveError, RuntimeError) as e:
            # Ассистент без агента-участника или старый — настройка всё равно сохранена.
            self.tray.log(f"«Как часто писать» не передано ассистенту: {e}")
        return {"frequency": key, "label": settings.FREQUENCY_LABELS[key], "live": live}

    def live_profile(self, body: dict | None) -> dict:
        """Профиль идущей сессии ассистента (`{"profile": work|personal}`,
        0.3.7): только эта сессия — настройка по умолчанию (`assist.profile`)
        не меняется. Ассистента нет — 409. → `{"profile", "label", "live"}`."""
        key = _session_profile(body)
        if key is None:
            raise _bad_request("profile — work или personal")
        reply = self._live_call(self.live.agent_profile, key)
        live = isinstance(reply, dict) and reply.get("live") is True
        return {"profile": key, "label": settings.PROFILE_LABELS[key], "live": live}

    def adopt_recording(self) -> dict:
        """Автозапись → ручная: детектор её больше не остановит."""
        if not self.tray.recording:
            return {**self.snapshot(), "ok": False, "action": "not-recording"}
        self.tray.source = MANUAL
        self.tray.watcher.suppress()
        self.tray.log("автозапись переведена в ручную из панели")
        return {**self.snapshot(), "ok": True, "action": "adopted"}

    def set_auto_record(self, body: dict) -> dict:
        """Переключатель автозаписи из трея и настроек. Детектор читает
        cfg["enabled"] на каждом такте, поэтому применяется сразу — в отличие
        от остальных полей auto_record, которые требуют перезапуска."""
        enabled = (body or {}).get("enabled")
        if not isinstance(enabled, bool):
            raise _bad_request("ожидается enabled: true/false")
        # Сначала на диск, потом вживую: не сохранилось — трей не должен
        # разойтись с config.json (после перезапуска вернулось бы старое).
        settings.patch({"auto_record": {"enabled": enabled}})
        self.tray.cfg["enabled"] = enabled
        self.tray.log(f"автозапись {'включена' if enabled else 'выключена'} из приложения")
        return self.snapshot()

    # --- настройки и диагностика ----------------------------------------

    def settings(self) -> dict:
        return settings.load().to_raw()

    # --- профили людей убраны (0.3.2) -------------------------------------

    def profiles_removed(self) -> dict:
        """Отметка об уборке профилей (meet.retired_profiles) для строки в
        «Голосах»: {"notice": {"notes", "folder"} | None}."""
        from meet import retired_profiles

        return {"notice": retired_profiles.notice(paths.data_dir())}

    def dismiss_profiles_removed(self) -> dict:
        """«Понятно»: строку больше не показывать."""
        from meet import retired_profiles

        retired_profiles.dismiss(paths.data_dir())
        return {"ok": True}

    def patch_settings(self, updates: dict) -> dict:
        integrations = (updates or {}).get("integrations")
        if isinstance(integrations, dict) and str(integrations.get("hf_token") or "").strip():
            self._hf_check = None  # токен сменился — прежняя проверка не о нём
        try:
            updated = settings.patch(updates or {})
        except ValueError as e:  # шаблон папки, имя файла выгрузки
            raise _bad_request(str(e))
        touched = [name for name in RESTART_REQUIRED_SECTIONS if name in (updates or {})]
        return {
            "settings": updated.to_raw(),
            # Не «применено», а «применится»: врать про живую перезагрузку хуже,
            # чем показать кнопку «перезапустить дежурного».
            "restart_required": touched,
        }

    def processes(self) -> dict:
        """Запущенные процессы — чтобы выбирать клиент конференции из списка, а
        не вспоминать имя exe.

        Отдаём всё, что живо, помечая известные клиенты и уже выбранные: угадать
        за пользователя, какая программа у него для звонков, нельзя, а показать
        реальность — можно."""
        selected = set(self.tray.cfg["processes"])
        known = set(settings.default_processes())
        names: set[str] = set()
        try:
            import psutil

            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").strip()
                if name:
                    names.add(name)
        except Exception as e:  # psutil не встал — не повод падать
            return {"available": False, "error": str(e),
                    "selected": sorted(selected), "known": sorted(known)}
        return {
            "available": True,
            "running": sorted(names, key=str.lower),
            "selected": sorted(selected),
            "known": sorted(known),
        }

    def devices(self, probe=None) -> dict:
        """Устройства для настроек «Звук»: микрофоны и устройства вывода
        (`inputs`/`outputs`, системные помечены `default`) и что запись видит
        сейчас (`system` — loopback, `mic`).

        IMPORTANT: спрашиваем **подпроцессом**, а не здесь. PortAudio считает
        ссылки на инициализацию, в проекте живёт один PyAudio-инстанс, и второй,
        созданный и завершённый в потоке HTTP-сервера, рушил состояние
        PortAudio — резидент падал целиком с segfault (поймано faulthandler'ом
        18.08.2026). Подпроцесс умирает вместе со своей инициализацией.

        Выбор хранится по имени (`recording.mic_device` / `output_device`):
        null — системное, за сменой которого запись следит; выбранное запись
        ищет по имени при каждом (пере)открытии дорожки, а не найдя — пишет с
        системного (см. recorder)."""
        now = time.monotonic()
        if self._devices_cache and now - self._devices_at < DEVICES_TTL_S:
            return self._devices_cache
        data = (probe or _probe_devices)()
        data["pinning"] = True
        # Кэшируем только успех: иначе мгновенный сбой подпроцесса залипал бы на
        # 15 с, и «Сбросить» не помогал бы, пока TTL не истёк.
        if data.get("available"):
            self._devices_cache, self._devices_at = data, now
        return data

    def test_device(self, body: dict | None, probe=None) -> dict:
        """«Проверить» в настройках звука: ~2 с с микрофона или с loopback
        устройства вывода → `{"peak": 0..1, "device", "fallback"}`.

        IMPORTANT: тоже подпроцессом (см. devices). Во время записи — отказ:
        проверка открыла бы то же устройство вторым потребителем, а мерить
        уровень идущей записи и так видно по её индикатору."""
        body = body if isinstance(body, dict) else {}
        kind, name = body.get("kind"), body.get("name")
        if kind not in ("mic", "output"):
            raise _bad_request("ожидается kind: mic или output")
        if name is not None and not isinstance(name, str):
            raise _bad_request("name — имя устройства или null")
        if self.tray.recording or self.live.busy():
            raise _conflict("Идёт запись — проверка устройства недоступна")
        if not self._device_check.acquire(blocking=False):
            raise _conflict("Проверка устройства уже идёт")
        try:
            result = (probe or _check_device)(kind, (name or "").strip() or None)
        finally:
            self._device_check.release()
        if not result.get("ok"):
            # Сбой проверки — не ошибка запроса: устройство или подпроцесс
            # сейчас недоступны (503), текст — человеку.
            from meet.control import Unavailable

            raise Unavailable(
                f"Не удалось проверить устройство: {result.get('error') or 'нет ответа'}")
        return result

    # --- библиотека и задачи --------------------------------------------

    def _root(self) -> Path:
        return settings.load().recording.recordings

    def _folder(self, recording_id: str) -> Path | None:
        """Папка записи по id. Проверка, что она внутри папки записей — не
        паранойя: id приходит из сети, и `..` в нём открыл бы чтение чего
        угодно на диске."""
        root = self._root().resolve()
        candidate = (root / recording_id).resolve()
        if candidate.parent != root or not candidate.is_dir():
            return None
        return candidate

    @staticmethod
    def _library_filter(categories: str | None = None, filters: dict | None = None):
        """Фильтр карточек (meet.library_filter) из параметров адреса; прежний
        `categories` — тот же параметр. Негодное значение — 400 с текстом."""
        from meet import library_filter

        params = dict(filters or {})
        if categories and "categories" not in params:
            params["categories"] = categories
        try:
            return library_filter.from_params(params, settings.load())
        except library_filter.FilterError as e:
            raise _bad_request(str(e))

    def recordings(self, limit: int = 200, q: str | None = None, categories: str | None = None,
                   filters: dict | None = None) -> dict:
        """Записи от свежих к старым — из кэша карточек поиска (meet.search):
        папки перечитываются, только когда меняются. Окно просит все (5000),
        трей — последние 200. Фильтр (`filters`, см. meet.library_filter) —
        до лимита."""
        from meet import search

        root = self._root()
        flt = self._library_filter(categories, filters)
        cards = [c for c in search.cards(root) if flt(c)]
        if (q or "").strip():
            items = library.search_cards(cards, q, limit=limit, title_only=flt.title_only)
        else:
            items = cards[:max(0, limit)]
        return {"root": str(root), "items": [dict(c) for c in items]}

    def search(self, q: str, limit: int = 200, categories: str | None = None,
               filters: dict | None = None) -> dict:
        """Поиск по тексту встреч (и названиям): записи с фрагментами реплик и
        подсветкой названия. Фильтр (`filters`, см. meet.library_filter) — по
        карточке до чтения транскриптов; `in=title` — только в названиях."""
        from meet import search

        flt = self._library_filter(categories, filters)
        return {"items": search.search_library(self._root(), q or "", limit=limit,
                                               keep=flt if flt.active else None,
                                               title_only=flt.title_only, title_terms=flt.title)}

    def delete_recording(self, recording_id: str) -> dict:
        """Удалить папку записи целиком. Отказ, пока в неё пишут или над ней
        работает расшифровка/импорт: иначе задача упала бы на исчезнувших файлах."""
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        reason = self._busy_reason(folder)
        if reason:
            raise _bad_request(reason)
        # Анализ встречи — производное: его снимаем, а не отказываем в удалении.
        self._drop_analysis(folder)
        try:
            self._remove(folder)
        except library.FolderBusy as e:
            raise _conflict(str(e))
        return {"ok": True}

    def _busy_reason(self, folder: Path, merging: Path | None = None,
                     model: bool = True) -> str | None:
        """Почему запись сейчас нельзя удалить или объединить; None — можно.
        `merging` — объединённая запись, чьи исходные удаляем: её собственное
        «эта запись — часть объединения» не считается. `model=False` — работа
        модели (итоги, вопрос) не мешает: она лишь читает расшифровку."""
        folder = Path(folder).resolve()
        owner = self._merge_owner(folder, merging)
        if owner is not None:
            return (f"запись входит в объединение «{owner}», которое ещё не завершено. "
                    "Дождитесь расшифровки объединённой записи или удалите её")
        if self._processing_now(folder):
            return PROCESSING[:1].lower() + PROCESSING[1:]
        if (self.tray.recording
                and Path(self.tray._current_folder()).resolve() == folder):
            return "запись ещё идёт"
        live = self.live.status()
        live_folder = live["folder"]
        if live_folder and Path(live_folder).resolve() == folder:
            if live.get("finishing"):
                # Запись сохранена, ассистент дописывает в её папку сводку (≤ 90 с).
                return LIVE_TAIL_BUSY
            return "запись ещё идёт"
        active = self.queue.active_for(str(folder), jobs.FOLDER_KINDS)
        if active is not None:
            if active.kind == jobs.MERGE:
                return "идёт объединение записей — дождитесь его"
            return "идёт расшифровка — отмените её или дождитесь"
        # Задача модели (и её CLI) работает с cwd в папке записи: на Windows
        # rmtree снёс бы файлы и упал на самой папке, а задача дописала бы
        # summary.md и meta.json в осиротевшую папку.
        # Анализ встречи (фоновая задача) сюда не входит: удаление и объединение
        # снимают его сами (_drop_analysis) — его можно сделать заново.
        if model and self.llm_queue.active_for(str(folder), (jobs.SUMMARY, jobs.ASK)):
            return "Идёт работа модели — отмените или дождитесь"
        # Голоса реплик и повторная диаризация читают звук записи: удалять или
        # объединять её посреди счёта нельзя (правкам спикеров они не мешают).
        if model and self.queue.active_for(str(folder), jobs.SPEAKER_KINDS):
            return "идёт разбор голосов записи — отмените его или дождитесь"
        return None

    @staticmethod
    def _merge_owner(folder: Path, ignore: Path | None) -> str | None:
        from meet import merge

        return merge.unfinished_owner(folder, ignore)

    def _remove(self, *folders: Path) -> None:
        """Удалить папки записей — все или ни одной. Плеер карточки мог только
        что запросить дорожку, а фоновое сведение — писать playback.opus: на
        Windows открытый файл не удалить, и rmtree снёс бы запись наполовину.
        Ответы плееру короткие (control.AUDIO_CHUNK), поэтому ждём недолго.
        Папку держит другая программа (агент во вкладке «Агент» работает в ней)
        дольше пары секунд — library.FolderBusy, ничего не тронуто."""
        from meet import playback

        for folder in folders:
            playback.wait_idle(folder, DELETE_WAIT_S)
        library.remove_folders(folders)

    # --- объединение встреч -------------------------------------------------

    def merge_recordings(self, body: dict | None) -> dict:
        """Объединить записи: новая папка и задача сборки звука; расшифровка —
        следом, удаление исходных (если не просили оставить) — после неё
        (см. meet.merge). Ни одна из записей не должна сейчас писаться или
        обрабатываться: звук в ней ещё не окончательный."""
        from meet import merge

        body = body if isinstance(body, dict) else {}
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise _bad_request("ожидается ids — список записей")
        folders = []
        for rid in ids:
            folder = self._folder(rid)
            if folder is None:
                raise _bad_request(f"записи нет: {rid}")
            reason = self._busy_reason(folder)
            if reason:
                raise _bad_request(f"{folder.name}: {reason}")
            folders.append(folder)
        keep = settings.as_flag(body.get("keep_originals"), False)
        # Анализ частей не нужен: объединённая встреча получит свой после
        # расшифровки. Снимаем его, только когда объединение состоится; без
        # «оставить исходные» — до проверки «можно ли удалить»: идущий анализ
        # работает в папке записи и держит её.
        if not keep:
            for folder in folders:
                self._drop_analysis(folder)
            try:
                # Исходные удалятся после расшифровки: занятую папку (агент в ней)
                # лучше назвать сейчас, чем молча оставить исходные потом.
                library.wait_removable(folders)
            except library.FolderBusy as e:
                self._restore_analysis(folders)
                raise _conflict(str(e))
        try:
            target = merge.create(self._root(), folders, keep_originals=keep)
        except merge.MergeError as e:
            if not keep:
                self._restore_analysis(folders)
            raise _bad_request(str(e))
        if keep:
            for folder in folders:
                self._drop_analysis(folder)
        job, _ = self._submit_once(jobs.MERGE, target)
        self.tray.log(f"объединение записей: {', '.join(f.name for f in folders)} → "
                      f"{target.name} ({job.id})")
        return {"recording": target.name, "job": job.to_raw()}

    @staticmethod
    def _merge_info(folder: Path) -> dict | None:
        meta = library.read_meta(folder)
        info = meta.get("merge")
        return info if meta.get("source") == "merge" and isinstance(info, dict) else None

    def _merge_sound_ready(self, folder: Path) -> None:
        """Звук объединённой записи собран — обычная расшифровка следом:
        спикеры одни на всю встречу, перерывы встанут отметками."""
        try:
            job, created = self._submit_once(jobs.TRANSCRIBE, folder)
        except Exception as e:  # подписчик шины: не ронять очередь задач
            self.tray.log(f"расшифровка объединённой записи не поставлена ({folder.name}): {e}")
            return
        if created:
            self.tray.log(f"записи объединены, расшифровка в очереди: {folder.name} ({job.id})")

    def _finish_merge(self, folder: Path) -> None:
        """Объединённая встреча расшифрована: удалить исходные записи, если не
        просили оставить. Хоть одна занята (пишется, обрабатывается, папку
        держит агент) — не удаляем ни одной: половина удалённых частей хуже,
        чем все на месте. Работает в фоновом потоке: любой сбой — в журнал."""
        try:
            self._finish_merge_now(folder)
        except Exception as e:
            self.tray.log(f"объединение не завершено ({folder.name}): {type(e).__name__}: {e}")

    def _finish_merge_now(self, folder: Path) -> None:
        from meet import merge

        info = self._merge_info(folder)
        if info is None or info.get("state") != "merged":
            # "done" — уже обработано; "pending" — звук не собран до конца:
            # исходные — единственная полная копия, их не трогаем.
            if info is not None and info.get("state") == "pending":
                self.tray.log(f"объединение не завершено — исходные записи не удаляю: {folder.name}")
            return
        deleted: list[str] = []
        kept_reason = None
        if not info.get("keep_originals"):
            sources = merge.originals(folder)
            busy = [(f.name, r) for f in sources if (r := self._busy_reason(f, merging=folder))]
            if not busy:
                for source in sources:
                    self._drop_analysis(source)
            if busy:
                kept_reason = f"{busy[0][0]}: {busy[0][1]}"
                self.tray.log(f"исходные записи не удалены ({folder.name}): {kept_reason}")
            else:
                try:
                    self._remove(*sources)
                    deleted = [source.name for source in sources]
                except Exception as e:
                    kept_reason = str(e) or type(e).__name__
                    self.tray.log(f"исходные записи не удалены ({folder.name}): {kept_reason}")
        if info.get("kb_exported"):
            self.tray.log("прежние папки исходных записей в базе знаний не тронуты: "
                          + ", ".join(info["kb_exported"]))
        merge.mark_done(folder, deleted, kept_reason)
        self._updated(folder)

    # --- hotwords ---------------------------------------------------------

    def _hotwords_reply(self, text: str) -> dict:
        # Те же правила, что у расшифровки (meet.hotwords): повторы не
        # считаются, бюджет общий — счётчик не должен врать о лимите.
        return {"text": text, "budget": hotwords.HOTWORDS_CHAR_BUDGET,
                "used": len(", ".join(hotwords.terms(text)))}

    def get_hotwords(self) -> dict:
        return self._hotwords_reply(hotwords.read(paths.hotwords_path()))

    def remove_hotword(self, body: dict | None) -> dict:
        """Убрать один термин (отмена «Добавлено в термины»): только его строку,
        остальной список — как есть, даже если его правили между делом."""
        term = (body or {}).get("term")
        if not isinstance(term, str) or not term.strip():
            raise _bad_request("term должен быть непустой строкой")
        path = paths.hotwords_path()
        try:
            text = hotwords.remove_term(hotwords.read(path), term)
            hotwords.write(path, text)
        except OSError as e:
            raise RuntimeError(f"не удалось сохранить список слов: {e}") from e
        return self._hotwords_reply(text)

    def put_hotwords(self, body: dict) -> dict:
        text = (body or {}).get("text")
        if not isinstance(text, str):
            raise _bad_request("text должен быть строкой")
        try:
            hotwords.write(paths.hotwords_path(), text)
        except OSError as e:
            raise RuntimeError(f"не удалось сохранить список слов: {e}") from e
        return self._hotwords_reply(text)

    def recording(self, recording_id: str) -> dict:
        folder = self._folder(recording_id)
        card = library.describe(folder) if folder else None
        if card is None:
            return {"error": "записи нет"}
        raw = card.to_raw()
        raw["transcript"] = _window_transcript(library.read_transcript_full(folder), folder)
        raw["edit_head"] = self._edit_head(folder)
        jira = self._jira_refs(folder, raw["transcript"])
        if jira is not None:
            raw["jira"] = jira
        return raw

    def _jira_refs(self, folder: Path, transcript: dict | None) -> dict | None:
        """Задачи Jira, названные во встрече (meet.jira_refs): ссылки по
        сегментам расшифровки (свои и из анализа) и фразы итогов и наблюдений.
        None — ссылки выключены или адреса Jira нет. Сбой — без ссылок: карточка
        важнее."""
        from meet import analysis, assistant, jira_refs

        try:
            cfg = settings.load()
            if jira_refs.spec_of(cfg) is None:
                return None
            # Карточку перечитывают на каждом шаге задач записи: тот же текст,
            # итоги, анализ и настройки ссылок — тот же ответ, без пересчёта.
            stamps = []
            for path in (library.transcript_path(folder), folder / assistant.SUMMARY_MD,
                         folder / analysis.ANALYSIS_JSON):
                try:
                    st = path.stat()
                    stamps.append((st.st_mtime_ns, st.st_size))
                except OSError:
                    stamps.append(None)
            key = (str(folder), tuple(stamps), json.dumps(
                [cfg.integrations.to_raw(), cfg.transcript_view.jira, cfg.analysis.issues],
                sort_keys=True, default=str))
            cache = self.__dict__.setdefault("_jira_cache", {})
            if key in cache:
                return cache[key]
            summary = assistant.read_summary(folder)
            got = jira_refs.for_recording(transcript, cfg, analysis_doc=analysis.read(folder),
                                          summary=(summary or {}).get("markdown"))
            if len(cache) >= JIRA_CACHE_MAX:
                cache.pop(next(iter(cache)))
            cache[key] = got
            return got
        except Exception as e:  # noqa: BLE001 — ссылки необязательны
            self.tray.log(f"ссылки на Jira не посчитаны ({folder.name}): {type(e).__name__}")
            return None

    def _edit_head(self, folder: Path) -> str | None:
        """Последний применённый шаг истории встречи (для «Отменить» у итогов)."""
        from meet import speakers

        try:
            return speakers.head(folder)
        except (OSError, ValueError):
            return None

    def update_recording(self, recording_id: str, body: dict) -> dict:
        """Переименовать запись. Название живёт в meta.json, а не в транскрипте:
        его можно дать и записи, которая ещё не расшифрована. Пустое — вернуть
        автоматическое (из транскрипта или по дате). Выгруженную в базу знаний
        встречу выгружаем заново, и её папка следует за названием
        (`kb_export.follow_title`) — в фоне, как и прочая выгрузка."""
        from meet import kb_export

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        card = library.describe(folder)
        if card is None:
            return {"error": "записи нет"}
        title = str((body or {}).get("title") or "").strip()[:TITLE_MAX]
        # "ai" — человек принял предложенное моделью («Предложить название»);
        # всё остальное — название человека, его модель больше не тронет.
        source = "ai" if (body or {}).get("title_source") == "ai" and title else "user"
        # Какая модель его предложила (`title_llm` из ответа «Предложить название»).
        origin = _origin((body or {}).get("title_llm")) if source == "ai" else None
        exported = kb_export.previously_exported(folder)
        old_title = kb_export.meeting(folder)[0] if exported else None
        if title:
            # Принятое предложение модели — выбор человека: автоматически его
            # больше не меняют (title_accepted), бейдж «ИИ» остаётся.
            library.update_meta(folder, lambda meta: {
                **{k: v for k, v in meta.items() if k not in ("title_accepted", "title_llm")}, "title": title,
                "title_source": source, **({"title_accepted": True} if source == "ai" else {}),
                **({"title_llm": origin} if origin else {})})
        else:
            library.update_meta(folder, lambda meta: {
                k: v for k, v in meta.items()
                if k not in ("title", "title_source", "title_accepted", "title_llm")})
        if exported:
            self._background(lambda: self._follow_title(folder, old_title))
        return library.describe(folder).to_raw()

    def _retitle_ai(self, folder: Path, title, why: str, apply=None,
                    origin: dict | None = None) -> str | None:
        """Название от модели (анализ, итоги, тема живого режима) — по правилам
        meet.titles: только при включённом «Придумывать название» и только
        вместо автоматического, прежнего от модели или общего из окна звонка.
        Тот же путь, что переименование из окна: папка в базе знаний следует за
        названием, окно перечитывает список. Фоновый поток: сбой — в журнал.
        `apply` — правило записи (по умолчанию `titles.apply_ai`); `origin` —
        какая модель придумала название (`title_llm`)."""
        from meet import kb_export, titles

        try:
            cfg = settings.load()
            if not cfg.assistant.auto_title:
                return None
            exported = kb_export.previously_exported(folder)
            old_title = kb_export.meeting(folder)[0] if exported else None
            applied = (apply(folder, title, cfg) if apply is not None
                       else titles.apply_ai(folder, title, cfg, origin=_origin(origin)))
        except Exception as e:
            self.tray.log(f"название от модели не поставлено ({Path(folder).name}): "
                          f"{type(e).__name__}: {e}")
            return None
        if not applied:
            return None
        self.tray.log(f"название встречи от модели ({why}): {Path(folder).name} → «{applied}»")
        self._updated(folder)
        if exported:
            self._follow_title(folder, old_title)
        return applied

    def _recategorize_ai(self, folder: Path, doc: dict) -> None:
        """Категория от модели из готового анализа — по правилам
        meet.categories: только при включённом «Определять категорию
        автоматически» и никогда вместо выбранной человеком. Выгруженную в базу
        знаний встречу выгружаем заново: категория — в её заметке."""
        from meet import categories, kb_export

        try:
            changed = categories.apply_ai(folder, doc, settings.load())
        except Exception as e:
            self.tray.log(f"категория от модели не поставлена ({Path(folder).name}): "
                          f"{type(e).__name__}: {e}")
            return
        if not changed:
            return
        got = categories.of(library.read_meta(folder))
        self.tray.log(f"категория встречи от модели: {Path(folder).name} → "
                      f"{got['id'] if got else 'без категории'}")
        self._updated(folder)
        if kb_export.previously_exported(folder):
            self._background(lambda: self._auto_kb_export(folder))

    def set_category(self, recording_id: str, body: dict | None) -> dict:
        """Категорию выбрал человек: {"id": "<id из настроек>"} или {"id": null}
        («Без категории»). Модель её больше не меняет. Выгруженную в базу знаний
        встречу выгружаем заново (в фоне)."""
        from meet import categories, kb_export

        folder = self._folder(recording_id)
        if folder is None or library.describe(folder) is None:
            return {"error": "записи нет"}
        body = body or {}
        if "id" not in body:
            raise _bad_request("нужен id категории или null")
        cid = body.get("id")
        if cid is not None:
            if not isinstance(cid, str) or not categories.known(settings.load(), cid):
                raise _bad_request("такой категории нет — обновите список в настройках")
        if categories.set_user(folder, cid):
            self._updated(folder)
            if kb_export.previously_exported(folder):
                self._background(lambda: self._auto_kb_export(folder))
        return library.describe(folder).to_raw()

    # --- группы встреч (meet.groups) -----------------------------------

    def _groups_changed(self, op: str, gid: str | None = None, moved_broken: str | None = None) -> None:
        try:
            self.bus.emit(GROUPS_CHANGED, op=op, **({"id": gid} if gid else {}),
                          **({"moved_broken": moved_broken} if moved_broken else {}))
        except Exception:
            pass  # шина — не повод ронять действие, которое уже сделано

    @staticmethod
    def _group_call(fn, *args, **kwargs):
        """Вызов meet.groups: нет группы — {"error"} (404), негодный запрос —
        400, файл групп занят — 503 (ничего не записано)."""
        from meet import groups
        from meet.control import Unavailable

        try:
            return fn(*args, **kwargs)
        except groups.NoGroup as e:
            return {"error": str(e)}
        except groups.GroupError as e:
            raise _bad_request(str(e))
        except groups.Busy as e:
            raise Unavailable(str(e))

    def groups(self, q: str | None = None, filters: dict | None = None) -> dict:
        """Группы по порядку со счётчиками встреч и неизвестные id из meta.json
        (meet.groups.summary). С запросом поиска `q` — счётчики среди
        найденных (поиск без фрагментов, общая с /search память запроса), с
        фильтром — среди подходящих под остальные его условия. `broken` —
        файл групп не прочитать (первая запись отложит его, `broken_copy` —
        уже отложенный), `newer` — файл от более новой версии (только чтение)."""
        from meet import groups, search

        root = self._root()
        flt = self._library_filter(None, filters).without("groups")
        keep = flt if flt.active else None
        scoped = bool(q) and search.searchable(q)
        if scoped:
            cards = search.search_library(root, q, limit=10**9, keep=keep, title_only=flt.title_only,
                                          count_only=True)
        else:
            cards = [c for c in search.cards(root) if keep is None or keep(c)]
        info = self._group_call(groups.state, root)
        extra = {k: info[k] for k in ("broken_copy",) if k in info}
        return {**groups.summary(info["items"], cards), "scope": "search" if scoped else "library",
                "broken": info["broken"], "newer": info["newer"], **extra}

    def create_group(self, body: dict | None) -> dict:
        """Новая группа {"name", "color"?}; {"id", "name", "color", "index",
        "created_at"?, …} — вернуть удалённую на место («Отменить»: группа из
        ответа DELETE целиком, с чужими полями) или назвать неизвестную.
        Отложен битый файл групп — `moved_broken` в ответе."""
        from meet import groups, search

        body = body if isinstance(body, dict) else {}
        root = self._root()
        taken = {card["group"] for card in search.cards(root) if card.get("group")}
        got = self._group_call(groups.create, root, body.get("name"), body.get("color"),
                               gid=body.get("id"), index=body.get("index"), taken=taken,
                               created_at=body.get("created_at"),
                               extra={k: v for k, v in body.items() if k != "index"})
        self._groups_changed("create", got["id"], got.get("moved_broken"))
        return got

    def patch_group(self, gid: str, body: dict | None) -> dict:
        from meet import groups

        body = body if isinstance(body, dict) else {}
        extra = {}
        if "kb_folder" in body:
            # Папка базы знаний группы (путь внутри базы; null или "" — убрать):
            # регистр — как на диске, если папка там есть.
            knowledge = settings.load().assistant.knowledge_dir
            extra = {"kb_folder": body.get("kb_folder"), "kb_root": knowledge}
        got = self._group_call(groups.update, self._root(), gid, name=body.get("name"),
                               color=body.get("color"), **extra)
        if "error" not in got:
            self._groups_changed("update", gid, got.get("moved_broken"))
        return got

    def delete_group(self, gid: str) -> dict:
        """Убрать группу из списка → {"group", "index"}; встречи остаются с её
        id в meta.json (неизвестная группа), «Отменить» — create_group с id и
        `created_at` удалённой."""
        from meet import groups

        got = self._group_call(groups.delete, self._root(), gid)
        if "error" not in got:
            self._groups_changed("delete", gid, got.get("moved_broken"))
        return got

    def order_groups(self, body: dict | None) -> dict:
        """Новый порядок {"ids"} → {"groups"}; тот же порядок — без записи и события."""
        from meet import groups

        ids = (body or {}).get("ids") if isinstance(body, dict) else None
        got = self._group_call(groups.reorder, self._root(), ids)
        if got["changed"]:
            self._groups_changed("order", None, got.get("moved_broken"))
        return {k: v for k, v in got.items() if k != "changed"}

    def group_members(self, gid: str, body: dict | None) -> dict:
        """{"add"?: [id записи], "remove"?: [...], "restore"?: true} → {"changed", "failed"}:
        `add` переносит встречу в группу (прежняя заменяется), `remove` убирает
        её, только если она в этой группе; `restore` — «Отменить» перенос:
        `add` и в неизвестную (но годную) группу, только meta.json. Одно
        событие на всё действие, и только если что-то поменялось."""
        from meet import groups

        body = body if isinstance(body, dict) else {}
        got = self._group_call(groups.members, self._root(), gid, add=body.get("add"),
                               remove=body.get("remove"), restore=body.get("restore") is True)
        if got.get("changed"):
            self._groups_changed("members", gid)
        return got

    def facets(self, q: str | None = None, filters: dict | None = None) -> dict:
        """Счётчики панели «Фильтры» при нынешнем запросе и фильтре: каждое
        измерение — среди встреч, прошедших все *прочие* условия (своё не
        учитывается: видно, что ещё можно выбрать), `total` — прошедших все.
        Один проход по текстам на все измерения (поиск без фрагментов, общая
        с /search память запроса); встречи, не прошедшие два условия и больше,
        не проверяются вовсе."""
        from meet import categories, groups, library_filter, search

        root = self._root()
        cfg = settings.load()
        flt = self._library_filter(None, filters)
        keep = library_filter.near(flt)
        scoped = bool(q) and search.searchable(q)
        if scoped:
            cards = search.search_library(root, q, limit=10**9, keep=keep, title_only=flt.title_only,
                                          count_only=True)
        else:
            cards = [c for c in search.cards(root) if keep is None or keep(c)]
        info = self._group_call(groups.state, root)
        return {**library_filter.facets(cards, flt, categories.ids(cfg), info["items"]),
                "scope": "search" if scoped else "library"}

    def participants(self, q: str | None = None, limit: int = 20) -> list[dict]:
        """Участники встреч для подсказок `участник:` (имена из расшифровок),
        владелец микрофона — последним (meet.library_filter.participants)."""
        from meet import library_filter, search, segvoices

        return library_filter.participants(search.cards(self._root()), q or "", limit,
                                           owners=segvoices.owners())

    def categories(self, q: str | None = None, filters: dict | None = None) -> dict:
        """Категории: нынешний список, стандартный («Сбросить к стандартным»)
        и сколько встреч в каждой (подтверждение удаления, счётчики фильтра в
        списке). С запросом поиска `q` — счётчики среди найденных; с фильтром
        (`filters`) — среди подходящих под остальные его условия."""
        from meet import categories

        cfg = settings.load()
        flt = self._library_filter(None, filters).without("categories")
        return {"categories": [c.to_raw() for c in cfg.categories],
                "defaults": [c.to_raw() for c in settings.default_categories()],
                **categories.counts(self._root(), cfg, q, keep=flt if flt.active else None,
                                    title_only=flt.title_only)}

    def _summary_title(self, folder: Path) -> None:
        """Итоги готовы: название из их первой строки (если его просили)."""
        found = library.read_meta(folder).get("summary_title")
        if isinstance(found, dict) and found.get("title"):
            self._retitle_ai(folder, found["title"], "итоги", origin=found.get("llm"))

    def _live_title(self, folder: Path) -> None:
        """Черновое название записи с ассистентом по теме его сводки: при
        остановке записи (тема на этот миг) и когда его хвост дописал последнюю
        сводку. Второй раз — только поверх своего же чернового: название от
        итогов, анализа или человека не трогаем (`titles.apply_live_draft`).
        Под замком: два вызова не перепишут свежую тему прочитанной раньше."""
        from meet import titles

        with self._live_title_lock:
            try:
                topic = titles.live_topic_title(folder)
            except Exception:
                topic = None
            if topic:
                self._retitle_ai(folder, topic, "тема живого режима",
                                 apply=titles.apply_live_draft)

    def _follow_title(self, folder: Path, old_title: str) -> None:
        """Папка встречи в базе знаний — под новое название (если она целиком
        наша и имя свободно), затем выгрузка заново: новое название в файлах."""
        from meet import kb_export

        try:
            cfg = settings.load()
            if not cfg.export.meetings_dir:
                return
            before = (library.read_meta(folder).get("kb_export") or {}).get("path")
            with self._kb_lock:
                moved = kb_export.follow_title(folder, cfg, old_title)
        except Exception as e:
            self.tray.log(f"папка встречи в базе знаний не переименована ({folder.name}): "
                          f"{type(e).__name__}: {e}")
            moved = None
        else:
            if moved is not None:
                self.tray.log(f"папка встречи в базе знаний переименована: {before} → {moved}")
                self._updated(folder)
        self._auto_kb_export(folder)

    def transcript(self, recording_id: str) -> dict:
        """Транскрипт для окна: без слов с таймкодами (их много, окну нужно
        только знать, можно ли резать реплику по слову — `has_words`)."""
        folder = self._folder(recording_id)
        data = _window_transcript(library.read_transcript_full(folder) if folder else None, folder)
        return data or {"error": "транскрипта нет"}

    def export(self, recording_id: str, fmt: str) -> dict:
        from meet import categories, export

        folder = self._folder(recording_id)
        # Как в редакторе: сырые SPEAKER_XX старых транскриптов — «Спикер N».
        data = library.with_display_names(library.read_transcript(folder)) if folder else None
        if data is None:
            return {"error": "транскрипта нет"}
        self._refuse_text_only(folder)
        title, date = library.title_and_date(folder, data)
        try:
            content = export.render({**data, "title": title}, fmt, date=date,
                                    chapters=export.chapters_of(folder, data),
                                    category=categories.display_name(folder, settings.load()))
        except ValueError as e:
            raise _bad_request(str(e))
        safe_name = export.safe_filename(title, recording_id)
        return {"filename": f"{safe_name}.{fmt}", "content": content}

    def _agent_extras(self, folder: Path) -> list[str]:
        """Файлы рядом с расшифровкой, которые стоит знать агенту: итоги и
        разметка встречи — если они есть."""
        from meet import assistant

        return [name for name in (assistant.SUMMARY_MD, AGENT_ANALYSIS_JSON)
                if (folder / name).is_file()]

    def agent_files(self, recording_id: str) -> dict:
        """Что получит агент, без записи файлов (строка «Контекст: …» во
        вкладке «Агент»). `live` — расшифровки ещё нет, агент получит ленту
        живого режима; пустой список — агенту пока нечего дать."""
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        if library.final_transcript(folder) is not None:
            out = {"files": [AGENT_TRANSCRIPT_MD, *self._agent_extras(folder)], "live": False}
        elif (folder / LIVE_TRANSCRIPT_MD).is_file():
            out = {"files": [AGENT_TRANSCRIPT_MD], "live": True}
        else:
            out = {"files": [], "live": False}
        if out["files"] and _has_chat(folder):
            out["files"].append(ASSISTANT_CHAT_MD)  # переписка с ассистентом (V4)
        # `sessions` — агенты, уже работавшие в папке (только если такие есть).
        sessions = _agent_sessions(library.read_meta(folder))
        if sessions:
            out["sessions"] = sessions
        return out

    def _mark_agent_session(self, folder: Path, body: dict) -> str | None:
        """Запомнить в meta.json, что в папке запускается агент
        `body.provider`: новый сеанс — с его id (`body.session`, если оболочка
        его задала), «Продолжить прошлую» (`body.resume`) — прежний id
        остаётся. → id сеанса, который продолжить (только при resume).
        Неизвестный провайдер или сбой записи — без метки, запуск не мешаем."""
        provider = body.get("provider")
        if provider not in AGENT_PROVIDERS:
            return None
        resume = body.get("resume") is True
        new_id = body.get("session")
        new_id = new_id if isinstance(new_id, str) and _SESSION_ID.fullmatch(new_id) else None
        found: list[str | None] = [None]

        def change(meta: dict) -> dict:
            marks = meta.get(AGENT_SESSIONS_META)
            marks = dict(marks) if isinstance(marks, dict) else {}
            sid = _agent_session_id(meta, provider) if resume else new_id
            found[0] = sid
            marks[provider] = {"at": time.time(), "id": sid}
            return {**meta, AGENT_SESSIONS_META: marks}

        try:
            library.update_meta(folder, change)
        except OSError as e:
            self.tray.log(f"метка сеанса агента не записана ({folder.name}): {e}")
            if resume:
                return _agent_session_id(library.read_meta(folder), provider)
        return found[0] if resume else None

    def agent_context(self, recording_id: str, body: dict | None = None) -> dict:
        """Файлы для вкладки «Агент» (Claude Code / Codex в папке встречи):
        `transcript.md` — расшифровка тем же Markdown, что «Экспорт» (имена
        спикеров, таймкоды), переписывается при каждом запуске агента
        атомарно; `summary.md` — итоги и `analysis.json` — разметка встречи,
        если они есть. Пока точной расшифровки нет (идёт запись с ассистентом
        или расшифровка), transcript.md — лента живого режима с пометкой
        «черновая». Папку оболочка проверяет сама: она должна лежать в папке
        записей. `body` — {provider, session?, resume?}: метка агента ложится в
        meta.json (`agent_sessions`, см. `agent_files`); при resume в ответе
        `session` — id прошлого сеанса, если он известен."""
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        # Текст до спикеров агенту не даём: как и без расшифровки — лента живого
        # режима, если она есть.
        text_only = self._text_only(folder)
        rendered = {"error": text_only} if text_only else self.export(recording_id, "md")
        live = "error" in rendered
        if live:
            try:
                feed = (folder / LIVE_TRANSCRIPT_MD).read_text(encoding="utf-8")
            except OSError:
                if text_only:
                    raise _conflict(text_only)
                return rendered
            content = AGENT_LIVE_HEADER + feed
        else:
            content = rendered["content"]
        import tempfile

        path = folder / AGENT_TRANSCRIPT_MD
        tmp = None
        try:
            # Временный файл — уникальный: два запуска агента подряд (двойной
            # щелчок, перезапуск) не пишут в один и тот же.
            fd, name = tempfile.mkstemp(prefix=f".{AGENT_TRANSCRIPT_MD}.", suffix=".tmp",
                                        dir=folder)
            tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except OSError as e:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            raise RuntimeError(f"не удалось подготовить расшифровку для агента: {e}") from e
        files = [AGENT_TRANSCRIPT_MD]
        if not live:
            files += self._agent_extras(folder)
        if self._write_chat_md(folder):
            files.append(ASSISTANT_CHAT_MD)
        out = {"folder": str(folder), "files": files}
        session = self._mark_agent_session(folder, body or {})
        if session:
            out["session"] = session
        return out

    def _write_chat_md(self, folder: Path) -> bool:
        """`assistant_chat.md` — переписка с ассистентом в читаемом виде для
        агента во вкладке «Агент» (§2.5). Только если чат есть (`has_chat`):
        у встреч до 0.3.6 файла не будет. Не записался — без него."""
        if not _has_chat(folder):
            return False
        from meet.assist import chatlog

        try:
            chatlog.ChatLog(folder, log=self.tray.log).write_md(
                title=f"Разговор с ассистентом — {library.read_meta(folder).get('title') or folder.name}")
        except OSError as e:
            self.tray.log(f"assistant_chat.md не записан ({folder.name}): {e}")
            return False
        return True

    def save_transcript(self, recording_id: str, data: dict) -> dict:
        """Сохранить правки редактора. Пишем как есть: редактор — владелец
        текста после расшифровки."""
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
            return {"error": "ожидается транскрипт с полем segments"}
        with self._speakers_lock:  # не посреди правки спикеров
            # Текст до спикеров заменит окончательная расшифровка: правка пропала бы.
            self._refuse_text_only(folder)
            library.write_transcript(folder, _without_marks(data))
        self._analysis_check(folder)
        return {"ok": True, "path": str(library.transcript_path(folder))}

    def name_speakers(self, recording_id: str, mapping: dict) -> dict:
        """Назвать спикеров и запомнить их голоса (прежний вход API, `{метка:
        имя}`). Тот же путь, что «Применить» в панели «Спикеры»: шаг истории
        встречи (его можно отменить), отказ, пока запись обрабатывается.

        Это и есть «обучение клона»: эмбеддинги из сайдкара расшифровки уходят в
        базу голосов, и на следующей встрече человек узнаётся сам. Порог матчинга
        строгий (0.75 с запасом 0.05) — лучше «Спикер 2», чем чужое имя."""
        from meet import speakers

        pairs = {str(k): str(v).strip() for k, v in (mapping or {}).items() if str(v).strip()}
        if not pairs:
            return {"error": "нечего сохранять"}
        result: dict = {}

        def change(folder: Path, voices: Path) -> dict:
            # Ключи — и «Спикер N», и сырые SPEAKER_XX старых транскриптов
            # (та же нумерация, что у сайдкара): переводим до нормализации.
            data = library.read_transcript(folder) or {}
            segments = data.get("segments") if isinstance(data.get("segments"), list) else []
            raw = library.display_names([s for s in segments if isinstance(s, dict)])
            ops = [{"type": "rename", "label": raw.get(label, label), "to": name}
                   for label, name in pairs.items()]
            got = speakers.apply(folder, ops, {op["label"]: True for op in ops}, voices)
            result.update(got)
            return got

        reply = self._speakers_change(recording_id, change)
        if "error" in reply:
            return reply
        step = result.get("step") or {}
        return {"ok": True, "renamed": result.get("changed", 0),
                "enrolled": sorted({e["person"] for e in step.get("enrolled") or []}),
                "voices_error": result.get("voices_error")}

    # --- панель «Спикеры»: правки одним шагом и их откат ---------------------

    def _speakers_view(self, folder: Path) -> dict:
        from meet import speakers

        cfg = settings.load()
        view = speakers.overview(folder, cfg.recording.voices, owner=cfg.recording.speaker_name)
        return {**view, "voice_threshold_default": cfg.asr.voice_threshold}

    def speakers(self, recording_id: str) -> dict:
        """Спикеры встречи для панели: доли, образцы фраз, подсказки из базы
        голосов, история правок."""
        from meet import search, speakers

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        self._refuse_text_only(folder)
        # Старые сырые SPEAKER_XX — один раз в «Спикер N» (не шаг истории),
        # пока над записью ничего не работает; иначе только показываем.
        with self._speakers_lock:
            if self._busy_reason(folder, model=False) is None:
                try:
                    if speakers.normalize(folder):
                        search.forget(folder)
                except OSError as e:
                    self.tray.log(f"метки спикеров не переписаны ({folder.name}): {e}")
            try:
                return self._speakers_view(folder)
            except speakers.SpeakerError as e:
                return {"error": str(e)}

    def _speakers_change(self, recording_id: str, change) -> dict:
        """Правка спикеров записи (`change(folder, voices)`), по одной за раз.
        Пока запись расшифровывают, объединяют или обрезают, — отказ: задача
        перепишет транскрипт, и правка (или её откат) потерялась бы."""
        from meet import search, speakers

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        # Проверка «занята ли запись» и сама правка — под одним замком: между
        # ними не вклинится ни другая правка, ни сохранение транскрипта.
        with self._speakers_lock:
            # Текст до спикеров: править нечего, а правку заменит окончательная.
            self._refuse_text_only(folder)
            reason = self._busy_reason(folder, model=False)
            if reason:
                raise _conflict(reason[:1].upper() + reason[1:])
            try:
                result = change(folder, self._voices())
            except (speakers.Stale, speakers.VoiceBaseError) as e:
                raise _conflict(str(e))
            except speakers.SpeakerError as e:
                raise _bad_request(str(e))
            except OSError as e:
                raise RuntimeError(f"не удалось сохранить: {e}") from e
        if result is not None:  # None — правка ничего не записала
            search.forget(folder)
            self._updated(folder)
            self._speakers_reexport(folder)
            self._analysis_check(folder)
        result = result or {}
        view = self._speakers_view(folder)
        return {**view, "voices_error": result.get("voices_error"),
                **({"step": result["step"]} if "step" in result else {})}

    def _speakers_reexport(self, folder: Path) -> None:
        """Имена — в выгрузке базы знаний: выгрузить заново, если выгрузка
        автоматическая или встречу уже выгружали (итоги не пересчитываются)."""
        from meet import kb_export

        try:
            cfg = settings.load().export
            wanted = bool(cfg.meetings_dir) and (
                cfg.auto_export or kb_export.previously_exported(folder))
        except Exception as e:
            self.tray.log(f"выгрузка в базу знаний не проверена ({folder.name}): {e}")
            return
        if wanted:
            self._background(lambda: self._auto_kb_export(folder), "meet-kb-export")

    def speakers_apply(self, recording_id: str, body: dict | None) -> dict:
        from meet import speakers

        body = body or {}
        # «Это я» + «Запомнить мой голос»: голос владельца встречи — образцом.
        owner = settings.load().recording.speaker_name if body.get("remember_owner") is True else None
        return self._speakers_change(recording_id, lambda folder, voices: speakers.apply(
            folder, body.get("ops") or [], body.get("remember"), voices,
            remember_owner=owner is not None, owner=owner,
            confirm_candidate=body.get("owner_candidate") is True))

    def speakers_relabel(self, recording_id: str, body: dict | None) -> dict:
        """Реплики (индексы сегментов) — другому спикеру: одна, серия подряд или
        выбранные в расшифровке. `count`/`labels` — как их видит окно."""
        from meet import speakers

        body = body or {}
        return self._speakers_change(recording_id, lambda folder, voices: speakers.relabel(
            folder, body.get("idx"), body.get("to"), voices,
            count=body.get("count"), labels=body.get("labels")))

    def speakers_split_turn(self, recording_id: str, body: dict | None) -> dict:
        """«Разделить реплику здесь»: {"turn": номера сегментов реплики, "at":
        сегмент, "char": место в его тексте, "to", "labels", "count"}."""
        from meet import speakers

        body = body or {}
        at, char = body.get("at"), body.get("char")
        return self._speakers_change(recording_id, lambda folder, voices: speakers.split_turn(
            folder, body.get("turn"), at if isinstance(at, int) else -1,
            char if isinstance(char, int) else 0, body.get("to"), voices,
            count=body.get("count"), labels=body.get("labels")))

    # --- «Исправить…»: распознанное слово во встрече и в терминах --------------

    def text_preview(self, recording_id: str, body: dict | None) -> dict:
        """Сколько раз слово или фраза встречается во встрече: {"find",
        "whole_word", "segment", "offset"} → {"count", "samples", "here"}."""
        from meet import textfix

        body = body or {}
        return self._speakers_read(recording_id, lambda folder: textfix.preview(
            folder, body.get("find"), whole_word=body.get("whole_word") is not False,
            segment=_int_or_none(body.get("segment")), offset=_int_or_none(body.get("offset"))))

    def text_apply(self, recording_id: str, body: dict | None) -> dict:
        """Исправить распознанное: {"find", "replace", "scope": "one" | "all",
        "segment", "offset", "count", "add_hotword", "add_rule"} — замена одним
        шагом истории встречи (её отменяют, как правки спикеров), исправление —
        в термины распознавания и в правила замены для будущих расшифровок.
        Текст уже такой, а термин или правило просили — только они."""
        from meet import textfix

        body = body or {}
        term = body.get("add_hotword") is True
        rule = body.get("add_rule") is True
        result: dict = {}

        def change(folder: Path, voices: Path) -> dict | None:
            try:
                got = textfix.apply(
                    folder, body.get("find"), body.get("replace"), str(body.get("scope") or ""), voices,
                    segment=_int_or_none(body.get("segment")), offset=_int_or_none(body.get("offset")),
                    whole_word=body.get("whole_word") is not False, count=_int_or_none(body.get("count")))
            except textfix.Unchanged:
                if not (term or rule):
                    raise
                return None
            result.update(got)
            return got

        reply = self._speakers_change(recording_id, change)
        if "error" in reply:
            return reply
        reply["changed"] = result.get("changed", 0)
        if term:
            from meet import replacements

            reply["hotword"] = hotwords.add_to_file(
                paths.hotwords_path(), replacements.hotword_for(body.get("find"), body.get("replace")))
        if rule:
            reply["rule"] = self._add_rule(body.get("find"), body.get("replace"))
        return reply

    def _add_rule(self, find, replace) -> dict | None:
        """Правило замены для будущих расшифровок (`asr.replacements`); то же
        «from» — заменяется (прежнее — в `replaced`). None — правило ничего бы
        не меняло."""
        from meet import replacements

        src, dst = replacements.clean_text(find), replacements.clean_text(replace)
        if not src or not dst or src == dst:
            return None
        try:
            current = settings.load().asr.replacements
            key = replacements.words_of(src)
            replaced = next((dict(r) for r in current if replacements.words_of(r["from"]) == key), None)
            settings.patch({"asr": {"replacements": replacements.with_rule(current, src, dst)}})
        except OSError as e:  # исправление уже применено — о правиле только сообщаем
            return {"from": src, "to": dst, "error": f"не удалось сохранить правило: {e}"}
        # `replaced` — правило с тем же «from», которое это вытеснило: отмена его вернёт.
        return {"from": src, "to": dst, "replaced": replaced}

    # --- «Разделить спикера» и порог узнавания ----------------------------------

    def _speakers_read(self, recording_id: str, read) -> dict:
        """Чтение для панели (предпросмотр): под замком правок, отказ — 400."""
        from meet import speakers

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        with self._speakers_lock:
            self._refuse_text_only(folder)
            try:
                return read(folder)
            except speakers.Stale as e:
                raise _conflict(str(e))
            except speakers.SpeakerError as e:
                raise _bad_request(str(e))

    def speakers_split_prepare(self, recording_id: str, body: dict | None) -> dict:
        """«Разделить спикера», шаг 1: голоса его реплик посчитаны? Нет —
        поставить задачу speaker_split (или вернуть уже идущую)."""
        from meet import speaker_split, speakers

        label = str((body or {}).get("label") or "")
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        with self._speakers_lock:
            self._refuse_text_only(folder)
            reason = self._busy_reason(folder, model=False)
            if reason:
                raise _conflict(reason[:1].upper() + reason[1:])
            try:
                if speakers.normalize(folder):
                    from meet import search

                    search.forget(folder)
                state = speaker_split.status(folder, label)
            except speakers.SpeakerError as e:
                raise _bad_request(str(e))
        if state["ready"]:
            return state
        with self._submit_lock:
            job = self.queue.active_for(str(folder), (jobs.SPEAKER_SPLIT,))
            if job is None or (job.options or {}).get("label") != label:
                job = self.queue.submit(jobs.SPEAKER_SPLIT, str(folder), {"label": label})
        return {**state, "job": job.to_raw()}

    def speakers_split_preview(self, recording_id: str, body: dict | None) -> dict:
        from meet import speaker_split, transcribe

        body = body or {}
        return self._speakers_read(recording_id, lambda folder: speaker_split.preview(
            folder, str(body.get("label") or ""), self._voices(),
            mode="people" if body.get("mode") == "people" else "auto",
            k=body.get("k") if isinstance(body.get("k"), int) else 2,
            people=body.get("people") if isinstance(body.get("people"), list) else None,
            threshold=transcribe.voice_threshold(folder)))

    def speakers_split_apply(self, recording_id: str, body: dict | None) -> dict:
        from meet import speaker_split

        body = body or {}
        reply = self._speakers_change(recording_id, lambda folder, voices: speaker_split.apply(
            folder, str(body.get("label") or ""), body.get("groups"), body.get("fingerprint"), voices,
            mode="people" if body.get("mode") == "people" else "auto"))
        self._reanalyze_if_stale(recording_id)
        return reply

    # --- «Переразделить на спикеров» -----------------------------------------------

    def speakers_rediarize(self, recording_id: str, body: dict | None) -> dict:
        """Поставить повторную диаризацию: {"num_speakers"} или {"min_speakers",
        "max_speakers"} и {"sensitivity": 0..1}. Уже идёт — вернуть её."""
        from meet import rediarize

        body = body or {}
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        options: dict = {}
        for name in ("num_speakers", "min_speakers", "max_speakers"):
            value = body.get(name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= rediarize.MAX_SPEAKERS:
                raise _bad_request(f"Число собеседников — от 1 до {rediarize.MAX_SPEAKERS}")
            options[name] = value
        if "num_speakers" in options and ("min_speakers" in options or "max_speakers" in options):
            raise _bad_request("Укажите либо точное число собеседников, либо диапазон")
        if options.get("min_speakers", 0) > options.get("max_speakers", rediarize.MAX_SPEAKERS):
            raise _bad_request("Наименьшее число собеседников больше наибольшего")
        sensitivity = body.get("sensitivity")
        if sensitivity is not None:
            if isinstance(sensitivity, bool) or not isinstance(sensitivity, (int, float))                     or not 0 <= sensitivity <= 1:
                raise _bad_request("Чувствительность — число от 0 до 1")
            options["sensitivity"] = float(sensitivity)
        with self._speakers_lock:
            self._refuse_text_only(folder)
            reason = self._busy_reason(folder, model=False)
            if reason:
                raise _conflict(reason[:1].upper() + reason[1:])
            if library.read_transcript(folder) is None:
                raise _bad_request("у записи нет расшифровки")
            from meet import speakers

            # Сырые SPEAKER_XX — в «Спикер N» до расчёта: иначе применение,
            # переписав их, сочло бы результат устаревшим.
            if speakers.normalize(folder):
                from meet import search

                search.forget(folder)
        with self._submit_lock:
            job = self.queue.active_for(str(folder), (jobs.REDIARIZE,))
            if job is None:
                rediarize.discard(folder)  # прежний результат уже не нужен
                job = self.queue.submit(jobs.REDIARIZE, str(folder), options)
        self._updated(folder)
        return {"job": job.to_raw()}

    def speakers_rediarized(self, recording_id: str) -> dict:
        """Предпросмотр посчитанного разделения (нет — 404)."""
        from meet import rediarize

        got = self._speakers_read(recording_id, rediarize.preview)
        return got if got is not None else {"error": "нового разделения нет"}

    def speakers_rediarize_apply(self, recording_id: str) -> dict:
        from meet import rediarize

        reply = self._speakers_change(recording_id, rediarize.apply)
        # Переразделение режет реплики по смене спикера: границы и номера
        # сегментов другие — анализ ставится заново (если включён); записи без
        # анализа — тоже: человек сейчас работает именно с ней.
        folder = self._folder(recording_id)
        if folder is not None:
            self._background(lambda: self._auto_analyze(folder), "meet-analysis")
        return reply

    def speakers_rediarize_discard(self, recording_id: str) -> dict:
        from meet import rediarize

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        with self._speakers_lock:
            rediarize.discard(folder)
        self._updated(folder)
        return {"ok": True}

    @staticmethod
    def _threshold_value(body: dict | None) -> float:
        value = (body or {}).get("value")
        low, high = settings.VOICE_THRESHOLD_RANGE
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise _bad_request(f"Порог — число от {low} до {high}")
        return round(float(value), 3)

    def speakers_threshold(self, recording_id: str, body: dict | None) -> dict:
        """Что сделает порог узнавания с именами спикеров встречи (без записи)."""
        from meet import speakers

        value = self._threshold_value(body)
        return self._speakers_read(recording_id, lambda folder: {
            "value": value, **speakers.threshold_plan(folder, value, self._voices())})

    def speakers_threshold_apply(self, recording_id: str, body: dict | None) -> dict:
        from meet import speakers

        value = self._threshold_value(body)
        return self._speakers_change(recording_id, lambda folder, voices: speakers.threshold_apply(
            folder, value, voices))

    def speakers_undo(self, recording_id: str, body: dict | None = None) -> dict:
        """Отменить последний шаг; {"expect_step": id} — только если последний
        именно он (итог правки в расшифровке), иначе 409."""
        from meet import speakers

        expect = (body or {}).get("expect_step")
        expect = expect if isinstance(expect, str) and expect else None
        return self._speakers_change(recording_id, lambda folder, voices: speakers.undo(
            folder, voices, expect_step=expect))

    def speakers_redo(self, recording_id: str) -> dict:
        from meet import speakers

        return self._speakers_change(recording_id, speakers.redo)

    def speakers_revert(self, recording_id: str, body: dict | None) -> dict:
        from meet import speakers

        to = (body or {}).get("to_step_id")
        return self._speakers_change(recording_id, lambda folder, voices: speakers.revert(
            folder, str(to) if to else None, voices))

    def engine(self) -> dict:
        """Что установлено для расшифровки и что для неё нужно."""
        from meet import engine as engine_module

        return engine_module.state()

    def install_engine(self, options: dict | None = None) -> dict:
        """Поставить движок задачей — той же очередью, что и расшифровку.

        Установка идёт минуты и гигабайты, поэтому не в потоке резидента: он
        должен оставаться отзывчивым и продолжать писать встречу, если она идёт.

        Пока качаются модели — отказ: их подпроцессы грузят пакеты движка
        (GigaAM — и torch), а pip переставлял бы их на ходу (на Windows —
        поверх загруженных DLL, с полуразобранным движком в итоге)."""
        with self._engine_lock:
            # Разбор образца голоса грузит torch движка — о нём и говорим, а не
            # о загрузках моделей (его задача живёт в том же слоте).
            if self.owner_takes.active():
                raise _conflict(OWNER_VOICE_BUSY)
            if self.downloads.any_active():
                raise _conflict(MODELS_DOWNLOADING)
            job = self.queue.submit(
                jobs.INSTALL_ENGINE, str(paths.data_dir()), options or {}
            )
        return job.to_raw()

    # --- где хранить движок и модели (переносит оболочка, storage.rs) --------

    def storage(self) -> dict:
        """Где лежат движок и модели, сколько весят модели Meet и можно ли
        переносить прямо сейчас (`busy` — причина «нельзя» или None)."""
        from meet import storage

        return {**storage.info(), "busy": self._storage_busy()}

    def _storage_busy(self) -> str | None:
        """Перенос перезапускает резидент и меняет место моделей: не во время
        записи, ассистента, загрузки моделей и фоновых задач (их подпроцессы —
        из старого движка)."""
        if self.tray.recording:
            return "идёт запись"
        if self.live.busy():
            return "работает ассистент"
        if self.owner_takes.active():
            return "записывается или разбирается образец голоса"
        if self.downloads.any_active():
            return "скачиваются модели"
        for item in self.queue.listing() + self.llm_queue.listing():
            if item.get("state") in (jobs.QUEUED, jobs.RUNNING):
                return "идёт расшифровка или другая фоновая задача"
        if self._processing_list():
            return "идёт обработка записи (обрезка, выгрузка)"
        return None

    def storage_hold(self, body: dict | None = None) -> dict:
        """Удержать резидент перед переключением папки: ничего не идёт —
        удержание на `storage.HOLD_S` (новые запись, ассистент, задачи,
        загрузки — отказ), иначе — чем занят. Проверка и удержание атомарны."""
        from meet import storage

        hold_id = (body or {}).get("id") if isinstance(body, dict) else None
        if hold_id is not None and not isinstance(hold_id, str):
            raise _bad_request("id удержания — строка")
        reason = storage.HOLD.take(self._storage_busy, hold_id or None)
        return {"held": reason is None, "busy": reason}

    def storage_release(self, hold_id: str | None = None) -> dict:
        """Снять удержание (переключение сорвалось до перезапуска); с id —
        только своё."""
        from meet import storage

        storage.HOLD.release(hold_id or None)
        return {"ok": True}

    def storage_leftovers(self, body: dict | None = None) -> dict:
        """Ответ на вопрос после переезда: удалить ли модели Meet из общего
        кэша Hugging Face (`{"delete": true}`) или оставить (`false`)."""
        from meet import storage

        delete = (body or {}).get("delete")
        if not isinstance(delete, bool):
            raise _bad_request("ответ — delete: true или false")
        return storage.answer_leftovers(delete)

    def models(self) -> dict:
        """Каталог моделей с отметками «скачано» и «выбрано»."""
        from meet import models as models_module

        asr = settings.load().asr
        return models_module.state(selected=asr.model, selected_gigaam=asr.gigaam_model)

    def remove_model(self, body: dict | None = None) -> dict:
        """Удалить скачанную модель GigaAM (быстро: удаление файлов). Пока она
        же качается — нельзя: загрузчик пишет в те же файлы."""
        from meet import models as models_module

        model_id = str((body or {}).get("id") or "").strip()
        if not model_id:
            return {"ok": False, "error": "не сказано, какую модель удалить"}
        if self.downloads.active_for(model_id, (jobs.DOWNLOAD_MODEL,)) is not None:
            return {"ok": False, "error": MODEL_DOWNLOADING}
        return models_module.remove(model_id)

    # --- токен Hugging Face ------------------------------------------------
    #
    # Проверка — сетевые запросы, всего до 12 с (models.HF_CHECK_TOTAL_S); идёт
    # прямо в потоке запроса: сервер многопоточный, остальные запросы не ждут.
    # Токен не уходит ни в ответ, ни в журнал.

    def hf_status(self) -> dict:
        from meet import credentials

        source = credentials.hf_token_source()
        return {"configured": source is not None, "source": source,
                "check": self._hf_check}

    def set_hf_token(self, body: dict | None) -> dict:
        """Проверить токен и сохранить его, только если доступ есть."""
        from meet import credentials, models as models_module

        token = (body or {}).get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise _bad_request("пустой токен")
        token = token.strip()
        result = models_module.check_hf_access(token)
        if result.get("ok"):
            where = credentials.set_hf_token(token)
            # Кэш — о сохранённом токене: меняется только вместе с ним.
            # Неудачная проверка нового токена старый не трогает.
            self._hf_check = result
            self.tray.log(f"токен HF сохранён ({where})")
        return result

    def check_hf_token(self, body: dict | None = None) -> dict:
        """Перепроверить сохранённый токен (условия могли принять с тех пор).
        Тело не нужно; принимается, чтобы прочитать его из keep-alive сокета."""
        from meet import credentials, models as models_module

        token = credentials.get_hf_token()
        if not token:
            raise _bad_request("токен Hugging Face не задан")
        self._hf_check = models_module.check_hf_access(token)
        return self._hf_check

    def clear_hf_token(self) -> dict:
        from meet import credentials

        credentials.clear_hf_token()
        self._hf_check = None
        self.tray.log("токен HF удалён")
        return self.hf_status()

    def download_model(self, body: dict | None = None) -> dict:
        """Скачать модель задачей: это гигабайты, и резидент должен оставаться
        отзывчивым. Разные модели качаются одновременно; та же, что уже
        качается, — та же задача (её ход и показывается).

        Пока очередь ставит движок (`POST /engine/install`, из командной
        строки), загрузка ждать за ним не будет — отказ: загрузчик приходит с
        движком, и подпроцесс загрузки поднялся бы из недоставленного пакета."""
        repo_id = str((body or {}).get("id") or "").strip()
        if not repo_id:
            return {"error": "не сказано, какую модель качать"}
        from meet import storage

        # Посреди переноса новая модель легла бы в старое место мимо копии.
        if storage.moving():
            raise _conflict(STORAGE_MOVING)
        with self._engine_lock:
            if self.queue.active_for(str(paths.data_dir()), (jobs.INSTALL_ENGINE,)) is not None:
                raise _conflict(ENGINE_INSTALLING)
            try:
                job, _ = self.downloads.submit_once(jobs.DOWNLOAD_MODEL, repo_id, {})
            except jobs.QueueStopped:
                raise _conflict(RESIDENT_STOPPING) from None
        return job.to_raw()

    def _submit_once(self, kind: str, folder: Path, options: dict | None = None):
        """Поставить задачу над записью, если над ней уже не ждёт и не идёт
        расшифровка или импорт. Возвращает (задача, поставлена ли новая).

        Проверка и постановка под одним локом: два почти одновременных запроса
        (двойной клик, автопостановка поверх ручной) не должны оба пройти."""
        with self._submit_lock:
            existing = self.queue.active_for(str(folder), jobs.FOLDER_KINDS)
            if existing is not None:
                return existing, False
            self._mark_pending(folder, True)
            # Посчитанное «Переразделить на спикеров» относится к прежней
            # расшифровке — после новой оно ни к чему.
            try:
                (folder / library.REDIARIZE_PREVIEW).unlink(missing_ok=True)
            except OSError:
                pass
            return self.queue.submit(kind, str(folder), options or {}), True

    def transcribe(self, recording_id: str, options: dict | None = None) -> dict:
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        if self._processing_now(folder):
            raise _conflict(PROCESSING)
        # Импорт, упавший до копии (исходник был недоступен), оставляет папку
        # без дорожки: расшифровывать нечего, повторяем импорт целиком.
        card = library.describe(folder)
        kind = jobs.TRANSCRIBE
        if card and not card.tracks and card.source == "import":
            kind = jobs.IMPORT
        elif card and card.source == "merge" and (
                not card.tracks or (card.merge or {}).get("state") == "pending"):
            # Звук не собран или сборка сорвана посередине (дорожки могут
            # лежать, но неполные) — повторить сборку, а не расшифровывать.
            kind = jobs.MERGE
        job, _ = self._submit_once(kind, folder, options)
        return job.to_raw()

    def submit_job(self, body: dict) -> dict:
        body = body or {}
        kind = str(body.get("kind") or jobs.TRANSCRIBE)
        if kind != jobs.TRANSCRIBE:
            return {"error": f"неизвестный вид задачи: {kind}"}
        recording_id = str(body.get("recording") or body.get("folder") or "")
        return self.transcribe(recording_id, body.get("options"))

    def jobs(self) -> dict:
        items = self.queue.listing() + self.llm_queue.listing() + self.downloads.listing()
        items.sort(key=lambda item: item.get("created_at") or 0.0)
        return {"items": items}

    def cancel_job(self, job_id: str) -> dict:
        get = getattr(self.queue, "get", None)
        job = get(job_id) if get else None
        if job is None:
            get = getattr(self.llm_queue, "get", None)
            job = get(job_id) if get else None
        ok = (self.queue.cancel(job_id) or self.llm_queue.cancel(job_id)
              or self.downloads.cancel(job_id))
        if ok and job is not None and job.kind in jobs.FOLDER_KINDS:
            # Отменённую человеком задачу после перезапуска не повторяем.
            self._mark_pending(Path(job.folder), False)
        if ok and job is not None and job.kind == jobs.IMPROVE:
            self._mark_improve(Path(job.folder), False)
        if ok and job is not None and job.kind == jobs.ANALYZE:
            # Ждущая задача снимается без события (_analysis_finished не придёт):
            # отметку снимаем здесь, иначе анализ вернётся при следующем запуске.
            folder = Path(job.folder)
            with self._analysis_lock:
                self._analysis_rerun.pop(self._key(folder), None)
            self._mark_analysis(folder, False)
        return {"ok": ok}

    # --- ассистент: итоги и вопросы ---------------------------------------

    def _transcribed(self, recording_id: str) -> Path | dict:
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        if library.read_transcript(folder) is None:
            return {"error": "транскрипта нет"}
        # Итоги, вопрос, анализ, название, «Улучшить» — по окончательной расшифровке.
        self._refuse_text_only(folder)
        return folder

    def _ready_for_model(self, folder: Path, provider: str | None = None) -> None:
        """409, если модели сейчас нечего дать: транскрипт вот-вот перепишет
        расшифровка (итоги по нему устарели бы сразу) или не подключён провайдер.
        `provider` — модель, выбранная человеком: нет её — 409 с причиной,
        модель по умолчанию вместо неё не берётся."""
        from meet import assistant, llm

        if self.queue.active_for(str(folder), (jobs.TRANSCRIBE, jobs.IMPORT)):
            raise _conflict("Дождитесь окончания расшифровки")
        cfg = settings.load()
        if provider is not None and not _provider_installed(cfg, provider):
            raise _conflict(f"{llm.LABELS[provider]} недоступна: {llm.not_found(provider, cfg)}")
        if provider is None and not _provider_installed(cfg):
            raise _conflict(assistant.NO_PROVIDER)

    @staticmethod
    def _chosen(body: dict | None) -> str | None:
        """Модель, выбранная человеком для одного действия (`provider` в теле
        запроса): только известная и включённая в настройках — иначе 400.
        Нет — None: модель по умолчанию."""
        from meet import llm

        value = (body or {}).get("provider")
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise _bad_request("модель — строка")
        if value not in llm.PROVIDERS:
            raise _bad_request(f"неизвестная модель: {value}")
        cfg = settings.load()
        if value not in cfg.llm.enabled:
            raise _bad_request(f"модель «{llm.LABELS[value]}» не включена в настройках")
        # Выбор закрепляется за задачей, даже если это сама модель по умолчанию:
        # сменят модель по умолчанию, пока задача ждёт, — встреча уйдёт всё равно
        # выбранной (а не новой по умолчанию).
        return value

    @staticmethod
    def _same_model(job, provider: str | None) -> bool:
        """Задача `job` — той же модели, что просьба `provider`: без выбора —
        модель по умолчанию (если она не «Авто»), так что задача по умолчанию и
        явный выбор той же модели — не «другая модель»."""
        default = settings.load().llm.provider
        norm = lambda p: p or (default if default != "auto" else None)  # noqa: E731
        return norm(_job_provider(job)) == norm(provider)

    def make_summary(self, recording_id: str, body: dict | None = None) -> dict:
        """Итоги задачей. Вторая просьба, пока первая ждёт или идёт, — та же задача;
        просьба другой модели в это время — 409 (одни итоги на запись)."""
        provider = self._chosen(body)
        folder = self._transcribed(recording_id)
        if isinstance(folder, dict):
            return folder
        self._ready_for_model(folder, provider)
        with self._submit_lock:
            job = self.llm_queue.active_for(str(folder), (jobs.SUMMARY,))
            if job is not None and _job_provider(job) != provider:
                # Ждущая без закреплённой модели — заменяется задачей с выбором человека.
                if job.state != jobs.RUNNING and self._cancel_queued(job.id):
                    job = None
                elif not self._same_model(job, provider):
                    raise _conflict("Итоги уже готовятся другой моделью — дождитесь окончания")
            if job is None:
                job = self.llm_queue.submit(jobs.SUMMARY, str(folder), _with_provider({}, provider))
        return job.to_raw()

    def summary(self, recording_id: str) -> dict:
        from meet import assistant

        folder = self._folder(recording_id)
        found = assistant.read_summary(folder) if folder else None
        return found or {"error": "итогов нет"}

    def live_draft(self, recording_id: str) -> dict:
        """Сводка живого режима записи (`live_state.json`) — черновик итогов,
        пока настоящих нет: {"summary", "hints", "markdown", "saved_at"}."""
        from meet.assist.live_state import load_saved

        folder = self._folder(recording_id)
        found = load_saved(folder) if folder else None
        return found or {"error": "черновика нет"}

    def ask(self, recording_id: str, body: dict | None) -> dict:
        provider = self._chosen(body)
        question = (body or {}).get("question")
        if not isinstance(question, str) or not question.strip():
            raise _bad_request("пустой вопрос")
        question = question.strip()
        if len(question) > QUESTION_MAX_CHARS:
            raise _bad_request(f"вопрос длиннее {QUESTION_MAX_CHARS} символов")
        folder = self._transcribed(recording_id)
        if isinstance(folder, dict):
            return folder
        self._ready_for_model(folder, provider)
        return self.llm_queue.submit(jobs.ASK, str(folder),
                                     _with_provider({"question": question}, provider)).to_raw()

    # --- чат ассистента после встречи («Продолжить разговор») -------------

    def recording_chat(self, recording_id: str) -> dict:
        """Чат записи: `{"messages", "seq", "legacy", "live", "job"}`.
        `messages` — лента (`visible_in_feed`); у встречи без чата (до
        0.3.6) `legacy` — прежние подсказки (`live_state.json`) и вопросы
        (`qa.jsonl`), иначе null. `live` — идёт живой режим этой записи (писать
        через /live/chat); `job` — задача ответа, которая ждёт или идёт."""
        from meet.assist import chatlog

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        out: dict = {"messages": [], "seq": 0, "legacy": None}
        if chatlog.has_chat(folder):
            snap = chatlog.ChatLog(folder, log=self.tray.log).snapshot(feed=True)
            if any(m.get("status") == chatlog.WRITING for m in snap["messages"]):
                # «Пишет…» без писателя — от задачи, убитой выходом резидента.
                self._close_stuck_chat(folder)
                snap = chatlog.ChatLog(folder, log=self.tray.log).snapshot(feed=True)
            out.update(snap)
        else:
            out["legacy"] = _legacy_assistant(folder)
        out["live"] = self._live_folder(folder)
        job = self.llm_queue.active_for(str(folder), (jobs.CHAT,))
        out["job"] = job.to_raw() if job is not None else None
        # Агент-участник включён в настройках: писать после встречи можно (иначе 409).
        out["enabled"] = bool(settings.load().assist.participant)
        # Профиль сессии этой встречи (0.3.7); до 0.3.7 или без ассистента — None.
        out["profile"] = chatlog.stored_profile(folder)
        return out

    def continue_chat(self, recording_id: str, body: dict | None) -> dict:
        """«Продолжить разговор» после встречи: сообщение — сразу в журнал
        записи (окно видит его), ответ агента — задачей `jobs.CHAT` в очереди
        модели (продолжение сеанса агента встречи, иначе затравка из журнала).
        `{"text", "attachments"?: ["a3"], "client_id"?, "provider"?}` →
        `{"message", "job", "duplicate"?}`. Идёт живой режим этой записи — 409
        (писать через /live/chat)."""
        from meet.assist import chatlog

        provider = self._chosen(body)
        payload = _chat_message(body)
        folder = self._after_meeting_folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        self._ready_for_model(folder, provider)
        log = chatlog.ChatLog(folder, log=self.tray.log)
        try:
            added = log.append("user", client_id=payload.get("client_id"),
                               text=payload["text"], attachments=payload.get("attachments", []),
                               after_meeting=True)
        except chatlog.FileLockTimeout:
            raise _unavailable("Журнал чата занят — повторите")
        return self._chat_reply_job(folder, log, added, provider)

    def recording_chat_click(self, recording_id: str, mid: str, body: dict | None) -> dict:
        """Кнопка сообщения агента после встречи: `{"label", "client_id"?,
        "provider"?}` → сообщение человека с надписью (`via: "button"`, `re`)
        и задача ответа — как «Продолжить разговор». Кнопки нет — 400."""
        from meet.assist import chatlog

        mid = _chat_mid(mid)
        body = body or {}
        label = body.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise _bad_request("label — надпись кнопки")
        cid = _chat_client_id(body)
        provider = self._chosen(body)
        folder = self._after_meeting_folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        self._ready_for_model(folder, provider)
        log = chatlog.ChatLog(folder, log=self.tray.log)
        try:
            added = log.click_button(mid, label, client_id=cid)
        except ValueError as e:
            raise _bad_request(str(e))
        except chatlog.FileLockTimeout:
            raise _unavailable("Журнал чата занят — повторите")
        return self._chat_reply_job(folder, log, added, provider)

    def _chat_reply_job(self, folder: Path, log, added, provider: str | None) -> dict:
        """Сообщение человека записано (`added`) — задача ответа агента.
        Повтор того же `client_id` — то же сообщение и идущая задача; ответа
        нет и задачи нет — спросить агента снова (ревью chat-api I1)."""
        message = added.message
        if not added.created:
            job = self.llm_queue.active_for(str(folder), (jobs.CHAT,))
            if job is None and message.get("kind") == "user" and not self._chat_answered(log, message["id"]):
                # Повтор после потерянной задачи (перезапуск резидента, отмена,
                # падение до ответа): ответа нет — спросить агента снова (ревью I1).
                job = self.llm_queue.submit(jobs.CHAT, str(folder),
                                            _with_provider({"message": message["id"]}, provider))
            return {"message": message, "job": job.to_raw() if job is not None else None,
                    "duplicate": True}
        self.bus.emit(CHAT_UPDATED, id=folder.name)
        job = self.llm_queue.submit(jobs.CHAT, str(folder),
                                    _with_provider({"message": message["id"]}, provider))
        return {"message": message, "job": job.to_raw()}

    def _after_meeting_folder(self, recording_id: str) -> Path | None:
        """Папка записи, в журнал чата которой резидент пишет после встречи.
        Нет записи — None. 409: агент-участник выключен в настройках, идёт
        живой режим этой записи (пишет ребёнок) или запись ещё пишется; 503 —
        не узнать, идёт ли живой режим."""
        folder = self._folder(recording_id)
        if folder is None:
            return None
        if not settings.load().assist.participant:
            # Ревью chat-api, M7: агент-участник выключен в настройках — и после встречи.
            raise _conflict("Чат с ассистентом выключен в настройках")
        if self._live_folder(folder, strict=True):
            raise _conflict("Идёт живой режим этой записи — пишите ассистенту в чат встречи")
        if self._key(folder) in self._busy_now():
            raise _conflict("Запись ещё идёт — продолжить разговор можно после её окончания")
        if self.queue.active_for(str(folder), CHAT_BUSY_KINDS):
            # Ревью after-chat, I3: пока встреча (пере)расшифровывается — не писать.
            raise _conflict(TRANSCRIBING)
        return folder

    # --- вложения и реакции после встречи ------------------------------------

    def recording_chat_attach(self, recording_id: str, body: dict | None) -> dict:
        """Файл или папка с диска к чату записи после встречи (`{"path",
        "provider"?}`) — те же проверки пути, что у `/live/chat/attach`
        (полный локальный путь, существует, не сетевой) и тот же разбор, пределы
        и квоты, что во время встречи (`participant.parse_attachment`). →
        `{"id", "status", "attachment", "error"?}`; сообщение потом ссылается
        на id."""
        path = _chat_path((body or {}).get("path"))
        return self._attach_after(recording_id, path, body)

    def recording_chat_paste(self, recording_id: str, data: bytes, content_type: str | None,
                             name: str | None = None) -> dict:
        """Вставленная картинка (сырое тело до 10 МБ) к чату записи после встречи."""
        kind = (content_type or "").split(";")[0].strip().lower()
        if kind not in CHAT_PASTE_TYPES:
            raise _bad_request("нужна картинка: PNG, JPEG, WEBP, GIF, BMP или TIFF")
        if name is not None:
            from urllib.parse import unquote

            name = os.path.basename(unquote(name).replace("\\", "/"))[:200] or None
        if not data:
            raise _bad_request("пустая картинка")
        return self._attach_after(recording_id, {"data": data, "name": name}, None)

    def _attach_after(self, recording_id: str, item, body: dict | None) -> dict:
        from meet.assist import chatlog, participant

        provider = self._chosen(body)
        folder = self._after_meeting_folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        # Один разбор за раз на резидент (ревью after-chat, I1): следующий — 503.
        if not _ATTACH_SLOT.acquire(blocking=False):
            raise _unavailable("Разбирается другой файл — повторите через минуту")
        try:
            fields, parsed = _parse_attachment_in_child(folder, item, self._chat_vision(provider), self.tray.log)
        finally:
            _ATTACH_SLOT.release()
        try:
            added = chatlog.ChatLog(folder, log=self.tray.log).append("attachment", **fields)
        except (chatlog.FileLockTimeout, OSError, RuntimeError) as e:
            if parsed:
                participant.drop_parsed(folder, fields, log=self.tray.log)
            if isinstance(e, chatlog.FileLockTimeout):
                raise _unavailable("Журнал чата занят — повторите")
            raise
        self.bus.emit(CHAT_UPDATED, id=folder.name)
        message = added.message
        out = {"id": message["id"], "status": message.get("status"), "attachment": message}
        if message.get("status") == "failed":
            out["error"] = message.get("error") or "не разобрано"
        return out

    @staticmethod
    def _chat_vision(provider: str | None) -> bool:
        """Видит ли картинки модель, которая ответит после встречи (выбранная
        или по умолчанию). «Авто» — не знаем, кого оно выберет (выбор не
        запускаем ради пометки): да, пометки нет; ответ агенту всё равно
        уходит с пометкой, если модель картинок не видит."""
        from meet import llm

        if provider is None:
            provider = settings.load().llm.provider
        return True if provider in (None, "auto") else llm.vision(provider)

    def recording_chat_remove(self, recording_id: str, aid: str) -> dict:
        """Вложение убрали из строки ввода до отправки (после встречи):
        `status: "removed"`, файлы — с диска. Уже отправленное — 400."""
        from meet.assist import chatlog, participant

        if not isinstance(aid, str) or not re.fullmatch(r"a\d{1,9}", aid):
            raise _bad_request("неизвестное вложение")
        folder = self._after_meeting_folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        try:
            got = participant.remove_attachment(chatlog.ChatLog(folder, log=self.tray.log), folder, aid,
                                                log=self.tray.log)
        except ValueError as e:
            raise _bad_request(str(e))
        except chatlog.FileLockTimeout:
            raise _unavailable("Журнал чата занят — повторите")
        if got is not None and got[0] is not None:
            self.bus.emit(CHAT_UPDATED, id=folder.name)
        return {"ok": True, "removed": got is not None}

    def recording_chat_confirm(self, recording_id: str, mid: str, body: dict | None) -> dict:
        """Карточка подтверждения Meet после встречи: `{"allow": bool}` →
        решение в журнал (агента в задаче «Продолжить разговор» ждёт его там).
        Решённая карточка — 400."""
        from meet.assist import chatlog

        mid = _chat_mid(mid)
        allow, meeting = (body or {}).get("allow"), (body or {}).get("meeting", False)
        if not isinstance(allow, bool) or not isinstance(meeting, bool):
            raise _bad_request("allow, meeting — true или false")
        folder = self._after_meeting_folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        log = chatlog.ChatLog(folder, log=self.tray.log)
        try:
            log.decide_card(mid, ("allow_meeting" if meeting else "allow") if allow else "deny")
        except ValueError as e:
            raise _bad_request(str(e))
        except chatlog.FileLockTimeout:
            raise _unavailable("Журнал чата занят — повторите")
        self.bus.emit(CHAT_UPDATED, id=folder.name)
        return {"ok": True}

    def recording_chat_react(self, recording_id: str, mid: str, body: dict | None) -> dict:
        """Реакция на сообщение агента после встречи: `{"emoji": "👍|👎|❓",
        "on"?: bool|null, "provider"?}` → `{"ok", "changed"}`. 👍 и 👎 —
        только в журнал (агент увидит их при следующем ответе); ❓ поставлен —
        ещё и просьба пояснить это сообщение: запись `via: "reaction"` и
        задача ответа (`message`, `job` в ответе; ревью after-chat, I2)."""
        from meet.assist import chatlog

        mid = _chat_mid(mid)
        body = body or {}
        emoji, on = body.get("emoji"), body.get("on")
        if emoji not in ("👍", "👎", "❓"):
            raise _bad_request("реакция — 👍, 👎 или ❓")
        if on is not None and not isinstance(on, bool):
            raise _bad_request("on — true, false или null")
        provider = self._chosen(body)
        folder = self._after_meeting_folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        explain = emoji == "❓" and on is not False
        if explain:
            self._ready_for_model(folder, provider)   # пояснить некому — реакции не будет
        log = chatlog.ChatLog(folder, log=self.tray.log)
        try:
            changed = log.react(mid, emoji, on)
        except ValueError as e:
            raise _bad_request(str(e))
        except chatlog.FileLockTimeout:
            raise _unavailable("Журнал чата занят — повторите")
        if not changed:
            return {"ok": True, "changed": False}
        switched_on = next((ev["message"].get("on") for ev in changed if ev.get("op") == "add"), False)
        if not (explain and switched_on):
            self.bus.emit(CHAT_UPDATED, id=folder.name)
            return {"ok": True, "changed": True}
        try:
            added = log.append("user", text=EXPLAIN_TEXT, via="reaction", re=mid, after_meeting=True)
        except chatlog.FileLockTimeout:
            raise _unavailable("Журнал чата занят — повторите")
        return {"ok": True, "changed": True, **self._chat_reply_job(folder, log, added, provider)}

    def kb_docs(self) -> dict:
        """Документы базы знаний (пути относительно неё, через «/»), без
        исключённых `assist.kb_exclude`, — окну, чтобы узнавать их в
        сообщениях агента (чипы-источники). `{"root", "docs", "more"}`;
        базы нет — root null."""
        from meet.assist import kb_prep

        cfg = settings.load()
        kb = kb_prep.for_settings(cfg, self._root())
        if not kb.configured:
            return {"root": None, "docs": [], "more": False}
        docs = kb.doc_list(KB_DOCS_MAX + 1)
        return {"root": str(kb.root), "docs": docs[:KB_DOCS_MAX], "more": len(docs) > KB_DOCS_MAX}

    @staticmethod
    def _chat_answered(log, mid: str) -> bool:
        from meet.assist.participant import answered

        return answered(log.messages(), mid)

    def _close_stuck_chat(self, folder: Path) -> None:
        """Задачу ответа убили (отмена, выход резидента) посреди хода: реплика
        осталась «пишет». Писателя сейчас нет — закрыть её (ревью M6)."""
        from meet.assist import chatlog

        if not chatlog.has_chat(folder) or self._live_folder(folder):
            return
        if self.llm_queue.active_for(str(folder), (jobs.CHAT,)) is not None:
            return
        try:
            events = chatlog.ChatLog(folder, log=self.tray.log).close_interrupted(
                "ответ прерван: задача остановлена")
        except OSError as e:
            self.tray.log(f"чат ассистента: недописанный ответ не закрыт ({folder.name}): {e}")
            return
        if events:
            self.bus.emit(CHAT_UPDATED, id=folder.name)

    def _live_folder(self, folder: Path, *, strict: bool = False) -> bool:
        """Идёт ли живой режим этой записи (тогда пишет ребёнок). Не удалось
        узнать: для чтения — «нет», для записи в журнал (`strict`) — 503, чтобы
        не писать в него вдвоём с ребёнком (ревью chat-api, M7)."""
        try:
            live_folder = self.live.status().get("folder")
        except Exception:
            if strict:
                raise _unavailable("Не удалось узнать, идёт ли живой режим — повторите")
            return False
        return bool(live_folder) and self._key(Path(live_folder)) == self._key(folder)

    def qa(self, recording_id: str) -> dict:
        from meet import assistant

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        return {"items": assistant.read_qa(folder)}

    # --- анализ встречи и название -----------------------------------------

    def analysis(self, recording_id: str) -> dict:
        """Состояние анализа для окна: {"state": none|queued|running|ready|
        stale|failed, "analysis"?, "error"?, "job"?}. queued/running — по
        очереди задач модели; остальное — по analysis.json и meta.json."""
        from meet import analysis

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        out = analysis.state(folder)
        job = self.llm_queue.active_for(str(folder), (jobs.ANALYZE,))
        if job is not None:
            out = {**{k: v for k, v in out.items() if k != "error"}, "state": job.state,
                   "job": job.to_raw()}
        return out

    def make_analysis(self, recording_id: str, body: dict | None = None) -> dict:
        """«Переанализировать» (и `meet analyze` через приложение): задача
        анализа в очередь модели. Уже ждёт — та же задача, но вперёд фоновых;
        уже идёт — та же, с повтором после неё. 409 — идёт расшифровка, не
        подключена модель или запись ещё пишется. `body.provider` — модель,
        выбранная человеком (см. `_queue_analysis`)."""
        provider = self._chosen(body)
        folder = self._transcribed(recording_id)
        if isinstance(folder, dict):
            return folder
        self._ready_for_model(folder, provider)
        if self._key(folder) in self._busy_now():
            raise _conflict("Запись ещё идёт — анализ будет доступен после её окончания")
        job, _created = self._queue_analysis(folder, low=False, manual=True, provider=provider)
        return job.to_raw()

    def analysis_consent(self, recording_id: str, body: dict) -> dict:
        """Ответ на разовое предложение включить автоматический анализ (тому, кто
        обновился с 0.2.x, см. settings.Analysis.consent): {"answer": "granted" |
        "declined"}. Ответ сохраняется в настройках, больше не спрашиваем.
        «Включить» из карточки ставит и её анализ — по тем же правилам, что
        автоматический (модель подключена, встреча не короче порога, анализ не
        свежий). → {"analysis": секция настроек}."""
        answer = (body or {}).get("answer")
        if answer not in (settings.CONSENT_GRANTED, settings.CONSENT_DECLINED):
            raise _bad_request("ответ — granted или declined")
        updated = settings.patch({"analysis": {"consent": answer}})
        self.tray.log("анализ встречи: автоматический " + (
            "включён" if answer == settings.CONSENT_GRANTED else "не включён") + " по ответу на предложение")
        folder = self._folder(recording_id)
        if answer == settings.CONSENT_GRANTED and folder is not None:
            self._background(lambda: self._auto_analyze(folder), "meet-analysis")
        return {"analysis": updated.analysis.to_raw()}

    def suggest_title(self, recording_id: str, body: dict | None = None) -> dict:
        """«Предложить название»: из свежего анализа сразу, иначе — коротким
        вызовом модели подпроцессом (до TITLE_TIMEOUT_S). Ничего не меняет:
        применяет окно (PATCH с title_source "ai" и title_llm). Повторный запрос
        по той же записи, пока первый считается, ждёт его ответа. `body.provider`
        — модель, выбранная человеком: зовём её, а не берём название из анализа.
        → {"title", "from", "llm"?}."""
        from meet import analysis

        provider = self._chosen(body)
        folder = self._transcribed(recording_id)
        if isinstance(folder, dict):
            return folder
        title = analysis.fresh_title(folder) if provider is None else None
        if title:
            doc = analysis.read(folder) or {}
            by = _origin(doc.get("llm"))
            return {"title": title, "from": "analysis", **({"llm": by} if by else {})}
        self._ready_for_model(folder, provider)
        key = (self._key(folder), provider)
        with self._suggest_lock:
            entry = self._suggesting.get(key)
            owner = entry is None
            if owner:
                entry = {"done": threading.Event(), "result": None}
                self._suggesting[key] = entry
        if owner:
            try:
                entry["result"] = _suggest_title(folder, provider) if provider else _suggest_title(folder)
            except Exception as e:
                entry["result"] = {"error": str(e) or type(e).__name__}
            finally:
                with self._suggest_lock:
                    self._suggesting.pop(key, None)
                entry["done"].set()
        else:
            entry["done"].wait(TITLE_TIMEOUT_S + 10)
        got = entry["result"] or {"error": "модель не ответила"}
        if got.get("error"):
            raise RuntimeError(f"название не предложено: {got['error']}")
        return got

    def _mark_analysis(self, folder: Path, on: bool, *, manual: bool = False,
                       provider: str | None = None) -> None:
        """`pending_analysis` в meta.json: анализ поставлен или отложен, но не
        закончился. Резидент, закрытый посреди него, при следующем запуске
        поставит его снова (см. recover, _resume_analysis) — как расшифровку.
        `provider` — модель, выбранная человеком: повтор пойдёт ею же."""
        try:
            if on:
                library.write_meta(folder, {"pending_analysis": {
                    "at": time.time(), "manual": manual, **({"provider": provider} if provider else {})}})
            elif "pending_analysis" in library.read_meta(folder):
                library.update_meta(folder, lambda meta: {
                    k: v for k, v in meta.items() if k != "pending_analysis"})
        except Exception as e:
            self.tray.log(f"отметка об анализе не записана ({Path(folder).name}): {e}")

    def _queue_analysis(self, folder: Path, *, low: bool, manual: bool = False,
                        provider: str | None = None):
        """Одна задача анализа на запись. Ждущая — та же (просьба человека
        поднимает фоновую вперёд); идущая — та же, с пометкой «повторить после»
        (расшифровку тем временем поменяли). → (задача, поставлена ли новая).

        `provider` — модель, выбранная человеком (U3). Ждущая задача другой
        модели заменяется его задачей; идущая другой моделью — 409: молча
        отдать встречу не той модели нельзя, и повтор после неё был бы не тем,
        о чём просили. Фоновая просьба довольствуется любой задачей."""
        from meet import storage

        with self._submit_lock:
            job = self.llm_queue.active_for(str(folder), (jobs.ANALYZE,))
            if job is not None and not low and _job_provider(job) != provider:
                # Ждущая снимается без события и заменяется задачей с выбором
                # человека; идущая — та же модель — та же задача, иначе 409 (не убиваем).
                if job.state != jobs.RUNNING and self._cancel_queued(job.id):
                    job = None
                elif not self._same_model(job, provider):
                    raise _conflict("Анализ уже идёт другой моделью — дождитесь окончания")
            if job is not None:
                if job.state == jobs.RUNNING:
                    with self._analysis_lock:
                        key = self._key(folder)
                        # Повтор — той же моделью, что идущая задача, если о нём просил человек.
                        was, was_by = self._analysis_rerun.get(key, (False, None))
                        again = was or manual
                        by = (provider if manual else was_by) or _job_provider(job)
                        self._analysis_rerun[key] = (again, by if again else None)
                elif not low and hasattr(self.llm_queue, "promote"):
                    self.llm_queue.promote(job.id)
                return job, False
            try:
                job = self.llm_queue.submit(jobs.ANALYZE, str(folder), _with_provider({}, provider), low=low)
            except storage.Held:
                # Перенос удерживает резидент: анализ не теряется — отметка
                # `pending_analysis`, новый резидент поставит его снова.
                self._mark_analysis(folder, True, manual=manual, provider=provider)
                self.tray.log(f"анализ встречи отложен до конца переноса: {folder.name}")
                raise
        self._mark_analysis(folder, True, manual=manual, provider=provider)
        return job, True

    def _defer_analysis(self, folder: Path, manual: bool, provider: str | None = None) -> None:
        with self._analysis_lock:
            key = self._key(folder)
            _, was_manual, was_provider = self._analysis_deferred.get(key, (folder, False, None))
            merged = (folder, was_manual or manual, provider if manual else was_provider)
            self._analysis_deferred[key] = merged
        # Отметка — с объединённым выбором: фоновая просьба не стирает модель человека.
        self._mark_analysis(folder, True, manual=merged[1], provider=merged[2])
        self.tray.log(f"анализ встречи отложен до конца записи: {folder.name}")

    def _auto_analyze(self, folder: Path, *, stale_only: bool = False) -> None:
        """Автоматический анализ (настройка `analysis.auto`) — после расшифровки,
        импорта, объединения, переразделения на спикеров; после правки
        спикеров — только если прежний анализ устарел (`stale_only`). Не
        ставится: запись короче `auto_record.min_call_seconds`, модель не
        подключена, анализ уже свежий. Идёт запись или живой режим —
        откладывается до их конца. Фоновый поток: любой сбой — в журнал."""
        from meet import analysis

        try:
            cfg = settings.load()
            if not cfg.analysis.auto or not analysis.effective_features(cfg):
                return
            data = library.final_transcript(folder)
            if data is None:
                return
            doc = analysis.read(folder)
            if stale_only and doc is None:
                return
            if doc is not None and analysis.is_fresh(folder, doc, data):
                if self.llm_queue.active_for(str(folder), (jobs.ANALYZE,)) is None:
                    self._mark_analysis(folder, False)  # например, отложенный — уже не нужен
                return
            spoken = max((float(s.get("end") or 0.0) for s in data.get("segments") or []
                          if isinstance(s, dict)), default=0.0)
            card = library.describe(folder)
            duration = max(spoken, float((card.duration_s if card else None) or 0.0))
            if duration < cfg.auto_record.min_call_seconds:
                self.tray.log(f"анализ встречи не ставлю — запись короче "
                              f"{cfg.auto_record.min_call_seconds:.0f} с: {folder.name}")
                return
            if self._busy_now():
                # Во время встречи модель не дёргаем: анализ — после неё.
                self._defer_analysis(folder, manual=False)
                return
            if not _provider_installed(cfg):
                return
            job, created = self._queue_analysis(folder, low=True)
            if created:
                self.tray.log(f"анализ встречи поставлен в очередь: {folder.name} ({job.id})")
        except Exception as e:
            self.tray.log(f"анализ встречи не поставлен ({Path(folder).name}): "
                          f"{type(e).__name__}: {e}")

    def _manual_again(self, folder: Path, provider: str | None = None) -> None:
        """Анализ, о котором просил человек (повтор после идущего, восстановление
        после перезапуска): без `analysis.auto` и порога длительности; модель —
        та, что он выбрал (`provider`), иначе по умолчанию."""
        try:
            if not _provider_installed(settings.load(), provider):
                return
            self._queue_analysis(folder, low=False, manual=True, provider=provider)
        except Exception as e:
            self.tray.log(f"анализ встречи не поставлен ({Path(folder).name}): "
                          f"{type(e).__name__}: {e}")

    def _flush_deferred_analysis(self) -> None:
        """Запись или живой режим кончились — отложенные анализы (и улучшения
        расшифровки) в очередь."""
        if self._busy_now():
            return
        with self._analysis_lock:
            improves = list(self._improve_deferred.values())
            self._improve_deferred.clear()
        for folder, provider in improves:
            if not folder.is_dir():
                continue
            if provider:
                self._improve_again(folder, provider)
            else:
                self._auto_improve(folder)
        with self._analysis_lock:
            waiting = list(self._analysis_deferred.values())
            self._analysis_deferred.clear()
        for folder, manual, provider in waiting:
            if not folder.is_dir():
                continue
            if manual:
                self._manual_again(folder, provider)
            else:
                self._auto_analyze(folder)

    def _resume_analysis(self, folder: Path, mark: dict, cutoff: float) -> bool:
        """Анализ, прерванный выходом резидента (`pending_analysis`), — снова в
        очередь, если он ещё нужен: модель подключена, запись не идёт (иначе —
        отложить), анализ не свежий. Автоматический — только при включённом
        `analysis.auto`. → поставлен ли (или отложен)."""
        from meet import analysis

        at = mark.get("at")
        manual = bool(mark.get("manual"))
        provider = _marked_provider(mark)
        if (not isinstance(at, (int, float)) or isinstance(at, bool) or at < cutoff
                or library.final_transcript(folder) is None or analysis.is_fresh(folder)):
            self._mark_analysis(folder, False)
            return False
        cfg = settings.load()
        if not manual and (not cfg.analysis.auto or not analysis.effective_features(cfg)):
            self._mark_analysis(folder, False)
            return False
        if provider is not None and provider not in cfg.llm.enabled:
            # Выбранную модель с тех пор выключили — другой эту встречу не отдаём.
            self._mark_analysis(folder, False)
            return False
        if self._busy_now():
            self._defer_analysis(folder, manual, provider)
            return True
        if not _provider_installed(cfg, provider):
            return False  # отметка остаётся: модель подключат — поставим при следующем запуске
        self._queue_analysis(folder, low=not manual, manual=manual, provider=provider)
        self.tray.log(f"анализ встречи восстановлен после перезапуска: {folder.name}")
        return True

    def _drop_analysis(self, folder: Path) -> None:
        """Снять анализ записи (удаление, объединение): задачу — из очереди или
        остановив, отложенный и повтор — забыть. Анализ — производное, его
        можно сделать заново; ждать его ради удаления незачем. Идущую задачу
        ждём недолго: её процесс работает в папке записи."""
        self._drop_improve(folder)
        key = self._key(folder)
        with self._analysis_lock:
            self._analysis_deferred.pop(key, None)
            self._analysis_rerun.pop(key, None)
        job = self.llm_queue.active_for(str(folder), (jobs.ANALYZE,))
        if job is None:
            self._mark_analysis(folder, False)  # был только отложен
            return
        self.llm_queue.cancel(job.id)
        # Ждущая снимается без события — _analysis_finished не придёт.
        self._mark_analysis(folder, False)
        deadline = time.monotonic() + DROP_ANALYSIS_WAIT_S
        while time.monotonic() < deadline:
            current = self.llm_queue.active()
            if current is None or current.id != job.id:
                break
            time.sleep(0.05)
        self.tray.log(f"анализ встречи снят: {Path(folder).name} ({job.id})")

    def _restore_analysis(self, folders) -> None:
        """Объединение не состоялось после того, как анализ частей сняли, —
        поставить его снова по обычным правилам (если включён)."""
        for folder in folders:
            self._background(lambda f=folder: self._auto_analyze(f), "meet-analysis")

    def _reanalyze_if_stale(self, recording_id: str) -> None:
        folder = self._folder(recording_id)
        if folder is not None:
            self._background(lambda: self._auto_analyze(folder, stale_only=True), "meet-analysis")

    def _analysis_check(self, folder: Path) -> None:
        """Текст или границы реплик поменялись: анализ, если он есть, устарел —
        окну событие (анализ сам не перезапускается: правок бывает много
        подряд; «Переанализировать» — в карточке)."""
        from meet import analysis

        try:
            doc = analysis.read(folder)
            if doc is not None and not analysis.is_fresh(folder, doc):
                self.bus.emit(ANALYSIS_UPDATED, id=Path(folder).name, state="stale")
        except Exception:
            pass  # событие — подсказка окну, не повод ронять правку

    def _analysis_finished(self, folder: Path, job_state, *, job: dict | None = None,
                           stopping: bool = False) -> None:
        """Задача анализа кончилась: снять отметку `pending_analysis`, записать
        ошибку упавшего процесса (если он не успел сам), название (если
        включено), событие окну, повтор, если расшифровку поменяли, пока она
        шла. Убитая остановкой резидента — отметку сохраняет: её поставит
        следующий запуск."""
        from meet import analysis

        if stopping:
            return
        with self._analysis_lock:
            rerun = self._analysis_rerun.pop(self._key(folder), None)
        if not folder.is_dir():
            return  # запись удалили — сообщать не о чем
        self._mark_analysis(folder, False)
        try:
            if job_state == jobs.FAILED:
                failure = library.read_meta(folder).get("analysis_error")
                at = failure.get("at") if isinstance(failure, dict) else None
                started = (job or {}).get("started_at") or 0.0
                if not isinstance(at, (int, float)) or at < started:
                    # Процесс умер до записи ошибки: окно всё равно покажет «Повторить».
                    analysis.mark_failed(folder, (job or {}).get("error") or "задача анализа прервалась",
                                         (job or {}).get("provider"))
            got = analysis.state(folder)
            if job_state == jobs.DONE and got.get("state") == "ready":
                doc = got.get("analysis") or {}
                if doc.get("title"):
                    self._retitle_ai(folder, doc["title"], "анализ встречи", origin=doc.get("llm"))
                self._recategorize_ai(folder, doc)
            self.bus.emit(ANALYSIS_UPDATED, id=folder.name, state=got.get("state"))
            self._updated(folder)
        except Exception as e:
            self.tray.log(f"анализ встречи не обработан ({folder.name}): {type(e).__name__}: {e}")
        if rerun is not None and job_state != jobs.CANCELLED:
            manual, provider = rerun
            if manual:
                self._manual_again(folder, provider)
            else:
                self._auto_analyze(folder)

    # --- «Улучшить расшифровку» ---------------------------------------------

    def improve(self, recording_id: str) -> dict:
        """Состояние для окна: {"state": none|queued|running|ready|failed,
        "proposal"?, "error"?, "job"?, "hint"}. Предложение по устаревшему
        тексту выбрасывается. `hint` — предложить улучшение после GigaAM
        (похоже, термины записаны кириллицей)."""
        from meet import improve

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        out = improve.state(folder)
        job = self.llm_queue.active_for(str(folder), (jobs.IMPROVE,))
        if job is not None:
            out = {"state": job.state, "job": job.to_raw()}
        hint = False
        if out["state"] == "none":
            try:
                hint = improve.hint_wanted(folder)
            except Exception:
                hint = False  # подсказка необязательна
        return {**out, "hint": hint}

    def make_improve(self, recording_id: str, body: dict | None = None) -> dict:
        """«Улучшить расшифровку»: задача в очередь модели. Уже ждёт или идёт —
        та же задача. 409 — идёт расшифровка, не подключена модель или запись
        ещё пишется. `body.provider` — модель, выбранная человеком."""
        from meet import improve

        provider = self._chosen(body)
        folder = self._transcribed(recording_id)
        if isinstance(folder, dict):
            return folder
        self._ready_for_model(folder, provider)
        if self._key(folder) in self._busy_now():
            raise _conflict("Запись ещё идёт — улучшение будет доступно после её окончания")
        improve.hint_done(folder)
        job, _created = self._queue_improve(folder, low=False, manual=True, provider=provider)
        return job.to_raw()

    def improve_dismiss(self, recording_id: str) -> dict:
        """Подсказку «Похоже, в тексте есть термины латиницей» больше не показывать."""
        from meet import improve

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        improve.hint_done(folder)
        return {"ok": True}

    def improve_apply(self, recording_id: str, body: dict | None) -> dict:
        """Применить выбранное: {"groups": [id…], "extra": {id: [номера мест]},
        "targets": {id: «как правильно»}, "created_at", "add_rules",
        "add_terms"} — одним шагом истории встречи (тот же путь и те же
        отказы, что у «Исправить…»); `targets` — вписанное человеком вместо
        предложенного ИИ (пустое — 400); `created_at` — какой список видел
        человек (его заменили новым — 409). По желанию — термины (не
        исправления обычных слов) правилами для будущих расшифровок и в
        термины распознавания: с вписанным человеком, а не с предложенным."""
        from meet import improve, replacements

        body = body or {}
        result: dict = {}

        def change(folder: Path, voices: Path) -> dict:
            created = body.get("created_at")
            got = improve.apply(folder, body.get("groups"), voices, extra=body.get("extra"),
                                targets=body.get("targets"),
                                created_at=created if isinstance(created, (int, float))
                                and not isinstance(created, bool) else None)
            result.update(got)
            return got

        reply = self._speakers_change(recording_id, change)
        if "error" in reply:
            return reply
        used = result.get("groups") or []
        reply["changed"] = result.get("changed", 0)
        reply["groups"] = used
        if body.get("add_rules") is True and used:
            reply["rules"] = self._add_rules(improve.rule_pairs(used))
        if body.get("add_terms") is True and used:
            added, errors = [], []
            for g in (g for g in used if g.get("kind") == "term"):
                got = hotwords.add_to_file(paths.hotwords_path(), replacements.hotword_for(g["from"], g["to"]))
                if got.get("error"):
                    errors.append(got["error"])
                elif got.get("added"):
                    added.append(got["term"])
            reply["terms"] = {"added": added, **({"error": errors[0]} if errors else {})}
        folder = self._folder(recording_id)
        if folder is not None:
            self.bus.emit(IMPROVE_UPDATED, id=folder.name, state="none")
        return reply

    def _add_rules(self, pairs: list[dict]) -> dict:
        """Правила замены для будущих расшифровок (`asr.replacements`) из
        выбранных групп: то же «from» — заменяется. → {"added": [...]}."""
        from meet import replacements

        try:
            rules = list(settings.load().asr.replacements)
            added = []
            for pair in pairs:
                src, dst = replacements.clean_text(pair["from"]), replacements.clean_text(pair["to"])
                if src and dst and src != dst:
                    rules = replacements.with_rule(rules, src, dst)
                    added.append({"from": src, "to": dst})
            if added:
                settings.patch({"asr": {"replacements": rules}})
        except OSError as e:  # замены уже применены — о правилах только сообщаем
            return {"added": [], "error": f"не удалось сохранить правила: {e}"}
        return {"added": added}

    def _mark_improve(self, folder: Path, on: bool, *, manual: bool = False,
                      provider: str | None = None) -> None:
        """`pending_improve` в meta.json: улучшение поставлено или отложено, но
        не закончилось — следующий запуск резидента поставит его снова (той же
        моделью, если её выбрал человек: `provider`)."""
        try:
            if on:
                library.write_meta(folder, {"pending_improve": {
                    "at": time.time(), "manual": manual, **({"provider": provider} if provider else {})}})
            elif "pending_improve" in library.read_meta(folder):
                library.update_meta(folder, lambda meta: {
                    k: v for k, v in meta.items() if k != "pending_improve"})
        except Exception as e:
            self.tray.log(f"отметка об улучшении не записана ({Path(folder).name}): {e}")

    def _queue_improve(self, folder: Path, *, low: bool, manual: bool = False,
                       provider: str | None = None):
        """Одна задача улучшения на запись: ждущая или идущая — та же (просьба
        человека поднимает фоновую вперёд). → (задача, поставлена ли новая).
        `provider` — модель, выбранная человеком: ждущая задача другой модели
        заменяется, идущая другой моделью — 409 (как у анализа)."""
        from meet import storage

        with self._submit_lock:
            job = self.llm_queue.active_for(str(folder), (jobs.IMPROVE,))
            if job is not None and not low and _job_provider(job) != provider:
                if job.state != jobs.RUNNING and self._cancel_queued(job.id):
                    job = None
                elif not self._same_model(job, provider):
                    raise _conflict("Улучшение уже идёт другой моделью — дождитесь окончания")
            if job is not None:
                if job.state != jobs.RUNNING and not low and hasattr(self.llm_queue, "promote"):
                    self.llm_queue.promote(job.id)
                return job, False
            try:
                job = self.llm_queue.submit(jobs.IMPROVE, str(folder), _with_provider({}, provider), low=low)
            except storage.Held:
                # Как с анализом: отметка остаётся, новый резидент продолжит.
                self._mark_improve(folder, True, manual=manual, provider=provider)
                self.tray.log(f"улучшение расшифровки отложено до конца переноса: {folder.name}")
                raise
        self._mark_improve(folder, True, manual=manual, provider=provider)
        return job, True

    def _cancel_queued(self, job_id: str) -> bool:
        """Снять ждущую задачу очереди модели (идущую — нет)."""
        cancel = getattr(self.llm_queue, "cancel_queued", None)
        return bool(cancel(job_id)) if cancel else False

    def _improve_again(self, folder: Path, provider: str) -> None:
        """Улучшение выбранной человеком моделью, отложенное до конца записи."""
        try:
            if _provider_installed(settings.load(), provider):
                self._queue_improve(folder, low=False, manual=True, provider=provider)
        except Exception as e:
            self.tray.log(f"улучшение расшифровки не поставлено ({Path(folder).name}): "
                          f"{type(e).__name__}: {e}")

    def _auto_improve(self, folder: Path, *, transcribed: bool = False) -> None:
        """После расшифровки (`transcribed`): прежнее предложение и прежняя
        ошибка — по старому тексту, их долой; если включено «Улучшать
        расшифровку автоматически после распознавания» (`analysis.improve_auto`),
        подготовить новое фоновой задачей — применяет его человек. Не ставится,
        как и анализ: запись короче `auto_record.min_call_seconds`, модель не
        подключена. Идёт запись или живой режим — после них."""
        from meet import improve

        try:
            improve.fresh(folder)  # устаревшее выбрасывается
            if transcribed and "improve_error" in library.read_meta(folder):
                library.update_meta(folder, lambda meta: {
                    k: v for k, v in meta.items() if k != "improve_error"})
            cfg = settings.load()
            data = library.final_transcript(folder)
            if not cfg.analysis.improve_auto or data is None:
                return
            if improve.read(folder) is not None:
                return  # свежее уже есть
            spoken = max((float(x.get("end") or 0.0) for x in data.get("segments") or []
                          if isinstance(x, dict)), default=0.0)
            card = library.describe(folder)
            if max(spoken, float((card.duration_s if card else None) or 0.0)) < cfg.auto_record.min_call_seconds:
                return
            if not _provider_installed(cfg):
                return
            if self._busy_now():
                with self._analysis_lock:
                    kept = self._improve_deferred.setdefault(self._key(folder), (folder, None))
                if kept[1] is None:  # отложенное моделью человека не трогаем
                    self._mark_improve(folder, True)
                return
            job, created = self._queue_improve(folder, low=True)
            if created:
                self.tray.log(f"улучшение расшифровки поставлено в очередь: {folder.name} ({job.id})")
        except Exception as e:
            self.tray.log(f"улучшение расшифровки не поставлено ({Path(folder).name}): "
                          f"{type(e).__name__}: {e}")

    def _resume_improve(self, folder: Path, mark: dict, cutoff: float) -> bool:
        """Улучшение, прерванное выходом резидента, — снова в очередь, если ещё
        нужно: расшифровка есть, свежего предложения нет, модель подключена;
        автоматическое — только при включённой настройке."""
        from meet import improve

        at = mark.get("at")
        manual = bool(mark.get("manual"))
        provider = _marked_provider(mark)
        if (not isinstance(at, (int, float)) or isinstance(at, bool) or at < cutoff
                or library.final_transcript(folder) is None or improve.fresh(folder) is not None
                or self.queue.active_for(str(folder), jobs.FOLDER_KINDS)):
            self._mark_improve(folder, False)
            return False
        cfg = settings.load()
        if not manual and not cfg.analysis.improve_auto:
            self._mark_improve(folder, False)
            return False
        if provider is not None and provider not in cfg.llm.enabled:
            self._mark_improve(folder, False)  # выбранную модель выключили — другой не отдаём
            return False
        if self._busy_now():
            with self._analysis_lock:
                self._improve_deferred[self._key(folder)] = (folder, provider)
            return True
        if not _provider_installed(cfg, provider):
            return False  # отметка остаётся: модель подключат — поставим при следующем запуске
        self._queue_improve(folder, low=not manual, manual=manual, provider=provider)
        self.tray.log(f"улучшение расшифровки восстановлено после перезапуска: {folder.name}")
        return True

    def _drop_improve(self, folder: Path) -> None:
        """Снять улучшение записи (удаление, объединение): задачу — из очереди
        или остановив; отложенное — забыть. Как анализ: это производное."""
        with self._analysis_lock:
            self._improve_deferred.pop(self._key(folder), None)
        job = self.llm_queue.active_for(str(folder), (jobs.IMPROVE,))
        self._mark_improve(folder, False)
        if job is None:
            return
        self.llm_queue.cancel(job.id)
        deadline = time.monotonic() + DROP_ANALYSIS_WAIT_S
        while time.monotonic() < deadline:
            current = self.llm_queue.active()
            if current is None or current.id != job.id:
                break
            time.sleep(0.05)
        self.tray.log(f"улучшение расшифровки снято: {Path(folder).name} ({job.id})")

    def _improve_finished(self, folder: Path, job_state, *, job: dict | None = None,
                          stopping: bool = False) -> None:
        """Задача улучшения кончилась: снять отметку, записать ошибку упавшего
        процесса (если он не успел сам), событие окну. Убитая остановкой
        резидента — отметку сохраняет."""
        from meet import improve

        if stopping or not folder.is_dir():
            return
        self._mark_improve(folder, False)
        try:
            if job_state == jobs.FAILED:
                failure = library.read_meta(folder).get("improve_error")
                at = failure.get("at") if isinstance(failure, dict) else None
                started = (job or {}).get("started_at") or 0.0
                if not isinstance(at, (int, float)) or at < started:
                    improve.mark_failed(folder, (job or {}).get("error") or "задача улучшения прервалась",
                                        (job or {}).get("provider"))
            self.bus.emit(IMPROVE_UPDATED, id=folder.name, state=improve.state(folder).get("state"))
        except Exception as e:
            self.tray.log(f"улучшение расшифровки не обработано ({folder.name}): {type(e).__name__}: {e}")

    # --- выгрузка в базу знаний --------------------------------------------

    def kb_export(self, recording_id: str) -> dict:
        """Кнопка «В базу знаний»: выгрузить сейчас, ответ — папка и файлы."""
        from meet import kb_export

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        cfg = settings.load()
        if not cfg.export.meetings_dir:
            raise _bad_request(kb_export.NOT_SET)
        self._refuse_text_only(folder)
        try:
            with self._kb_lock:
                return kb_export.export_recording(folder, cfg)
        except ValueError as e:
            raise _bad_request(str(e))
        except OSError as e:
            # Не OSError наружу: control API принял бы его за обрыв клиента.
            raise RuntimeError(f"не удалось выгрузить встречу: {e}") from e

    # «В заметки» прежнего окна — та же выгрузка.
    to_notes = kb_export

    def export_preview(self, params: dict | None = None) -> dict:
        """Пример папки и файлов для окна настроек (несохранённые значения —
        в `params`): на последней записи библиотеки, иначе на выдуманной."""
        from meet import kb_export

        return kb_export.preview(settings.load(), self._root(), dict(params or {}))

    def assistant(self, probe_local: bool = True) -> dict:
        """Кто ответит и что для этого есть. Не ждёт проверки входа в CLI:
        `checking` — ответ ещё считается в фоне (см. ProviderCache).
        `probe_local=False` — не проверять, отвечает ли локальная модель
        (запуск агента во вкладке «Агент»: ему нужны только пути к CLI)."""
        from meet import netproxy
        from meet.llm import detect

        from meet import llm

        cfg = settings.load()
        provider, checking = self._providers.get(cfg)
        knowledge = cfg.assistant.knowledge_dir
        found = detect.available(cfg.llm.base_url, probe_local=probe_local, via_proxy=llm_pkg_route(cfg))
        return {
            "provider": provider,
            "checking": checking,
            # Модель по умолчанию («Авто» или имя) и включённые модели — для
            # выбора модели у действий карточки (U3).
            "setting": cfg.llm.provider,
            "enabled": list(cfg.llm.enabled),
            "models": llm.models(cfg, found, auto_pick=provider),
            "available": found,
            "knowledge_dir": str(knowledge) if knowledge else None,
            # Какой прокси получат Claude Code/Codex (логин и пароль скрыты).
            "proxy": netproxy.describe(cfg),
            # Профиль сессии по умолчанию (`assist.profile`) — меню старта
            # помечает его и ставит первым (ревью M5).
            "profile": cfg.assist.profile,
        }

    def check_provider(self, body: dict | None) -> dict:
        """Кнопка «Проверить»: короткий вызов модели подпроцессом (до 90 с;
        HTTP-сервер многопоточный, остальные запросы не ждут)."""
        from meet import llm

        provider = str((body or {}).get("provider") or "auto")
        if provider != "auto" and provider not in llm.PROVIDERS:
            raise _bad_request(f"неизвестный провайдер: {provider}")
        result = _check_provider(provider)
        # Человек мог только что войти в CLI — выбор провайдера пересчитаем.
        self._providers.invalidate()
        return result

    def local_models(self, body: dict | None) -> dict:
        """«Найти модели»: список моделей локального сервера по адресу из окна
        (может быть ещё не сохранён; нет — из настроек; `via_proxy` — так же).
        Короткий GET без
        вызова модели — прямо в резиденте (см. meet.llm.local_models)."""
        from meet.llm import local_models

        body = body or {}
        base_url, model = body.get("base_url"), body.get("model")
        if not all(v is None or isinstance(v, str) for v in (base_url, model)):
            raise _bad_request("адрес и имя модели — строки")
        cfg = settings.load()
        if not (base_url or "").strip():
            base_url = cfg.llm.base_url
            if "model" not in body:
                model = cfg.llm.local_model
        # «Через прокси» — из черновика окна, если он прислан, иначе сохранённое.
        drafted = body.get("via_proxy")
        return local_models.list_models(base_url, model, via_proxy=llm_pkg_route(
            cfg, drafted if isinstance(drafted, bool) else None))

    def import_file(self, body: dict) -> dict:
        """Импорт чужой записи: папка + задача (копия и расшифровка)."""
        raw = str((body or {}).get("path") or "").strip()
        src = Path(raw) if raw else None
        if src is None or not src.is_file():
            return {"error": "файла нет"}
        try:
            folder = library.create_import(self._root(), src)
        except ValueError as e:
            return {"error": str(e)}
        self._mark_pending(folder, True)
        job = self.queue.submit(jobs.IMPORT, str(folder), {})
        return {"recording": folder.name, "job": job.to_raw()}

    def track_path(self, recording_id: str, track: str) -> Path | None:
        """Файл для плеера. `playback` — то, что играет карточка: обе стороны
        звонка, сведённые в одну дорожку (или импортированный файл как есть);
        `sys`/`mic`/`source` — отдельные дорожки, для диагностики."""
        folder = self._folder(recording_id)
        if folder is None:
            return None
        if self._processing_now(folder):
            from meet.control import Unavailable

            raise Unavailable(PROCESSING)
        if track == "playback":
            from meet import playback

            try:
                return playback.playback_path(folder)
            except RuntimeError as e:
                from meet.control import Unavailable

                raise Unavailable(str(e)) from e
        if track not in library.TRACK_STEMS:
            return None
        return library.find_track(folder, track)

    def diagnostics(self, lines: int = TAIL_DEFAULT) -> dict:
        """Хвосты журналов для экрана диагностики.

        Это ровно те два файла, по которым сейчас разбирают вручную «почему не
        записалось / записалось лишнее»: журнал решений дежурного и журнал
        записи текущей (или последней) папки."""
        folder = self.tray._current_folder() if self.tray.recording else None
        record_log: list[str] = []
        if folder and folder != "папка ещё не создана":
            record_log = _tail(Path(folder) / "record.log", lines)
        return {
            "watch_log": _tail(watch.default_log_path(), lines),
            "record_log": record_log,
            "folder": folder,
            "paths": {
                "data_dir": str(paths.data_dir()),
                "recordings": str(settings.load().recording.recordings),
                "config": str(paths.config_path()),
                "watch_log": str(watch.default_log_path()),
            },
            "dev_mode": paths.is_dev(),
        }

    # --- образец голоса владельца (meet.owner_voice_control) ----------------

    def owner_voice(self) -> dict:
        return self.owner_takes.status()

    def owner_voice_record(self, body: dict | None) -> dict:
        """Запись образца голоса — под воротами переноса: проверка удержания и
        начало записи атомарны (её ~25 с микрофона перезапуск оборвал бы)."""
        from meet import storage

        try:
            with storage.HOLD.gate():
                return self.owner_takes.record(body)
        except storage.Held:
            raise _conflict("идёт перенос движка и моделей — образец голоса запишите "
                            "через минуту") from None

    def owner_voice_delete(self, sample_id: str) -> dict:
        return self.owner_takes.delete(sample_id)

    def owner_voice_derive(self) -> dict:
        return self.owner_takes.derive()

    def owner_voice_suggestion(self, body: dict | None) -> dict:
        return self.owner_takes.answer(body)

    # --- люди -----------------------------------------------------------

    def _voices(self) -> Path:
        return settings.load().recording.voices

    def people(self) -> dict:
        from meet import people

        return {"items": people.listing(self._voices(), self._root())}

    def person(self, name: str) -> dict:
        from meet import people

        try:
            return people.person(name, self._voices(), self._root())
        except KeyError:
            return {"error": "человека нет"}
        except ValueError as e:
            raise _bad_request(str(e))

    def person_sample(self, name: str) -> dict:
        from meet import people

        try:
            found = people.sample(name, self._voices(), self._root())
        except ValueError as e:
            return {"error": str(e)}
        return found or {"error": "образца нет"}

    def avatar_path(self, name: str) -> Path | None:
        from meet import people

        try:
            path = people.avatar_path(name, self._voices())
        except ValueError:
            return None
        return path if path.exists() else None

    def set_avatar(self, name: str, data: bytes) -> dict:
        from meet import people

        try:
            people.set_avatar(name, data, self._voices())
        except KeyError:
            return {"error": "человека нет"}
        except ValueError as e:
            raise _bad_request(str(e))
        except OSError as e:
            # Не OSError наружу: control API принимает его за «клиент ушёл» и
            # молча не отвечает. RuntimeError станет честным 500 с текстом.
            raise RuntimeError(f"не удалось сохранить аватар: {e}") from e
        return {"ok": True}

    def person_action(self, name: str, action: str, body: dict | None = None) -> dict:
        """rename / merge / delete / clear-avatar — одним входом, ошибки текстом."""
        from meet import people

        voices = self._voices()
        try:
            # rename/merge переписывают имя и в транскриптах библиотеки: по ним
            # считается статистика и берётся образец. Под замком правок
            # спикеров: не посреди «Применить» или отмены в панели.
            if action == "rename":
                target = people.valid_name(str((body or {}).get("to") or ""))
                with self._speakers_lock:
                    people.rename(name, target, voices, self._root())
            elif action == "merge":
                target = people.valid_name(str((body or {}).get("into") or ""))
                with self._speakers_lock:
                    people.merge(name, target, voices, self._root())
            elif action == "delete":
                people.delete(name, voices)
            elif action == "clear-avatar":
                people.clear_avatar(name, voices)
        except KeyError:
            return {"error": "человека нет"}
        except FileExistsError:
            raise _bad_request("человек с таким именем уже есть")
        except ValueError as e:
            raise _bad_request(str(e))
        return {"ok": True}
