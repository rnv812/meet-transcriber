"""Живые голоса: онлайн-кластеры по дорожкам и имена задним числом (С3).

Раньше живой режим решал имя по одному сегменту ASR (1–3 с) против образцов
базы, с порогом-константой 0.75: так имя почти не получалось — короткий клип
против центроида целой встречи даёт cos 0.55–0.66 (калибровка T0). Теперь:

1. сегмент от EMBED_MIN_S → эмбеддинг (середина сегмента, не длиннее
   EMBED_MAX_S; та же модель, что у базы голосов) → ближайший онлайн-кластер
   дорожки при cos от LIVE_ASSIGN и отрыве от второго LIVE_MARGIN, иначе новый
   кластер (только с сегмента от NEW_MIN_S; короче — кластер предыдущего
   сегмента при паузе до INHERIT_GAP_S); сегмент без эмбеддинга — кластер
   предыдущего при паузе до SHORT_INHERIT_GAP_S;
2. центроид — нормированная сумма эмбеддингов с весом длительности; раз в
   MERGE_EVERY сегментов кластеры от LIVE_MERGE_MIN_S речи сливаются при cos
   от LIVE_MERGE (на микрофоне — только одной роли; имя переходит к слитому,
   только если его центроид сам проходит проверку имени); не больше
   MAX_CLUSTERS кластеров на дорожку — дальше самый старый мелкий безымянный
   кластер замораживается, а не смешивается с чужим голосом;
3. имя — по накопленному центроиду: от LIVE_MIN_SPEECH_S речи, cos от
   T_live (`mic_split.live_threshold(asr.voice_threshold)`), отрыв, тот же
   кандидат на LIVE_CHECKS проверках подряд (каждые CHECK_STEP_S речи), имя
   не занято другим кластером дорожки (оба уверены — кластеры сливаются).
   Имя закрепляется; снимается, только если после удвоения речи сходство с
   ним упало ниже T_live − UNPIN_DROP;
4. микрофон (только при образце владельца) — как офлайн (mic_split._roles):
   сегмент, похожий на образец от T_WIN_FAST, идёт в якорь владельца
   (`mic:owner`) и других кластеров не растит; из остальных сегментов
   кластер от ROOM_MIN_S речи с центроидом ниже T_OTHER на двух проверках
   подряд — человек в комнате (`room`), но только пока владелец в микрофоне
   найден: среди кластеров от OWNER_PRESENT_SHARE речи микрофона есть
   похожий на образец от T_OTHER. Не найден (образец с другого микрофона) —
   микрофон весь владельца, как без образца. Обратно во владельца — от T_OWN.

Ключи голосов несут метку сеанса (`3fa1/sys:3`): новый ассистент в той же
записи начинает нумерацию заново, а панель держит строки прежнего.
`drain()` отдаёт переименования «ключ → подпись», живой режим правит по ним
ленту и файл (см. meet.live, meet.assist.bus).

Экономия процессора (T0: эмбеддинг 60–110 мс при 4 потоках): клип не длиннее
EMBED_MAX_S; когда владелец уверенно найден (якорь от OWNER_FAST_MIN_S и от
FAST_SHARE секунд микрофона похожи на образец), считается каждый
OWNER_FAST_EVERY-й сегмент владельца подряд; у названного собеседника, который
говорит без пауз, — каждый PINNED_EVERY-й. Остальные наследуют голос соседа.

Пороги — из калибровки T0 (.superpowers/sdd/v033/t0-calibration.md, §3),
константы — в meet.mic_split (LIVE_*). Все векторы нормируются: центроиды
community-1 в базе голосов не единичной длины (норма 0.9–2.9).
См. .superpowers/sdd/v033/speakers-design.md, §4.1–4.4."""

from __future__ import annotations

import secrets
import time
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from meet import mic_split, owner_voice

