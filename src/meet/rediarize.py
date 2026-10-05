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

Микрофон не переразделяется: его реплики и голоса (владелец `OWNER`, люди в
комнате `SPEAKER_M<n>`, meet.mic_split) остаются как есть. Человек в комнате,
подписанный по кластеру звонка, сохраняет прежнюю подпись на микрофоне, даже
если его кластер в звонке получил новую, — тогда это две строки в панели,
их объединяет «Объединить с…».

Параметры: число собеседников (точно или от–до) и чувствительность —
сдвиг порога кластеризации голосов pyannote community-1 (`clustering.threshold`,
по умолчанию 0.6): чувствительнее — порог ниже, людей различается больше."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

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
    """Отпечаток текста расшифровки: время и текст реплик и её версия. Подписи
    в него не входят — переименование после расчёта результат не портит:
    имена раздаются заново при предпросмотре и применении."""
    h = hashlib.sha1(str(data.get("created_at")).encode("utf-8"))
    for s in data.get("segments") or []:
        if isinstance(s, dict):
            h.update(json.dumps([s.get("start"), s.get("end"), s.get("text"), s.get("kind")],
                                ensure_ascii=False).encode("utf-8"))
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


def reassign(folder: Path, segments: list[dict], turns, overlaps) -> list[list[dict] | None]:
    """Части каждого сегмента (по номеру прежнего) с сырыми метками SPEAKER_XX
    новой диаризации; None — сегмент остаётся как есть (отметка перерыва,
    микрофон владельца)."""
    tracks = segvoices.tracks_of(folder, segments)
    out: list[list[dict] | None] = []
    for s, track in zip(segments, tracks):
        if track is None or track == "mic" or not turns:
            out.append(None)
        else:
            out.append(_parts(s, turns, overlaps))
    return out


# Сходство нового кластера с прежним (косинус центров), с которого новый
# наследует прежнюю подпись — имя, данное вручную, тоже.
SAME_SPEAKER = 0.65
# Доля речи нового кластера под одной нынешней подписью, с которой он её
# наследует (голосование по времени).
MAJORITY = 0.5


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / d) if d else -1.0


def _pairs(sim: np.ndarray, floor: float = SAME_SPEAKER) -> list[tuple[int, int]]:
    """Один к одному: новый кластер i ↔ прежний j с наибольшей суммой
    сходства (венгерский алгоритм; без scipy — жадно), не ниже `floor`."""
    if not sim.size:
        return []
    try:
        from scipy.optimize import linear_sum_assignment

        rows, cols = linear_sum_assignment(-sim)
        pairs = list(zip(rows.tolist(), cols.tolist()))
    except ImportError:
        pairs, used_r, used_c = [], set(), set()
        for flat in np.argsort(-sim, axis=None):
            r, c = divmod(int(flat), sim.shape[1])
            if r not in used_r and c not in used_c:
                pairs.append((r, c))
                used_r.add(r)
                used_c.add(c)
    return [(r, c) for r, c in pairs if sim[r, c] >= floor]


def _manual_keys(folder: Path, data: dict) -> set[tuple]:
    """Время сегментов, которым подпись дали вручную (реплики, разделение)
    после последнего переразделения."""
    keys: set[tuple] = set()
    for step in speakers._applied(folder, data):
        if any(isinstance(op, dict) and op.get("type") == "rediarize" for op in step.get("ops") or []):
            keys = set()
            continue
        for k in step.get("keys") or []:
            if isinstance(k, list) and len(k) == 2:
                keys.add((float(k[0]), float(k[1])))
    return keys


