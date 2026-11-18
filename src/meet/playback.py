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

Плеер может играть и одну сторону (переключатель «Обе · Собеседники · Вы»):
собеседники — `sys` как есть, «Вы» — микрофон на шкале текста (`mic_path`):
без применённого сдвига — сам `mic.*`, со сдвигом — `playback-mic.opus`,
сдвинутый тем же фильтром и закэшированный так же, как сведение (своя отметка
`playback-mic.json`). Сырой `mic` по-прежнему отдаётся как есть — по нему
играют образцы голосов, их время уже переведено в шкалу сырой дорожки.

Старая запись, расшифрованная со сдвигом микрофона (meet.track_sync), сводится
с тем же сдвигом: микрофон задерживается (или его начало срезается), чтобы
реплика «Вы» звучала там, где стоит в тексте. Берётся только применённый сдвиг
(из транскрипта), оценённый, но не применённый — нет: текст стоит по
несдвинутому звуку.

Кэш свежий, пока дорожки и сдвиг те же, что при сведении: размеры и mtime
дорожек и сдвиг пишутся в `playback.json` после удачного сведения (до запуска
ffmpeg снимаются, после — сверяются). Нет отметки, дорожки изменились или
перерасшифровка сменила сдвиг — сводим заново. Отметка прежних версий без
сдвига — сдвиг 0.

Когда сводится: после расшифровки или импорта — в фоне, по одной записи
(`schedule`), чтобы первое «▶» было мгновенным; иначе — по первому запросу
плеера (он ждёт). Одновременно сводят не больше двух ffmpeg.

Окно на macOS берёт любую дорожку в WebM (`webm_path`): Ogg/Opus WKWebView
не играет до macOS 15.4, а на 15.4+ играет с неверным медиа-временем — щелчок
по слову и точная остановка образца промахивались бы. ffmpeg перепаковывает
тот же Opus без перекодирования (`-c copy`) в `webm-<дорожка>.webm` рядом с
дорожкой; кэш свежий, пока дорожка та же (отметка `webm-<дорожка>.json`), —
пересведённая или сдвинутая заново дорожка перепаковывается заново. Что WebKit
играет и так (mp3, m4a, wav, webm импорта) — отдаётся как есть.

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

from meet import library, track_sync
from meet.audio import shift_filter

PLAYBACK_NAME = "playback.opus"
STAMP_NAME = "playback.json"
# Дорожка «Вы» со сдвигом микрофона (без сдвига играется сам mic.*).
MIC_NAME = "playback-mic.opus"
MIC_STAMP_NAME = "playback-mic.json"
# Перепаковка для окна на macOS: кэш `webm-<дорожка>.webm` и его отметка. Не
# `sys.webm`: такое имя library.find_track принял бы за дорожку записи.
WEBM_PREFIX = "webm-"
# Что перепаковывается в WebM: Ogg (Opus записи и сведения, Vorbis импорта).
WEBM_FROM = (".opus", ".ogg")
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


def mix_command(sys_track: Path, mic: Path, out: Path, mic_offset: float = 0.0) -> list[str]:
    """ffmpeg: моно-сумма собеседников и выровненного по громкости микрофона.

    `mic_offset` — применённый сдвиг микрофона (meet.track_sync): больше нуля —
    микрофон задержан, меньше — его начало срезано; звук собеседников не
    сдвигается, его время — время текста. `duration=longest` — дорожки
    начинаются вместе, но кончаться могут по-разному; `normalize=0` — сумма без
    деления громкости пополам."""
    shift = shift_filter(mic_offset)
    mic_chain = ",".join(f for f in ("aformat=channel_layouts=mono", shift, MIC_NORMALIZE) if f)
    graph = (
        "[0:a]aformat=channel_layouts=mono[s];"
        f"[1:a]{mic_chain}[m];"
        "[s][m]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95:level=disabled"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(sys_track), "-i", str(mic),
        "-filter_complex", graph,
        "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
        "-f", "ogg", str(out),
    ]


def shift_command(mic: Path, out: Path, mic_offset: float) -> list[str]:
    """ffmpeg: микрофон на шкале текста — сдвинут на применённый сдвиг тем же
    фильтром, что в сведении и расшифровке; громкость как есть."""
    chain = ",".join(f for f in ("aformat=channel_layouts=mono", shift_filter(mic_offset)) if f)
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(mic), "-af", chain,
        "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
        "-f", "ogg", str(out),
    ]


def webm_command(src: Path, out: Path) -> list[str]:
    """ffmpeg: тот же звук в контейнере WebM — поток копируется без перекодирования."""
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-map", "0:a:0", "-c", "copy",
        "-f", "webm", str(out),
    ]


