"""Уборка после профилей людей (убраны в 0.3.2): заметки — в один файл,
удалить только то, что писал Meet, ни одной заметки не потерять, голоса не
трогать, повтор безвреден. Папка данных — синтетическая; люди и тексты
выдуманы."""

import json
import os
import subprocess
import sys
import time

import pytest

from meet import retired_profiles, settings

HEX32 = "0123456789abcdef" * 2

PID_A = "0123456789abcdef"
PID_B = "fedcba9876543210"
PID_C = "aaaaaaaaaaaaaaaa"


def _voice(voices, name, pid=None):
    data = {"samples": [{"embedding": [0.1, 0.2], "source": "x", "id": "s1"}]}
    if pid:
        data["id"] = pid
    path = voices / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _world(tmp_path):
    data, voices = tmp_path / "data", tmp_path / "voices"
    voices.mkdir(parents=True)
    profiles = data / "profiles"
    (profiles / "_index").mkdir(parents=True)
    _voice(voices, "Вера Соколова", PID_A)
    _voice(voices, "Вадим", PID_B)
    _voice(voices, "Без профиля")
    (profiles / f"{PID_A}.json").write_text('{"version": 1, "summary": "сгенерировано"}', encoding="utf-8")
    (profiles / f"{PID_A}.notes.md").write_text("Любит повестку заранее.\n", encoding="utf-8")
    (profiles / f"{PID_A}.state.json").write_text('{"pending": {"at": 1, "manual": false}}', encoding="utf-8")
    (profiles / f"{PID_B}.json").write_text('{"version": 1}', encoding="utf-8")
    (profiles / f"{PID_B}.notes.md").write_text("  \n", encoding="utf-8")  # пустые — не заметки
    (profiles / f"{PID_C}.notes.md").write_text("Человека уже нет в базе.", encoding="utf-8")
    (profiles / "_index" / "2026-09-30_16-04.json").write_text("{}", encoding="utf-8")
    (profiles / "_index" / f".2026-09-30_16-04.json.{HEX32}.tmp").write_text("{", encoding="utf-8")
    (profiles / f".{PID_A}.json.{HEX32}.tmp").write_text("{", encoding="utf-8")
    when = time.mktime((2026, 10, 2, 12, 0, 0, 0, 0, -1))
    os.utime(profiles / f"{PID_A}.notes.md", (when, when))
    config = data / "config.json"
    config.write_text(json.dumps({"llm": {"provider": "codex"},
                                  "profiles": {"enabled": True, "pcm": True}}), encoding="utf-8")
    (data / "llm_stats.json").write_text(json.dumps({
        "profile:claude-code": [{"in": 1, "s": 2, "out": 3}],
        "profile-check:claude-code": [{"in": 1, "s": 2, "out": 3}],
        "analyze:claude-code": [{"in": 1, "s": 2, "out": 3}]}), encoding="utf-8")
    return data, voices, config


def _snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_notes_extracted_everything_else_removed(tmp_path):
    data, voices, config = _world(tmp_path)
    before = _snapshot(voices)

    got = retired_profiles.run(data, voices, config=config)

    notes = data / retired_profiles.NOTES_NAME
    assert got == {"notes": str(notes), "folder": str(data)}
    text = notes.read_text(encoding="utf-8")
    assert text.startswith("# Заметки о людях (из профилей)\n")
    assert "## Вера Соколова\n\n_Заметка от 02.10.2026_\n\nЛюбит повестку заранее." in text
    assert f"## Без имени ({PID_C[:6]})" in text and "Человека уже нет в базе." in text
    assert "Вадим" not in text  # пустая заметка не переносится
    assert "сгенерировано" not in text
    assert text.index("Без имени") < text.index("Вера Соколова")
    # Папка профилей — целиком, с индексом, отметками pending и служебным.
    assert not (data / "profiles").exists()
    # Голоса не тронуты (и id в них остаётся).
    assert _snapshot(voices) == before
    # Секция настроек и замеры задач профилей убраны, остальное на месте.
    assert json.loads(config.read_text(encoding="utf-8")) == {"llm": {"provider": "codex"}}
    assert list(json.loads((data / "llm_stats.json").read_text(encoding="utf-8"))) == ["analyze:claude-code"]
    assert retired_profiles.notice(data) == got


