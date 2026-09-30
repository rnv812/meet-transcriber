import pytest

from meet import export

DATA = {"title": "Встреча с Acme", "segments": [
    {"start": 36.2, "end": 47.9, "speaker": "Матвей", "text": "Давайте начнём…"},
    {"start": 48.0, "end": 60.5, "speaker": "Аркадий", "text": "Сначала о сроках…"},
    {"start": 60.5, "end": 3725.0, "speaker": "Аркадий", "text": "И ещё.",
     "uncertain": True},
]}


def test_txt_has_time_speaker_text_lines():
    text = export.render(DATA, "txt")
    assert text.splitlines()[0] == "Встреча с Acme"
    assert "[00:00:36] Матвей: Давайте начнём…" in text
    assert "[01:02:05]" not in text  # время — начала реплики
    assert "[00:01:00] Аркадий (нахлёст): И ещё." in text


def test_srt_numbering_and_timestamps():
    srt = export.render(DATA, "srt")
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 3
    assert blocks[0].splitlines() == [
        "1", "00:00:36,200 --> 00:00:47,900", "Матвей: Давайте начнём…"]
    assert blocks[2].splitlines()[1] == "00:01:00,500 --> 01:02:05,000"


def test_md_uses_project_markdown_format():
    md = export.render(DATA, "md", date="2026-09-30")
    assert md.startswith("---\ndate: 2026-09-30")
    assert "# Встреча с Acme" in md and "Матвей" in md


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        export.render(DATA, "docx")


def test_empty_transcript_is_fine():
    assert export.render({"segments": []}, "srt") == ""


def test_safe_filename_replaces_dangerous_chars():
    assert export.safe_filename("Встреча 1/2: итоги?", "fallback") == "Встреча 1_2_ итоги_"


def test_safe_filename_strips_whitespace_and_dots():
    assert export.safe_filename("  a.  ", "fallback") == "a"
    assert export.safe_filename("  ", "fallback") == "fallback"
    assert export.safe_filename("", "fallback") == "fallback"


def test_safe_filename_keeps_cyrillic():
    result = export.safe_filename("Встреча", "fallback")
    assert result == "Встреча"
    assert "Встреча" in result


def test_safe_filename_removes_control_chars():
    # Title with control characters
    result = export.safe_filename("Test\x00\x1fTitle", "fallback")
    assert "\x00" not in result
    assert "\x1f" not in result
    assert result == "Test__Title"
