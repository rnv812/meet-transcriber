"""Дубли соседа и эхо колонок: одна реплика попала и в микрофон, и в звук
собеседников (sys). Общий для расшифровки (meet.mic_split) и живого режима.

Откуда дубли:
- сосед по комнате говорит в свой ноутбук: его голос слышит мой микрофон, и
  он же приходит в sys через звонок — позже (лаг sys − mic > 0, ~0,1–1 с);
- эхо колонок: голос собеседника из sys звучит из колонок и попадает в мой
  микрофон — почти сразу (лаг ≤ ~0,05 с, sys раньше);
- утечка владельца: мой голос через ноутбук соседа приходит в мой sys (лаг > 0).

Механика одна: совпадение слов mic↔sys рядом по времени (токены + символьное
сходство склеек: копия в микрофоне тише и распознана с ошибками), устойчивый
лаг по всей встрече (L*) и сходство огибающих громкости. Какую копию
оставить, решает голос: окна микрофона с ролью `owner` (голос владельца по
образцу, meet.mic_split) не удаляются никогда — удаляется копия в sys; окна
`room` — удаляются из микрофона.

Главное правило: слова владельца без настоящих улик не теряются. Повтор
(собеседник зачитал номер заказа, «да, в пятницу в десять») — не копия,
хотя слова те же. Поэтому:
- L* — только от MIN_LAG_PAIRS уверенных пар с малым разбросом, и каждая
  пара проверяется по L*, посчитанному без неё самой: одна пара сама себя
  не подтверждает. Нет L* — не удаляется ничего;
- удаление, которое может задеть владельца или собеседника, — копия в sys
  по голосу владельца (`owner_leak`), окна `unsure` (они показаны как
  владелец) и любое удаление без образца — требует ещё и той же огибающей
  громкости (акустическая копия, а не второй человек с теми же словами);
  без образца копия к тому же должна быть заметно тише речи микрофона.

Пороги откалиброваны T0 на записях владельца с его согласия (2026-10-05,
.superpowers/sdd/v033/t0-calibration.md): настоящие копии соседа — лаг
0.07–0.29 с, покрытие 0.71–1.0, огибающая (амплитуда, кадр 20 мс) медиана
0.76. Огибающая — слабый признак (у «обе дорожки говорят, текст другой» p90
0.59), поэтому только дополнительное условие к тексту и лагу. Эхо колонок в
записях не встретилось — его пороги пока не проверены (нужна синтетика).
См. .superpowers/sdd/v033/speakers-design.md, §3.3."""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Hashable

import numpy as np

