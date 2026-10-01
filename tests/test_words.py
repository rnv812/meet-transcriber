"""Слова с таймкодами в transcript.json: пайплайн сохраняет их (компактно) и
пометку дорожки микрофона; окно получает транскрипт без слов, но с `has_words`;
сохранение из редактора не теряет слова неправленых сегментов.

Тексты выдуманные."""

import json

import pytest

from meet import library, tray, tray_control
from meet.asr import Segment, Word
from meet.diarize import split_by_speaker
from meet.interleave import interleave_tracks


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    return tray.TrayApp()


def _words(*items):
    return [Word(s, e, t) for s, e, t in items]


def test_split_by_speaker_keeps_words_and_track_of_each_part():
    seg = Segment(0.0, 4.0, "Добрый день. Здравствуйте.", words=_words(
        (0.0, 0.6, " Добрый"), (0.6, 1.2, " день."), (2.0, 3.9, " Здравствуйте.")), track="mic")
    turns = [(0.0, 1.5, "SPEAKER_00"), (1.5, 4.0, "SPEAKER_01")]
    first, second = split_by_speaker([seg], turns)
    assert [w.text for w in first.words] == [" Добрый", " день."] and first.text == "Добрый день."
    assert [w.text for w in second.words] == [" Здравствуйте."]
    assert first.track == second.track == "mic"


def test_interleave_keeps_track_of_cut_parts():
    sys_seg = Segment(0.0, 4.0, "раз два", "SPEAKER_00", words=_words((0.0, 1.0, " раз"), (3.0, 4.0, " два")))
    mic = Segment(2.0, 2.5, "ага", "Вы", words=_words((2.0, 2.5, " ага")), track="mic")
    out = interleave_tracks([sys_seg], [mic])
    assert [(s.text, s.track) for s in out] == [("раз", None), ("ага", "mic"), ("два", None)]


def test_words_live_in_words_json_and_transcript_stays_lean(tmp_path):
    from meet.transcribe import _write_structured

    (tmp_path / "sys.opus").write_bytes(b"x")
    (tmp_path / "mic.opus").write_bytes(b"x")
    segs = [Segment(0.0, 1.2, "Добрый день.", "SPEAKER_00",
                    words=_words((0.0, 0.6, " Добрый"), (0.6, 1.2, " день."))),
            Segment(1.3, 2.0, "Да.", "Вы", words=_words((1.3, 2.0, " Да.")), track="mic")]
    _write_structured(tmp_path, segs, "t", {})
    lean = library.read_transcript(tmp_path)
    assert lean["track_marks"] == "pipeline"
    assert all("words" not in s for s in lean["segments"])
    assert "\"words\"" not in (tmp_path / "transcript.json").read_text(encoding="utf-8")
    first, second = library.read_transcript_full(tmp_path)["segments"]
    assert first["words"] == [[0.0, 0.6, " Добрый"], [0.6, 1.2, " день."]]
    assert "track" not in first and second["track"] == "mic"
    raw = json.loads((tmp_path / "words.json").read_text(encoding="utf-8"))
    assert raw["items"][0] == [0.0, 1.2, [[0.0, 0.6, " Добрый"], [0.6, 1.2, " день."]]]


def test_words_of_edited_or_shifted_segments_are_not_attached(tmp_path):
    library.write_transcript(tmp_path, {"segments": [
        {"start": 0, "end": 1, "speaker": "А", "text": "раз два", "words": [[0, 0.5, " раз"], [0.5, 1, " два"]]},
        {"start": 1, "end": 2, "speaker": "Б", "text": "три", "words": [[1, 2, " три"]]}]})
    # Окно (без слов) поправило текст первого и время второго — words.json не трогается,
    # но устаревшие записи больше не подставляются.
    library.write_transcript(tmp_path, {"segments": [
        {"start": 0, "end": 1, "speaker": "А", "text": "раз три"},
        {"start": 1, "end": 2.5, "speaker": "Б", "text": "три"}]})
    assert [("words" in s) for s in library.read_transcript_full(tmp_path)["segments"]] == [False, False]


def test_words_left_in_transcript_json_by_older_builds_move_out_on_write(tmp_path):
    (tmp_path / "transcript.json").write_text(json.dumps({"segments": [
        {"start": 0, "end": 1, "speaker": "А", "text": "раз", "words": [[0, 1, " раз"]]}]},
        ensure_ascii=False), encoding="utf-8")
    full = library.read_transcript_full(tmp_path)
    assert full["segments"][0]["words"] == [[0, 1, " раз"]]
    library.write_transcript(tmp_path, full)
    assert "words" not in library.read_transcript(tmp_path)["segments"][0]
    assert library.read_transcript_full(tmp_path)["segments"][0]["words"] == [[0, 1, " раз"]]


def test_listing_does_not_reparse_unchanged_transcripts(tmp_path, monkeypatch):
    folder = tmp_path / "2026-09-30_16-04"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"title": "Планёрка", "segments": []})
    calls = []
    real = library.read_transcript
    monkeypatch.setattr(library, "read_transcript", lambda f: calls.append(f) or real(f))
    assert library.describe(folder).title == "Планёрка"
    assert library.describe(folder).title == "Планёрка"
    assert len(calls) == 1
    library.write_transcript(folder, {"title": "Планёрка отдела", "diarization": "skipped_no_token",
                                      "segments": []})
    card = library.describe(folder)
    assert (card.title, card.diarization, card.has_transcript) == ("Планёрка отдела", "skipped_no_token", True)
    assert len(calls) == 2


def test_window_gets_no_words_but_knows_where_they_are(app, tmp_path, monkeypatch):
    folder = tmp_path / "recordings" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"segments": [
        {"start": 0, "end": 1.2, "speaker": "Спикер 1", "text": "Добрый день.",
         "words": [[0, 0.6, " Добрый"], [0.6, 1.2, " день."]]},
        {"start": 2, "end": 3, "speaker": "Спикер 2", "text": "Правленый текст",
         "words": [[2, 3, " Другой"]]},
        {"start": 3, "end": 4, "speaker": "Спикер 2", "text": "Без слов"}]})
    state = tray_control.TrayControl(app)
    monkeypatch.setattr(state, "_root", lambda: folder.parent)
    served = state.transcript(folder.name)["segments"]
    assert state.recording(folder.name)["transcript"]["segments"] == served   # карточка — так же
    assert all("words" not in s for s in served)
    assert [s.get("has_words") for s in served] == [True, None, None]

    # Редактор шлёт без слов: у неправленого сегмента они остаются, у правленого — нет.
    edited = [dict(s) for s in served]
    edited[1]["speaker"] = "Анна"
    state.save_transcript(folder.name, {"segments": edited})
    assert "has_words" not in library.read_transcript(folder)["segments"][0]
    stored = library.read_transcript_full(folder)["segments"]
    assert stored[0]["words"] == [[0, 0.6, " Добрый"], [0.6, 1.2, " день."]]
    assert "words" not in stored[1]
    edited[0]["text"] = "Добрый вечер."
    state.save_transcript(folder.name, {"segments": edited})
    assert "words" not in library.read_transcript_full(folder)["segments"][0]
