from meet.asr import Segment


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def merge_consecutive(segments: list[Segment], max_gap: float = 2.0) -> list[Segment]:
    """Склеить подряд идущие сегменты одного спикера с паузой не больше max_gap сек.

    Блоки с разным uncertain не клеятся: зона нахлёста остаётся отдельным блоком."""
    merged: list[Segment] = []
    for seg in segments:
        last = merged[-1] if merged else None
        if (
            last
            and last.speaker == seg.speaker
            and last.uncertain == seg.uncertain
            and seg.start - last.end <= max_gap
        ):
            last.text = f"{last.text} {seg.text}"
            last.end = seg.end
        else:
            merged.append(
                Segment(
                    seg.start, seg.end, seg.text, seg.speaker, uncertain=seg.uncertain
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


def to_markdown(title: str, segments: list[Segment], date: str = "") -> str:
    """Транскрипт в формате режима transcript скилла notes-vault: «## ВРЕМЯ — Спикер».

    Если задан date (ISO YYYY-MM-DD), сверху добавляется frontmatter для Obsidian
    с пустым task (заполняется вручную при переносе в хранилище).
    """
    names = speaker_names(segments)
    lines: list[str] = []
    if date:
        lines += [
            "---",
            f"date: {date}",
            "task:",
            "type: transcript",
            "tags: [claude-generated, transcript]",
            "---",
            "",
        ]
    lines += [f"# {title}", ""]
    for seg in merge_consecutive(segments):
        who = names.get(seg.speaker, seg.speaker) if seg.speaker else "Спикер ?"
        mark = " (нахлёст)" if seg.uncertain else ""
        lines += [f"## {fmt_ts(seg.start)} — {who}{mark}", "", seg.text, ""]
    return "\n".join(lines)
