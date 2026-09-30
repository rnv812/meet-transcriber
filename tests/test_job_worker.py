import shutil

from meet import job_worker, library


def _import_folder(tmp_path):
    src = tmp_path / "in" / "встреча.mp3"
    src.parent.mkdir()
    src.write_bytes(b"media")
    return library.create_import(tmp_path / "rec", src)


def test_copy_import_leaves_only_final_source(tmp_path):
    folder = _import_folder(tmp_path)
    assert job_worker._copy_import(str(folder)) == 0
    assert (folder / "source.mp3").read_bytes() == b"media"
    assert not list(folder.glob("*.part"))


def test_copy_import_failure_cleans_up_partial(tmp_path, monkeypatch):
    folder = _import_folder(tmp_path)

    def broken(src, dst):
        open(dst, "wb").write(b"me")
        raise OSError("диск полон")

    monkeypatch.setattr(shutil, "copy2", broken)
    assert job_worker._copy_import(str(folder)) != 0
    assert not (folder / "source.mp3").exists()
    assert not list(folder.glob("*.part"))
