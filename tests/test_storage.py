"""Где хранить движок и модели (0.3.3): выбранная папка, кэш моделей Meet и
перенос моделей.

Настоящих моделей и кэшей здесь нет: крошечные поддельные кэши Hugging Face и
GigaAM во временных папках. Настоящий `~/.cache/huggingface` и
`%LOCALAPPDATA%\\meet` тесты не видят: conftest уводит LOCALAPPDATA во временную
папку, а кэш HF здесь всегда задан явно (HF_HUB_CACHE)."""

import json
import os
import time
from pathlib import Path

import pytest

from meet import models, paths, storage

MEET_REPO = "Systran/faster-whisper-medium"
DIAR_REPO = "pyannote/speaker-diarization-community-1"
OTHER_REPO = "someone/other-project-model"


def _repo(cache: Path, repo: str, files: dict[str, bytes], ref: str = "rev1") -> Path:
    """Репозиторий в раскладке кэша HF: refs/main, snapshots/<rev>, blobs."""
    folder = cache / ("models--" + repo.replace("/", "--"))
    snap = folder / "snapshots" / ref
    snap.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text(ref, encoding="utf-8")
    (folder / "blobs").mkdir()
    for name, data in files.items():
        (snap / name).parent.mkdir(parents=True, exist_ok=True)
        (snap / name).write_bytes(data)
    return folder


def _gigaam(models_dir: Path) -> Path:
    root = models_dir / "gigaam"
    root.mkdir(parents=True)
    (root / "v3_e2e_rnnt.ckpt").write_bytes(b"weights" * 10)
    (root / "v3_e2e_rnnt_tokenizer.model").write_bytes(b"tok")
    (root / "v3_e2e_rnnt.verified.json").write_text("{}", encoding="utf-8")
    (root / "v3_e2e_rnnt.ckpt.part").write_bytes(b"half")  # недокачанное — не переносим
    return root


@pytest.fixture
def data(monkeypatch, tmp_path):
    """Папка данных приложения и общий кэш HF — во временной папке."""
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setenv("MEET_DATA_DIR", str(folder))
    shared = tmp_path / "hf-shared"
    shared.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(shared))
    # use_meet_cache пишет их в os.environ: setenv запоминает, что их не
    # было, и после теста они уберутся (delenv отсутствующей не запоминает).
    for env in ("HF_XET_CACHE", models.USER_HF_HUB_CACHE):
        monkeypatch.setenv(env, "")
        monkeypatch.delenv(env)
    return folder


def _choose(data: Path, root: Path, **extra) -> None:
    (data / paths.STORAGE_FILE).write_text(
        json.dumps({"root": str(root), **extra}), encoding="utf-8")


# --- одна правда о путях: storage.json ------------------------------------------


def test_without_choice_everything_stays_where_it_was(data, tmp_path):
    assert paths.storage_root() is None
    assert paths.storage_home() == data
    assert paths.models_dir() == data / "models"
    assert paths.engine_dir() == data / "engine"
    assert models.cache_root() == tmp_path / "hf-shared"  # общий кэш, как раньше
    assert paths.storage_missing() is None


def test_chosen_folder_holds_engine_and_models(data, tmp_path):
    root = tmp_path / "E" / "Meet"
    root.mkdir(parents=True)
    _choose(data, root)
    assert paths.storage_root() == root
    assert paths.models_dir() == root / "models"
    assert paths.engine_dir() == root / "engine"
    # Свой кэш HF — даже если в окружении задан общий.
    assert models.cache_root() == root / "models" / "hf"
    assert models.shared_cache_root() == tmp_path / "hf-shared"
    from meet import gigaam_asr

    assert gigaam_asr.cache_dir() == root / "models" / "gigaam"


@pytest.mark.parametrize("raw", ["", "{", "[]", '{"root": ""}', '{"root": "   "}', '{"root": 5}',
                                 '{"root": "relative/path"}', '{"version": 2, "root": "C:/x"}'])
def test_broken_choice_file_is_not_a_silent_default(data, raw):
    """Оборванная запись или чужой формат — «не прочитан», а не «по
    умолчанию»: движок на системном диске перенос уже удалил, молча ставить
    его заново нельзя. Модели не качаются, пока человек не решит."""
    (data / paths.STORAGE_FILE).write_text(raw, encoding="utf-8")
    assert paths.storage_root() is None
    assert paths.storage_unreadable() is True
    assert paths.storage_missing() == data / paths.STORAGE_FILE
    lines = []
    assert models.download(MEET_REPO, on_line=lines.append) == 4


