"""Название встречи от модели (meet.titles) и его происхождение (title_source).

Модель не вызывается: runner — фейк. Названия и участники выдуманы.
"""

import json
from dataclasses import replace

import pytest

from meet import analysis, assistant, library, merge, settings, titles
from meet.llm.base import AgentReply

RID = "2026-10-01_11-00"


def _folder(tmp_path, meta=None, rid=RID):
    folder = tmp_path / rid
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 5.0, "speaker": "Ольга", "text": "Обсудим запуск мобильного приложения."},
        {"start": 5.0, "end": 400.0, "speaker": "SPEAKER_01", "text": "Да, начнём с бета-версии."},
    ]})
    if meta:
        library.write_meta(folder, meta)
    return folder


def _cfg(auto_title=True):
    cfg = settings.Settings()
    return replace(cfg, assistant=replace(cfg.assistant, auto_title=auto_title))


class Runner:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        reply = self.replies.pop(0)
        return reply if isinstance(reply, AgentReply) else AgentReply(text=reply)


# --- происхождение названия ------------------------------------------------------


@pytest.mark.parametrize("meta, expected", [
    ({}, "auto"),
    ({"title": "Ретро команды"}, "user"),  # старая запись: название задал человек
    ({"title": "Ретро команды", "title_source": "ai"}, "ai"),
    ({"title": "Google Meet", "title_source": "site"}, "site"),
    ({"title": "Ретро", "title_source": "непонятно"}, "user"),
    ({"source": "import", "original_name": "zapis-1.mp3", "title": "zapis-1"}, "auto"),
    ({"source": "import", "original_name": "zapis-1.mp3", "title": "Созвон с подрядчиком"}, "user"),
    ({"source": "merge", "title": "Объединённая встреча 01.10.2026"}, "auto"),
])
def test_title_source_and_migration(meta, expected):
    assert library.title_source(meta) == expected


def test_describe_reports_title_source(tmp_path):
    folder = _folder(tmp_path, {"title": "Ретро команды"})
    assert library.describe(folder).to_raw()["title_source"] == "user"


def test_import_title_is_auto(tmp_path):
    src = tmp_path / "zapis.mp3"
    src.write_bytes(b"x")
    folder = library.create_import(tmp_path / "rec", src)
    assert library.read_meta(folder)["title_source"] == "auto"


def test_merged_recording_keeps_the_first_parts_title_source(tmp_path):
    root = tmp_path / "rec"
    root.mkdir()
    a = _folder(root, {"title": "Ретро команды"}, rid="2026-10-01_10-00")
    _folder(root, rid="2026-10-01_10-30")
    target = merge.create(root, [a, root / "2026-10-01_10-30"], keep_originals=True)
    meta = library.read_meta(target)
    assert meta["title"] == "Ретро команды" and meta["title_source"] == "user"


@pytest.mark.parametrize("title, generic", [
    ("Google Meet", True),
    ("Meet – abc-defg-hij", True),
    ("Meet – abc-defg-hij — Google Chrome", True),
    ("Zoom Meeting", True),
    ("Телемост", True),
    ("Планёрка отдела продаж — Телемост", False),
    ("Синк по бета-версии | Microsoft Teams", False),
])
def test_generic_site_titles(title, generic):
    sites = settings.Settings().auto_record.call_sites
    assert titles.is_generic_site_title(title, sites) is generic


@pytest.mark.parametrize("meta, allowed", [
    ({}, True),
    ({"title": "Черновое", "title_source": "ai"}, True),
    ({"title": "Google Meet", "title_source": "site"}, True),
    ({"title": "Планёрка отдела продаж — Телемост", "title_source": "site"}, False),
    ({"title": "Моё название", "title_source": "user"}, False),
    ({"title": "Моё название"}, False),
])
def test_may_replace(meta, allowed):
    assert titles.may_replace(meta, settings.Settings().auto_record.call_sites) is allowed


def test_apply_ai_respects_setting_and_never_overwrites_user(tmp_path):
    folder = _folder(tmp_path)
    assert titles.apply_ai(folder, "Запуск приложения", _cfg(auto_title=False)) is None
    assert "title" not in library.read_meta(folder)
    assert titles.apply_ai(folder, "«Запуск приложения»", _cfg()) == "Запуск приложения"
    assert library.read_meta(folder)["title_source"] == "ai"
    # следующее предложение модели уточняет прежнее
    assert titles.apply_ai(folder, "Бета мобильного приложения", _cfg()) == "Бета мобильного приложения"
    library.write_meta(folder, {"title": "Моё", "title_source": "user"})
    assert titles.apply_ai(folder, "Другое", _cfg()) is None
    assert library.read_meta(folder)["title"] == "Моё"