def finish(folder: Path, data: dict, got: dict) -> dict:
    """Подписи нового разделения по нынешнему транскрипту.

    - Новый кластер получает прежнюю подпись, один к одному: сначала по
      времени (большая часть его речи сейчас под этой подписью), затем по
      голосу (косинус с прежним кластером ≥ SAME_SPEAKER). Имя, данное
      вручную, не теряется. Прежний «Спикер N» уступает имени из базы.
    - Остальные — имя из базы или «Спикер N» с номером, не занятым ни одной
      оставшейся подписью.
    - Реплика, исправленная вручную (по времени из истории), остаётся у своего
      спикера, если её сегмент не разрезан, а голос нового кластера — тот же,
      что у прежнего (best effort).

    → {"parts": части по прежним сегментам, "voices": сайдкар, "kept": имена,
    сохранённые по голосу}."""
    segments = data["segments"]
    raw_parts = got["parts"]
    base_names = got.get("names") or {}
    new_voices = {str(k): np.asarray(v, dtype=np.float32) for k, v in (got.get("voices") or {}).items()}
    order: list[str] = []
    for group in raw_parts:
        for p in group or []:
            raw = p.get("speaker")
            if isinstance(raw, str) and raw not in order:
                order.append(raw)
    present = set(speakers._order(speakers._shown(segments)))
    manual = _manual_keys(folder, data)
    # 1. По времени: какой нынешней подписью размечена большая часть речи
    # нового кластера (реплики, исправленные вручную, не голосуют). Так
    # подпись берётся из того, что человек видит в расшифровке, — даже если
    # подписи кластеров в сайдкаре устарели (переименования в базе голосов).
    votes: dict[str, dict[str, float]] = {}
    for s, group in zip(segments, raw_parts):
        label = s.get("speaker")
        if group is None or not label or (float(s["start"]), float(s["end"])) in manual:
            continue
        for p in group:
            raw = p.get("speaker")
            share = votes.setdefault(raw, {})
            share[label] = share.get(label, 0.0) + speakers._duration(p)
    labels = sorted({label for v in votes.values() for label in v})
    vote_raws = [r for r in order if r in votes]
    shares = np.array([[votes[r].get(label, 0.0) / (sum(votes[r].values()) or 1.0) for label in labels]
                       for r in vote_raws]) if vote_raws and labels else np.zeros((0, 0))
    inherited = {vote_raws[r]: labels[c] for r, c in _pairs(shares, MAJORITY)}
    # 2. Остальные — по голосу: центр нового кластера против прежних кластеров
    # сайдкара (их нынешние подписи), один к одному.
    names = data.get("names") if isinstance(data.get("names"), dict) else {}
    old = []
    for e in (speakers._sidecar(folder) or {}).get("speakers") or []:
        # Голоса микрофона (владелец, люди в комнате) не наследуются собеседниками:
        # переразделение — только звонка, микрофон остаётся как есть.
        if isinstance(e, dict) and e.get("track") == "mic":
            continue
        if isinstance(e, dict) and isinstance(e.get("display"), str) and isinstance(e.get("embedding"), list):
            label = speakers._resolve(names, e["display"])
            if label in present and label not in inherited.values():
                old.append((label, np.asarray(e["embedding"], dtype=np.float32)))
    raws = [r for r in order if r in new_voices and r not in inherited]
    sim = np.array([[_cos(new_voices[r], v) if new_voices[r].shape == v.shape else -1.0 for _, v in old]
                    for r in raws]) if raws and old else np.zeros((0, 0))
    for r, c in _pairs(sim):
        if old[c][0] not in inherited.values():
            inherited[raws[r]] = old[c][0]
    label_of: dict[str, str] = {}
    kept: list[str] = []
    # Сначала унаследованные имена (они один к одному), потом имена из базы —
    # только не занятые: два разных голоса под одним именем слились бы снова.
    for raw in order:
        prior = inherited.get(raw)
        if prior is not None and not speakers.unnamed(prior):
            label_of[raw] = prior
            kept.append(prior)
    for raw in order:
        if raw in label_of:
            continue
        prior = inherited.get(raw)
        name = base_names.get(raw)
        if name and name not in label_of.values():
            label_of[raw] = name
        elif prior is not None:
            label_of[raw] = prior
    used = {s.get("speaker") for s, group in zip(segments, raw_parts) if group is None and s.get("speaker")}
    used |= set(label_of.values())
    for raw in order:
        if raw not in label_of:
            label_of[raw] = speakers.fresh_label(used)
            used.add(label_of[raw])
    parts: list[list[dict]] = []
    for s, group in zip(segments, raw_parts):
        if group is None:
            parts.append([dict(s)])
            continue
        out = [{**p, "speaker": label_of.get(p.get("speaker"), p.get("speaker"))} for p in group]
        if (len(group) == 1 and (float(group[0]["start"]), float(group[0]["end"])) in manual
                and (float(s["start"]), float(s["end"])) == (float(group[0]["start"]), float(group[0]["end"]))
                and group[0].get("speaker") in inherited and s.get("speaker")):
            out[0]["speaker"] = s["speaker"]  # ручная правка реплики переживает переразделение
        parts.append(out)
    voices = [{"label": raw, "display": label_of.get(raw, raw), "embedding": [float(x) for x in vec]}
              for raw, vec in new_voices.items()]
    return {"parts": parts, "voices": voices, "kept": sorted(set(kept))}


# --- задача (подпроцесс) --------------------------------------------------------