def test_versioned_choice_is_read(data, tmp_path):
    root = tmp_path / "Meet"
    root.mkdir()
    (data / paths.STORAGE_FILE).write_text(json.dumps({"version": 1, "root": str(root)}), encoding="utf-8")
    assert paths.storage_root() == root
    assert paths.storage_unreadable() is False
    assert paths.storage_missing() is None


def test_no_choice_file_is_not_unreadable(data):
    assert paths.storage_unreadable() is False


def test_missing_folder_is_reported_not_created(data, tmp_path):
    """Внешний диск отключён: папки нет — так и говорим, ничего не создаём."""
    root = tmp_path / "unplugged" / "Meet"
    _choose(data, root)
    assert paths.storage_missing() == root
    assert models.cache_root() == root / "models" / "hf"
    assert not root.exists()


def test_meet_cache_reaches_children_through_the_environment(data, tmp_path, monkeypatch):
    """Библиотеки (faster-whisper, transformers, pyannote) берут кэш из
    HF_HUB_CACHE при импорте: резидент и CLI выставляют его один раз при
    старте, дети (задачи, ассистент, проба устройств) наследуют окружение."""
    root = tmp_path / "Meet"
    root.mkdir()
    _choose(data, root)
    models.use_meet_cache()
    assert os.environ["HF_HUB_CACHE"] == str(root / "models" / "hf")
    assert os.environ["HF_XET_CACHE"] == str(root / "models" / "xet")
    from meet import netproxy

    child = netproxy.settings_env()
    assert child["HF_HUB_CACHE"] == str(root / "models" / "hf")


