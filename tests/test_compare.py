from pathlib import Path

from meet.compare import Word, parse_transcript

SAMPLE = """---
date: 2026-07-02
task:
type: transcript
tags: [claude-generated, transcript]
---

# Встреча — 02.07.2026

## 00:03 — Гена

Привет, начнём.

## 01:02:03 — Вы

Да, поехали дальше,
продолжение реплики.
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "t.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_skips_frontmatter_and_title(tmp_path):
    words = parse_transcript(_write(tmp_path, SAMPLE))
    # ни date, ни transcript, ни слов заголовка в результате нет
    assert all(w.speaker in ("Гена", "Вы") for w in words)


def test_parse_words_lowercase_no_punct(tmp_path):
    words = parse_transcript(_write(tmp_path, SAMPLE))
    assert [w.text for w in words if w.speaker == "Гена"] == ["привет", "начнём"]


def test_parse_block_attribution_and_time(tmp_path):
    words = parse_transcript(_write(tmp_path, SAMPLE))
    first = words[0]
    assert first == Word("привет", "Гена", 3.0)
    you = [w for w in words if w.speaker == "Вы"]
    assert you[0].start == 3723.0  # 01:02:03
    # многострочный текст блока попадает целиком
    assert [w.text for w in you] == ["да", "поехали", "дальше", "продолжение", "реплики"]


def test_parse_not_a_transcript_returns_empty(tmp_path):
    words = parse_transcript(_write(tmp_path, "просто текст\nбез блоков\n"))
    assert words == []