# Кадр огибающей: амплитуда (RMS) по 20 мс. T0: амплитуда отделяет копии от
# совпадений лучше дБ, а 20 мс — лучше 50 мс.
FRAME_S = 0.02
# Кандидаты: слова sys от WINDOW_BEFORE до начала реплики микрофона до
# WINDOW_AFTER после её конца. Старт, калибрует T0.
WINDOW_BEFORE = 0.5
WINDOW_AFTER = 2.5
# Сосед: mic раньше sys больше чем на NEIGHBOUR_MIN_LAG (T0: 0.07–0.29 с, у
# одного человека ~0.08); эхо: лаг не больше ECHO_MAX_LAG (sys раньше или
# вместе; не откалибровано — в записях эха не было).
NEIGHBOUR_MIN_LAG = 0.05
ECHO_MAX_LAG = 0.05
# Уверенная пара (задаёт L*): покрытие и число совпавших слов. Старт (T0).
CONFIDENT_COVERAGE = 0.8
CONFIDENT_WORDS = 4
# L* установлен: от MIN_LAG_PAIRS уверенных пар (не считая проверяемую) с
# разбросом (медиана отклонений от медианы) не больше LAG_MAD; лаги вне
# PLAUSIBLE_LAG в L* не идут. T0: коридор соседа 0–0.6 с (весь разброс
# настоящих пар — 0.22 с).
MIN_LAG_PAIRS = 3
LAG_MAD = 0.15
PLAUSIBLE_LAG = (-0.3, 0.6)
# Лаг согласован с L*: в LAG_TOLERANCE (и для коротких пар). T0: при ±0.5 в
# коридор попадало случайное «то же самое» владельца с лагом −0.26.
LAG_TOLERANCE = 0.3
SHORT_LAG_TOLERANCE = 0.3
# Длинная пара (от LONG_WORDS совпавших слов): покрытие mic символами sys (T0:
# у настоящих пар 0.71–1.0 — ASR sys иногда добавляет слово).
LONG_WORDS = 3
MIN_COVERAGE = 0.6
# Огибающая: поиск сдвига ±ENV_SEARCH вокруг текстового лага (длинные пары) и
# ±ENV_SHORT_SEARCH вокруг L* (короткие). Короткой паре нужна корреляция от
# MIN_ENV_CORR; длинной — от MIN_ENV_CORR_LONG там, где удаление может задеть
# владельца или собеседника (owner_leak, `unsure`, без образца). T0: от 0.5
# проходят 93 % настоящих пар; поиск ±0.3 с завышал контроль (p95 0.80).
ENV_SEARCH = 0.1
ENV_SHORT_SEARCH = 0.1
MIN_ENV_CORR = 0.5
MIN_ENV_CORR_LONG = 0.5
MIN_ENV_FRAMES = 4
# Короткая пара сравнивается с полями ENV_PAD по краям: внутри одного «ага»
# всего несколько кадров, а вот тишина вокруг него (или чужая сплошная речь)
# отличает копию от совпадения. Длинная — без полей: у повтора другим
# человеком границы фразы совпадают, а слоги внутри — нет. Старт (T0).
ENV_PAD = 0.25
# Без образца владельца: копия в микрофоне удаляется, только если её медиана
# громкости не выше QUIET_PERCENTILE-го процентиля речи микрофона минус
# QUIET_DB. T0: копии соседа в микрофоне на ~18 дБ тише владельца.
QUIET_DB = 8.0
QUIET_PERCENTILE = 90
# Удаляются прогоны от MIN_RUN_WORDS слов или от SHORT_SEGMENT_SHARE слов
# короткого сегмента («Ага.» целиком). Старт (T0).
MIN_RUN_WORDS = 2
SHORT_SEGMENT_SHARE = 0.6
# Склейка соседних совпавших кусков через пропуск до MAX_GAP_WORDS слов
# (ошибки распознавания копии) при символьном сходстве пропуска от GAP_SIMILARITY.
MAX_GAP_WORDS = 2
GAP_SIMILARITY = 0.5
# Прогон продлевается на соседние несовпавшие слова, похожие на копию
# («завтро» / «завтра»): символьное сходство слова от EDGE_SIMILARITY.
EDGE_SIMILARITY = 0.6

OWNER = "owner"
ROLES = (OWNER, "room", "unsure")
_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)


@dataclass(frozen=True)
class Tok:
    """Слово дорожки: `key` — по нему вызывающий удаляет слово, `seg` — его
    сегмент (доля слов короткого сегмента); `role` — роль окна голоса
    микрофона (owner | room | unsure), у слов sys не используется."""
    key: Hashable
    seg: Hashable
    start: float
    end: float
    text: str
    role: str = OWNER


@dataclass
class Pair:
    """Кандидат: прогон слов микрофона и совпавший с ним прогон sys."""
    mic: list[Tok]
    sys: list[Tok]
    links: list[tuple[int, int]]  # совпавшие слова: (номер в mic, номер в sys)
    coverage: float
    lag: float
    matched: int
    exact: bool


@dataclass
class Drop:
    """Удалённая копия: слова `words` дорожки `track`; `pair` — оставленная."""
    track: str
    words: list[Tok]
    pair: list[Tok]
    coverage: float
    lag: float
    env_corr: float | None
    reason: str  # echo | neighbour | owner_leak

    def to_raw(self) -> dict:
        other = "sys" if self.track == "mic" else "mic"
        return {"track": self.track, "start": round(self.words[0].start, 3), "end": round(self.words[-1].end, 3),
                "text": _text(self.words), "words": len(self.words),
                "pair": {"track": other, "start": round(self.pair[0].start, 3) if self.pair else None,
                         "end": round(self.pair[-1].end, 3) if self.pair else None, "text": _text(self.pair)},
                "coverage": round(self.coverage, 3), "lag": round(self.lag, 3),
                "env_corr": None if self.env_corr is None else round(self.env_corr, 3), "reason": self.reason}


