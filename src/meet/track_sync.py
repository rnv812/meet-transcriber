"""Сдвиг дорожек старой записи: насколько микрофон начал писать позже системного звука.

До общего ноля сессии дорожки стартовали по очереди: `sys` открывалась первой,
`mic` — после неё (на Mac — после рукопожатия помощника, до нескольких секунд).
Начало каждой дорожки — её собственный ноль, поэтому реплики «Вы» в такой
записи стоят раньше, чем звучали.

Оценка — по служебным данным записи, а не по звуку: при «Стоп» обе дорожки
доливаются тишиной до одного момента (`recorder._Session.close` →
`_Track.close(until)`), значит

    длительность(sys) − длительность(mic) = mic.started − sys.started.

Журнал событий (`events.jsonl`) проверяет оценку:

* запись закрыта штатно — после последней «запись начата» есть «запись
  остановлена штатно» (иначе хвосты дорожек не доливались до одного момента);
* оценка попадает в рамку [0; время между «запись начата» и первой строкой
  `mic.opus: …`] — микрофон открылся не раньше системного звука и не позже
  своей строки в журнале;
* хвост не обрезан (`record.trimmed`): обрезка режет все дорожки до одной длины,
  и разница пропадает.

Ранняя доливка тишины у `sys` на Mac (`record.silence` в первые секунды)
отказом не считается: она сдвигает только начало дорожки, а не её длину.

Итог — `meta.json` → `track_offset`: принятая оценка (`mic_s`, `method`,
`bounds`, `estimated_at`) или отказ (`reason`, `estimated_at`). Запись с общим
нолём помечает себя при записи (`recorded_mark`) и не оценивается. Длительность
не узнали (нет ffprobe, файл занят) — это не свойство записи: такой отказ не
запоминается, оценка повторится в следующий раз.

Здесь только оценка; применяет её «Расшифровать заново».
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from meet import library
from meet.merge import probe_duration

META_KEY = "track_offset"

# Способ оценки: разница длительностей дорожек.
METHOD_DURATIONS = "durations"
# Отметка записи с общим нолём дорожек: выровнена при записи, оценивать нечего.
METHOD_RECORDING = "recording"

# Причины отказа (машинные: их показывает карточка).
NO_JOURNAL = "no_journal"  # нет events.jsonl
NO_WINDOW = "no_window"  # в журнале нет «запись начата» или строки mic.opus
UNCLEAN_STOP = "unclean_stop"  # нет «запись остановлена штатно» — процесс убили
TRIMMED = "trimmed"  # хвост обрезан, длины дорожек выровнены обрезкой
OUT_OF_BOUNDS = "out_of_bounds"  # оценка вне рамки журнала
NO_DURATION = "no_duration"  # длительность дорожки не узнали — не запоминается

REASONS = {
    NO_JOURNAL: "у записи нет журнала событий",
    NO_WINDOW: "в журнале нет начала записи или открытия микрофона",
    UNCLEAN_STOP: "запись закрыта нештатно",
    TRIMMED: "хвост записи обрезан — длины дорожек уже не говорят о сдвиге",
    OUT_OF_BOUNDS: "оценка вне рамки журнала",
    NO_DURATION: "длительность дорожки не удалось узнать",
}

# Допуск на зернистость длительностей: последний буфер устройства и кадр Opus
# могут лечь за момент «Стоп». Отрицательная разница в пределах допуска — ноль,
# превышение рамки в пределах допуска — граница рамки.
TOLERANCE_S = 0.1

START_LINE = "запись начата"
STOP_LINE = "запись остановлена штатно"
MIC_PREFIX = "mic.opus: "
TRIMMED_KIND = "record.trimmed"


def recorded_mark() -> dict:
    """Что пишет в meta.json запись с общим нолём дорожек: сдвига нет по построению."""
    return {"method": METHOD_RECORDING, "mic_s": 0.0}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_journal(folder: Path) -> list[dict] | None:
    """Строки `events.jsonl`; None — файла нет. Битые строки пропускаются."""
    try:
        lines = (Path(folder) / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _text(event: dict) -> str:
    text = event.get("text") if event.get("kind") == "log" else None
    return text if isinstance(text, str) else ""


def check_journal(events: list[dict]) -> tuple[float | None, str | None]:
    """Верхняя граница рамки по журналу (секунды) или причина отказа.

    Решает последний сеанс журнала: файл журнала дописывается, а дорожки —
    от последнего старта."""
    starts = [i for i, e in enumerate(events) if _text(e).startswith(START_LINE)]
    if not starts:
        return None, NO_WINDOW
    session = events[starts[-1]:]
    if not any(_text(e) == STOP_LINE for e in session):
        return None, UNCLEAN_STOP
    if any(e.get("kind") == TRIMMED_KIND for e in session):
        return None, TRIMMED
    mic = next((e for e in session if _text(e).startswith(MIC_PREFIX)), None)
    start_at, mic_at = session[0].get("at"), (mic or {}).get("at")
    if not isinstance(start_at, (int, float)) or not isinstance(mic_at, (int, float)):
        return None, NO_WINDOW
    window = float(mic_at) - float(start_at)
    if window < 0:  # стенные часы перевели посреди старта
        return None, NO_WINDOW
    return window, None


def judge(window: float, sys_s: float | None, mic_s: float | None) -> dict:
    """Оценка по длительностям дорожек в рамке [0; window]."""
    if sys_s is None or mic_s is None:
        return {"reason": NO_DURATION}
    raw = float(sys_s) - float(mic_s)
    bounds = [0.0, round(window, 3)]
    if raw < -TOLERANCE_S or raw > window + TOLERANCE_S:
        return {"method": METHOD_DURATIONS, "reason": OUT_OF_BOUNDS,
                "raw_s": round(raw, 3), "bounds": bounds}
    return {"mic_s": round(min(max(raw, 0.0), window), 3), "method": METHOD_DURATIONS,
            "bounds": bounds}


def estimate(events: list[dict], sys_s: float | None, mic_s: float | None) -> dict:
    """Чистая оценка: журнал и длительности дорожек → `track_offset` без времени."""
    window, reason = check_journal(events)
    if reason is not None:
        return {"reason": reason}
    return judge(window, sys_s, mic_s)


def _tracks(folder: Path) -> tuple[Path, Path] | None:
    sys_track = library.find_track(folder, "sys")
    mic_track = library.find_track(folder, "mic")
    return (sys_track, mic_track) if sys_track and mic_track else None


def needs_estimate(folder: Path) -> bool:
    """Двухдорожечная запись, у которой ещё нет ни оценки, ни отказа, ни отметки."""
    folder = Path(folder)
    return _tracks(folder) is not None and META_KEY not in library.read_meta(folder)


def estimate_folder(folder: Path, probe=None) -> dict | None:
    """Оценка папки записи без записи в meta.json (None — запись не двухдорожечная).
    Дорожки открываются (ffprobe) только если журнал оценку допускает."""
    folder = Path(folder)
    tracks = _tracks(folder)
    if tracks is None:
        return None
    events = read_journal(folder)
    if events is None:
        return {"reason": NO_JOURNAL}
    window, reason = check_journal(events)
    if reason is not None:
        return {"reason": reason}
    probe = probe or probe_duration
    return judge(window, probe(tracks[0]), probe(tracks[1]))


def ensure(folder: Path, probe=None) -> dict | None:
    """`track_offset` записи: уже записанный — как есть, иначе оценить и записать.

    Один раз на запись: принятая оценка и отказ запоминаются в meta.json. Не
    запоминается только «длительность не узнали» — это сбой окружения, а не
    свойство записи. None — запись не двухдорожечная."""
    folder = Path(folder)
    stored = library.read_meta(folder).get(META_KEY)
    if stored is not None:
        return stored
    got = estimate_folder(folder, probe)
    if got is None or got.get("reason") == NO_DURATION:
        return got
    got = {**got, "estimated_at": _now()}

    def change(meta: dict) -> dict:
        if META_KEY in meta:  # другой поток успел раньше — его оценка и остаётся
            return meta
        return {**meta, META_KEY: got}

    return library.update_meta(folder, change)[META_KEY]
