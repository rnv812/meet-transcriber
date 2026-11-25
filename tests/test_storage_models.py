"""Отдельная папка моделей (0.6.3): модели Meet (свой кэш HF, GigaAM, Xet) —
в своей папке, независимо от движка (`storage-models.json`).

Поддельные кэши HF и GigaAM — во временных папках, как в test_storage.py."""

import json
import os
from pathlib import Path

import pytest

from meet import gigaam_asr, models, paths, storage

from test_storage import DIAR_REPO, MEET_REPO, OTHER_REPO, _choose, _source, data  # noqa: F401


def _models_choice(data: Path, folder: Path, **extra) -> None:
    (data / paths.MODELS_STORAGE_FILE).write_text(
        json.dumps({"version": 1, "root": str(folder), **extra}), encoding="utf-8")


def _repo_dir(cache: Path, repo: str) -> Path:
    return cache / ("models--" + repo.replace("/", "--"))


# --- пути: где модели, когда папка выбрана отдельно -----------------------------


def test_without_models_choice_models_live_with_the_engine(data, tmp_path):
    """Старые установки (нет storage-models.json): модели там же, где движок."""
    assert paths.models_root() is None
    assert paths.models_missing() is None
    assert paths.models_dir() == data / "models"
    assert paths.own_hf_cache() is False
    root = tmp_path / "E" / "Meet"
    root.mkdir(parents=True)
    _choose(data, root)
    assert paths.models_dir() == root / "models"
    assert paths.default_models_dir() == root / "models"
    assert models.cache_root() == root / "models" / "hf"


def test_separate_models_folder_holds_hf_gigaam_and_xet(data, tmp_path, monkeypatch):
    folder = tmp_path / "F" / "Models"
    folder.mkdir(parents=True)
    _models_choice(data, folder)
    assert paths.models_root() == folder
    assert paths.models_dir() == folder
    assert paths.engine_dir() == data / "engine", "движок не переезжает"
    assert paths.default_models_dir() == data / "models"
    assert models.cache_root() == folder / "hf"
    assert gigaam_asr.cache_dir() == folder / "gigaam"
    # Резидент и CLI при старте направляют библиотеки движка туда же; дети
    # (задачи, job_worker, ассистент) наследуют окружение.
    models.use_meet_cache()
    assert os.environ["HF_HUB_CACHE"] == str(folder / "hf")
    assert os.environ["HF_XET_CACHE"] == str(folder / "xet")
    assert models.shared_cache_root() == tmp_path / "hf-shared"
    from meet import netproxy

    assert netproxy.settings_env()["HF_HUB_CACHE"] == str(folder / "hf")


def test_separate_models_folder_wins_over_the_engine_folder(data, tmp_path):
    root = tmp_path / "E" / "Meet"
    root.mkdir(parents=True)
    _choose(data, root)
    folder = tmp_path / "F" / "Models"
    folder.mkdir(parents=True)
    _models_choice(data, folder)
    assert paths.engine_dir() == root / "engine"
    assert paths.models_dir() == folder
    assert models.cache_root() == folder / "hf"


@pytest.mark.parametrize("raw", ["", "{", '{"root": ""}', '{"root": "relative"}',
                                 '{"version": 2, "root": "C:/x"}'])
def test_broken_models_choice_blocks_downloads(data, raw):
    """Не прочитан — не «по умолчанию»: куда класть модели, неизвестно."""
    (data / paths.MODELS_STORAGE_FILE).write_text(raw, encoding="utf-8")
    assert paths.models_root() is None
    assert paths.models_unreadable() is True
    assert paths.models_missing() == data / paths.MODELS_STORAGE_FILE
    lines = []
    assert models.download(MEET_REPO, on_line=lines.append) == 4
    assert "папка моделей недоступна" in lines[0]


def test_unplugged_models_disk_is_reported_not_created(data, tmp_path):
    folder = tmp_path / "unplugged" / "Models"
    _models_choice(data, folder)
    assert paths.models_missing() == folder
    assert models.cache_root() == folder / "hf"
    lines = []
    assert models.download(MEET_REPO, on_line=lines.append) == 4
    assert "папка моделей недоступна" in lines[0]
    with pytest.raises(gigaam_asr.Unavailable, match="папка моделей недоступна"):
        gigaam_asr.ensure("v3_e2e_rnnt")
    assert not folder.exists()


# --- копирование в отдельную папку и обратно ---------------------------------------


def test_copy_to_a_separate_folder_takes_only_meet_models(data, tmp_path):
    shared = tmp_path / "hf-shared"
    meet, diar, other, gig = _source(data, shared)
    folder = tmp_path / "F" / "Models"
    seen = []
    result = storage.copy_models_to(folder, on_progress=lambda done, total: seen.append((done, total)))
    assert (_repo_dir(folder / "hf", MEET_REPO) / "snapshots" / "rev1" / "model.bin").read_bytes() == b"w" * 1000
    assert (_repo_dir(folder / "hf", DIAR_REPO) / "refs" / "main").is_file()
    assert not _repo_dir(folder / "hf", OTHER_REPO).exists(), "чужие модели общего кэша не трогаем"
    assert (folder / "gigaam" / "v3_e2e_rnnt.ckpt").read_bytes() == b"weights" * 10
    assert not (folder / "models").exists(), "раскладка — прямо в выбранной папке"
    assert (meet / "snapshots" / "rev1" / "model.bin").is_file(), "источник цел до переключения"
    assert (gig / "v3_e2e_rnnt.ckpt").is_file()
    assert seen[-1] == (result["bytes"], result["bytes"])
    # После переключения загрузчики находят модели в новой папке.
    _models_choice(data, folder)
    assert models.downloaded(MEET_REPO)
    assert models.local_snapshot(DIAR_REPO) == _repo_dir(folder / "hf", DIAR_REPO) / "snapshots" / "rev1"
    assert not models.downloaded(OTHER_REPO)
    assert gigaam_asr.model_files("v3_e2e_rnnt")[0].is_file()


