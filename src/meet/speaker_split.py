"""«Разделить спикера»: диаризация слила двух (или больше) людей в одного —
развести его реплики по голосу, не перерасшифровывая запись.

Голос каждой реплики спикера (segvoices: эмбеддинг сегмента, кэш в папке
записи) считает задача `speaker_split`; дальше всё мгновенно, в резиденте:

- «Автоматически на K голосов» — агломеративная кластеризация по косинусу
  (средняя связь) до K групп и несколько проходов уточнения к центрам групп;
- «По образцам из базы» — каждая реплика к ближайшему из выбранных людей;
  слабое или неоднозначное сходство — в группу «не уверен» для ручной проверки.

Реплики короче MIN_SECONDS (и те, голос которых модель не посчитала) берут
группу ближайшей по времени реплики того же спикера. Предпросмотр показывает
группы (время, фразы с ▶, похожие люди из базы), человек называет их, и
применение — один шаг истории встречи (speakers): подписи реплик, а в
сайдкаре голосов смешанный голос спикера заменяется голосами групп — по ним
работают подсказки и «Запомнить голос»."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import numpy as np

from meet import library, segvoices, speakers, voices

K_MIN, K_MAX = 2, 5
# Сходство реплики с образцами человека (по одной реплике — шумнее, чем по
# кластеру целиком, поэтому ниже порога узнавания кластера): ниже — «не уверен».
UNSURE_MIN = 0.45
# Отрыв лучшего человека от второго: меньше — «не уверен».
UNSURE_MARGIN = 0.04
REFINE_ROUNDS = 10
SAMPLES = 3


# --- вспомогательное ------------------------------------------------------------


def _own(segments: list[dict], label: str) -> list[int]:
    return [i for i, s in enumerate(segments)
            if s.get("kind") != "break" and s.get("speaker") == label]


def fingerprint(segments: list[dict], idx: list[int]) -> str:
    """Отпечаток реплик спикера: применение сверяет, что разделяем то, что
    видели в предпросмотре."""
    h = hashlib.sha1()
    for i in idx:
        s = segments[i]
        h.update(f"{i}|{s.get('start')}|{s.get('end')}|{s.get('speaker')}\n".encode("utf-8"))
    h.update(str(len(segments)).encode())
    return h.hexdigest()[:16]


def _dur(s: dict) -> float:
    return segvoices._duration(s)


def _unit(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(n == 0, 1.0, n)


def _load(folder: Path, label: str, data: dict | None = None) -> tuple[dict, list[dict], list[int]]:
    if data is None:
        data = speakers._transcript(folder)
        # Дорожки старой записи звонка — в памяти, как их видела задача голосов.
        segvoices.mark_tracks(folder, data, speakers._owners())
    segments = data["segments"]
    own = _own(segments, label)
    if not own:
        raise speakers.SpeakerError(f"в записи нет спикера «{label}»")
    return data, segments, own


def _voices(folder: Path, segments: list[dict], own: list[int]) -> tuple[list[int], np.ndarray]:
    """Реплики спикера с посчитанным голосом и сами векторы (нормированные)."""
    cache = segvoices.read_cache(folder)
    have, vecs = [], []
    for i, _track, key in segvoices.needed(folder, segments, own):
        vec = cache.get(key)
        if vec is not None:
            have.append(i)
            vecs.append(vec)
    if not vecs:
        return [], np.zeros((0, 0), dtype=np.float32)
    dims = {v.shape for v in vecs}
    if len(dims) > 1:  # образцы разных моделей не сравнить
        shape = max(dims, key=lambda d: sum(v.shape == d for v in vecs))
        pairs = [(i, v) for i, v in zip(have, vecs) if v.shape == shape]
        have, vecs = [p[0] for p in pairs], [p[1] for p in pairs]
    return have, _unit(np.stack(vecs).astype(np.float32))


def status(folder: Path, label: str) -> dict:
    """Сколько реплик у спикера, сколько из них с голосом и скольким голос ещё
    не посчитан (тогда нужна задача speaker_split)."""
    _data, segments, own = _load(folder, label)
    voiced = segvoices.needed(folder, segments, own)
    todo = segvoices.missing(folder, segments, own)
    return {"label": label, "segments": len(own), "voiced": len(voiced), "missing": len(todo),
            "ready": not todo, "fingerprint": fingerprint(segments, own)}


# --- группы ---------------------------------------------------------------------


def _ahc(x: np.ndarray, k: int) -> np.ndarray:
    """Агломеративная кластеризация, средняя связь по косинусу, до k групп.
    Для нормированных векторов средний косинус двух групп = (Σa·Σb)/(|a||b|):
    хватает сумм векторов групп, без пересчёта всех пар."""
    n = len(x)
    sums = x.astype(np.float64).copy()
    sizes = np.ones(n)
    alive = np.ones(n, dtype=bool)
    members = [[i] for i in range(n)]
    sim = sums @ sums.T
    np.fill_diagonal(sim, -np.inf)
    for _ in range(n - k):
        a, b = np.unravel_index(int(np.argmax(sim)), sim.shape)
        a, b = min(a, b), max(a, b)
        sums[a] += sums[b]
        sizes[a] += sizes[b]
        members[a] += members[b]
        alive[b] = False
        sim[b, :] = -np.inf
        sim[:, b] = -np.inf
        row = (sums @ sums[a]) / (sizes * sizes[a])
        row[~alive] = -np.inf
        row[a] = -np.inf
        sim[a, :] = row
        sim[:, a] = row
    out = np.zeros(n, dtype=int)
    for g, i in enumerate(np.flatnonzero(alive)):
        out[members[i]] = g
    return out


def _refine(x: np.ndarray, w: np.ndarray, assign: np.ndarray, k: int) -> tuple[np.ndarray, float]:
    """Уточнение к центрам групп (сферический k-means с весом — длительностью
    реплики). → разбиение и взвешенное среднее сходство с центром."""
    score = -1.0
    for _ in range(REFINE_ROUNDS):
        centers = np.zeros((k, x.shape[1]))
        for g in range(k):
            sel = assign == g
            if sel.any():
                centers[g] = (x[sel] * w[sel, None]).sum(0)
        centers = _unit(centers)
        sims = x @ centers.T
        new = sims.argmax(1)
        score = float((sims[np.arange(len(x)), new] * w).sum() / w.sum())
        if np.array_equal(new, assign):
            break
        assign = new
    return assign, score


def _plusplus(x: np.ndarray, w: np.ndarray, k: int, seed: int) -> np.ndarray:
    """Начальные центры k-means++ (детерминированно по seed)."""
    rng = np.random.default_rng(seed)
    first = int(rng.choice(len(x), p=w / w.sum()))
    centers = [x[first]]
    for _ in range(1, k):
        d = 1.0 - np.max(x @ np.stack(centers).T, axis=1)
        p = np.clip(d, 0, None) * w
        if p.sum() <= 0:
            break
        centers.append(x[int(rng.choice(len(x), p=p / p.sum()))])
    return (x @ np.stack(centers).T).argmax(1)


def cluster(x: np.ndarray, w: np.ndarray, k: int) -> np.ndarray:
    """K групп голосов: средняя связь + уточнение; из нескольких стартов
    (агломеративный и k-means++) — с лучшим сходством с центрами. Группы
    нумеруются по убыванию суммарной длительности."""
    k = max(1, min(k, len(x)))
    if k == 1:
        return np.zeros(len(x), dtype=int)
    w = np.asarray(w, dtype=np.float64)
    w = np.where(w > 0, w, 1e-3)
    starts = [_ahc(x, k)] + [_plusplus(x, w, k, seed) for seed in range(4)]
    best, best_score = None, -np.inf
    for start in starts:
        assign, score = _refine(x, w, start, k)
        if len(set(assign.tolist())) == k and score > best_score + 1e-9:
            best, best_score = assign, score
    if best is None:
        best = _ahc(x, k)
    talk = [(-w[best == g].sum(), g) for g in range(k)]
    order = {g: n for n, (_, g) in enumerate(sorted(talk))}
    return np.array([order[g] for g in best])


def by_people(x: np.ndarray, base: dict[str, list[np.ndarray]], names: list[str],
              floor: float = UNSURE_MIN, margin: float = UNSURE_MARGIN) -> tuple[np.ndarray, np.ndarray]:
    """Каждая реплика — ближайшему из `names` (максимум косинуса по его
    образцам); слабое или неоднозначное сходство — -1 («не уверен»).
    → (номер человека или -1, сходство с лучшим)."""
    scores = np.full((len(x), len(names)), -1.0)
    for j, name in enumerate(names):
        samples = [s for s in base.get(name) or [] if s.shape == x.shape[1:]]
        if samples:
            scores[:, j] = (x @ _unit(np.stack(samples)).T).max(1)
    order = np.argsort(-scores, axis=1)
    best = scores[np.arange(len(x)), order[:, 0]]
    second = scores[np.arange(len(x)), order[:, 1]] if len(names) > 1 else np.full(len(x), -1.0)
    sure = (best >= floor) & (best - second >= margin)
    return np.where(sure, order[:, 0], -1), best


def _inherit(segments: list[dict], own: list[int], assigned: dict[int, int]) -> dict[int, int]:
    """Реплики без голоса — группа ближайшей по времени реплики спикера с
    голосом (при равенстве — предыдущей)."""
    if not assigned:
        return {i: 0 for i in own}
    known = sorted(assigned)
    mids = np.array([(float(segments[i]["start"]) + float(segments[i]["end"])) / 2 for i in known])
    out = dict(assigned)
    for i in own:
        if i in out:
            continue
        mid = (float(segments[i]["start"]) + float(segments[i]["end"])) / 2
        j = int(np.argmin(np.abs(mids - mid) + (mids > mid) * 1e-6))
        out[i] = assigned[known[j]]
    return out


# --- предпросмотр ---------------------------------------------------------------


def _samples(segments: list[dict], idx: list[int]) -> list[dict]:
    clean = [i for i in idx if not segments[i].get("uncertain")] or idx
    clean = sorted(clean, key=lambda i: _dur(segments[i]), reverse=True)[:SAMPLES]
    return [{"start": round(float(segments[i]["start"]), 2), "end": round(float(segments[i]["end"]), 2),
             "text": str(segments[i].get("text") or "")[:speakers.SAMPLE_TEXT_MAX]}
            for i in sorted(clean, key=lambda i: float(segments[i]["start"]))]


def _turns(segments: list[dict], idx: list[int]) -> int:
    return speakers._turn_count(segments, idx)


def _centroid(vecs: list[np.ndarray], weights: list[float]) -> np.ndarray | None:
    if not vecs:
        return None
    w = np.asarray(weights, dtype=np.float64)
    return _unit((np.stack(vecs) * w[:, None]).sum(0)).astype(np.float32)


def _similar(groups: list[dict], segments: list[dict], vec_of: dict) -> float | None:
    """Наибольшее сходство голосов двух групп (косинус центров): высокое —
    скорее всего, это один человек и делить его незачем."""
    centers = [_centroid([vec_of[i] for i in g["idx"] if i in vec_of],
                         [_dur(segments[i]) for i in g["idx"] if i in vec_of]) for g in groups]
    centers = [c for c in centers if c is not None]
    if len(centers) < 2:
        return None
    m = np.stack(centers) @ np.stack(centers).T
    np.fill_diagonal(m, -1.0)
    return round(float(m.max()), 3)


def _group(segments, idx, total, vec_of, base, threshold) -> dict:
    seconds = sum(_dur(segments[i]) for i in idx)
    vecs = [vec_of[i] for i in idx if i in vec_of]
    center = _centroid(vecs, [_dur(segments[i]) for i in idx if i in vec_of])
    sugs = speakers._suggestions([{"embedding": center.tolist()}], base) if center is not None else []
    top = sugs[0] if sugs and sugs[0]["score"] >= threshold else None
    return {"idx": idx, "seconds": round(seconds, 2), "share": round(seconds / total, 4) if total else 0.0,
            "turns": _turns(segments, idx), "samples": _samples(segments, idx),
            "voiced": len(vecs), "suggestions": sugs, "name": top["name"] if top else None}


def preview(folder: Path, label: str, voices_dir: Path, *, mode: str = "auto", k: int = 2,
            people: list | None = None, threshold: float = voices.THRESHOLD) -> dict:
    """Группы для «Разделить спикера»: что получится, до применения."""
    _data, segments, own = _load(folder, label)
    have, x = _voices(folder, segments, own)
    if len(have) < 2:
        raise speakers.SpeakerError("Слишком мало реплик с голосом: разделять нечего")
    sidecar = speakers._sidecar(folder)
    base_all = speakers._base_without(voices_dir, folder.name, (sidecar or {}).get("source"))
    w = np.array([_dur(segments[i]) for i in have])
    vec_of = dict(zip(have, x))
    total = sum(_dur(segments[i]) for i in own)
    out = {"label": label, "mode": mode, "fingerprint": fingerprint(segments, own),
           "segments": len(own), "voiced": len(have), "groups": [], "unsure": None}
    if mode == "people":
        names = [str(n) for n in dict.fromkeys(people or []) if str(n).strip()]
        if len(names) < 2:
            raise speakers.SpeakerError("Выберите хотя бы двух людей из базы голосов")
        full = voices.load_voices(voices_dir)
        base = {}
        for name in names:
            # Образцы из этой же встречи (кластер, который и разделяем) не берём,
            # если у человека есть другие; иначе — какие есть.
            base[name] = base_all.get(name) or full.get(name) or []
            if not base[name]:
                raise speakers.SpeakerError(f"У «{name}» нет образцов голоса в базе")
        assign, _score = by_people(x, base, names)
        groups = {i: int(g) for i, g in zip(have, assign)}
        full_assign = _inherit(segments, own, groups)
        for j, name in enumerate(names):
            idx = [i for i in own if full_assign[i] == j]
            group = _group(segments, idx, total, vec_of, base_all, threshold)
            out["groups"].append({**group, "key": f"p{j}", "person": name, "name": name})
        unsure = [i for i in own if full_assign[i] == -1]
        if unsure:
            out["unsure"] = {**_group(segments, unsure, total, vec_of, base_all, threshold),
                             "key": "unsure", "name": None}
        out["similar"] = _similar(out["groups"], segments, vec_of)
        return out
    k = int(k) if isinstance(k, int) or str(k).isdigit() else 2
    if not K_MIN <= k <= K_MAX:
        raise speakers.SpeakerError(f"Число голосов — от {K_MIN} до {K_MAX}")
    k = min(k, len(have))
    assign = cluster(x, w, k)
    full_assign = _inherit(segments, own, {i: int(g) for i, g in zip(have, assign)})
    for g in range(k):
        idx = [i for i in own if full_assign[i] == g]
        if idx:
            out["groups"].append({**_group(segments, idx, total, vec_of, base_all, threshold), "key": f"g{g}"})
    out["similar"] = _similar(out["groups"], segments, vec_of)
    return out


# --- применение -----------------------------------------------------------------


def apply(folder: Path, label: str, groups, fp: str | None, voices_dir: Path, *,
          mode: str = "auto", now: datetime | None = None) -> dict:
    """Развести реплики спикера по группам одним шагом истории.

    groups: [{"idx": [...], "to": имя | подпись спикера встречи | None (новый
    «Спикер N»), "remember": bool}]; `to` == label — группа остаётся за ним.
    `fp` — отпечаток из предпросмотра: реплики с тех пор менялись — Stale."""
    data, segments, own = _load(folder, label, speakers.editable(folder))
    if fp is not None and fp != fingerprint(segments, own):
        raise speakers.Stale(speakers.STALE_VIEW)
    if not isinstance(groups, list) or not groups:
        raise speakers.SpeakerError("нечего применять")
    present = set(speakers._order(speakers._shown(segments)))
    side_path, side = speakers.sidecar_for_write(folder)
    used = speakers._used_labels(data, side)
    mine, seen = set(own), set()
    plan: list[tuple[list[int], str, bool]] = []
    for g in groups:
        if not isinstance(g, dict):
            raise speakers.SpeakerError("непонятная группа")
        idx = g.get("idx")
        if not isinstance(idx, list) or not idx or not all(isinstance(i, int) for i in idx):
            raise speakers.SpeakerError("пустая группа")
        if not set(idx) <= mine:
            raise speakers.Stale(speakers.STALE_VIEW)
        if seen & set(idx):
            raise speakers.SpeakerError("реплика попала в две группы")
        seen |= set(idx)
        to = g.get("to")
        if to is None:
            target = speakers.fresh_label(used)
        elif str(to).strip() == label:
            target = label
        else:
            target = speakers._target(to, data, side, present)
        used.add(target)
        plan.append((sorted(idx), target, bool(g.get("remember")) and not speakers.unnamed(target)))
    targets: list[str | None] = [None] * len(segments)
    for idx, target, _ in plan:
        for i in idx:
            targets[i] = target
    deltas = speakers._deltas(segments, targets)
    if not deltas:
        raise speakers.SpeakerError("нечего применять — все группы остаются за спикером")

    step = speakers._new_step([{"type": "split", "label": label, "mode": mode,
                                "into": list(dict.fromkeys(t for _, t, _ in plan))}],
                              deltas, len(segments), now)
    step["payload"] = True
    # Голос спикера был смешанным — его кластеры уходят, голоса групп приходят.
    names = data.get("names") if isinstance(data.get("names"), dict) else {}
    before = [e for e in side.get("speakers") or [] if isinstance(e, dict)]
    after = [e for e in before if not (isinstance(e.get("display"), str)
                                       and speakers._resolve(names, e["display"]) == label)]
    have, x = _voices(folder, segments, own)
    vec_of = dict(zip(have, x))
    entries: dict[str, dict] = {}
    for n, (idx, target, _remember) in enumerate(plan):
        center = _centroid([vec_of[i] for i in idx if i in vec_of], [_dur(segments[i]) for i in idx if i in vec_of])
        if center is None:
            continue
        entry = {"label": f"SPLIT_{step['id']}_{n + 1}", "display": target,
                 "embedding": [float(v) for v in center], "split_of": label}
        entries[f"{n}"] = entry
        after.append(entry)
    for d in deltas:
        for i in d["idx"]:
            segments[i]["speaker"] = d["to"]
    new_side = {**side, "speakers": after}
    wanted = [(target, entries[f"{n}"]) for n, (_idx, target, remember) in enumerate(plan)
              if remember and f"{n}" in entries]

    def enroll(meta: dict) -> dict:
        enrolled: list[dict] = []
        for target, entry in wanted:
            enrolled += speakers._enroll(folder, new_side, target, [entry], voices_dir)
        step["enrolled"] = enrolled
        step["created_people"] = sorted({e["person"] for e in enrolled if e["created"]})
        if not enrolled:
            return meta
        return library.update_meta(folder, lambda m: {**m, speakers.HISTORY: [
            step if isinstance(x, dict) and x.get("id") == step["id"] else x
            for x in m.get(speakers.HISTORY) or []]})

    _, meta = speakers._commit(
        folder, data, speakers._recorder(data, step), voices_dir, enroll,
        sidecar=(side_path, new_side),
        payload=(step["id"], {"sidecar": {"before": before, "after": after}}))
    steps, pos = speakers._history_of(meta, data)
    return {"step": speakers._public([step])[0], "history": speakers._public(steps), "pos": pos,
            "trimmed": speakers._trimmed(meta, data), "voices_error": None,
            "changed": sum(len(d["idx"]) for d in deltas)}
