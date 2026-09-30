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
import time
from pathlib import Path

from meet import engine, events, gpu_lock, jobs, library, paths, settings, watch

# Источник записи. Константы живут здесь, а не в tray.py: адаптер не должен
# зависеть от модуля, который тянет pystray, — наоборот, tray импортирует их
# отсюда.
AUTO = "auto"
MANUAL = "manual"

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


class TrayControl:
    """Состояние для `meet.control.ControlServer` поверх объекта трея."""

    def __init__(self, tray, queue=None) -> None:
        self.tray = tray
        self.bus = tray.bus
        # Очередь задач живёт рядом с записью, в том же резиденте: расшифровка
        # идёт подпроцессом и не мешает ни записи, ни панели.
        self.queue = queue if queue is not None else jobs.JobQueue(self.bus)
        self._levels: dict = {}
        self._devices_cache: dict | None = None
        self._devices_at = 0.0
        self.bus.subscribe(self._remember_levels)

    def _remember_levels(self, event) -> None:
        """Последние уровни дорожек — чтобы снимок состояния не ждал события."""
        if event.kind == events.RECORD_LEVEL:
            self._levels = event.data.get("levels") or {}

    # --- что показывать -------------------------------------------------

    def snapshot(self) -> dict:
        tray = self.tray
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
            "disk_free_gb": engine._free_gb(settings.load().recording.recordings),
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
            "recordings_dir": str(settings.load().recording.recordings),
            # По живости pid, а не по наличию файла: убитая расшифровка оставляет
            # протухший маркер, и панель показывала бы «GPU занят» вечно.
            "gpu_busy": gpu_lock.held_by_live_process(),
        }

    # --- команды панели -------------------------------------------------

    def start_recording(self) -> dict:
        """Начать вручную. Если запись уже идёт автоматически — это то же
        нажатие «Начать запись» поверх автозаписи, что и в меню трея: человек
        берёт её под свою руку, автостоп отключается."""
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

    def adopt_recording(self) -> dict:
        """Автозапись → ручная: детектор её больше не остановит."""
        if not self.tray.recording:
            return {**self.snapshot(), "ok": False, "action": "not-recording"}
        self.tray.source = MANUAL
        self.tray.watcher.suppress()
        self.tray.log("автозапись переведена в ручную из панели")
        return {**self.snapshot(), "ok": True, "action": "adopted"}

    # --- настройки и диагностика ----------------------------------------

    def settings(self) -> dict:
        return settings.load().to_raw()

    def patch_settings(self, updates: dict) -> dict:
        updated = settings.patch(updates or {})
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

    def recordings(self, limit: int = 200) -> dict:
        return {"root": str(self._root()),
                "items": library.listing(self._root(), limit=limit)}

    def recording(self, recording_id: str) -> dict:
        folder = self._folder(recording_id)
        card = library.describe(folder) if folder else None
        if card is None:
            return {"error": "записи нет"}
        raw = card.to_raw()
        raw["transcript"] = library.read_transcript(folder)
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
        return data or {"error": "транскрипта нет"}

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
        pairs = {str(k): str(v).strip() for k, v in (mapping or {}).items()
                 if str(v).strip()}
        if not pairs:
            return {"error": "нечего сохранять"}
        data = library.read_transcript(folder)
        renamed = 0
        if data:
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

    def download_model(self, body: dict | None = None) -> dict:
        """Скачать модель задачей: это гигабайты, и резидент должен оставаться
        отзывчивым."""
        repo_id = str((body or {}).get("id") or "").strip()
        if not repo_id:
            return {"error": "не сказано, какую модель качать"}
        job = self.queue.submit(jobs.DOWNLOAD_MODEL, repo_id, {})
        return job.to_raw()

    def transcribe(self, recording_id: str, options: dict | None = None) -> dict:
        folder = self._folder(recording_id)
        if folder is None:
            return {"error": "записи нет"}
        job = self.queue.submit(jobs.TRANSCRIBE, str(folder), options or {})
        return job.to_raw()

    def submit_job(self, body: dict) -> dict:
        body = body or {}
        kind = str(body.get("kind") or jobs.TRANSCRIBE)
        if kind != jobs.TRANSCRIBE:
            return {"error": f"неизвестный вид задачи: {kind}"}
        recording_id = str(body.get("recording") or body.get("folder") or "")
        return self.transcribe(recording_id, body.get("options"))

    def jobs(self) -> dict:
        return {"items": self.queue.listing()}

    def cancel_job(self, job_id: str) -> dict:
        return {"ok": self.queue.cancel(job_id)}

    def track_path(self, recording_id: str, track: str) -> Path | None:
        folder = self._folder(recording_id)
        if folder is None or track not in ("sys", "mic"):
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
