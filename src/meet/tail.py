"""Хвост автозаписи: минуты ожидания повторного подключения без разговора.

Автозапись останавливается не в конце звонка, а через `grace_minutes` после
него (см. meet.watch) — иначе перезаход в комнату рвал бы файл. Если человек
не вернулся, в конце записи остаются до часа фона. Резидент пишет в
`events.jsonl` момент, когда пропал сигнал звонка (`record.call_end`), и после
остановки, до расшифровки, обрезает дорожки:

    по последнему звуку на ЛЮБОЙ дорожке после конца сигнала + TAIL_PAD_S.

По звуку, а не по сигналу: сигнал бывает неверен (веб-клиент на мьюте отпустил
микрофон, а разговор шёл), и такой разговор в хвосте терять нельзя. Звук
ищет ffmpeg `silencedetect` только в хвосте. Обрезка — новыми файлами `.part`
и подменой всех дорожек разом после того, как каждая готова; экономия меньше
MIN_SAVING_S — не трогаем вовсе (перекодирование того не стоит).

Объединение встреч (meet.merge) берёт ту же точку как длину части, если
хвост не обрезали (`effective_end`): склейка не тащит в середину встречи
минуты тишины, а перерыв считается от настоящего конца разговора.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from meet import library

TAIL_PAD_S = 30.0
MIN_SAVING_S = 60.0
# Тише этого — тишина. Микрофон в тишине комнаты ~−60 дБ, речь — выше −40.
SILENCE_DB = -50
SILENCE_MIN_S = 2.0
# silence_end у самого конца хвоста — это ffmpeg закрыл тишину на EOF, а не
# звук: тишина тянется до конца.
EOF_SLACK_S = 1.0
FFMPEG_TIMEOUT_S = 1800
OPUS_BITRATE = "24k"
RATE = 16000
CALL_END = "record.call_end"
TRIMMED = "record.trimmed"

_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


# --- чистые функции ----------------------------------------------------------------


def silence_command(track: Path, start_s: float) -> list[str]:
    """ffmpeg: найти тишину в дорожке, начиная с `start_s` (времена в выводе —
    от этой точки)."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{max(0.0, start_s):.3f}",
            "-i", str(track), "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_S:g}",
            "-f", "null", "-"]


def parse_silences(stderr: str) -> list[tuple[float, float | None]]:
    """Отрезки тишины из вывода silencedetect: [(начало, конец или None)]."""
    out: list[tuple[float, float | None]] = []
    for line in (stderr or "").splitlines():
        m = _START.search(line)
        if m:
            out.append((float(m.group(1)), None))
            continue
        m = _END.search(line)
        if m and out and out[-1][1] is None:
            out[-1] = (out[-1][0], float(m.group(1)))
    return out


def last_activity(silences: list[tuple[float, float | None]], tail_start: float,
                  duration: float) -> float:
    """Когда в хвосте [tail_start, duration] звучало последний раз (от начала
    записи). Тишины нет — звук до конца; последняя тишина тянется до конца
    (не закрыта или закрыта на EOF) — звук до её начала."""
    if not silences:
        return duration
    start, end = silences[-1]
    tail = duration - tail_start
    if end is None or end >= tail - EOF_SLACK_S:
        return tail_start + max(0.0, start)
    return duration


def trim_point(signal_end: float, duration: float, activity: list[float],
               pad: float = TAIL_PAD_S, min_saving: float = MIN_SAVING_S) -> float | None:
    """Где обрезать запись: последний звук любой дорожки после конца сигнала
    (не раньше самого конца сигнала) + `pad`. Экономия меньше `min_saving` —
    None: не трогаем."""
    last = max([signal_end, *activity])
    cut = min(duration, last + pad)
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


def write_call_end(folder: Path, at: float, wait_s: float) -> None:
    """Момент, когда пропал сигнал звонка (секунды эпохи), и сколько после него
    ждали повторного подключения."""
    _append_event(folder, {"kind": CALL_END, "at": float(at), "wait_s": float(wait_s)})


def tail_bounds(folder: Path) -> tuple[float, float] | None:
    """(конец сигнала, длительность) от начала записи — если хвост есть и его
    ещё не обрезали; иначе None."""
    events = _events(folder)
    if any(e.get("kind") == TRIMMED for e in events):
        return None
    started = next((e.get("at") for e in events if e.get("kind") == "record.started"), None)
    call_end = next((e.get("at") for e in reversed(events) if e.get("kind") == CALL_END), None)
    duration = next((e.get("duration_s") for e in reversed(events)
                     if e.get("kind") == "record.stopped"), None)
    if not all(isinstance(v, (int, float)) for v in (started, call_end, duration)):
        return None
    signal_end = float(call_end) - float(started)
    if signal_end <= 0 or signal_end >= float(duration):
        return None
    return signal_end, float(duration)


# --- с ffmpeg -----------------------------------------------------------------------------


def _run(run, cmd: list[str]):
    return run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
               timeout=FFMPEG_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def cut_point(folder: Path, run=subprocess.run, min_saving: float = MIN_SAVING_S) -> float | None:
    """Где обрезать хвост записи `folder` (см. docstring модуля); не нужно,
    нечем или не удалось измерить — None (лучше длинная запись, чем потерянный
    разговор)."""
    bounds = tail_bounds(folder)
    if bounds is None or shutil.which("ffmpeg") is None:
        return None
    signal_end, duration = bounds
    tracks = [p for p in (library.find_track(folder, s) for s in library.TRACK_STEMS) if p]
    if not tracks:
        return None
    activity = []
    for track in tracks:
        try:
            proc = _run(run, silence_command(track, signal_end))
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        activity.append(last_activity(parse_silences(proc.stderr), signal_end, duration))
    return trim_point(signal_end, duration, activity, min_saving=min_saving)


def effective_end(folder: Path, run=subprocess.run) -> float | None:
    """Длина части для объединения: хвост без разговора отрезан, даже если
    экономия мала. Хвоста нет или уже обрезан — None (берётся вся запись)."""
    return cut_point(folder, run=run, min_saving=0.0)


def trim(folder: Path, run=subprocess.run) -> float | None:
    """Обрезать хвост всех дорожек записи. Возвращает новую длину или None
    (обрезать нечего или не стоит). Атомарно: сначала все `.part`, затем
    подмена; любой сбой — `.part` удаляются, дорожки как были."""
    folder = Path(folder)
    cut = cut_point(folder, run=run)
    if cut is None:
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
