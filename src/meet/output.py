from meet.asr import Segment


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def merge_consecutive(segments: list[Segment], max_gap: float = 2.0) -> list[Segment]:
    """Склеить подряд идущие сегменты одного спикера с паузой не больше max_gap сек."""
    merged: list[Segment] = []
    for seg in segments:
        last = merged[-1] if merged else None
        if last and last.speaker == seg.speaker and seg.start - last.end <= max_gap:
            last.text = f"{last.text} {seg.text}"
            last.end = seg.end
        else:
            merged.append(Segment(seg.start, seg.end, seg.text, seg.speaker))
    return merged


def speaker_names(segments: list[Segment]) -> dict[str, str]:
    """SPEAKER_XX → «Спикер N» в порядке первого появления; прочие метки не трогаем."""
    names: dict[str, str] = {}
    for seg in segments:
        if seg.speaker and seg.speaker.startswith("SPEAKER_") and seg.speaker not in names:
            names[seg.speaker] = f"Спикер {len(names) + 1}"
    return names


def to_markdown(title: str, segments: list[Segment]) -> str:
    names = speaker_names(segments)
    duration_min = int(segments[-1].end // 60) if segments else 0
    lines = [f"# {title}", f"Длительность: {duration_min} мин", ""]
    for seg in merge_consecutive(segments):
        who = names.get(seg.speaker, seg.speaker) if seg.speaker else "Спикер ?"
        lines.append(f"**[{fmt_ts(seg.start)}] {who}:** {seg.text}")
        lines.append("")
    return "\n".join(lines)
