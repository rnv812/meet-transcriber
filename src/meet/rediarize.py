"""«Переразделить на спикеров»: заново только диаризация (и узнавание голосов
по базе) по звуку записи — без распознавания речи. Текст остаётся прежним:
реплики раздаются новым спикерам, а сегмент со словами режется по границе
слова там, где внутри него сменился говорящий (у старых расшифровок без слов —
весь сегмент целиком тому, кто говорил в нём дольше).

Задача `rediarize` (подпроцесс очереди) считает результат и кладёт его рядом с
записью (`rediarize.json`) — транскрипт не трогает. Окно показывает
предпросмотр (спикеры, доли, фразы), человек применяет его одним шагом истории
встречи (speakers): разрезы сегментов, подписи, новые голоса в сайдкаре — и всё
это откатывается «Отменить». Если расшифровку с тех пор меняли, применить
нельзя: результат считался по другому тексту.

Параметры: число собеседников (точно или от–до) и чувствительность —
сдвиг порога кластеризации голосов pyannote community-1 (`clustering.threshold`,
по умолчанию 0.6): чувствительнее — порог ниже, людей различается больше."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from meet import library, segvoices, speakers

PREVIEW_NAME = library.REDIARIZE_PREVIEW
# Порог кластеризации community-1 по умолчанию и как далеко его сдвигает
# чувствительность (0..1, середина — как у обычной расшифровки).
DEFAULT_THRESHOLD = 0.6
THRESHOLD_SPAN = 0.4
MAX_SPEAKERS = 20


def clustering_threshold(sensitivity: float | None) -> float | None:
    """Чувствительность 0..1 → порог кластеризации; середина (и None) —
    порог пайплайна как есть."""
    if sensitivity is None:
        return None
    s = min(1.0, max(0.0, float(sensitivity)))
    if abs(s - 0.5) < 1e-6:
        return None
    return round(DEFAULT_THRESHOLD + (0.5 - s) * THRESHOLD_SPAN, 3)


def fingerprint(data: dict) -> str:
    """Отпечаток расшифровки: реплики (время, подпись, текст) и её версия."""
    h = hashlib.sha1(str(data.get("created_at")).encode("utf-8"))
    for s in data.get("segments") or []:
        if isinstance(s, dict):
            h.update(json.dumps([s.get("start"), s.get("end"), s.get("speaker"), s.get("text"),
                                 s.get("kind"), s.get("track")], ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


# --- раздача реплик новым спикерам ----------------------------------------------


def _parts(segment: dict, turns, overlaps) -> list[dict]:
    """Сегмент собеседников → части по новым спикерам (как split_by_speaker
    пайплайна, но над сохранённым транскриптом)."""
    from meet.asr import Word
    from meet.diarize import _word_speaker, _word_uncertain

    if not library.words_match(segment):
        whole = Word(float(segment["start"]), float(segment["end"]), str(segment.get("text") or ""))
        return [{**segment, "speaker": _word_speaker(whole, turns) or segment.get("speaker"),
                 "uncertain": _word_uncertain(whole, overlaps)}]
    runs: list[tuple[tuple, list]] = []
    for w in segment["words"]:
        word = Word(float(w[0]), float(w[1]), str(w[2]))
        key = (_word_speaker(word, turns) or segment.get("speaker"), _word_uncertain(word, overlaps))
        if runs and runs[-1][0] == key:
            runs[-1][1].append(w)
        else:
            runs.append((key, [w]))
    if len(runs) == 1:
        (speaker, uncertain), _ = runs[0]
        return [{**segment, "speaker": speaker, "uncertain": uncertain}]
    out = []
    for n, ((speaker, uncertain), words) in enumerate(runs):
        out.append({**segment, "speaker": speaker, "uncertain": uncertain,
                    "start": segment["start"] if n == 0 else round(float(words[0][0]), 2),
                    "end": segment["end"] if n == len(runs) - 1 else round(float(words[-1][1]), 2),
                    "text": "".join(str(w[2]) for w in words).strip(),
                    "words": [list(w) for w in words]})
    return out


def reassign(folder: Path, segments: list[dict], turns, overlaps) -> list[list[dict]]:
    """Части каждого сегмента (по номеру прежнего): отметки перерыва и
    микрофон владельца — как были, реплики собеседников — новым спикерам.
    Сырые метки SPEAKER_XX ещё не переименованы."""
    tracks = segvoices.tracks_of(folder, segments)
    out = []
    for s, track in zip(segments, tracks):
        if track is None or track == "mic" or not turns:
            out.append([dict(s)])
        else:
            out.append(_parts(s, turns, overlaps))
    return out


def _display(parts: list[list[dict]], name_map: dict[str, str]) -> dict[str, str]:
    """Сырая метка → подпись: узнанное по базе имя или «Спикер N» по порядку
    появления (как у расшифровки)."""
    out: dict[str, str] = {}
    n = 0
    for group in parts:
        for p in group:
            raw = p.get("speaker")
            if not isinstance(raw, str) or not raw.startswith("SPEAKER_") or raw in out:
                continue
            if raw in name_map:
                out[raw] = name_map[raw]
            else:
                n += 1
                out[raw] = f"Спикер {n}"
    return out


# --- задача (подпроцесс) --------------------------------------------------------


def run(folder: Path, *, num_speakers: int | None = None, min_speakers: int | None = None,
        max_speakers: int | None = None, sensitivity: float | None = None, bus=None,
        diarize=None, to_wav=None) -> Path:
    """Посчитать новое разделение и положить предпросмотр рядом с записью.
    `diarize`/`to_wav` подменяются в тестах."""
    from meet import events, settings, transcribe

    bus = bus if bus is not None else events.EventBus()
    data = library.read_transcript(folder)
    if not data or not isinstance(data.get("segments"), list):
        raise ValueError("у записи нет расшифровки")
    segments = [s for s in data["segments"] if isinstance(s, dict)]
    stem = "sys" if library.find_track(folder, "sys") else "source"
    src = library.find_track(folder, stem)
    if src is None:
        raise ValueError("нет звука собеседников — переразделять нечего")
    if diarize is None:
        from meet.diarize import diarize_wav as diarize
    if to_wav is None:
        from meet.audio import to_wav16k as to_wav
    threshold = clustering_threshold(sensitivity)
    with tempfile.TemporaryDirectory() as td:
        bus.progress("convert", done=0, total=1)
        # Как у расшифровки: дорожку собеседников выравниваем по громкости.
        wav = to_wav(src, Path(td) / "audio16.wav", normalize=stem == "sys")
        bus.progress("convert", done=1, total=1)
        bus.progress("diarize")
        diar = diarize(wav, num_speakers=num_speakers, min_speakers=min_speakers,
                       max_speakers=max_speakers, exclusive=not settings.load().asr.overlap,
                       clustering_threshold=threshold)
    if diar.skipped:
        raise RuntimeError("Нет доступа к модели разделения на спикеров — настройте Hugging Face")
    bus.progress("voices")
    name_map = transcribe._match_names(diar, transcribe.voice_threshold(folder))
    parts = reassign(folder, segments, diar.turns, diar.overlaps)
    display = _display(parts, name_map)
    for group in parts:
        for p in group:
            p["speaker"] = display.get(p.get("speaker"), p.get("speaker"))
    voices = [{"label": raw, "display": name_map.get(raw) or display.get(raw, raw),
               "embedding": [float(x) for x in emb]}
              for raw, emb in (diar.embeddings or {}).items()]
    out = folder / PREVIEW_NAME
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base": fingerprint(data),
        "params": {"num_speakers": num_speakers, "min_speakers": min_speakers,
                   "max_speakers": max_speakers, "sensitivity": sensitivity, "threshold": threshold},
        "parts": parts,
        "voices": voices,
    }
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    bus.progress("render", done=1, total=1)
    return out


# --- предпросмотр и применение (резидент) ---------------------------------------


def _read(folder: Path) -> dict | None:
    try:
        data = json.loads((folder / PREVIEW_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("parts"), list) else None


STALE = "Расшифровку изменили после запуска — запустите «Переразделить на спикеров» заново"


def preview(folder: Path) -> dict | None:
    """Что получится: спикеры с долями и фразами, сколько реплик сменят
    спикера и сколько сегментов разрежется. Нет результата — None."""
    got = _read(folder)
    if got is None:
        return None
    data = speakers._transcript(folder)
    old = data["segments"]
    parts = got["parts"]
    stale = got.get("base") != fingerprint(data) or len(parts) != len(old)
    flat = [p for group in parts for p in group]
    shown = speakers._shown(flat)
    order = speakers._order(shown)
    seconds: dict[str, float] = {}
    for p, label in zip(flat, shown):
        if label:
            seconds[label] = seconds.get(label, 0.0) + speakers._duration(p)
    total = sum(seconds.values()) or 1.0
    turns = speakers._turns(flat, shown)
    changed = cut = 0
    if not stale:
        for s, group in zip(old, parts):
            if len(group) > 1:
                cut += 1
            if any(p.get("speaker") != s.get("speaker") for p in group):
                changed += 1
    before = speakers._order(speakers._shown(old))
    rows = [{"label": label, "seconds": round(seconds.get(label, 0.0), 2),
             "share": round(seconds.get(label, 0.0) / total, 4),
             "turns": sum(1 for t in turns if t["label"] == label),
             "samples": speakers._samples(turns, label)} for label in order]
    return {"created_at": got.get("created_at"), "params": got.get("params") or {}, "stale": stale,
            "speakers": rows, "before": len(before), "changed": changed, "cut": cut,
            "segments": len(old)}


def discard(folder: Path) -> bool:
    try:
        (folder / PREVIEW_NAME).unlink()
        return True
    except FileNotFoundError:
        return False


def apply(folder: Path, voices_dir: Path, now: datetime | None = None) -> dict:
    """Применить посчитанное разделение одним шагом истории."""
    speakers.normalize(folder)
    got = _read(folder)
    if got is None:
        raise speakers.SpeakerError("Нового разделения нет — запустите «Переразделить на спикеров»")
    data = speakers._transcript(folder)
    old = data["segments"]
    parts = got["parts"]
    if got.get("base") != fingerprint(data) or len(parts) != len(old):
        raise speakers.Stale(STALE)
    replace, after = [], []
    for i, (s, group) in enumerate(zip(old, parts)):
        if len(group) == 1 and {k: v for k, v in group[0].items() if k != "speaker"} == \
                {k: v for k, v in s.items() if k != "speaker"}:
            after.append(dict(s))  # только подпись — дельтой ниже
            continue
        replace.append({"at": i, "before": s, "after": group})
        after += [dict(p) for p in group]
    targets = []
    for s, group in zip(old, parts):
        if len(group) == 1 and {k: v for k, v in group[0].items() if k != "speaker"} == \
                {k: v for k, v in s.items() if k != "speaker"}:
            targets.append(group[0].get("speaker") if s.get("kind") != "break" else None)
        else:
            targets += [None] * len(group)
    deltas = speakers._deltas(after, targets)
    side_path, side = speakers.sidecar_for_write(folder)
    voices_before = [e for e in side.get("speakers") or [] if isinstance(e, dict)]
    voices_after = [e for e in got.get("voices") or [] if isinstance(e, dict)]
    names_before = data.get("names") if isinstance(data.get("names"), dict) else None
    if not deltas and not replace and _labels(voices_before) == _labels(voices_after):
        discard(folder)
        raise speakers.SpeakerError("Новое разделение совпадает с нынешним — менять нечего")
    new_count = len({p.get("speaker") for g in parts for p in g if p.get("kind") != "break"
                     and p.get("speaker")})
    op = {"type": "rediarize", "speakers": new_count,
          "params": {k: v for k, v in (got.get("params") or {}).items() if v is not None and k != "threshold"}}
    step = speakers._new_step([op], deltas, len(old), now)
    step["count_after"] = len(after)
    step["payload"] = True
    step["names"] = speakers._names_diff(names_before, None)
    for d in deltas:
        for i in d["idx"]:
            after[i]["speaker"] = d["to"]
    data["segments"] = after
    speakers._set_names(data, None)
    meta, _ = speakers._commit(
        folder, data, speakers._recorder(data, step), voices_dir, lambda meta: None,
        sidecar=(side_path, {**side, "speakers": voices_after}),
        payload=(step["id"], {"replace": replace,
                              "sidecar": {"before": voices_before, "after": voices_after}}))
    discard(folder)
    steps, pos = speakers._history_of(meta, data)
    return {"step": speakers._public([step])[0], "history": speakers._public(steps), "pos": pos,
            "trimmed": speakers._trimmed(meta, data), "voices_error": None,
            "changed": sum(len(d["idx"]) for d in deltas) + len(replace)}


def _labels(entries: list[dict]) -> list:
    return sorted((str(e.get("label")), str(e.get("display"))) for e in entries)
