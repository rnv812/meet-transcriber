"""Спикеры одной встречи для панели «Спикеры» карточки: кто сколько говорил,
подсказки по базе голосов, набор правок одним шагом и откат шагов.

Транскрипт хранит у реплики готовую подпись спикера («Спикер 2», «Анна»), а
голос кластера лежит в сайдкаре расшифровки под исходной подписью (`display`).
Связь между ними — `names` транскрипта (подпись сайдкара → нынешнее имя, бывает
цепочкой: так его вели и прежние «Назвать спикеров»). Каждая правка из панели
держит `names` в согласии с репликами — по нему строка панели находит свой
голос, даже если её переименовали или объединили с другой.

История — в meta.json записи: `speaker_history` (шаги, не больше HISTORY_MAX),
`speaker_history_pos` (сколько из них применено), `speaker_history_base`
(`created_at` транскрипта, к которому они относятся: перерасшифровка начинает
историю заново) и `speaker_history_trimmed` (самые старые шаги уже отброшены).
Шаг хранит ровно то, что нужно для отката: какие реплики с какой подписи на
какую сменил, какие записи `names` поменял (только их — переименование других
людей в базе голосов после шага откат не затирает) и какие образцы голоса
записал в базу (по id — их убирают точно, вместе с людьми, которых шаг создал и
у которых больше ничего нет). Перед откатом и повтором шаг сверяется с
транскриптом: если реплики с тех пор поменяли (переименовали человека в базе
голосов, правили вручную), откат честно отказывает, а не портит расшифровку.

Порядок записи — транскрипт и meta.json, затем база голосов. Сбой базы голосов
возвращает и транскрипт, и историю, и сами файлы голосов к прежнему виду
(VoiceBaseError): правка применяется целиком или никак."""

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
TRIMMED = "speaker_history_trimmed"
# Сколько шагов помнить: старше — отбрасываются, отменить их уже нельзя.
HISTORY_MAX = 50
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


class VoiceBaseError(RuntimeError):
    """База голосов не записалась; правка откатана целиком (409)."""


def unnamed(label: str) -> bool:
    return bool(_UNNAMED.match(label or ""))


# --- чтение -------------------------------------------------------------------


def _transcript(folder: Path) -> dict:
    data = library.read_transcript(folder)
    if not data or not isinstance(data.get("segments"), list):
        raise SpeakerError("у записи нет расшифровки")
    if not all(isinstance(s, dict) for s in data["segments"]):
        # Переписав такой транскрипт, мы бы молча потеряли непонятные куски.
        raise SpeakerError("расшифровка повреждена — перерасшифруйте запись")
    return data


def normalize(folder: Path) -> bool:
    """Старые транскрипты хранят сырые SPEAKER_XX: один раз переписать их в
    «Спикер N» (как их и показывает окно) — без шага истории, это не правка
    человека. Иначе первая же правка «меняла» бы все реплики. True — переписан."""
    data = library.read_transcript(folder)
    segments = (data or {}).get("segments")
    if not isinstance(segments, list) or not all(isinstance(s, dict) for s in segments):
        return False
    raw = library.display_names(segments)
    if not raw:
        return False
    for s in segments:
        if s.get("speaker") in raw:
            s["speaker"] = raw[s["speaker"]]
    library.write_transcript(folder, data)
    return True


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


def _base_without(voices_dir: Path, recording: str, source: str | None) -> dict[str, list[np.ndarray]]:
    """База голосов без образцов из этой же встречи: голос, запомненный отсюда,
    совпал бы сам с собой на 100% и подсказывал бы уже выбранное имя."""
    out: dict[str, list[np.ndarray]] = {}
    if not voices_dir.is_dir():
        return out
    for f in sorted(voices_dir.glob("*.json")):
        try:
            samples = voices._read_samples(f)
        except (OSError, ValueError):
            continue
        own = [np.asarray(x["embedding"], dtype=np.float32) for x in samples
               if isinstance(x.get("embedding"), list)
               and x.get("recording") != recording and (source is None or x.get("source") != source)]
        if own:
            out[f.stem] = own
    return out


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


def _trimmed(meta: dict, data: dict) -> bool:
    return meta.get(BASE) == data.get("created_at") and bool(meta.get(TRIMMED))


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
    sidecar = _sidecar(folder)
    clusters = _clusters(data, sidecar, set(order))
    base = _base_without(voices_dir, folder.name, (sidecar or {}).get("source")) if clusters else {}
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
    meta = library.read_meta(folder)
    steps, pos = _history_of(meta, data)
    return {"speakers": rows, "owner": owner, "history": _public(steps), "pos": pos,
            "trimmed": _trimmed(meta, data)}


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


def _names_diff(before: dict | None, after: dict | None) -> dict[str, list]:
    """Какие записи `names` шаг поменял: ключ → [было, стало] (None — не было)."""
    before, after = before or {}, after or {}
    return {k: [before.get(k), after.get(k)] for k in sorted(set(before) | set(after))
            if before.get(k) != after.get(k)}