def _ffmpeg(cmd: list[str], run, done: str, failed: str) -> None:
    """ffmpeg под общим ограничением (не больше двух сразу). Сбой — RuntimeError:
    `done` — чем дорожки не стали («сведены»), `failed` — что ffmpeg не сделал."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(f"ffmpeg не найден — дорожки записи не {done}")
    try:
        with _slots:
            proc = run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=MIX_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg не успел: дорожки записи не {done}") from e
    except OSError as e:
        raise RuntimeError(f"ffmpeg не запустился: {e}") from e
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg не {failed}: {(proc.stderr or '').strip()[-300:]}")


def mix(sys_track: Path, mic: Path, out: Path, run=subprocess.run, mic_offset: float = 0.0) -> None:
    """Свести две дорожки в `out` (Ogg/Opus). Ошибка — RuntimeError с текстом."""
    _ffmpeg(mix_command(sys_track, mic, out, mic_offset), run, "сведены", "свёл дорожки")


def _stats(srcs: list[Path]) -> list[dict]:
    out = []
    for s in srcs:
        st = s.stat()
        out.append({"name": s.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return out


def fresh(folder: Path, srcs: list[Path], mic_offset: float = 0.0,
          name: str = PLAYBACK_NAME, stamp_name: str = STAMP_NAME) -> bool:
    """Кэш `name` построен из тех же дорожек, что лежат сейчас, с тем же
    сдвигом микрофона, и не пуст."""
    try:
        if (folder / name).stat().st_size <= 0:
            return False
        stamp = json.loads((folder / stamp_name).read_text(encoding="utf-8"))
        return (stamp.get("sources") == _stats(srcs)
                and float(stamp.get("mic_offset_s", 0.0)) == float(mic_offset))
    except (OSError, ValueError, TypeError, AttributeError):
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


def _cached(folder: Path, srcs: list[Path], offset: float, name: str, stamp_name: str, build) -> Path:
    """Кэш `name` из дорожек `srcs` со сдвигом микрофона `offset`: свежий — как
    есть, иначе `build(tmp)` пишет его во временный файл, и он подменяется
    целиком (плеер не увидит недописанный). Одна сборка файла одновременно:
    плеер шлёт несколько запросов с Range подряд — второй ждёт первый, а не
    собирает заново."""
    out = folder / name
    with _lock_for(out), using(folder):
        if fresh(folder, srcs, offset, name, stamp_name):
            return out
        try:
            before = _stats(srcs)
        except OSError as e:
            raise RuntimeError(f"дорожки записи недоступны: {e}") from e
        tmp = out.with_name(out.name + ".tmp")
        try:
            build(tmp)
            if not _replace(tmp, out):
                if out.is_file() and out.stat().st_size > 0:
                    return out  # прежняя сборка; новая — при следующем запросе
                raise RuntimeError("не удалось сохранить дорожку для плеера: файл занят")
            stamp = folder / (stamp_name + ".tmp")
            stamp.write_text(json.dumps({"sources": before, "mic_offset_s": offset}), encoding="utf-8")
            os.replace(stamp, folder / stamp_name)
        except OSError as e:
            raise RuntimeError(f"не удалось подготовить дорожку для плеера: {e}") from e
        finally:
            tmp.unlink(missing_ok=True)
    return out


def playback_path(folder: Path, run=subprocess.run) -> Path | None:
    """Файл для плеера записи `folder`; нет дорожек — None. Сбой — RuntimeError."""
    folder = Path(folder)
    srcs = sources(folder)
    if not srcs:
        return None
    if len(srcs) == 1:
        return srcs[0]
    # Сдвиг, с которым расшифрован текст: сведение должно с ним совпасть.
    offset = round(track_sync.folder_applied_s(folder), 3)
    return _cached(folder, srcs, offset, PLAYBACK_NAME, STAMP_NAME,
                   lambda tmp: mix(srcs[0], srcs[1], tmp, run=run, mic_offset=offset))


def mic_path(folder: Path, run=subprocess.run) -> Path | None:
    """Дорожка «Вы» для плеера: микрофон на шкале текста; нет микрофона — None.

    Берётся только применённый сдвиг (из транскрипта): оценённый, но не
    применённый в тексте не участвует. Без сдвига — сам `mic.*`, со сдвигом —
    `MIC_NAME`, собранный ffmpeg и закэшированный. Сбой — RuntimeError."""
    folder = Path(folder)
    mic = library.find_track(folder, "mic")
    if mic is None:
        return None
    offset = round(track_sync.folder_applied_s(folder), 3)
    if not offset:
        return mic
    return _cached(folder, [mic], offset, MIC_NAME, MIC_STAMP_NAME,
                   lambda tmp: _ffmpeg(shift_command(mic, tmp, offset), run, "сдвинуты", "сдвинул микрофон"))


def webm_path(src: Path, run=subprocess.run) -> Path:
    """Дорожка `src` для окна на macOS: Ogg — перепакованный в WebM кэш рядом
    (`WEBM_PREFIX` + имя дорожки), остальное — как есть. Сбой — RuntimeError."""
    src = Path(src)
    if src.suffix.lower() not in WEBM_FROM:
        return src
    name = f"{WEBM_PREFIX}{src.stem}"
    return _cached(src.parent, [src], 0.0, f"{name}.webm", f"{name}.json",
                   lambda tmp: _ffmpeg(webm_command(src, tmp), run, "перепакованы", "перепаковал дорожку"))


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
