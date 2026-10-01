from pathlib import Path

from meet import paths


def test_repo_root_found_from_sources():
    """Тесты идут из репозитория — значит dev-режим и корень с pyproject.toml."""
    root = paths.repo_root()
    assert root is not None
    assert (root / "pyproject.toml").is_file()
    assert paths.is_dev() is True


def test_dev_mode_keeps_historic_locations():
    """Пути dev-режима — те же, что были до появления модуля: иначе записи и
    лексика тех, кто запускает из исходников, «переехали» бы при обновлении."""
    root = paths.repo_root()
    assert paths.default_recordings_dir() == root / "recordings"
    assert paths.hotwords_path() == root / "hotwords.txt"
    assert paths.glossary_path() == root / "glossary.txt"
    assert paths.default_voices_dir() == root / "voices"


def test_data_dir_follows_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.data_dir() == tmp_path / "meet"
    assert paths.config_path() == tmp_path / "meet" / "config.json"


def test_data_dir_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "ignored"))
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "portable"))
    assert paths.data_dir() == tmp_path / "portable"


def test_blank_override_is_ignored(monkeypatch, tmp_path):
    """Пустая переменная среды не должна означать «писать в текущую папку»."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("MEET_DATA_DIR", "   ")
    assert paths.data_dir() == tmp_path / "meet"


def test_data_dir_is_not_cached(monkeypatch, tmp_path):
    """Кэш сломал бы монкипатч LOCALAPPDATA, на котором стоят тесты трея."""
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "a"))
    first = paths.data_dir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "b"))
    assert paths.data_dir() != first


def test_installed_mode_puts_everything_in_data_dir(monkeypatch, tmp_path):
    """Без корня репозитория (пакет в site-packages) записи и лексика уезжают
    в data_dir: относительных путей от cwd у приложения быть не должно."""
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(paths, "repo_root", lambda: None)
    data = tmp_path / "meet"
    assert paths.is_dev() is False
    assert paths.default_recordings_dir() == data / "recordings"
    assert paths.hotwords_path() == data / "hotwords.txt"
    assert paths.default_voices_dir() == data / "voices"
    assert paths.models_dir() == data / "models"
    assert paths.engine_dir() == data / "engine"


def test_all_paths_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    for value in (
        paths.data_dir(),
        paths.config_path(),
        paths.default_recordings_dir(),
        paths.hotwords_path(),
        paths.glossary_path(),
        paths.default_voices_dir(),
        paths.models_dir(),
        paths.logs_dir(),
        paths.engine_dir(),
    ):
        assert isinstance(value, Path) and value.is_absolute()


def test_watch_log_follows_data_dir(monkeypatch, tmp_path):
    """Журнал дежурного — единственный файл, который раньше не слушался
    MEET_DATA_DIR: изолированный прогон писал его в чужое место."""
    from meet import watch

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "portable"))
    assert watch.default_log_path() == tmp_path / "portable" / "watch.log"


def test_watch_log_keeps_historic_place_without_override(monkeypatch, tmp_path):
    from meet import watch

    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert watch.default_log_path() == tmp_path / "meet" / "watch.log"


def test_gpu_lock_follows_data_dir(monkeypatch, tmp_path):
    """gpu.lock — публичный контракт с внешними программами: путь обязан остаться тем
    же в обычном запуске."""
    from meet import gpu_lock

    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert gpu_lock.lock_path() == tmp_path / "meet" / "gpu.lock"