def _text(words: list[Tok]) -> str:
    return " ".join(w.text.strip() for w in words if w.text.strip())


def normalize(text: str) -> str:
    """Нижний регистр, ё→е, без пунктуации."""
    return " ".join(_PUNCT.sub(" ", str(text).lower().replace("ё", "е")).split())


def _kind(lag: float) -> str:
    return "neighbour" if lag > NEIGHBOUR_MIN_LAG else "echo"


@dataclass
class Lags:
    """Глобальный лаг L*: медиана лагов уверенных пар — отдельно для соседа и
    для эха. Живой режим копит его по ходу встречи."""
    neighbour: list[float] = field(default_factory=list)
    echo: list[float] = field(default_factory=list)

    def add(self, lag: float) -> bool:
        """Лаг уверенной пары. → вошёл ли он в L*."""
        if not PLAUSIBLE_LAG[0] <= lag <= PLAUSIBLE_LAG[1]:
            return False
        if lag > NEIGHBOUR_MIN_LAG:
            self.neighbour.append(float(lag))
        elif lag <= ECHO_MAX_LAG:
            self.echo.append(float(lag))
        else:
            return False
        return True

    def observe(self, pair: Pair) -> float | None:
        """Уверенная пара пополняет L*. → её лаг, если вошёл (его потом
        исключают при проверке самой пары), иначе None."""
        if _confident(pair) and self.add(pair.lag):
            return float(pair.lag)
        return None

    def reference(self, kind: str, exclude: float | None = None) -> float | None:
        """L* для соседа или эха: медиана лагов уверенных пар — только если
        их (без `exclude`, лага самой проверяемой пары) не меньше
        MIN_LAG_PAIRS и они кучно (MAD ≤ LAG_MAD). Иначе None."""
        values = list(self.neighbour if kind == "neighbour" else self.echo)
        if exclude is not None and exclude in values:
            values.remove(exclude)
        if len(values) < MIN_LAG_PAIRS:
            return None
        med = float(np.median(values))
        if float(np.median(np.abs(np.asarray(values) - med))) > LAG_MAD:
            return None
        return med

    def consistent(self, lag: float, words: int, exclude: float | None = None) -> bool:
        """Лаг пары из `words` совпавших слов согласован с L* (посчитанным без
        `exclude`). Нет L* — не согласован: без независимых пар совпадение
        слов может быть повтором («всем спасибо всем пока»)."""
        ref = self.reference(_kind(lag), exclude)
        if ref is None:
            return False
        tol = SHORT_LAG_TOLERANCE if words < LONG_WORDS else LAG_TOLERANCE
        return abs(lag - ref) <= tol

    def to_raw(self) -> dict:
        def r(x):
            return None if x is None else round(x, 3)
        return {"neighbour": r(self.reference("neighbour")), "echo": r(self.reference("echo")),
                "pairs": {"neighbour": len(self.neighbour), "echo": len(self.echo)}}


def _frame_amp(audio: np.ndarray, rate: int, frame: float = FRAME_S) -> np.ndarray:
    """Амплитуда (RMS, доля полной шкалы) кадров int16-звука, по кускам — без
    float-копии всей дорожки."""
    size = max(1, int(rate * frame))
    n = len(audio) // size
    out = np.empty(n, dtype=np.float32)
    step = 50000
    for a in range(0, n, step):
        b = min(n, a + step)
        block = audio[a * size:b * size].reshape(b - a, size).astype(np.float32) / 32768.0
        out[a:b] = np.sqrt((block * block).mean(axis=1))
    return out