def test_without_notes_no_file_but_notice(tmp_path):
    data = tmp_path / "data"
    (data / "profiles" / "_index").mkdir(parents=True)
    (data / "profiles" / f"{PID_A}.json").write_text("{}", encoding="utf-8")
    got = retired_profiles.run(data, tmp_path / "нет-голосов", config=data / "config.json")
    assert got == {"notes": None, "folder": str(data)}
    assert not (data / "profiles").exists()
    assert not list(data.glob("*.md"))
    assert not (data / "config.json").exists()  # файла настроек не было — и не появился


def test_idempotent_and_dismiss(tmp_path):
    data, voices, config = _world(tmp_path)
    first = retired_profiles.run(data, voices, config=config)
    notes = data / retired_profiles.NOTES_NAME
    written = notes.read_bytes()
    # Второй запуск: папки нет — ничего не делает, файл заметок и отметка те же.
    assert retired_profiles.run(data, voices, config=config) is None
    assert notes.read_bytes() == written
    assert retired_profiles.notice(data) == first
    retired_profiles.dismiss(data)
    assert retired_profiles.notice(data) is None
    assert retired_profiles.run(data, voices, config=config) is None
    assert retired_profiles.notice(data) is None
    assert notes.read_bytes() == written


def test_no_profiles_folder_touches_nothing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    config = data / "config.json"
    config.write_text('{"profiles": {"enabled": true}}', encoding="utf-8")
    assert retired_profiles.run(data, None, config=config) is None
    assert config.read_text(encoding="utf-8") == '{"profiles": {"enabled": true}}'
    assert retired_profiles.notice(data) is None


def test_existing_notes_file_not_overwritten(tmp_path):
    data, voices, config = _world(tmp_path)
    mine = data / retired_profiles.NOTES_NAME
    mine.write_text("свой файл человека", encoding="utf-8")
    got = retired_profiles.run(data, voices, config=config)
    assert mine.read_text(encoding="utf-8") == "свой файл человека"
    assert got["notes"] == str(data / "Заметки о людях (из профилей) (2).md")
    assert "Любит повестку заранее." in (data / "Заметки о людях (из профилей) (2).md").read_text(encoding="utf-8")


def test_partial_previous_run_reuses_same_notes_file(tmp_path):
    """Прошлый запуск записал заметки, но папку удалить не успел: тот же файл,
    без второго."""
    data, voices, config = _world(tmp_path)
    notes = retired_profiles.collect_notes(data / "profiles", voices).notes
    (data / retired_profiles.NOTES_NAME).write_text(retired_profiles.notes_text(notes), encoding="utf-8")
    got = retired_profiles.run(data, voices, config=config)
    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)
    assert sorted(p.name for p in data.glob("*.md")) == [retired_profiles.NOTES_NAME]


def test_notes_kept_in_place_when_they_cannot_be_written(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    lines = []

    def broken(path, text):
        raise OSError("диск полон")

    with monkeypatch.context() as mp:
        mp.setattr(retired_profiles, "_write_text", broken)
        got = retired_profiles.run(data, voices, config=config, log=lines.append)
    assert got is None  # и отметку не записать
    # Сгенерированное удалено, непустые заметки остались до следующего запуска.
    assert sorted(p.name for p in (data / "profiles").iterdir()) == sorted(
        [f"{PID_A}.notes.md", f"{PID_C}.notes.md"])
    assert any("заметки не перенесены" in line for line in lines)
    got = retired_profiles.run(data, voices, config=config)
    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)
    assert not (data / "profiles").exists()


