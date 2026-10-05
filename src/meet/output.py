import re

from meet.asr import Segment


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


# Пометка неуверенной подписи (`uncertain`): у собеседников — нахлёст
# спикеров (диаризация), у микрофона — голос между «точно владелец» и «точно
# не он» (meet.mic_split, роль unsure): подписан владельцем, но под вопросом.
OVERLAP_MARK = "нахлёст"
UNSURE_VOICE_MARK = "голос под вопросом"


def uncertain_mark(uncertain: bool, track: str | None) -> str | None:
    if not uncertain:
        return None
    return UNSURE_VOICE_MARK if track == "mic" else OVERLAP_MARK


def merge_consecutive(segments: list[Segment], max_gap: float = 2.0) -> list[Segment]:
    """Склеить подряд идущие сегменты одного спикера с паузой не больше max_gap сек.

    Блоки с разной пометкой (uncertain_mark) не клеятся: зона нахлёста и голос
    под вопросом остаются отдельными блоками.
    Отметка перерыва (kind="break") — сама по себе и не склеивает соседей."""
    merged: list[Segment] = []
    for seg in segments:
        last = merged[-1] if merged else None
        if (
            last
            and not last.kind
            and not seg.kind
            and last.speaker == seg.speaker
            and uncertain_mark(last.uncertain, last.track) == uncertain_mark(seg.uncertain, seg.track)
            and seg.start - last.end <= max_gap
        ):
            last.text = f"{last.text} {seg.text}"
            last.end = seg.end
        else:
            merged.append(
                Segment(
                    seg.start, seg.end, seg.text, seg.speaker, uncertain=seg.uncertain,
                    kind=seg.kind, track=seg.track,
                )
            )
    return merged


def speaker_names(segments: list[Segment]) -> dict[str, str]:
    """SPEAKER_XX → «Спикер N» в порядке первого появления; прочие метки не трогаем."""
    names: dict[str, str] = {}
    for seg in segments:
        if seg.speaker and seg.speaker.startswith("SPEAKER_") and seg.speaker not in names:
            names[seg.speaker] = f"Спикер {len(names) + 1}"
    return names


def to_markdown(title: str, segments: list[Segment], date: str = "",
                contents: list[tuple[float, str]] | None = None, category: str | None = None) -> str:
    """Транскрипт в Markdown для базы заметок: «## ВРЕМЯ — Спикер».

    Если задан date (ISO YYYY-MM-DD), сверху добавляется frontmatter для Obsidian
    с пустым task (заполняется вручную при переносе в хранилище) и категорией
    встречи (`category` — её имя), если она есть. `contents` — главы анализа
    встречи (начало, название): раздел «Содержание» под заголовком.
    """
    lines: list[str] = []
    if date:
        lines += [
            "---",
            f"date: {date}",
            "task:",
            "type: transcript",
            *([f"category: {yaml_text(category)}"] if category else []),
            "tags: [claude-generated, transcript]",
            "---",
            "",
        ]
    lines += [f"# {title}", ""]
    if contents:
        lines += ["## Содержание", ""]
        lines += [f"- {fmt_ts(start)} — {name}" for start, name in contents]
        lines += [""]
    lines += turn_lines(segments)
    return "\n".join(lines)


_YAML_SPECIAL = frozenset(":#[]{},&*!|>'\"%@`")
# Простые значения, которые YAML прочтёт не строкой: null, логические (YAML 1.1
# и 1.2), числа (в т. ч. 0x1F, .inf, 1_000), даты.
_YAML_RESERVED = re.compile(
    r"(?i)(?:null|~|true|false|yes|no|on|off|y|n"
    r"|[-+]?(?:\.?\d[\d_]*(?:\.[\d_]*)?(?:e[-+]?\d+)?|0x[\da-f_]+|0o[0-7_]+|\.inf|\.nan)"
    r"|\d{4}-\d\d?-\d\d?(?:[tT ].*)?)")


def yaml_text(text: str) -> str:
    """Строка для frontmatter: простая — как есть, со спецсимволами YAML — в
    кавычках (JSON-строка — правильная YAML-строка)."""
    import json

    text = " ".join(str(text).split())
    plain = (text and not any(c in _YAML_SPECIAL for c in text) and text[0] not in "-?"
             and not _YAML_RESERVED.fullmatch(text))
    return text if plain else json.dumps(text, ensure_ascii=False)


def turn_lines(segments: list[Segment], level: int = 2) -> list[str]:
    """Реплики в формате to_markdown: «## ВРЕМЯ — Спикер», пустая строка, текст.
    `level` — уровень заголовка реплики (в заметке над ними стоят разделы итогов)."""
    names = speaker_names(segments)
    hashes = "#" * level
    lines: list[str] = []
    for seg in merge_consecutive(segments):
        if seg.kind == "break":  # перерыв объединённой встречи — разделитель
            lines += [f"*{seg.text}*", ""]
            continue
        who = names.get(seg.speaker, seg.speaker) if seg.speaker else "Спикер ?"
        flag = uncertain_mark(seg.uncertain, seg.track)
        mark = f" ({flag})" if flag else ""
        lines += [f"{hashes} {fmt_ts(seg.start)} — {who}{mark}", "", seg.text, ""]
    return lines
