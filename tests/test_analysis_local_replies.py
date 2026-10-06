"""Анализ встречи на ответах, какие дают локальные модели (LM Studio, Ollama,
vLLM): заготовки в tests/fixtures/analysis_replies. Модель не вызывается.

Битый ответ не должен давать молча пустую полосу плеера: годное — берётся
(блок ```json, текст вокруг, <think>, номера строками, обёртка, обрыв), чего
нет — записано в `missing`, ничего нет — анализ не удался."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from meet import analysis, library, settings
from meet.llm.base import AgentReply

REPLIES = Path(__file__).parent / "fixtures" / "analysis_replies"
RID = "2026-10-01_10-00"
SEGMENTS = [
    {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "Добрый день, начинаем планирование."},
    {"start": 4.0, "end": 9.0, "speaker": "Ольга", "text": "Что успеваем к пятнице?"},
    {"start": 9.0, "end": 15.0, "speaker": "SPEAKER_00", "text": "Решили: экспорт отчётов выпускаем в пятницу."},
    {"start": 15.0, "end": 16.0, "speaker": "Ольга", "text": ""},
    {"start": 16.0, "end": 22.0, "speaker": "Ольга", "text": "Беру уведомления на себя."},
    {"start": 22.0, "end": 400.0, "speaker": "SPEAKER_00", "text": "Риск: тестовый стенд пока не готов."},
]
CHAPTERS = [{"start_i": 0, "end_i": 2, "title": "План на неделю", "short": "План"},
            {"start_i": 4, "end_i": 5, "title": "Задачи и риски", "short": "Задачи"}]


class FakeRunner:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def __call__(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return AgentReply(text=self.replies.pop(0))


def _reply(name: str) -> str:
    return (REPLIES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_context_lookup(monkeypatch):
    # Окно контекста локальной модели узнаётся по сети — в тестах его нет.
    monkeypatch.setattr(analysis, "local_context", lambda cfg: None)


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / RID
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [dict(s) for s in SEGMENTS]})
    return folder


def _cfg():
    return settings.Settings()


@pytest.mark.parametrize("name", ["lmstudio_fenced_prose.txt", "qwen3_think.txt", "string_indices.json",
                                  "wrapped.json", "trailing_commas.txt"])
def test_wrapped_or_sloppy_json_is_read(folder, name):
    runner = FakeRunner(_reply(name), _reply(name))
    doc = analysis.run(folder, runner, _cfg())
    assert doc["chapters"] == CHAPTERS
    assert doc["importance"]["2"] == pytest.approx(0.9, abs=0.06) and doc["importance"]["4"] == 0.8
    assert doc["phrase_types"]["2"] == "decision"
    assert doc["title"] == "Планирование недели"
    assert "missing" not in doc


def test_out_of_range_items_are_dropped_or_clamped_and_counted(folder):
    doc = analysis.run(folder, FakeRunner(_reply("out_of_range.json")), _cfg())
    # Глава с #57 — не из этой встречи (реплик 0–5): отброшена; конец #99 — до последней.
    assert doc["chapters"] == CHAPTERS
    assert doc["phrase_types"] == {"2": "decision"} and set(doc["importance"]) == {"2", "4"}
    assert doc["dropped"] == {"types": 1, "importance": 1, "chapters": 1}


def test_aliases_lists_russian_types_and_ten_point_scale(folder):
    doc = analysis.run(folder, FakeRunner(_reply("aliases.json")), _cfg())
    assert doc["phrase_types"] == {"2": "decision", "4": "task", "5": "risk"}
    assert doc["importance"] == {"2": 0.9, "4": 0.8, "5": 0.6, "1": 0.3}
    assert doc["chapters"] == [{**CHAPTERS[0], "short": "План на неделю"}, CHAPTERS[1]]


def test_truncated_reply_keeps_its_complete_parts_and_says_what_is_missing(folder):
    runner = FakeRunner(_reply("truncated.txt"), _reply("truncated.txt"))
    doc = analysis.run(folder, runner, _cfg())
    assert len(runner.calls) == 2  # нет частей — просили исправить
    assert doc["chapters"] == CHAPTERS and doc["importance"] == {"2": 0.9, "4": 0.8}
    assert doc["missing"] == ["insights", "category"]
    assert any("оборван" in w for w in doc["warnings"])


def test_reply_in_another_schema_fails_instead_of_an_empty_analysis(folder):
    runner = FakeRunner(_reply("wrong_schema.json"), _reply("wrong_schema.json"))
    with pytest.raises(analysis.AnalysisError) as e:
        analysis.run(folder, runner, _cfg())
    assert "в ответе модели нет нужных частей: нет поля phrase_types" in str(e.value)


def test_empty_chapters_of_a_long_meeting_are_asked_again_then_marked_missing(folder):
    runner = FakeRunner(_reply("no_chapters.json"), _reply("no_chapters.json"))
    doc = analysis.run(folder, runner, _cfg())
    assert len(runner.calls) == 2
    assert "главы" in runner.calls[1]["prompt"] or "chapters" in runner.calls[1]["prompt"]
    assert doc["chapters"] == [] and doc["missing"] == ["chapters"]
    assert doc["importance"]  # остальное принято


def test_empty_chapters_then_repaired(folder):
    runner = FakeRunner(_reply("no_chapters.json"), _reply("lmstudio_fenced_prose.txt"))
    doc = analysis.run(folder, runner, _cfg())
    assert doc["chapters"] == CHAPTERS and "missing" not in doc


def test_short_meeting_may_have_no_chapters(tmp_path):
    folder = tmp_path / RID
    folder.mkdir()
    short = [dict(s, end=min(s["end"], 60.0)) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": short})
    runner = FakeRunner(_reply("no_chapters.json"))
    doc = analysis.run(folder, runner, _cfg())
    assert len(runner.calls) == 1 and doc["chapters"] == [] and "missing" not in doc


def test_local_model_is_asked_for_a_json_schema(folder):
    runner = FakeRunner(_reply("lmstudio_fenced_prose.txt"))
    analysis.run(folder, runner, _cfg(), provider="openai-compatible")
    schema = runner.calls[0]["response_schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"phrase_types", "importance", "chapters", "insights", "category", "title"}
    chapter = schema["properties"]["chapters"]["items"]
    assert chapter["properties"]["start_i"] == {"type": "integer"}


def test_cli_models_get_no_schema_argument(folder):
    runner = FakeRunner(_reply("lmstudio_fenced_prose.txt"))
    analysis.run(folder, runner, _cfg(), provider="claude-code")
    assert "response_schema" not in runner.calls[0]


def test_schema_follows_enabled_parts(folder):
    cfg = settings.Settings()
    cfg = replace(cfg, analysis=replace(cfg.analysis, insights=False, category=False, title=False, types=False))
    runner = FakeRunner(json.dumps({"importance": {"2": 0.9}, "chapters": CHAPTERS}))
    analysis.run(folder, runner, cfg, provider="openai-compatible")
    assert set(runner.calls[0]["response_schema"]["properties"]) == {"importance", "chapters"}


def test_cli_text_names_the_missing_parts():
    from meet import cli_library

    text = cli_library._analysis_text({"chapters": [], "missing": ["chapters", "importance"],
                                       "warnings": ["часть 1: chapters пустой"]})
    assert "Модель не дала: главы, важность — попробуйте другую модель" in text
    assert "chapters пустой" in text


# --- окно контекста локальной модели, куски, обрезка ------------------------------


class UsageRunner:
    """Отвечает по очереди; строка — текст, AgentReply — как есть."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def __call__(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        reply = self.replies.pop(0)
        return reply if isinstance(reply, AgentReply) else AgentReply(text=reply)


