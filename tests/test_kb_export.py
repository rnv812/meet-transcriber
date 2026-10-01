"""Выгрузка встречи в базу знаний (Obsidian и т. п.) по шаблону папки.

Данные — выдуманные: «Планирование спринта», Анна и Борис.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from meet import kb_export, library, settings

RID = "2026-09-30_10-15"


def _recording(root: Path, rid=RID, title="Планирование спринта", summary=None):
    folder = root / "rec" / rid
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"sys")
    if title is not None:
        library.write_meta(folder, {"title": title})
    library.write_transcript(folder, {"version": 1, "title": "Встреча — 30.09.2026", "segments": [
        {"start": 5.0, "end": 7.0, "speaker": "Анна", "text": "Начнём с задач."},
        {"start": 65.0, "end": 66.5, "speaker": "SPEAKER_01", "text": "Согласен."},
    ]})
    if summary is not None:
        (folder / "summary.md").write_text(summary, encoding="utf-8")
    return folder


def _cfg(vault: Path | None, **over) -> settings.Export:
    return settings.Export.from_raw({"meetings_dir": str(vault) if vault else None, **over})


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    path.mkdir()
    return path


# --- шаблоны ---------------------------------------------------------------------


START = datetime(2026, 9, 30, 10, 15)


def test_default_template_renders_date_and_title():
    assert kb_export.render_folder("{date} - {title}", "Планирование спринта", START) == [
        "2026-09-30 - Планирование спринта"]


def test_all_tokens_and_subfolders():
    parts = kb_export.render_folder("{year}/{month}/{day} {time} - {title}", "Обзор", START)
    assert parts == ["2026", "09", "30 10-15 - Обзор"]
    assert kb_export.render_folder("{year}\\{date}", "x", START) == ["2026", "2026-09-30"]


def test_title_cannot_add_folders_or_dangerous_characters():
    parts = kb_export.render_folder("{date} - {title}", "Итоги: план/факт?", START)
    assert parts == ["2026-09-30 - Итоги_ план_факт_"]
    assert kb_export.render_folder("{title}", "..", START) == ["Встреча"]


@pytest.mark.parametrize("template, fragment", [
    ("", "пуст"),
    ("   ", "пуст"),
    ("/{title}", "относительн"),
    ("\\{title}", "относительн"),
    ("C:/{title}", "диск"),
    ("{year}//{title}", "пустая часть"),
    ("{year}/", "пустая часть"),
    ("../{title}", "«..»"),
    ("{year}/../{title}", "«..»"),
    ("{date} - {name}", "{name}"),
])
def test_invalid_folder_template_is_explained_in_russian(template, fragment):
    error = kb_export.check_folder_template(template)
    assert error and fragment in error


def test_valid_templates_pass():
    for template in ("{date} - {title}", "{year}/{date} - {title}", "Встречи/{title}"):
        assert kb_export.check_folder_template(template) is None


def test_file_names_take_tokens_get_md_and_reject_paths():
    assert kb_export.render_file("Транскрипт", "x", START) == "Транскрипт.md"
    assert kb_export.render_file("{date} Итоги.md", "x", START) == "2026-09-30 Итоги.md"
    assert kb_export.render_file("{title}.md", "a:b", START) == "a_b.md"
    assert kb_export.check_file_name("Итоги.md") is None
    assert "пуст" in kb_export.check_file_name(" ")
    assert "«/»" in kb_export.check_file_name("папка/Итоги.md")
    assert "{who}" in kb_export.check_file_name("{who}.md")


# --- выгрузка ----------------------------------------------------------------------


def test_export_writes_folder_with_transcript_and_summary(tmp_path, vault):
    folder = _recording(tmp_path, summary="# Итоги — Планирование спринта\n\n## Решения\n- Да\n")
    got = kb_export.export_recording(folder, _cfg(vault))
    target = vault / "2026-09-30 - Планирование спринта"
    assert got == {"path": str(target), "files": ["Транскрипт.md", "Итоги.md"], "kept": []}
    transcript = (target / "Транскрипт.md").read_text(encoding="utf-8")
    assert transcript.startswith("---\ndate: 2026-09-30\n")
    assert "# Планирование спринта\n" in transcript
    assert "## 00:05 — Анна\n\nНачнём с задач." in transcript
    assert "## 01:05 — Спикер 1" in transcript
    assert (target / "Итоги.md").read_text(encoding="utf-8") == (folder / "summary.md").read_text(
        encoding="utf-8")
    meta = library.read_meta(folder)["kb_export"]
    assert meta["path"] == str(target) and isinstance(meta["at"], float)
    assert set(meta["files"]) == {"Транскрипт.md", "Итоги.md"}
    assert len(meta["files"]["Транскрипт.md"]) == 64  # sha256 записанного
    assert "error" not in meta
    assert not list(target.glob("*.tmp"))


def test_title_falls_back_to_meeting_time(tmp_path, vault):
    folder = _recording(tmp_path, title=None)
    got = kb_export.export_recording(folder, _cfg(vault))
    assert Path(got["path"]).name == "2026-09-30 - Встреча 10:15".replace(":", "_")


def test_subfolder_template(tmp_path, vault):
    folder = _recording(tmp_path)
    got = kb_export.export_recording(folder, _cfg(vault, folder_template="{year}/{date} - {title}"))
    assert Path(got["path"]) == vault / "2026" / "2026-09-30 - Планирование спринта"


def test_include_flags(tmp_path, vault, monkeypatch):
    folder = _recording(tmp_path, summary="# Итоги\n")
    (folder / "mic.opus").write_bytes(b"mic")
    mixed = []

    def mix(a, b, out):
        mixed.append((a.name, b.name))
        out.write_bytes(b"mixed")

    cfg = _cfg(vault, include_transcript=False, include_summary=False,
               include_audio=True, include_srt=True)
    got = kb_export.export_recording(folder, cfg, mix=mix)
    target = Path(got["path"])
    assert got["files"] == ["Субтитры.srt", "Запись.opus"]
    assert sorted(p.name for p in target.iterdir()) == ["Запись.opus", "Субтитры.srt"]
    assert mixed == [("sys.opus", "mic.opus")]
    assert "00:00:05,000 --> 00:00:07,000\nАнна: Начнём с задач." in (
        target / "Субтитры.srt").read_text(encoding="utf-8")


def test_two_tracks_reuse_the_player_mix(tmp_path, vault, monkeypatch):
    """Без подмены `mix` запись в базу знаний — копия сведения плеера
    (`playback.playback_path`): та же нормализация, без второго ffmpeg."""
    from meet import playback

    folder = _recording(tmp_path)
    (folder / "mic.opus").write_bytes(b"mic")
    asked = []

    def fake(where):
        asked.append(where)
        mixed = where / playback.PLAYBACK_NAME
        mixed.write_bytes(b"player-mix")
        return mixed

    monkeypatch.setattr(playback, "playback_path", fake)
    got = kb_export.export_recording(folder, _cfg(vault, include_audio=True))
    assert (Path(got["path"]) / "Запись.opus").read_bytes() == b"player-mix"
    assert asked == [folder]


def test_player_mix_failure_is_an_export_error(tmp_path, vault, monkeypatch):
    from meet import playback

    folder = _recording(tmp_path)
    (folder / "mic.opus").write_bytes(b"mic")

    def broken(where):
        raise RuntimeError("ffmpeg не найден — дорожки записи не сведены")

    monkeypatch.setattr(playback, "playback_path", broken)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        kb_export.export_recording(folder, _cfg(vault, include_audio=True))


def test_single_track_audio_is_copied(tmp_path, vault):
    folder = tmp_path / "rec" / "2026-09-30_11-00_import"
    folder.mkdir(parents=True)
    (folder / "source.m4a").write_bytes(b"audio")
    library.write_meta(folder, {"source": "import", "title": "Интервью"})
    library.write_transcript(folder, {"version": 1, "segments": []})
    got = kb_export.export_recording(folder, _cfg(vault, include_audio=True),
                                     mix=lambda *a: pytest.fail("один трек не смешивают"))
    assert (Path(got["path"]) / "Запись.m4a").read_bytes() == b"audio"


def test_missing_summary_is_skipped_quietly(tmp_path, vault):
    folder = _recording(tmp_path)
    got = kb_export.export_recording(folder, _cfg(vault))
    assert got["files"] == ["Транскрипт.md"]


def test_custom_file_names_with_tokens(tmp_path, vault):
    folder = _recording(tmp_path, summary="# s\n")
    got = kb_export.export_recording(folder, _cfg(
        vault, transcript_name="{date} Транскрипт", summary_name="Итоги {title}.md"))
    assert got["files"] == ["2026-09-30 Транскрипт.md", "Итоги Планирование спринта.md"]


def test_reexport_goes_to_recorded_folder_after_title_change(tmp_path, vault):
    folder = _recording(tmp_path)
    first = kb_export.export_recording(folder, _cfg(vault))
    target = Path(first["path"])
    (target / "Мои заметки.md").write_text("своё", encoding="utf-8")
    library.write_meta(folder, {"title": "Новое название"})
    (folder / "summary.md").write_text("# Итоги\n", encoding="utf-8")
    second = kb_export.export_recording(folder, _cfg(vault))
    assert second["path"] == first["path"]
    assert sorted(p.name for p in vault.iterdir()) == [target.name]
    assert (target / "Мои заметки.md").read_text(encoding="utf-8") == "своё"
    assert (target / "Итоги.md").exists()
    assert "# Новое название" in (target / "Транскрипт.md").read_text(encoding="utf-8")


def test_recorded_folder_gone_means_template_again(tmp_path, vault):
    import shutil

    folder = _recording(tmp_path)
    first = Path(kb_export.export_recording(folder, _cfg(vault))["path"])
    shutil.rmtree(first)
    library.write_meta(folder, {"title": "Ретро"})
    again = kb_export.export_recording(folder, _cfg(vault))
    assert Path(again["path"]).name == "2026-09-30 - Ретро"


def test_existing_foreign_folder_is_reused_without_deleting_files(tmp_path, vault):
    folder = _recording(tmp_path)
    target = vault / "2026-09-30 - Планирование спринта"
    target.mkdir()
    (target / "Черновик.md").write_text("чужое", encoding="utf-8")
    got = kb_export.export_recording(folder, _cfg(vault))
    assert got["path"] == str(target)
    assert (target / "Черновик.md").read_text(encoding="utf-8") == "чужое"


def test_same_name_of_another_meeting_gets_a_suffix(tmp_path, vault):
    first = _recording(tmp_path, rid="2026-09-30_10-15")
    second = _recording(tmp_path, rid="2026-09-30_16-00")
    a = kb_export.export_recording(first, _cfg(vault))
    b = kb_export.export_recording(second, _cfg(vault))
    assert Path(a["path"]).name == "2026-09-30 - Планирование спринта"
    assert Path(b["path"]).name == "2026-09-30 - Планирование спринта (2)"


def test_without_meetings_dir_is_an_error(tmp_path):
    folder = _recording(tmp_path)
    with pytest.raises(ValueError, match="Папка для встреч не задана"):
        kb_export.export_recording(folder, _cfg(None))
    with pytest.raises(ValueError, match="не найдена"):
        kb_export.export_recording(folder, _cfg(tmp_path / "нет" / "Встречи"))
    assert not (tmp_path / "нет").exists()


def test_missing_last_part_of_meetings_dir_is_created(tmp_path, vault):
    """Перенесённая «подпапка заметок» могла ещё не существовать."""
    folder = _recording(tmp_path)
    got = kb_export.export_recording(folder, _cfg(vault / "Встречи"))
    assert Path(got["path"]).parent == vault / "Встречи"


def test_failure_is_remembered_in_meta_and_cleared_by_success(tmp_path, vault):
    folder = _recording(tmp_path)
    cfg = _cfg(vault, include_audio=True)

    def broken(*a):
        raise RuntimeError("ffmpeg не найден")

    (folder / "mic.opus").write_bytes(b"mic")
    with pytest.raises(RuntimeError):
        kb_export.export_recording(folder, cfg, mix=broken)
    assert "ffmpeg не найден" in library.read_meta(folder)["kb_export"]["error"]
    kb_export.export_recording(folder, _cfg(vault))
    assert "error" not in library.read_meta(folder)["kb_export"]


def test_card_carries_kb_export(tmp_path, vault):
    folder = _recording(tmp_path)
    kb_export.export_recording(folder, _cfg(vault))
    raw = library.describe(folder).to_raw()
    assert raw["kb_export"]["path"].endswith("2026-09-30 - Планирование спринта")


# --- предпросмотр ------------------------------------------------------------------


def test_preview_uses_latest_recording(tmp_path):
    _recording(tmp_path, rid="2026-09-29_09-00", title="Старое")
    _recording(tmp_path, rid="2026-09-30_10-15", title="Планирование спринта")
    got = kb_export.preview(_cfg(None), tmp_path / "rec", {})
    assert got == {"folder": "2026-09-30 - Планирование спринта",
                   "files": ["Транскрипт.md", "Итоги.md"], "error": None}


def test_preview_sample_and_overrides(tmp_path):
    got = kb_export.preview(_cfg(None), tmp_path / "нет", {
        "folder_template": "{year}/{date} {title}", "transcript_name": "{date}",
        "include_summary": "false", "include_srt": "true", "include_audio": "1"})
    assert got == {"folder": "2026/2026-09-30 Планирование спринта",
                   "files": ["2026-09-30.md", "Субтитры.srt", "Запись.opus"], "error": None}


def test_preview_reports_template_error(tmp_path):
    got = kb_export.preview(_cfg(None), tmp_path, {"folder_template": "../{title}"})
    assert got["folder"] is None and "«..»" in got["error"]


# --- настройки ---------------------------------------------------------------------


def test_export_settings_defaults():
    cfg = settings.Settings.from_raw({}).export
    assert cfg.meetings_dir is None
    assert cfg.folder_template == "{date} - {title}"
    assert (cfg.transcript_name, cfg.summary_name) == ("Транскрипт.md", "Итоги.md")
    assert (cfg.include_transcript, cfg.include_summary) == (True, True)
    assert (cfg.include_audio, cfg.include_srt) == (False, False)
    assert cfg.auto_export is True


def test_notes_folder_migrates_into_meetings_dir():
    raw = {"version": 2, "assistant": {"knowledge_dir": "C:/kb", "notes_dir": "C:/vault",
                                       "notes_subdir": "Встречи"}}
    cfg = settings.Settings.from_raw(raw)
    assert cfg.export.meetings_dir == Path("C:/vault/Встречи")
    assert cfg.assistant.knowledge_dir == Path("C:/kb")
    no_sub = {"version": 2, "assistant": {"notes_dir": "C:/vault", "notes_subdir": ""}}
    assert settings.Settings.from_raw(no_sub).export.meetings_dir == Path("C:/vault")


def test_explicit_meetings_dir_wins_and_null_stays_null():
    raw = {"version": 2, "assistant": {"notes_dir": "C:/vault"},
           "export": {"meetings_dir": "D:/kb/Встречи"}}
    assert settings.Settings.from_raw(raw).export.meetings_dir == Path("D:/kb/Встречи")
    raw["export"] = {"meetings_dir": None}
    assert settings.Settings.from_raw(raw).export.meetings_dir is None


def test_broken_template_in_file_falls_back_to_default():
    cfg = settings.Export.from_raw({"folder_template": "../x", "transcript_name": "a/b"})
    assert cfg.folder_template == "{date} - {title}"
    assert cfg.transcript_name == "Транскрипт.md"


def test_patch_export_validates_in_russian(tmp_path):
    f = tmp_path / "config.json"
    updated = settings.patch({"export": {"folder_template": "{year}/{date} - {title}",
                                         "include_audio": True}}, f)
    assert updated.export.folder_template == "{year}/{date} - {title}"
    assert settings.load(f).export.include_audio is True
    with pytest.raises(ValueError, match="«..»"):
        settings.patch({"export": {"folder_template": "../{title}"}}, f)
    with pytest.raises(ValueError, match="полн"):
        settings.patch({"export": {"meetings_dir": "относительная"}}, f)
    assert settings.load(f).export.folder_template == "{year}/{date} - {title}"
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert saved["export"]["include_audio"] is True


# --- правки человека в базе знаний ----------------------------------------------------


def test_reexport_keeps_files_edited_by_hand(tmp_path, vault):
    folder = _recording(tmp_path, summary="# Итоги\n")
    target = Path(kb_export.export_recording(folder, _cfg(vault))["path"])
    (target / "Итоги.md").write_text("# Итоги\n\nМои пометки.\n", encoding="utf-8")
    (folder / "summary.md").write_text("# Итоги v2\n", encoding="utf-8")
    library.write_meta(folder, {"title": "Новое название"})
    got = kb_export.export_recording(folder, _cfg(vault))
    assert got["kept"] == ["Итоги.md"]
    assert got["files"] == ["Транскрипт.md"]
    assert "Мои пометки." in (target / "Итоги.md").read_text(encoding="utf-8")
    assert "# Новое название" in (target / "Транскрипт.md").read_text(encoding="utf-8")
    assert not list(target.glob("*обновлено*"))
    # Правка остаётся защищённой и при следующей выгрузке.
    again = kb_export.export_recording(folder, _cfg(vault))
    assert again["kept"] == ["Итоги.md"]
    assert library.read_meta(folder)["kb_export"]["kept"] == ["Итоги.md"]


def test_unchanged_files_are_updated(tmp_path, vault):
    folder = _recording(tmp_path, summary="# Итоги\n")
    kb_export.export_recording(folder, _cfg(vault))
    (folder / "summary.md").write_text("# Итоги v2\n", encoding="utf-8")
    got = kb_export.export_recording(folder, _cfg(vault))
    assert got["kept"] == []
    assert (Path(got["path"]) / "Итоги.md").read_text(encoding="utf-8") == "# Итоги v2\n"


def test_foreign_file_with_our_name_is_not_overwritten(tmp_path, vault):
    folder = _recording(tmp_path)
    target = vault / "2026-09-30 - Планирование спринта"
    target.mkdir()
    (target / "Транскрипт.md").write_text("чужой транскрипт", encoding="utf-8")
    got = kb_export.export_recording(folder, _cfg(vault))
    assert got["kept"] == ["Транскрипт.md"]
    assert (target / "Транскрипт.md").read_text(encoding="utf-8") == "чужой транскрипт"


def test_export_of_older_version_without_hashes_is_still_ours(tmp_path, vault):
    """Первая версия выгрузки писала в meta список имён без хешей."""
    folder = _recording(tmp_path)
    target = Path(kb_export.export_recording(folder, _cfg(vault))["path"])
    library.write_meta(folder, {"kb_export": {"path": str(target), "at": 1.0,
                                              "files": ["Транскрипт.md"]}})
    library.write_meta(folder, {"title": "Ретро"})
    got = kb_export.export_recording(folder, _cfg(vault))
    assert got["kept"] == []
    assert "# Ретро" in (target / "Транскрипт.md").read_text(encoding="utf-8")


def test_remember_error_survives_broken_meta(tmp_path, monkeypatch):
    folder = _recording(tmp_path)

    def boom(*a, **k):
        raise ValueError("meta.json сломан")

    monkeypatch.setattr(library, "update_meta", boom)
    kb_export._remember_error(folder, RuntimeError("x"))  # не бросает


@pytest.mark.parametrize("update, fragment", [
    ({"transcript_name": "Итоги.md"}, "совпада"),
    ({"summary_name": "транскрипт"}, "совпада"),
    ({"transcript_name": "Запись.md"}, "Запись"),
    ({"summary_name": "Субтитры"}, "Субтитры"),
])
def test_file_names_must_not_clash(tmp_path, update, fragment):
    f = tmp_path / "config.json"
    with pytest.raises(ValueError, match=fragment):
        settings.patch({"export": update}, f)
    got = kb_export.preview(_cfg(None), tmp_path, update)
    assert fragment in got["error"]
