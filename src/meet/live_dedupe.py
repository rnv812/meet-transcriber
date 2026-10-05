"""Дубли соседа в живой ленте (С2 live, дизайн §4.5).

Сосед по комнате говорит в свой ноутбук: его голос слышит мой микрофон, а
через звонок он же приходит в звук собеседников (sys) — на 0,1–0,3 с позже.
Без этого модуля такая реплика в живой ленте видна дважды: «Собеседник рядом»
из микрофона и «Демьян» из звонка.

Строки владельца публикуются сразу (подсказки «Вам вопрос» не ждут). Строки
микрофона с ролью `room`/`unsure` (meet.live_voices) ждут, пока распознавание
sys дойдёт до их конца + HOLD_SYS_S, но не дольше HOLD_MAX_S, и проверяются
общим кодом дублей (`mic_dedupe.match`): копия не публикуется, частичная
копия — без совпавших слов. Строка, которую пришлось показать раньше (sys не
успел), проверяется ещё раз, когда sys догонит, и, если вся оказалась копией,
прячется (`hide`).

Огибающие громкости дорожек копятся здесь же (амплитуда, кадр
mic_dedupe.FRAME_S): по ним mic_dedupe отличает акустическую копию от того же
текста другим человеком. Глобальный лаг L* копится по ходу встречи
(`mic_dedupe.Lags`): пока нет MIN_LAG_PAIRS уверенных пар, не удаляется ничего.

Включается настройкой `asr.live_mic_dedupe` (пока выключена по умолчанию: на
живом звуке не проверено) при `asr.mic_dedupe` и образце владельца."""

from __future__ import annotations

import copy
import time
from collections import deque

import numpy as np

from meet import mic_dedupe

SAMPLE_RATE = 16000
# Строка ждёт, пока sys распознан до её конца плюс HOLD_SYS_S (копия в sys
# позже микрофона на лаг, а слова — ещё на длину копии), но не дольше
# HOLD_MAX_S стенных секунд.
HOLD_SYS_S = 3.0
HOLD_MAX_S = 8.0
# Слова sys для сравнения — за последние RING_S секунд звука.
RING_S = 60.0
# Показанная раньше времени строка перепроверяется не дольше RECHECK_MAX_S
# (sys так и не догнал — оставляем как есть).
RECHECK_MAX_S = 30.0
HOLD_ROLES = ("room", "unsure")


class _Amp:
    """Амплитуда (RMS) кадров FRAME_S дорожки по времени записи; растёт."""

    def __init__(self) -> None:
        self.values = np.zeros(0, dtype=np.float32)
        self.size = 0

    def put(self, start_s: float, audio: np.ndarray) -> None:
        size = int(SAMPLE_RATE * mic_dedupe.FRAME_S)
        n = len(audio) // size
        if n <= 0:
            return
        block = np.asarray(audio[:n * size], dtype=np.float32).reshape(n, size)
        amp = np.sqrt((block * block).mean(axis=1))
        a = max(0, int(round(start_s / mic_dedupe.FRAME_S)))
        end = a + n
        if end > len(self.values):
            grown = np.zeros(max(end, 2 * len(self.values)), dtype=np.float32)
            grown[:len(self.values)] = self.values
            self.values = grown
        self.values[a:end] = amp
        self.size = max(self.size, end)

    def view(self) -> np.ndarray:
        return self.values[:self.size]


