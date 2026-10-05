"""Распознавание живого режима: GigaAM короткими окнами или Whisper.

Живой режим распознаёт речь окнами по ходу встречи (`meet.live.LiveEngine`).
От окна зависит, как скоро реплика появится в ленте и дойдёт до подсказок:

* **GigaAM** (русский) — окна ~5 с: окно уходит по первой паузе между
  словами после 5,5 с (не позже 7 с — тогда в самой тихой точке между 4 и
  7 с, `gigaam_asr.find_pause`/`quiet_cut`); окно распознаётся за доли
  секунды и на CPU (замер M10: ~0,47 с на окно ~5 с, 8 потоков).
  Распознавание отстало — окна до 20 с (меньше вызовов). Публичный API
  GigaAM: окно пишется во временный wav и уходит в
  `model.transcribe(path, word_timestamps=True)`; свой wav GigaAM читает в
  процессе, без запуска ffmpeg на каждое окно (`gigaam_asr.in_process_wav`).
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

from meet import tempdirs


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
                gigaam_asr.in_process_wav()  # окна — без процесса ffmpeg на каждое
                if device == "cpu":
                    _limit_cpu_threads()
            except gigaam_asr.Unavailable as e:
                self._log(f"живой режим: {e} — распознаёт Whisper")
                self._switch()
                return
        # С pid в имени (meet.tempdirs): ассистента убили — папку удалит резидент.
        self._tmp = Path(tempfile.mkdtemp(prefix=tempdirs.prefix("live-gigaam-")))
        self._warm_up()
        self._log(f"живой режим: распознаёт GigaAM ({self.model_name}, {self.device}), окна ~5 с")

    def _warm_up(self) -> None:
        """Первый вызов модели и VAD дорогой (загрузка ONNX, ffmpeg, кэши):
        платим его при старте, а не на первой реплике встречи."""
        try:
            import numpy as np

            from meet import gigaam_asr

            noise = (np.random.default_rng(0).normal(0, 0.05, SAMPLE_RATE)).astype(np.float32)
            (self._vad or gigaam_asr.speech_regions)(noise, SAMPLE_RATE)
            path = self._tmp / "warm.wav"
            gigaam_asr._write_wav(path, (noise * 32767).astype(np.int16), SAMPLE_RATE)
            self._model.transcribe(str(path), word_timestamps=True)
            path.unlink(missing_ok=True)
        except Exception:
            pass  # прогрев — только ускорение

    def _switch(self) -> None:
        if self._fallback_factory is None:
            raise RuntimeError("GigaAM недоступна, а запасного Whisper нет")
        self._drop_model()
        self._drop_tmp()  # окна GigaAM больше не пишутся
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
        try:
            result = self._model.transcribe(str(path), word_timestamps=True)
        finally:
            # Звук встречи во временной папке не лежит, даже если ассистента
            # потом убьют и unload() не случится.
            path.unlink(missing_ok=True)
        chunk = gigaam_asr.Chunk(offset_s, offset_s + len(audio) / SAMPLE_RATE)
        words = gigaam_asr.words_of_chunk(chunk, getattr(result, "words", None))
        return gigaam_asr.to_segments(words)

    def set_cpu_threads(self, n: int | None) -> None:
        """Потоки torch на CPU: `n` (догонялка — поменьше), None — обычные
        (CPU_THREADS). На видеокарте и у запасного Whisper — ничего."""
        if self.device != "cpu" or self._fallback is not None or self._model is None:
            return
        try:
            import torch

            torch.set_num_threads(max(1, int(n)) if n else _default_threads())
        except Exception:
            pass

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
        self._drop_tmp()

    def _drop_tmp(self) -> None:
        tmp, self._tmp = self._tmp, None
        if tmp is not None:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


# Потоков torch для GigaAM на CPU в живом режиме. Замер M10 (i9-13900H): 8
# потоков — 0,47 с на окно ~5 с, 14 — 0,45–0,55 с, но с редкими провалами,
# когда процессор занят и другим (звонок, задачи расшифровки).
CPU_THREADS = 8


def _default_threads() -> int:
    return max(1, min(CPU_THREADS, os.cpu_count() or CPU_THREADS))


def _limit_cpu_threads() -> None:
    try:
        import torch

        torch.set_num_threads(max(1, min(CPU_THREADS, torch.get_num_threads())))
    except Exception:
        pass


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


MODEL_MISSING = "модель распознавания не скачана — скачайте в «Движок и модели»"


class ModelMissing(RuntimeError):
    """Модели Whisper нет на диске. Живой режим её не качает: полтора
    гигабайта посреди старта — это минуты без единого признака хода, и
    резидент снял бы такой старт по таймауту этапа."""


def _on_disk(model_name: str) -> bool:
    from meet import models

    try:
        if Path(model_name).is_dir():
            return True
        if "/" not in model_name:
            return True  # короткое имя («medium»): где его кэш, знает faster-whisper
        return models.downloaded(model_name)
    except Exception:
        return True  # не знаем — не мешаем (качать будет faster-whisper)


MODEL_BROKEN = ("модель распознавания скачана не полностью или повреждена — скачайте её "
                "заново в «Движок и модели»")


def _without_download(load):
    """Загрузка Whisper в живом режиме — только с диска: Hugging Face на
    время загрузки «вне сети», и запасная попытка загрузчика (неполный кэш —
    докачать) тоже не уходит в сеть. Не загрузилась с диска — ModelMissing:
    качать посреди старта не будем."""
    def run():
        try:
            from huggingface_hub import constants
        except Exception:
            constants = None
        saved = getattr(constants, "HF_HUB_OFFLINE", None)
        if constants is not None:
            constants.HF_HUB_OFFLINE = True
        try:
            return load()
        except Exception as e:
            if not _looks_missing(e):
                raise  # CUDA, доступ, тип вычислений — как было: свои откаты и честный текст
            raise ModelMissing(MODEL_BROKEN) from e
        finally:
            if constants is not None:
                constants.HF_HUB_OFFLINE = saved
    return run


# Чем загрузчик говорит «файлов модели нет / не все»: hub без сети
# (LocalEntryNotFoundError, OfflineModeIsEnabled), ctranslate2 без model.bin.
_MISSING_TYPES = ("LocalEntryNotFoundError", "EntryNotFoundError", "OfflineModeIsEnabled",
                  "FileNotFoundError", "IsADirectoryError", "NotADirectoryError")
_MISSING_TEXT = ("unable to open file", "model.bin", "no such file", "does not exist",
                 "cannot find", "local_files_only", "offline mode", "not found in")


def _looks_missing(error: BaseException) -> bool:
    """Сбой загрузки — от нехватки файлов модели (а не CUDA, доступа, памяти)."""
    seen = 0
    while error is not None and seen < 5:
        if isinstance(error, PermissionError) or "memory" in str(error).lower():
            return False
        names = {cls.__name__ for cls in type(error).__mro__}
        if names & set(_MISSING_TYPES):
            return True
        text = str(error).lower()
        if any(mark in text for mark in _MISSING_TEXT):
            return True
        error = error.__cause__ or error.__context__
        seen += 1
    return False


def pick(cfg=None, *, log=print):
    """Движок распознавания живого режима по настройкам (см. модуль).
    Whisper, которого нет на диске, — ModelMissing (не качаем, см. выше)."""
    from meet import asr, gigaam_asr, settings

    if cfg is None:
        cfg = settings.load()

    def whisper():
        # Окна Whisper задаёт движок живого режима (`window_seconds`).
        model = asr.Transcriber()
        name = getattr(model, "model_name", None)
        if isinstance(name, str) and not _on_disk(name):
            raise ModelMissing(MODEL_MISSING)
        try:
            model.load = _without_download(model.load)
        except (AttributeError, TypeError):
            pass  # подделка в тестах
        return model

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
