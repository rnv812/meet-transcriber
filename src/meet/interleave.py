"""Слияние двух дорожек с разрезом на перебиваниях: реплика, прозвучавшая
внутри длинного сегмента другой дорожки, должна встать по месту звучания,
а не после всего сегмента (иначе саммари видит ответ не на то место диалога).
См. спеку docs/superpowers/specs/2026-07-02-mic-resolver-design.md."""

from meet.asr import Segment, Word


def interleave_tracks(a: list[Segment], b: list[Segment]) -> list[Segment]:
    """Общий список двух дорожек с точным порядком на перебиваниях.

    Сегменты каждой дорожки режутся по началам сегментов другой; тай-брейк
    по end ставит короткую реплику раньше её продолжения при равных start."""
    a_cut = [part for seg in a for part in _cut_segment(seg, [s.start for s in b])]
    b_cut = [part for seg in b for part in _cut_segment(seg, [s.start for s in a])]
    return sorted(a_cut + b_cut, key=lambda s: (s.start, s.end))


def _cut_segment(seg: Segment, times: list[float]) -> list[Segment]:
    """Разрезать сегмент по точкам times, попавшим строго внутрь него.

    Слово с word.start < t уходит в левую часть. Часть без слов не создаётся;
    сегмент без пословных таймкодов не режется."""
    if not seg.words:
        return [seg]
    cuts = sorted(t for t in times if seg.start < t < seg.end)
    if not cuts:
        return [seg]
    parts: list[Segment] = []
    words = list(seg.words)
    for t in cuts:
        left = [w for w in words if w.start < t]
        right = [w for w in words if w.start >= t]
        if left and right:
            parts.append(_words_to_segment(left, seg.speaker))
            words = right
    parts.append(_words_to_segment(words, seg.speaker))
    return parts


def _words_to_segment(words: list[Word], speaker: str | None) -> Segment:
    text = "".join(w.text for w in words).strip()
    return Segment(words[0].start, words[-1].end, text, speaker, words=words)
