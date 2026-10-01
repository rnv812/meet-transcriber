"""Файл для плеера карточки: обе стороны разговора в одной дорожке.

Запись звонка — две дорожки: `sys` (собеседники, loopback) и `mic` (вы).
Плееру нужна одна: раньше он играл реплику с дорожки «её» спикера и выбирал
дорожку по имени — после переименования владельца микрофона его реплики
уходили на `sys`, где его голоса нет, и звучала тишина. Теперь резидент сводит
обе дорожки в `playback.opus` рядом с ними и кэширует его: перемотка к любой
реплике слышит обоих.

Микрофон обычно заметно тише loopback-звука (−38 против −29 дБ на реальной
записи), поэтому его громкость выравнивается (`speechnorm` — быстрый фильтр
для речи: ~25 с на двухчасовую встречу против ~2,5 мин у `loudnorm`), звук
собеседников идёт как есть, а `alimiter` не даёт сумме клиппировать.

Импорт (`source.*`) и запись с одной дорожкой играются как есть, без сведения.
Кэш свежий, пока он не старше дорожек: дозапись или замена дорожки сводит заново.
Сводится по первому запросу плеера (`preload="metadata"` запрашивает файл, как
только карточка открыта): запрос ждёт сведения, повторные берут кэш.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from meet import library

PLAYBACK_NAME = "playback.opus"
MIX_TIMEOUT_S = 600

# Ослабленный `speechnorm` из примеров ffmpeg: поднимает тихую речь до ~22 дБ,
# но не раздувает шум пауз.
MIC_NORMALIZE = "speechnorm=e=12.5:r=0.00001:l=1"

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


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


def fresh(out: Path, srcs: list[Path]) -> bool:
    try:
        made = out.stat()
        return made.st_size > 0 and all(made.st_mtime >= s.stat().st_mtime for s in srcs)
    except OSError:
        return False


def _lock_for(path: Path) -> threading.Lock:
    with _guard:
        return _locks.setdefault(str(path).lower(), threading.Lock())


def playback_path(folder: Path, run=subprocess.run) -> Path | None:
    """Файл для плеера записи `folder`; нет дорожек — None.

    Сведение — один раз на папку одновременно (плеер шлёт несколько запросов
    с Range подряд: второй ждёт первый, а не сводит заново). Пишется во
    временный файл и подменяется целиком: плеер не увидит недописанный."""
    srcs = sources(folder)
    if not srcs:
        return None
    if len(srcs) == 1:
        return srcs[0]
    out = folder / PLAYBACK_NAME
    with _lock_for(out):
        if fresh(out, srcs):
            return out
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg не найден — дорожки записи не сведены для плеера")
        tmp = out.with_name(out.name + ".tmp")
        try:
            proc = run(
                mix_command(srcs[0], srcs[1], tmp),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=MIX_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg не свёл дорожки для плеера: {(proc.stderr or '').strip()[-300:]}")
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
    return out

