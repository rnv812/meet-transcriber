"""Сравнение двух транскриптов одной записи: пословный диф атрибуции спикеров.

Инструмент A/B-замера (п.4 бэклога): прогнали запись двумя вариантами
пайплайна -> `meet compare a.md b.md` показывает, сколько слов сменило
спикера и где именно переслушивать.
"""
import difflib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Заголовок блока, как его пишет output.to_markdown: "## 12:34 — Имя".
# Разделитель — em-dash (в самом файле, не в консольном выводе).
_HEADER_RE = re.compile(r"^## (\d{1,2}:\d{2}(?::\d{2})?) — (.+)$")
_WORD_RE = re.compile(r"\w+")


@dataclass(frozen=True)
class Word:
    text: str      # нормализованное слово: нижний регистр, без пунктуации
    speaker: str
    start: float   # время начала блока, сек


def _parse_ts(ts: str) -> float:
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    return float(parts[0] * 3600 + parts[1] * 60 + parts[2])


def parse_transcript(path: Path) -> list[Word]:
    """Md-транскрипт (формат output.to_markdown) -> слова с атрибуцией.

    Всё до первого заголовка блока (frontmatter, "# Заголовок") пропускается.
    Не-транскрипт даёт пустой список — решает, ошибка ли это, вызывающий.
    """
    words: list[Word] = []
    speaker: str | None = None
    start = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _HEADER_RE.match(line)
        if m:
            start, speaker = _parse_ts(m.group(1)), m.group(2).strip()
            continue
        if speaker is None:
            continue
        for token in _WORD_RE.findall(line.lower()):
            words.append(Word(token, speaker, start))
    return words


ZONE_GAP = 5        # стабильных слов между сменами, при которых зоны склеиваются
SNIPPET_WORDS = 10  # слов из A в фрагменте зоны


@dataclass
class CompareResult:
    total_a: int
    total_b: int
    pairs: list[tuple[Word, Word]]  # сопоставленные слова (текст совпал)
    changed_idx: list[int]          # индексы pairs, где спикеры разошлись

    @property
    def matched(self) -> int:
        return len(self.pairs)

    @property
    def changed(self) -> int:
        return len(self.changed_idx)

    @property
    def unmatched(self) -> int:
        return (self.total_a - self.matched) + (self.total_b - self.matched)


@dataclass
class Zone:
    start: float       # время блока A первого сменившего спикера слова
    words: int         # сколько слов в зоне сменило спикера
    moves: list[str]   # уникальные "СпикерA -> СпикерB" в порядке появления
    snippet: str       # первые SNIPPET_WORDS слов зоны (по A)


def compare_words(words_a: list[Word], words_b: list[Word]) -> CompareResult:
    """Пословное выравнивание двух транскриптов по нормализованному тексту.

    autojunk=False: на длинных встречах difflib иначе выкидывает частые слова
    («ну», «да») из сопоставления как «мусор» — нам они нужны.
    """
    sm = difflib.SequenceMatcher(
        a=[w.text for w in words_a], b=[w.text for w in words_b], autojunk=False
    )
    pairs: list[tuple[Word, Word]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs.extend(zip(words_a[i1:i2], words_b[j1:j2]))
    changed_idx = [i for i, (a, b) in enumerate(pairs) if a.speaker != b.speaker]
    return CompareResult(len(words_a), len(words_b), pairs, changed_idx)


def build_zones(result: CompareResult) -> list[Zone]:
    """Подряд идущие смены спикера -> зоны; зазор <= ZONE_GAP стабильных
    слов склеивает соседние зоны в одну."""
    zones: list[Zone] = []
    group: list[int] = []
    for idx in result.changed_idx:
        if group and idx - group[-1] > ZONE_GAP + 1:
            zones.append(_make_zone(result.pairs, group))
            group = []
        group.append(idx)
    if group:
        zones.append(_make_zone(result.pairs, group))
    return zones


def _make_zone(pairs: list[tuple[Word, Word]], idx: list[int]) -> Zone:
    moves: list[str] = []
    for i in idx:
        a, b = pairs[i]
        move = f"{a.speaker} -> {b.speaker}"
        if move not in moves:
            moves.append(move)
    # Связный фрагмент от начала зоны (включая стабильные слова между сменами) —
    # по нему проще найти место на слух, чем по выдерганным словам.
    snippet = " ".join(p[0].text for p in pairs[idx[0] : idx[0] + SNIPPET_WORDS])
    return Zone(pairs[idx[0]][0].start, len(idx), moves, snippet)


def _fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def render_report(name_a: str, name_b: str, result: CompareResult) -> str:
    """Текстовый отчёт. IMPORTANT: только ASCII-пунктуация — cp866-консоль
    Windows падает на «→»/«—» (грабли приёмки enrollment)."""
    lines = [
        "Сравнение транскриптов:",
        f"  A: {name_a} - {result.total_a} слов",
        f"  B: {name_b} - {result.total_b} слов",
        f"  Сопоставлено: {result.matched}; текст разошёлся: {result.unmatched}",
    ]
    pct = 100.0 * result.changed / result.matched if result.matched else 0.0
    lines.append(f"  Сменили спикера: {result.changed} ({pct:.1f}% сопоставленных)")

    matrix = Counter((a.speaker, b.speaker) for a, b in result.pairs)
    rows = sorted({a for a, _ in matrix})
    cols = sorted({b for _, b in matrix})
    width = max((len(n) for n in rows + cols), default=0) + 2
    lines += ["", "Матрица (строки - A, столбцы - B):"]
    lines.append(" " * width + "".join(c.rjust(width) for c in cols))
    for r in rows:
        cells = "".join(str(matrix.get((r, c), 0)).rjust(width) for c in cols)
        lines.append(r.ljust(width) + cells)

    zones = build_zones(result)
    lines += ["", f"Зоны расхождения ({len(zones)}), переслушать:"]
    for z in zones:
        moves = ", ".join(z.moves)
        lines.append(f'  [{_fmt_ts(z.start)}] {moves}: {z.words} слов: "{z.snippet}"')
    return "\n".join(lines)
