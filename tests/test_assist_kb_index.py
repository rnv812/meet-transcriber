"""Указатель терминов базы знаний для тиков живого ассистента.

Файлы базы и реплики выдуманы."""

from meet.assist.kb_index import MAX_FILES, TermIndex


def _kb(tmp_path):
    kb = tmp_path / "kb"
    (kb / "Проекты").mkdir(parents=True)
    (kb / ".obsidian").mkdir()
    (kb / "Проекты" / "Платёжный шлюз.md").write_text(
        "---\ntags: [проект]\n---\n# Платёжный шлюз\n\n"
        "Сервис приёма платежей от партнёров. Отвечает команда Альфа.\n\n"
        "## Вебхуки\n\nУведомления о статусе платежа, подписаны секретом.\n\n"
        "## Итоги\n\nобщий раздел\n",
        encoding="utf-8")
    (kb / "Глоссарий.md").write_text(
        "Здесь собраны понятия.\n\n**Эквайринг** — приём карт через банк-партнёр.\n",
        encoding="utf-8")
    (kb / ".obsidian" / "Скрытое.md").write_text("# Секретная тема\n\nне индексируется\n",
                                                 encoding="utf-8")
    (kb / "картинка.png").write_bytes(b"\x89PNG")
    return kb


def test_index_collects_names_headings_and_bold_terms(tmp_path):
    index = TermIndex.build(_kb(tmp_path))
    labels = {t.label for t in index.terms}
    assert {"Платёжный шлюз", "Вебхуки", "Эквайринг", "Глоссарий"} <= labels
    assert "Итоги" not in labels            # общий заголовок — не термин
    assert "Секретная тема" not in labels   # служебные папки не читаются


def test_excerpts_found_by_inflected_mention(tmp_path):
    index = TermIndex.build(_kb(tmp_path))
    found = index.excerpts(["[00:01:00] Ольга: а по вебхукам что, секрет уже выдали?"])
    assert len(found) == 1
    hit = found[0]
    assert hit["term"] == "Вебхуки" and hit["ref"] == "Проекты/Платёжный шлюз.md"
    assert "подписаны секретом" in hit["text"]


def test_excerpts_capped_and_deduplicated_by_file(tmp_path):
    index = TermIndex.build(_kb(tmp_path))
    lines = ["[00:02:00] Вы: платёжный шлюз и вебхуки, плюс эквайринг и глоссарий"]
    found = index.excerpts(lines, limit=3)
    refs = [f["ref"] for f in found]
    assert len(found) <= 3 and len(refs) == len(set(refs))
    assert all(len(f["text"]) <= 300 for f in found)


def test_no_match_no_excerpts(tmp_path):
    index = TermIndex.build(_kb(tmp_path))
    assert index.excerpts(["[00:00:05] Вы: всем привет, начинаем"]) == []


def test_missing_or_empty_folder_gives_empty_index(tmp_path):
    assert len(TermIndex.build(tmp_path / "нет")) == 0
    assert len(TermIndex.build(None)) == 0
    assert TermIndex.build(tmp_path).excerpts(["что угодно"]) == []


def test_file_count_is_capped(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    for i in range(MAX_FILES + 20):
        (kb / f"Заметка номер {i:04d}.md").write_text("текст", encoding="utf-8")
    index = TermIndex.build(kb)
    assert len({t.ref for t in index.terms}) <= MAX_FILES


def test_skipped_folders_are_not_entered(tmp_path, monkeypatch):
    import os

    kb = _kb(tmp_path)
    (kb / "node_modules" / "pkg").mkdir(parents=True)
    (kb / "node_modules" / "pkg" / "README.md").write_text("# Пакет\n\nчужое\n", encoding="utf-8")
    seen = []
    real_walk = os.walk

    def walk(top, *a, **kw):
        for here, dirs, names in real_walk(top, *a, **kw):
            seen.append(os.path.basename(here))
            yield here, dirs, names

    monkeypatch.setattr("meet.assist.kb_index.os.walk", walk)
    labels = {t.label for t in TermIndex.build(kb).terms}
    assert "Пакет" not in labels
    assert "node_modules" not in seen and ".obsidian" not in seen and "pkg" not in seen
