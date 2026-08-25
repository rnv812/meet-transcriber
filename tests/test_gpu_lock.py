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


def test_marker_can_be_disabled(monkeypatch, tmp_path):
    """Кому нечем читать маркер — выключает, и файл не создаётся вовсе."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.json").write_text(
        json.dumps({"integrations": {"gpu_marker": False}}), encoding="utf-8"
    )
    with hold_gpu_lock("transcribe"):
        assert not lock_path().exists()


def test_marker_path_can_be_moved(monkeypatch, tmp_path):
    """Путь настраивается осознанно: наблюдатель может ждать маркер в другом
    месте. По умолчанию он остаётся там же, где был всегда."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    custom = tmp_path / "где-то" / "busy.lock"
    (tmp_path / "config.json").write_text(
        json.dumps({"integrations": {"gpu_marker_path": str(custom)}}),
        encoding="utf-8",
    )
    with hold_gpu_lock("live"):
        assert custom.exists()
    assert not custom.exists()


def test_held_by_live_process_true_for_self(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    import os
    from meet import gpu_lock
    (tmp_path).mkdir(parents=True, exist_ok=True)
    lock_path().write_text(json.dumps({"pid": os.getpid(), "reason": "x"}), encoding="utf-8")
    assert gpu_lock.held_by_live_process() is True


def test_stale_marker_reads_as_not_held(tmp_path, monkeypatch):
    """Убитый без finally процесс оставляет файл — панель не должна вечно
    показывать «GPU занят»."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    from meet import gpu_lock
    (tmp_path).mkdir(parents=True, exist_ok=True)
    # заведомо мёртвый pid
    lock_path().write_text(json.dumps({"pid": 999999, "reason": "x"}), encoding="utf-8")
    assert gpu_lock.held_by_live_process() is False


def test_lock_not_removed_when_pid_is_not_ours(tmp_path, monkeypatch):
    """finally снимает только свой маркер: осиротевший процесс не должен удалить
    маркер новой работы."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    from meet import gpu_lock
    with hold_gpu_lock("transcribe"):
        # пока держим — кто-то другой перезаписал маркер своим pid
        lock_path().write_text(json.dumps({"pid": 424242, "reason": "чужой"}),
                               encoding="utf-8")
    # наш finally не должен был снять чужой маркер
    assert lock_path().exists()
