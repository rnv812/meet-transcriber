"""Карта базы знаний и буквальные исполнители просьб агента
(`meet.assist.kb_prep`): карта под бюджет с папкой группы целиком, read /
search / list только внутри базы и библиотеки, исключения везде, защита от
выхода за корень. Только временные папки; база и встречи выдуманы."""

import json
import os
import time
import zipfile

import pytest

from meet import groups, library, settings
from meet.assist import kb_prep


def _note(kb, rel, text, mtime=None):
    path = kb / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _docx(path, text):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return path


def _kb(tmp_path):
    kb = tmp_path / "kb"
    _note(kb, "Проекты/Альфа/План запуска.md",
          "# План запуска\n\nЗапуск платёжного шлюза — 15 ноября.\n\n## Риски\n\nСертификация.\n", mtime=1_700_000_000)
    _note(kb, "Проекты/Альфа/Вебхуки.md", "# Вебхуки\n\nВ Альфе вебхуки подписываются ключом.\n", mtime=1_700_000_100)
    _docx(kb / "Проекты/Альфа/Договор.docx", "Договор с партнёром на эквайринг.")
    _note(kb, "Справочник/SLA.md", "# SLA\n\nSLA поддержки — 4 часа.\n", mtime=1_600_000_000)
    _note(kb, "Справочник/Глоссарий.md", "**Эквайринг** — приём карт.\n", mtime=1_650_000_000)
    _note(kb, "Входящие.md", "Разобрать: вебхукам нужен ретрай.\n")
    _note(kb, "Личное/Дневник.md", "# Дневник\n\nЛичные мысли про вебхуки шлюза.\n")
    _note(kb, ".obsidian/workspace.md", "служебное про вебхуки\n")
    return kb


def _rec(lib, name, *, gid=None, title=None, summary=None, transcript=None):
    folder = lib / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    meta = {}
    if gid:
        meta["group"] = gid
    if title:
        meta.update(title=title, title_source="user")
    if meta:
        library.write_meta(folder, meta)
    if summary:
        (folder / "summary.md").write_text(summary, encoding="utf-8")
    if transcript:
        segments = [{"start": i * 5.0, "end": i * 5.0 + 4, "speaker": who, "text": text}
                    for i, (who, text) in enumerate(transcript)]
        (folder / "transcript.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False),
                                                encoding="utf-8")
    return folder


