from pathlib import Path

import pytest

from meet.compare import (
    Word,
    build_zones,
    compare_words,
    parse_transcript,
    render_report,
    run_compare,
)

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


def _words(pairs: list[tuple[str, str]], start: float = 0.0) -> list:
    """[('текст', 'спикер'), ...] -> list[Word]; каждому слову своё время."""
    return [Word(t, s, start + i) for i, (t, s) in enumerate(pairs)]


def test_compare_counts_speaker_changes():
    a = _words([("раз", "Ева"), ("два", "Ева"), ("три", "Ева")])
    b = _words([("раз", "Ева"), ("два", "Гена"), ("три", "Ева")])
    res = compare_words(a, b)
    assert res.matched == 3
    assert res.changed == 1
    assert res.unmatched == 0
    assert res.pairs[1][0].text == "два"


def test_compare_text_mismatch_not_counted_as_change():
    a = _words([("раз", "Ева"), ("кот", "Ева"), ("три", "Ева")])
    b = _words([("раз", "Ева"), ("пёс", "Гена"), ("три", "Ева")])
    res = compare_words(a, b)
    assert res.matched == 2      # "раз" и "три"
    assert res.changed == 0      # разный текст — не смена спикера
    assert res.unmatched == 2    # "кот" из A + "пёс" из B


def test_zones_merge_within_gap():
    # две смены спикера через 3 стабильных слова (<= ZONE_GAP=5) -> одна зона
    stable = [(f"с{i}", "Ева") for i in range(3)]
    a = _words([("x", "Ева")] + stable + [("y", "Ева")])
    b = _words([("x", "Гена")] + stable + [("y", "Гена")])
    zones = build_zones(compare_words(a, b))
    assert len(zones) == 1
    assert zones[0].words == 2
    assert zones[0].moves == ["Ева -> Гена"]


def test_zones_split_beyond_gap():
    # смены через 6 стабильных слов (> ZONE_GAP=5) -> две зоны
    stable = [(f"с{i}", "Ева") for i in range(6)]
    a = _words([("x", "Ева")] + stable + [("y", "Ева")])
    b = _words([("x", "Гена")] + stable + [("y", "Гена")])
    zones = build_zones(compare_words(a, b))
    assert len(zones) == 2


def test_zone_snippet_and_start():
    a = _words([("привет", "Ева"), ("как", "Ева"), ("дела", "Ева")], start=100.0)
    b = _words([("привет", "Зоя"), ("как", "Зоя"), ("дела", "Зоя")])
    zone = build_zones(compare_words(a, b))[0]
    assert zone.start == 100.0            # время из A
    assert zone.snippet == "привет как дела"


def test_report_is_cp866_safe_and_has_sections():
    a = _words([("раз", "Ева"), ("два", "Ева")])
    b = _words([("раз", "Ева"), ("два", "Гена")])
    text = render_report("a.md", "b.md", compare_words(a, b))
    text.encode("cp866")                  # регрессия ловушки cp866
    assert "Сменили спикера: 1" in text
    assert "Ева -> Гена" in text        # и в матрице/зонах только ASCII-стрелки


TWO_BLOCKS = """# Встреча

## 00:00 — Ева

Привет, начнём работу.

## 00:10 — Гена

Да, поехали.
"""


def test_run_compare_prints_report(tmp_path, capsys):
    p = _write(tmp_path, TWO_BLOCKS)
    run_compare(str(p), str(p))
    out = capsys.readouterr().out
    assert "Сменили спикера: 0" in out
    assert "Похоже" not in out  # предупреждение не печатается на той же записи


def test_run_compare_rejects_missing_file(tmp_path):
    p = _write(tmp_path, TWO_BLOCKS)
    with pytest.raises(SystemExit, match="Не найден файл"):
        run_compare(str(tmp_path / "нет.md"), str(p))


def test_run_compare_rejects_non_transcript(tmp_path):
    good = _write(tmp_path, TWO_BLOCKS)
    bad = tmp_path / "bad.md"
    bad.write_text("просто текст без блоков", encoding="utf-8")
    with pytest.raises(SystemExit, match="Не транскрипт текущего формата"):
        run_compare(str(good), str(bad))


def test_run_compare_warns_on_different_meetings(tmp_path, capsys):
    a = _write(tmp_path, TWO_BLOCKS)
    other = tmp_path / "other.md"
    other.write_text(
        "# Другая\n\n## 00:00 — Зоя\n\nСовершенно иные слова тут звучат.\n",
        encoding="utf-8",
    )
    run_compare(str(a), str(other))
    out = capsys.readouterr().out
    assert "Похоже, это транскрипты разных записей" in out
