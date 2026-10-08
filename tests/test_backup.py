"""Копия настроек перед откатом версии (0.5): что попадает в архив, а что нет."""

from __future__ import annotations

import zipfile
from datetime import datetime

import pytest

from meet import backup


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "meet"
    d.mkdir()
    (d / "config.json").write_text('{"version": 9}', encoding="utf-8")
    (d / "hotwords.txt").write_text("Kubernetes\n", encoding="utf-8")
    (d / "wizard_done").write_text("", encoding="utf-8")
    (d / "api.token").write_text("секрет", encoding="utf-8")
    (d / "daemon.json").write_text('{"token": "секрет"}', encoding="utf-8")
    (d / "uninstall.exe").write_bytes(b"MZ")
    for sub in ("engine", "logs", "recordings", "tmp-meetings"):
        (d / sub).mkdir()
        (d / sub / "big.bin").write_bytes(b"0" * 10)
    return d


def _names(path):
    with zipfile.ZipFile(path) as z:
        return sorted(z.namelist())


def test_settings_voices_and_service_files_go_in_secrets_and_bulk_do_not(data, tmp_path):
    voices = tmp_path / "voices"
    (voices / "_owner").mkdir(parents=True)
    (voices / "Анна.npy").write_bytes(b"v")
    (voices / "_owner" / "sample.npy").write_bytes(b"o")
    path = backup.make(data, voices, "0.4.0", now=datetime(2026, 10, 9, 14, 30, 5))
    assert path.parent == data / "backups"
    assert path.name == "2026-10-09_14-30-05-before-0.4.0.zip"
    assert _names(path) == ["config.json", "hotwords.txt", "voices/_owner/sample.npy",
                            "voices/Анна.npy", "wizard_done"]
    assert not list((data / "backups").glob("*.part"))


def test_label_is_a_file_name_not_a_path(data):
    path = backup.make(data, None, "../../evil", now=datetime(2026, 1, 1))
    assert path.parent == data / "backups" and ".." not in path.name and "/" not in path.name


def test_voices_inside_data_dir_are_not_doubled(data):
    (data / "voices").mkdir()
    (data / "voices" / "Анна.npy").write_bytes(b"v")
    path = backup.make(data, data / "voices", "0.3.9", now=datetime(2026, 1, 1))
    assert _names(path).count("voices/Анна.npy") == 1


def test_resident_route_backs_up_before_a_version(monkeypatch, tmp_path):
    from meet import control, tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    (tmp_path / "meet").mkdir()
    (tmp_path / "meet" / "config.json").write_text("{}", encoding="utf-8")
    state = tray_control.TrayControl(tray.TrayApp())
    with pytest.raises(control.BadRequest):
        state.backup({})
    path = state.backup({"target": "0.4.0"})["path"]
    assert path.endswith("-before-0.4.0.zip") and "config.json" in _names(path)
    assert ("POST", "/backup") in control._ROUTES


def test_links_are_not_followed(data, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("чужое", encoding="utf-8")
    try:
        (data / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("ссылки недоступны без прав")
    assert "link.txt" not in _names(backup.make(data, None, "0.4.0", now=datetime(2026, 1, 1)))
