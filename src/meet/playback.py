"""Файл для плеера карточки: обе стороны разговора в одной дорожке.

Запись звонка — две дорожки: `sys` (собеседники, loopback) и `mic` (вы).
Плееру нужна одна: раньше он играл реплику с дорожки «её» спикера и выбирал
дорожку по имени — после переименования владельца микрофона его реплики
уходили на `sys`, где его голоса нет, и звучала тишина. Теперь резидент сводит
обе дорожки в `playback.opus` рядом с ними и кэширует его: перемотка к любой
реплике слышит обоих. То же сведение берёт выгрузка в базу знаний.

Микрофон обычно заметно тише loopback-звука (−38 против −29 дБ на реальной
записи), поэтому его громкость выравнивается (`speechnorm` — быстрый фильтр
для речи: ~25 с на двухчасовую встречу против ~2,5 мин у `loudnorm`), звук
собеседников идёт как есть, а `alimiter` не даёт сумме клиппировать.

Импорт (`source.*`) и запись с одной дорожкой играются как есть, без сведения.

Кэш свежий, пока дорожки те же, что при сведении: их размеры и mtime пишутся
в `playback.json` после удачного сведения (до запуска ffmpeg снимаются, после —
сверяются). Нет отметки или дорожки изменились — сводим заново.

Когда сводится: после расшифровки или импорта — в фоне, по одной записи
(`schedule`), чтобы первое «▶» было мгновенным; иначе — по первому запросу
плеера (он ждёт). Одновременно сводят не больше двух ffmpeg.

`using`/`wait_idle`: кто сейчас держит файлы папки (поток плеера, сведение).
Удаление записи ждёт их недолго — на Windows открытый файл не удаляется.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from meet import library

PLAYBACK_NAME = "playback.opus"
STAMP_NAME = "playback.json"
MIX_TIMEOUT_S = 1800
# Подмена кэша, пока старый файл отдаётся плееру: Windows не даёт заменить
# открытый файл — ждём столько, затем отдаём старый.
REPLACE_TRIES = 10
REPLACE_PAUSE_S = 0.2

# Ослабленный `speechnorm` из примеров ffmpeg: поднимает тихую речь до ~22 дБ,
# но не раздувает шум пауз.
MIC_NORMALIZE = "speechnorm=e=12.5:r=0.00001:l=1"

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_slots = threading.BoundedSemaphore(2)

_busy: dict[str, int] = {}
_idle = threading.Condition()


def _key(folder: Path) -> str:
    try:
        folder = Path(folder).resolve()
    except OSError:
        pass
    return os.path.normcase(str(folder))


@contextmanager
def using(folder: Path):
    """Файлы папки записи сейчас открыты (отдаётся дорожка, идёт сведение)."""
    key = _key(folder)
    with _idle:
        _busy[key] = _busy.get(key, 0) + 1
    try:
        yield
    finally:
        with _idle:
            _busy[key] -= 1
            if _busy[key] <= 0:
                del _busy[key]
            _idle.notify_all()


def wait_idle(folder: Path, timeout: float = 3.0) -> bool:
    """Дождаться, пока файлы папки никто не держит; False — не дождались."""
    key = _key(folder)
    with _idle:
        return _idle.wait_for(lambda: key not in _busy, timeout)


def sources(folder: Path) -> list[Path]:
    """Что играть: импортированный файл или обе стороны звонка (sys, mic)."""
    source = library.find_track(folder, "source")
    if source is not None:
        return [source]
    return [p for p in (library.find_track(folder, "sys"), library.find_track(folder, "mic")) if p]


def mix_command(sys_track: Path, mic: Path, out: Path) -> list[str]:
    """ffmpeg: моно-сумма собеседников и выровненного по громкости микрофона.

    `duration=longest` — дорожки начинаются вместе, но кончаться могут
    по-разному; `normalize=0` — сумма без деления громкости пополам."""
    graph = (
        "[0:a]aformat=channel_layouts=mono[s];"
        f"[1:a]aformat=channel_layouts=mono,{MIC_NORMALIZE}[m];"
        "[s][m]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95:level=disabled"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(sys_track), "-i", str(mic),
        "-filter_complex", graph,
        "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
        "-f", "ogg", str(out),
    ]


def mix(sys_track: Path, mic: Path, out: Path, run=subprocess.run) -> None:
    """Свести две дорожки в `out` (Ogg/Opus). Ошибка — RuntimeError с текстом."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg не найден — дорожки записи не сведены")
    try:
        with _slots:
            proc = run(
                mix_command(sys_track, mic, out),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=MIX_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("ffmpeg не успел свести дорожки") from e
    except OSError as e:
        raise RuntimeError(f"ffmpeg не запустился: {e}") from e
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg не свёл дорожки: {(proc.stderr or '').strip()[-300:]}")


def _stats(srcs: list[Path]) -> list[dict]:
    out = []
    for s in srcs:
        st = s.stat()
        out.append({"name": s.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return out


def fresh(folder: Path, srcs: list[Path]) -> bool:
    """Кэш сведён из тех же дорожек, что лежат сейчас, и не пуст."""
    try:
        if (folder / PLAYBACK_NAME).stat().st_size <= 0:
            return False
        stamp = json.loads((folder / STAMP_NAME).read_text(encoding="utf-8"))
        return stamp.get("sources") == _stats(srcs)
    except (OSError, ValueError, AttributeError):
        return False


def _lock_for(path: Path) -> threading.Lock:
    with _guard:
        return _locks.setdefault(_key(path), threading.Lock())


def _replace(tmp: Path, out: Path) -> bool:
    """Подменить кэш; False — старый файл всё ещё открыт (его отдают плееру)."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, out)
            return True
        except PermissionError:
            if attempt + 1 < REPLACE_TRIES:
                time.sleep(REPLACE_PAUSE_S)
    return False


def playback_path(folder: Path, run=subprocess.run) -> Path | None:
    """Файл для плеера записи `folder`; нет дорожек — None. Сбой — RuntimeError.

    Сведение — одно на папку одновременно (плеер шлёт несколько запросов
    с Range подряд: второй ждёт первый, а не сводит заново). Пишется во
    временный файл и подменяется целиком: плеер не увидит недописанный."""
    folder = Path(folder)
    srcs = sources(folder)
    if not srcs:
        return None
    if len(srcs) == 1:
        return srcs[0]
    out = folder / PLAYBACK_NAME
    with _lock_for(folder), using(folder):
        if fresh(folder, srcs):
            return out
        try:
            before = _stats(srcs)
        except OSError as e:
            raise RuntimeError(f"дорожки записи недоступны: {e}") from e
        tmp = out.with_name(out.name + ".tmp")
        try:
            mix(srcs[0], srcs[1], tmp, run=run)
            if not _replace(tmp, out):
                if out.is_file() and out.stat().st_size > 0:
                    return out  # прежнее сведение; новое — при следующем запросе
                raise RuntimeError("не удалось сохранить сведённую дорожку: файл занят")
            stamp = folder / (STAMP_NAME + ".tmp")
            stamp.write_text(json.dumps({"sources": before}), encoding="utf-8")
            os.replace(stamp, folder / STAMP_NAME)
        except OSError as e:
            raise RuntimeError(f"не удалось свести дорожки для плеера: {e}") from e
        finally:
            tmp.unlink(missing_ok=True)
    return out


# --- фоновое сведение после расшифровки ---------------------------------------

_pending: "queue.Queue[Path]" = queue.Queue()
_queued: set[str] = set()
_worker: threading.Thread | None = None


def _work() -> None:
    while True:
        folder = _pending.get()
        with _guard:
            _queued.discard(_key(folder))
        try:
            if folder.is_dir():
                playback_path(folder)
        except Exception:
            pass  # ошибку покажет плеер, когда его попросят


def schedule(folder: Path) -> None:
    """Свести заранее, в фоне, по одной записи за раз (повтор в очереди — пропуск)."""
    global _worker
    folder = Path(folder)
    with _guard:
        key = _key(folder)
        if key in _queued:
            return
        _queued.add(key)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="playback-mix", daemon=True)
            _worker.start()
    _pending.put(folder)