class LiveDuplicates:
    """Задержка и проверка строк микрофона на дубли. Зовётся из рабочего
    потока живого режима (под его `_window_lock`), сам без замков."""

    def __init__(self, *, clock=time.monotonic, hold_max_s: float = HOLD_MAX_S) -> None:
        self._clock = clock
        self.hold_max_s = hold_max_s
        self.lags = mic_dedupe.Lags()
        self._amp = {"mic": _Amp(), "sys": _Amp()}
        self._sys: deque = deque()  # mic_dedupe.Tok слов sys по времени
        self._sys_n = 0
        self.sys_until = 0.0  # докуда распознан звук sys (секунды записи)
        self._held: list[dict] = []
        self._shown: list[dict] = []
        self._uid = 0
        self.stats = {"held": 0, "dropped": 0, "trimmed": 0, "hidden": 0}

    # --- что услышано ---

    def sound(self, track: str, start_s: float, audio: np.ndarray) -> None:
        """Звук распознанного окна (16 кГц) — в огибающую дорожки."""
        amp = self._amp.get(track)
        if amp is not None:
            amp.put(start_s, audio)

    def heard_sys(self, segs, until_s: float) -> None:
        """Сегменты окна sys (со словами) и докуда sys теперь распознан."""
        for seg in segs:
            self._sys_n += 1
            for wi, w in enumerate(getattr(seg, "words", None) or ()):
                self._sys.append(mic_dedupe.Tok(key=("sys", self._sys_n, wi), seg=("sys", self._sys_n),
                                                start=float(w.start), end=float(w.end), text=w.text))
        self.sys_until = max(self.sys_until, float(until_s))
        while self._sys and self._sys[0].end < self.sys_until - RING_S:
            self._sys.popleft()

    # --- задержка ---

    @staticmethod
    def wants(role: str | None, seg) -> bool:
        """Строку микрофона с этой ролью стоит задержать (есть слова)."""
        return role in HOLD_ROLES and bool(getattr(seg, "words", None))

    def hold(self, item: dict, seg, role: str) -> None:
        """Задержать строку: `item` — что опубликовать потом (вызывающий
        кладёт туда строку и запись), `seg` — сегмент со словами."""
        self._uid += 1
        uid = self._uid
        item["toks"] = [mic_dedupe.Tok(key=("mic", uid, wi), seg=("mic", uid), start=float(w.start),
                                       end=float(w.end), text=w.text, role=role)
                        for wi, w in enumerate(seg.words)]
        item["end_t"] = float(seg.end)
        item["held_at"] = self._clock()
        self._held.append(item)
        self.stats["held"] += 1

    def release(self, final: bool = False) -> list[dict]:
        """Строки, которым пора: sys догнал, вышло HOLD_MAX_S или `final`.
        → что публиковать: `item["words"]` — оставшиеся слова (все или часть),
        `item["due"]` — проверена против догнавшего sys. Копии целиком не
        возвращаются."""
        now = self._clock()
        out, keep = [], []
        for item in self._held:
            due = self.sys_until >= item["end_t"] + HOLD_SYS_S
            if not (due or final or now - item["held_at"] >= self.hold_max_s):
                keep.append(item)
                continue
            dropped = self._dropped(item["toks"], self.lags)
            kept = [t for t in item["toks"] if t.key not in dropped]
            if not kept:
                self.stats["dropped"] += 1
                continue
            if len(kept) < len(item["toks"]):
                self.stats["trimmed"] += 1
            item["words"] = kept
            item["due"] = due
            out.append(item)
        self._held = keep
        return out

    def shown(self, item: dict) -> None:
        """Строку показали раньше, чем sys её догнал: перепроверить потом."""
        item["shown_at"] = self._clock()
        self._shown.append(item)

    def recheck(self) -> list[dict]:
        """Показанные раньше времени строки, которые теперь видны как копия
        целиком, — их пора спрятать."""
        now = self._clock()
        out, keep = [], []
        for item in self._shown:
            if self.sys_until >= item["end_t"] + HOLD_SYS_S:
                # L* — копией: эту пару уже учли при первой проверке.
                if not [t for t in item["words"] if t.key not in self._dropped(item["words"],
                                                                               copy.deepcopy(self.lags))]:
                    self.stats["hidden"] += 1
                    out.append(item)
            elif now - item["shown_at"] < RECHECK_MAX_S:
                keep.append(item)
        self._shown = keep
        return out

    def pending(self) -> int:
        return len(self._held)

    def _dropped(self, toks: list, lags) -> set:
        env = None
        if self._amp["mic"].size and self._amp["sys"].size:
            env = mic_dedupe.Envelope(self._amp["mic"].view(), self._amp["sys"].view())
        drops = mic_dedupe.match(toks, list(self._sys), lags=lags, owner_known=True, env=env)
        return {w.key for d in drops if d.track == "mic" for w in d.words}

    def stats_line(self) -> str | None:
        st = self.stats
        if not st["held"]:
            return None
        return (f"дубли живого режима: задержано {st['held']}, убрано {st['dropped']}, "
                f"обрезано {st['trimmed']}, спрятано показанных {st['hidden']}")
