import json
import os

from meet.gpu_lock import hold_gpu_lock, lock_path


def test_lock_created_with_pid_and_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with hold_gpu_lock("transcribe"):
        data = json.loads(lock_path().read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert data["reason"] == "transcribe"
    assert not lock_path().exists()


def test_lock_removed_on_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    try:
        with hold_gpu_lock("live"):
            assert lock_path().exists()
            raise RuntimeError("боевое исключение")
    except RuntimeError:
        pass
    assert not lock_path().exists()


def test_unwritable_marker_does_not_break_work(tmp_path, monkeypatch):
    # Маркер — best effort: сбой записи не должен ронять транскрибацию.
    file_not_dir = tmp_path / "занято"
    file_not_dir.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(file_not_dir))  # mkdir внутри упадёт
    with hold_gpu_lock("transcribe"):
        pass  # не должно бросить
