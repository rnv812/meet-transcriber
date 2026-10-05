"""«Найти по прошлым встречам»: голос владельца микрофона без записи образца —
по его же прошлым звонкам (задача `owner_voice --derive`, meet.job_worker).

Результат — только предложение (`owner_voice.save_derived`): карточка в
настройках «Звук» даёт его послушать, и образцом (`source="auto"`) оно
становится лишь после «Да, это я». Ничего не нашлось — честная причина
словами (`Outcome.reason`), без догадок.

Порядок (дизайн §2.2 п.3):

1. кандидаты — до MAX_MEETINGS последних записей звонка (есть и микрофон, и
   звук собеседников) от MIN_MEETING_S, не объединённые, с готовыми спикерами;
2. в каждой — участки речи владельца на микрофоне: прогоны слов (VAD, если
   у сегмента нет слов) с паузой внутри не больше SAMPLE_RUN_MAX_PAUSE, от
   RUN_MIN_S. Никогда не целые сегменты расшифровки: длинный сегмент бывает
   почти пустым (T0). Берутся только участки, где собеседники молчат хотя бы
   на SYS_QUIET_SHARE кадров: иначе в микрофон могло попасть их эхо;
3. голос участка — из кэша голосов реплик (`segment_voices.json`), если
   участок и есть весь сегмент, иначе эмбеддинг WeSpeaker; каждый вектор
   нормируется (у community-1 нормы не единичные, T0);
4. участки встречи — по кластерам; голос встречи — доминирующий кластер,
   если в нём от DOMINANT_SHARE речи и от DOMINANT_MIN_S;
5. голоса встреч согласуются: группа с попарным косинусом от GROUP_COS
   должна покрыть от GROUP_MIN_MEETINGS встреч и от GROUP_MIN_SHARE кандидатов;
6. найденный голос не должен совпадать ни с кем из базы голосов (а совпадение
   с уже сохранённым образцом владельца — «добавлять нечего»);
7. для прослушивания — SAMPLE_COUNT участков из разных встреч.

См. .superpowers/sdd/v033/speakers-design.md, §2.2 п.3, и t0-calibration.md."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from meet import library, owner_voice, segvoices, speaker_split

SAMPLE_RATE = 16000
# --- кандидаты ---
MAX_MEETINGS = 10
MIN_MEETING_S = 300.0
# --- участки речи (T0: участки 2–10 с против общего центроида — p10 0.78) ---
RUN_MAX_PAUSE = owner_voice.SAMPLE_RUN_MAX_PAUSE
RUN_MIN_S = owner_voice.SAMPLE_RUN_MIN_S
RUN_MAX_S = 10.0
# Собеседники молчат хотя бы на этой доле кадров участка.
SYS_QUIET_SHARE = 0.8
# Голос из кэша реплик — только если участок покрывает сегмент целиком (края
# не дальше CACHE_EDGE_S): голос сегмента — голос всего его звука.
CACHE_EDGE_S = 0.5
# Речи на встречу не больше: час разговора — сотни участков, а голосу
# встречи (от DOMINANT_MIN_S) хватает и части — участки берутся равномерно по
# встрече. T0: ~35 мс счёта на секунду звука при 4 потоках, то есть ~10 с на
# встречу и 2–4 мин на десять встреч вместе с декодированием дорожек.
MAX_SPEECH_S = 240.0
# --- голос встречи ---
# Остановка кластеризации — как у окон микрофона (mic_split.AHC_STOP, T0: при
# 0.35 владелец одним кластером, соседи — своими).
CLUSTER_STOP = 0.35
DOMINANT_SHARE = 0.6
DOMINANT_MIN_S = 60.0
# --- согласие встреч ---
GROUP_COS = 0.75
GROUP_MIN_MEETINGS = 3
GROUP_MIN_SHARE = 0.6
# Найденный голос совпадает с уже сохранённым образцом — от этого косинуса
# (тот же порог, что «кластер микрофона — владелец», mic_split.T_OWN).
ALREADY_COS = 0.75
# Ниже этого — найденный голос не похож на сохранённый образец (как
# mic_split.T_OTHER): предлагается с пометкой — возможно, это не вы или
# записанный образец неудачен.
CONFLICT_COS = 0.65
# --- примеры для прослушивания ---
SAMPLE_COUNT = 3
SAMPLE_MIN_S = 3.0

SUGGESTED = "suggested"
TOO_FEW = "too_few"
INCONSISTENT = "inconsistent"
ALREADY = "already"
IN_BASE = "in_base"


class EmbedderError(RuntimeError):
    """Эмбеддер не загрузился: это сбой задачи, а не «встреча не подошла»."""


class DeriveError(RuntimeError):
    """Ни одну встречу не удалось разобрать: сбой, а не «мало встреч»."""


@dataclass
class Run:
    """Участок речи владельца: время на дорожке микрофона и голос."""
    start: float
    end: float
    seg: tuple[float, float] | None = None
    emb: np.ndarray | None = None

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass
class MeetingVoice:
    name: str
    runs: list[Run] = field(default_factory=list)
    dominant: list[Run] = field(default_factory=list)
    centroid: np.ndarray | None = None
    seconds: float = 0.0
    share: float = 0.0

    @property
    def usable(self) -> bool:
        return self.centroid is not None


@dataclass
class Outcome:
    status: str
    reason: str | None
    suggestion: dict | None = None
    checked: int = 0
    used: int = 0
    found: int = 0
    # Чем вызван итог (окно по ним прячет устаревшую причину): sample_id —
    # совпавший образец (already), person — человек из базы (in_base),
    # newest — самая новая запись на момент поиска.
    extra: dict = field(default_factory=dict)

    def to_raw(self) -> dict:
        return {"status": self.status, "reason": self.reason, "checked": self.checked,
                "used": self.used, "found": self.found, **self.extra}


def _unit(x: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(x))
    return x / n if n else x


def _vector(vec) -> np.ndarray | None:
    if vec is None:
        return None
    vec = np.asarray(vec, dtype=np.float64)
    if vec.ndim != 1 or not vec.size or not np.isfinite(vec).all() or not np.linalg.norm(vec):
        return None
    return _unit(vec)


def _meetings_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "встреча"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "встречи"
    return "встреч"


# --- кандидаты ------------------------------------------------------------------


def _duration(folder: Path, data: dict) -> float:
    card = library.describe(folder)
    if card is not None and card.duration_s:
        return float(card.duration_s)
    ends = []
    for s in data.get("segments") or []:
        try:
            ends.append(float(s["end"]))
        except (KeyError, TypeError, ValueError):
            continue
    return max(ends, default=0.0)


def candidates(root: Path, limit: int = MAX_MEETINGS) -> list[Path]:
    """Последние записи звонка, годные для поиска: от новых к старым."""
    out = []
    for folder in reversed(library.recording_folders(Path(root))):
        if len(out) >= limit:
            break
        if not segvoices.is_call(folder):
            continue
        meta = library.read_meta(folder)
        if meta.get("merged_from") or meta.get("source") == "merge":
            continue
        data = library.final_transcript(folder)
        if not data or not isinstance(data.get("segments"), list):
            continue
        if _duration(folder, data) < MIN_MEETING_S:
            continue
        out.append(folder)
    return out


def newest_recording(root: Path) -> str | None:
    """Имя самой новой папки записи (по имени-дате, без чтения файлов: окно
    спрашивает часто). Нет записей — None."""
    try:
        names = [f.name for f in Path(root).iterdir()
                 if library.FOLDER_RE.match(f.name) and f.is_dir()]
    except OSError:
        return None
    return max(names, default=None)


# --- участки речи владельца --------------------------------------------------


def _even(a: float, b: float) -> list[tuple[float, float]]:
    """Кусок длиннее RUN_MAX_S (участок VAD без слов) — на равные части до RUN_MAX_S."""
    if b - a <= RUN_MAX_S:
        return [(a, b)]
    n = math.ceil((b - a) / RUN_MAX_S - 1e-9)
    step = (b - a) / n
    return [(a + i * step, a + (i + 1) * step) for i in range(n)]


def _cut(spans: list[tuple[float, float]], seg: tuple[float, float]) -> list[Run]:
    """Прогоны (пауза внутри до RUN_MAX_PAUSE) → участки от RUN_MIN_S и не
    длиннее RUN_MAX_S + RUN_MIN_S: длинный прогон режется по словам (кусок VAD
    — на равные части), короткий хвост — к предыдущему куску, только если тот
    не выйдет за эту границу; иначе хвост отбрасывается."""
    runs: list[list[tuple[float, float]]] = []
    for a, b in sorted(x for a0, b0 in spans for x in _even(a0, b0)):
        if runs and a - runs[-1][-1][1] <= RUN_MAX_PAUSE:
            runs[-1].append((a, b))
        else:
            runs.append([(a, b)])
    out: list[Run] = []
    for spans_of_run in runs:
        pieces: list[list[tuple[float, float]]] = [[]]
        for a, b in spans_of_run:
            cur = pieces[-1]
            if cur and b - cur[0][0] > RUN_MAX_S:
                pieces.append([(a, b)])
            else:
                cur.append((a, b))
        if (len(pieces) > 1 and pieces[-1][-1][1] - pieces[-1][0][0] < RUN_MIN_S
                and pieces[-1][-1][1] - pieces[-2][0][0] <= RUN_MAX_S + RUN_MIN_S):
            pieces[-2] += pieces.pop()
        for p in pieces:
            start, end = p[0][0], max(b for _, b in p)
            if end - start >= RUN_MIN_S:
                out.append(Run(round(start, 2), round(end, 2), seg))
    return out


def _word_spans(seg: dict) -> list[tuple[float, float]]:
    out = []
    for w in seg.get("words") or []:
        try:
            a, b = float(w[0]), float(w[1])
        except (IndexError, TypeError, ValueError):
            continue
        if b > a:
            out.append((a, b))
    return out


def owner_runs(data: dict, owner_labels: set[str], *, mic=None, vad=None) -> list[Run]:
    """Участки речи владельца на микрофоне (сегменты с `track == "mic"` и
    подписью владельца). Слова сегмента — по ним; нет слов — VAD по звуку
    сегмента (`mic()` → int16 16 кГц, `vad(float32, rate)` → [(a, b)] от начала
    куска); нет ни того, ни другого — сегмент пропускается."""
    out: list[Run] = []
    for seg in data.get("segments") or []:
        if not isinstance(seg, dict) or seg.get("kind") == "break":
            continue
        if seg.get("track") != "mic" or seg.get("speaker") not in owner_labels:
            continue
        try:
            span = (float(seg["start"]), float(seg["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        spans = _word_spans(seg)
        if not spans and mic is not None and vad is not None and span[1] - span[0] >= RUN_MIN_S:
            audio = mic()
            clip = audio[int(span[0] * SAMPLE_RATE):int(span[1] * SAMPLE_RATE)].astype(np.float32) / 32768.0
            if clip.size:
                spans = [(span[0] + float(a), span[0] + float(b)) for a, b in vad(clip, SAMPLE_RATE)
                         if float(b) > float(a)]
        out += _cut(spans, span)
    return out


def _quiet(runs: list[Run], sys_audio: np.ndarray, rate: int) -> list[Run]:
    """Участки, где собеседники молчат хотя бы на SYS_QUIET_SHARE кадров
    (кадр «звучит» — громче своего фона на segvoices.ACTIVE_DB)."""
    db = segvoices._frame_db(sys_audio, rate)
    if not db.size:
        return list(runs)
    loud = (db - segvoices._floor(db)) > segvoices.ACTIVE_DB
    out = []
    for r in runs:
        a, b = int(r.start / segvoices.FRAME_S), int(math.ceil(r.end / segvoices.FRAME_S))
        part = loud[a:b]
        share = float(part.sum()) / max(1, b - a)  # за концом дорожки — тишина
        if 1.0 - share >= SYS_QUIET_SHARE:
            out.append(r)
    return out


def _spread(runs: list[Run], limit_s: float = MAX_SPEECH_S) -> list[Run]:
    """Не больше limit_s речи — участки равномерно по встрече."""
    total = sum(r.seconds for r in runs)
    if total <= limit_s:
        return runs
    n = len(runs)
    k = max(1, int(n * limit_s / total))
    while True:
        idx = sorted(set(np.linspace(0, n - 1, k).round().astype(int).tolist()))
        pick = [runs[i] for i in idx]
        if k == 1 or sum(r.seconds for r in pick) <= limit_s:
            return pick
        k -= 1


# --- голос встречи ---------------------------------------------------------------


def _embed_runs(folder: Path, runs: list[Run], embed, mic) -> None:
    cache = segvoices.read_cache(folder)
    for r in runs:
        vec = None
        if r.seg is not None and r.start - r.seg[0] <= CACHE_EDGE_S and r.seg[1] - r.end <= CACHE_EDGE_S:
            vec = cache.get(segvoices.key("mic", {"start": r.seg[0], "end": r.seg[1]}))
        if vec is None:
            audio = mic()
            clip = audio[int(r.start * SAMPLE_RATE):int(r.end * SAMPLE_RATE)].astype(np.float32) / 32768.0
            vec = embed(clip) if clip.size else None
        r.emb = _vector(vec)


def _dominant(runs: list[Run]) -> list[Run]:
    if len(runs) == 1:
        return list(runs)
    x = np.stack([r.emb for r in runs])
    w = np.array([r.seconds for r in runs])
    labels = speaker_split.ahc_threshold(x, w, CLUSTER_STOP)
    k = int(labels.max()) + 1
    if k > 1:
        labels, _ = speaker_split._refine(x, w, labels, k)
    groups: dict[int, list[Run]] = {}
    for r, g in zip(runs, labels):
        groups.setdefault(int(g), []).append(r)
    return max(groups.values(), key=lambda g: sum(r.seconds for r in g))


def _centroid(runs: list[Run]) -> np.ndarray | None:
    use = [r for r in runs if r.emb is not None]
    if not use:
        return None
    return _unit((np.stack([r.emb for r in use]) * np.array([r.seconds for r in use])[:, None]).sum(0))


def meeting_voice(folder: Path, *, embed, decode, owner_labels: set[str], vad=None) -> MeetingVoice:
    """Голос владельца в одной встрече: доминирующий кластер его участков."""
    folder = Path(folder)
    out = MeetingVoice(folder.name)
    data = library.read_transcript_full(folder) or {}
    data = {**data, "segments": [dict(s) for s in data.get("segments") or [] if isinstance(s, dict)]}
    segvoices.mark_tracks(folder, data, owner_labels)  # старые записи: дорожка по звуку или подписи
    loaded: dict[str, np.ndarray] = {}

    def mic() -> np.ndarray:
        if "mic" not in loaded:
            loaded["mic"] = decode(library.find_track(folder, "mic"), SAMPLE_RATE)
        return loaded["mic"]

    runs = owner_runs(data, owner_labels, mic=mic, vad=vad)
    if not runs:
        return out
    sys_src = library.find_track(folder, "sys")
    runs = _quiet(runs, decode(sys_src, segvoices.ENERGY_RATE), segvoices.ENERGY_RATE)
    runs = _spread(runs)
    _embed_runs(folder, runs, embed, mic)
    out.runs = [r for r in runs if r.emb is not None]
    if not out.runs:
        return out
    total = sum(r.seconds for r in out.runs)
    dominant = _dominant(out.runs)
    out.dominant = dominant
    out.seconds = round(sum(r.seconds for r in dominant), 2)
    out.share = out.seconds / total if total else 0.0
    if out.share >= DOMINANT_SHARE and out.seconds >= DOMINANT_MIN_S:
        out.centroid = _centroid(dominant)
    return out


# --- по всем встречам --------------------------------------------------------------


def _group(found: list[MeetingVoice]) -> list[MeetingVoice]:
    """Самая большая группа голосов встреч с попарным косинусом от GROUP_COS
    (при равенстве — с большей речью). Встреч не больше MAX_MEETINGS — перебор."""
    n = len(found)
    if not n:
        return []
    sim = np.stack([m.centroid for m in found]) @ np.stack([m.centroid for m in found]).T
    for size in range(n, 0, -1):
        best, best_talk = None, -1.0
        for combo in itertools.combinations(range(n), size):
            if all(sim[i, j] >= GROUP_COS for i, j in itertools.combinations(combo, 2)):
                talk = sum(found[i].seconds for i in combo)
                if talk > best_talk:
                    best, best_talk = combo, talk
        if best is not None:
            return [found[i] for i in best]
    return []


def _samples(group: list[MeetingVoice], center: np.ndarray) -> list[dict]:
    """По участку из SAMPLE_COUNT разных встреч — самому похожему на найденный голос."""
    ranked = sorted(group, key=lambda m: -float(m.centroid @ center))
    out = []
    for m in ranked:
        runs = [r for r in m.dominant if r.emb is not None]
        fit = [r for r in runs if SAMPLE_MIN_S <= r.seconds <= RUN_MAX_S] or runs
        if not fit:
            continue
        best = max(fit, key=lambda r: float(r.emb @ center))
        out.append({"recording": m.name, "start": best.start, "end": best.end, "track": "mic"})
        if len(out) >= SAMPLE_COUNT:
            break
    return out


def _base_threshold() -> float:
    """Совпадение с человеком из базы — от порога узнавания (не выше
    voices.THRESHOLD: строже — честнее «не предлагаю»)."""
    from meet import settings, voices

    try:
        value = float(settings.load().asr.voice_threshold)
    except Exception:
        value = voices.THRESHOLD
    return min(value, voices.THRESHOLD)


def _load_embedder():
    from meet import owner_enroll

    return owner_enroll.load_embedder()


def _default_vad(audio: np.ndarray, rate: int):
    from meet import owner_enroll

    return owner_enroll._default_vad(audio, rate)


def _too_few(checked: int, used: int) -> str:
    if checked < GROUP_MIN_MEETINGS:
        return (f"Подходящих встреч пока {checked}, а нужно хотя бы {GROUP_MIN_MEETINGS}: "
                f"звонки от {round(MIN_MEETING_S / 60)} минут с расшифровкой. "
                "Запишите ещё несколько встреч или запишите образец голоса сами.")
    return (f"Ваш голос уверенно слышен только в {used} из {checked} {_meetings_word(checked)}, "
            f"а нужно хотя бы {GROUP_MIN_MEETINGS}: в остальных мало вашей речи в паузах собеседников "
            "или в микрофоне несколько голосов. Запишите образец голоса сами — так надёжнее.")


def find(root: Path, voices: Path, *, embed=None, vad=None, decode=None, bus=None,
         owner_labels: set[str] | None = None, threshold: float | None = None,
         limit: int = MAX_MEETINGS, log=print) -> Outcome:
    """Найти голос владельца по прошлым встречам. Ничего не пишет."""
    from meet import voices as voice_base

    folders = candidates(root, limit)
    checked = len(folders)
    if checked < GROUP_MIN_MEETINGS:
        return Outcome(TOO_FEW, _too_few(checked, 0), checked=checked,
                       extra={"newest": newest_recording(root)})
    labels = segvoices.owners() if owner_labels is None else owner_labels
    decode = decode or segvoices.decode
    vad = vad or _default_vad
    loaded = {}

    def lazy_embed(clip):
        if "embed" not in loaded:
            try:
                loaded["embed"] = embed or _load_embedder()
            except Exception as e:
                raise EmbedderError(f"модель голосов не загрузилась: {type(e).__name__}: {e}") from e
        return loaded["embed"](clip)

    found: list[MeetingVoice] = []
    failures: list[Exception] = []
    for i, folder in enumerate(folders):
        if bus is not None:
            bus.progress("owner_derive", label="поиск вашего голоса", done=i, total=checked, note=folder.name)
        try:
            voice = meeting_voice(folder, embed=lazy_embed, decode=decode, owner_labels=labels, vad=vad)
        except EmbedderError:
            raise
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as e:
            log(f"голос владельца: встреча {folder.name} пропущена ({type(e).__name__}: {e})")
            failures.append(e)
            continue
        if voice.usable:
            found.append(voice)
    if bus is not None:
        bus.progress("owner_derive", label="поиск вашего голоса", done=checked, total=checked)
    if failures and len(failures) == checked:
        first = failures[0]
        raise DeriveError(f"не удалось разобрать ни одной встречи: {type(first).__name__}: {first}") from first
    newest = {"newest": newest_recording(root)}
    used = len(found)
    if used < GROUP_MIN_MEETINGS:
        return Outcome(TOO_FEW, _too_few(checked, used), checked=checked, used=used, extra=newest)
    group = _group(found)
    # Доля — от встреч, где голос владельца вообще нашёлся: встреча, где он
    # почти молчал, не говорит о том, что голос «другой».
    need = max(GROUP_MIN_MEETINGS, math.ceil(GROUP_MIN_SHARE * used - 1e-9))
    counts = dict(checked=checked, used=used, found=len(group))
    if len(group) < need:
        if len(group) == used:  # не разнобой, а мало встреч с голосом
            return Outcome(TOO_FEW, _too_few(checked, used), **counts, extra=newest)
        return Outcome(INCONSISTENT,
                       f"Голос в вашем микрофоне от встречи к встрече звучит по-разному: одинаково — "
                       f"только в {len(group)} из {used} {_meetings_word(used)}, где он хорошо слышен, "
                       f"а нужно хотя бы {need}. Возможно, микрофоном пользуются и другие люди. "
                       "Запишите образец голоса сами — так надёжнее.", **counts, extra=newest)
    talk = np.array([m.seconds for m in group])
    center = _unit((np.stack([m.centroid for m in group]) * talk[:, None]).sum(0))
    sim = [float(a.centroid @ b.centroid) for a, b in itertools.combinations(group, 2)]
    own = owner_voice.load(voices)
    own_score = owner_voice.score(center, own) if own else None
    if own_score is not None and own_score >= ALREADY_COS:
        closest = max(own, key=lambda s: float(center @ _unit(s.embedding.astype(np.float64))))
        return Outcome(ALREADY, "Найденный голос совпадает с уже сохранённым образцом вашего голоса — "
                                "добавлять нечего.", **counts, extra={**newest, "sample_id": closest.id})
    base = voice_base.load_voices(Path(voices))
    if base:
        score, name, _ = voice_base.best_match(center.astype(np.float32), base)
        if score >= (threshold if threshold is not None else _base_threshold()):
            return Outcome(IN_BASE, f"Чаще всего в вашем микрофоне звучит голос, похожий на «{name}» из "
                                    "базы голосов, — поэтому не предлагаю его как ваш. Если это вы, "
                                    "запишите образец голоса сами.", **counts, extra={**newest, "person": name})
    suggestion = {"embedding": center.astype(np.float32), "meetings": [m.name for m in group],
                  "samples": _samples(group, center), "seconds": round(float(talk.sum()), 2),
                  "quality": round(min(sim), 4) if sim else None,
                  # Есть образец, а найденный голос на него не похож: окно предупредит.
                  "conflict": own_score is not None and own_score < CONFLICT_COS}
    return Outcome(SUGGESTED, None, suggestion, **counts, extra=newest)


def run(root: Path, voices: Path, **kw) -> Outcome:
    """Найти и сохранить итог: найденный голос — предложением (образцом он
    станет после «Да, это я»), иначе — причина; прежнее предложение снимается."""
    outcome = find(root, voices, **kw)
    owner_voice.save_derived(outcome.to_raw(), outcome.suggestion, voices=voices)
    return outcome
