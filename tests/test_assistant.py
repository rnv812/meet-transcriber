"""Итоги, вопросы и «В заметки» по готовой записи — с фейковым runner'ом.

Настоящая модель здесь не вызывается: runner — async-функция с той же
сигнатурой, что у провайдеров (см. tests/test_assist_qa.py).
"""

import json

import pytest

from meet import assistant, library
from meet.llm.base import AgentReply


def _folder(tmp_path, name="2026-09-30_16-04", title="Планёрка"):
    folder = tmp_path / "rec" / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {
        "version": 1, "title": title, "segments": [
            {"start": 5.0, "end": 7.0, "speaker": "Демьян", "text": "Начнём."},
            {"start": 7.5, "end": 9.0, "speaker": "Демьян", "text": "Срок пятница."},
            {"start": 65.0, "end": 66.0, "speaker": "SPEAKER_01", "text": "Согласен."},
        ],
    })
    return folder


def _runner(replies, calls):
    async def runner(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return replies.pop(0)
    return runner


# --- transcript_text ---------------------------------------------------------


def test_transcript_text_glues_consecutive_turns_and_names_speakers(tmp_path):
    data = library.read_transcript(_folder(tmp_path))
    text = assistant.transcript_text(data)
    assert text.splitlines() == [
        "[00:05] Демьян: Начнём. Срок пятница.",
        "[01:05] Спикер 1: Согласен.",
    ]


def test_transcript_text_cuts_the_middle_of_a_huge_transcript():
    segments = [{"start": float(i), "end": float(i) + 0.5,
                 "speaker": "А" if i % 2 else "Б", "text": f"реплика {i} " + "x" * 90}
                for i in range(6000)]
    text = assistant.transcript_text({"segments": segments})
    assert len(text) <= assistant.MAX_TRANSCRIPT_CHARS + 200
    assert "реплика 0 " in text and "реплика 5999 " in text
    assert "пропущено" in text
    assert "реплика 3000 " not in text


def test_transcript_text_short_is_not_cut():
    text = assistant.transcript_text({"segments": [
        {"start": 0, "end": 1, "speaker": "А", "text": "раз"}]})
    assert "пропущено" not in text


# --- summarize ---------------------------------------------------------------


def test_summarize_writes_summary_with_title_and_model_line(tmp_path):
    folder = _folder(tmp_path)
    knowledge = tmp_path / "kb"
    knowledge.mkdir()
    calls = []
    runner = _runner([AgentReply(text="## Итоги\n- решили X")], calls)
    path = assistant.summarize(folder, runner, knowledge, provider="codex")
    assert path == folder / "summary.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Итоги — Планёрка\n")
    assert "## Итоги\n- решили X" in text
    assert "_Модель: codex · " in text
    prompt, kwargs = calls[0]
    assert "[00:05] Демьян: Начнём. Срок пятница." in prompt
    assert kwargs["system_prompt"] == assistant.SUMMARY_SYSTEM
    assert kwargs["allowed_dirs"] == (folder, knowledge)
    assert kwargs["cwd"] == folder
    assert isinstance(library.read_meta(folder).get("summary_at"), float)


def test_summarize_skips_missing_knowledge_dir(tmp_path):
    folder = _folder(tmp_path)
    calls = []
    assistant.summarize(folder, _runner([AgentReply(text="ок")], calls),
                        tmp_path / "нет-такой")
    assert calls[0][1]["allowed_dirs"] == (folder,)
    assistant.summarize(folder, _runner([AgentReply(text="ок")], calls), None)
    assert calls[1][1]["allowed_dirs"] == (folder,)


@pytest.mark.parametrize("reply, message", [
    (AgentReply(text="", error="rate_limit"), "rate_limit"),
    (AgentReply(text="   "), "модель вернула пустой ответ"),
])
def test_summarize_error_keeps_old_summary(tmp_path, reply, message):
    folder = _folder(tmp_path)
    (folder / "summary.md").write_text("старые итоги", encoding="utf-8")
    with pytest.raises(RuntimeError, match=message):
        assistant.summarize(folder, _runner([reply], []), None)
    assert (folder / "summary.md").read_text(encoding="utf-8") == "старые итоги"
    assert "summary_at" not in library.read_meta(folder)


def test_summary_prompt_rules():
    for needle in ("## Итоги", "## Решения", "## Задачи", "Кто", "Срок",
                   "## Открытые вопросы", "## Цитаты", "[мм:сс]"):
        assert needle in assistant.SUMMARY_SYSTEM


def test_read_summary(tmp_path):
    folder = _folder(tmp_path)
    assert assistant.read_summary(folder) is None
    assistant.summarize(folder, _runner([AgentReply(text="итог")], []), None)
    got = assistant.read_summary(folder)
    assert "итог" in got["markdown"]
    assert got["created_at"] == library.read_meta(folder)["summary_at"]


# --- ask ---------------------------------------------------------------------


