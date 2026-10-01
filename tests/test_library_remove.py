"""Удаление папок записей «все или ни одной»: занятая папка остаётся целой."""

import subprocess
import sys
from pathlib import Path

import pytest

from meet import library


def _folder(root, name, files=("sys.opus", "mic.opus")):
    folder = root / name
    folder.mkdir(parents=True)
    for file in files:
        (folder / file).write_bytes(b"x")
    return folder


class _Clock:
    """Часы, которые идут только когда кто-то ждёт (sleep)."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_remove_folders_deletes_everything(tmp_path):
    a, b = _folder(tmp_path, "2026-09-30_10-00"), _folder(tmp_path, "2026-09-30_11-00")
    library.remove_folders([a, b])
    assert list(tmp_path.iterdir()) == []


def test_busy_folder_keeps_every_folder_whole(tmp_path):
    """Вторую папку держит агент: не удаляется ни одна, и ни один файл не пропал."""
    a, b = _folder(tmp_path, "2026-09-30_10-00"), _folder(tmp_path, "2026-09-30_11-00")
    clock = _Clock()

    def rename(src, dst):
        if src == b:
            raise PermissionError(32, "занято")
        return library.os.rename(src, dst)

    with pytest.raises(library.FolderBusy, match="агент в терминале"):
        library.remove_folders([a, b], wait_s=1.0, sleep=clock.sleep, clock=clock, rename=rename)
    assert sorted(p.name for p in tmp_path.iterdir()) == [a.name, b.name]
    assert sorted(p.name for p in a.iterdir()) == ["mic.opus", "sys.opus"]
    assert clock.now >= 1.0  # подождали, прежде чем отказать


def test_folder_released_while_waiting_is_removed(tmp_path):
    a = _folder(tmp_path, "2026-09-30_10-00")
    clock = _Clock()
    tries = []

    def rename(src, dst):
        tries.append(src)
        if len(tries) < 3:
            raise PermissionError(32, "занято")
        return library.os.rename(src, dst)

    library.remove_folders([a], wait_s=2.0, sleep=clock.sleep, clock=clock, rename=rename)
    assert not a.exists() and len(tries) == 3


def test_missing_folder_is_not_an_error(tmp_path):
    library.remove_folders([tmp_path / "2026-09-30_10-00"])


@pytest.mark.skipif(sys.platform != "win32", reason="занятая рабочая папка — поведение Windows")
def test_folder_used_as_working_directory_is_not_half_deleted(tmp_path):
    """Настоящий процесс с рабочей папкой в записи (как агент во вкладке
    «Агент»): rmtree снёс бы файлы и упал на папке — здесь всё остаётся."""
    folder = _folder(tmp_path, "2026-09-30_10-00")
    child = subprocess.Popen([sys.executable, "-c", "import time; print(1, flush=True); time.sleep(30)"],
                             cwd=folder, stdout=subprocess.PIPE)
    child.stdout.readline()  # процесс поднялся и сидит в папке
    try:
        with pytest.raises(library.FolderBusy):
            library.remove_folders([folder], wait_s=0.3)
        assert sorted(p.name for p in folder.iterdir()) == ["mic.opus", "sys.opus"]
        with pytest.raises(library.FolderBusy):
            library.wait_removable([folder], wait_s=0.3)
        assert folder.is_dir()
    finally:
        child.kill()
        child.wait()
    library.wait_removable([folder], wait_s=0.3)
    library.remove_folders([folder], wait_s=2.0)
    assert not folder.exists()


def test_leftover_deletions_are_hidden_from_the_library(tmp_path):
    _folder(tmp_path, "2026-09-30_10-00")
    leftover = _folder(tmp_path, ".2026-09-30_11-00.deleting-abcd1234")
    assert [p.name for p in library.recording_folders(tmp_path)] == ["2026-09-30_10-00"]
    assert library.leftover_deletions(tmp_path) == [leftover]


def test_probe_renames_aside_with_its_own_mark(tmp_path):
    """Проверка «можно ли удалить» откладывает папку под меткой `.probing-`,
    не `.deleting-`: прерванную проверку восстановление вернёт, а не удалит."""
    folder = _folder(tmp_path, "2026-09-30_10-00")
    seen = []

    def rename(src, dst):
        seen.append(Path(dst).name)
        return library.os.rename(src, dst)

    library.wait_removable([folder], rename=rename)
    assert seen[0].startswith(f".{folder.name}{library.PROBING_MARK}")
    assert library.DELETING_MARK not in seen[0]
    assert sorted(p.name for p in tmp_path.iterdir()) == [folder.name]


def test_probe_retries_the_rename_back(tmp_path):
    """Обратное переименование не прошло с первого раза (антивирус, индексатор
    заглянул в папку) — пробуем ещё, папка возвращается под своё имя."""
    folder = _folder(tmp_path, "2026-09-30_10-00")
    clock = _Clock()
    backs = []

    def rename(src, dst):
        if Path(dst) == folder:
            backs.append(src)
            if len(backs) < 4:
                raise PermissionError(5, "занято")
        return library.os.rename(src, dst)

    library.wait_removable([folder], sleep=clock.sleep, clock=clock, rename=rename)
    assert len(backs) == 4
    assert sorted(p.name for p in tmp_path.iterdir()) == [folder.name]
    assert sorted(p.name for p in folder.iterdir()) == ["mic.opus", "sys.opus"]


def test_probe_that_cannot_rename_back_keeps_the_folder_for_recovery(tmp_path):
    """Вернуть так и не удалось: ошибка человеку, а папка остаётся отложенной
    проверкой — её вернёт восстановление, удаление её не тронет."""
    folder = _folder(tmp_path, "2026-09-30_10-00")
    clock = _Clock()

    def rename(src, dst):
        if Path(dst) == folder:
            raise PermissionError(5, "занято")
        return library.os.rename(src, dst)

    with pytest.raises(library.FolderBusy):
        library.wait_removable([folder], sleep=clock.sleep, clock=clock, rename=rename)
    assert clock.slept.count(library.RENAME_BACK_PAUSE_S) == library.RENAME_BACK_TRIES - 1
    assert library.leftover_deletions(tmp_path) == []
    [probe] = library.leftover_probes(tmp_path)
    assert sorted(p.name for p in probe.iterdir()) == ["mic.opus", "sys.opus"]
    assert library.restore_probes(tmp_path) == [folder.name]
    assert sorted(p.name for p in tmp_path.iterdir()) == [folder.name]
    assert sorted(p.name for p in folder.iterdir()) == ["mic.opus", "sys.opus"]


def test_restore_probes_never_overwrites_an_existing_folder(tmp_path):
    folder = _folder(tmp_path, "2026-09-30_10-00", files=("new.opus",))
    probe = _folder(tmp_path, f".{folder.name}{library.PROBING_MARK}abcd1234")
    assert library.restore_probes(tmp_path) == []
    assert probe.is_dir() and sorted(p.name for p in folder.iterdir()) == ["new.opus"]


def test_probe_folders_are_hidden_and_not_deletions(tmp_path):
    _folder(tmp_path, "2026-09-30_10-00")
    probe = _folder(tmp_path, f".2026-09-30_11-00{library.PROBING_MARK}abcd1234")
    assert [p.name for p in library.recording_folders(tmp_path)] == ["2026-09-30_10-00"]
    assert library.leftover_deletions(tmp_path) == []
    assert library.leftover_probes(tmp_path) == [probe]


def test_cancelled_delete_retries_putting_folders_back(tmp_path):
    """Удаление отменено (вторая папка занята): первую возвращаем на место с
    повторами — иначе её как `.deleting-` снесло бы восстановление."""
    a, b = _folder(tmp_path, "2026-09-30_10-00"), _folder(tmp_path, "2026-09-30_11-00")
    clock = _Clock()
    backs = []

    def rename(src, dst):
        if src == b:
            raise PermissionError(32, "занято")
        if Path(dst) == a:
            backs.append(src)
            if len(backs) < 3:
                raise PermissionError(5, "занято")
        return library.os.rename(src, dst)

    with pytest.raises(library.FolderBusy):
        library.remove_folders([a, b], wait_s=1.0, sleep=clock.sleep, clock=clock, rename=rename)
    assert len(backs) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == [a.name, b.name]
