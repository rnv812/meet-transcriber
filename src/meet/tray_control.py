"""Адаптер: резидентный трей как состояние control API.

Своей логики записи здесь нет — только перевод команд панели в методы трея и
сборка снимка состояния. Владелец записи остаётся один, и это трей: панель
ничего не пишет сама, иначе появился бы второй претендент на устройство и на
`.recording.lock`.

Снимок сознательно плоский и самодостаточный: панель должна уметь нарисоваться
по одному ответу, без дополнительных запросов.
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from meet import (engine, events, gpu_lock, hotwords, jobs, library, live_control, paths,
                  settings, watch)

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

# Список устройств меняется редко, а каждый запрос — подпроцесс: кэшируем,
# чтобы открытая страница настроек не плодила их пачками.
DEVICES_TTL_S = 15.0
DEVICE_PROBE_TIMEOUT_S = 20.0


def _probe_devices() -> dict:
    """Спросить устройства у подпроцесса (см. meet.devices_probe)."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "meet.devices_probe"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=DEVICE_PROBE_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    line = (out.stdout or "").strip().splitlines()
    try:
        return json.loads(line[-1]) if line else {
            "available": False, "error": (out.stderr or "нет ответа")[:300]}
    except ValueError:
        return {"available": False, "error": (out.stdout or "")[:300]}


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
        return (cfg.llm.provider, cfg.llm.base_url)

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


def _provider_installed(cfg) -> bool:
    """Дешёвая проверка «есть кому ответить» (без проверки входа и без SDK).

    Задача всё равно спросит `llm.resolve` и, если вход не выполнен, упадёт с
    тем же текстом — это осознанно: ждать проверки входа в HTTP нельзя."""
    from meet.llm import detect

    found = detect.available(cfg.llm.base_url)
    choice = cfg.llm.provider
    if choice == "auto":
        return any(item.get("found") for item in found.values())
    return bool(found.get(choice, {}).get("found"))