def test_ask_appends_to_qa_and_passes_history_and_summary(tmp_path):
    folder = _folder(tmp_path)
    (folder / "summary.md").write_text("# Итоги\nрешили X", encoding="utf-8")
    calls = []
    runner = _runner([AgentReply(text=f"ответ {i}") for i in range(8)], calls)
    for i in range(8):
        item = assistant.ask(folder, f"вопрос {i}", runner, None, provider="codex")
    assert item["q"] == "вопрос 7" and item["a"] == "ответ 7"
    assert item["provider"] == "codex" and isinstance(item["at"], float)
    items = assistant.read_qa(folder)
    assert [x["q"] for x in items] == [f"вопрос {i}" for i in range(8)]
    last_prompt, kwargs = calls[-1]
    assert "решили X" in last_prompt
    assert "Начнём." in last_prompt
    # Последние 6 пар до текущего вопроса: 1..6, без 0.
    assert "вопрос 6" in last_prompt and "ответ 1" in last_prompt
    assert "вопрос 0" not in last_prompt
    assert last_prompt.rstrip().endswith("вопрос 7")
    assert kwargs["cwd"] == folder


def test_ask_error_raises_and_writes_nothing(tmp_path):
    folder = _folder(tmp_path)
    with pytest.raises(RuntimeError, match="таймаут"):
        assistant.ask(folder, "что решили?",
                      _runner([AgentReply(text="", error="таймаут вызова модели")], []),
                      None)
    assert assistant.read_qa(folder) == []


def test_read_qa_skips_broken_lines(tmp_path):
    folder = _folder(tmp_path)
    (folder / "qa.jsonl").write_text(
        json.dumps({"q": "а", "a": "б", "at": 1.0, "provider": "x"}, ensure_ascii=False)
        + "\nмусор\n", encoding="utf-8")
    assert [x["q"] for x in assistant.read_qa(folder)] == ["а"]


# --- to_notes ----------------------------------------------------------------


def test_to_notes_writes_dated_file_with_summary_and_transcript(tmp_path):
    folder = _folder(tmp_path, title="Планёрка: итоги?")
    (folder / "summary.md").write_text("# Итоги — x\n\n## Решения\n- X\n",
                                       encoding="utf-8")
    notes = tmp_path / "vault"
    notes.mkdir()
    path = assistant.to_notes(folder, notes, "Встречи")
    assert path == notes / "Встречи" / "2026-09-30 Планёрка_ итоги_.md"
    text = path.read_text(encoding="utf-8")
    assert "# Планёрка: итоги?" in text
    assert "## Решения\n- X" in text
    assert "Начнём. Срок пятница." in text
    assert text.index("## Решения") < text.index("Начнём.")


def test_to_notes_never_overwrites(tmp_path):
    folder = _folder(tmp_path)
    notes = tmp_path / "vault"
    notes.mkdir()
    first = assistant.to_notes(folder, notes, "")
    second = assistant.to_notes(folder, notes, "")
    assert first == notes / "2026-09-30 Планёрка.md"
    assert second == notes / "2026-09-30 Планёрка (2).md"
    assert "Начнём." in first.read_text(encoding="utf-8")


def test_to_notes_without_notes_dir_is_an_error(tmp_path):
    folder = _folder(tmp_path)
    with pytest.raises(ValueError, match="Папка заметок не задана"):
        assistant.to_notes(folder, None, "Встречи")
    with pytest.raises(ValueError):
        assistant.to_notes(folder, tmp_path / "нет-такой", "Встречи")


@pytest.mark.parametrize("subdir", ["..", "../вне", "Встречи/../../вне", "C:/вне", "/вне"])
def test_to_notes_subdir_stays_inside_notes_dir(tmp_path, subdir):
    folder = _folder(tmp_path)
    notes = tmp_path / "vault"
    notes.mkdir()
    with pytest.raises(ValueError, match="подпапка"):
        assistant.to_notes(folder, notes, subdir)
    assert not list(tmp_path.glob("*.md")) and not (tmp_path / "вне").exists()


def test_to_notes_without_transcript_is_an_error(tmp_path):
    folder = tmp_path / "rec" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    notes = tmp_path / "vault"
    notes.mkdir()
    with pytest.raises(ValueError, match="транскрипта нет"):
        assistant.to_notes(folder, notes, "")


def test_note_layout_is_built_from_segments_not_parsed(tmp_path):
    """Шапка и реплики собираются из сегментов: название, совпадающее с текстом
    реплики, не сбивает раскладку заметки."""
    folder = _folder(tmp_path, title="Начнём.")
    notes = tmp_path / "vault"
    notes.mkdir()
    text = assistant.to_notes(folder, notes, "").read_text(encoding="utf-8")
    assert text.startswith("---\ndate: 2026-09-30\n")
    assert text.count("# Начнём.\n") == 1
    assert "## Транскрипт\n\n### 00:05 — Демьян\n\nНачнём. Срок пятница." in text
    assert "### 01:05 — Спикер 1" in text
