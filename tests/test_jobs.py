"""Очередь задач: последовательность, прогресс, отмена, честные ошибки.

Подпроцесс здесь подменяется: настоящий требует установленного движка и минут
работы, а проверять надо саму очередь.
"""

import json
import threading
import time

from meet import events, jobs


def _wait(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "не дождались состояния"
        time.sleep(0.02)


def _fake_spawn(lines, code: int = 0, started: threading.Event | None = None,
                release: threading.Event | None = None):
    """Подпроцесс-заглушка: печатает заданные строки и возвращает код."""

    def spawn(job, on_line):
        if started is not None:
            started.set()
        if release is not None:
            release.wait(timeout=5)
        for line in lines:
            on_line(line if isinstance(line, str) else json.dumps(line))
        return code

    return spawn


def test_job_runs_and_reports_result(tmp_path):
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    queue = jobs.JobQueue(bus, spawn=_fake_spawn([
        {"kind": "progress", "stage": "asr", "label": "распознавание",
         "done": 1, "total": 2, "note": "sys"},
        {"kind": "job.result", "path": str(tmp_path / "т.md")},
    ]))
    job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(lambda: queue.get(job.id).state == jobs.DONE)
    finally:
        queue.stop()
    done = queue.get(job.id)
    assert done.result.endswith("т.md")
    assert done.stage == "asr" and done.done == 1
    kinds = [e.kind for e in seen]
    assert kinds[0] == jobs.JOB_QUEUED
    assert jobs.JOB_STARTED in kinds and jobs.JOB_PROGRESS in kinds
    assert kinds[-1] == jobs.JOB_DONE


def test_failed_job_keeps_error_text(tmp_path):
    queue = jobs.JobQueue(spawn=_fake_spawn(
        [{"kind": "error", "text": "движок расшифровки не установлен"}], code=2
    ))
    job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(lambda: queue.get(job.id).state == jobs.FAILED)
    finally:
        queue.stop()
    assert "движок" in queue.get(job.id).error


def test_success_without_result_is_a_failure(tmp_path):
    """Подпроцесс отработал, но результата не назвал — редактору нечего открыть."""
    queue = jobs.JobQueue(spawn=_fake_spawn([], code=0))
    job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(lambda: queue.get(job.id).state == jobs.FAILED)
    finally:
        queue.stop()
    assert "без результата" in queue.get(job.id).error


def test_plain_output_becomes_error_text(tmp_path):
    """Пайплайн печатает и не-JSON: последняя строка пригодится текстом ошибки."""
    queue = jobs.JobQueue(spawn=_fake_spawn(["Не найдено: K:/нет\n"], code=3))
    job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(lambda: queue.get(job.id).state == jobs.FAILED)
    finally:
        queue.stop()
    assert "Не найдено" in queue.get(job.id).error


def test_jobs_run_one_at_a_time(tmp_path):
    """Слот один: две расшифровки всё равно упрутся в одну карту."""
    started, release = threading.Event(), threading.Event()
    queue = jobs.JobQueue(spawn=_fake_spawn(
        [{"kind": "job.result", "path": "x"}], started=started, release=release
    ))
    first = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    second = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(started.is_set)
        assert queue.get(second.id).state == jobs.QUEUED
        release.set()
        _wait(lambda: queue.get(second.id).state == jobs.DONE)
    finally:
        release.set()
        queue.stop()
    assert queue.get(first.id).state == jobs.DONE


def test_pending_job_can_be_cancelled(tmp_path):
    started, release = threading.Event(), threading.Event()
    queue = jobs.JobQueue(spawn=_fake_spawn(
        [{"kind": "job.result", "path": "x"}], started=started, release=release
    ))
    queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    waiting = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(started.is_set)
        assert queue.cancel(waiting.id) is True
        assert queue.get(waiting.id).state == jobs.CANCELLED
    finally:
        release.set()
        queue.stop()


def test_listing_is_newest_last_and_bounded(tmp_path):
    queue = jobs.JobQueue(spawn=_fake_spawn([{"kind": "job.result", "path": "x"}]))
    ids = [queue.submit(jobs.TRANSCRIBE, str(tmp_path)).id for _ in range(3)]
    try:
        _wait(lambda: all(queue.get(i).state == jobs.DONE for i in ids))
    finally:
        queue.stop()
    listing = queue.listing(limit=2)
    assert [j["id"] for j in listing] == ids[-2:]


def test_worker_argv_carries_options(tmp_path):
    job = jobs.Job(id="1", kind=jobs.TRANSCRIBE, folder=str(tmp_path),
                   options={"speakers": 3, "align": False, "hotwords": "джоба"})
    argv = jobs.worker_argv(job)
    assert argv[1:4] == ["-m", "meet.job_worker", "transcribe"]
    assert "--speakers" in argv and "3" in argv
    assert "--no-align" in argv and "--hotwords" in argv


def test_queue_survives_a_broken_spawn(tmp_path):
    """Сбой запуска не должен убивать очередь: следующая задача обязана пойти."""

    def boom(job, on_line):
        raise RuntimeError("нечем запускать")

    queue = jobs.JobQueue(spawn=boom)
    first = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(lambda: queue.get(first.id).state == jobs.FAILED)
        queue._spawn = _fake_spawn([{"kind": "job.result", "path": "x"}])
        second = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
        _wait(lambda: queue.get(second.id).state == jobs.DONE)
    finally:
        queue.stop()


def test_safe_cwd_handles_non_path_folders(tmp_path):
    """У загрузки модели folder — repo_id, а не путь: cwd от него роняло запуск
    подпроцесса на Windows (WinError 267)."""
    assert jobs._safe_cwd("Systran/faster-whisper-large-v3") is None
    assert jobs._safe_cwd("install-engine") is None
    assert jobs._safe_cwd(str(tmp_path)) == str(tmp_path)
    rec = tmp_path / "2026-08-18_11-00"
    rec.mkdir()
    assert jobs._safe_cwd(str(rec)) == str(rec)


def test_cancel_running_marks_cancelled_not_failed(tmp_path):
    """Отмена идущей задачи — CANCELLED, а не FAILED «код возврата 1»."""
    started, release = threading.Event(), threading.Event()

    def spawn(job, on_line):
        started.set()
        release.wait(timeout=5)
        return 1  # kill закрывает пайп → ненулевой код

    queue = jobs.JobQueue(spawn=spawn)
    job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(started.is_set)
        assert queue.cancel(job.id) is True
        release.set()
        _wait(lambda: queue.get(job.id).finished_at is not None)
    finally:
        release.set()
        queue.stop()
    assert queue.get(job.id).state == jobs.CANCELLED


def test_cancel_during_spawn_window_is_not_lost(tmp_path):
    """Отмена в окне до публикации процесса не теряется."""
    entered, release = threading.Event(), threading.Event()

    def spawn(job, on_line):
        entered.set()
        release.wait(timeout=5)
        return 0

    queue = jobs.JobQueue(spawn=spawn)
    job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
    try:
        _wait(entered.is_set)
        assert queue.cancel(job.id) is True
        assert queue.get(job.id).state == jobs.CANCELLED
        release.set()
        _wait(lambda: queue.get(job.id).finished_at is not None)
    finally:
        release.set()
        queue.stop()
    assert queue.get(job.id).state == jobs.CANCELLED


def test_worker_runs_below_normal_priority():
    """Расшифровка не должна тормозить встречу и остальную работу."""
    import subprocess

    flags = jobs._creationflags()
    assert flags & getattr(subprocess, "CREATE_NO_WINDOW", 0) == getattr(
        subprocess, "CREATE_NO_WINDOW", 0)
    assert flags & getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) == getattr(
        subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def test_import_job_argv(tmp_path):
    job = jobs.Job(id="x", kind=jobs.IMPORT, folder=str(tmp_path))
    argv = jobs.worker_argv(job)
    assert argv[-2:] == ["import", str(tmp_path)]
    assert jobs.IMPORT in jobs.KINDS


def test_active_for_finds_queued_and_running_jobs_of_a_folder(tmp_path):
    """Дубли расшифровки ловятся по папке: та же запись, пока её задача ждёт
    или идёт, второй раз в очередь не встаёт."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    started, release = threading.Event(), threading.Event()
    queue = jobs.JobQueue(events.EventBus(), spawn=_fake_spawn(
        [{"kind": "job.result", "path": "x"}], started=started, release=release))
    try:
        first = queue.submit(jobs.TRANSCRIBE, str(a))
        assert started.wait(5)
        second = queue.submit(jobs.IMPORT, str(b))
        kinds = (jobs.TRANSCRIBE, jobs.IMPORT)
        assert queue.active_for(str(a / "."), kinds).id == first.id    # идёт
        assert queue.active_for(str(b), kinds).id == second.id        # ждёт
        assert queue.active_for(str(a), (jobs.INSTALL_ENGINE,)) is None
        assert queue.active_for(str(tmp_path / "c"), kinds) is None
        release.set()
        _wait(lambda: queue.get(second.id).state == jobs.DONE)
        assert queue.active_for(str(a), kinds) is None                # закончилась
    finally:
        release.set()
        queue.stop()


def test_active_for_ignores_cancelled_jobs(tmp_path):
    started, release = threading.Event(), threading.Event()
    queue = jobs.JobQueue(events.EventBus(), spawn=_fake_spawn(
        [], started=started, release=release))
    try:
        queue.submit(jobs.TRANSCRIBE, str(tmp_path / "busy"))
        assert started.wait(5)
        waiting = queue.submit(jobs.TRANSCRIBE, str(tmp_path / "x"))
        assert queue.cancel(waiting.id)
        assert queue.active_for(str(tmp_path / "x"), (jobs.TRANSCRIBE,)) is None
    finally:
        release.set()
        queue.stop()