class Envelope:
    """Огибающие дорожек — амплитуда на кадр FRAME_S (общее время записи):
    корреляция — по амплитуде, «тише речи микрофона» — в дБ."""

    def __init__(self, mic_amp, sys_amp, speech: list[tuple[float, float]] | None = None,
                 frame: float = FRAME_S) -> None:
        self.mic = np.asarray(mic_amp, dtype=np.float64)
        self.sys = np.asarray(sys_amp, dtype=np.float64)
        self.frame = frame
        self.mic_db = 20.0 * np.log10(np.maximum(self.mic, 1e-5))
        frames = self.mic_db
        if speech:
            idx = [i for a, b in speech for i in range(*self._span(a, b))]
            if idx:
                frames = self.mic_db[np.unique(np.asarray(idx))]
        self.loud = float(np.percentile(frames, QUIET_PERCENTILE)) if frames.size else 0.0

    @classmethod
    def from_db(cls, mic_db, sys_db, speech: list[tuple[float, float]] | None = None,
                frame: float = FRAME_S) -> "Envelope":
        """Из громкости кадров в дБ — тесты и калибровка."""
        return cls(10.0 ** (np.asarray(mic_db, dtype=np.float64) / 20.0),
                   10.0 ** (np.asarray(sys_db, dtype=np.float64) / 20.0), speech, frame)

    @classmethod
    def from_audio(cls, mic: np.ndarray, sys: np.ndarray, rate: int,
                   speech: list[tuple[float, float]] | None = None) -> "Envelope":
        """Из звука дорожек (int16, `rate` Гц)."""
        return cls(_frame_amp(mic, rate), _frame_amp(sys, rate), speech)

    def _span(self, start: float, end: float) -> tuple[int, int]:
        a = max(0, int(round(start / self.frame)))
        b = min(len(self.mic), max(a + 1, int(round(end / self.frame))))
        return a, b

    def corr(self, start: float, end: float, center: float, search: float,
             pad: float = 0.0) -> tuple[float, float] | None:
        """Наибольшая корреляция Пирсона огибающей микрофона на [start − pad,
        end + pad] с огибающей sys, сдвинутой на center ± search. →
        (корреляция, сдвиг) или None (мало кадров, ровный звук)."""
        a, b = self._span(start - pad, end + pad)
        x = self.mic[a:b]
        if len(x) < MIN_ENV_FRAMES or not x.std():
            return None
        best = None
        lo, hi = int(round((center - search) / self.frame)), int(round((center + search) / self.frame))
        for k in range(lo, hi + 1):
            if a + k < 0 or b + k > len(self.sys):
                continue
            y = self.sys[a + k:b + k]
            if not y.std():
                continue
            r = float(np.corrcoef(x, y)[0, 1])
            if best is None or r > best[0]:
                best = (r, k * self.frame)
        return best

    def gap_db(self, start: float, end: float) -> float | None:
        """Громкость отрезка микрофона относительно его речи (медиана отрезка
        минус QUIET_PERCENTILE-й процентиль речи), дБ."""
        a, b = self._span(start, end)
        part = self.mic_db[a:b]
        return float(np.median(part)) - self.loud if part.size else None

    def quiet(self, start: float, end: float) -> bool:
        """Отрезок микрофона заметно тише его речи: дальний голос, не владелец."""
        gap = self.gap_db(start, end)
        return gap is not None and gap <= -QUIET_DB


# --- кандидаты ------------------------------------------------------------------