def _check_provider(provider: str) -> dict:
    """`python -m meet.llm.check <provider>` подпроцессом: вызов модели не
    должен жить в резиденте, а SDK провайдера — грузиться в него."""
    try:
        out = subprocess.run(
            [sys.executable, "-m", "meet.llm.check", provider],
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


def _bad_request(text: str):
    """400 для API: ошибка ввода (имя, картинка), а не «не найдено»."""
    from meet.control import BadRequest

    return BadRequest(text)


def _conflict(text: str):
    """409 для API: действие сейчас невозможно (не подключена модель)."""
    from meet.control import Conflict

    return Conflict(text)


class TrayControl:
    """Состояние для `meet.control.ControlServer` поверх объекта трея."""

    def __init__(self, tray, queue=None, llm_queue=None, live=None) -> None:
        self.tray = tray
        self.bus = tray.bus
        # Очередь задач живёт рядом с записью, в том же резиденте: расшифровка
        # идёт подпроцессом и не мешает ни записи, ни панели.
        self.queue = queue if queue is not None else jobs.JobQueue(self.bus)
        # Итоги и вопросы — своя очередь: GPU им не нужен, и ждать за часовой
        # расшифровкой ответ на вопрос было бы странно.
        self.llm_queue = llm_queue if llm_queue is not None else jobs.JobQueue(self.bus)
        self._providers = ProviderCache()
        self._submit_lock = threading.Lock()
        self._levels: dict = {}
        self._devices_cache: dict | None = None
        self._devices_at = 0.0
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
        self.bus.subscribe(self._on_live_event)
        # Выгрузка в базу знаний: по одной за раз (кнопка поверх автоматики не
        # должна писать в ту же папку одновременно). Последний сбой автоматики —
        # в снимке, оболочка показывает по нему уведомление; о каждой встрече —
        # не больше одного за жизнь резидента.
        self._kb_lock = threading.Lock()
        self._kb_failed: dict | None = None
        self._kb_reported: set[str] = set()
        self.bus.subscribe(self._on_job_event)

    @staticmethod
    def _background(fn) -> None:
        """Фоновая работа вне потока очереди задач (тесты подменяют на вызов)."""
        threading.Thread(target=fn, name="meet-kb-export", daemon=True).start()

    def _on_job_event(self, event) -> None:
        """Расшифровка или импорт готовы — выгрузить встречу в базу знаний, если
        включена автоматика; итоги готовы — выгрузить заново (итоги ложатся
        рядом), если включена автоматика или встречу уже выгружали вручную."""
        if event.kind != jobs.JOB_DONE:
            return
        job = event.data.get("job") or {}
        kind, folder = job.get("kind"), job.get("folder")
        if kind not in (jobs.TRANSCRIBE, jobs.IMPORT, jobs.SUMMARY) or not folder:
            return
        cfg = settings.load().export
        if not cfg.meetings_dir:
            return
        path = Path(folder)
        if kind == jobs.SUMMARY:
            from meet import kb_export

            wanted = cfg.auto_export or kb_export.previously_exported(path)
        else:
            wanted = cfg.auto_export
        if wanted and library.read_transcript(path) is not None:
            self._background(lambda: self._auto_kb_export(path))

    def _auto_kb_export(self, folder: Path) -> None:
        from meet import kb_export

        try:
            with self._kb_lock:
                result = kb_export.export_recording(folder, settings.load())
        except Exception as e:
            error = str(e) or type(e).__name__
            self.tray.log(f"не удалось выгрузить встречу в базу знаний ({folder.name}): {error}")
            if folder.name not in self._kb_reported:
                self._kb_reported.add(folder.name)
                self._kb_failed = {"folder": folder.name, "error": error, "at": time.time()}
            return
        self.tray.log(f"встреча выгружена в базу знаний: {result['path']}")

    def _on_live_event(self, event) -> None:
        """Ассистент остановлен — та же автоматическая расшифровка, что после
        обычной записи, если запись дописана (`complete`) и дорожки на месте.
        Убитый до финализации только помечается (`source: live`), без
        расшифровки: она шла бы по неполным дорожкам. Упавший (`live.failed`)
        сюда не попадает: его папка в библиотеке, расшифровать можно вручную."""
        if event.kind != live_control.LIVE_STOPPED or not event.data.get("folder"):
            return
        folder = Path(event.data["folder"])
        if not folder.is_dir():
            return
        card = library.describe(folder)
        full = bool(event.data.get("complete")) and bool(card and card.tracks)
        self._on_saved(str(folder), LIVE, full)

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
        if not full or not settings.load().recording.auto_transcribe:
            return
        job, created = self._submit_once(jobs.TRANSCRIBE, path)
        if created:
            self.tray.log(f"расшифровка поставлена в очередь: {path.name} ({job.id})")
        else:
            self.tray.log(f"расшифровка уже в очереди: {path.name} ({job.id})")

    def _remember_levels(self, event) -> None:
        """Последние уровни дорожек — чтобы снимок состояния не ждал события."""
        if event.kind == events.RECORD_LEVEL:
            self._levels = event.data.get("levels") or {}

    # --- что показывать -------------------------------------------------

    def snapshot(self) -> dict:
        tray = self.tray
        cfg = settings.load()
        meetings = cfg.export.meetings_dir
        recording = bool(tray.recording)
        elapsed = time.monotonic() - tray.started if recording and tray.started else 0.0
        return {
            "status": "recording" if recording else "idle",
            "source": tray.source,
            "folder": tray._current_folder() if recording else None,
            "elapsed_s": round(elapsed, 1),
            "levels": dict(self._levels) if recording else {},
            # Свободное место под записи: UI предупреждает при < 5 ГБ до старта
            # записи, а не когда ffmpeg упрётся в полный диск посреди встречи.
            # None — диск недоступен (отключён, шара не отвечает).
            "disk_free_gb": engine._free_gb(cfg.recording.recordings),
            "auto_record": {
                "enabled": bool(tray.cfg["enabled"]),
                "processes": list(tray.cfg["processes"]),
                "grace_seconds": tray.cfg["grace_seconds"],
                "state": getattr(tray.watcher, "state", None),
                # Сигналы детектора: None означает «ответить нечем» (ключа в
                # реестре нет, pycaw не встал) — это не то же самое, что «нет».
                "mic": tray._last_signals[0] if tray._last_signals else None,
                "render": tray._last_signals[1] if tray._last_signals else None,
            },
            "recordings_dir": str(cfg.recording.recordings),
            # Папка для встреч в базе знаний: оболочка открывает выгруженные
            # папки только внутри неё (и папки записей).
            "meetings_dir": str(meetings) if meetings else None,
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
        }

    # --- команды панели -------------------------------------------------

    def start_recording(self) -> dict:
        """Начать вручную. Если запись уже идёт автоматически — это то же
        нажатие «Начать запись» поверх автозаписи, что и в меню трея: человек
        берёт её под свою руку, автостоп отключается."""
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

    def shutdown(self) -> dict:
        """Выход по просьбе оболочки: идущая запись сохраняется штатно.

        Ассистента ждём здесь же, как и обычную запись (до 60 с, дальше —
        убийство дерева): после ответа оболочка даёт резиденту всего 10 с на
        выход, а потом гасит его вместе с ребёнком — финальный проход и
        закрытие дорожек потерялись бы."""
        if self.tray.recording:
            self.tray.stop_recording()
        self.live.stop(wait=True, timeout=live_control.SHUTDOWN_WAIT_S)
        self.tray.request_exit()
        return {"ok": True}

    # --- живой режим (запись с ассистентом) -----------------------------

    def live_start(self) -> dict:
        """Запись с ассистентом. Ответ сразу (`starting`): модель грузится до
        минуты, дальше — события `live.started` / `live.failed`."""
        from meet import assistant

        if self.tray.recording:
            raise _bad_request("Идёт обычная запись — сначала остановите её")
        if not _provider_installed(settings.load()):
            raise _conflict(assistant.NO_PROVIDER)
        try:
            return self.live.start(self._root())
        except live_control.LiveBusy as e:
            raise _bad_request(str(e))

    def live_stop(self) -> dict:
        """Остановить ассистента. Ответ сразу; дорожки он дописывает сам, конец —
        событием `live.stopped`, после которого запись встаёт в расшифровку."""
        return self.live.stop()

    def _live_call(self, call, *args) -> dict:
        try:
            return call(*args)
        except live_control.LiveNotRunning as e:
            raise _conflict(str(e))
        except live_control.LiveError as e:
            raise _bad_request(str(e))

    def live_ask(self, body: dict | None) -> dict:
        question = (body or {}).get("question")
        if not isinstance(question, str) or not question.strip():
            raise _bad_request("пустой вопрос")
        if len(question) > QUESTION_MAX_CHARS:
            raise _bad_request(f"вопрос длиннее {QUESTION_MAX_CHARS} символов")
        return self._live_call(self.live.ask, question.strip())

    def live_task(self, body: dict | None) -> dict:
        task = (body or {}).get("task")
        if not isinstance(task, str) or not task.strip():
            raise _bad_request("пустая задача")
        return self._live_call(self.live.task, task.strip())

    def live_events(self, last_event_id: str | None = None):
        return self._live_call(self.live.open_events, last_event_id)

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
        known = set(settings.DEFAULT_PROCESSES)
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
        """Что увидит запись: устройство вывода (loopback) и микрофон.

        IMPORTANT: спрашиваем **подпроцессом**, а не здесь. PortAudio считает
        ссылки на инициализацию, в проекте живёт один PyAudio-инстанс, и второй,
        созданный и завершённый в потоке HTTP-сервера, рушил состояние
        PortAudio — резидент падал целиком с segfault (поймано faulthandler'ом
        18.08.2026). Подпроцесс умирает вместе со своей инициализацией.

        Только для показа. Закрепить конкретное устройство нельзя осознанно:
        запись следит за *дефолтными* endpoint'ами и переживает их смену — если
        прибить дорожку к устройству, этот механизм сломается (см. recorder)."""
        now = time.monotonic()
        if self._devices_cache and now - self._devices_at < DEVICES_TTL_S:
            return self._devices_cache
        data = (probe or _probe_devices)()
        data["pinning"] = False
        # Кэшируем только успех: иначе мгновенный сбой подпроцесса залипал бы на
        # 15 с, и «Сбросить» не помогал бы, пока TTL не истёк.
        if data.get("available"):
            self._devices_cache, self._devices_at = data, now
        return data

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

    def recordings(self, limit: int = 200, q: str | None = None) -> dict:
        root = self._root()
        if (q or "").strip():
            items = library.search(root, q, limit=limit)
        else:
            items = library.listing(root, limit=limit)
        return {"root": str(root), "items": items}

    def delete_recording(self, recording_id: str) -> dict:
        """Удалить папку записи целиком. Отказ, пока в неё пишут или над ней
        работает расшифровка/импорт: иначе задача упала бы на исчезнувших файлах."""
        import shutil

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        if (self.tray.recording
                and Path(self.tray._current_folder()).resolve() == folder):
            raise _bad_request("запись ещё идёт")
        live_folder = self.live.status()["folder"]
        if live_folder and Path(live_folder).resolve() == folder:
            raise _bad_request("запись ещё идёт")
        if self.queue.active_for(str(folder), (jobs.TRANSCRIBE, jobs.IMPORT)):
            raise _bad_request("идёт расшифровка — отмените её или дождитесь")
        # Задача модели (и её CLI) работает с cwd в папке записи: на Windows
        # rmtree снёс бы файлы и упал на самой папке, а задача дописала бы
        # summary.md и meta.json в осиротевшую папку.
        if self.llm_queue.active_for(str(folder), (jobs.SUMMARY, jobs.ASK)):
            raise _bad_request("Идёт работа модели — отмените или дождитесь")
        try:
            shutil.rmtree(folder)
        except OSError as e:
            raise RuntimeError(f"не удалось удалить запись: {e}") from e
        return {"ok": True}

    # --- hotwords ---------------------------------------------------------

    def _hotwords_reply(self, text: str) -> dict:
        # Те же правила, что у расшифровки (meet.hotwords): повторы не
        # считаются, бюджет общий — счётчик не должен врать о лимите.
        return {"text": text, "budget": hotwords.HOTWORDS_CHAR_BUDGET,
                "used": len(", ".join(hotwords.terms(text)))}

    def get_hotwords(self) -> dict:
        try:
            text = paths.hotwords_path().read_text(encoding="utf-8")
        except OSError:
            text = ""
        return self._hotwords_reply(text)

    def put_hotwords(self, body: dict) -> dict:
        import os

        text = (body or {}).get("text")
        if not isinstance(text, str):
            raise _bad_request("text должен быть строкой")
        path = paths.hotwords_path()
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            raise RuntimeError(f"не удалось сохранить список слов: {e}") from e
        return self._hotwords_reply(text)

    def recording(self, recording_id: str) -> dict:
        folder = self._folder(recording_id)
        card = library.describe(folder) if folder else None
        if card is None:
            return {"error": "записи нет"}
        raw = card.to_raw()
        raw["transcript"] = library.with_display_names(library.read_transcript(folder))
        return raw

    def update_recording(self, recording_id: str, body: dict) -> dict:
        """Переименовать запись. Название живёт в meta.json, а не в транскрипте:
        его можно дать и записи, которая ещё не расшифрована."""
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        card = library.describe(folder)
        if card is None:
            return {"error": "записи нет"}
        title = str((body or {}).get("title") or "").strip()
        if not title:
            return {"error": "пустое название"}
        library.write_meta(folder, {"title": title})
        return library.describe(folder).to_raw()

    def transcript(self, recording_id: str) -> dict:
        folder = self._folder(recording_id)
        data = library.read_transcript(folder) if folder else None
        return library.with_display_names(data) or {"error": "транскрипта нет"}

    def export(self, recording_id: str, fmt: str) -> dict:
        from meet import export

        folder = self._folder(recording_id)
        # Как в редакторе: сырые SPEAKER_XX старых транскриптов — «Спикер N».
        data = library.with_display_names(library.read_transcript(folder)) if folder else None
        if data is None:
            return {"error": "транскрипта нет"}
        title, date = library.title_and_date(folder, data)
        try:
            content = export.render({**data, "title": title}, fmt, date=date)
        except ValueError as e:
            raise _bad_request(str(e))
        safe_name = export.safe_filename(title, recording_id)
        return {"filename": f"{safe_name}.{fmt}", "content": content}

    def save_transcript(self, recording_id: str, data: dict) -> dict:
        """Сохранить правки редактора. Пишем как есть: редактор — владелец
        текста после расшифровки."""
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
            return {"error": "ожидается транскрипт с полем segments"}
        library.write_transcript(folder, data)
        return {"ok": True, "path": str(library.transcript_path(folder))}

    def name_speakers(self, recording_id: str, mapping: dict) -> dict:
        """Назвать спикеров и запомнить их голоса.

        Это и есть «обучение клона»: эмбеддинги из сайдкара расшифровки уходят в
        базу голосов, и на следующей встрече человек узнаётся сам. Порог матчинга
        строгий (0.75 с запасом 0.05) — лучше «Спикер 2», чем чужое имя."""
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        from meet import people

        pairs = {str(k): str(v).strip() for k, v in (mapping or {}).items()
                 if str(v).strip()}
        if not pairs:
            return {"error": "нечего сохранять"}
        # Имя становится именем файла базы голосов: проверяем все до того, как
        # тронуть транскрипт, — иначе отказ оставил бы его наполовину переименованным.
        for label, name in pairs.items():
            try:
                pairs[label] = people.valid_name(name)
            except ValueError as e:
                raise _bad_request(f"«{name}»: {e}")
        data = library.read_transcript(folder)
        renamed = 0
        if data:
            # Старые транскрипты хранят сырые SPEAKER_XX: приводим к «Спикер N»
            # (та же нумерация, что у сайдкара) и переводим ключи pairs.
            raw_to_display = library.display_names(data.get("segments", []))
            for segment in data.get("segments", []):
                segment["speaker"] = raw_to_display.get(segment.get("speaker"),
                                                        segment.get("speaker"))
            pairs = {raw_to_display.get(k, k): v for k, v in pairs.items()}
            for segment in data.get("segments", []):
                if segment.get("speaker") in pairs:
                    segment["speaker"] = pairs[segment["speaker"]]
                    renamed += 1
            names = dict(data.get("names") or {})
            names.update(pairs)
            data["names"] = names
            library.write_transcript(folder, data)
        enrolled, error = self._enroll(folder, pairs)
        return {"ok": True, "renamed": renamed, "enrolled": enrolled,
                "voices_error": error}

    def _enroll(self, folder: Path, pairs: dict) -> tuple[list, str | None]:
        """Записать голоса в базу. Сайдкара нет (расшифровка без эмбеддингов) —
        не ошибка: имена в транскрипте всё равно сохранены."""
        from meet import voices

        try:
            voices.enroll(
                str(folder),
                [f"{label}={name}" for label, name in pairs.items()],
                folder=settings.load().recording.voices,
            )
        except SystemExit as e:  # «нет сайдкара», «битый сайдкар» — текстом
            return [], str(e)
        except Exception as e:
            return [], f"{type(e).__name__}: {e}"
        return sorted(pairs.values()), None

    def engine(self) -> dict:
        """Что установлено для расшифровки и что для неё нужно."""
        from meet import engine as engine_module

        return engine_module.state()

    def install_engine(self, options: dict | None = None) -> dict:
        """Поставить движок задачей — той же очередью, что и расшифровку.

        Установка идёт минуты и гигабайты, поэтому не в потоке резидента: он
        должен оставаться отзывчивым и продолжать писать встречу, если она идёт."""
        job = self.queue.submit(
            jobs.INSTALL_ENGINE, str(paths.data_dir()), options or {}
        )
        return job.to_raw()

    def models(self) -> dict:
        """Каталог моделей с отметками «скачано» и «выбрано»."""
        from meet import models as models_module

        return models_module.state(selected=settings.load().asr.model)

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
        отзывчивым."""
        repo_id = str((body or {}).get("id") or "").strip()
        if not repo_id:
            return {"error": "не сказано, какую модель качать"}
        job = self.queue.submit(jobs.DOWNLOAD_MODEL, repo_id, {})
        return job.to_raw()

    def _submit_once(self, kind: str, folder: Path, options: dict | None = None):
        """Поставить задачу над записью, если над ней уже не ждёт и не идёт
        расшифровка или импорт. Возвращает (задача, поставлена ли новая).

        Проверка и постановка под одним локом: два почти одновременных запроса
        (двойной клик, автопостановка поверх ручной) не должны оба пройти."""
        with self._submit_lock:
            existing = self.queue.active_for(str(folder), (jobs.TRANSCRIBE, jobs.IMPORT))
            if existing is not None:
                return existing, False
            return self.queue.submit(kind, str(folder), options or {}), True

    def transcribe(self, recording_id: str, options: dict | None = None) -> dict:
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        # Импорт, упавший до копии (исходник был недоступен), оставляет папку
        # без дорожки: расшифровывать нечего, повторяем импорт целиком.
        card = library.describe(folder)
        kind = jobs.IMPORT if card and card.source == "import" and not card.tracks \
            else jobs.TRANSCRIBE
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
        items = self.queue.listing() + self.llm_queue.listing()
        items.sort(key=lambda item: item.get("created_at") or 0.0)
        return {"items": items}

    def cancel_job(self, job_id: str) -> dict:
        return {"ok": self.queue.cancel(job_id) or self.llm_queue.cancel(job_id)}

    # --- ассистент: итоги и вопросы ---------------------------------------

    def _transcribed(self, recording_id: str) -> Path | dict:
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        if library.read_transcript(folder) is None:
            return {"error": "транскрипта нет"}
        return folder

    def _ready_for_model(self, folder: Path) -> None:
        """409, если модели сейчас нечего дать: транскрипт вот-вот перепишет
        расшифровка (итоги по нему устарели бы сразу) или не подключён провайдер."""
        from meet import assistant

        if self.queue.active_for(str(folder), (jobs.TRANSCRIBE, jobs.IMPORT)):
            raise _conflict("Дождитесь окончания расшифровки")
        if not _provider_installed(settings.load()):
            raise _conflict(assistant.NO_PROVIDER)

    def make_summary(self, recording_id: str) -> dict:
        """Итоги задачей. Вторая просьба, пока первая ждёт или идёт, — та же задача."""
        folder = self._transcribed(recording_id)
        if isinstance(folder, dict):
            return folder
        self._ready_for_model(folder)
        with self._submit_lock:
            job = self.llm_queue.active_for(str(folder), (jobs.SUMMARY,))
            if job is None:
                job = self.llm_queue.submit(jobs.SUMMARY, str(folder), {})
        return job.to_raw()

    def summary(self, recording_id: str) -> dict:
        from meet import assistant

        folder = self._folder(recording_id)
        found = assistant.read_summary(folder) if folder else None
        return found or {"error": "итогов нет"}

    def ask(self, recording_id: str, body: dict | None) -> dict:
        question = (body or {}).get("question")
        if not isinstance(question, str) or not question.strip():
            raise _bad_request("пустой вопрос")
        question = question.strip()
        if len(question) > QUESTION_MAX_CHARS:
            raise _bad_request(f"вопрос длиннее {QUESTION_MAX_CHARS} символов")
        folder = self._transcribed(recording_id)
        if isinstance(folder, dict):
            return folder
        self._ready_for_model(folder)
        return self.llm_queue.submit(jobs.ASK, str(folder), {"question": question}).to_raw()

    def qa(self, recording_id: str) -> dict:
        from meet import assistant

        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        return {"items": assistant.read_qa(folder)}

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

    def assistant(self) -> dict:
        """Кто ответит и что для этого есть. Не ждёт проверки входа в CLI:
        `checking` — ответ ещё считается в фоне (см. ProviderCache)."""
        from meet.llm import detect

        cfg = settings.load()
        provider, checking = self._providers.get(cfg)
        knowledge = cfg.assistant.knowledge_dir
        return {
            "provider": provider,
            "checking": checking,
            "setting": cfg.llm.provider,
            "available": detect.available(cfg.llm.base_url),
            "knowledge_dir": str(knowledge) if knowledge else None,
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
        job = self.queue.submit(jobs.IMPORT, str(folder), {})
        return {"recording": folder.name, "job": job.to_raw()}

    def track_path(self, recording_id: str, track: str) -> Path | None:
        folder = self._folder(recording_id)
        if folder is None or track not in library.TRACK_STEMS:
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
            # считается статистика и берётся образец.
            if action == "rename":
                people.rename(name, people.valid_name(str((body or {}).get("to") or "")),
                              voices, self._root())
            elif action == "merge":
                people.merge(name, people.valid_name(str((body or {}).get("into") or "")),
                             voices, self._root())
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