def test_user_transformers_cache_does_not_bypass_the_meet_cache(data, tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFORMERS_CACHE", str(tmp_path / "tc"))
    monkeypatch.setenv("PYTORCH_TRANSFORMERS_CACHE", str(tmp_path / "ptc"))
    root = tmp_path / "Meet"
    root.mkdir()
    _choose(data, root)
    models.use_meet_cache()
    assert "TRANSFORMERS_CACHE" not in os.environ
    assert "PYTORCH_TRANSFORMERS_CACHE" not in os.environ


def test_without_choice_the_environment_is_untouched(data, tmp_path):
    models.use_meet_cache()
    assert os.environ["HF_HUB_CACHE"] == str(tmp_path / "hf-shared")
    assert "HF_XET_CACHE" not in os.environ


def test_resident_and_cli_switch_to_the_meet_cache_at_start(monkeypatch):
    from meet import cli, tray

    calls = []
    monkeypatch.setattr(models, "use_meet_cache", lambda: calls.append("cache"))
    monkeypatch.setattr(tray.sys, "argv", ["meet-tray", "--headless"])
    monkeypatch.setattr(tray, "_resident_alive", lambda: True)
    with pytest.raises(SystemExit):
        tray.main()
    assert calls == ["cache"]
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert calls == ["cache", "cache"]


def test_download_refuses_when_the_chosen_folder_is_missing(data, tmp_path):
    _choose(data, tmp_path / "unplugged")
    lines = []
    assert models.download(MEET_REPO, on_line=lines.append) == 4
    assert "недоступна" in lines[0]


# --- перенос моделей ------------------------------------------------------------


def _source(data: Path, shared: Path):
    meet = _repo(shared, MEET_REPO, {"model.bin": b"w" * 1000, "config.json": b"{}"})
    diar = _repo(shared, DIAR_REPO, {"config.yaml": b"c", "embedding/pytorch_model.bin": b"e" * 50})
    other = _repo(shared, OTHER_REPO, {"model.bin": b"o" * 500})
    gig = _gigaam(data / "models")
    return meet, diar, other, gig


def test_copy_takes_only_meet_models_and_verifies_them(data, tmp_path):
    shared = tmp_path / "hf-shared"
    meet, diar, other, gig = _source(data, shared)
    target = tmp_path / "E" / "Meet"
    seen = []
    result = storage.copy_models(target, on_progress=lambda done, total: seen.append((done, total)))

    own = target / "models" / "hf"
    copied = own / meet.name
    assert (copied / "refs" / "main").read_text(encoding="utf-8") == "rev1"
    assert (copied / "snapshots" / "rev1" / "model.bin").read_bytes() == b"w" * 1000
    assert (own / diar.name / "snapshots" / "rev1" / "embedding" / "pytorch_model.bin").is_file()
    assert not (own / other.name).exists(), "чужие модели общего кэша не трогаем"
    assert (target / "models" / "gigaam" / "v3_e2e_rnnt.ckpt").read_bytes() == b"weights" * 10
    assert not (target / "models" / "gigaam" / "v3_e2e_rnnt.ckpt.part").exists()
    # Источник цел: удалять — только после переключения и с согласия.
    assert (meet / "snapshots" / "rev1" / "model.bin").is_file()
    assert (gig / "v3_e2e_rnnt.ckpt").is_file()
    total = 1000 + 2 + 1 + 50 + 70 + 3 + 2 + 4 + 4  # файлы + refs/main
    assert result["bytes"] == total
    assert seen and seen[-1] == (total, total)
    # Время изменения сохраняется: GigaAM не пересчитывает сумму весов зря.
    src = gig / "v3_e2e_rnnt.ckpt"
    dst = target / "models" / "gigaam" / "v3_e2e_rnnt.ckpt"
    assert dst.stat().st_mtime_ns == src.stat().st_mtime_ns


def test_copied_models_are_found_by_the_loaders_after_the_switch(data, tmp_path):
    shared = tmp_path / "hf-shared"
    _source(data, shared)
    target = tmp_path / "Meet"
    storage.copy_models(target)
    _choose(data, target)
    assert models.local_snapshot(DIAR_REPO) == (
        target / "models" / "hf" / ("models--" + DIAR_REPO.replace("/", "--")) / "snapshots" / "rev1")
    assert models.downloaded(MEET_REPO)
    assert not models.downloaded(OTHER_REPO)
    from meet import gigaam_asr

    assert gigaam_asr.model_files("v3_e2e_rnnt")[0].is_file()


def test_copy_is_idempotent_and_resumes(data, tmp_path):
    shared = tmp_path / "hf-shared"
    _source(data, shared)
    target = tmp_path / "Meet"
    storage.copy_models(target)
    again = storage.copy_models(target)
    assert again["copied"] == 0
    # Оборванная копия (недописанный файл) докопируется.
    broken = target / "models" / "hf" / ("models--" + MEET_REPO.replace("/", "--")) / "snapshots" / "rev1" / "model.bin"
    broken.write_bytes(b"w" * 10)
    third = storage.copy_models(target)
    assert third["copied"] == 1000
    assert broken.read_bytes() == b"w" * 1000


def test_copy_follows_symlinked_snapshots(data, tmp_path):
    """macOS (и Windows с режимом разработчика): файлы снапшота — ссылки на
    blobs. Копия — настоящие файлы, без ссылок: так работает и на exFAT."""
    shared = tmp_path / "hf-shared"
    folder = _repo(shared, MEET_REPO, {})
    blob = folder / "blobs" / "abc"
    blob.write_bytes(b"real" * 100)
    link = folder / "snapshots" / "rev1" / "model.bin"
    try:
        link.symlink_to(Path("..") / ".." / "blobs" / "abc")
    except OSError:
        pytest.skip("символические ссылки недоступны")
    storage.copy_models(tmp_path / "Meet")
    copied = tmp_path / "Meet" / "models" / "hf" / folder.name / "snapshots" / "rev1" / "model.bin"
    assert copied.read_bytes() == b"real" * 100 and not copied.is_symlink()
    assert not (tmp_path / "Meet" / "models" / "hf" / folder.name / "blobs").exists()


def test_copy_that_does_not_match_is_an_error(data, tmp_path, monkeypatch):
    _source(data, tmp_path / "hf-shared")
    real = storage._digest

    def corrupt(path):
        return "0" * 64 if ".meet-copy" not in path.name and "Meet" in str(path) else real(path)

    monkeypatch.setattr(storage, "_digest", corrupt)
    with pytest.raises(storage.CopyError, match="не совпала"):
        storage.copy_models(tmp_path / "Meet")


def test_copy_refuses_without_space(data, tmp_path, monkeypatch):
    _source(data, tmp_path / "hf-shared")
    monkeypatch.setattr(storage, "_free_bytes", lambda path: 10)
    with pytest.raises(storage.CopyError, match="Недостаточно места"):
        storage.copy_models(tmp_path / "Meet")
    assert not (tmp_path / "Meet" / "models" / "hf").exists()


def test_models_bytes_counts_only_meet_models(data, tmp_path):
    _source(data, tmp_path / "hf-shared")
    assert storage.models_bytes() == 1000 + 2 + 1 + 50 + 70 + 3 + 2 + 4 + 4


def test_copy_cli_streams_progress_as_json_lines(data, tmp_path, capsys):
    _source(data, tmp_path / "hf-shared")
    assert storage.main(["copy", "--to", str(tmp_path / "Meet")]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1]["ok"] is True and lines[-1]["bytes"] > 0
    assert any("done" in line for line in lines)


def test_copy_cli_reports_errors_in_russian(data, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(storage, "_free_bytes", lambda path: 0)
    _source(data, tmp_path / "hf-shared")
    assert storage.main(["copy", "--to", str(tmp_path / "Meet")]) == 1
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert "Недостаточно места" in last["error"]


# --- остатки в общем кэше: спросить и удалить только модели Meet -----------------


def _moved(data: Path, tmp_path: Path) -> Path:
    shared = tmp_path / "hf-shared"
    _source(data, shared)
    target = tmp_path / "Meet"
    storage.copy_models(target)
    _choose(data, target)
    (data / storage.LEFTOVERS_FILE).write_text("{}", encoding="utf-8")
    return shared


def test_leftovers_are_meet_models_already_copied(data, tmp_path):
    shared = _moved(data, tmp_path)
    found = storage.leftovers()
    assert found["cache"] == str(shared)
    assert sorted(r["id"] for r in found["repos"]) == sorted([MEET_REPO, DIAR_REPO])
    assert found["bytes"] == sum(r["bytes"] for r in found["repos"]) > 0


def test_resident_with_the_meet_cache_still_sees_the_shared_one(data, tmp_path):
    """Резидент при старте направил HF_HUB_CACHE в свой кэш; общий кэш (для
    вопроса об остатках) — по исходному окружению человека."""
    shared = _moved(data, tmp_path)
    models.use_meet_cache()
    assert models.cache_root() == paths.models_dir() / "hf"
    assert models.shared_cache_root() == shared
    assert storage.leftovers()["cache"] == str(shared)
    models.use_meet_cache()  # повторный вызов (CLI внутри резидента) не теряет исходное
    assert models.shared_cache_root() == shared


def test_shared_cache_without_user_variable_is_the_default(data, tmp_path, monkeypatch):
    monkeypatch.delenv("HF_HUB_CACHE")
    for env in ("HUGGINGFACE_HUB_CACHE", "HF_HOME"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "Meet"
    root.mkdir()
    _choose(data, root)
    models.use_meet_cache()
    assert os.environ["HF_HUB_CACHE"] == str(root / "models" / "hf")
    assert models.shared_cache_root() == tmp_path / "xdg" / "huggingface" / "hub"


def test_no_question_without_a_move(data, tmp_path):
    _source(data, tmp_path / "hf-shared")
    assert storage.leftovers() is None


def test_delete_leftovers_removes_only_meet_repos(data, tmp_path):
    shared = _moved(data, tmp_path)
    result = storage.answer_leftovers(delete=True)
    assert result["ok"] is True
    assert sorted(result["removed"]) == sorted([MEET_REPO, DIAR_REPO])
    assert (shared / ("models--" + OTHER_REPO.replace("/", "--"))).is_dir()
    assert not (shared / ("models--" + MEET_REPO.replace("/", "--"))).exists()
    assert not (data / storage.LEFTOVERS_FILE).exists()
    assert storage.leftovers() is None
    assert models.downloaded(MEET_REPO)  # копия в своём кэше на месте


def test_keep_leftovers_only_forgets_the_question(data, tmp_path):
    shared = _moved(data, tmp_path)
    assert storage.answer_leftovers(delete=False) == {"ok": True, "removed": []}
    assert (shared / ("models--" + MEET_REPO.replace("/", "--"))).is_dir()
    assert storage.leftovers() is None


def test_repo_missing_in_the_new_cache_is_not_offered(data, tmp_path):
    shared = _moved(data, tmp_path)
    import shutil

    shutil.rmtree(paths.models_dir() / "hf" / ("models--" + DIAR_REPO.replace("/", "--")))
    assert [r["id"] for r in storage.leftovers()["repos"]] == [MEET_REPO]
    storage.answer_leftovers(delete=True)
    assert (shared / ("models--" + DIAR_REPO.replace("/", "--"))).is_dir()


def test_shared_cache_equal_to_own_is_never_offered(data, tmp_path, monkeypatch):
    shared = _moved(data, tmp_path)
    monkeypatch.setenv("HF_HUB_CACHE", str(paths.models_dir() / "hf"))
    assert storage.leftovers() is None
    assert shared.is_dir()


def test_partial_own_copy_is_not_offered_for_deletion(data, tmp_path):
    """Своя копия без одного файла или с другим размером — из общего кэша не
    удаляем: там единственная целая копия."""
    _moved(data, tmp_path)
    own = paths.models_dir() / "hf" / ("models--" + MEET_REPO.replace("/", "--")) / "snapshots" / "rev1"
    (own / "config.json").unlink()
    (own / "model.bin").write_bytes(b"w" * 10)
    assert [r["id"] for r in storage.leftovers()["repos"]] == [DIAR_REPO]


def test_leftover_behind_a_link_is_not_deleted_through_it(data, tmp_path):
    """Папка репозитория в общем кэше — ссылка (junction/symlink) на чужое
    место: удаляем не через неё — содержимое по ту сторону цело."""
    import shutil

    shared = _moved(data, tmp_path)
    folder = shared / ("models--" + MEET_REPO.replace("/", "--"))
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(folder, elsewhere)
    shutil.rmtree(folder)
    try:
        import _winapi

        _winapi.CreateJunction(str(elsewhere), str(folder))
    except (ImportError, AttributeError, OSError):
        try:
            folder.symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            pytest.skip("ни junction, ни символической ссылки")
    storage.answer_leftovers(delete=True)
    assert (elsewhere / "snapshots" / "rev1" / "model.bin").is_file()


def test_copied_cache_resolves_offline_with_real_huggingface_hub(data, tmp_path, monkeypatch):
    """Настоящий huggingface_hub находит модель в скопированном кэше без сети
    и без `blobs/` (как кэш HF на Windows без символических ссылок)."""
    hub = pytest.importorskip("huggingface_hub")
    _source(data, tmp_path / "hf-shared")
    target = tmp_path / "Meet"
    storage.copy_models(target)
    own = target / "models" / "hf"
    assert not (own / ("models--" + MEET_REPO.replace("/", "--")) / "blobs").exists()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    snap = hub.snapshot_download(MEET_REPO, cache_dir=str(own), local_files_only=True)
    assert Path(snap, "model.bin").read_bytes() == b"w" * 1000
    one = hub.hf_hub_download(DIAR_REPO, "embedding/pytorch_model.bin", cache_dir=str(own),
                              local_files_only=True)
    assert Path(one).read_bytes() == b"e" * 50


def test_moving_is_only_the_steps_before_the_switch(data):
    journal = data / storage.JOURNAL_FILE
    for phase, moving in (("engine", True), ("models", True), ("switching", True),
                          ("interrupted", False), ("cleanup", False)):
        journal.write_text(json.dumps({"version": 1, "phase": phase}), encoding="utf-8")
        assert storage.moving() is moving, phase
    journal.write_text("{", encoding="utf-8")
    assert storage.moving() is True  # оболочка при старте отложит его в .bad
    journal.unlink()
    assert storage.moving() is False


# --- удержание резидента перед переключением -------------------------------------


def test_hold_is_taken_only_when_idle_and_blocks_new_work():
    hold = storage.Hold()
    assert hold.take(lambda: "идёт запись") == "идёт запись"
    assert not hold.held()
    assert hold.take(lambda: None) is None
    assert hold.held()
    with pytest.raises(storage.Held):
        with hold.gate():
            pass
    hold.release()
    with hold.gate():
        pass


def test_hold_expires_by_itself():
    now = [100.0]
    hold = storage.Hold(clock=lambda: now[0])
    hold.take(lambda: None, seconds=300)
    now[0] += 299
    assert hold.held()
    now[0] += 2
    assert not hold.held()


def test_hold_and_start_are_atomic():
    """Начало (запись, задача) под воротами и удержание — под одним замком:
    начавшееся до удержания видно его проверке «занят»."""
    import threading

    hold = storage.Hold()
    entered, started = threading.Event(), []

    def start():
        with hold.gate():
            entered.set()
            time.sleep(0.2)
            started.append(True)

    worker = threading.Thread(target=start)
    worker.start()
    entered.wait(2)
    assert hold.take(lambda: "идёт запись" if started else None) == "идёт запись"
    worker.join()
    assert not hold.held()


def test_info_describes_locations(data, tmp_path):
    _source(data, tmp_path / "hf-shared")
    info = storage.info()
    assert info["custom"] is False and info["root"] is None
    assert info["home"] == str(data)
    assert info["hf_cache"] == str(tmp_path / "hf-shared")
    assert info["models_bytes"] > 0
    assert info["moving"] is False and info["leftovers"] is None
    assert info["unreadable"] is False and info["prefix"]
    (data / storage.JOURNAL_FILE).write_text('{"version": 1, "phase": "models"}', encoding="utf-8")
    assert storage.info()["moving"] is True
    # Переключились, убираем прежнюю папку — качать можно, уже в новую.
    (data / storage.JOURNAL_FILE).write_text('{"phase": "cleanup"}', encoding="utf-8")
    assert storage.moving() is False
