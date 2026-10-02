"""Распознавание живого режима: GigaAM короткими окнами или Whisper.

Живой режим распознаёт речь окнами по ходу встречи (`meet.live.LiveEngine`).
От окна зависит, как скоро реплика появится в ленте и дойдёт до подсказок:

* **GigaAM** (русский) — окна ~5 с: резка в самой тихой точке между 4 и 7 с
  (`gigaam_asr.quiet_cut`, пауза между словами), окно распознаётся за доли
  секунды и на CPU (замер: 0,35 с на 5 с звука). Распознавание отстало —
  окна до 20 с (меньше вызовов). Только публичный API GigaAM: окно пишется
  во временный wav и уходит в `model.transcribe(path, word_timestamps=True)`.
  Окно без речи (Silero VAD) модель не видит вовсе. Подсказок (hotwords) у
  GigaAM нет: термины чинят правила замены и возврат латиницы (`TextFixes`);
* **Whisper** — язык встречи не русский, GigaAM не установлена или не
  скачана, выбран `assist.live_asr = whisper`, или GigaAM не загрузилась /
  трижды подряд не распознала окно. Окна ~15–20 с: на коротких Whisper
  заметно хуже.

`pick(cfg)` выбирает движок при старте: модель GigaAM не скачивается ради
живого режима (это минуты), без неё — Whisper.
"""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WindowPolicy:
    """Как резать звук на окна: окно — от `min_s` до `max_s` секунд, резка
    в паузе между ними; копится больше `behind_s` (распознавание отстаёт) —
    окна до `merge_s`."""

    min_s: float
    max_s: float
    merge_s: float

    @property
    def behind_s(self) -> float:
        return 2 * self.max_s


GIGAAM_POLICY = WindowPolicy(min_s=4.0, max_s=7.0, merge_s=20.0)


def whisper_policy(window_seconds: float = 20.0) -> WindowPolicy:
    window = max(2.0, float(window_seconds))
    return WindowPolicy(min_s=0.75 * window, max_s=window, merge_s=window)


# Подряд столько сбоев GigaAM на окнах — дальше распознаёт Whisper.
GIGAAM_MAX_ERRORS = 3
SAMPLE_RATE = 16000


class GigaamLive:
    """GigaAM для живого режима (см. модуль). `fallback()` — Whisper на
    случай, если GigaAM не загрузилась или ломается на окнах; `model` и
    `vad` подменяются в тестах."""

    def __init__(self, model_name: str, device: str = "cpu", *, fallback=None,
                 model=None, vad=None, log=print) -> None:
        self.model_name = model_name
        self.device = device
        self._fallback_factory = fallback
        self._fallback = None
        self._model = model
        self._vad = vad
        self._log = log
        self._tmp: Path | None = None
        self._errors = 0
        self.policy = GIGAAM_POLICY
        self.name = "GigaAM"
        self.latin_pass = True

    @property
    def active(self):
        """Кто сейчас распознаёт: self или запасной Whisper."""
        return self._fallback or self

    def load(self) -> None:
        if self._model is None:
            from meet import gigaam_asr

            device = self.device
            if device == "cuda" and not _torch_cuda():
                device = "cpu"
            try:
                self._model = gigaam_asr.load(self.model_name, device)
                self.device = device
            except gigaam_asr.Unavailable as e:
                self._log(f"живой режим: {e} — распознаёт Whisper")
                self._switch()
                return
        self._tmp = Path(tempfile.mkdtemp(prefix="meet-live-gigaam-"))
        self._log(f"живой режим: распознаёт GigaAM ({self.model_name}, {self.device}), окна ~5 с")

    def _switch(self) -> None:
        if self._fallback_factory is None:
            raise RuntimeError("GigaAM недоступна, а запасного Whisper нет")
        self._drop_model()
        fallback = self._fallback_factory()
        fallback.load()
        self._fallback = fallback
        self.policy = getattr(fallback, "policy", None)  # None — окна Whisper движка
        self.name = getattr(fallback, "name", "Whisper")
        self.latin_pass = False

    def transcribe_window(self, audio, *, offset_s: float = 0.0, hotwords: str | None = None,
                          initial_prompt: str | None = None) -> list:
        if self._fallback is not None:
            return self._fallback.transcribe_window(audio, offset_s=offset_s, hotwords=hotwords,
                                                    initial_prompt=initial_prompt)
        try:
            segments = self._recognize(audio, offset_s)
        except Exception as e:
            self._errors += 1
            if self._errors >= GIGAAM_MAX_ERRORS and self._fallback_factory is not None:
                self._log(f"живой режим: GigaAM не распознаёт окна ({type(e).__name__}: {e}) — "
                          "дальше распознаёт Whisper")
                self._switch()
            raise
        self._errors = 0
        return segments

    def _recognize(self, audio, offset_s: float) -> list:
        import numpy as np

        from meet import gigaam_asr

        audio = np.asarray(audio, dtype=np.float32)
        if not len(audio) or not np.any(audio):
            return []
        vad = self._vad or gigaam_asr.speech_regions
        if not vad(audio, SAMPLE_RATE):
            return []  # без речи: GigaAM на шуме выдумывает слова
        tmp = self._tmp or Path(tempfile.gettempdir())
        path = tmp / f"w{os.getpid()}.wav"
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        gigaam_asr._write_wav(path, pcm, SAMPLE_RATE)
        result = self._model.transcribe(str(path), word_timestamps=True)
        chunk = gigaam_asr.Chunk(offset_s, offset_s + len(audio) / SAMPLE_RATE)
        words = gigaam_asr.words_of_chunk(chunk, getattr(result, "words", None))
        return gigaam_asr.to_segments(words)

    def _drop_model(self) -> None:
        model, self._model = self._model, None
        if model is not None:
            del model
            if self.device == "cuda":
                try:
                    import torch

                    torch.cuda.empty_cache()
                except Exception:
                    pass

    def unload(self) -> None:
        if self._fallback is not None:
            self._fallback.unload()
        self._drop_model()
        tmp, self._tmp = self._tmp, None
        if tmp is not None:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