def _long_folder(tmp_path, n=200, size=600):
    folder = tmp_path / "long"
    folder.mkdir()
    segments = [{"start": float(i * 20), "end": float(i * 20 + 19), "speaker": "Ольга",
                 "text": f"Реплика {i} " + "слово " * (size // 6)} for i in range(n)]
    library.write_transcript(folder, {"version": 1, "segments": segments})
    return folder


def _types_only():
    cfg = settings.Settings()
    return replace(cfg, analysis=replace(cfg.analysis, importance=False, chapters=False, category=False,
                                         title=False))


def test_too_small_context_is_refused_up_front(folder, monkeypatch):
    monkeypatch.setattr(analysis, "local_context", lambda cfg: 4096)
    runner = UsageRunner()
    with pytest.raises(analysis.AnalysisError) as e:
        analysis.run(folder, runner, _cfg(), provider="openai-compatible")
    assert "слишком маленькое окно контекста: 4096" in str(e.value) and "16K" in str(e.value)
    assert runner.calls == []


def test_local_windows_fit_the_context_and_none_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "local_context", lambda cfg: 8192)
    folder = _long_folder(tmp_path)
    lines = analysis.compact_lines(library.read_transcript(folder))
    limit = analysis.window_chars(8192)
    expected = analysis.windows(lines, limit=limit, max_windows=None)
    assert len(expected) > analysis.MAX_WINDOWS  # Claude получил бы выборку, локальная — всё
    reply = json.dumps({"phrase_types": {}, "insights": []})
    runner = UsageRunner(*([reply] * len(expected)))
    doc = analysis.run(folder, runner, _types_only(), provider="openai-compatible")
    assert len(runner.calls) == len(expected) and "unparsed" not in doc
    assert all(len(c["prompt"]) < limit + 2000 for c in runner.calls)
    assert all(c["max_tokens"] == analysis.reply_tokens(len(w)) for c, w in zip(runner.calls, expected))


def test_window_size_follows_the_context():
    assert analysis.window_chars(32768) == pytest.approx(54000, rel=0.05)
    assert analysis.window_chars(8192) < 12000
    assert analysis.window_chars(131072) == analysis.WINDOW_CHARS
    assert analysis.window_chars(6144) >= analysis.MIN_WINDOW_CHARS


def test_prompt_cut_by_the_server_is_reported(folder):
    # Сервер насчитал промпту 300 токенов, а в нём ~1000: обрезан по контексту.
    good = _reply("lmstudio_fenced_prose.txt")
    runner = UsageRunner(AgentReply(text=good, usage={"prompt_tokens": 300, "completion_tokens": 200}))
    doc = analysis.run(folder, runner, _cfg(), provider="openai-compatible")
    assert doc["context_cut"]["seen"] == 300 and doc["context_cut"]["need"] > 1000
    assert doc["warnings"][0].startswith("модель видела только часть текста")


def test_failed_windows_are_counted_with_the_reason(folder, monkeypatch):
    monkeypatch.setattr(analysis, "windows", lambda ls: [ls[:2], ls[2:4], ls[4:]])
    ok = json.dumps({"phrase_types": {}, "insights": [], "summary": "Часть."})
    overflow = AgentReply(text="", error="текст не помещается в контекст модели: maximum context length")
    final = json.dumps({"category": None, "title": "План"})
    runner = UsageRunner(ok, overflow, ok, final)
    cfg = settings.Settings()
    cfg = replace(cfg, analysis=replace(cfg.analysis, importance=False, chapters=False))
    doc = analysis.run(folder, runner, cfg)
    assert len(runner.calls) == 4  # переполнение — без попытки исправления
    assert doc["unparsed"]["parts"] == 1 and doc["unparsed"]["of"] == 3
    assert doc["unparsed"]["reason"].startswith("текст не помещается в контекст модели")


def test_part_delivered_only_by_some_windows_is_partial(folder, monkeypatch):
    monkeypatch.setattr(analysis, "windows", lambda ls: [ls[:3], ls[3:]])
    with_insights = json.dumps({"phrase_types": {}, "insights": [], "summary": "Часть."})
    without = json.dumps({"phrase_types": {}, "summary": "Часть."})
    final = json.dumps({"category": None, "title": "План"})
    runner = UsageRunner(with_insights, without, without, final)
    cfg = settings.Settings()
    cfg = replace(cfg, analysis=replace(cfg.analysis, importance=False, chapters=False))
    doc = analysis.run(folder, runner, cfg)
    assert doc["partial"] == {"insights": "1/2"} and "missing" not in doc


def test_likert_scale_importance():
    got = analysis._importance({"1": 5, "2": 3, "4": 1}, {1, 2, 4})
    assert got == {"1": 1.0, "2": 0.6, "4": 0.2}


def test_cli_text_names_unparsed_and_partial_parts():
    from meet import cli_library

    text = cli_library._analysis_text({"unparsed": {"parts": 2, "of": 5, "reason": "таймаут вызова модели"},
                                       "partial": {"chapters": "1/3"}})
    assert "Часть встречи не разобрана (2 из 5 кусков): таймаут вызова модели" in text
    assert "Только для части встречи: главы (1/3 кусков)" in text



def test_unknown_local_context_cuts_windows_as_for_8k(tmp_path, monkeypatch):
    folder = _long_folder(tmp_path, n=120)
    lines = analysis.compact_lines(library.read_transcript(folder))
    expected = analysis.windows(lines, limit=analysis.window_chars(analysis.UNKNOWN_CONTEXT), max_windows=None)
    assert len(expected) > 1
    reply = json.dumps({"phrase_types": {}, "insights": []})
    runner = UsageRunner(*([reply] * len(expected)))
    analysis.run(folder, runner, _types_only(), provider="openai-compatible")
    assert len(runner.calls) == len(expected)
    assert all(c["on_cut"] == "keep" for c in runner.calls)  # обрезку анализ отмечает сам
