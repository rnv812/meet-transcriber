"""M3: «Содержание» (главы анализа встречи) в выгрузке Markdown и в базу знаний,
настройки «Расшифровка: подсветка и разметка».

Данные — выдуманные: «Планирование спринта», Анна и Борис.
"""

import json
from pathlib import Path

from meet import analysis, export, kb_export, library, settings
from meet.llm.base import AgentReply

SEGMENTS = [
    {"start": 5.0, "end": 7.0, "speaker": "Анна", "text": "Начнём с задач."},
    {"start": 65.0, "end": 66.5, "speaker": "Борис", "text": "Согласен."},
    {"start": 70.0, "end": 70.0, "speaker": "Борис", "text": " "},
    {"start": 312.0, "end": 318.0, "speaker": "Анна", "text": "Теперь бюджет на квартал."},
    {"start": 3725.0, "end": 3730.0, "speaker": "Борис", "text": "Итоги и сроки."},
]
CHAPTERS = [
    {"start_i": 0, "end_i": 1, "title": "Вступление", "short": "Вступление"},
    {"start_i": 2, "end_i": 3, "title": "Бюджет на квартал", "short": "Бюджет"},
    {"start_i": 4, "end_i": 4, "title": "Итоги и сроки", "short": "Итоги"},
]


def _data(segments=SEGMENTS):
    return {"version": 1, "title": "Планирование спринта", "segments": [dict(s) for s in segments]}


def _doc(data, **over):
    return {"version": 1, "model": "test", "created_at": 1.0, "fingerprint": analysis.fingerprint(data),
            "segments": len(data["segments"]), "features": ["chapters"], "chapters": CHAPTERS, **over}


def test_contents_from_fresh_analysis_starts_at_first_spoken_segment():
    data = _data()
    # Глава 2 начинается с пустого сегмента — время берётся у следующей реплики.
    assert export.contents(data, _doc(data)) == [
        (5.0, "Вступление"), (312.0, "Бюджет на квартал"), (3725.0, "Итоги и сроки")]


def test_contents_from_stale_analysis_only_with_same_segment_count():
    data = _data()
    doc = _doc(data)
    edited = _data()
    edited["segments"][1]["text"] = "Согласен, начнём."
    assert export.contents(edited, doc)  # правили текст — номера те же
    split = _data(SEGMENTS + [{"start": 3800.0, "end": 3801.0, "speaker": "Анна", "text": "Всё."}])
    assert export.contents(split, doc) == []  # сегментов стало больше — номера уже не те
    assert export.contents(edited, {k: v for k, v in doc.items() if k != "segments"}) == []


def test_contents_skips_bad_chapters_and_no_analysis():
    data = _data()
    doc = _doc(data, chapters=[{"start_i": 0, "title": "  "}, {"start_i": "1", "title": "Плохая"},
                               {"start_i": 3, "title": "Бюджет"}, {"start_i": 3, "title": "Повтор"}])
    assert export.contents(data, doc) == [(312.0, "Бюджет")]
    assert export.contents(data, None) == []
    assert export.contents(data, _doc(data, chapters=None)) == []


def test_markdown_export_has_contents_with_timestamps():
    data = _data()
    md = export.render({**data, "title": "Планирование спринта"}, "md", date="2026-09-30",
                       chapters=export.contents(data, _doc(data)))
    head, _, rest = md.partition("## Содержание\n\n")
    assert head.endswith("# Планирование спринта\n\n")
    assert rest.startswith("- 00:05 — Вступление\n- 05:12 — Бюджет на квартал\n- 01:02:05 — Итоги и сроки\n\n## 00:05 — Анна")
    # Без глав — как раньше.
    assert "Содержание" not in export.render(data, "md")
    # Текст и субтитры — без содержания.
    assert "Содержание" not in export.render(data, "txt")


def _recording(root: Path, *, with_analysis=True) -> Path:
    folder = root / "rec" / "2026-09-30_10-15"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"sys")
    library.write_meta(folder, {"title": "Планирование спринта"})
    data = _data()
    library.write_transcript(folder, data)
    if with_analysis:
        (folder / analysis.ANALYSIS_JSON).write_text(json.dumps(_doc(data), ensure_ascii=False), encoding="utf-8")
    return folder


def test_kb_export_transcript_has_contents(tmp_path):
    folder = _recording(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    got = kb_export.export_recording(folder, settings.Export.from_raw({"meetings_dir": str(vault)}))
    text = (Path(got["path"]) / "Транскрипт.md").read_text(encoding="utf-8")
    assert "## Содержание\n\n- 00:05 — Вступление\n- 05:12 — Бюджет на квартал\n" in text
    assert text.index("## Содержание") < text.index("## 00:05 — Анна")


def test_chapters_of_reads_the_recordings_analysis(tmp_path):
    folder = _recording(tmp_path)
    data = library.read_transcript(folder)
    assert [t for _, t in export.chapters_of(folder, data)] == ["Вступление", "Бюджет на квартал", "Итоги и сроки"]
    bare = _recording(tmp_path / "other", with_analysis=False)
    assert export.chapters_of(bare, library.read_transcript(bare)) == []


def test_analysis_run_records_segment_count(tmp_path):
    folder = _recording(tmp_path, with_analysis=False)
    reply = json.dumps({"chapters": [{"start_i": 0, "title": "Вступление", "short": "Вступление"}]})
    cfg = settings.Settings(analysis=settings.Analysis(
        types=False, importance=False, insights=False, category=False, title=False))
    doc = analysis.run(folder, lambda *a, **k: AgentReply(text=reply), cfg)
    assert doc["segments"] == len(SEGMENTS)


def test_transcript_view_settings_defaults_and_round_trip(tmp_path):
    view = settings.Settings().transcript_view
    assert view.to_raw() == {"types": True, "importance": True, "chapters": True, "insights": True,
                             "curve": "hover", "bar_labels": True, "jira": True}
    path = tmp_path / "config.json"
    updated = settings.patch({"transcript_view": {"curve": "always", "types": False}}, path)
    assert updated.transcript_view.curve == "always" and updated.transcript_view.types is False
    assert settings.load(path).transcript_view.importance is True
    # Негодное значение из файла — по умолчанию.
    assert settings.TranscriptView.from_raw({"curve": "сбоку", "jira": "да"}).curve == "hover"
