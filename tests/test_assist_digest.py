import pytest

from meet.assist.digest import Digest, DeltaParseError, NO_NEWS, build_tick_prompt


def test_add_and_render_grouped_by_section():
    d = Digest()
    assert d.apply_delta("ADD Релиз 6.1 :: Двигаем срок на неделю")
    assert d.apply_delta("ADD Релиз 6.1 :: QA просит стенд\nADD Найм :: Два кандидата на next week")
    md = d.render()
    assert "## Релиз 6.1" in md and "- [1] Двигаем срок на неделю" in md
    assert "## Найм" in md and "- [3]" in md
    assert d.version == 2


def test_edit_replaces_point_text():
    d = Digest()
    d.apply_delta("ADD Релиз :: Срок пятница")
    d.apply_delta("EDIT 1 :: Срок перенесли на понедельник")
    assert "Срок перенесли на понедельник" in d.render()
    assert "[2]" not in d.render()


def test_no_news_is_noop():
    d = Digest()
    assert d.apply_delta(NO_NEWS) is False
    assert d.version == 0


def test_garbage_raises_and_leaves_state_intact():
    d = Digest()
    d.apply_delta("ADD Тема :: Тезис")
    with pytest.raises(DeltaParseError):
        d.apply_delta("Вот обновлённый дайджест:\n## Тема\n- всё хорошо")
    assert d.version == 1 and "Тезис" in d.render()


def test_edit_unknown_point_raises():
    d = Digest()
    with pytest.raises(DeltaParseError):
        d.apply_delta("EDIT 7 :: нет такого")


def test_tick_prompt_contains_digest_lines_and_format():
    p = build_tick_prompt("## Тема\n- [1] Тезис", ["[00:01:00] Вы: привет"])
    assert "## Тема" in p and "[00:01:00] Вы: привет" in p
    assert "ADD" in p and "EDIT" in p and NO_NEWS in p
