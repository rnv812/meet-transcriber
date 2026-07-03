"""Сравнение двух транскриптов одной записи: пословный диф атрибуции спикеров.

Инструмент A/B-замера (п.4 бэклога): прогнали запись двумя вариантами
пайплайна -> `meet compare a.md b.md` показывает, сколько слов сменило
спикера и где именно переслушивать.
"""
import re
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
