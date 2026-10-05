"""Микрофон по голосам: владелец, люди рядом с ним в комнате и дубли.

Сейчас вся дорожка микрофона подписывается владельцем («Вы»). Но в микрофон
говорят и люди рядом — коллега за соседним столом, переговорная с колонками,
сосед со своим ноутбуком в том же звонке (его слова тогда приходят ещё и через
звонок). Здесь микрофон делится по голосам «лёгкой диаризацией», без pyannote:

1. окна голоса по словам ASR (1,5–3 с: разрыв на паузе или на 3 с; короткие
   хвосты — к соседнему окну, иначе окно «короткое», без голоса);
2. голос окна — эмбеддинг WeSpeaker (та же модель, что у базы голосов);
3. быстрый путь: почти все окна похожи на образец владельца — весь микрофон его;
4. иначе кластеры окон (средняя связь до порога, уточнение), мелкие — к
   ближайшему; роль кластера по образцу владельца (meet.owner_voice):
   `owner`, `room` (человек в комнате) или `unsure` (владелец с пометкой);
5. дубли соседа и эхо (meet.mic_dedupe) — по ролям окон;
6. человек в комнате получает подпись кластера sys с тем же голосом, имя из
   базы или «Спикер N» (SPEAKER_M…);
7. слова раздаются по меткам окон; короткие берут метку соседнего окна.

Без образца владельца, без эмбеддера или если образец не похож ни на один
крупный голос микрофона — поведение как раньше (весь микрофон — владелец),
дубли — только заметно тихие копии. Чистая функция `run`: встраивание в
пайплайн, шаг и артефакты на диске — задача T6.

Пороги — стартовые, их калибрует T0 (scripts/speakers_calib.py) на записях
с согласия владельца. См. .superpowers/sdd/v033/speakers-design.md, §2.3, §3.2."""

from __future__ import annotations

import wave
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from meet import mic_dedupe, owner_voice, speaker_split
from meet.asr import Segment, Word

RULE = 1
SAMPLE_RATE = 16000

# --- окна голоса ---
# Окно режется на паузе длиннее WIN_GAP и на длине WIN_MAX. Старт, калибрует T0.
WIN_GAP = 0.3
WIN_MAX = 3.0
# Хвост короче TAIL_MIN — к соседнему окну, если пауза меньше TAIL_JOIN_GAP. Старт (T0).
TAIL_MIN = 1.0
TAIL_JOIN_GAP = 0.5
# Короче — эмбеддинг неустойчив (как voice_id.MIN_SECONDS): окно «короткое».
EMBED_MIN = 1.0
# Короткое окно берёт метку ближайшего окна с голосом не дальше INHERIT_S. Старт (T0).
INHERIT_S = 1.5

# --- владелец (§2.3) ---
# Центроид кластера против образца: от T_OWN — владелец, ниже T_OTHER — точно
# не он. Между — владелец, если кластер «ближний» (медиана громкости не ниже
# доминирующего кластера минус NEAR_DB), иначе `unsure`. Старт (T0).
T_OWN = 0.72
T_OTHER = 0.55
NEAR_DB = 6.0
# Быстрый путь: от FAST_SHARE секунд окон со сходством от T_WIN_FAST — весь
# микрофон владельца. Старт (T0).
T_WIN_FAST = 0.60
FAST_SHARE = 0.95
# Кластер «человек в комнате» — только от MIN_DECIDE_S речи: меньше — `unsure`
# (владелец с пометкой). Старт (T0).
MIN_DECIDE_S = 10.0
# Владелец найден, если среди кластеров от OWNER_PRESENT_SHARE речи есть
# похожий на образец хотя бы на T_OTHER. Иначе — owner_not_found. Старт (T0).
OWNER_PRESENT_SHARE = 0.30

# --- кластеры ---
# Слияние групп окон — пока средняя близость не ниже AHC_STOP. Старт (T0).
AHC_STOP = 0.55
# Кластер короче SMALL_CLUSTER_S — в ближайший при близости от SMALL_MERGE_COS,
# иначе `unsure`. Старт (T0).
SMALL_CLUSTER_S = 4.0
SMALL_MERGE_COS = 0.4
# Человек в комнате с голосом кластера sys (сосед в том же звонке) — от SYS_LINK. Старт (T0).
SYS_LINK = 0.65

STATUS_OK = "ok"
STATUS_NO_PROFILE = "no_profile"
STATUS_NOT_FOUND = "owner_not_found"
STATUS_NO_TOKEN = "skipped_no_token"
STATUS_OFF = "off"
OWNER_LABEL = "OWNER"
REASONS = ("echo", "neighbour", "owner_leak")


