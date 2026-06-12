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


def _add_nvidia_dll_dirs() -> None:
    """ctranslate2 на Windows ищет DLL cuBLAS/cuDNN; pip-пакеты nvidia-*
    кладут их в site-packages, откуда система их сама не находит."""
    import os
    import site

    for sp in site.getsitepackages():
        for bin_dir in (Path(sp) / "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))


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
            result = [
                Segment(
                    s.start,
                    s.end,
                    s.text.strip(),
                    words=[Word(w.start, w.end, w.word) for w in (s.words or [])],
                )
                for s in segments
            ]
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