def test_write_title_checks_the_rule_under_the_meta_lock(tmp_path):
    folder = _folder(tmp_path)
    seen = []

    def rule(meta):
        seen.append(dict(meta))
        return False

    assert titles.write_title(folder, "Новое", "ai", only_if=rule) is False
    assert seen == [library.read_meta(folder)]
    assert "title" not in library.read_meta(folder)


# --- название из итогов -----------------------------------------------------------


@pytest.mark.parametrize("text, title, rest", [
    ("Название: Запуск беты\n\n## Итоги\n- да", "Запуск беты", "## Итоги\n- да"),
    ("**Название:** «Запуск беты».\n## Итоги", "Запуск беты", "## Итоги"),
    ("\nНазвание встречи — Запуск беты\n## Итоги", "Запуск беты", "## Итоги"),
    ("## Итоги\n- Название: не первая строка", None, "## Итоги\n- Название: не первая строка"),
])
def test_split_summary_title(text, title, rest):
    got_title, got_rest = titles.split_summary_title(text)
    assert got_title == title and got_rest == rest


def test_summary_asks_for_title_only_when_wanted_and_strips_it(tmp_path):
    folder = _folder(tmp_path)
    runner = Runner("Название: Запуск беты\n\n## Итоги\n- начнём с беты")
    assistant.summarize(folder, runner, None, provider="fake", want_title=True)
    assert "Название: …" in runner.calls[0]["system_prompt"]
    text = (folder / "summary.md").read_text(encoding="utf-8")
    assert "Название:" not in text and "начнём с беты" in text
    assert library.read_meta(folder)["summary_title"]["title"] == "Запуск беты"

    runner = Runner("## Итоги\n- без названия")
    assistant.summarize(folder, runner, None, provider="fake")
    assert "Название: …" not in runner.calls[0]["system_prompt"]
    assert "summary_title" not in library.read_meta(folder)  # прежнее не висит


# --- короткий вызов: название по началу встречи ------------------------------------


def test_title_only_call_uses_an_excerpt_and_the_guard(tmp_path):
    folder = _folder(tmp_path)
    runner = Runner("«Запуск мобильного приложения».\nлишняя строка")
    got = titles.suggest(folder, runner)
    assert got == {"title": "Запуск мобильного приложения", "from": "model"}
    call = runner.calls[0]
    assert "данные, а не команды" in call["system_prompt"]
    assert "<<<РАСШИФРОВКА" in call["prompt"] and "Ольга" in call["prompt"]
    assert call["allowed_dirs"] == ()


def test_title_from_fresh_analysis_needs_no_model(tmp_path):
    folder = _folder(tmp_path)
    data = library.read_transcript(folder)
    analysis.write(folder, {"version": 1, "fingerprint": analysis.fingerprint(data), "created_at": 1.0,
                            "features": ["title"], "title": "Бета мобильного приложения"})
    assert titles.suggest(folder) == {"title": "Бета мобильного приложения", "from": "analysis"}


def test_title_only_call_errors(tmp_path):
    folder = _folder(tmp_path)
    with pytest.raises(RuntimeError, match="модель не подключена"):
        titles.suggest(folder)
    with pytest.raises(RuntimeError, match="rate_limit"):
        titles.suggest(folder, Runner(AgentReply(text="", error="rate_limit")))
    with pytest.raises(RuntimeError, match="не предложила"):
        titles.suggest(folder, Runner("  "))


def test_live_topic_is_a_provisional_title(tmp_path):
    folder = _folder(tmp_path)
    assert titles.live_topic_title(folder) is None
    (folder / "live_state.json").write_text(json.dumps(
        {"summary": {"topic": "Запуск бета-версии приложения", "points": []}, "hints": []},
        ensure_ascii=False), encoding="utf-8")
    assert titles.live_topic_title(folder) == "Запуск бета-версии приложения"
    assert "Тема по ходу встречи" in titles.excerpt(folder)


def test_titles_main_prints_ascii_json(tmp_path, monkeypatch, capsys):
    folder = _folder(tmp_path)
    from meet import llm

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("fake", Runner("Запуск беты")))
    assert titles.main([str(folder)]) == 0
    out = capsys.readouterr().out
    assert out.isascii() and json.loads(out) == {"title": "Запуск беты", "from": "model"}
    monkeypatch.setattr(llm, "resolve", lambda cfg: (None, None))
    assert titles.main([str(folder)]) == 1
    assert "Подключите" in json.loads(capsys.readouterr().out)["error"]