@dataclass
class Window:
    """Окно голоса: слова (номер сегмента, номер слова) подряд одного куска речи."""
    start: float
    end: float
    keys: list[tuple[int, int]]
    short: bool = False
    emb: np.ndarray | None = None
    db: float | None = None
    role: str = mic_dedupe.OWNER
    label: str | None = None

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass
class MicResult:
    """mic — сегменты микрофона с подписями (track="mic"); sys — сегменты sys
    без утечек владельца; dropped — удалённые копии (mic_voices.json);
    sidecar — голоса микрофона для сайдкара; report — поле `mic_split`
    транскрипта; voices — содержимое mic_voices.json."""
    mic: list[Segment]
    sys: list[Segment]
    dropped: list[dict]
    sidecar: list[dict]
    report: dict
    voices: dict = field(default_factory=dict)


# --- слова и окна ---------------------------------------------------------------


def _words_of(seg: Segment) -> tuple[list[Word], bool]:
    """Слова сегмента; без пословных таймкодов — слова текста, равномерно по
    времени сегмента (False: времена выдуманы, в сегмент они не вернутся)."""
    if seg.words:
        return list(seg.words), True
    tokens = str(seg.text or "").split()
    if not tokens:
        return [], False
    step = max(0.0, float(seg.end) - float(seg.start)) / len(tokens)
    return [Word(seg.start + i * step, seg.start + (i + 1) * step, " " + t) for i, t in enumerate(tokens)], False


def windows(segs: list[Segment]) -> list[Window]:
    """Окна голоса по словам всех сегментов микрофона."""
    items = sorted(((w.start, w.end, (si, wi)) for si, seg in enumerate(segs) if seg.kind != "break"
                    for wi, w in enumerate(_words_of(seg)[0])), key=lambda x: (x[0], x[1]))
    raw: list[Window] = []
    for start, end, key in items:
        cur = raw[-1] if raw else None
        if cur is not None and start - cur.end <= WIN_GAP and end - cur.start <= WIN_MAX:
            cur.end = max(cur.end, end)
            cur.keys.append(key)
        else:
            raw.append(Window(start, end, [key]))
    out: list[Window] = []
    for win in raw:
        last = out[-1] if out else None
        if last is not None and win.start - last.end < TAIL_JOIN_GAP and (
                win.seconds < TAIL_MIN or last.seconds < TAIL_MIN):
            last.end = max(last.end, win.end)
            last.keys += win.keys
            continue
        out.append(win)
    for win in out:
        win.short = win.seconds < EMBED_MIN
    return out


# --- звук -----------------------------------------------------------------------


def _read(path: Path) -> np.ndarray:
    """WAV 16 кГц моно (как его пишет пайплайн) — напрямую, прочее — через ffmpeg."""
    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getframerate() == SAMPLE_RATE and wf.getnchannels() == 1 and wf.getsampwidth() == 2:
                return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    except (wave.Error, EOFError):
        pass
    from meet import segvoices

    return segvoices.decode(Path(path), SAMPLE_RATE)


def _frame_db(audio: np.ndarray) -> np.ndarray:
    from meet import segvoices

    return segvoices._frame_db(audio, SAMPLE_RATE)


def _span_db(db: np.ndarray, start: float, end: float) -> float | None:
    a = max(0, int(round(start / mic_dedupe.FRAME_S)))
    b = min(len(db), max(a + 1, int(round(end / mic_dedupe.FRAME_S))))
    return float(np.median(db[a:b])) if b > a else None


def _unit(x: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(x))
    return x / n if n else x


def _embed(wins: list[Window], audio: np.ndarray, embed) -> None:
    for win in wins:
        if win.short:
            continue
        a, b = int(win.start * SAMPLE_RATE), int(win.end * SAMPLE_RATE)
        clip = audio[max(0, a):max(0, b)].astype(np.float32) / 32768.0
        if clip.size < int(EMBED_MIN * SAMPLE_RATE):
            win.short = True
            continue
        vec = embed(clip)
        vec = None if vec is None else np.asarray(vec, dtype=np.float64)
        if vec is None or not vec.size or not np.isfinite(vec).all() or not np.linalg.norm(vec):
            win.short = True
            continue
        win.emb = _unit(vec)


def _centroid(wins: list[Window]) -> np.ndarray | None:
    use = [w for w in wins if w.emb is not None]
    if not use:
        return None
    return _unit((np.stack([w.emb for w in use]) * np.array([w.seconds for w in use])[:, None]).sum(0))


def _cos(a: np.ndarray, b) -> float:
    b = np.asarray(b, dtype=np.float64)
    if a is None or b.shape != a.shape or not np.linalg.norm(b):
        return -1.0
    return float(a @ _unit(b))