def run(folder: Path, *, num_speakers: int | None = None, min_speakers: int | None = None,
        max_speakers: int | None = None, sensitivity: float | None = None, bus=None,
        diarize=None, to_wav=None, energy=None) -> Path:
    """Посчитать новое разделение и положить предпросмотр рядом с записью.
    `diarize`/`to_wav`/`energy` (звук для теста громкости) подменяются в тестах."""
    from meet import events, settings, transcribe

    bus = bus if bus is not None else events.EventBus()
    data = library.read_transcript_full(folder)
    if not data or not isinstance(data.get("segments"), list):
        raise ValueError("у записи нет расшифровки")
    if library.is_text_phase(data):
        raise ValueError(library.TEXT_ONLY)
    data = {**data, "segments": [s for s in data["segments"] if isinstance(s, dict)]}
    segments = data["segments"]
    # Старая запись звонка: чьи реплики с микрофона (их не переразделяем) —
    # по звуку обеих дорожек; решения остаются резиденту для применения.
    segvoices.decide_tracks(folder, data, list(range(len(segments))), bus=bus, load=energy)
    segvoices.mark_tracks(folder, data, speakers._owners())
    stem = "sys" if library.find_track(folder, "sys") else "source"
    src = library.find_track(folder, stem)
    if src is None:
        raise ValueError("нет звука собеседников — переразделять нечего")
    if diarize is None:
        from meet.diarize import diarize_wav as diarize
    if to_wav is None:
        from meet.audio import to_wav16k as to_wav
    threshold = clustering_threshold(sensitivity)
    from meet.jobs import temp_dir

    from meet.progress import Stages, Step

    # Ход одной шкалой (meet.progress): диаризация — почти всё время работы.
    stages = Stages(bus, [Step("convert", "convert", 10), Step("diarize", "diarize", 80, measured=True),
                          Step("voices", "voices", 5), Step("render", "render", 5)])
    with temp_dir() as td, stages.ticking():
        stages.begin("convert")
        # Как у расшифровки: дорожку собеседников выравниваем по громкости.
        wav = to_wav(src, Path(td) / "audio16.wav", normalize=stem == "sys")
        stages.update(1)
        _estimate(stages, wav)
        stages.begin("diarize")
        diar = diarize(wav, num_speakers=num_speakers, min_speakers=min_speakers,
                       max_speakers=max_speakers, exclusive=not settings.load().asr.overlap,
                       clustering_threshold=threshold, on_progress=stages.update)
    if diar.skipped:
        raise RuntimeError("Нет доступа к модели разделения на спикеров — настройте Hugging Face")
    if getattr(diar, "device", None) == "cpu":
        from meet.diarize import report_cpu

        report_cpu(bus, stages)
    stages.begin("voices")
    name_map = transcribe._match_names(diar, transcribe.voice_threshold(folder))
    parts = reassign(folder, segments, diar.turns, diar.overlaps)
    voices = {raw: [float(x) for x in emb] for raw, emb in (diar.embeddings or {}).items()}
    out = folder / PREVIEW_NAME
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base": fingerprint(data),
        "params": {"num_speakers": num_speakers, "min_speakers": min_speakers,
                   "max_speakers": max_speakers, "sensitivity": sensitivity, "threshold": threshold},
        "parts": parts,
        "voices": voices,
        "names": name_map,
    }
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    stages.begin("render")
    stages.finish()
    return out


def _estimate(stages, wav: Path) -> None:
    """Ожидаемое время шагов (`progress.step_time`): загрузка пайплайна идёт
    по времени, диаризация — по своей шкале, веса — по времени."""
    from meet.asr import torch_device
    from meet.progress import StepStats, step_time
    from meet.transcribe import wav_seconds

    try:
        # Диаризация — на устройстве torch, а не распознавания.
        seconds, device, stats = wav_seconds(wav), torch_device(), StepStats()
        stages.plan_times({key: step_time(device, None, key, seconds, stats) for key in ("diarize", "voices", "render")})
    except Exception:
        pass  # без оценки — бегущий блик там, где своей шкалы нет


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
    stale = got.get("base") != fingerprint(data) or len(got["parts"]) != len(old)
    if stale:
        return {"created_at": got.get("created_at"), "params": got.get("params") or {}, "stale": True,
                "speakers": [], "before": len(speakers._order(speakers._shown(old))), "changed": 0, "cut": 0,
                "segments": len(old), "kept": []}
    done = finish(folder, data, got)
    parts = done["parts"]
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
            "segments": len(old), "kept": done["kept"]}


def discard(folder: Path) -> bool:
    try:
        (folder / PREVIEW_NAME).unlink()
        return True
    except FileNotFoundError:
        return False


def apply(folder: Path, voices_dir: Path, now: datetime | None = None) -> dict:
    """Применить посчитанное разделение одним шагом истории."""
    got = _read(folder)
    if got is None:
        raise speakers.SpeakerError("Нового разделения нет — запустите «Переразделить на спикеров»")
    data = speakers.editable(folder)
    old = data["segments"]
    if got.get("base") != fingerprint(data) or len(got["parts"]) != len(old):
        raise speakers.Stale(STALE)
    done = finish(folder, data, got)  # подписи — по нынешним, не по тем, что были при расчёте
    parts = done["parts"]
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
    # Голоса микрофона (meet.mic_split) остаются: переразделяется только звонок.
    voices_after = done["voices"] + [e for e in voices_before if e.get("track") == "mic"]
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