def _torch_cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


class TextFixes:
    """Исправления текста каждой реплики живого режима, как в офлайн-проходе
    (`transcribe._fix_terms`): правила замены из настроек, после GigaAM — ещё
    возврат латинских терминов (`meet.translit`). Сбой правил не роняет окно."""

    def __init__(self, rules, terms, *, latin: bool) -> None:
        self._rules = list(rules or [])
        self._terms = list(terms or [])
        self.latin = latin

    @classmethod
    def from_settings(cls, extra_hotwords: str | None = None, *, latin: bool) -> "TextFixes":
        from meet import settings
        from meet.transcribe import _hotword_terms

        try:
            rules = list(settings.load().asr.replacements)
        except Exception:
            rules = []
        return cls(rules, _hotword_terms(extra_hotwords) if latin else [], latin=latin)

    def __call__(self, segments: list, *, latin: bool | None = None) -> list:
        if not segments:
            return segments
        try:
            if self._rules:
                from meet import textfix

                textfix.apply_rules(segments, self._rules)
            if (self.latin if latin is None else latin) and self._terms:
                from meet import translit

                translit.apply(segments, self._terms)
        except Exception as e:
            print(f"живой режим: правила текста пропущены ({type(e).__name__}: {e})")
        return segments


def pick(cfg=None, *, log=print):
    """Движок распознавания живого режима по настройкам (см. модуль)."""
    from meet import asr, gigaam_asr, settings

    if cfg is None:
        cfg = settings.load()

    def whisper():
        # Окна Whisper задаёт движок живого режима (`window_seconds`).
        return asr.Transcriber()

    if getattr(cfg.assist, "live_asr", "auto") == "whisper":
        log("живой режим: распознаёт Whisper (так в настройках)")
        return whisper()
    language = (cfg.asr.language or asr.DEFAULT_LANGUAGE).strip().lower()
    if language != "ru":
        log(f"живой режим: язык распознавания «{language}» — распознаёт Whisper")
        return whisper()
    if not gigaam_asr.installed():
        log("живой режим: GigaAM не установлена в движке — распознаёт Whisper")
        return whisper()
    name = cfg.asr.gigaam_model or gigaam_asr.MODEL_NAME
    if not gigaam_asr.downloaded(name):
        log("живой режим: модель GigaAM не скачана — распознаёт Whisper "
            "(скачать: «Движок и модели»)")
        return whisper()
    device = asr.resolve_device(cfg.asr.device)
    return GigaamLive(name, device, fallback=whisper, log=log)