# --- роли -----------------------------------------------------------------------


def _groups(wins: list[Window]) -> list[list[Window]]:
    """Кластеры окон с голосом: средняя связь до AHC_STOP и уточнение к центрам;
    мелкие — в ближайший крупный при близости от SMALL_MERGE_COS. → группы
    (мелкие без пары — отдельно, их помечает вызывающий)."""
    x = np.stack([w.emb for w in wins])
    secs = np.array([w.seconds for w in wins])
    labels = speaker_split.ahc_threshold(x, secs, AHC_STOP)
    k = int(labels.max()) + 1
    if k > 1:
        labels, _ = speaker_split._refine(x, secs, labels, k)
    groups: dict[int, list[Window]] = {}
    for win, g in zip(wins, labels):
        groups.setdefault(int(g), []).append(win)
    found = list(groups.values())
    big = [g for g in found if sum(w.seconds for w in g) >= SMALL_CLUSTER_S] or found
    out = [list(g) for g in big]
    centers = [_centroid(g) for g in big]
    for g in found:
        if any(g is b for b in big):
            continue
        c = _centroid(g)
        sims = [_cos(c, other) for other in centers]
        j = int(np.argmax(sims))
        if sims[j] >= SMALL_MERGE_COS:
            out[j] += g
        else:
            out.append(g)
            centers.append(None)  # мелкая и ни на кого не похожа — `unsure`
    return out


def _roles(wins: list[Window], owner, device: str | None) -> tuple[str, bool, list[dict], list[list[Window]]]:
    """Роли окон с голосом по образцу владельца. → (статус, быстрый путь,
    кластеры для mic_voices.json, группы окон в том же порядке)."""
    voiced = [w for w in wins if w.emb is not None]
    if not voiced:
        return STATUS_OK, False, [], []
    secs = np.array([w.seconds for w in voiced])
    scores = np.array([owner_voice.score(w.emb, owner, device) for w in voiced])
    if secs[scores >= T_WIN_FAST].sum() >= FAST_SHARE * secs.sum():
        c = _centroid(voiced)
        return STATUS_OK, True, [{"id": None, "seconds": round(float(secs.sum()), 2),
                                  "owner_cos": round(owner_voice.score(c, owner, device), 3),
                                  "role": "owner", "label": None, "link": None}], [voiced]
    groups = _groups(voiced)
    total = float(secs.sum())
    info = []
    for g in groups:
        c = _centroid(g)
        seconds = sum(w.seconds for w in g)
        dbs = [w.db for w in g if w.db is not None]
        info.append({"seconds": seconds, "cos": owner_voice.score(c, owner, device),
                     "db": float(np.median(dbs)) if dbs else None,
                     "small": seconds < SMALL_CLUSTER_S and len(groups) > 1})
    if not any(i["seconds"] >= OWNER_PRESENT_SHARE * total and i["cos"] >= T_OTHER for i in info):
        return STATUS_NOT_FOUND, False, [], []
    dominant = max(info, key=lambda i: i["seconds"])
    clusters = []
    for g, i in zip(groups, info):
        near = (i["db"] is not None and dominant["db"] is not None and i["db"] >= dominant["db"] - NEAR_DB)
        if i["cos"] >= T_OWN:
            role = "owner"
        elif i["small"]:
            role = "unsure"
        elif i["cos"] < T_OTHER:
            role = "room" if i["seconds"] >= MIN_DECIDE_S else "unsure"
        else:
            role = "owner" if near else "unsure"
        for w in g:
            w.role = role
        clusters.append({"id": None, "seconds": round(i["seconds"], 2), "owner_cos": round(i["cos"], 3),
                         "role": role, "label": None, "link": None})
    return STATUS_OK, False, clusters, groups


def _name_room(center: np.ndarray, diar, names: dict, base: dict, threshold: float) -> tuple[str | None, dict]:
    """Подпись человека в комнате: кластер sys с тем же голосом, имя из базы
    или None (новый «Спикер N»)."""
    sys_emb = getattr(diar, "embeddings", None) if diar is not None and not getattr(diar, "skipped", None) else None
    if sys_emb:
        scored = sorted(((_cos(center, v), label) for label, v in sys_emb.items()), reverse=True)
        score, label = scored[0]
        if score >= SYS_LINK:
            return names.get(label, label), {"kind": "sys", "to": label, "score": round(score, 3)}
    if base:
        from meet import voices

        got = voices.match_speakers({"mic": center.astype(np.float32)}, base, threshold=threshold)
        if "mic" in got:
            score = max(_cos(center, s) for s in base[got["mic"]])
            return got["mic"], {"kind": "base", "to": got["mic"], "score": round(score, 3)}
    return None, {"kind": "new", "to": None, "score": None}


