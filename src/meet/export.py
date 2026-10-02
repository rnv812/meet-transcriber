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


def contents(data: dict | None, doc: dict | None) -> list[tuple[float, str]]:
    """Главы анализа встречи (analysis.json) для «Содержания»: (начало, название).

    Анализ годится, если он сделан по этой расшифровке (отпечаток совпадает)
    или устарел, но сегментов столько же: правили текст, номера реплик те же
    (так же решает окно). Глава начинается с первой непустой реплики от её
    `start_i`; главы с одним началом и без названия пропускаются."""
    if not data or not isinstance(doc, dict) or not isinstance(doc.get("chapters"), list):
        return []
    from meet import analysis

    segments = data.get("segments") or []
    same = doc.get("fingerprint") == analysis.fingerprint(data)
    if not same and doc.get("segments") != len(segments):
        return []
    out: list[tuple[float, str]] = []
    for chapter in doc["chapters"]:
        if not isinstance(chapter, dict):
            continue
        title = " ".join(str(chapter.get("title") or "").split())
        start_i = chapter.get("start_i")
        if not title or not isinstance(start_i, int) or isinstance(start_i, bool) or start_i < 0:
            continue
        start = next((float(seg.get("start") or 0.0) for seg in segments[start_i:]
                      if isinstance(seg, dict) and not is_break(seg) and str(seg.get("text") or "").strip()),
                     None)
        if start is None or any(abs(start - t) < 1e-6 for t, _ in out):
            continue
        out.append((start, title))
    return sorted(out)


def chapters_of(folder, data: dict | None) -> list[tuple[float, str]]:
    """«Содержание» записи: главы её анализа встречи, если он подходит (см. contents)."""
    from meet import analysis

    return contents(data, analysis.read(folder)) if folder else []


def render(data: dict, fmt: str, date: str = "", chapters: list[tuple[float, str]] | None = None,
           category: str | None = None) -> str:
    """Транскрипт в формате `fmt`. `chapters` — «Содержание», `category` — имя
    категории встречи во frontmatter (только для md: у txt и srt блока
    метаданных нет)."""
    segments = [s for s in data.get("segments", []) if str(s.get("text", "")).strip()]
    title = data.get("title") or "Встреча"
    if fmt == "md":
        return to_markdown(title, md_segments(data), date, contents=chapters, category=category)
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