def test_copy_between_separate_folders(data, tmp_path):
    _source(data, tmp_path / "hf-shared")
    first = tmp_path / "F" / "Models"
    storage.copy_models_to(first)
    _models_choice(data, first)
    second = tmp_path / "G" / "Models"
    result = storage.copy_models_to(second)
    assert result["copied"] == result["bytes"] > 0
    assert (_repo_dir(second / "hf", MEET_REPO) / "snapshots" / "rev1" / "model.bin").is_file()
    assert (second / "gigaam" / "v3_e2e_rnnt.ckpt").is_file()


def test_copy_back_to_default_goes_to_the_shared_cache_and_data_dir(data, tmp_path):
    """Без папки движка «по умолчанию» — общий кэш HF и папка данных: туда
    модели и возвращаются (уже лежащее там не копируется заново)."""
    shared = tmp_path / "hf-shared"
    _source(data, shared)
    folder = tmp_path / "F" / "Models"
    storage.copy_models_to(folder)
    _models_choice(data, folder)
    # Модель, скачанная уже в отдельную папку, — её в общем кэше нет.
    import shutil

    shutil.rmtree(_repo_dir(shared, DIAR_REPO))
    shutil.rmtree(data / "models" / "gigaam")
    result = storage.copy_models_to(None)
    assert (_repo_dir(shared, DIAR_REPO) / "snapshots" / "rev1" / "config.yaml").is_file()
    assert (data / "models" / "gigaam" / "v3_e2e_rnnt.ckpt").is_file()
    assert result["copied"] < result["bytes"], "целое в общем кэше не копируется"
    (data / paths.MODELS_STORAGE_FILE).unlink()
    assert models.cache_root() == shared
    assert models.downloaded(DIAR_REPO)


def test_copy_back_to_an_engine_folder_goes_to_its_models(data, tmp_path):
    _source(data, tmp_path / "hf-shared")
    root = tmp_path / "E" / "Meet"
    root.mkdir(parents=True)
    storage.copy_models(root)
    _choose(data, root)
    folder = tmp_path / "F" / "Models"
    storage.copy_models_to(folder)
    _models_choice(data, folder)
    import shutil

    shutil.rmtree(root / "models")
    storage.copy_models_to(None)
    assert (_repo_dir(root / "models" / "hf", MEET_REPO) / "snapshots" / "rev1" / "model.bin").is_file()
    assert (root / "models" / "gigaam" / "v3_e2e_rnnt.ckpt").is_file()


def test_engine_move_does_not_copy_separate_models(data, tmp_path):
    """Общий перенос при отдельной папке моделей двигает только движок."""
    _source(data, tmp_path / "hf-shared")
    folder = tmp_path / "F" / "Models"
    storage.copy_models_to(folder)
    _models_choice(data, folder)
    result = storage.copy_models(tmp_path / "E" / "Meet")
    assert result == {"files": 0, "bytes": 0, "copied": 0}
    assert not (tmp_path / "E" / "Meet" / "models").exists()


def test_copy_models_cli(data, tmp_path, capsys):
    _source(data, tmp_path / "hf-shared")
    folder = tmp_path / "F" / "Models"
    assert storage.main(["copy-models", "--to", str(folder)]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1]["ok"] is True and lines[-1]["bytes"] > 0
    assert (folder / "gigaam" / "v3_e2e_rnnt.ckpt").is_file()
    _models_choice(data, folder)
    assert storage.main(["copy-models"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["ok"] is True


# --- перенос идёт, остатки, сведения ---------------------------------------------


def test_models_journal_holds_downloads_like_the_engine_one(data):
    journal = data / storage.MODELS_JOURNAL_FILE
    for phase, moving in (("models", True), ("switching", True), ("interrupted", False),
                          ("cleanup", False)):
        journal.write_text(json.dumps({"version": 1, "phase": phase}), encoding="utf-8")
        assert storage.moving() is moving, phase
    journal.write_text("{", encoding="utf-8")
    assert storage.moving() is True
    journal.unlink()
    assert storage.moving() is False


def test_leftovers_after_moving_only_the_models_out_of_the_shared_cache(data, tmp_path):
    shared = tmp_path / "hf-shared"
    _source(data, shared)
    folder = tmp_path / "F" / "Models"
    storage.copy_models_to(folder)
    _models_choice(data, folder)
    (data / storage.LEFTOVERS_FILE).write_text("{}", encoding="utf-8")
    found = storage.leftovers()
    assert found["cache"] == str(shared)
    assert sorted(r["id"] for r in found["repos"]) == sorted([MEET_REPO, DIAR_REPO])
    result = storage.answer_leftovers(delete=True)
    assert result["ok"] is True
    assert _repo_dir(shared, OTHER_REPO).is_dir(), "чужие модели общего кэша — никогда"
    assert models.downloaded(MEET_REPO)


def test_info_describes_the_separate_models_folder(data, tmp_path):
    _source(data, tmp_path / "hf-shared")
    info = storage.info()
    assert info["models_root"] is None
    assert info["models_default"] == str(data / "models")
    assert info["models_missing"] is None
    folder = tmp_path / "F" / "Models"
    storage.copy_models_to(folder)
    _models_choice(data, folder)
    info = storage.info()
    assert info["models_root"] == str(folder)
    assert info["models_dir"] == str(folder)
    assert info["hf_cache"] == str(folder / "hf")
    assert info["home"] == str(data)
    assert info["models_bytes"] > 0