SAMPLE_RATE = 16000
# Короче — эмбеддинг почти случаен (T0: клип 1–2 с против центроида встречи —
# p10 0.40): кластер соседа.
EMBED_MIN_S = 1.5
# Длиннее — считается середина: ранжирует людей и 2–3 с (T0: 92–96 %), а 5 с
# стоят вдвое дороже 3 с (182 мс против 112 мс).
EMBED_MAX_S = 3.0
# Новый кластер — только с сегмента от NEW_MIN_S: голос 1–2 с слишком шумный,
# чтобы заводить по нему человека.
NEW_MIN_S = 2.0
# Сегмент с голосом, не похожий ни на один кластер, но короче NEW_MIN_S, —
# кластер предыдущего сегмента при паузе до INHERIT_GAP_S; сегмент без голоса
# — при паузе до SHORT_INHERIT_GAP_S.
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
# Роль `room` — только у кластера от ROOM_MIN_S речи (как офлайн MIN_DECIDE_S).
ROOM_MIN_S = mic_split.MIN_DECIDE_S
# Сколько последних сегментов дорожки помнить для «кластер предыдущего».
RECENT = 16
# Сегмент может начаться чуть раньше конца предыдущего (границы слов ASR).
OVERLAP_S = 0.05
# Экономия: владелец найден уверенно — якорь от OWNER_FAST_MIN_S и от
# FAST_SHARE секунд микрофона похожи на образец; тогда эмбеддинг — у каждого
# OWNER_FAST_EVERY-го сегмента владельца подряд (и у любого от LONG_SEGMENT_S).
OWNER_FAST_MIN_S = 30.0
OWNER_FAST_EVERY = 3
LONG_SEGMENT_S = 4.0
# Названный собеседник говорит дальше (пауза до PINNED_GAP_S) — эмбеддинг у
# каждого PINNED_EVERY-го сегмента: GigaAM режет один монолог на много сегментов.
PINNED_GAP_S = 0.3
PINNED_EVERY = 2
# Владелец найден по сильной улике — группа якоря от OWNER_LATCH_S речи с
# центроидом от T_OWN, и она же не меньше OWNER_PRESENT_SHARE речи микрофона
# от LATCH_VOICED_S: дальше «найден» до конца сеанса. Доля и объём — чтобы не
# защёлкнуться на прохожем, совпавшем с чужим образцом (ревью, lv_sim6).
OWNER_LATCH_S = 30.0
LATCH_VOICED_S = 2 * OWNER_LATCH_S
# Уже найденный владелец «теряется», только если сходство группы якоря упало
# ниже T_OTHER − PRESENT_SLACK (у образца среднего качества оно гуляет у порога).
PRESENT_SLACK = 0.05
# Время эмбеддингов для журнала — последние TIMES_KEPT.
TIMES_KEPT = 2000

OWNER = "owner"
ROOM = "room"
UNSURE = "unsure"
OTHER = "other"  # дорожка собеседников: роли владельца нет
# Подпись человека в комнате, которого нет в базе голосов.
ROOM_SPEAKER = "Собеседник рядом"
# Номер якоря владельца на микрофоне (ключ `mic:owner`).
OWNER_N = -1


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


def track_of(key: str) -> str:
    """Дорожка ключа голоса: `3fa1/mic:2` → `mic`."""
    return key.rpartition("/")[2].partition(":")[0]