def test_old_config_with_profiles_section_loads_and_is_dropped_on_save(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"profiles": {"enabled": True, "pcm": True}, "my_key": 1}), encoding="utf-8")
    cfg = settings.load(path)
    assert not hasattr(cfg, "profiles")
    assert "profiles" not in cfg.to_raw()
    settings.save(cfg, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "profiles" not in raw and raw["my_key"] == 1


def test_patch_ignores_profiles_section(tmp_path):
    path = tmp_path / "config.json"
    updated = settings.patch({"profiles": {"enabled": True}}, path)
    assert "profiles" not in updated.to_raw()
    assert "profiles" not in json.loads(path.read_text(encoding="utf-8"))


def test_resident_api_notice_and_dismiss(tmp_path, monkeypatch):
    from meet import tray_control

    data, voices, config = _world(tmp_path)
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    retired_profiles.run(data, voices, config=config)
    state = tray_control.TrayControl.__new__(tray_control.TrayControl)
    assert state.profiles_removed() == {"notice": {"notes": str(data / retired_profiles.NOTES_NAME),
                                                   "folder": str(data)}}
    assert state.dismiss_profiles_removed() == {"ok": True}
    assert state.profiles_removed() == {"notice": None}


def test_resident_start_cleans_up(tmp_path, monkeypatch):
    from meet import tray

    data, voices, config = _world(tmp_path)
    raw = json.loads(config.read_text(encoding="utf-8"))
    raw["recording"] = {"voices_dir": str(voices)}
    config.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    lines = []
    app = tray.TrayApp()
    monkeypatch.setattr(app, "log", lines.append)
    app._retire_profiles()
    assert not (data / "profiles").exists()
    assert "## Вера Соколова" in (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert "profiles" not in json.loads(config.read_text(encoding="utf-8"))
    assert any("профили людей убраны" in line for line in lines)


# --- только файлы Meet ----------------------------------------------------------


def _link_dir(link, target):
    """Junction на Windows (без прав администратора), symlink в остальных ОС."""
    if sys.platform == "win32":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return
        except (ImportError, OSError) as e:
            pytest.skip(f"junction не создаётся: {e}")
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as e:
        pytest.skip(f"symlink не создаётся: {e}")


def test_profiles_as_junction_removes_only_the_link(tmp_path):
    data, voices, config = _world(tmp_path)
    outside = tmp_path / "elsewhere"
    (data / "profiles").rename(outside)
    (outside / "unrelated-user-file.docx").write_bytes(b"PK user document")
    before = _snapshot(outside)
    _link_dir(data / "profiles", outside)
    lines = []

    assert retired_profiles.run(data, voices, config=config, log=lines.append) is None

    assert not os.path.lexists(data / "profiles")
    assert _snapshot(outside) == before  # цель ссылки — ни байта
    assert not (data / retired_profiles.NOTES_NAME).exists()
    assert any("ссылкой" in line for line in lines)


def test_junction_inside_profiles_is_unlinked_not_followed(tmp_path):
    import shutil

    data, voices, config = _world(tmp_path)
    outside = tmp_path / "index-elsewhere"
    outside.mkdir()
    (outside / "keep.json").write_text("{}", encoding="utf-8")
    shutil.rmtree(data / "profiles" / "_index")
    _link_dir(data / "profiles" / "_index", outside)

    retired_profiles.run(data, voices, config=config)

    assert not (data / "profiles").exists()
    assert (outside / "keep.json").read_text(encoding="utf-8") == "{}"


def test_data_dir_is_a_parent_with_unrelated_profiles_folder(tmp_path):
    """MEET_DATA_DIR указали на родительскую папку, где лежит чужая profiles/."""
    data = tmp_path / "home"
    work = data / "profiles" / "work"
    work.mkdir(parents=True)
    (work / "contract.pdf").write_bytes(b"%PDF-1.7")
    (data / "profiles" / "cv.notes.md").write_text("моё резюме", encoding="utf-8")
    (data / "profiles" / "settings.json").write_text("{}", encoding="utf-8")
    before = _snapshot(data)

    assert retired_profiles.run(data, None, config=data / "config.json") is None

    assert _snapshot(data) == before
    assert not (data / retired_profiles.NOTES_NAME).exists()
    assert retired_profiles.notice(data) is None


def test_foreign_files_inside_profiles_are_kept(tmp_path):
    data, voices, config = _world(tmp_path)
    profiles = data / "profiles"
    (profiles / "мой список.txt").write_text("своё", encoding="utf-8")
    (profiles / "cv.notes.md").write_text("не заметка Meet", encoding="utf-8")
    (profiles / "pcm-eval.json").write_text("{}", encoding="utf-8")
    (profiles / "_index" / "readme.txt").write_text("своё", encoding="utf-8")
    (profiles / "archive").mkdir()
    (profiles / "archive" / f"{PID_A}.json").write_text("{}", encoding="utf-8")

    got = retired_profiles.run(data, voices, config=config)

    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)
    assert sorted(p.relative_to(profiles).as_posix() for p in profiles.rglob("*")) == sorted([
        "_index", "_index/readme.txt", "archive", f"archive/{PID_A}.json",
        "cv.notes.md", "pcm-eval.json", "мой список.txt"])
    assert "не заметка Meet" not in (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")


# --- ни одной заметки не потерять ------------------------------------------------


def test_non_utf8_note_decoded_and_raw_bytes_kept(tmp_path):
    data, voices, config = _world(tmp_path)
    raw = "Договорились созвониться в пятницу — «срок».".encode("cp1251")
    (data / "profiles" / f"{PID_B}.notes.md").write_bytes(raw)

    got = retired_profiles.run(data, voices, config=config)

    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)
    text = (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert "## Вадим" in text and "Договорились созвониться в пятницу — «срок»." in text
    copy = data / retired_profiles.SOURCES_NAME / f"Вадим — {PID_B}.notes.md"
    assert copy.read_bytes() == raw
    assert not (data / "profiles").exists()


def test_undecodable_bytes_are_never_dropped():
    text, utf8 = retired_profiles.decode(b"\x98\xff abc")
    assert not utf8 and "abc" in text
    bom = bytes([0xEF, 0xBB, 0xBF]) + "привет".encode("utf-8")
    assert retired_profiles.decode(bom) == ("привет", True)


def test_unreadable_note_stays_until_next_start(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    note = data / "profiles" / f"{PID_A}.notes.md"
    real = type(note).read_bytes

    def deny(self):
        if self.name == note.name:
            raise PermissionError(13, "Отказано в доступе")
        return real(self)

    lines = []
    with monkeypatch.context() as mp:
        mp.setattr(type(note), "read_bytes", deny)
        retired_profiles.run(data, voices, config=config, log=lines.append)
    assert note.exists()  # не прочитали — не удаляем
    assert sorted(p.name for p in (data / "profiles").iterdir()) == [note.name]
    assert any("не прочитана" in line for line in lines)
    assert "Любит повестку" not in (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    # Следующий запуск: файл читается — заметка дописана в тот же файл, папки нет.
    got = retired_profiles.run(data, voices, config=config)
    text = (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)
    assert "Любит повестку заранее." in text and text.count("## Без имени") == 1
    assert not (data / "profiles").exists()
    assert sorted(p.name for p in data.glob("*.md")) == [retired_profiles.NOTES_NAME]


@pytest.mark.skipif(sys.platform != "win32", reason="ACL Windows")
def test_deny_read_acl_note_is_not_deleted(tmp_path):
    data, voices, config = _world(tmp_path)
    note = data / "profiles" / f"{PID_A}.notes.md"
    user = os.environ.get("USERNAME") or ""
    deny = subprocess.run(["icacls", str(note), "/deny", f"{user}:(RD)"], capture_output=True)
    if deny.returncode != 0:
        pytest.skip("icacls недоступен")
    try:
        try:
            note.read_bytes()
            pytest.skip("запрет чтения не действует (права администратора?)")
        except PermissionError:
            pass
        retired_profiles.run(data, voices, config=config)
        assert os.path.lexists(note)
    finally:
        subprocess.run(["icacls", str(note), "/remove:d", user], capture_output=True)
    retired_profiles.run(data, voices, config=config)
    assert "Любит повестку заранее." in (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert not (data / "profiles").exists()


def test_unsaved_note_edit_tmp_is_kept_as_raw_copy(tmp_path):
    data, voices, config = _world(tmp_path)
    later = time.time() + 60
    tmp = data / "profiles" / f".{PID_A}.notes.md.{HEX32}.tmp"
    tmp.write_text("Любит повестку заранее. И короткие встречи.", encoding="utf-8")
    os.utime(tmp, (later, later))
    same = data / "profiles" / f".{PID_C}.notes.md.{HEX32}.tmp"
    same.write_text("Человека уже нет в базе.", encoding="utf-8")  # то же, что сохранено
    os.utime(same, (later, later))

    retired_profiles.run(data, voices, config=config)

    copies = sorted(p.name for p in (data / retired_profiles.SOURCES_NAME).iterdir())
    assert copies == [f"Вера Соколова — несохранённая правка {PID_A}.notes.md"]
    copy = data / retired_profiles.SOURCES_NAME / copies[0]
    assert "И короткие встречи." in copy.read_text(encoding="utf-8")
    assert not (data / "profiles").exists()


def test_failure_inside_run_never_escapes(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    before = _snapshot(data / "profiles")

    def boom(*a, **kw):
        raise RuntimeError("сбой")

    lines = []
    monkeypatch.setattr(retired_profiles, "collect_notes", boom)
    assert retired_profiles.run(data, voices, config=config, log=lines.append) is None
    assert _snapshot(data / "profiles") == before
    assert any("повторю при следующем запуске" in line for line in lines)


def test_second_run_appends_to_own_notes_file_without_duplicates(tmp_path):
    data, voices, config = _world(tmp_path)
    first = retired_profiles.collect_notes(data / "profiles", voices).notes[:1]
    (data / retired_profiles.NOTES_NAME).write_text(retired_profiles.notes_text(first), encoding="utf-8")

    retired_profiles.run(data, voices, config=config)

    text = (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert text.count("\n## ") == 2 and text.count("# Заметки о людях") == 1
    assert sorted(p.name for p in data.glob("*.md")) == [retired_profiles.NOTES_NAME]


def test_late_process_keeps_notes_link_in_notice(tmp_path):
    """Второй процесс пришёл, когда заметки уже перенесены, а отметки ещё нет."""
    data, voices, config = _world(tmp_path)
    notes = retired_profiles.collect_notes(data / "profiles", voices).notes
    (data / retired_profiles.NOTES_NAME).write_text(retired_profiles.notes_text(notes), encoding="utf-8")
    for path in (data / "profiles").glob("*.notes.md"):
        path.unlink()

    got = retired_profiles.run(data, voices, config=config)

    assert got["notes"] == str(data / retired_profiles.NOTES_NAME)


def test_crlf_note_is_not_duplicated_on_retry(tmp_path):
    data, voices, config = _world(tmp_path)
    note = data / "profiles" / f"{PID_A}.notes.md"
    note.write_bytes("Первая строка.\r\nВторая строка.\r\n".encode("utf-8"))
    keep = data / "profiles" / f"{PID_C}.notes.md"
    retired_profiles._save_notes(data, retired_profiles.collect_notes(data / "profiles", voices).notes)
    keep.unlink()
    retired_profiles.run(data, voices, config=config)
    text = (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert text.count("Вторая строка.") == 1
    assert b"\r" not in (data / retired_profiles.NOTES_NAME).read_bytes()


# --- re-review r1: только настоящие ссылки, тихая строка ----------------------------


CLOUD_TAG = 0x9000601A  # IO_REPARSE_TAG_CLOUD_6: заглушка OneDrive «файлы по запросу»


def _fake_reparse(monkeypatch, names, tag):
    """lstat для файлов `names` — как у точки повторной обработки с меткой `tag`."""
    real = retired_profiles._lstat

    class Fake:
        def __init__(self, st):
            self.st_mode = st.st_mode
            self.st_reparse_tag = tag
            self.st_file_attributes = 0x400

    def lstat(path):
        st = real(path)
        return Fake(st) if os.path.basename(path) in names else st

    monkeypatch.setattr(retired_profiles, "_lstat", lstat)


def test_cloud_placeholder_note_is_read_not_treated_as_link(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    _fake_reparse(monkeypatch, {f"{PID_A}.notes.md"}, CLOUD_TAG)

    got = retired_profiles.run(data, voices, config=config)

    text = (data / retired_profiles.NOTES_NAME).read_text(encoding="utf-8")
    assert got["notes"] and "Любит повестку заранее." in text
    assert not (data / "profiles").exists()


def test_cloud_placeholder_that_cannot_be_read_stays(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    note = data / "profiles" / f"{PID_A}.notes.md"
    _fake_reparse(monkeypatch, {note.name}, CLOUD_TAG)
    real = type(note).read_bytes

    def offline(self):
        if self.name == note.name:
            raise OSError(362, "Поставщик облачных файлов не запущен")
        return real(self)

    monkeypatch.setattr(type(note), "read_bytes", offline)
    retired_profiles.run(data, voices, config=config)
    assert note.read_text(encoding="utf-8") == "Любит повестку заранее.\n"


def test_real_link_tags_still_count_as_links(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    _fake_reparse(monkeypatch, {"x"}, 0xA0000003)  # IO_REPARSE_TAG_MOUNT_POINT
    assert retired_profiles._is_link(tmp_path / "x") is False  # нет такого файла — не ссылка
    (tmp_path / "x").write_text("", encoding="utf-8")
    assert retired_profiles._is_link(tmp_path / "x") is True
    _fake_reparse(monkeypatch, {"x"}, CLOUD_TAG)
    assert retired_profiles._is_link(tmp_path / "x") is False


def test_notice_stays_dismissed_while_a_note_is_stuck(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    note = data / "profiles" / f"{PID_A}.notes.md"
    real = type(note).read_bytes

    def deny(self):
        if self.name == note.name:
            raise PermissionError(13, "Отказано в доступе")
        return real(self)

    with monkeypatch.context() as mp:
        mp.setattr(type(note), "read_bytes", deny)
        assert retired_profiles.run(data, voices, config=config) is not None  # первая уборка — строка
        retired_profiles.dismiss(data)
        for _ in range(3):  # перезапуски: заметка всё ещё не читается
            assert retired_profiles.run(data, voices, config=config) is None
            assert retired_profiles.notice(data) is None
        assert note.exists()
    # Заметка наконец прочиталась и перенесена — об этом строка скажет ещё раз.
    got = retired_profiles.run(data, voices, config=config)
    assert got == {"notes": str(data / retired_profiles.NOTES_NAME), "folder": str(data)}
    assert not (data / "profiles").exists()


def test_cleaning_leftovers_after_dismiss_stays_quiet(tmp_path, monkeypatch):
    data, voices, config = _world(tmp_path)
    stuck = data / "profiles" / f"{PID_B}.json"
    real_unlink = retired_profiles._unlink_file

    def locked(path):
        if path.name == stuck.name:
            raise PermissionError(32, "Файл занят")
        real_unlink(path)

    with monkeypatch.context() as mp:
        mp.setattr(retired_profiles, "_unlink_file", locked)
        assert retired_profiles.run(data, voices, config=config) is not None
    retired_profiles.dismiss(data)
    assert stuck.exists()
    # Файл освободился: дочищен, но строку уже видели — она не возвращается.
    assert retired_profiles.run(data, voices, config=config) is None
    assert not (data / "profiles").exists()
    assert retired_profiles.notice(data) is None
