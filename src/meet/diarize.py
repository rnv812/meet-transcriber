from meet.asr import Segment


def assign_speakers(
    segments: list[Segment], turns: list[tuple[float, float, str]]
) -> None:
    """Каждому сегменту — спикер с максимальным перекрытием по времени (in-place)."""
    for seg in segments:
        best, best_overlap = None, 0.0
        for start, end, label in turns:
            overlap = min(seg.end, end) - max(seg.start, start)
            if overlap > best_overlap:
                best, best_overlap = label, overlap
        seg.speaker = best
