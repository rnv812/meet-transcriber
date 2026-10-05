"""Резидент: образец голоса владельца для мастера и настроек «Звук» —
`GET /owner-voice`, `POST /owner-voice/record`, `DELETE /owner-voice/<id>`.

Запись — подпроцессом устройств (`devices_probe --record mic`): в резиденте
живёт один PyAudio, второй рушил PortAudio у записи (см. devices_probe).
Разбор — задачей `owner_voice` (meet.owner_enroll) в своём слоте очереди
загрузок: torch и модель резиденту не нужны, а ждать за часовой расшифровкой
человеку в мастере незачем. Пока идёт запись встречи (или живой режим) —
отказ: микрофон занят ею.

Ход одной попытки («take») держится здесь, окно его опрашивает:
`recording` (идёт запись ~25 с) → `analyzing` (задача) → `done` | `failed`
с текстом ошибки для человека. WAV лежит во временной папке резидента
(`meet-job-<pid>-owner-…`): задача удаляет файл в finally, резидент — папку
по её концу (убитую задачу — при следующем запуске, см. tempdirs).
См. .superpowers/sdd/v033/speakers-design.md, §2.2."""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from pathlib import Path

from meet import jobs, owner_voice

RECORD_S = 25.0
# Подпроцесс записи: сама запись, плюс запуск интерпретатора и PortAudio.
RECORD_TIMEOUT_S = RECORD_S + 20.0
WAV_NAME = "owner.wav"

RECORDING = "recording"
ANALYZING = "analyzing"
DONE = "done"
FAILED = "failed"
ACTIVE = (RECORDING, ANALYZING)

BUSY_RECORDING = "Идёт запись — образец голоса можно записать после неё"
BUSY_TAKE = "Образец голоса уже записывается"
ENGINE_INSTALLING = "Идёт установка движка — образец голоса можно записать после неё"
NO_ENGINE = "Сначала установите движок расшифровки — без него голос не разобрать"
NO_TOKEN = "Нужен токен Hugging Face: без него модель голосов не загрузить"
NO_MODEL = "Скачайте модель разделения на спикеров: отпечаток голоса строит она"


def readiness() -> str | None:
    """Можно ли записать образец: None — да, иначе почему нет (словами).
    Нужны torch, pyannote и VAD (faster-whisper), токен HF и скачанная модель
    диаризации — из её чекпойнта эмбеддер голосов."""
    from meet import credentials, engine, models
    from meet.diarize import DIARIZATION_MODEL

    if not all(engine.installed(m) for m in ("torch", "pyannote.audio", "faster_whisper")):
        return NO_ENGINE
    if credentials.hf_token_source() is None:
        return NO_TOKEN
    if not models.downloaded(DIARIZATION_MODEL):
        return NO_MODEL
    return None


def capture(device: str | None, out: Path) -> dict:
    """Записать RECORD_S с микрофона подпроцессом → его ответ JSON."""
    from meet.tray_control import _run_probe

    args = ["--record", "mic", "--seconds", str(RECORD_S), "--out", str(out)]
    if device:
        args += ["--name", device]
    return _run_probe(args, "ok", RECORD_TIMEOUT_S)


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
    голосов). Запись, готовность и фон подменяются в тестах."""

    def __init__(self, *, queue, bus, busy, voices, installing=lambda: False, log=print, record=None,
                 ready=None, background=None, clock=time.time) -> None:
        self.queue = queue
        self.busy = busy
        self.installing = installing
        self.voices = voices
        self.log = log
        self._record = record or capture
        self._ready = ready or readiness
        self._background = background or (lambda fn: threading.Thread(
            target=fn, name="meet-owner-voice", daemon=True).start())
        self._clock = clock
        self._lock = threading.Lock()
        self._take: dict | None = None
        bus.subscribe(self._on_event)

    # --- маршруты ---------------------------------------------------------

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
        return {"samples": [public(s) for s in owner_voice.load(self.voices())],
                "take": take, "ready": reason is None, "reason": reason,
                "recording": bool(self.busy()), "seconds": RECORD_S}

    def record(self, body: dict | None) -> dict:
        body = body if isinstance(body, dict) else {}
        device = body.get("device")
        if device is not None and not isinstance(device, str):
            raise _bad_request("device — имя микрофона или null")
        device = (device or "").strip() or None
        if self.busy():
            raise _conflict(BUSY_RECORDING)
        if self.installing():
            raise _conflict(ENGINE_INSTALLING)
        reason = self._ready()
        if reason:
            raise _conflict(reason)
        self._reconcile()
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

    # --- попытка ---------------------------------------------------------

    def _fail(self, take: dict, error: str) -> None:
        with self._lock:
            self._drop_dir(take)
            take.update(state=FAILED, error=error)

    def _capture(self, take: dict, device: str | None) -> None:
        from meet import tempdirs

        try:
            folder = Path(tempfile.mkdtemp(prefix=tempdirs.prefix("owner-"), dir=tempdirs.system_temp()))
        except OSError as e:
            self._fail(take, f"Не удалось подготовить запись: {e}")
            return
        take["dir"] = str(folder)
        wav = folder / WAV_NAME
        try:
            got = self._record(device, wav)
        except Exception as e:
            got = {"ok": False, "error": type(e).__name__}
        if not got.get("ok") or not wav.exists():
            self._fail(take, f"Не удалось записать с микрофона: {got.get('error') or 'нет ответа'}")
            return
        name = got.get("device") or device
        try:
            job, _ = self.queue.submit_once(jobs.OWNER_VOICE, str(self.voices()),
                                            {"wav": str(wav), "device": name})
        except jobs.QueueStopped:
            self._fail(take, "Приложение закрывается — образец не разобран")
            return
        with self._lock:
            take.update(state=ANALYZING, device=name, job=job.id)
        self.log(f"голос владельца: записано {got.get('seconds')} с ({name}), разбор — задача {job.id}")
        self._reconcile()  # задача могла кончиться раньше, чем узнали её id

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
        if job.get("kind") == jobs.OWNER_VOICE:
            self._settle(job)

    def _reconcile(self) -> None:
        """Задачу сняли, пока она шла: очередь события не шлёт — сверяемся сами."""
        with self._lock:
            job_id = self._take.get("job") if self._take and self._take["state"] == ANALYZING else None
        if job_id:
            job = self.queue.get(job_id)
            if job is not None:
                self._settle(job.to_raw())

    @staticmethod
    def _drop_dir(take: dict) -> None:
        folder = take.get("dir")
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