def _coverage(mic: list[str], sys: list[str]) -> float:
    """Доля символов склейки mic, совпавших со склейкой sys."""
    a, b = "".join(mic).replace(" ", ""), "".join(sys).replace(" ", "")
    if not a:
        return 0.0
    same = sum(m.size for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    return same / len(a)


def _similar(a: list[str], b: list[str]) -> bool:
    if not a or not b:
        return True  # слово пропущено в одной из копий
    x, y = "".join(a), "".join(b)
    return SequenceMatcher(None, x, y, autojunk=False).ratio() >= GAP_SIMILARITY


def _close(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= EDGE_SIMILARITY


def _block_lag(block, m, s) -> float:
    return float(np.median([s[block.b + k][0].start - m[block.a + k][0].start for k in range(block.size)]))


def _groups(blocks, m, s) -> list[list]:
    """Соседние совпавшие куски — один прогон, если между ними до
    MAX_GAP_WORDS слов, похожих на копию, и лаг тот же."""
    out: list[list] = []
    for block in blocks:
        if out:
            last = out[-1][-1]
            gap_a, gap_b = block.a - (last.a + last.size), block.b - (last.b + last.size)
            if (0 <= gap_a <= MAX_GAP_WORDS and 0 <= gap_b <= MAX_GAP_WORDS
                    and abs(_block_lag(block, m, s) - _block_lag(last, m, s)) <= LAG_TOLERANCE
                    and _similar([n for _, n in m[last.a + last.size:block.a]],
                                 [n for _, n in s[last.b + last.size:block.b]])):
                out[-1].append(block)
                continue
        out.append([block])
    return out


def _pairs(mic: list[Tok], sys: list[Tok], starts: list[float]) -> list[Pair]:
    """Пары одной реплики микрофона (слова по времени) со словами sys рядом."""
    m = [(t, normalize(t.text)) for t in mic]
    m = [(t, n) for t, n in m if n]
    if not m:
        return []
    lo = bisect.bisect_left(starts, m[0][0].start - WINDOW_BEFORE)
    hi = bisect.bisect_right(starts, m[-1][0].end + WINDOW_AFTER)
    s = [(t, normalize(t.text)) for t in sys[lo:hi]]
    s = [(t, n) for t, n in s if n]
    if not s:
        return []
    tm, ts = [n for _, n in m], [n for _, n in s]
    blocks = [b for b in SequenceMatcher(None, tm, ts, autojunk=False).get_matching_blocks() if b.size]
    out = []
    for group in _groups(blocks, m, s):
        i0, i1 = group[0].a, group[-1].a + group[-1].size
        j0, j1 = group[0].b, group[-1].b + group[-1].size
        links = [(b.a + k, b.b + k) for b in group for k in range(b.size)]
        # Края: соседние слова, похожие на копию, — тоже часть прогона.
        while i0 > 0 and j0 > 0 and _close(tm[i0 - 1], ts[j0 - 1]):
            i0, j0 = i0 - 1, j0 - 1
        while i1 < len(tm) and j1 < len(ts) and _close(tm[i1], ts[j1]):
            i1, j1 = i1 + 1, j1 + 1
        links = [(i - i0, j - j0) for i, j in links]
        lag = float(np.median([s[j0 + j][0].start - m[i0 + i][0].start for i, j in links]))
        out.append(Pair(mic=[t for t, _ in m[i0:i1]], sys=[t for t, _ in s[j0:j1]], links=links,
                        coverage=_coverage(tm[i0:i1], ts[j0:j1]), lag=lag, matched=len(links),
                        exact=tm[i0:i1] == ts[j0:j1]))
    return out


# --- решение --------------------------------------------------------------------


def _confident(pair: Pair) -> bool:
    return pair.matched >= CONFIDENT_WORDS and pair.coverage >= CONFIDENT_COVERAGE


def _side(t: Tok, owner_known: bool) -> str:
    """Чьё слово микрофона для удаления: `owner` — владелец по образцу;
    `room` — человек в комнате; `weak` — показан как владелец (`unsure`) или
    образца нет: удалять только при сильных уликах."""
    if not owner_known:
        return "weak"
    return {OWNER: "owner", "room": "room"}.get(t.role, "weak")


def _role_runs(pair: Pair, owner_known: bool) -> list[tuple[str, list[int]]]:
    """Подряд идущие слова пары одной стороны: (сторона, номера в pair.mic)."""
    out: list[tuple[str, list[int]]] = []
    for i, t in enumerate(pair.mic):
        side = _side(t, owner_known)
        if out and out[-1][0] == side:
            out[-1][1].append(i)
        else:
            out.append((side, [i]))
    return out


def _big_enough(words: list[Tok], sizes: Counter) -> bool:
    if len(words) >= MIN_RUN_WORDS:
        return True
    return bool(words) and len(words) / max(1, sizes[words[0].seg]) >= SHORT_SEGMENT_SHARE


def _aligned(pair: Pair, idx: list[int]) -> list[Tok]:
    """Слова sys, совпавшие со словами mic `idx` (с пропусками между ними)."""
    want = set(idx)
    js = [j for i, j in pair.links if i in want]
    return pair.sys[min(js):max(js) + 1] if js else []


def _decide(pair: Pair, own: float | None, lags: Lags, env: Envelope | None, owner_known: bool,
            mic_sizes: Counter, sys_sizes: Counter) -> list[Drop]:
    """Решение по паре. `own` — лаг самой пары, если он вошёл в L*: пара
    проверяется по L* без него."""
    start, end = pair.mic[0].start, pair.mic[-1].end
    ref = lags.reference(_kind(pair.lag), own)
    if ref is None or not lags.consistent(pair.lag, pair.matched, own):
        return []
    if pair.matched >= LONG_WORDS:
        if pair.coverage < MIN_COVERAGE:
            return []
        got = env.corr(start, end, pair.lag, ENV_SEARCH) if env is not None else None
        strong = got is not None and got[0] >= MIN_ENV_CORR_LONG
    else:
        # 1–2 слова: точное совпадение, лаг у самого L* и та же огибающая.
        if not pair.exact or env is None:
            return []
        got = env.corr(start, end, ref, ENV_SHORT_SEARCH, pad=ENV_PAD)
        if got is None or got[0] < MIN_ENV_CORR:
            return []
        strong = True
    env_corr = got[0] if got else None
    kind = _kind(pair.lag)
    drops = []
    for side, idx in _role_runs(pair, owner_known):
        words = [pair.mic[i] for i in idx]
        other = _aligned(pair, idx)
        if side == "owner":
            # Окно владельца: его слова в микрофоне не трогаем. Копия в sys
            # позже микрофона и с той же огибающей — мой голос через чужой ноутбук.
            if strong and pair.lag > NEIGHBOUR_MIN_LAG and other and _big_enough(other, sys_sizes):
                drops.append(Drop("sys", other, words, pair.coverage, pair.lag, env_corr, "owner_leak"))
            continue
        if side == "weak":
            if not strong:
                continue  # показан как владелец: без той же огибающей не трогаем
            if not owner_known and not env.quiet(words[0].start, words[-1].end):
                continue  # без образца — только заметно тихая копия
        if _big_enough(words, mic_sizes):
            drops.append(Drop("mic", words, other, pair.coverage, pair.lag, env_corr, kind))
    return drops


def _by_segment(mic: list[Tok]) -> list[list[Tok]]:
    groups: dict = {}
    for t in mic:
        groups.setdefault(t.seg, []).append(t)
    return [sorted(g, key=lambda t: t.start) for g in groups.values()]


def _fresh(drops: list[Drop], used: set) -> list[Drop]:
    """Слово удаляется один раз: пересекающиеся решения — первое."""
    out = []
    for d in drops:
        keys = {(d.track, w.key) for w in d.words}
        if keys & used:
            continue
        used |= keys
        out.append(d)
    return out


def _resolve(mic: list[Tok], sys: list[Tok], lags: Lags, env: Envelope | None,
             owner_known: bool) -> list[Drop]:
    sys = sorted(sys, key=lambda t: t.start)
    starts = [t.start for t in sys]
    pairs = [p for seg in _by_segment(mic) for p in _pairs(seg, sys, starts)]
    own = [lags.observe(p) for p in pairs]
    mic_sizes, sys_sizes = Counter(t.seg for t in mic), Counter(t.seg for t in sys)
    used: set = set()
    drops: list[Drop] = []
    for p, lag in zip(pairs, own):
        drops += _fresh(_decide(p, lag, lags, env, owner_known, mic_sizes, sys_sizes), used)
    return drops


def find(mic: list[Tok], sys: list[Tok], *, owner_known: bool, env: Envelope | None = None,
         lags: Lags | None = None) -> tuple[list[Drop], Lags]:
    """Дубли по всей встрече (расшифровка): сначала L* по уверенным парам всей
    встречи, потом решение по каждой паре. → (удалённые копии, лаги)."""
    lags = lags if lags is not None else Lags()
    return _resolve(mic, sys, lags, env, owner_known), lags


def match(mic: list[Tok], sys: list[Tok], *, lags: Lags, owner_known: bool = True,
          env: Envelope | None = None) -> list[Drop]:
    """Живой режим: одна реплика микрофона против недавних слов sys. Уверенные
    пары пополняют `lags` — короткие реплики опираются на накопленный L*."""
    return _resolve(mic, sys, lags, env, owner_known)
