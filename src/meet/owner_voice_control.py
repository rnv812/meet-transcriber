"""Резидент: образец голоса владельца для мастера и настроек «Звук» —
`GET /owner-voice`, `POST /owner-voice/record`, `DELETE /owner-voice/<id>`,
а также «Найти по прошлым встречам»: `POST /owner-voice/derive` (задача
`owner_voice --derive`, meet.owner_derive) и `POST /owner-voice/suggestion`
(`{"accept": true|false}` — «Да, это я» / «Нет»; без ответа ничего не применяется).

Запись — подпроцессом устройств (`devices_probe --record mic`): в резиденте
живёт один PyAudio, второй рушил PortAudio у записи (см. devices_probe).
Разбор — задачей `owner_voice` (meet.owner_enroll) в своём слоте очереди
загрузок: torch и модель резиденту не нужны, а ждать за часовой расшифровкой
человеку в мастере незачем. Пока идёт запись встречи (или живой режим) или
ставится движок — отказ; началась посреди попытки — попытка прерывается.

Ход одной попытки («take») держится здесь, окно его опрашивает:
`recording` (идёт запись ~25 с) → `analyzing` (задача) → `done` | `failed`
с текстом ошибки для человека. WAV лежит во временной папке резидента
(`meet-job-<pid>-owner-…`): задача удаляет файл в finally, а фоновый поток
попытки дожидается конца задачи (и снятой — она события не шлёт) и удаляет
папку сам, не дожидаясь окна. Подпроцесс записи, переживший резидент, файла
не пишет (`--parent-pid`); папку убитого резидента удалит следующий запуск
(см. tempdirs). Зависшая запись по таймауту считается неудачной.
См. .superpowers/sdd/v033/speakers-design.md, §2.2."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from meet import jobs, owner_voice

RECORD_S = 25.0
# Подпроцесс записи: сама запись, плюс запуск интерпретатора и PortAudio.
RECORD_TIMEOUT_S = RECORD_S + 20.0
# Попытка в `recording` дольше этого — поток записи пропал: неудача.
STUCK_S = RECORD_TIMEOUT_S + 15.0
# Фоновый поток ждёт конца задачи разбора (секунды на CPU) не дольше этого.
JOB_WAIT_S = 600.0
JOB_POLL_S = 0.5
WAV_NAME = "owner.wav"

RECORDING = "recording"
ANALYZING = "analyzing"
DONE = "done"
FAILED = "failed"
ACTIVE = (RECORDING, ANALYZING)

BUSY_RECORDING = "Идёт запись — образец голоса можно записать после неё"
STARTED_RECORDING = "Началась запись встречи — образец не сохранён. Запишите его после встречи"
BUSY_TAKE = "Образец голоса уже записывается"
BUSY_JOB = "Образец голоса уже разбирается — дождитесь конца"
ENGINE_INSTALLING = "Идёт установка движка — образец голоса можно записать после неё"
STUCK = "Запись образца не завершилась. Повторите"
NO_ENGINE = "Сначала установите движок расшифровки — без него голос не разобрать"
NO_TOKEN = "Нужен токен Hugging Face — без него модель голосов не загрузить"
NO_MODEL = "Скачайте модель разделения на спикеров — отпечаток голоса строит она"
MODEL_DOWNLOADING = "Модель разделения на спикеров ещё скачивается — подождите немного"
# «Найти по прошлым встречам» (meet.owner_derive): задача читает до десяти
# записей и грузит модель — не во время встречи и не вместе с записью образца.
DERIVE_RECORDING = "Идёт запись — искать голос по прошлым встречам можно после неё"
DERIVE_INSTALLING = "Идёт установка движка — искать голос можно после неё"
DERIVE_BUSY = "Идёт поиск вашего голоса по прошлым встречам — запишите образец после него"
DERIVE_TAKE = "Образец голоса сейчас записывается — искать по встречам можно после этого"
DERIVE_SLOT = "Образец голоса сейчас разбирается — искать по встречам можно после этого"
NO_SUGGESTION = "Предложения уже нет — запустите поиск по прошлым встречам снова"
# Причина последнего поиска («ничего не предложено») показывается не дольше
# недели и пока она правда: совпавший образец на месте, человек — в базе,
# новых встреч не появилось. Иначе она снимается.
NOTE_MAX_DAYS = 7


def readiness(downloading: bool = False) -> str | None:
    """Можно ли записать образец: None — да, иначе почему нет (словами).
    Нужны torch, pyannote и VAD (faster-whisper), токен HF и скачанная модель
    диаризации — из её чекпойнта эмбеддер голосов. `downloading` — модель
    сейчас качается (мастер: шаг моделей отпускает дальше, не дожидаясь)."""
    from meet import credentials, engine, models
    from meet.diarize import DIARIZATION_MODEL

    if not all(engine.installed(m) for m in ("torch", "pyannote.audio", "faster_whisper")):
        return NO_ENGINE
    if credentials.hf_token_source() is None:
        return NO_TOKEN
    if downloading:
        return MODEL_DOWNLOADING
    if not models.downloaded(DIARIZATION_MODEL):
        return NO_MODEL
    return None


def capture(device: str | None, out: Path) -> dict:
    """Записать RECORD_S с микрофона подпроцессом → его ответ JSON. Подпроцесс
    знает pid резидента: резидент умер — файла он не пишет."""
    from meet.tray_control import _run_probe

    args = ["--record", "mic", "--seconds", str(RECORD_S), "--out", str(out),
            "--parent-pid", str(os.getpid())]
    if device:
        args += ["--name", device]
    return _run_probe(args, "ok", RECORD_TIMEOUT_S)


def configured_mic() -> str | None:
    """Микрофон из настроек — тот, с которого пишутся встречи."""
    from meet import settings

    return settings.load().recording.mic_device


def configured_recordings() -> Path:
    """Папка записей из настроек — по ней ищет «Найти по прошлым встречам»."""
    from meet import settings

    return settings.load().recording.recordings


def _playable(ref: dict, root: Path) -> bool:
    """Кусок ещё можно послушать: запись (и её микрофон) не удалена."""
    from meet import library

    name = ref.get("recording")
    if not isinstance(name, str) or not name or Path(name).name != name or name in (".", ".."):
        return False
    return library.find_track(Path(root) / name, "mic") is not None


def public_suggestion(found: dict | None, root: Path | None = None) -> dict | None:
    """Найденный голос для окна — без вектора: встречи и участки для
    прослушивания (кроме кусков удалённых с тех пор записей)."""
    if found is None:
        return None
    samples = found["samples"] if root is None else [r for r in found["samples"] if _playable(r, root)]
    return {"meetings": found["meetings"], "samples": samples, "seconds": found["seconds"],
            "quality": found.get("quality"), "date": found.get("date"),
            "conflict": bool(found.get("conflict"))}


def public(sample: owner_voice.OwnerSample) -> dict:
    """Образец для окна — без вектора."""
    return {"id": sample.id, "source": sample.source, "date": sample.date,
            "seconds": sample.seconds, "device": sample.device,
            "recording": sample.recording, "quality": sample.quality}


def _conflict(text: str):
    from meet.control import Conflict

    return Conflict(text)


def _bad_request(text: str):
    from meet.control import BadRequest

    return BadRequest(text)


class OwnerTakes:
    """Запись и разбор образца голоса, по одной попытке за раз.

    `busy()` — идёт ли запись встречи; `installing()` — ставится ли движок
    (задача грузит его torch, а pip переставлял бы его на ходу); `voices()` —
    папка базы голосов; `queue` — KeyedQueues резидента (слот по папке базы
    голосов; там же загрузки моделей); `mic()` — микрофон из настроек, если
    окно его не назвало. Запись, готовность, фон и часы подменяются в тестах."""

    def __init__(self, *, queue, bus, busy, voices, installing=lambda: False, mic=None, log=print,
                 record=None, ready=None, background=None, clock=time.time, sleep=time.sleep,
                 recordings=None) -> None:
        self.queue = queue
        self.busy = busy
        self.installing = installing
        self.voices = voices
        self.log = log
        self._mic = mic or configured_mic
        self._record = record or capture
        self._ready = ready or self._default_ready
        self._background = background or (lambda fn: threading.Thread(
            target=fn, name="meet-owner-voice", daemon=True).start())
        self._clock = clock
        self._sleep = sleep
        self._recordings = recordings or configured_recordings
        self._lock = threading.Lock()
        self._take: dict | None = None
        # Задача «Найти по прошлым встречам»: id последней (итог — в файле владельца).
        self._derive_job: str | None = None
        bus.subscribe(self._on_event)

    def _default_ready(self) -> str | None:
        from meet.diarize import DIARIZATION_MODEL

        downloading = self.queue.active_for(DIARIZATION_MODEL, (jobs.DOWNLOAD_MODEL,)) is not None
        return readiness(downloading=downloading)

    # --- маршруты ---------------------------------------------------------

    def active(self) -> bool:
        """Идёт ли попытка (запись или разбор) или поиск по прошлым встречам:
        движок в это время не ставят — задача грузит его torch."""
        self._reconcile()
        with self._lock:
            take = bool(self._take and self._take["state"] in ACTIVE)
        return take or self._deriving() is not None

    def status(self) -> dict:
        self._reconcile()
        try:
            reason = self._ready()
        except Exception as e:  # проверка не должна ронять настройки
            reason = f"Не удалось проверить готовность: {type(e).__name__}"
        with self._lock:
            take = dict(self._take) if self._take else None
        if take:
            take.pop("dir", None)
        voices = self.voices()
        return {"samples": [public(s) for s in owner_voice.load(voices)],
                "take": take, "ready": reason is None, "reason": reason,
                "recording": bool(self.busy()), "seconds": RECORD_S,
                "suggestion": public_suggestion(owner_voice.suggestion(voices), self._root()),
                "derive": self._derive_status(voices)}

    def record(self, body: dict | None) -> dict:
        body = body if isinstance(body, dict) else {}
        device = body.get("device")
        if device is not None and not isinstance(device, str):
            raise _bad_request("device — имя микрофона или null")
        if self.busy():
            raise _conflict(BUSY_RECORDING)
        if self.installing():
            raise _conflict(ENGINE_INSTALLING)
        reason = self._ready()
        if reason:
            raise _conflict(reason)
        try:
            device = (device or "").strip() or self._mic() or None
        except Exception:
            device = None  # нечитаемые настройки — системный микрофон
        self._reconcile()
        if self._deriving() is not None:
            raise _conflict(DERIVE_BUSY)
        with self._lock:
            if self._take and self._take["state"] in ACTIVE:
                raise _conflict(BUSY_TAKE)
            self._take = {"state": RECORDING, "device": device, "seconds": RECORD_S,
                          "started_at": self._clock(), "error": None, "sample_id": None,
                          "job": None, "dir": None}
            take = self._take
        self._background(lambda: self._capture(take, device))
        return self.status()

    def delete(self, sample_id: str) -> dict:
        if not owner_voice.remove(sample_id, self.voices()):
            return {"error": "образца нет"}
        return self.status()

    # --- «Найти по прошлым встречам» -------------------------------------

    def _deriving(self):
        """Идущая (или ждущая) задача поиска; нет — None."""
        job_id = self._derive_job
        job = self.queue.get(job_id) if job_id else None
        return job if job is not None and job.state in (jobs.QUEUED, jobs.RUNNING) else None

    def _root(self) -> Path | None:
        try:
            return Path(self._recordings())
        except Exception:
            return None  # нечитаемые настройки — без проверки записей

    def _derive_status(self, voices: Path) -> dict:
        job = self.queue.get(self._derive_job) if self._derive_job else None
        running = job is not None and job.state in (jobs.QUEUED, jobs.RUNNING)
        error = None
        if job is not None and job.state == jobs.FAILED:
            error = job.error or "Поиск не удался"
        return {"running": running, "job": job.id if running else None, "error": error,
                "last": self._last(voices)}

    def _last(self, voices: Path) -> dict | None:
        """Итог последнего поиска; устаревший — снимается (и в файле)."""
        last = owner_voice.derived(voices)
        if not last or last.get("status") == "suggested":
            return last  # предложение показывает карточка, причина не нужна
        if self._stale(last, voices):
            owner_voice.clear_derived(last, voices)
            return None
        return last

    def _stale(self, last: dict, voices: Path) -> bool:
        from datetime import date

        try:
            when = date.fromisoformat(str(last.get("date")))
        except ValueError:
            return True
        if (date.fromtimestamp(self._clock()) - when).days > NOTE_MAX_DAYS:
            return True
        status = last.get("status")
        if status == "already":
            return last.get("sample_id") not in {s.id for s in owner_voice.load(voices)}
        if status == "in_base":
            person = last.get("person")
            if not isinstance(person, str) or Path(person).name != person:
                return True
            return not (Path(voices) / f"{person}.json").is_file()
        root = self._root()
        if root is not None:
            from meet import owner_derive

            newest = owner_derive.newest_recording(root)
            if newest is not None and newest > str(last.get("newest") or ""):
                return True  # с тех пор записаны новые встречи — стоит искать снова
        return False

    def derive(self) -> dict:
        """Запустить поиск голоса по прошлым встречам. Найденное — только
        предложение: образцом оно станет после «Да, это я» (answer)."""
        if self.busy():
            raise _conflict(DERIVE_RECORDING)
        if self.installing():
            raise _conflict(DERIVE_INSTALLING)
        reason = self._ready()
        if reason:
            raise _conflict(reason)
        self._reconcile()
        with self._lock:
            if self._take and self._take["state"] in ACTIVE:
                raise _conflict(DERIVE_TAKE)
        if self._deriving() is None:
            try:
                job, fresh = self.queue.submit_once(
                    jobs.OWNER_VOICE, str(self.voices()),
                    {"derive": True, "recordings": str(self._recordings())})
            except jobs.QueueStopped:
                raise _conflict("Приложение закрывается — поиск не запущен")
            if not fresh and not (job.options or {}).get("derive"):
                raise _conflict(DERIVE_SLOT)  # слот занят разбором записанного образца
            self._derive_job = job.id
            self.log(f"голос владельца: поиск по прошлым встречам — задача {job.id}")
        return self.status()

    def answer(self, body: dict | None) -> dict:
        """«Да, это я» (`accept: true`) — найденный голос образцом `auto`;
        «Нет» (`accept: false`) — предложение снимается."""
        accept = body.get("accept") if isinstance(body, dict) else None
        if not isinstance(accept, bool):
            raise _bad_request("accept — true или false")
        voices = self.voices()
        if accept:
            sample = owner_voice.accept_suggestion(voices)
            if sample is None:
                raise _conflict(NO_SUGGESTION)
            self.log(f"голос владельца: найденный по встречам голос подтверждён ({sample.seconds} с)")
        else:
            owner_voice.clear_suggestion(voices)
        return self.status()

    # --- попытка ---------------------------------------------------------

    def _fail(self, take: dict, error: str) -> None:
        """Попытка не удалась: папку записи — прочь (всегда), состояние — если
        попытка ещё не кончилась (поздний поток не затирает итог)."""
        with self._lock:
            self._drop_dir(take)
            if take["state"] in ACTIVE:
                take.update(state=FAILED, error=error)

    def _capture(self, take: dict, device: str | None) -> None:
        """Фоновый поток попытки: запись, задача, ожидание её конца. Любой сбой
        — неудача попытки с удалённой папкой, а не вечное «записывается»."""
        try:
            self._run(take, device)
        except Exception as e:
            self._fail(take, f"Не удалось записать с микрофона: {type(e).__name__}")

    def _run(self, take: dict, device: str | None) -> None:
        from meet import tempdirs

        folder = Path(tempfile.mkdtemp(prefix=tempdirs.prefix("owner-"), dir=tempdirs.system_temp()))
        with self._lock:
            take["dir"] = str(folder)
        wav = folder / WAV_NAME
        got = self._record(device, wav)
        got = got if isinstance(got, dict) else {}
        if take["state"] != RECORDING:  # попытку уже сочли зависшей
            self._fail(take, STUCK)
            return
        if not got.get("ok") or not wav.exists():
            self._fail(take, f"Не удалось записать с микрофона: {got.get('error') or 'нет ответа'}")
            return
        if self.busy():
            # Встреча началась посреди попытки: в записи может быть звонок.
            self._fail(take, STARTED_RECORDING)
            return
        if self.installing():
            self._fail(take, ENGINE_INSTALLING)
            return
        name = got.get("device") or device
        try:
            job, fresh = self.queue.submit_once(jobs.OWNER_VOICE, str(self.voices()),
                                                {"wav": str(wav), "device": name})
        except jobs.QueueStopped:
            self._fail(take, "Приложение закрывается — образец не разобран")
            return
        if not fresh:  # слот занят чужим разбором — эта запись в него не попадёт
            self._fail(take, BUSY_JOB)
            return
        with self._lock:
            take.update(state=ANALYZING, device=name, job=job.id)
        self.log(f"голос владельца: записано {got.get('seconds')} с ({name}), разбор — задача {job.id}")
        self._wait(job.id)

    def _wait(self, job_id: str) -> None:
        """Дождаться конца задачи и подвести итог: снятая задача события не
        шлёт, а окно могли закрыть — папку записи убираем сами."""
        deadline = self._clock() + JOB_WAIT_S
        while True:
            job = self.queue.get(job_id)
            if job is None or job.state in (jobs.DONE, jobs.FAILED, jobs.CANCELLED):
                break
            if self._clock() >= deadline:
                return  # подведёт status() или следующий запуск (sweep)
            self._sleep(JOB_POLL_S)
        if job is not None:
            self._settle(job.to_raw())

    def _settle(self, job: dict) -> None:
        """Конец задачи → итог попытки. Папку записи убираем до смены
        состояния: окно, увидевшее «готово», файла уже не найдёт."""
        with self._lock:
            take = self._take
            if not take or take.get("job") != job.get("id") or take["state"] != ANALYZING:
                return
            if job.get("state") == jobs.DONE:
                self._drop_dir(take)
                take.update(state=DONE, sample_id=job.get("result"))
            elif job.get("state") in (jobs.FAILED, jobs.CANCELLED):
                self._drop_dir(take)
                take.update(state=FAILED, error=job.get("error") or "Образец не разобран")

    def _on_event(self, event) -> None:
        if event.kind not in (jobs.JOB_DONE, jobs.JOB_FAILED):
            return
        job = event.data.get("job") or {}
        if job.get("kind") == jobs.OWNER_VOICE:  # задача поиска — не попытка: _settle её не узнает
            self._settle(job)

    def _reconcile(self) -> None:
        """Сверка без событий: снятая задача (очередь о ней молчит) и запись,
        зависшая дольше STUCK_S (поток записи пропал)."""
        with self._lock:
            take = self._take
            state = take["state"] if take else None
            job_id = take.get("job") if state == ANALYZING else None
            stuck = state == RECORDING and self._clock() - take["started_at"] > STUCK_S
        if stuck:
            self._fail(take, STUCK)
        if job_id:
            job = self.queue.get(job_id)
            if job is not None:
                self._settle(job.to_raw())

    @staticmethod
    def _drop_dir(take: dict) -> None:
        folder = take.get("dir")
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