def _step_names(step: dict) -> dict[str, list]:
    if isinstance(step.get("names"), dict):
        return step["names"]
    return _names_diff(step.get("names_before"), step.get("names_after"))  # шаги до P8-fix


def _move_names(data: dict, diff: dict[str, list], forward: bool) -> None:
    """Сдвинуть только записи шага; ту, что с тех пор поменяли (человека
    переименовали в базе голосов), не трогаем."""
    names = dict(data.get("names") or {}) if isinstance(data.get("names"), dict) else {}
    for key, (was, now) in diff.items():
        expect, put = (was, now) if forward else (now, was)
        if names.get(key) != expect:
            continue
        if put is None:
            names.pop(key, None)
        else:
            names[key] = put
    _set_names(data, names)


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


def _no_voice(name: str) -> str:
    return (f"Голос «{name}» не сохранён: у записи нет голосовых отпечатков — "
            "перерасшифруйте её с разделением на спикеров")


def _deleted(name: str) -> str:
    return f"Голос «{name}» удалён в базе — образцы не восстановлены"


# --- запись: транскрипт и история, затем база голосов, сбой — откат всего -----


def _voice_snapshot(voices_dir: Path) -> dict[str, bytes]:
    """Файлы голосов как есть: база маленькая (векторы), а откат по снимку не
    зависит от того, какие файлы успела тронуть операция."""
    if not voices_dir.is_dir():
        return {}
    return {f.name: f.read_bytes() for f in voices_dir.glob("*.json")}


def _voice_restore(voices_dir: Path, snap: dict[str, bytes]) -> None:
    try:
        for f in voices_dir.glob("*.json"):
            if f.name not in snap:
                f.unlink(missing_ok=True)
        for name, data in snap.items():
            f = voices_dir / name
            if not f.exists() or f.read_bytes() != data:
                f.write_bytes(data)
    except OSError:
        pass  # сообщим о первой ошибке; вторая — тот же сбой диска


def _meta_keys(meta: dict) -> dict:
    return {k: meta[k] for k in (HISTORY, POS, BASE, TRIMMED) if k in meta}


def _put_meta_keys(saved: dict):
    def change(meta: dict) -> dict:
        out = {k: v for k, v in meta.items() if k not in (HISTORY, POS, BASE, TRIMMED)}
        return {**out, **saved}
    return change


def _commit(folder: Path, data: dict, meta_change, voices_dir: Path, voice_ops) -> tuple[dict, object]:
    """Транскрипт и meta.json, затем база голосов (`voice_ops() -> что угодно`).
    Сбой где угодно — всё назад: транскрипт, история, файлы голосов."""
    path = library.transcript_path(folder)
    before = path.read_bytes()
    saved = _meta_keys(library.read_meta(folder))
    library.write_transcript(folder, data)
    try:
        meta = library.update_meta(folder, meta_change)
    except Exception:
        _put_back(path, before)
        raise
    snap = _voice_snapshot(voices_dir)
    try:
        result = voice_ops(meta)
    except (OSError, ValueError) as e:
        _voice_restore(voices_dir, snap)
        _put_back(path, before)
        try:
            library.update_meta(folder, _put_meta_keys(saved))
        except Exception:
            pass
        raise VoiceBaseError(f"Не удалось изменить базу голосов ({e}) — изменение не применено")
    return meta, result


