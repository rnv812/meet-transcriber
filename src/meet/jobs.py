"""Очередь задач резидента: расшифровка и всё, что долго считает на GPU.

Задача — **подпроцесс**, а не поток. Причины две, обе практические: падение
CUDA или ctranslate2 не должно ронять резидента вместе с идущей записью, а
видеопамять надёжнее всего освобождается завершением процесса. Слот один: две
расшифровки одновременно всё равно упрутся в одну карту.

Прогресс приходит из подпроцесса построчным JSON — тем же, что пишет
`meet.events`, — и переизлучается в шину резидента с добавленным `job`. Панель и
редактор видят ступени, ничего не зная про подпроцессы.

Движок расшифровки (torch, faster-whisper, pyannote) может быть не установлен —
это штатное состояние машины, на которой только пишут. Тогда задача честно
падает с понятным текстом, а не молчит.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from meet import events

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

# Виды задач. Расшифровка сейчас одна, но очередь заводится общая: следом
# приходят чистовой проход и протокол встречи (см. план, Ф7c).
TRANSCRIBE = "transcribe"
IMPORT = "import"
INSTALL_ENGINE = "install-engine"
DOWNLOAD_MODEL = "download-model"
# Задачи модели по готовой записи (meet.assistant). Идут отдельной очередью
# резидента: итоги не должны ждать часовую расшифровку и наоборот.
SUMMARY = "summary"
ASK = "ask"
# Объединение встреч (meet.merge): склейка дорожек в новую папку записи; за
# ней резидент ставит обычную расшифровку.
MERGE = "merge"
# Правка спикеров без перерасшифровки: голоса реплик спикера («Разделить
# спикера», meet.segvoices) и повторная диаризация («Переразделить на
# спикеров», meet.rediarize). Транскрипт они не пишут — только кэш голосов и
# предпросмотр рядом; применяет результат резидент шагом истории.
SPEAKER_SPLIT = "speaker_split"
REDIARIZE = "rediarize"
# «Анализ встречи» (meet.analysis): разметка расшифровки моделью → analysis.json.
# Очередь модели, как итоги; автоматический — с низким приоритетом.
ANALYZE = "analyze"
# «Улучшить расшифровку» (meet.improve): модель предлагает замены неверно
# распознанных терминов → improve.json; применяет их человек. Очередь модели.
IMPROVE = "improve"
# Образец голоса владельца (meet.owner_enroll): разбор ~25 с, записанных
# подпроцессом устройств, в отпечаток. Папка задачи — база голосов; WAV и
# устройство — в options. Идёт своим слотом (резидент ставит её в
# KeyedQueues), а не за часовой расшифровкой: человек ждёт её в мастере.
# С `derive` в options — поиск голоса по прошлым встречам (meet.owner_derive):
# тот же слот, поэтому запись образца и поиск не идут одновременно.
OWNER_VOICE = "owner_voice"
KINDS = (TRANSCRIBE, IMPORT, INSTALL_ENGINE, DOWNLOAD_MODEL, SUMMARY, ASK, MERGE, SPEAKER_SPLIT, REDIARIZE,
         ANALYZE, IMPROVE, OWNER_VOICE)
# Задачи модели над папкой записи: пишут в неё итоги, ответы и разметку.
MODEL_KINDS = (SUMMARY, ASK, ANALYZE, IMPROVE)
# Задачи, которые пишут в папку записи звук или транскрипт: пока такая ждёт или
# идёт, запись нельзя удалить, объединить или поставить вторую такую же.
FOLDER_KINDS = (TRANSCRIBE, IMPORT, MERGE)
# Задачи, которые читают звук и транскрипт записи (но не пишут их): пока такая
# ждёт или идёт, запись нельзя удалить или объединить.
SPEAKER_KINDS = (SPEAKER_SPLIT, REDIARIZE)

JOB_QUEUED = "job.queued"
JOB_STARTED = "job.started"
JOB_PROGRESS = "job.progress"
JOB_DONE = "job.done"
JOB_FAILED = "job.failed"

ENGINE_HINT = (
    "движок расшифровки не установлен (torch, faster-whisper, pyannote) — "
    "на этой машине можно только записывать"
)


_created_lock = threading.Lock()
_last_created = 0.0


def _created_at() -> float:
    """Время создания задачи, строго возрастающее: у задач, созданных в один тик
    часов, порядок в общем списке очередей не зависит от сортировки."""
    global _last_created
    with _created_lock:
        _last_created = max(time.time(), _last_created + 1e-6)
        return _last_created


@dataclass
class Job:
    id: str
    kind: str
    folder: str
    options: dict = field(default_factory=dict)
    state: str = QUEUED
    stage: str | None = None
    label: str | None = None
    done: float | None = None
    total: float | None = None
    note: str | None = None
    # Ход одной шкалой (meet.progress): номер шага, их число, общая доля,
    # ожидаемая длительность работы. У задач без плана шагов — None.
    step: int | None = None
    steps: int | None = None
    fraction: float | None = None
    estimate_s: float | None = None
    # Доля в конце текущего шага (дальше окно полоску не продлевает) и в чём
    # меряется ход шага («audio_s», «time», «bytes»…), см. meet.progress.
    cap: float | None = None
    unit: str | None = None
    # Задачи модели (meet.llm_progress): часть n из N («окно 2 из 4»),
    # подшаг (request / generating / validating / repair), «дольше обычного»
    # и сколько осталось, секунд (только уверенная оценка).
    part: int | None = None
    parts: int | None = None
    phase: str | None = None
    slow: bool | None = None
    eta_s: float | None = None
    # Предупреждение на всю задачу (meet.progress `warn`): например,
    # «Распознаётся на процессоре: видеокарта NVIDIA не найдена».
    warning: str | None = None
    # Расшифровка: текст уже в transcript.json, идут спикеры (Р4, событие
    # `transcript.text`). Окно показывает текст, не дожидаясь конца задачи.
    text_ready: bool | None = None
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=_created_at)
    started_at: float | None = None
    finished_at: float | None = None

    def to_raw(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "folder": self.folder,
            "state": self.state,
            "stage": self.stage,
            "label": self.label,
            "done": self.done,
            "total": self.total,
            "note": self.note,
            "step": self.step,
            "steps": self.steps,
            "fraction": self.fraction,
            "estimate_s": self.estimate_s,
            "cap": self.cap,
            "unit": self.unit,
            "part": self.part,
            "parts": self.parts,
            "phase": self.phase,
            "slow": self.slow,
            "eta_s": self.eta_s,
            "warning": self.warning,
            "text_ready": self.text_ready,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# Временные папки задач (WAV 16 кГц на время распознавания и диаризации,
# куски GigaAM, ответы Codex): в имени — pid процесса, см. meet.tempdirs.
# Задачу, убитую отменой или выходом резидента, finally не дочищает: её папки
# резидент удаляет сразу после конца задачи и при следующем запуске.
from meet.tempdirs import TEMP_PREFIX, sweep_temp, temp_dir  # noqa: E402,F401


def _sweep_after() -> None:
    """Задача кончилась (или её убили): папки умерших процессов — прочь.

    По всем, а не по pid задачи: `python.exe` venv на Windows — лаунчер, и
    папку пишет его дочерний интерпретатор со своим pid. Идущие процессы (задача
    второй очереди, ассистент) живы — их папки не трогаются."""
    try:
        sweep_temp()
    except Exception:
        pass  # уборка не должна ломать очередь; остальное — при следующем запуске


def worker_argv(job: Job) -> list[str]:
    """Команда подпроцесса. Тот же интерпретатор, что у резидента: движок стоит
    в его окружении, а не в системном."""
    argv = [sys.executable, "-m", "meet.job_worker", job.kind, job.folder]
    options = job.options or {}
    if job.kind == INSTALL_ENGINE:
        if options.get("flavor"):
            argv += ["--flavor", str(options["flavor"])]
        return argv
    if job.kind == DOWNLOAD_MODEL:
        return argv  # путь задачи — это repo_id модели
    if job.kind in (SUMMARY, MERGE, ANALYZE, IMPROVE):
        return argv
    if job.kind == OWNER_VOICE and options.get("derive"):
        # Поиск голоса по прошлым встречам (meet.owner_derive): папка записей
        # одним аргументом через «=» — путь с ведущим дефисом не флаг.
        return argv + ["--derive", f"--recordings={options.get('recordings') or ''}"]
    if job.kind == OWNER_VOICE:
        # Одним аргументом через «=»: имя устройства с ведущим дефисом не флаг.
        argv.append(f"--wav={options.get('wav') or ''}")
        if options.get("device"):
            argv.append(f"--device={options['device']}")
        return argv
    if job.kind == SPEAKER_SPLIT:
        # Одним аргументом через «=»: подпись с ведущим дефисом не станет флагом.
        return argv + [f"--label={options.get('label') or ''}"]
    if job.kind == REDIARIZE:
        for name in ("num_speakers", "min_speakers", "max_speakers"):
            value = options.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                argv.append(f"--{name.replace('_', '-')}={value}")
        value = options.get("sensitivity")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            argv.append(f"--sensitivity={float(value)}")
        return argv

    if job.kind == ASK:
        # Одним аргументом через «=»: вопрос с ведущим дефисом argparse иначе
        # принял бы за флаг.
        return argv + [f"--question={options.get('question') or ''}"]
    if options.get("speakers"):
        argv += ["--speakers", str(int(options["speakers"]))]
    if options.get("hotwords"):
        argv += ["--hotwords", str(options["hotwords"])]
    if options.get("align") is False:
        argv.append("--no-align")
    if options.get("overlap") is False:
        argv.append("--no-overlap")
    return argv


def _kill_tree(process) -> None:
    """Убить подпроцесс вместе с потомками.

    Задача установки движка порождает pip; на Windows `Popen.kill()` (это
    `TerminateProcess`) гасит только родителя, и pip осиротело докачивал бы
    гигабайты. psutil уже зависимость проекта; нет его или процесс уже мёртв —
    падаем назад на одиночный kill."""
    try:
        import psutil

        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _safe_cwd(folder: str) -> str | None:
    """Рабочая папка подпроцесса — только если это реальная директория.

    У расшифровки `folder` — папка записи (годится). У установки движка и
    загрузки модели это не путь вовсе (repo_id вроде `Systran/faster-whisper`),
    и `.parent` от него — несуществующая папка: Windows роняет запуск с
    WinError 267. Не директория — наследуем cwd резидента, worker работает по
    абсолютным путям, и cwd на результат не влияет."""
    try:
        path = Path(folder)
    except (TypeError, ValueError):
        return None
    if path.is_dir():
        return str(path)
    if path.parent.is_dir() and str(path.parent) not in ("", "."):
        return str(path.parent)
    return None


def _folder_key(folder: str) -> str:
    try:
        return os.path.normcase(str(Path(folder).resolve()))
    except (OSError, ValueError):
        return os.path.normcase(str(folder))


# Строки журнала задачи, которые идут и в журнал резидента: время ступеней
# расшифровки и устройство (процессор вместо видеокарты — почему).
RESIDENT_LOG_SOURCES = ("timing", "device")


def _log_line(text: str) -> None:
    """Строка в вывод резидента (оболочка пишет его в logs/resident.log).
    Под pythonw вывода может не быть — тогда молча."""
    try:
        print(text, flush=True)
    except (OSError, ValueError, AttributeError):
        pass


def _creationflags() -> int:
    """Флаги подпроцесса задачи: без окна консоли и с пониженным приоритетом —
    расшифровка грузит все ядра, а встреча и остальная работа тормозить не
    должны. Вне Windows констант нет — тогда 0."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0
    )