@dataclass
class _Cluster:
    n: int
    total: np.ndarray  # сумма единичных эмбеддингов с весом длительности
    seconds: float = 0.0
    into: int | None = None  # слит в кластер с этим номером
    retired: bool = False  # заморожен на потолке кластеров: строки — по умолчанию
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
    (`"sys:3"`, с меткой сеанса `prefix`) или None (голоса нет и соседнего
    сегмента тоже). На микрофоне ещё якорь владельца (`observe_owner`)."""

    def __init__(self, track: str, *, prefix: str = "", assign: float = mic_split.LIVE_ASSIGN,
                 margin: float = mic_split.LIVE_MARGIN, merge: float = mic_split.LIVE_MERGE,
                 merge_min_s: float = mic_split.LIVE_MERGE_MIN_S,
                 max_clusters: int = MAX_CLUSTERS, accept_name=None) -> None:
        self.track = track
        self.prefix = prefix
        self.assign_at = assign
        self.margin = margin
        self.merge_at = merge
        self.merge_min_s = merge_min_s
        self.max_clusters = max_clusters
        # (центроид, имя) -> имя этому центроиду можно дать (слияние с именем).
        self.accept_name = accept_name
        self.clusters: dict[int, _Cluster] = {}  # и слитые (ключи строк живут)
        self._recent: deque = deque(maxlen=RECENT)
        self._voiced = 0
        self._next = 0

    def key(self, n: int) -> str:
        return f"{self.prefix}{self.track}:{'owner' if n == OWNER_N else n}"

    def number(self, key: str) -> int | None:
        if not key.startswith(self.prefix):
            return None
        head, _, tail = key[len(self.prefix):].partition(":")
        if head != self.track:
            return None
        if tail == "owner":
            return OWNER_N
        return int(tail) if tail.isdigit() else None

    def resolve(self, n: int) -> _Cluster:
        """Кластер, в который в итоге слит кластер `n` (сам он, если не слит)."""
        c = self.clusters[n]
        while c.into is not None:
            c = self.clusters[c.into]
        return c

    def live(self) -> list[_Cluster]:
        """Кластеры, к которым присоединяются сегменты (без якоря владельца)."""
        return [c for c in self.clusters.values()
                if c.into is None and not c.retired and c.n != OWNER_N]

    def owner(self) -> _Cluster | None:
        return self.clusters.get(OWNER_N)

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

    def observe_owner(self, emb: np.ndarray, start: float, end: float) -> str:
        """Сегмент, похожий на образец владельца: в якорь `mic:owner`."""
        seconds = max(0.0, float(end) - float(start))
        c = self.clusters.get(OWNER_N)
        if c is None:
            self.clusters[OWNER_N] = _Cluster(OWNER_N, emb * seconds, seconds)
        else:
            c.add(emb, seconds)
        self._recent.append((float(start), float(end), OWNER_N))
        return self.key(OWNER_N)

    def previous(self, start: float, gap: float) -> _Cluster | None:
        n = self._previous(start, gap)
        return None if n is None else self.resolve(n)

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
            # Потолок: чужой голос не смешиваем ни с кем (его центроид и имя —
            # чужие). Самый старый мелкий безымянный кластер замораживается
            # (его строки и так по умолчанию), место — новому голосу.
            old = self._recyclable()
            if old is not None:
                old.retired = True
                return self._new(emb, seconds)
        return self._previous(start, INHERIT_GAP_S)

    def _recyclable(self) -> _Cluster | None:
        small = [c for c in self.live() if c.name is None and c.role != ROOM
                 and c.seconds < mic_split.LIVE_MIN_SPEECH_S]
        return min(small, key=lambda c: c.n) if small else None

    def _new(self, emb: np.ndarray, seconds: float) -> int:
        n = self._next
        self._next += 1
        self.clusters[n] = _Cluster(n, emb * seconds, seconds)
        return n

    def _previous(self, start: float, gap: float) -> int | None:
        """Кластер сегмента, кончившегося прямо перед `start` (пауза до `gap`).
        Окна догонялки идут вперемешку с живыми — ищем по времени, а не
        просто последний. Замороженный кластер не наследуется."""
        best = None
        for a, b, n in self._recent:
            if b <= start + OVERLAP_S and start - b <= gap and (best is None or b > best[0]):
                best = (b, n)
        if best is None:
            return None
        c = self.resolve(best[1])
        return None if c.retired else c.n

    def merge(self, keep: _Cluster, gone: _Cluster) -> _Cluster:
        """Слить `gone` в `keep`: больший остаётся, имя переходит к нему.
        Разные роли (микрофон) — итог владелец: `room` надо заслужить заново."""
        if gone.seconds > keep.seconds:
            keep, gone = gone, keep
        if keep.role != gone.role:
            keep.role, keep.room_streak = OWNER, 0
        keep.total = keep.total + gone.total
        keep.seconds += gone.seconds
        if keep.name is None and gone.name is not None:
            keep.name, keep.named_s = gone.name, gone.named_s
        keep.role_checked_s = 0.0  # роль пересчитает ближайшая проверка по новому центроиду
        gone.into = keep.n
        return keep

    def _mergeable(self, a: _Cluster, b: _Cluster) -> bool:
        if a.name and b.name and a.name != b.name:
            return False
        if self.track == "mic" and a.role != b.role:
            return False  # голос владельца с человеком рядом не смешиваем
        name = a.name or b.name
        if name and (a.name is None or b.name is None) and self.accept_name is not None:
            # Имя переходит к безымянному — только если общий центроид сам его заслуживает.
            return bool(self.accept_name(_unit(a.total + b.total), name))
        return True

    def merge_close(self) -> int:
        """Слить кластеры от merge_min_s речи с cos от merge_at. → сколько слито."""
        done = 0
        while True:
            big = [c for c in self.live() if c.seconds >= self.merge_min_s]
            pairs = [(float(a.center @ b.center), a, b)
                     for i, a in enumerate(big) for b in big[i + 1:]]
            pairs = [p for p in pairs if p[0] >= self.merge_at and self._mergeable(p[1], p[2])]
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
    — микрофон. `session` — метка ключей (по умолчанию случайная)."""

    def __init__(self, embed, base, owner, *, name_threshold: float,
                 defaults: dict | None = None, mic: bool = True, device: str | None = None,
                 log=print, clock=time.perf_counter, session: str | None = None) -> None:
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
        self.session = session or secrets.token_hex(4)
        self._tracks: dict[str, TrackVoices] = {}
        self._shown: dict[str, str] = {}  # подпись ключа в уже выданных строках
        self._embed_error: str | None = None
        # Микрофон: секунды с эмбеддингом и из них похожие на образец; владелец
        # найден (§2.4) — иначе микрофон не делится.
        self._mic_voiced = 0.0
        self._mic_owner_like = 0.0
        self._present = False
        self._latched = False  # владелец найден по сильной улике — до конца сеанса
        self._missing_logged = False
        self._mic_run = 0  # сегменты владельца подряд (экономия)
        self._sys_run = 0  # сегменты названного собеседника подряд (экономия)
        self.stats = {"embeds": 0, "embed_s": 0.0, "times": deque(maxlen=TIMES_KEPT), "skipped": 0}

    def active(self, track: str) -> bool:
        return self._mic if track == "mic" else bool(self._base)

    def _default(self, track: str) -> str:
        return self.defaults.get(track, self.defaults["sys"])

    def _plain(self, track: str) -> Assigned:
        return Assigned(None, self._default(track), OWNER if track == "mic" else OTHER)

    def _track(self, track: str) -> TrackVoices:
        tv = self._tracks.get(track)
        if tv is None:
            tv = self._tracks[track] = TrackVoices(track, prefix=f"{self.session}/",
                                                   accept_name=self._name_holds)
        return tv

    def assign(self, track: str, clip: np.ndarray, start: float, end: float) -> Assigned:
        """Голос сегмента `clip` (16 кГц) дорожки `track` → ключ, подпись на
        сейчас и роль (для дублей микрофона)."""
        if not self.active(track):
            return self._plain(track)
        tv = self._track(track)
        seconds = max(0.0, float(end) - float(start))
        emb = None
        if len(clip) >= EMBED_MIN_S * SAMPLE_RATE:
            if self._skip(track, tv, start, seconds):
                self.stats["skipped"] += 1
            else:
                emb = self._embedding(self._middle(clip))
        if track == "mic" and emb is not None:
            score = owner_voice.score(emb, self._owner, self._device)
            self._mic_voiced += seconds
            if score >= mic_split.T_WIN_FAST:
                # Похож на образец — якорь владельца: голос владельца не
                # дробится на кластеры, из которых мог бы выйти «человек рядом».
                self._mic_owner_like += seconds
                key = tv.observe_owner(emb, start, end)
            else:
                self._mic_run = 0  # чужой голос — снова считаем каждый сегмент
                key = tv.observe(emb, start, end)
        else:
            key = tv.observe(emb, start, end)
        if key is None:
            return self._plain(track)
        self._review(track, tv)
        speaker = self.speaker(key)
        self._shown.setdefault(key, speaker)
        return Assigned(key, speaker, self._role(track, tv, key, emb))

    @staticmethod
    def _middle(clip: np.ndarray) -> np.ndarray:
        size = int(EMBED_MAX_S * SAMPLE_RATE)
        if len(clip) <= size:
            return clip
        a = (len(clip) - size) // 2
        return clip[a:a + size]

    def _skip(self, track: str, tv: TrackVoices, start: float, seconds: float) -> bool:
        """Сегмент можно не считать: голос тот же, что у соседнего, и уверенный."""
        if track == "mic":
            owner = tv.owner()
            prev = tv.previous(start, SHORT_INHERIT_GAP_S)
            sure = owner is not None and owner.seconds >= OWNER_FAST_MIN_S \
                and self._mic_owner_like >= mic_split.FAST_SHARE * self._mic_voiced
            if not sure or prev is None or prev.n != OWNER_N or seconds >= LONG_SEGMENT_S:
                self._mic_run = 0
                return False
            self._mic_run += 1
            return self._mic_run % OWNER_FAST_EVERY != 0
        prev = tv.previous(start, PINNED_GAP_S)
        if prev is None or prev.name is None:
            self._sys_run = 0
            return False
        self._sys_run += 1
        return self._sys_run % PINNED_EVERY != 0

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
        tv = self._tracks.get(track_of(key))
        n = tv.number(key) if tv is not None else None
        if n is None or n not in tv.clusters:
            return None
        return tv, tv.resolve(n)

    def _near_owner(self, c: _Cluster) -> bool:
        """Кластер — тот же голос, что якорь владельца (cos от LIVE_MERGE): у
        шумного образца часть окон владельца ниже T_WIN_FAST и складывается в
        свой кластер — это не человек рядом."""
        tv = self._tracks.get("mic")
        anchor = tv.owner() if tv is not None else None
        return anchor is not None and float(c.center @ anchor.center) >= mic_split.LIVE_MERGE

    def _is_room(self, c: _Cluster) -> bool:
        return c.n != OWNER_N and c.role == ROOM and self._present and not self._near_owner(c)

    def speaker(self, key: str) -> str:
        """Подпись голоса на сейчас."""
        track = track_of(key)
        found = self._cluster(key)
        if found is None:
            return self._default(track)
        _, c = found
        if track == "mic":
            return (c.name or ROOM_SPEAKER) if self._is_room(c) else self._default(track)
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
        track = track_of(key)
        if track != "mic":
            return OTHER
        found = self._cluster(key)
        if found is None:
            return OWNER
        tv, _ = found
        return self._role(track, tv, key, None)

    def _role(self, track: str, tv: TrackVoices, key: str, emb: np.ndarray | None) -> str:
        if track != "mic":
            return OTHER
        c = tv.resolve(tv.number(key))
        if c.n == OWNER_N:
            return OWNER
        if self._is_room(c):
            return ROOM
        if self._mic_voiced >= mic_split.MIN_VOICED_S and not self._present:
            return OWNER  # владелец не найден — микрофон как без образца
        if c.seconds >= ROOM_MIN_S and c.role_checked_s:
            return OWNER  # роль кластера уже проверялась — владелец
        # Не похож на образец (иначе был бы в якоре) — не решено.
        vec = emb if emb is not None else c.center
        return OWNER if owner_voice.score(vec, self._owner, self._device) >= mic_split.T_WIN_FAST \
            else UNSURE

    # --- проверки кластеров ---

    def _owner_present(self, tv: TrackVoices) -> bool:
        """Владелец в микрофоне найден (mic_split._roles, §2.4): от
        MIN_VOICED_S секунд с голосом и среди групп от OWNER_PRESENT_SHARE
        этой речи есть похожая на образец от T_OTHER. Якорь владельца считается
        вместе с близкими к нему кластерами (cos от LIVE_MERGE): сам якорь —
        только окна выше T_WIN_FAST, его центроид завышен, а офлайн (AHC) собрал
        бы голос владельца целиком.

        Уже найденный остаётся найденным до половины доли и до T_OTHER −
        PRESENT_SLACK (доля и сходство гуляют около порога — подписи «рядом»
        мигали бы), а при сильной улике (группа владельца от OWNER_LATCH_S с
        центроидом от T_OWN, и это от OWNER_PRESENT_SHARE из не меньше чем
        LATCH_VOICED_S речи микрофона) — до конца сеанса: «не найден» — про
        доверие к образцу, а не про то, что владелец сейчас молчит; прошлые
        строки людей рядом не становятся «Вы». Голос, который сначала говорил
        больше всех и совпал с чужим образцом, так не отличить от владельца,
        который ушёл, — это неустранимо; прохожий на минуту — отличается."""
        if self._latched:
            return True
        voiced = self._mic_voiced
        if voiced < mic_split.MIN_VOICED_S:
            return False
        share = mic_split.OWNER_PRESENT_SHARE / (2 if self._present else 1)
        bar = mic_split.T_OTHER - (PRESENT_SLACK if self._present else 0.0)
        groups = []  # (центроид, секунды)
        anchor = tv.owner()
        near = []
        if anchor is not None:
            near = [c for c in tv.live() if float(c.center @ anchor.center) >= mic_split.LIVE_MERGE]
            total = anchor.total + sum((c.total for c in near), np.zeros_like(anchor.total))
            seconds = anchor.seconds + sum(c.seconds for c in near)
            groups.append((_unit(total), seconds))
            score = owner_voice.score(_unit(total), self._owner, self._device)
            if seconds >= OWNER_LATCH_S and score >= mic_split.T_OWN \
                    and voiced >= LATCH_VOICED_S \
                    and seconds >= mic_split.OWNER_PRESENT_SHARE * voiced:
                self._latched = True
                self._log(f"голоса: голос владельца закреплён до конца встречи (cos {score:.2f}, "
                          f"{seconds:.0f} с из {voiced:.0f} с)")
                return True
        groups += [(c.center, c.seconds) for c in tv.live() if all(c is not n for n in near)]
        present = any(seconds >= share * voiced
                      and owner_voice.score(center, self._owner, self._device) >= bar
                      for center, seconds in groups)
        if not present and not self._missing_logged:
            self._missing_logged = True  # одна строка на сеанс
            self._log("голоса: голос владельца в микрофоне пока не найден (образец с другого "
                      "микрофона?) — микрофон не делится")
        return present

    def _review(self, track: str, tv: TrackVoices) -> None:
        if track == "mic":
            was, self._present = self._present, self._owner_present(tv)
            # Журнал — как подписи: «рядом» появляются и пропадают вместе с этим.
            if self._present and not was:
                self._log("голоса: голос владельца в микрофоне найден — люди рядом "
                          "подписываются отдельно")
            elif was and not self._present:
                self._log("голоса: голос владельца больше не найден — микрофон снова весь владельца")
        for c in tv.live():
            if c.into is not None:
                continue  # слит на этом же обходе
            if track == "mic":
                self._check_role(c)
                if not self._is_room(c):
                    continue
            self._check_name(tv, c)

    def _check_role(self, c: _Cluster) -> None:
        if self._near_owner(c):
            # Голос якоря владельца (его окна ниже T_WIN_FAST) — никогда не «рядом».
            if c.role == ROOM:
                self._log("голоса: голос микрофона — это владелец (близок к его якорю)")
            c.role, c.room_streak = OWNER, 0
            return
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
        if c.room_streak >= mic_split.LIVE_CHECKS and self._present:
            c.role = ROOM
            self._log(f"голоса: в микрофоне человек рядом (cos с образцом {score:.2f}, "
                      f"{c.seconds:.0f} с речи)")

    def _score_of(self, center: np.ndarray, name: str) -> float:
        return max((float(center @ v) for v in self._base.get(name, ())), default=-1.0)

    def _best(self, center: np.ndarray) -> tuple[float, str, float | None]:
        scores = sorted(((self._score_of(center, name), name) for name in self._base), reverse=True)
        best, name = scores[0]
        return best, name, scores[1][0] if len(scores) > 1 else None

    def _sure(self, best: float, second: float | None) -> bool:
        return best >= self.threshold and (second is None or best - second >= self.margin)

    def _name_holds(self, center: np.ndarray, name: str) -> bool:
        """Центроид сам проходит проверку имени `name` (порог и отрыв)."""
        if not self._base:
            return False
        best, top, second = self._best(center)
        return top == name and self._sure(best, second)

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
        best, name, second = self._best(c.center)
        candidate = name if self._sure(best, second) else None
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
        clusters = ", ".join(f"{t} {len(tv.live())}" for t, tv in self._tracks.items())
        named = sum(1 for tv in self._tracks.values() for c in tv.live() if c.name)
        rooms = sum(1 for c in self._tracks["mic"].live() if self._is_room(c)) \
            if "mic" in self._tracks else 0
        return (f"голоса живого режима: эмбеддингов {st['embeds']}, {st['embed_s']:.1f} с "
                f"(в среднем {1000 * st['embed_s'] / st['embeds']:.0f} мс, p90 {1000 * p90:.0f} мс), "
                f"пропущено {st['skipped']}, кластеров {clusters}, с именем {named}, рядом {rooms}")
