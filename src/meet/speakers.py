"""Спикеры одной встречи для панели «Спикеры» карточки: кто сколько говорил,
подсказки по базе голосов, набор правок одним шагом и откат шагов.

Транскрипт хранит у реплики готовую подпись спикера («Спикер 2», «Анна»), а
голос кластера лежит в сайдкаре расшифровки под исходной подписью (`display`).
Связь между ними — `names` транскрипта (подпись сайдкара → нынешнее имя, бывает
цепочкой: так его вели и прежние «Назвать спикеров»). Каждая правка из панели
держит `names` в согласии с репликами — по нему строка панели находит свой
голос, даже если её переименовали или объединили с другой.

История — в meta.json записи: `speaker_history` (шаги), `speaker_history_pos`
(сколько из них применено) и `speaker_history_base` (`created_at` транскрипта,
к которому они относятся: перерасшифровка начинает историю заново). Шаг хранит
ровно то, что нужно для отката: какие реплики с какой подписи на какую сменил,
`names` до и после, и какие образцы голоса записал в базу (по id — их убирают
точно, вместе с людьми, которых шаг создал и у которых больше ничего нет).
Перед откатом и повтором шаг сверяется с транскриптом: если реплики с тех пор
поменяли (переименовали человека в базе голосов, правили вручную), откат честно
отказывает, а не портит расшифровку."""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np

from meet import library, people, voices

HISTORY = "speaker_history"
POS = "speaker_history_pos"
BASE = "speaker_history_base"
# Подсказки из базы голосов: ниже 40% — шум, а не похожий голос.
SUGGEST_MIN = 0.40
SUGGEST_TOP = 3
SAMPLE_TURNS = 3
SAMPLE_TEXT_MAX = 300
# Как склеивает реплики окно (lib/speakers.ts): пауза меньше 2 с — та же реплика.
GAP_S = 2.0
_UNNAMED = re.compile(r"^(Спикер \d+|SPEAKER_\d+)$")
TYPES = ("rename", "merge", "reset")


class SpeakerError(ValueError):
    """Неверный набор правок или нечего делать — ошибка ввода (400)."""


class Stale(RuntimeError):
    """Шаг истории не сходится с транскриптом: его уже не отменить (409)."""


def unnamed(label: str) -> bool:
    return bool(_UNNAMED.match(label or ""))


# --- чтение -------------------------------------------------------------------


def _transcript(folder: Path) -> dict:
    data = library.read_transcript(folder)
    if not data or not isinstance(data.get("segments"), list):
        raise SpeakerError("у записи нет расшифровки")
    data["segments"] = [s for s in data["segments"] if isinstance(s, dict)]
    return data


def _shown(segments: list[dict]) -> list[str | None]:
    """Подпись каждой реплики, как её видит окно: сырые SPEAKER_XX старых
    транскриптов — «Спикер N»; у отметки перерыва спикера нет."""
    raw = library.display_names(segments)
    out: list[str | None] = []
    for s in segments:
        speaker = s.get("speaker")
        if s.get("kind") == "break" or not isinstance(speaker, str) or not speaker:
            out.append(None)
        else:
            out.append(raw.get(speaker, speaker))
    return out


def _order(shown: list[str | None]) -> list[str]:
    return list(dict.fromkeys(s for s in shown if s))