class JobQueue:
    """Последовательная очередь на один слот.

    `spawn` подменяется в тестах: настоящий подпроцесс требует установленного
    движка и минут работы, а проверять надо саму очередь.
    """

    def __init__(self, bus=None, spawn=None) -> None:
        self.bus = bus if bus is not None else events.EventBus()
        self._spawn = spawn or self._spawn_subprocess
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._pending: list[str] = []
        # Задачи «в фоне» (автоматический анализ): ждут, пока впереди есть
        # задачи, о которых человек попросил сам.
        self._low: set[str] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._current: Job | None = None
        self._process = None
        self._thread: threading.Thread | None = None
        self._alive = True

    # --- публичное ------------------------------------------------------

    def submit(self, kind: str, folder: str, options: dict | None = None, *,
               low: bool = False) -> Job:
        """Поставить задачу. `low` — фоновая: обычные задачи, поставленные
        позже, встают перед ждущими фоновыми (идущую никто не прерывает)."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, folder=str(folder),
                  options=dict(options or {}))
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            if low:
                self._low.add(job.id)
                self._pending.append(job.id)
            else:
                at = next((n for n, i in enumerate(self._pending) if i in self._low),
                          len(self._pending))
                self._pending.insert(at, job.id)
            self._ensure_worker()
        self._emit(JOB_QUEUED, job)
        self._wake.set()
        return job

    def promote(self, job_id: str) -> bool:
        """Ждущую фоновую задачу — в обычные: встаёт перед остальными фоновыми
        (человек попросил именно её). → поднята ли."""
        with self._lock:
            if job_id not in self._low or job_id not in self._pending:
                return False
            self._low.discard(job_id)
            self._pending.remove(job_id)
            at = next((n for n, i in enumerate(self._pending) if i in self._low), len(self._pending))
            self._pending.insert(at, job_id)
            return True

    @property
    def stopping(self) -> bool:
        """Очередь гасится вместе с резидентом: задача, упавшая сейчас, убита
        остановкой, а не провалилась сама."""
        return not self._alive

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def listing(self, limit: int = 50) -> list[dict]:
        with self._lock:
            ids = self._order[-limit:]
            return [self._jobs[i].to_raw() for i in ids]

    def active(self) -> Job | None:
        with self._lock:
            return self._current

    def active_for(self, folder: str, kinds) -> Job | None:
        """Ждущая или идущая задача одного из видов `kinds` над той же папкой.

        Сравниваем разрешённые пути без учёта регистра (Windows): «Расшифровать»
        дважды, автопостановка после записи поверх ручной — всё это одна и та
        же запись, и вторая расшифровка только заняла бы GPU."""
        key = _folder_key(folder)
        with self._lock:
            for job_id in self._order:
                job = self._jobs[job_id]
                if (job.state in (QUEUED, RUNNING) and job.kind in kinds
                        and _folder_key(job.folder) == key):
                    return job
        return None

    def cancel(self, job_id: str) -> bool:
        """Снять задачу: ждущую — из очереди, идущую — убив подпроцесс.

        IMPORTANT: state в CANCELLED ставится ПОД локом и до kill, даже когда
        подпроцесс ещё не запущен (Popen в процессе). Иначе:
        - отмена идущей задачи оставляла её FAILED «код возврата 1» вместо
          CANCELLED (state не трогался, а kill закрывал пайп и wait возвращал 1);
        - отмена в окне между «взял из очереди» и «self._process опубликован»
          терялась вовсе. Флаг CANCELLED ловит _run (строка с проверкой state)
          и _spawn_subprocess (не публикует уже отменённый процесс)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state in (DONE, FAILED, CANCELLED):
                return False
            if job.id in self._pending:
                self._pending.remove(job.id)
                job.state = CANCELLED
                job.finished_at = time.time()
                return True
            if not (self._current and self._current.id == job_id):
                return False  # не текущая и не ждущая — снять нечего
            job.state = CANCELLED
            job.finished_at = time.time()
            process = self._process  # может быть None: Popen ещё идёт
        if process is not None:
            _kill_tree(process)
        return True

    def stop(self) -> None:
        """Погасить очередь вместе с резидентом.

        Дерево процессов: у задачи установки движка подпроцесс job_worker
        порождает pip, и один kill родителя оставил бы pip качать гигабайты
        осиротевшим. То же для расшифровки, если пайплайн что-то породит."""
        self._alive = False
        self._wake.set()
        process, self._process = self._process, None
        if process is not None:
            _kill_tree(process)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)

    # --- внутреннее -----------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._loop, name="meet-jobs", daemon=True
            )
            self._thread.start()

    def _loop(self) -> None:
        while self._alive:
            with self._lock:
                job_id = self._pending.pop(0) if self._pending else None
                job = self._jobs.get(job_id) if job_id else None
                self._current = job
                # Переход в RUNNING атомарен со снятием с очереди: иначе cancel,
                # успевший поставить CANCELLED, был бы затёрт обратно.
                if job is not None and job.state != CANCELLED:
                    job.state = RUNNING
                    job.started_at = time.time()
            if job is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            try:
                if job.state == CANCELLED:  # отменена, пока ждала слот
                    self._emit(JOB_FAILED, job)
                else:
                    self._run(job)
            except Exception as e:  # очередь не должна умирать от одной задачи
                self._finish(job, FAILED, error=f"{type(e).__name__}: {e}")
            finally:
                with self._lock:
                    self._current = None
                    self._process = None

    def _run(self, job: Job) -> None:
        self._emit(JOB_STARTED, job)
        code = self._spawn(job, self._on_line)
        if job.state == CANCELLED:
            return
        if code == 0 and job.result:
            self._finish(job, DONE)
        elif code == 0:
            # Подпроцесс отработал, но результата не назвал — считать это
            # успехом нельзя: редактору нечего открыть.
            self._finish(job, FAILED, error="задача завершилась без результата")
        else:
            self._finish(job, FAILED, error=job.error or f"код возврата {code}")

    def _on_line(self, line: str) -> None:
        """Строка из подпроцесса: событие пайплайна, результат или текст ошибки."""
        job = self._current
        if job is None:
            return
        try:
            payload = json.loads(line)
        except ValueError:
            # Не JSON — это обычный вывод пайплайна; последняя строка пригодится
            # текстом ошибки, если задача упадёт.
            text = line.strip()
            if text:
                job.error = text[:500]
            return
        kind = payload.get("kind")
        if kind == "progress":
            job.stage = payload.get("stage")
            job.label = payload.get("label")
            job.done = payload.get("done")
            job.total = payload.get("total")
            job.note = payload.get("note")
            job.step = payload.get("step")
            job.steps = payload.get("steps")
            job.fraction = payload.get("fraction")
            job.estimate_s = payload.get("estimate_s")
            job.cap = payload.get("cap")
            job.unit = payload.get("unit")
            job.part = payload.get("part")
            job.parts = payload.get("parts")
            job.phase = payload.get("phase")
            job.slow = payload.get("slow")
            job.eta_s = payload.get("eta_s")
            job.warning = payload.get("warning") or job.warning
            self._emit(JOB_PROGRESS, job)
        elif kind == events.TRANSCRIPT_TEXT:
            # Текст записан до спикеров: окно перечитывает карточку по этому
            # событию хода, а не ждёт конца задачи.
            job.text_ready = True
            self._emit(JOB_PROGRESS, job)
        elif kind == "job.result":
            job.result = payload.get("path")
        elif kind == "error":
            job.error = str(payload.get("text") or payload.get("error") or "")[:500]
        elif kind == "log" and payload.get("source") in RESIDENT_LOG_SOURCES:
            # Время ступеней расшифровки и устройство (процессор вместо
            # видеокарты — почему) — в журнал резидента (resident.log): по
            # нему видно, где и почему уходят минуты.
            _log_line(f"задача {job.kind} ({Path(job.folder).name}): {payload.get('text')}")

    def _finish(self, job: Job, state: str, error: str | None = None) -> None:
        job.state = state
        job.finished_at = time.time()
        if error:
            job.error = error
        self._emit(JOB_DONE if state == DONE else JOB_FAILED, job)

    def _emit(self, kind: str, job: Job) -> None:
        self.bus.emit(kind, job=job.to_raw())

    def _spawn_subprocess(self, job: Job, on_line) -> int:
        """Запустить задачу подпроцессом и прокачать её вывод построчно."""
        from meet import netproxy

        creationflags = _creationflags()
        try:
            process = subprocess.Popen(
                worker_argv(job),
                # Прокси из настроек: Claude Code/Codex и загрузки моделей
                # (Hugging Face, pip) берут его только из переменных среды.
                env=netproxy.settings_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=_safe_cwd(job.folder),
                creationflags=creationflags,
            )
        except OSError as e:
            job.error = f"не удалось запустить задачу: {e}"
            return 1
        # Публикуем процесс под локом и только если задачу не отменили, пока шёл
        # Popen: иначе cancel в этом окне не нашёл бы, что убивать.
        with self._lock:
            if job.state == CANCELLED:
                _kill_tree(process)
                return process.wait()
            self._process = process
        assert process.stdout is not None
        for line in process.stdout:
            on_line(line)
        code = process.wait()
        # Убитая (отмена) или упавшая задача своих временных папок не дочистила.
        _sweep_after()
        return code


