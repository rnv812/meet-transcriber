from meet.asr import Segment
from meet.output import fmt_ts, merge_consecutive, speaker_names, to_markdown


def test_fmt_ts():
    assert fmt_ts(0) == "00:00"
    assert fmt_ts(75) == "01:15"
    assert fmt_ts(3725.9) == "01:02:05"


def test_merge_consecutive_glues_same_speaker():
    segs = [
        Segment(0.0, 2.0, "Привет,", "SPEAKER_00"),
        Segment(2.5, 4.0, "коллеги.", "SPEAKER_00"),
        Segment(4.5, 6.0, "Добрый день.", "SPEAKER_01"),
    ]
    merged = merge_consecutive(segs, max_gap=2.0)
    assert len(merged) == 2
    assert merged[0].text == "Привет, коллеги."
    assert merged[0].end == 4.0


def test_merge_consecutive_respects_gap():
    segs = [
        Segment(0.0, 2.0, "Раз.", "SPEAKER_00"),
        Segment(10.0, 11.0, "Два.", "SPEAKER_00"),
    ]
    assert len(merge_consecutive(segs, max_gap=2.0)) == 2


def test_merge_does_not_mutate_input():
    segs = [
        Segment(0.0, 2.0, "Раз.", "SPEAKER_00"),
        Segment(2.5, 3.0, "Два.", "SPEAKER_00"),
    ]
    merge_consecutive(segs, max_gap=2.0)
    assert segs[0].text == "Раз." and segs[0].end == 2.0


def test_speaker_names_in_order_of_appearance():
    segs = [
        Segment(0, 1, "а", "SPEAKER_03"),
        Segment(1, 2, "б", "Вы"),
        Segment(2, 3, "в", "SPEAKER_00"),
        Segment(3, 4, "г", "SPEAKER_03"),
    ]
    assert speaker_names(segs) == {"SPEAKER_03": "Спикер 1", "SPEAKER_00": "Спикер 2"}


def test_to_markdown():
    segs = [
        Segment(12.0, 14.0, "Коллеги, начнём.", "Вы"),
        Segment(25.0, 30.0, "Да, по первому вопросу.", "SPEAKER_00"),
        Segment(31.0, 32.0, "Угу.", None),
    ]
    md = to_markdown("Встреча — 12.06.2026", segs, date="2026-06-12")
    assert md.startswith("---\ndate: 2026-06-12\ntask:\ntype: transcript\n")
    assert "tags: [claude-generated, transcript]" in md
    assert "# Встреча — 12.06.2026" in md
    assert "## 00:12 — Вы\n\nКоллеги, начнём." in md
    assert "## 00:25 — Спикер 1\n\nДа, по первому вопросу." in md
    assert "## 00:31 — Спикер ?\n\nУгу." in md


def test_to_markdown_without_date_has_no_frontmatter():
    md = to_markdown("Заголовок", [Segment(0.0, 1.0, "Раз.", "Вы")])
    assert not md.startswith("---")
    assert md.startswith("# Заголовок\n")


def test_merge_does_not_glue_uncertain_with_certain():
    segs = [
        Segment(0.0, 2.0, "Раз.", "SPEAKER_00"),
        Segment(2.5, 3.0, "Два.", "SPEAKER_00", uncertain=True),
        Segment(3.5, 4.0, "Три.", "SPEAKER_00"),
    ]
    merged = merge_consecutive(segs, max_gap=2.0)
    assert [(m.text, m.uncertain) for m in merged] == [
        ("Раз.", False),
        ("Два.", True),
        ("Три.", False),
    ]


def test_merge_glues_consecutive_uncertain_blocks():
    segs = [
        Segment(0.0, 1.0, "Раз.", "SPEAKER_00", uncertain=True),
        Segment(1.5, 2.0, "Два.", "SPEAKER_00", uncertain=True),
    ]
    merged = merge_consecutive(segs, max_gap=2.0)
    assert len(merged) == 1
    assert merged[0].uncertain is True
    assert merged[0].text == "Раз. Два."


def test_to_markdown_marks_overlap_block():
    segs = [
        Segment(12.0, 14.0, "Коллеги, начнём.", "SPEAKER_00"),
        Segment(25.0, 26.0, "Ага.", "SPEAKER_00", uncertain=True),
    ]
    md = to_markdown("Т", segs)
    assert "## 00:12 — Спикер 1\n\nКоллеги, начнём." in md
    assert "## 00:25 — Спикер 1 (нахлёст)\n\nАга." in md
