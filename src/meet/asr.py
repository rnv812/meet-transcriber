from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[Word] = field(default_factory=list)
    no_speech_prob: float | None = None
    avg_logprob: float | None = None


def _add_nvidia_dll_dirs() -> None:
    """ctranslate2 на Windows ищет DLL cuBLAS/cuDNN; pip-пакеты nvidia-*
    кладут их в site-packages, откуда система их сама не находит."""
    import os
    import site

    for sp in site.getsitepackages():
        for bin_dir in (Path(sp) / "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))


def _segments_from_whisper(raw_segments, offset_s: float = 0.0) -> list[Segment]:
    """Сегменты faster-whisper → list[Segment]; offset_s переводит таймкоды окна
    (всегда от нуля) в абсолютное время встречи. no_speech_prob/avg_logprob
    сохраняются для фильтра галлюцинаций (drop_hallucinations)."""
    result: list[Segment] = []
    for s in raw_segments:
        words = [
            Word(w.start + offset_s, w.end + offset_s, w.word) for w in (s.words or [])
        ]
        result.append(
            Segment(
                s.start + offset_s,
                s.end + offset_s,
                s.text.strip(),
                words=words,
                no_speech_prob=getattr(s, "no_speech_prob", None),
                avg_logprob=getattr(s, "avg_logprob", None),
            )
        )
    return result


# Консервативный денилист известных артефактов Whisper из обучающих данных
# (титры/«спасибо за просмотр»), которых в реальной рабочей речи не бывает.
# Совпадение по конкретным многословным фразам / уникальным токенам
# (НЕ по голому слову «субтитры»), чтобы не выкинуть легитимную речь.
_HALLUCINATION_PHRASES = (
    "dimatorzok",
    "amara.org",
    "субтитры сделал",
    "субтитры создавал",
    "субтитры подготовил",
    "редактор субтитров",
    "спасибо за просмотр",
    "продолжение следует",
)


def drop_hallucinations(
    segments: list[Segment],
    *,
    max_no_speech: float = 0.6,
    min_avg_logprob: float = -1.0,
) -> list[Segment]:
    """Убрать пустые сегменты и похожие на галлюцинации Whisper на тишине/шуме:
    высокая вероятность «не речь» или слишком низкая средняя уверенность,
    а также сегменты с фразами из денилиста известных артефактов Whisper.
    None-метрики (офлайн-путь) по порогам не фильтруются; денилист — всегда."""
    kept: list[Segment] = []
    for s in segments:
        if not s.text:
            continue
        if s.no_speech_prob is not None and s.no_speech_prob > max_no_speech:
            continue
        if s.avg_logprob is not None and s.avg_logprob < min_avg_logprob:
            continue
        # денилист независим от метрик: сработает даже при None-метриках
        norm = s.text.lower().strip()
        if any(phrase in norm for phrase in _HALLUCINATION_PHRASES):
            continue
        kept.append(s)
    return kept


def transcribe_wav(path: Path, hotwords: str | None = None) -> list[Segment]:
    """Распознать русскую речь; при нехватке видеопамяти — квантованная модель.

    Пословные таймкоды нужны для точной привязки спикеров.
    condition_on_previous_text оставлен включённым (по умолчанию): проверка
    на реальной встрече показала, что без него пунктуация и термины заметно
    деградируют, а зацикливаний и так не было благодаря vad_filter."""
    _add_nvidia_dll_dirs()
    from faster_whisper import WhisperModel

    last_error: Exception | None = None
    for compute_type in ("float16", "int8_float16"):
        try:
            model = WhisperModel("large-v3", device="cuda", compute_type=compute_type)
            print(f"Распознавание ({compute_type})...")
            segments, _ = model.transcribe(
                str(path),
                language="ru",
                vad_filter=True,
                word_timestamps=True,
                hotwords=hotwords,
            )
            result = _segments_from_whisper(segments)
            del model
            return result
        except RuntimeError as e:
            if "memory" not in str(e).lower():
                raise
            last_error = e
            print(f"Не хватило видеопамяти ({compute_type}), пробую компактнее...")
            import torch

            torch.cuda.empty_cache()
    raise SystemExit(f"Модель не загрузилась даже в int8: {last_error}")


class Transcriber:
    """Резидентная модель Whisper для живого режима: грузится один раз,
    расшифровывает окна аудио без перезагрузки на каждый вызов."""

    def __init__(self) -> None:
        self._model = None
        self.compute_type: str | None = None

    def load(self) -> None:
        _add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel

        last_error: Exception | None = None
        for compute_type in ("float16", "int8_float16"):
            try:
                self._model = WhisperModel(
                    "large-v3", device="cuda", compute_type=compute_type
                )
                self.compute_type = compute_type
                print(f"Модель загружена ({compute_type})")
                return
            except RuntimeError as e:
                if "memory" not in str(e).lower():
                    raise
                last_error = e
                print(f"Не хватило видеопамяти ({compute_type}), пробую компактнее...")
                import torch

                torch.cuda.empty_cache()
        raise SystemExit(f"Модель не загрузилась даже в int8: {last_error}")

    def transcribe_window(
        self,
        audio,
        *,
        offset_s: float = 0.0,
        hotwords: str | None = None,
        initial_prompt: str | None = None,
    ) -> list[Segment]:
        if self._model is None:
            raise RuntimeError("Transcriber.load() не был вызван")
        segments, _ = self._model.transcribe(
            audio,
            language="ru",
            vad_filter=True,
            word_timestamps=True,
            hotwords=hotwords,
            initial_prompt=initial_prompt,
        )
        return _segments_from_whisper(segments, offset_s)

    def unload(self) -> None:
        self._model = None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:
            pass