class QueueStopped(RuntimeError):
    """Очередь уже гасится вместе с резидентом — новую задачу не поставить."""


class KeyedQueues:
    """Задачи, которые идут одновременно, но не по две над одним ключом: у
    каждого ключа (`folder` задачи; у загрузки модели это её id; сравнение —
    как у `active_for`, через `_folder_key`) свой слот —
    своя JobQueue. Загрузки моделей нагружают сеть и диск, а не GPU: стоять
    друг за другом и за часовой расшифровкой им незачем, а одну и ту же
    модель два загрузчика писали бы в одни и те же файлы.

    Слоты заводятся по первой задаче ключа и живут до `stop` (ключей — столько,
    сколько моделей в каталоге)."""

    def __init__(self, bus=None, spawn=None) -> None:
        self.bus = bus if bus is not None else events.EventBus()
        self._spawn = spawn
        self._queues: dict[str, JobQueue] = {}
        self._lock = threading.Lock()
        self._alive = True

    def submit_once(self, kind: str, folder: str, options: dict | None = None
                    ) -> tuple[Job, bool]:
        """Поставить задачу, если над тем же ключом такая же уже не ждёт и не
        идёт. → (задача, поставлена ли новая). Проверка и постановка — под
        одним локом: двойной клик и два окна (мастер и настройки) не ставят
        вторую загрузку той же модели."""
        key = _folder_key(str(folder))
        with self._lock:
            # Резидент гасится: слот, заведённый после stop, жил бы сиротой.
            if not self._alive:
                raise QueueStopped("очередь остановлена")
            queue = self._queues.get(key)
            if queue is None:
                queue = self._queues[key] = JobQueue(self.bus, spawn=self._spawn)
            existing = queue.active_for(str(folder), (kind,))
            if existing is not None:
                return existing, False
            return queue.submit(kind, str(folder), options), True

    def active_for(self, folder: str, kinds) -> Job | None:
        with self._lock:
            queue = self._queues.get(_folder_key(str(folder)))
        return queue.active_for(str(folder), kinds) if queue is not None else None

    def any_active(self) -> bool:
        """Ждёт или идёт хоть одна задача какого-нибудь ключа (у ключа идущая —
        всегда среди последних: задачи одного ключа идут по одной)."""
        return any(item["state"] in (QUEUED, RUNNING)
                   for queue in self._all() for item in queue.listing())

    def _all(self) -> list[JobQueue]:
        with self._lock:
            return list(self._queues.values())

    def get(self, job_id: str) -> Job | None:
        for queue in self._all():
            job = queue.get(job_id)
            if job is not None:
                return job
        return None

    def listing(self, limit: int = 50) -> list[dict]:
        items = [item for queue in self._all() for item in queue.listing(limit)]
        items.sort(key=lambda item: item.get("created_at") or 0.0)
        return items[-limit:]

    def cancel(self, job_id: str) -> bool:
        return any(queue.cancel(job_id) for queue in self._all())

    @property
    def stopping(self) -> bool:
        return not self._alive

    def stop(self) -> None:
        with self._lock:
            self._alive = False
        for queue in self._all():
            queue.stop()