def _inherit(wins: list[Window]) -> None:
    """Короткие окна — роль и метка ближайшего окна с голосом не дальше
    INHERIT_S (при равенстве — предыдущего); иначе — владелец."""
    voiced = [w for w in wins if w.emb is not None]
    for win in wins:
        if win.emb is not None:
            continue
        best, gap = None, float("inf")
        for other in voiced:  # по времени: при равенстве остаётся предыдущее
            d = max(0.0, other.start - win.end, win.start - other.end)
            if d < gap:
                best, gap = other, d
        if best is not None and gap <= INHERIT_S:
            win.role, win.label = best.role, best.label
        else:
            win.role, win.label = mic_dedupe.OWNER, None


# --- сборка ---------------------------------------------------------------------


def _rebuild(segs: list[Segment], dropped: set, label_of=None, track: str | None = None) -> list[Segment]:
    """Сегменты без удалённых слов, разрезанные по меткам слов `label_of(key)
    -> (подпись, uncertain)`. Неизменный сегмент остаётся как был (текст, поля)."""
    out: list[Segment] = []
    for si, seg in enumerate(segs):
        if seg.kind == "break":
            out.append(seg)
            continue
        words, real = _words_of(seg)
        keep = [(wi, w) for wi, w in enumerate(words) if (si, wi) not in dropped]
        if not keep and words:
            continue
        runs: list[tuple[tuple, list[Word]]] = []
        for wi, w in keep:
            key = label_of((si, wi)) if label_of else (seg.speaker, seg.uncertain)
            if runs and runs[-1][0] == key:
                runs[-1][1].append(w)
            else:
                runs.append((key, [w]))
        if len(runs) <= 1 and len(keep) == len(words):
            if runs:
                speaker, uncertain = runs[0][0]
            else:  # сегмент без текста — подпись по умолчанию
                speaker, uncertain = label_of((si, -1)) if label_of else (seg.speaker, seg.uncertain)
            out.append(replace(seg, speaker=speaker, uncertain=bool(uncertain or seg.uncertain),
                               track=track if track is not None else seg.track))
            continue
        for (speaker, uncertain), run in runs:
            out.append(Segment(run[0].start, run[-1].end, "".join(w.text for w in run).strip(), speaker,
                               words=list(run) if real else [], uncertain=bool(uncertain or seg.uncertain),
                               kind=seg.kind, track=track if track is not None else seg.track))
    return out


def _toks(segs: list[Segment], track: str, role_of=None) -> list[mic_dedupe.Tok]:
    out = []
    for si, seg in enumerate(segs):
        if seg.kind == "break":
            continue
        for wi, w in enumerate(_words_of(seg)[0]):
            role = role_of((si, wi)) if role_of else mic_dedupe.OWNER
            out.append(mic_dedupe.Tok(key=(track, si, wi), seg=(track, si), start=float(w.start),
                                      end=float(w.end), text=w.text, role=role))
    return out