def _library(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    alpha = groups.create(lib, "Проект Альфа")["id"]
    beta = groups.create(lib, "Бета")["id"]
    groups.update(lib, alpha, kb_folder="Проекты/Альфа")
    _rec(lib, "2026-09-25_10-00", gid=alpha, title="Сертификация", summary="# Итоги\n\nРешили про сертификацию шлюза.\n")
    _rec(lib, "2026-09-30_10-00", gid=alpha, title="Синк по запуску", summary="# Итоги\n\nРешили: запуск шлюза 15.11.\n",
         transcript=[("Демьян", "Вебхуки шлюза готовы?"), ("Вы", "Да, к пятнице.")])
    _rec(lib, "2026-10-01_10-00", gid=beta, title="Бета-обзор", summary="# Итоги\n\nДругая группа.\n")
    current = _rec(lib, "2026-10-06_10-00", gid=alpha, title="Сегодня")
    return lib, alpha, beta, current


def _base(tmp_path, exclude=("Личное/",)):
    kb = _kb(tmp_path)
    lib, alpha, beta, current = _library(tmp_path)
    return kb_prep.KnowledgeBase(kb, exclude=exclude, library_root=lib), lib, alpha, beta, current


# --- карта ------------------------------------------------------------------------------


def test_map_lists_group_folder_in_full_then_other_folders_and_group_meetings(tmp_path):
    kb, _lib, alpha, _beta, current = _base(tmp_path)
    text = kb.kb_map(group=alpha, current=current)
    lines = text.splitlines()
    group_at = lines.index("[папка группы] Проекты/Альфа/ — 3 документа:")
    assert lines[group_at + 1:group_at + 4] == ["  - Вебхуки.md", "  - Договор.docx", "  - План запуска.md"]
    others = [line for line in lines if " — " in line and not line.startswith(("[", "-", "База"))]
    assert others == ["(корень) — 1 документ: Входящие.md",
                      "Справочник/ — 2 документа: Глоссарий.md · SLA.md"]  # свежие первыми
    assert "- meet:2026-09-30_10-00 · 2026-09-30 · Синк по запуску" in lines
    assert "- meet:2026-09-25_10-00 · 2026-09-25 · Сертификация" in lines
    assert "Бета-обзор" not in text and "Сегодня" not in text  # чужая группа и текущая встреча
    # без содержимого, без исключённого и служебного
    assert "15 ноября" not in text and "Личное" not in text and "Дневник" not in text and ".obsidian" not in text
    assert kb.kb_map(group=alpha, current=current) is text  # один раз на сессию


def test_map_without_group_kb_or_when_switched_off(tmp_path):
    kb, lib, alpha, _beta, current = _base(tmp_path)
    plain = kb.kb_map()
    assert "[папка группы]" not in plain and "Проекты/Альфа/ — 3 документа: " in plain and "meet:" not in plain
    no_kb = kb_prep.KnowledgeBase(None, library_root=lib)
    assert no_kb.kb_map(group=alpha).startswith("Прошлые встречи группы «Проект Альфа»")
    off = kb_prep.KnowledgeBase(tmp_path / "kb", library_root=lib, show_map=False)
    hidden = off.kb_map(group=alpha, current=current)
    # без структуры базы, но со встречами группы (это данные Meet, не базы)
    assert "База знаний" not in hidden and "Проекты" not in hidden
    assert hidden.startswith("Прошлые встречи группы «Проект Альфа»")
    assert kb_prep.KnowledgeBase(tmp_path / "kb", show_map=False).kb_map() == ""


def test_map_fits_the_budget_on_a_big_kb(tmp_path):
    root = tmp_path / "kb"
    for i in range(2000):
        _note(root, f"Раздел {i % 40:02d}/Подраздел {i % 7}/Заметка про тему {i}.md", f"текст {i}",
              mtime=1_600_000_000 + i)
    for i in range(12):
        _note(root, f"Клиенты/Альфа/Документ {i}.md", "x")
    lib = tmp_path / "lib"
    lib.mkdir()
    gid = groups.create(lib, "Альфа")["id"]
    groups.update(lib, gid, kb_folder="Клиенты/Альфа")
    for day in range(1, 16):
        _rec(lib, f"2026-09-{day:02d}_10-00", gid=gid, title=f"Встреча {day}")
    kb = kb_prep.KnowledgeBase(root, library_root=lib)
    start = time.perf_counter()
    text = kb.kb_map(group=gid)
    assert time.perf_counter() - start < 5.0  # обычно доли секунды; запас на медленную машину
    assert len(text) <= kb_prep.MAP_BUDGET_CHARS
    assert all(f"  - Документ {i}.md" in text for i in range(12))  # папка группы целиком
    assert "… ещё" in text  # остальное сжато
    assert sum(1 for line in text.splitlines() if line.startswith("- meet:")) == kb_prep.MAP_MEETINGS
    for budget in (3000, 800, 200):
        small = kb_prep.KnowledgeBase(root, library_root=lib).kb_map(group=gid, budget_chars=budget)
        assert len(small) <= budget
    tight = kb_prep.KnowledgeBase(root, library_root=lib).kb_map(group=gid, budget_chars=1500)
    assert "[папка группы] Клиенты/Альфа/ — 12 документов:" in tight and "… ещё" in tight


# --- read --------------------------------------------------------------------------------


def test_read_documents_and_meetings_with_locators(tmp_path):
    kb, _lib, _alpha, _beta, _current = _base(tmp_path)
    got = kb.kb_read(["Проекты/Альфа/План запуска.md", "Проекты\\Альфа\\Договор.docx",
                      "meet:2026-09-30_10-00/summary.md", "meet:2026-09-30_10-00/transcript.md"])
    assert [g["path"] for g in got] == ["Проекты/Альфа/План запуска.md", "Проекты/Альфа/Договор.docx",
                                        "meet:2026-09-30_10-00/summary.md", "meet:2026-09-30_10-00/transcript.md"]
    assert got[0]["text"].startswith("[План запуска]\n# План запуска\nЗапуск платёжного шлюза — 15 ноября.")
    assert "[Риски]" in got[0]["text"] and got[0]["truncated"] is False
    assert got[1]["text"] == "Договор с партнёром на эквайринг."
    assert "запуск шлюза 15.11" in got[2]["text"]
    assert "Вебхуки шлюза готовы?" in got[3]["text"] and "Демьян" in got[3]["text"]


def test_read_limits(tmp_path, monkeypatch):
    kb, *_ = _base(tmp_path)
    _note(kb.root, "Большой.md", "\n\n".join("абзац " * 50 for _ in range(200)))
    monkeypatch.setattr(kb_prep, "READ_MAX_CHARS", 1000)
    (big,) = kb.kb_read("Большой.md")
    assert big["truncated"] and big["text"].endswith(kb_prep.CUT.format(1000))
    assert len(big["text"]) <= 1000 + len(kb_prep.CUT.format(1000)) + 1
    monkeypatch.setattr(kb_prep, "READ_MAX_FILES", 2)
    got = kb.kb_read(["Входящие.md", "Справочник/SLA.md", "Справочник/Глоссарий.md"])
    assert [("error" in g) for g in got] == [False, False, True]
    assert kb.kb_read("Справочник")[0]["error"] == kb_prep.NOT_A_FILE.format("Справочник")
    assert "Нет такого" in kb.kb_read("Нет.md")[0]["error"]
    monkeypatch.setattr(kb_prep.materials, "MAX_INPUT_BYTES", 10)
    kb._parsed.clear()
    assert "20 МБ" in kb.kb_read("Входящие.md")[0]["error"]


@pytest.mark.parametrize("bad", [
    "../секрет.md", "Проекты/../../секрет.md", "/etc/passwd", "C:/Windows/win.ini", "C:secret.md",
    "\\\\server\\share\\x.md", "Проекты/./Альфа/План запуска.md", ".obsidian/workspace.md",
    "Личное/Дневник.md", "личное/дневник.md", "meet:../lib/x", "meet:2026-09-30_10-00/../../kb/Входящие.md",
    "meet:2026-09-30_10-00/meta.json", "meet:2026-09-30_10-00/sys.opus", "meet:.deleting-x", "meet:нет",
    "meet:2026-09-30_10-00/a/b", "Входящие.md\x00.png", 42,
    # приёмы Windows: регистр, точки и пробелы в конце имени, потоки NTFS,
    # длинные пути, устройства, переменные среды и домашняя папка
    "ЛИЧНОЕ/Дневник.md", "Личное./Дневник.md", "Личное /Дневник.md", "Входящие.md::$DATA",
    "\\\\?\\C:\\Windows\\win.ini", "CON", "CON.md", "NUL.md", "COM1.md", "AUX.txt", "Проекты/CON.md",
    "~/секрет.md", "%USERPROFILE%/секрет.md", "..\\секрет.md",
])
def test_paths_outside_or_excluded_are_rejected_everywhere(tmp_path, bad):
    kb, *_ = _base(tmp_path)
    (tmp_path / "секрет.md").write_text("секрет", encoding="utf-8")
    (got,) = kb.kb_read([bad])
    assert "error" in got and "text" not in got
    assert "error" in kb.kb_list(bad)
    assert "error" in kb.kb_search("секрет вебхуки", bad)


def _dir_link(target, link):
    """Ссылка на папку: symlink, а без прав на него (Windows) — junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("нет прав создавать символические ссылки")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_links_out_of_the_root_or_into_excluded_are_rejected(tmp_path):
    kb, *_ = _base(tmp_path)
    outside = tmp_path / "снаружи"
    outside.mkdir()
    (outside / "секрет.md").write_text("секрет снаружи", encoding="utf-8")
    _dir_link(outside, kb.root / "ссылка")
    _dir_link(kb.root / "Личное", kb.root / "Открытое")
    bad = ["ссылка/секрет.md", "Открытое/Дневник.md"]
    try:
        os.symlink(outside / "секрет.md", kb.root / "секрет.md")
        bad.append("секрет.md")
    except (OSError, NotImplementedError):
        pass  # ссылку на файл без прав не создать — проверяем папки
    for path in bad:
        assert "error" in kb.kb_read(path)[0], path
    assert "error" in kb.kb_list("ссылка") and "error" in kb.kb_search("секрет", "ссылка")
    names = kb.kb_list("")
    assert "секрет.md" not in names["files"]
    assert {f["name"] for f in names["folders"]} == {"Проекты", "Справочник"}
    assert all("секрет" not in h["snippet"] and "Личные" not in h["snippet"]
               for h in kb.kb_search("секрет снаружи личные мысли")["hits"])
    text = kb.kb_map()
    assert "секрет" not in text and "Дневник" not in text


# --- search ------------------------------------------------------------------------------


def test_search_by_word_stems_with_locators(tmp_path):
    kb, *_ = _base(tmp_path)
    got = kb.kb_search("вебхуки шлюза")
    assert got["in"] == "(корень)" and got["words"] == 2
    paths = [(h["path"], h["matched"]) for h in got["hits"]]
    # «вебхукам», «Вебхуки» — одна основа; со всеми словами — выше
    assert ("Проекты/Альфа/Вебхуки.md", 1) in paths and ("Входящие.md", 1) in paths
    assert all(not p.startswith(("Личное", ".obsidian")) for p, _ in paths)
    hit = kb.kb_search("эквайринг", "Проекты/Альфа")["hits"]
    assert hit == [{"path": "Проекты/Альфа/Договор.docx", "loc": "", "matched": 1,
                    "snippet": "Договор с партнёром на эквайринг."}]
    sla = kb.kb_search("SLA", "Справочник/SLA.md")["hits"][0]
    assert (sla["path"], sla["loc"]) == ("Справочник/SLA.md", "SLA")
    both = kb.kb_search("запуск шлюза 15 ноября")["hits"]
    assert both[0]["path"] == "Проекты/Альфа/План запуска.md" and both[0]["matched"] == 4
    assert kb.kb_search("ну вот это")["error"] == kb_prep.NO_QUERY


def test_search_in_meetings(tmp_path):
    kb, *_ = _base(tmp_path)
    got = kb.kb_search("решили запуск", "meet:")["hits"]
    assert got[0]["path"] == "meet:2026-09-30_10-00/summary.md" and got[0]["matched"] == 2
    one = kb.kb_search("вебхуки", "meet:2026-09-30_10-00")["hits"]
    assert [h["path"] for h in one] == ["meet:2026-09-30_10-00/transcript.md"]


def test_search_limits(tmp_path, monkeypatch):
    kb, *_ = _base(tmp_path)
    for i in range(10):
        _note(kb.root, f"Много/Заметка {i}.md", f"бюджет релиза {i}")
    monkeypatch.setattr(kb_prep, "SEARCH_MAX_HITS", 3)
    got = kb.kb_search("бюджет", "Много")
    assert len(got["hits"]) == 3 and got["more"] == 7 and got["files"] == 10
    monkeypatch.setattr(kb_prep, "SEARCH_MAX_FILES", 4)
    assert kb.kb_search("бюджет", "Много")["files"] == 4
    long = _note(kb.root, "Длинный.md", "начало " + "вода " * 400 + "редкостное слово " + "вода " * 400)
    snippet = kb.kb_search("редкостное", "Длинный.md")["hits"][0]["snippet"]
    assert len(snippet) <= kb_prep.SNIPPET_CHARS and "редкостное" in snippet and long.exists()


# --- list --------------------------------------------------------------------------------


def test_list_folders_files_and_meetings(tmp_path, monkeypatch):
    kb, *_ = _base(tmp_path)
    root = kb.kb_list("")
    assert root["path"] == "(корень)" and root["files"] == ["Входящие.md"]
    assert root["folders"] == [{"name": "Проекты", "docs": 3}, {"name": "Справочник", "docs": 2}]
    assert kb.kb_list("Проекты/Альфа")["files"] == ["Вебхуки.md", "Договор.docx", "План запуска.md"]
    meetings = kb.kb_list("meet:")["meetings"]
    assert [m["id"] for m in meetings] == ["2026-10-06_10-00", "2026-10-01_10-00", "2026-09-30_10-00",
                                          "2026-09-25_10-00"]
    assert meetings[2] == {"id": "2026-09-30_10-00", "path": "meet:2026-09-30_10-00", "date": "2026-09-30",
                           "title": "Синк по запуску", "group": "Проект Альфа"}
    one = kb.kb_list("meet:2026-09-30_10-00")
    assert one["files"] == ["transcript.md", "summary.md"] and one["title"] == "Синк по запуску"
    assert kb.kb_list("Входящие.md")["error"] == kb_prep.NOT_A_FOLDER.format("Входящие.md")
    monkeypatch.setattr(kb_prep, "LIST_MAX", 1)
    cut = kb.kb_list("")
    assert len(cut["folders"]) + len(cut["files"]) == 1 and cut["more"] == 2
    assert len(kb.kb_list("meet:")["meetings"]) == 1 and kb.kb_list("meet:")["more"] == 3


def test_without_kb_or_library(tmp_path):
    kb = kb_prep.KnowledgeBase(None)
    assert kb.kb_read("x.md")[0]["error"] == kb_prep.NOT_CONFIGURED
    assert kb.kb_list("meet:")["error"] == kb_prep.NO_LIBRARY
    assert kb.kb_map() == ""


def test_for_settings(tmp_path):
    kb_dir = _kb(tmp_path)
    lib, alpha, _beta, current = _library(tmp_path)
    cfg = settings.Settings.from_raw({"version": settings.SCHEMA_VERSION,
                                      "assistant": {"knowledge_dir": str(kb_dir)},
                                      "assist": {"kb_exclude": ["Справочник/"], "kb_map": True}})
    kb = kb_prep.for_settings(cfg, lib)
    assert "error" in kb.kb_read("Справочник/SLA.md")[0]
    assert "Справочник" not in kb_prep.kb_map(kb, group=alpha, current=current)
    assert "Личное" in kb_prep.kb_map(kb)  # исключения — только из настроек
    off = settings.Settings.from_raw({"version": settings.SCHEMA_VERSION,
                                      "assistant": {"knowledge_dir": str(kb_dir)}, "assist": {"kb_map": False}})
    assert "База знаний" not in kb_prep.for_settings(off, lib).kb_map(group=alpha)


def test_exclude_paths_for_provider_deny_rules(tmp_path):
    kb_dir = _kb(tmp_path)
    (kb_dir / "Work [old]").mkdir()
    (kb_dir / "Работа" / "Клиенты").mkdir(parents=True)
    base = kb_dir.resolve()
    got = kb_prep.kb_exclude_paths(kb_dir, [" личное/ ", r"работа\клиенты", "Work [old]", "Нет такой",
                                            "../вне", "/abs", "", 5, "Личное/*"])
    # как на диске, абсолютные, без синтаксиса шаблонов, без повторов и несуществующего
    assert got == [str(base / "Личное"), str(base / "Работа" / "Клиенты"), str(base / "Work [old]")]
    assert kb_prep.kb_exclude_paths(None, ["Личное/"]) == []
    kb = kb_prep.KnowledgeBase(kb_dir, exclude=settings.KB_EXCLUDE_DEFAULT)
    assert kb.exclude_paths() == [str(base / "Личное")]  # .trash нет на диске


@pytest.mark.skipif(os.name == "nt", reason="«*» в имени папки Windows не допускает")
def test_exclude_paths_keep_star_in_names(tmp_path):
    kb_dir = tmp_path / "kb"
    (kb_dir / "a*b").mkdir(parents=True)
    (kb_dir / "aXb").mkdir()
    assert kb_prep.kb_exclude_paths(kb_dir, ["a*b"]) == [str((kb_dir / "a*b").resolve())]


def test_exclude_paths_name_the_link_and_its_target(tmp_path):
    kb_dir = _kb(tmp_path)
    secret = tmp_path / "секреты"
    secret.mkdir()
    _dir_link(secret, kb_dir / "Секреты")
    got = kb_prep.kb_exclude_paths(kb_dir, ["секреты"])
    assert got == [str(secret.resolve()), str(kb_dir.resolve() / "Секреты")]


def test_group_folder_with_other_casing_is_listed_in_full(tmp_path):
    kb, lib, alpha, _beta, current = _base(tmp_path)
    groups.update(lib, alpha, kb_folder="проекты/альфа")  # без базы — как ввели
    text = kb.kb_map(group=alpha, current=current)
    assert "[папка группы] Проекты/Альфа/ — 3 документа:" in text
    assert "Проекты/Альфа/ — 3" not in text.replace("[папка группы] Проекты/Альфа/", "")


def test_a_broken_file_does_not_break_search_or_read(tmp_path):
    kb, *_ = _base(tmp_path)
    (kb.root / "Справочник" / "битый.docx").write_bytes(b"PK\x03\x04 not really a zip")
    (kb.root / "Справочник" / "длинный.csv").write_text("a;b\nвебхуки;ок\nдлинное;\"" + "я" * 2_000_000 + "\"\n",
                                                        encoding="utf-8")
    got = kb.kb_search("вебхуки", "Справочник")
    assert "error" not in got and got["hits"][0]["path"] == "Справочник/длинный.csv"
    read = kb.kb_read(["Справочник/битый.docx", "Справочник/SLA.md"])
    assert "error" in read[0] and read[1]["text"].startswith("[SLA]")


def test_read_budget_spent_is_an_explicit_error(tmp_path, monkeypatch):
    kb, *_ = _base(tmp_path)
    monkeypatch.setattr(kb_prep, "READ_TOTAL_CHARS", 30)
    first, second = kb.kb_read(["Проекты/Альфа/План запуска.md", "Справочник/SLA.md"])
    assert first["truncated"] and second == {"path": "Справочник/SLA.md", "error": kb_prep.READ_SPENT}


def test_meeting_header_is_dropped_when_no_meeting_fits(tmp_path):
    kb, _lib, alpha, _beta, current = _base(tmp_path)
    tiny = kb_prep.KnowledgeBase(kb.root, library_root=kb.library_root, show_map=False)
    assert tiny.kb_map(group=alpha, current=current, budget_chars=70) == ""
