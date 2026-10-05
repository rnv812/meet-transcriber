"""Живые голоса: онлайн-кластеры по дорожкам и имена задним числом (С3).

Раньше живой режим решал имя по одному сегменту ASR (1–3 с) против образцов
базы, с порогом-константой 0.75: так имя почти не получалось — короткий клип
против центроида целой встречи даёт cos 0.55–0.66 (калибровка T0). Теперь:

1. сегмент от EMBED_MIN_S → эмбеддинг (та же модель, что у базы голосов) →
   ближайший онлайн-кластер дорожки при cos от LIVE_ASSIGN и отрыве от
   второго LIVE_MARGIN, иначе новый кластер (только с сегмента от NEW_MIN_S;
   короче — кластер предыдущего сегмента при паузе до INHERIT_GAP_S);
   сегмент короче EMBED_MIN_S — кластер предыдущего при паузе до
   SHORT_INHERIT_GAP_S;
2. центроид — нормированная сумма эмбеддингов с весом длительности; раз в
   MERGE_EVERY сегментов кластеры от LIVE_MERGE_MIN_S речи сливаются при cos
   от LIVE_MERGE; не больше MAX_CLUSTERS кластеров на дорожку;
3. имя — по накопленному центроиду: от LIVE_MIN_SPEECH_S речи, cos от
   T_live (`mic_split.live_threshold(asr.voice_threshold)`), отрыв, тот же
   кандидат на LIVE_CHECKS проверках подряд (каждые CHECK_STEP_S речи), имя
   не занято другим кластером дорожки (оба уверены — кластеры сливаются).
   Имя закрепляется; снимается, только если после удвоения речи сходство с
   ним упало ниже T_live − UNPIN_DROP;
4. микрофон (только при образце владельца): кластер от ROOM_MIN_S речи с
   центроидом ниже T_OTHER на двух проверках подряд — человек в комнате
   (`room`), его строки переподписываются задним числом; обратно во владельца —
   от T_OWN.

Строки ленты несут ключ голоса (`sys:3`, `mic:1`), а подпись ключа может
смениться позже: `drain()` отдаёт переименования «ключ → подпись», живой
режим правит по ним ленту и файл (см. meet.live, meet.assist.bus).

Пороги — из калибровки T0 (.superpowers/sdd/v033/t0-calibration.md, §3),
константы — в meet.mic_split (LIVE_*). Все векторы нормируются: центроиды
community-1 в базе голосов не единичной длины (норма 0.9–2.9).
См. .superpowers/sdd/v033/speakers-design.md, §4.1–4.4."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from meet import mic_split, owner_voice

SAMPLE_RATE = 16000
# Короче — эмбеддинг неустойчив (как voice_id.MIN_SECONDS): кластер соседа.
EMBED_MIN_S = 1.0
# Новый кластер — только с сегмента от NEW_MIN_S: голос 1–2 с слишком шумный,
# чтобы заводить по нему человека.
NEW_MIN_S = 2.0
# Сегмент с голосом, не похожий ни на один кластер, но короче NEW_MIN_S, —
# кластер предыдущего сегмента при паузе до INHERIT_GAP_S; сегмент без голоса
# (короче EMBED_MIN_S) — при паузе до SHORT_INHERIT_GAP_S.
INHERIT_GAP_S = 1.0
SHORT_INHERIT_GAP_S = 1.5
# Слияние кластеров — раз в MERGE_EVERY сегментов с голосом.
MERGE_EVERY = 10
MAX_CLUSTERS = 12
# Проверка имени и роли — каждые CHECK_STEP_S новой речи кластера.
CHECK_STEP_S = 3.0
# Закреплённое имя снимается, если после удвоения речи сходство с ним упало
# ниже T_live − UNPIN_DROP (T0: после 15 с own падал не больше чем на 0.05).
UNPIN_DROP = 0.08
# Роль кластера микрофона решается от ROOM_MIN_S речи (дизайн §4.4).
ROOM_MIN_S = 6.0
# Сколько последних сегментов дорожки помнить для «кластер предыдущего».
RECENT = 16
# Сегмент может начаться чуть раньше конца предыдущего (границы слов ASR).
OVERLAP_S = 0.05

OWNER = "owner"
ROOM = "room"
UNSURE = "unsure"
OTHER = "other"  # дорожка собеседников: роли владельца нет
# Подпись человека в комнате, которого нет в базе голосов.
ROOM_SPEAKER = "Собеседник рядом"


def _unit(x: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(x))
    return x / n if n else x


def _vector(value) -> np.ndarray | None:
    """Эмбеддинг → единичный float64; пустой, нулевой или с NaN — None."""
    if value is None:
        return None
    vec = np.asarray(value, dtype=np.float64).ravel()
    if not vec.size or not np.isfinite(vec).all() or not float(np.linalg.norm(vec)):
        return None
    return _unit(vec)


@dataclass
class _Cluster:
    n: int
    total: np.ndarray  # сумма единичных эмбеддингов с весом длительности
    seconds: float = 0.0
    into: int | None = None  # слит в кластер с этим номером
    # Имя из базы: закреплённое, кандидат и сколько проверок подряд он держится.
    name: str | None = None
    named_s: float = 0.0
    candidate: str | None = None
    streak: int = 0
    checked_s: float = 0.0
    # Роль кластера микрофона (owner | room) и такая же «две проверки подряд».
    role: str = OWNER
    room_streak: int = 0
    role_checked_s: float = 0.0

    @property
    def center(self) -> np.ndarray:
        return _unit(self.total)

    def add(self, emb: np.ndarray, seconds: float) -> None:
        self.total = self.total + emb * seconds
        self.seconds += seconds


class TrackVoices:
    """Онлайн-кластеры голосов одной дорожки. `observe` → ключ кластера
    (`"sys:3"`) или None (голоса нет и соседнего сегмента тоже)."""

    def __init__(self, track: str, *, assign: float = mic_split.LIVE_ASSIGN,
                 margin: float = mic_split.LIVE_MARGIN, merge: float = mic_split.LIVE_MERGE,
                 merge_min_s: float = mic_split.LIVE_MERGE_MIN_S,
                 max_clusters: int = MAX_CLUSTERS) -> None:
        self.track = track
        self.assign_at = assign
        self.margin = margin
        self.merge_at = merge
        self.merge_min_s = merge_min_s
        self.max_clusters = max_clusters
        self.clusters: dict[int, _Cluster] = {}  # и слитые (ключи строк живут)
        self._recent: deque = deque(maxlen=RECENT)
        self._voiced = 0
        self._next = 0

    def key(self, n: int) -> str:
        return f"{self.track}:{n}"

    def number(self, key: str) -> int | None:
        head, _, tail = key.partition(":")
        if head != self.track or not tail.isdigit():
            return None
        return int(tail)

    def resolve(self, n: int) -> _Cluster:
        """Кластер, в который в итоге слит кластер `n` (сам он, если не слит)."""
        c = self.clusters[n]
        while c.into is not None:
            c = self.clusters[c.into]
        return c

    def live(self) -> list[_Cluster]:
        return [c for c in self.clusters.values() if c.into is None]

    def observe(self, emb: np.ndarray | None, start: float, end: float) -> str | None:
        seconds = max(0.0, float(end) - float(start))
        if emb is None:
            n = self._previous(start, SHORT_INHERIT_GAP_S)
        else:
            n = self._place(emb, seconds, start)
            self._voiced += 1
            if self._voiced % MERGE_EVERY == 0:
                self.merge_close()
        if n is None:
            return None
        self._recent.append((float(start), float(end), n))
        return self.key(n)

    def _place(self, emb: np.ndarray, seconds: float, start: float) -> int | None:
        scored = sorted(((float(c.center @ emb), c.n) for c in self.live()), reverse=True)
        best, best_n = scored[0] if scored else (-1.0, None)
        second = scored[1][0] if len(scored) > 1 else -1.0
        if best_n is not None and best >= self.assign_at:
            if best - second >= self.margin:
                self.clusters[best_n].add(emb, seconds)
            # Похож на два кластера сразу — к лучшему, но центроиды не трогаем.
            return best_n
        if seconds >= NEW_MIN_S:
            if len(scored) < self.max_clusters:
                return self._new(emb, seconds)
            if best_n is not None:  # потолок кластеров — к ближайшему
                self.clusters[best_n].add(emb, seconds)
                return best_n
        return self._previous(start, INHERIT_GAP_S)

    def _new(self, emb: np.ndarray, seconds: float) -> int:
        n = self._next
        self._next += 1
        self.clusters[n] = _Cluster(n, emb * seconds, seconds)
        return n

    def _previous(self, start: float, gap: float) -> int | None:
        """Кластер сегмента, кончившегося прямо перед `start` (пауза до `gap`).
        Окна догонялки идут вперемешку с живыми — ищем по времени, а не
        просто последний."""
        best = None
        for a, b, n in self._recent:
            if b <= start + OVERLAP_S and start - b <= gap and (best is None or b > best[0]):
                best = (b, n)
        return None if best is None else self.resolve(best[1]).n

    def merge(self, keep: _Cluster, gone: _Cluster) -> _Cluster:
        """Слить `gone` в `keep`: больший остаётся, имя переходит к нему."""
        if gone.seconds > keep.seconds:
            keep, gone = gone, keep
        keep.total = keep.total + gone.total
        keep.seconds += gone.seconds
        if keep.name is None and gone.name is not None:
            keep.name, keep.named_s = gone.name, gone.named_s
        keep.role_checked_s = 0.0  # роль пересчитает ближайшая проверка по новому центроиду
        gone.into = keep.n
        return keep

    def merge_close(self) -> int:
        """Слить кластеры от merge_min_s речи с cos от merge_at. → сколько слито."""
        done = 0
        while True:
            big = [c for c in self.live() if c.seconds >= self.merge_min_s]
            pairs = [(float(a.center @ b.center), a, b)
                     for i, a in enumerate(big) for b in big[i + 1:]]
            pairs = [p for p in pairs if p[0] >= self.merge_at
                     and not (p[1].name and p[2].name and p[1].name != p[2].name)]
            if not pairs:
                return done
            _, a, b = max(pairs, key=lambda p: p[0])
            self.merge(a, b)
            done += 1


class Assigned(NamedTuple):
    voice: str | None  # ключ голоса строки; None — подпись по умолчанию навсегда
    speaker: str
    role: str  # owner | room | unsure (микрофон) или other (собеседники)


class LiveVoices:
    """Голоса живого режима по дорожкам `sys`/`mic`.

    `embed(clip) -> вектор` — эмбеддер (в тестах — фальшивый), `base` — база
    голосов {имя: [векторы]}, `owner` — образцы владельца
    (meet.owner_voice), `name_threshold` — T_live. `defaults` — подпись
    дорожки без имени («Собеседник», имя владельца). Без базы собеседники не
    кластеризуются (называть некем), без образца владельца или с `mic=False`
    — микрофон."""

    def __init__(self, embed, base, owner, *, name_threshold: float,
                 defaults: dict | None = None, mic: bool = True, device: str | None = None,
                 log=print, clock=time.perf_counter) -> None:
        self._embed = embed
        self._base = {}
        for name, samples in (base or {}).items():
            vecs = [v for v in (_vector(s) for s in samples) if v is not None]
            if vecs:
                self._base[name] = vecs
        self._owner = list(owner or [])
        self.threshold = float(name_threshold)
        self.margin = mic_split.LIVE_MARGIN
        self.defaults = {"sys": "Собеседник", "mic": "Вы", **(defaults or {})}
        self._mic = bool(mic) and bool(self._owner)
        self._device = device
        self._log = log
        self._clock = clock
        self._tracks: dict[str, TrackVoices] = {}
        self._shown: dict[str, str] = {}  # подпись ключа в уже выданных строках
        self._embed_error: str | None = None
        self.stats = {"embeds": 0, "embed_s": 0.0, "times": []}

    def active(self, track: str) -> bool:
        return self._mic if track == "mic" else bool(self._base)

    def _default(self, track: str) -> str:
        return self.defaults.get(track, self.defaults["sys"])

    def _plain(self, track: str) -> Assigned:
        return Assigned(None, self._default(track), OWNER if track == "mic" else OTHER)

    def assign(self, track: str, clip: np.ndarray, start: float, end: float) -> Assigned:
        """Голос сегмента `clip` (16 кГц) дорожки `track` → ключ, подпись на
        сейчас и роль (для дублей микрофона)."""
        if not self.active(track):
            return self._plain(track)
        emb = self._embedding(clip) if len(clip) >= EMBED_MIN_S * SAMPLE_RATE else None
        tv = self._tracks.get(track)
        if tv is None:
            tv = self._tracks[track] = TrackVoices(track)
        key = tv.observe(emb, start, end)
        if key is None:
            return self._plain(track)
        self._review(track, tv)
        speaker = self.speaker(key)
        self._shown.setdefault(key, speaker)
        return Assigned(key, speaker, self._role(track, tv, key, emb))

    def _embedding(self, clip: np.ndarray) -> np.ndarray | None:
        began = self._clock()
        try:
            vec = _vector(self._embed(np.asarray(clip, dtype=np.float32)))
        except Exception as e:  # эмбеддер не должен валить окно
            text = f"{type(e).__name__}: {e}"
            if text != self._embed_error:
                self._embed_error = text
                self._log(f"голоса: эмбеддинг не посчитан ({text})")
            return None
        spent = self._clock() - began
        self.stats["embeds"] += 1
        self.stats["embed_s"] += spent
        self.stats["times"].append(round(spent, 4))
        return vec

    # --- подписи ---

    def _cluster(self, key: str) -> tuple[TrackVoices, _Cluster] | None:
        track = key.partition(":")[0]
        tv = self._tracks.get(track)
        n = tv.number(key) if tv is not None else None
        if n is None or n not in tv.clusters:
            return None
        return tv, tv.resolve(n)

    def speaker(self, key: str) -> str:
        """Подпись голоса на сейчас."""
        track = key.partition(":")[0]
        found = self._cluster(key)
        if found is None:
            return self._default(track)
        _, c = found
        if track == "mic":
            return (c.name or ROOM_SPEAKER) if c.role == ROOM else self._default(track)
        return c.name or self._default(track)

    def speakers(self) -> dict[str, str]:
        """Все выданные голоса → подпись на сейчас."""
        return {key: self.speaker(key) for key in self._shown}

    def drain(self) -> list[tuple[str, str]]:
        """Переименования с прошлого вызова: [(ключ, новая подпись)]."""
        out = []
        for key, shown in self._shown.items():
            now = self.speaker(key)
            if now != shown:
                self._shown[key] = now
                out.append((key, now))
        return out

    def role(self, key: str | None) -> str:
        """Роль голоса на сейчас (без сегмента): owner | room | unsure | other."""
        if key is None:
            return OWNER
        track = key.partition(":")[0]
        found = self._cluster(key)
        if track != "mic":
            return OTHER
        if found is None:
            return OWNER
        tv, c = found
        return self._role(track, tv, key, None)

    def _role(self, track: str, tv: TrackVoices, key: str, emb: np.ndarray | None) -> str:
        if track != "mic":
            return OTHER
        c = tv.resolve(tv.number(key))
        if c.role == ROOM:
            return ROOM
        if c.seconds >= ROOM_MIN_S and c.role_checked_s:
            return OWNER  # роль кластера уже проверялась — владелец
        # Кластер ещё не решён: по самому сегменту (порог окон — низкий, T0),
        # без голоса — по центроиду кластера. Строки владельца не задерживаются.
        vec = emb if emb is not None else c.center
        return OWNER if owner_voice.score(vec, self._owner, self._device) >= mic_split.T_WIN_FAST \
            else UNSURE

    # --- проверки кластеров ---

    def _review(self, track: str, tv: TrackVoices) -> None:
        for c in tv.live():
            if c.into is not None:
                continue  # слит на этом же обходе
            if track == "mic":
                self._check_role(c)
                if c.role != ROOM:
                    continue
            self._check_name(tv, c)

    def _check_role(self, c: _Cluster) -> None:
        if c.seconds < ROOM_MIN_S or c.seconds - c.role_checked_s < CHECK_STEP_S:
            return
        c.role_checked_s = c.seconds
        score = owner_voice.score(c.center, self._owner, self._device)
        if c.role == ROOM:
            if score >= mic_split.T_OWN:
                c.role, c.room_streak = OWNER, 0
                self._log(f"голоса: голос микрофона — снова владелец (cos {score:.2f})")
            return
        c.room_streak = c.room_streak + 1 if score < mic_split.T_OTHER else 0
        if c.room_streak >= mic_split.LIVE_CHECKS:
            c.role = ROOM
            self._log(f"голоса: в микрофоне человек рядом (cos с образцом {score:.2f}, "
                      f"{c.seconds:.0f} с речи)")

    def _score_of(self, center: np.ndarray, name: str) -> float:
        return max((float(center @ v) for v in self._base.get(name, ())), default=-1.0)

    def _check_name(self, tv: TrackVoices, c: _Cluster) -> None:
        if not self._base or c.seconds < mic_split.LIVE_MIN_SPEECH_S:
            return
        if c.name is not None:
            if c.seconds < 2 * c.named_s:
                return
            score = self._score_of(c.center, c.name)
            if score < self.threshold - UNPIN_DROP:
                self._log(f"голоса: live-имя снято (cos {score:.2f})")
                c.name, c.candidate, c.streak = None, None, 0
                c.checked_s = c.seconds
            else:
                c.named_s = c.seconds  # следующая проверка — после нового удвоения
            return
        if c.seconds - c.checked_s < CHECK_STEP_S:
            return
        c.checked_s = c.seconds
        center = c.center
        scores = sorted(((self._score_of(center, name), name) for name in self._base), reverse=True)
        best, name = scores[0]
        second = scores[1][0] if len(scores) > 1 else None
        sure = best >= self.threshold and (second is None or best - second >= self.margin)
        candidate = name if sure else None
        if candidate is not None and candidate == c.candidate:
            c.streak += 1
        else:
            c.candidate, c.streak = candidate, 1 if candidate else 0
        if c.streak < mic_split.LIVE_CHECKS:
            return
        holder = next((o for o in tv.live() if o is not c and o.name == candidate), None)
        if holder is not None:
            # Имя уже у другого кластера дорожки, и этот в нём тоже уверен — один человек.
            kept = tv.merge(holder, c)
            kept.name = candidate
            return
        c.name, c.named_s = candidate, c.seconds
        self._log(f"голоса: live {candidate} (cos {best:.2f}, {c.seconds:.0f} с речи)")

    def stats_line(self) -> str | None:
        """Итог эмбеддингов живого режима для журнала (без имён)."""
        st = self.stats
        if not st["embeds"]:
            return None
        times = sorted(st["times"])
        p90 = times[min(len(times) - 1, int(len(times) * 0.9))]
        clusters = {t: len(tv.live()) for t, tv in self._tracks.items()}
        named = sum(1 for tv in self._tracks.values() for c in tv.live() if c.name)
        rooms = sum(1 for c in self._tracks["mic"].live() if c.role == ROOM) \
            if "mic" in self._tracks else 0
        return (f"голоса живого режима: эмбеддингов {st['embeds']}, {st['embed_s']:.1f} с "
                f"(в среднем {1000 * st['embed_s'] / st['embeds']:.0f} мс, p90 {1000 * p90:.0f} мс), "
                f"кластеров {clusters}, с именем {named}, рядом {rooms}")
