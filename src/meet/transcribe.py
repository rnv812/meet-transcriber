import tempfile
from pathlib import Path

from meet.asr import Segment, transcribe_wav
from meet.audio import to_wav16k
from meet.diarize import assign_speakers, diarize_wav
from meet.output import to_markdown


def transcribe(path_str: str) -> Path:
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"Не найдено: {path}")

    if path.is_dir():
        segments = _transcribe_two_track(path)
        out_md = path / "transcript.md"
        title = f"Встреча {path.name}"
    else:
        segments = _transcribe_single(path)
        out_md = path.with_suffix(".md")
        title = f"Встреча: {path.stem}"

    out_md.write_text(to_markdown(title, segments), encoding="utf-8")
    print(f"Готово: {out_md}")
    return out_md


def _transcribe_single(src: Path) -> list[Segment]:
    with tempfile.TemporaryDirectory() as td:
        wav = to_wav16k(src, Path(td) / "audio16.wav")
        segments = transcribe_wav(wav)
        assign_speakers(segments, diarize_wav(wav))
    return segments


def _transcribe_two_track(folder: Path) -> list[Segment]:
    sys_src, mic_src = folder / "sys.wav", folder / "mic.wav"
    if not (sys_src.exists() and mic_src.exists()):
        raise SystemExit(f"В {folder} нет sys.wav и mic.wav")
    with tempfile.TemporaryDirectory() as td:
        sys_wav = to_wav16k(sys_src, Path(td) / "sys16.wav")
        mic_wav = to_wav16k(mic_src, Path(td) / "mic16.wav")
        sys_segs = transcribe_wav(sys_wav)
        assign_speakers(sys_segs, diarize_wav(sys_wav))
        mic_segs = transcribe_wav(mic_wav)
        for seg in mic_segs:
            seg.speaker = "Вы"
    return sorted(sys_segs + mic_segs, key=lambda s: s.start)