def _put_back(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(".json.restore.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)


def _set_names(data: dict, names: dict | None) -> None:
    if names:
        data["names"] = dict(names)
    else:
        data.pop("names", None)


def apply(folder: Path, ops: list, remember: dict | None, voices_dir: Path,
          now: datetime | None = None) -> dict:
    """Применить набор правок одним шагом истории. Всё проверяется до записи:
    отказ не оставляет транскрипт наполовину переименованным."""
    normalize(folder)
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

    errors = [_no_voice(finals[label]) for label in wanted if not clusters.get(label)]
    step = {
        "id": uuid.uuid4().hex[:12],
        "at": (now or datetime.now()).isoformat(timespec="seconds"),
        "ops": normal,
        "enrolled": [],
        "created_people": [],
        "segments": deltas,
        "count": len(segments),
        "names": _names_diff(names_before, names_after),
    }

    def record(meta: dict) -> dict:
        steps, pos = _history_of(meta, data)
        steps = steps[:pos] + [step]
        trimmed = _trimmed(meta, data)
        if len(steps) > HISTORY_MAX:
            steps, trimmed = steps[-HISTORY_MAX:], True
        return {**meta, HISTORY: steps, POS: len(steps), BASE: data.get("created_at"), TRIMMED: trimmed}

    def enroll(meta: dict) -> dict:
        enrolled: list[dict] = []
        for label in wanted:
            if clusters.get(label):
                enrolled += _enroll(folder, sidecar, finals[label], clusters[label], voices_dir)
        step["enrolled"] = enrolled
        step["created_people"] = sorted({e["person"] for e in enrolled if e["created"]})
        if not enrolled:
            return meta
        return library.update_meta(folder, lambda m: {**m, HISTORY: [
            step if isinstance(x, dict) and x.get("id") == step["id"] else x for x in m.get(HISTORY) or []]})

    _, meta = _commit(folder, data, record, voices_dir, enroll)
    steps, pos = _history_of(meta, data)
    return {"step": _public([step])[0], "history": _public(steps), "pos": pos,
            "trimmed": _trimmed(meta, data), "voices_error": "; ".join(errors) or None,
            "changed": sum(len(d["idx"]) for d in deltas)}


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


def _move(data: dict, step: dict, forward: bool) -> None:
    side_from, side_to = ("from", "to") if forward else ("to", "from")
    segments = data["segments"]
    _check(segments, step, side_from)
    for d in step.get("segments") or []:
        for i in d.get("idx") or []:
            segments[i]["speaker"] = d.get(side_to)
    _move_names(data, _step_names(step), forward)


def _in_library(root: Path, name: str) -> bool:
    for folder in library.recording_folders(root):
        for s in (library.read_transcript(folder) or {}).get("segments") or []:
            if isinstance(s, dict) and s.get("speaker") == name:
                return True
    return False


def _unenroll(folder: Path, step: dict, voices_dir: Path) -> list[str]:
    """Убрать образцы шага (вернув вытесненные) и людей, которых он создал и у
    которых больше ничего нет: ни образцов, ни встреч, ни аватара. Человека,
    удалённого с тех пор в базе голосов, не воскрешаем — только сообщаем."""
    notes: list[str] = []
    for e in reversed(step.get("enrolled") or []):
        got = voices.remove_sample(e["sample_id"], e["person"], voices_dir, e.get("replaced"))
        notes += [_deleted(name) for name in got["skipped"]]
        if not e.get("created"):
            continue
        for person, count in got["left"].items():
            if count == 0 and not people.avatar_path(person, voices_dir).exists() \
                    and not _in_library(folder.parent, person):
                (voices_dir / f"{person}.json").unlink(missing_ok=True)
    return list(dict.fromkeys(notes))


def _reenroll(folder: Path, step: dict, voices_dir: Path) -> list[str]:
    """Повтор шага: голоса снова из сайдкара (новые id). Человека, которого
    шаг не создавал и которого с тех пор удалили в базе, не воскрешаем."""
    sidecar = _sidecar(folder)
    by_label = {str(e.get("label") or e.get("display")): e
                for e in (sidecar or {}).get("speakers") or [] if isinstance(e, dict)}
    fresh, notes = [], []
    for e in step.get("enrolled") or []:
        entry = by_label.get(e.get("label"))
        if entry is None or not isinstance(entry.get("embedding"), list):
            notes.append(_no_voice(e["person"]))
            continue
        if not e.get("created") and not (voices_dir / f"{e['person']}.json").exists():
            notes.append(f"Голос «{e['person']}» удалён в базе — образец не записан")
            continue
        fresh += _enroll(folder, sidecar, e["person"], [entry], voices_dir)
    step["enrolled"] = fresh
    step["created_people"] = sorted({e["person"] for e in fresh if e["created"]})
    return list(dict.fromkeys(notes))


def _shift(folder: Path, voices_dir: Path, forward: bool) -> dict:
    data = _transcript(folder)
    meta = library.read_meta(folder)
    steps, pos = _history_of(meta, data)
    if forward and pos >= len(steps):
        raise SpeakerError("повторять нечего")
    if not forward and pos == 0:
        raise SpeakerError("отменять нечего")
    step = steps[pos] if forward else steps[pos - 1]
    _move(data, step, forward)
    new_pos = pos + 1 if forward else pos - 1

    def move_pos(m: dict) -> dict:
        return {**m, POS: new_pos}

    def voice_ops(m: dict) -> list[str]:
        if not forward:
            return _unenroll(folder, step, voices_dir)
        notes = _reenroll(folder, step, voices_dir)
        library.update_meta(folder, lambda x: {**x, HISTORY: [
            step if isinstance(s, dict) and s.get("id") == step["id"] else s for s in x.get(HISTORY) or []]})
        return notes

    _, notes = _commit(folder, data, move_pos, voices_dir, voice_ops)
    meta = library.read_meta(folder)
    steps, pos = _history_of(meta, data)
    return {"history": _public(steps), "pos": pos, "trimmed": _trimmed(meta, data),
            "voices_error": "; ".join(notes) or None}


def undo(folder: Path, voices_dir: Path) -> dict:
    return _shift(folder, voices_dir, forward=False)


def redo(folder: Path, voices_dir: Path) -> dict:
    return _shift(folder, voices_dir, forward=True)


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
    result = {"history": _public(steps), "pos": pos, "voices_error": None,
              "trimmed": _trimmed(library.read_meta(folder), data)}
    errors = []
    while pos != target:
        result = undo(folder, voices_dir) if pos > target else redo(folder, voices_dir)
        pos = result["pos"]
        if result.get("voices_error"):
            errors.append(result["voices_error"])
    result["voices_error"] = "; ".join(errors) or None
    return result
