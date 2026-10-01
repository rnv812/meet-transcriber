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


# Не длиннее этого — каждая часть пути: глубокое хранилище плюс длинное
# название встречи иначе упирается в MAX_PATH (260) Windows.
MAX_NAME_CHARS = 120
# Имена устройств Windows: файл или папку с таким именем (и с любым
# расширением — «CON.md») создать нельзя.
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL"}
                      | {f"COM{i}" for i in range(1, 10)}
                      | {f"LPT{i}" for i in range(1, 10)})
_DANGEROUS = frozenset('\\/:*?"<>|') | frozenset(chr(i) for i in range(0x00, 0x20))


def safe_filename(title: str, fallback: str) -> str:
    """Имя файла или папки, которое можно создать на Windows.

    Опасные символы (\\ / : * ? " < > | и управляющие) — «_»; пробелы по краям
    и точки/пробелы в конце убираются; не длиннее MAX_NAME_CHARS; имя
    устройства (CON, NUL, COM1, LPT1… — и с расширением) получает «_» в конце
    основы. Пусто после очистки — `fallback`."""
    if not title:
        return fallback
    safe = "".join("_" if c in _DANGEROUS else c for c in title).strip()
    safe = safe[:MAX_NAME_CHARS].rstrip(". ")
    if not safe:
        return fallback
    stem, dot, rest = safe.partition(".")
    if stem.rstrip(" ").upper() in _RESERVED:
        safe = f"{stem.rstrip(' ')}_{dot}{rest}"
    return safe


def md_segments(data: dict) -> list[Segment]:
    """Непустые реплики транскрипта как Segment — вход для output.to_markdown."""
    return [Segment(float(s["start"]), float(s["end"]), s["text"],
                    speaker=s.get("speaker"), uncertain=bool(s.get("uncertain")),
                    kind="break" if is_break(s) else None)
            for s in data.get("segments", []) if str(s.get("text", "")).strip()]


def is_break(seg: dict) -> bool:
    """Отметка перерыва объединённой встречи (meet.merge), не реплика."""
    return isinstance(seg, dict) and seg.get("kind") == "break"


def render(data: dict, fmt: str, date: str = "") -> str:
    segments = [s for s in data.get("segments", []) if str(s.get("text", "")).strip()]
    title = data.get("title") or "Встреча"
    if fmt == "md":
        return to_markdown(title, md_segments(data), date)
    if fmt == "txt":
        lines = [title, ""]
        lines += [s["text"].strip() if is_break(s)
                  else f"[{_hms(float(s['start']))}] {_who(s)}: {s['text'].strip()}"
                  for s in segments]
        return "\n".join(lines) + "\n"
    if fmt == "srt":
        # Субтитры — только речь: отметке перерыва нечего показывать на экране.
        segments = [s for s in segments if not is_break(s)]
        blocks = [
            f"{i}\n{_srt_time(float(s['start']))} --> {_srt_time(float(s['end']))}\n"
            f"{_who(s)}: {s['text'].strip()}"
            for i, s in enumerate(segments, 1)
        ]
        return ("\n\n".join(blocks) + "\n") if blocks else ""
    raise ValueError(f"формат {fmt} не поддерживается")