def _sidecar(folder: Path) -> dict | None:
    for path in sorted(folder.glob("*_speakers.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("speakers"), list):
            return data
    return None


def _resolve(names: dict, label: str) -> str:
    seen = {label}
    while isinstance(names.get(label), str) and names[label] not in seen:
        label = names[label]
        seen.add(label)
    return label


def _clusters(data: dict, sidecar: dict | None, current: set[str]) -> dict[str, list[dict]]:
    """Нынешняя подпись → записи сайдкара (кластеры диаризации) с её голосом."""
    names = data.get("names") if isinstance(data.get("names"), dict) else {}
    out: dict[str, list[dict]] = {}
    for entry in (sidecar or {}).get("speakers") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("display"), str):
            continue
        label = _resolve(names, entry["display"])
        if label in current and isinstance(entry.get("embedding"), list):
            out.setdefault(label, []).append(entry)
    return out


def _duration(s: dict) -> float:
    try:
        return max(0.0, float(s["end"]) - float(s["start"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _turns(segments: list[dict], shown: list[str | None]) -> list[dict]:
    """Реплики как в карточке: подряд идущие сегменты одного спикера."""
    out: list[dict] = []
    for s, label in zip(segments, shown):
        try:
            start, end = float(s.get("start") or 0.0), float(s.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        last = out[-1] if out else None
        if label and last and last["label"] == label and start - last["end"] < GAP_S:
            last["end"] = max(last["end"], end)
            last["texts"].append(str(s.get("text") or ""))
            last["uncertain"] = last["uncertain"] or bool(s.get("uncertain"))
        else:
            out.append({"label": label, "start": start, "end": end,
                        "texts": [str(s.get("text") or "")], "uncertain": bool(s.get("uncertain"))})
    return out


def _samples(turns: list[dict], label: str) -> list[dict]:
    """Самые длинные «чистые» (без нахлёста) реплики спикера — по ним его узнают."""
    own = [t for t in turns if t["label"] == label and not t["uncertain"]]
    own.sort(key=lambda t: t["end"] - t["start"], reverse=True)
    return [{"start": round(t["start"], 2), "end": round(t["end"], 2),
             "text": " ".join(x for x in t["texts"] if x)[:SAMPLE_TEXT_MAX]}
            for t in own[:SAMPLE_TURNS]]


def _suggestions(entries: list[dict], base: dict[str, list[np.ndarray]]) -> list[dict]:
    """Люди базы, чей голос похож: как в voices.best_match — максимум косинуса
    по образцам человека (и по кластерам строки, если их несколько)."""
    if not entries or not base:
        return []
    embs = [np.asarray(e["embedding"], dtype=np.float32) for e in entries]
    scored = []
    for name, samples in base.items():
        # Образец другой модели (иная длина вектора) сравнивать не с чем.
        score = max((voices._cos(e, x) for e in embs for x in samples if e.shape == x.shape),
                    default=-1.0)
        if score >= SUGGEST_MIN:
            scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"name": name, "score": round(score, 3)} for score, name in scored[:SUGGEST_TOP]]


def _history(folder: Path, data: dict) -> tuple[list[dict], int]:
    return _history_of(library.read_meta(folder), data)


def _history_of(meta: dict, data: dict) -> tuple[list[dict], int]:
    """История правок этой расшифровки и сколько шагов применено; от прежней
    (до перерасшифровки) — пусто."""
    steps = meta.get(HISTORY)
    if not isinstance(steps, list) or meta.get(BASE) != data.get("created_at"):
        return [], 0
    steps = [s for s in steps if isinstance(s, dict)]
    pos = meta.get(POS)
    return steps, pos if isinstance(pos, int) and 0 <= pos <= len(steps) else len(steps)


def _public(steps: list[dict]) -> list[dict]:
    """Шаги для окна: без служебного (реплики, names) — только что и когда."""
    keep = ("id", "at", "ops", "enrolled", "created_people")
    return [{k: s.get(k) for k in keep} for s in steps]


def overview(folder: Path, voices_dir: Path, owner: str = "Вы") -> dict:
    data = _transcript(folder)
    segments = data["segments"]
    shown = _shown(segments)
    order = _order(shown)
    seconds: dict[str, float] = {}
    for s, label in zip(segments, shown):
        if label:
            seconds[label] = seconds.get(label, 0.0) + _duration(s)
    total = sum(seconds.values()) or 1.0
    turns = _turns(segments, shown)
    clusters = _clusters(data, _sidecar(folder), set(order))
    base = voices.load_voices(voices_dir) if clusters else {}
    rows = []
    for label in order:
        entries = clusters.get(label, [])
        rows.append({
            "label": label,
            "name": None if unnamed(label) else label,
            "seconds": round(seconds.get(label, 0.0), 2),
            "share": round(seconds.get(label, 0.0) / total, 4),
            "turns": sum(1 for t in turns if t["label"] == label),
            "samples": _samples(turns, label),
            "has_voice": bool(entries),
            "suggestions": _suggestions(entries, base),
        })
    steps, pos = _history(folder, data)
    return {"speakers": rows, "owner": owner, "history": _public(steps), "pos": pos}


# --- применение ---------------------------------------------------------------


def _finals(ops: list, order: list[str], clusters: dict[str, list[dict]]) -> tuple[dict, list]:
    """Набор правок → итоговая подпись каждой нынешней (все сразу: обмен
    именами двух спикеров — не цепочка переименований) и нормализованные ops."""
    if not isinstance(ops, list) or not ops:
        raise SpeakerError("нечего применять")
    present = set(order)
    by_label: dict[str, dict] = {}
    for op in ops:
        if not isinstance(op, dict):
            raise SpeakerError("непонятная правка")
        kind, label = op.get("type"), op.get("label")
        if kind not in TYPES:
            raise SpeakerError(f"неизвестная правка «{kind}»")
        if label not in present:
            raise SpeakerError(f"в записи нет спикера «{label}»")
        if label in by_label:
            raise SpeakerError(f"«{label}» изменён дважды — оставьте одну правку")
        by_label[label] = op
    finals: dict[str, str] = {}
    for label in order:
        op = by_label.get(label)
        if op is None:
            finals[label] = label
        elif op["type"] == "rename":
            to = str(op.get("to") or "").strip()
            try:
                to = people.valid_name(to)
            except ValueError as e:
                raise SpeakerError(f"«{to}»: {e}")
            if unnamed(to):
                raise SpeakerError(f"«{to}» — служебная подпись; чтобы снять имя, выберите «Неизвестный»")
            finals[label] = to
    # «Неизвестный»: прежний номер кластера, если он свободен, иначе следующий свободный.
    used = set(finals.values())
    for label in order:
        op = by_label.get(label)
        if op is None or op["type"] != "reset":
            continue
        own = [e["display"] for e in clusters.get(label, []) if unnamed(e["display"])
               and not e["display"].startswith("SPEAKER_")]
        free = next((d for d in own if d not in used), None)
        n = 1
        while free is None:
            if f"Спикер {n}" not in used:
                free = f"Спикер {n}"
            n += 1
        finals[label] = free
        used.add(free)

    def merged(label: str, path: tuple) -> str:
        if label in finals:
            return finals[label]
        target = by_label[label].get("to")
        if target == label:
            raise SpeakerError(f"«{label}» нельзя объединить сам с собой")
        if target not in present:
            raise SpeakerError(f"в записи нет спикера «{target}»")
        if target in path:
            raise SpeakerError("объединения идут по кругу")
        finals[label] = merged(target, path + (label,))
        return finals[label]

    for label in order:
        merged(label, ())
    normal = []
    for op in ops:
        label = op["label"]
        item = {"type": op["type"], "label": label, "from": label, "to": finals[label]}
        if op["type"] == "merge":
            item["into"] = op["to"]
        normal.append(item)
    return finals, normal


def _deltas(segments: list[dict], targets: list[str | None]) -> list[dict]:
    """Какие реплики с какой подписи на какую меняются (сгруппированно)."""
    groups: dict[tuple, list[int]] = {}
    for i, (s, to) in enumerate(zip(segments, targets)):
        if to is not None and s.get("speaker") != to:
            groups.setdefault((s.get("speaker"), to), []).append(i)
    return [{"from": a, "to": b, "idx": idx} for (a, b), idx in groups.items()]


def _names_after(data: dict, finals: dict, sidecar: dict | None) -> dict:
    names = dict(data.get("names") or {}) if isinstance(data.get("names"), dict) else {}
    displays = [e["display"] for e in (sidecar or {}).get("speakers") or []
                if isinstance(e, dict) and isinstance(e.get("display"), str)]
    resolved = {d: _resolve(names, d) for d in displays}
    out = {k: finals.get(v, v) for k, v in names.items() if isinstance(v, str)}
    for d, cur in resolved.items():
        new = finals.get(cur, cur)
        if new != d:
            out[d] = new
        else:
            out.pop(d, None)
    return {k: v for k, v in out.items() if k != v}


def _enroll(folder: Path, sidecar: dict | None, person: str, entries: list[dict],
            voices_dir: Path) -> list[dict]:
    out = []
    for entry in entries:
        got = voices.enroll_sample(
            person, entry["embedding"], source=str((sidecar or {}).get("source") or folder),
            date=str((sidecar or {}).get("date") or ""), folder=voices_dir,
            label=str(entry.get("label") or entry["display"]), recording=folder.name)
        out.append({"person": got["person"], "sample_id": got["sample_id"],
                    "label": str(entry.get("label") or entry["display"]),
                    "created": got["created"], "replaced": got["replaced"]})
    return out


NO_VOICE = ("голос не сохранён: у записи нет голосовых отпечатков — "
            "перерасшифруйте её с разделением на спикеров")


def _set_names(data: dict, names: dict | None) -> None:
    if names:
        data["names"] = dict(names)
    else:
        data.pop("names", None)


def apply(folder: Path, ops: list, remember: dict | None, voices_dir: Path,
          now: datetime | None = None) -> dict:
    """Применить набор правок одним шагом истории. Всё проверяется до записи:
    отказ не оставляет транскрипт наполовину переименованным."""
    data = _transcript(folder)
    segments = data["segments"]
    shown = _shown(segments)
    order = _order(shown)
    sidecar = _sidecar(folder)
    clusters = _clusters(data, sidecar, set(order))
    finals, normal = _finals(ops, order, clusters)
    remember = remember if isinstance(remember, dict) else {}
    wanted = [label for label in order if remember.get(label) is True and not unnamed(finals[label])]
    targets = [finals[label] if label else None for label in shown]
    deltas = _deltas(segments, targets)
    if not deltas and not wanted:
        raise SpeakerError("нечего применять — имена уже такие")

    names_before = data.get("names") if isinstance(data.get("names"), dict) else None
    names_after = _names_after(data, finals, sidecar)
    for d in deltas:
        for i in d["idx"]:
            segments[i]["speaker"] = d["to"]
    _set_names(data, names_after)
    library.write_transcript(folder, data)

    enrolled: list[dict] = []
    errors: list[str] = []
    for label in wanted:
        entries = clusters.get(label, [])
        if not entries:
            errors.append(f"«{finals[label]}»: {NO_VOICE}")
            continue
        try:
            enrolled += _enroll(folder, sidecar, finals[label], entries, voices_dir)
        except (OSError, ValueError) as e:
            errors.append(f"«{finals[label]}»: голос не сохранён ({e})")
    step = {
        "id": uuid.uuid4().hex[:12],
        "at": (now or datetime.now()).isoformat(timespec="seconds"),
        "ops": normal,
        "enrolled": enrolled,
        "created_people": sorted({e["person"] for e in enrolled if e["created"]}),
        "segments": deltas,
        "count": len(segments),
        "names_before": names_before,
        "names_after": names_after or None,
    }

    def record(meta: dict) -> dict:
        steps, pos = _history_of(meta, data)
        steps = steps[:pos] + [step]
        return {**meta, HISTORY: steps, POS: len(steps), BASE: data.get("created_at")}

    meta = library.update_meta(folder, record)
    return {"step": _public([step])[0], "history": _public(meta[HISTORY]), "pos": meta[POS],
            "voices_error": "; ".join(errors) or None}


# --- отмена и повтор ----------------------------------------------------------

STALE = ("Расшифровку изменили после этого шага (переименовали человека в базе голосов "
         "или перерасшифровали запись) — отменить его уже нельзя")


def _check(segments: list[dict], step: dict, side: str) -> None:
    """Реплики стоят там, где их оставил (side="to") или застал (side="from") шаг."""
    if step.get("count") != len(segments):
        raise Stale(STALE)
    for d in step.get("segments") or []:
        for i in d.get("idx") or []:
            if not (0 <= i < len(segments)) or segments[i].get("speaker") != d.get(side):
                raise Stale(STALE)


def _move(folder: Path, data: dict, step: dict, forward: bool) -> None:
    side_from, side_to = ("from", "to") if forward else ("to", "from")
    segments = data["segments"]
    _check(segments, step, side_from)
    for d in step.get("segments") or []:
        for i in d.get("idx") or []:
            segments[i]["speaker"] = d.get(side_to)
    _set_names(data, step.get("names_after") if forward else step.get("names_before"))
    library.write_transcript(folder, data)


def _in_library(root: Path, name: str) -> bool:
    for folder in library.recording_folders(root):
        for s in (library.read_transcript(folder) or {}).get("segments") or []:
            if isinstance(s, dict) and s.get("speaker") == name:
                return True
    return False


def _unenroll(folder: Path, step: dict, voices_dir: Path) -> None:
    """Убрать образцы шага (вернув вытесненные) и людей, которых он создал и у
    которых больше ничего нет: ни образцов, ни встреч, ни аватара."""
    for e in reversed(step.get("enrolled") or []):
        left = voices.remove_sample(e["sample_id"], e["person"], voices_dir, e.get("replaced"))
        if not e.get("created"):
            continue
        for person, count in left.items():
            if count == 0 and not people.avatar_path(person, voices_dir).exists() \
                    and not _in_library(folder.parent, person):
                (voices_dir / f"{person}.json").unlink(missing_ok=True)


def _reenroll(folder: Path, step: dict, voices_dir: Path) -> str | None:
    sidecar = _sidecar(folder)
    by_label = {str(e.get("label") or e.get("display")): e
                for e in (sidecar or {}).get("speakers") or [] if isinstance(e, dict)}
    fresh, errors = [], []
    for e in step.get("enrolled") or []:
        entry = by_label.get(e.get("label"))
        if entry is None or not isinstance(entry.get("embedding"), list):
            errors.append(f"«{e['person']}»: {NO_VOICE}")
            continue
        try:
            fresh += _enroll(folder, sidecar, e["person"], [entry], voices_dir)
        except (OSError, ValueError) as err:
            errors.append(f"«{e['person']}»: голос не сохранён ({err})")
    step["enrolled"] = fresh
    step["created_people"] = sorted({e["person"] for e in fresh if e["created"]})
    return "; ".join(errors) or None


def _save(folder: Path, data: dict, steps: list[dict], pos: int) -> None:
    library.update_meta(folder, lambda meta: {**meta, HISTORY: steps, POS: pos,
                                              BASE: data.get("created_at")})


def undo(folder: Path, voices_dir: Path) -> dict:
    data = _transcript(folder)
    steps, pos = _history(folder, data)
    if pos == 0:
        raise SpeakerError("отменять нечего")
    step = steps[pos - 1]
    _move(folder, data, step, forward=False)
    _unenroll(folder, step, voices_dir)
    _save(folder, data, steps, pos - 1)
    return {"history": _public(steps), "pos": pos - 1, "voices_error": None}


def redo(folder: Path, voices_dir: Path) -> dict:
    data = _transcript(folder)
    steps, pos = _history(folder, data)
    if pos >= len(steps):
        raise SpeakerError("повторять нечего")
    step = steps[pos]
    _move(folder, data, step, forward=True)
    error = _reenroll(folder, step, voices_dir)
    _save(folder, data, steps, pos + 1)
    return {"history": _public(steps), "pos": pos + 1, "voices_error": error}


def revert(folder: Path, to_step_id: str | None, voices_dir: Path) -> dict:
    """К состоянию сразу после шага `to_step_id` (None — до всех правок):
    отменяя или повторяя шаги по одному."""
    data = _transcript(folder)
    steps, pos = _history(folder, data)
    if to_step_id in (None, "", "start"):
        target = 0
    else:
        ids = [s.get("id") for s in steps]
        if to_step_id not in ids:
            raise SpeakerError("такого шага в истории нет")
        target = ids.index(to_step_id) + 1
    result = {"history": _public(steps), "pos": pos, "voices_error": None}
    errors = []
    while pos != target:
        result = undo(folder, voices_dir) if pos > target else redo(folder, voices_dir)
        pos = result["pos"]
        if result.get("voices_error"):
            errors.append(result["voices_error"])
    result["voices_error"] = "; ".join(errors) or None
    return result
