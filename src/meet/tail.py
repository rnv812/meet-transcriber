"""Хвост автозаписи: минуты ожидания повторного подключения после звонка.

Автозапись останавливается не в конце звонка, а через `grace_minutes` после
него (см. meet.watch) — иначе перезаход в комнату рвал бы файл. Если человек
не вернулся, в конце записи остаются до часа фона — и сказанное в комнате
после звонка. Резидент пишет в `events.jsonl` момент, когда пропал сигнал
звонка (`record.call_end`), и после остановки, до расшифровки, обрезает
дорожки:

    по концу сигнала звонка + TAIL_PAD_S.

По сигналу, а не по последнему звуку: правило браузерного звонка «липкое»
(meet.watch — пока браузер держит микрофон или играет звук, звонок идёт), так
что сигнал надёжен, а разговор в комнате во время ожидания — уже не встреча,
и хранить его не нужно. Если звонок возобновился за время ожидания, сигнал не
кончился — `record.call_end` пишется по последнему его концу, и вернувшийся
разговор остаётся целиком.

Обрезка — новыми файлами `.part` и подменой всех дорожек разом после того,
как каждая готова; хвост короче MIN_SAVING_S не трогаем (перекодирование
того не стоит) и отмечаем `record.trim_skipped`, чтобы восстановление после
перезапуска не бралось за запись снова.

Объединение встреч (meet.merge) берёт ту же точку как длину части, если
хвост не обрезали (`effective_end`): склейка не тащит в середину встречи
минуты ожидания, а перерыв считается от конца звонка.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from meet import library

TAIL_PAD_S = 30.0
# Короче — не перекодируем: десяток секунд после звонка и так в пределах запаса.
MIN_SAVING_S = 5.0
FFMPEG_TIMEOUT_S = 1800
OPUS_BITRATE = "24k"
RATE = 16000
CALL_END = "record.call_end"
TRIMMED = "record.trimmed"
TRIM_SKIPPED = "record.trim_skipped"


# --- чистые функции ----------------------------------------------------------------


def trim_point(signal_end: float, duration: float, pad: float = TAIL_PAD_S,
               min_saving: float = MIN_SAVING_S) -> float | None:
    """Где обрезать запись: конец сигнала звонка + `pad`. Отрезать остаётся
    меньше `min_saving` — None: не трогаем."""
    cut = min(duration, max(0.0, signal_end) + pad)
    return round(cut, 3) if duration - cut >= min_saving else None


def trim_command(src: Path, out: Path, cut: float) -> list[str]:
    """ffmpeg: первые `cut` секунд дорожки в формате записи."""
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-t", f"{cut:.3f}", "-ac", "1", "-ar", str(RATE), "-c:a", "libopus",
            "-b:a", OPUS_BITRATE, "-application", "voip", "-f", "ogg", str(out)]


# --- events.jsonl ----------------------------------------------------------------------


def _events(folder: Path) -> list[dict]:
    try:
        lines = (Path(folder) / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _append_event(folder: Path, event: dict) -> None:
    with open(Path(folder) / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def write_call_end(folder: Path, at: float, wait_s: float,
                   transcribe: bool | None = None) -> None:
    """Момент, когда пропал сигнал звонка (секунды эпохи), и сколько после него
    ждали повторного подключения. `transcribe` — ставить ли запись в
    расшифровку после обрезки: по нему резидент доделывает работу, если его
    закрыли раньше (см. TrayControl.recover)."""
    event = {"kind": CALL_END, "at": float(at), "wait_s": float(wait_s)}
    if transcribe is not None:
        event["transcribe"] = bool(transcribe)
    _append_event(folder, event)


def call_end(folder: Path) -> dict | None:
    """Последнее событие `record.call_end` записи; нет — None."""
    return next((e for e in reversed(_events(folder)) if e.get("kind") == CALL_END), None)


def pending(folder: Path) -> bool:
    """Конец звонка записан, а решения об обрезке ещё нет: резидент закрыли
    между остановкой записи и обрезкой (или посреди неё)."""
    kinds = {e.get("kind") for e in _events(folder)}
    return CALL_END in kinds and not kinds & {TRIMMED, TRIM_SKIPPED}


def tail_bounds(folder: Path) -> tuple[float, float] | None:
    """(конец сигнала, длительность) от начала записи — если хвост есть и его
    ещё не обрезали; иначе None."""
    events = _events(folder)
    if any(e.get("kind") == TRIMMED for e in events):
        return None
    started = next((e.get("at") for e in events if e.get("kind") == "record.started"), None)
    end = next((e.get("at") for e in reversed(events) if e.get("kind") == CALL_END), None)
    duration = next((e.get("duration_s") for e in reversed(events)
                     if e.get("kind") == "record.stopped"), None)
    if not all(isinstance(v, (int, float)) for v in (started, end, duration)):
        return None
    signal_end = float(end) - float(started)
    if signal_end <= 0 or signal_end >= float(duration):
        return None
    return signal_end, float(duration)


def cut_point(folder: Path, min_saving: float = MIN_SAVING_S) -> float | None:
    """Где обрезать хвост записи `folder` (см. docstring модуля); не нужно —
    None."""
    bounds = tail_bounds(folder)
    if bounds is None:
        return None
    signal_end, duration = bounds
    return trim_point(signal_end, duration, min_saving=min_saving)


def effective_end(folder: Path) -> float | None:
    """Длина части для объединения: хвост после звонка отрезан, даже если он
    короток. Хвоста нет или уже обрезан — None (берётся вся запись)."""
    return cut_point(folder, min_saving=0.0)


# --- с ffmpeg -----------------------------------------------------------------------------


def _run(run, cmd: list[str]):
    return run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
               timeout=FFMPEG_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def trim(folder: Path, run=subprocess.run) -> float | None:
    """Обрезать хвост всех дорожек записи. Возвращает новую длину или None
    (обрезать нечего или не стоит — тогда отметка `record.trim_skipped`).
    Атомарно: сначала все `.part`, затем подмена; любой сбой — `.part`
    удаляются, дорожки как были, отметки нет."""
    folder = Path(folder)
    cut = cut_point(folder)
    if cut is None:
        if pending(folder):
            _append_event(folder, {"kind": TRIM_SKIPPED})
        return None
    tracks = [p for p in (library.find_track(folder, s) for s in library.TRACK_STEMS) if p]
    parts = {track: track.with_name(track.name + ".part") for track in tracks}
    try:
        for track, part in parts.items():
            proc = _run(run, trim_command(track, part, cut))
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg не обрезал {track.name}: {(proc.stderr or '').strip()[-200:]}")
        for track, part in parts.items():
            os.replace(part, track.with_suffix(".opus"))
            if track.suffix != ".opus":
                track.unlink(missing_ok=True)
    finally:
        for part in parts.values():
            part.unlink(missing_ok=True)
    _append_event(folder, {"kind": TRIMMED, "duration_s": cut})
    return cut