def run(mic_segs: list[Segment], sys_segs: list[Segment], mic_wav: Path, sys_wav: Path, diar, *,
        owner: list, base: dict, threshold: float, owner_label: str, embed=None, log=print,
        names: dict | None = None, device: str | None = None, speakers: bool = True,
        dedupe: bool = True) -> MicResult:
    """Микрофон по голосам и без дублей.

    mic_segs — распознанный микрофон (слова с таймкодами); sys_segs — sys
    после split_by_speaker (подписи); diar — диаризация sys (центроиды
    кластеров — для людей в комнате, которые есть и в звонке), names — её
    метки → имена из базы; owner — образцы владельца (owner_voice.load),
    device — микрофон записи; base — база голосов, threshold — порог узнавания.
    speakers/dedupe — настройки asr.mic_speakers / asr.mic_dedupe. `embed`
    (звук → вектор) подменяется в тестах; None — эмбеддер segvoices."""
    from meet.diarize import DIARIZATION_MODEL

    names = names or {}
    wins = windows(mic_segs)
    mic_audio = _read(mic_wav) if wins else np.zeros(0, dtype=np.int16)
    mic_db = _frame_db(mic_audio) if mic_audio.size else np.zeros(0, dtype=np.float32)
    for win in wins:
        win.db = _span_db(mic_db, win.start, win.end)
    status, fast, clusters, groups = STATUS_OK, False, [], []
    if not speakers:
        status = STATUS_OFF
    elif not owner:
        status = STATUS_NO_PROFILE
    elif wins:
        if embed is None:
            from meet import segvoices

            try:
                embed = segvoices.load_embedder()
            except Exception as e:
                log(f"микрофон: голоса не посчитать, разделения нет ({type(e).__name__}: {e})")
                status = STATUS_NO_TOKEN
        if embed is not None:
            _embed(wins, mic_audio, embed)
            status, fast, clusters, groups = _roles(wins, owner, device)
    split = status == STATUS_OK
    if not split:
        for win in wins:
            win.emb, win.role, win.label = None, mic_dedupe.OWNER, None

    # Подписи людей в комнате — по порядку первого появления.
    rooms = sorted((i for i, c in enumerate(clusters) if c["role"] == "room"),
                   key=lambda i: min(w.start for w in groups[i]))
    room_center: dict[str, np.ndarray] = {}
    for n, i in enumerate(rooms):
        center = _centroid(groups[i])
        name, link = _name_room(center, diar, names, base, threshold)
        label = name or f"SPEAKER_M{n}"
        clusters[i].update(id=f"M{n}", label=label, link=link)
        room_center[f"SPEAKER_M{n}"] = center
        for w in groups[i]:
            w.label = label
    counters = {"O": 0, "U": 0}
    for c in clusters:
        if c["id"] is None:
            prefix = "O" if c["role"] == "owner" else "U"
            c["id"], c["label"] = f"{prefix}{counters[prefix]}", owner_label
            counters[prefix] += 1
    _inherit(wins)

    word: dict[tuple[int, int], Window] = {k: w for w in wins for k in w.keys}

    def label_of(key):
        w = word.get(key)
        if w is None or w.role == mic_dedupe.OWNER:
            return owner_label, False
        if w.role == "unsure":
            return owner_label, True
        return w.label or owner_label, False

    def role_of(key):
        w = word.get(key)
        return w.role if w is not None else mic_dedupe.OWNER

    drops: list[mic_dedupe.Drop] = []
    lags = None
    if dedupe and wins and sys_segs:
        mic_toks, sys_toks = _toks(mic_segs, "mic", role_of), _toks(sys_segs, "sys")
        env = None
        try:
            sys_audio = _read(sys_wav)
            env = mic_dedupe.Envelope(mic_db, _frame_db(sys_audio), speech=[(t.start, t.end) for t in mic_toks])
        except (OSError, RuntimeError, ValueError, wave.Error) as e:
            log(f"микрофон: громкость дорожек не прочитать, дубли только по словам ({type(e).__name__})")
        drops, lags = mic_dedupe.find(mic_toks, sys_toks, owner_known=split, env=env)
    gone = {"mic": set(), "sys": set()}
    for d in drops:
        for w in d.words:
            gone[d.track].add(w.key[1:])
    mic_out = _rebuild(mic_segs, gone["mic"], label_of, track="mic")
    sys_out = _rebuild(sys_segs, gone["sys"]) if gone["sys"] else list(sys_segs)

    present = {s.speaker for s in mic_out}
    room_labels = {c["label"] for c in clusters if c["role"] == "room"} & present
    sidecar = []
    owner_center = _centroid([w for w in wins if w.role == mic_dedupe.OWNER and w.emb is not None]) if split else None
    if owner_center is not None:
        sidecar.append({"label": OWNER_LABEL, "display": owner_label,
                        "embedding": [float(v) for v in owner_center], "track": "mic", "owner": True})
    for c in clusters:
        if c["role"] == "room" and c["label"] in present:
            center = room_center[f"SPEAKER_{c['id']}"]
            sidecar.append({"label": f"SPEAKER_{c['id']}", "display": c["label"],
                            "embedding": [float(v) for v in center], "track": "mic"})
    dropped = [d.to_raw() for d in drops]
    counts = {r: sum(d.reason == r for d in drops) for r in REASONS}
    report = {"rule": RULE, "status": status,
              "owner_profile": owner_voice.best_source(owner_center, owner, device) if owner_center is not None
              else None,
              "room_speakers": len(room_labels), "dropped": counts}
    voices_file = {"version": 1, "rule": RULE, "model": DIARIZATION_MODEL, "status": status, "fast": fast,
                   "lag_s": lags.to_raw() if lags is not None else None, "clusters": clusters, "dropped": dropped}
    log(f"микрофон: окон {len(wins)}, голосов {len(clusters)}, людей в комнате {len(room_labels)}, "
        f"убрано дублей {len(drops)} ({status}{', быстрый путь' if fast else ''})")
    return MicResult(mic=mic_out, sys=sys_out, dropped=dropped, sidecar=sidecar, report=report, voices=voices_file)
