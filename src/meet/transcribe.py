import re
import tempfile
from datetime import datetime
from pathlib import Path

from meet.asr import Segment, transcribe_wav
from meet.audio import to_wav16k
from meet.diarize import diarize_wav, split_by_speaker
from meet.output import to_markdown


HOTWORDS_FILE = Path("hotwords.txt")


def _load_hotwords(extra: str | None, path: Path = HOTWORDS_FILE) -> str | None:
    """Подсказка лексики для распознавания: накопительный список из hotwords.txt
    (по термину на строку, # — комментарий) плюс разовые термины из --hotwords.

    Возвращает термины через запятую (как ждёт faster-whisper) или None.
    """
    terms: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.split("#", 1)[0].strip()
            if term:
                terms.append(term)
    if extra:
        terms += [t.strip() for t in extra.split(",") if t.strip()]
    seen: dict[str, None] = dict.fromkeys(terms)  # дедуп с сохранением порядка
    return ", ".join(seen) if seen else None


def _folder_dates(name: str) -> tuple[str, str]:
    """Дата из имени папки записи (recorder именует папки YYYY-MM-DD_...).

    → (ISO YYYY-MM-DD, ДД.ММ.ГГГГ); если префикс не распознан — текущая дата.
    """
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", name)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}", f"{d}.{mo}.{y}"
    dt = datetime.now()
    return f"{dt:%Y-%m-%d}", f"{dt:%d.%m.%Y}"


def _file_dates(p: Path) -> tuple[str, str]:
    dt = datetime.fromtimestamp(p.stat().st_mtime)
    return f"{dt:%Y-%m-%d}", f"{dt:%d.%m.%Y}"


def transcribe(
    path_str: str,
    speakers: int | None = None,
    hotwords: str | None = None,
) -> Path:
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"Не найдено: {path}")

    hotwords = _load_hotwords(hotwords)

    if path.is_dir():
        segments = _transcribe_two_track(path, speakers, hotwords)
        iso, dmy = _folder_dates(path.name)
        out_md = path / f"{iso}_transcript.md"
        title = f"Встреча — {dmy}"
    else:
        segments = _transcribe_single(path, speakers, hotwords)
        iso, dmy = _file_dates(path)
        out_md = path.with_suffix(".md")
        title = f"{path.stem} — {dmy}"

    out_md.write_text(to_markdown(title, segments, iso), encoding="utf-8")
    print(f"Готово: {out_md}")
    return out_md


def _transcribe_single(
    src: Path, speakers: int | None, hotwords: str | None
) -> list[Segment]:
    with tempfile.TemporaryDirectory() as td:
        wav = to_wav16k(src, Path(td) / "audio16.wav")
        segments = transcribe_wav(wav, hotwords)
        segments = split_by_speaker(segments, diarize_wav(wav, num_speakers=speakers))
    return segments


def _transcribe_two_track(
    folder: Path, speakers: int | None, hotwords: str | None
) -> list[Segment]:
    sys_src, mic_src = folder / "sys.wav", folder / "mic.wav"
    if not (sys_src.exists() and mic_src.exists()):
        raise SystemExit(f"В {folder} нет sys.wav и mic.wav")
    with tempfile.TemporaryDirectory() as td:
        sys_wav = to_wav16k(sys_src, Path(td) / "sys16.wav")
        mic_wav = to_wav16k(mic_src, Path(td) / "mic16.wav")
        sys_segs = transcribe_wav(sys_wav, hotwords)
        sys_segs = split_by_speaker(
            sys_segs, diarize_wav(sys_wav, num_speakers=speakers)
        )
        mic_segs = transcribe_wav(mic_wav, hotwords)
        for seg in mic_segs:
            seg.speaker = "Вы"
    return sorted(sys_segs + mic_segs, key=lambda s: s.start)
