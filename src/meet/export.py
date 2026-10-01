"""Транскрипт записи → файлы для людей: Markdown (формат проекта), текст, SRT.

Источник — transcript.json (структурный), а не .md: имена спикеров после
переименования в окне живут там."""

from meet.asr import Segment
from meet.output import to_markdown

FORMATS = ("md", "txt", "srt")


def _hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{_hms(ms // 1000)},{ms % 1000:03d}"


def _who(seg: dict) -> str:
    who = seg.get("speaker") or "Спикер ?"
    return f"{who} (нахлёст)" if seg.get("uncertain") else who


def safe_filename(title: str, fallback: str) -> str:
    """Sanitize a filename: replace dangerous characters, strip whitespace.

    Replaces Windows-unsafe characters (\\, /, :, *, ?, ", <, >, |) and control
    chars (\\x00-\\x1f) with underscore. Strips surrounding whitespace and trailing
    dots/spaces. Returns fallback if result is empty."""
    if not title:
        return fallback
    # Replace dangerous characters with underscore
    dangerous = set('\\/:<>"|?') | set(chr(i) for i in range(0x00, 0x20))
    safe = ''.join(c if c not in dangerous else '_' for c in title)
    # Strip whitespace from both ends
    safe = safe.strip()
    # Remove trailing dots and spaces (Windows filename issue)
    safe = safe.rstrip('. ')
    return safe if safe else fallback


def md_segments(data: dict) -> list[Segment]:
    """Непустые реплики транскрипта как Segment — вход для output.to_markdown."""
    return [Segment(float(s["start"]), float(s["end"]), s["text"],
                    speaker=s.get("speaker"), uncertain=bool(s.get("uncertain")))
            for s in data.get("segments", []) if str(s.get("text", "")).strip()]


def render(data: dict, fmt: str, date: str = "") -> str:
    segments = [s for s in data.get("segments", []) if str(s.get("text", "")).strip()]
    title = data.get("title") or "Встреча"
    if fmt == "md":
        return to_markdown(title, md_segments(data), date)
    if fmt == "txt":
        lines = [title, ""]
        lines += [f"[{_hms(float(s['start']))}] {_who(s)}: {s['text'].strip()}"
                  for s in segments]
        return "\n".join(lines) + "\n"
    if fmt == "srt":
        blocks = [
            f"{i}\n{_srt_time(float(s['start']))} --> {_srt_time(float(s['end']))}\n"
            f"{_who(s)}: {s['text'].strip()}"
            for i, s in enumerate(segments, 1)
        ]
        return ("\n\n".join(blocks) + "\n") if blocks else ""
    raise ValueError(f"формат {fmt} не поддерживается")
