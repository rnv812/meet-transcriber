"""Очередь задач: последовательность, прогресс, отмена, честные ошибки.

Подпроцесс здесь подменяется: настоящий требует установленного движка и минут
работы, а проверять надо саму очередь.
"""

import json
import threading
import time

import pytest

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


def test_created_at_is_strictly_increasing_within_one_clock_tick(monkeypatch):
    # Общий список двух очередей сортируется по created_at; при равных метках
    # порядок зависел бы от того, в какой очереди задача.
    monkeypatch.setattr(jobs.time, "time", lambda: 1000.0)
    first = jobs.Job(id="a", kind=jobs.TRANSCRIBE, folder="x")
    second = jobs.Job(id="b", kind=jobs.TRANSCRIBE, folder="x")
    assert first.created_at < second.created_at


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


def test_assistant_job_argv(tmp_path):
    """Вопрос передаётся одним аргументом `--question=…`: текст, начинающийся
    с дефиса, argparse иначе принял бы за флаг."""
    summary = jobs.worker_argv(jobs.Job(id="s", kind=jobs.SUMMARY, folder=str(tmp_path)))
    assert summary[-2:] == ["summary", str(tmp_path)]
    ask = jobs.worker_argv(jobs.Job(id="a", kind=jobs.ASK, folder=str(tmp_path),
                                    options={"question": "-что решили?"}))
    assert ask[-3:] == ["ask", str(tmp_path), "--question=-что решили?"]
    assert jobs.SUMMARY in jobs.KINDS and jobs.ASK in jobs.KINDS


def test_worker_subprocess_gets_proxy_env(tmp_path, monkeypatch):
    """Задача (итоги, вопросы, загрузка моделей) получает прокси из настроек:
    Claude Code/Codex и Hugging Face берут его из переменных среды."""
    from meet import settings

    settings.patch({"llm": {"proxy": "http://10.1.1.1:3128"}})
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen.update(kwargs)
            self.stdout = iter(())

        def wait(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)
    queue = jobs.JobQueue()
    job = jobs.Job(id="x", kind=jobs.IMPORT, folder=str(tmp_path))
    assert queue._spawn_subprocess(job, lambda line: None) == 0
    env = {k.upper(): v for k, v in seen["env"].items()}
    assert env["HTTPS_PROXY"] == "http://10.1.1.1:3128"
    assert "PATH" in env


def test_merge_kind_runs_worker_with_folder(tmp_path):
    job = jobs.Job(id="m", kind=jobs.MERGE, folder=str(tmp_path))
    assert jobs.MERGE in jobs.KINDS
    assert jobs.worker_argv(job)[-2:] == ["merge", str(tmp_path)]


def test_speaker_split_job_argv(tmp_path):
    job = jobs.Job(id="v", kind=jobs.SPEAKER_SPLIT, folder=str(tmp_path), options={"label": "-Спикер 2"})
    assert jobs.worker_argv(job)[-3:] == ["speaker_split", str(tmp_path), "--label=-Спикер 2"]
    assert jobs.SPEAKER_SPLIT in jobs.KINDS and jobs.SPEAKER_SPLIT in jobs.SPEAKER_KINDS
    assert jobs.SPEAKER_SPLIT not in jobs.FOLDER_KINDS


def test_low_priority_jobs_wait_behind_later_normal_ones():
    import threading

    from meet import jobs

    release = threading.Event()
    started = []

    def spawn(job, on_line):
        started.append(job.kind)
        release.wait(timeout=5)
        job.result = "ok"
        return 0

    q = jobs.JobQueue(spawn=spawn)
    try:
        first = q.submit(jobs.SUMMARY, "a")
        low = q.submit(jobs.ANALYZE, "b", low=True)
        normal = q.submit(jobs.ASK, "c")
        # Первая уже могла уйти в работу; из ждущих фоновая — последней.
        waiting = [i for i in q._pending if i != first.id]
        assert waiting == [normal.id, low.id]
    finally:
        release.set()
        q.stop()


def test_stage_timing_goes_to_the_resident_log(tmp_path, capsys):
    """«время ступеней …» задачи расшифровки — в вывод резидента
    (logs/resident.log); прочие строки журнала задачи туда не льются."""
    folder = tmp_path / "2026-10-01_10-00"
    queue = jobs.JobQueue(spawn=_fake_spawn([
        {"kind": "log", "text": "время ступеней (GigaAM, cpu): распознавание 20.0 с",
         "source": "timing"},
        {"kind": "log", "text": "шум"},
        {"kind": "job.result", "path": "x"},
    ]))
    job = queue.submit(jobs.TRANSCRIBE, str(folder))
    try:
        _wait(lambda: queue.get(job.id).state == jobs.DONE)
    finally:
        queue.stop()
    out = capsys.readouterr().out
    assert "задача transcribe (2026-10-01_10-00): время ступеней (GigaAM, cpu)" in out
    assert "шум" not in out


def _gated_spawn(started: dict, release: threading.Event):
    """Подпроцесс-заглушка загрузки: отмечает начало по id модели и ждёт `release`."""

    def spawn(job, on_line):
        started[job.folder] = time.monotonic()
        release.wait(timeout=5)
        on_line(json.dumps({"kind": "job.result", "path": job.folder}))
        return 0

    return spawn


def test_different_models_download_at_once():
    """Загрузки разных моделей идут одновременно, у каждой своя задача и ход."""
    started, release = {}, threading.Event()
    q = jobs.KeyedQueues(spawn=_gated_spawn(started, release))
    try:
        a, new_a = q.submit_once(jobs.DOWNLOAD_MODEL, "Systran/faster-whisper-small")
        b, new_b = q.submit_once(jobs.DOWNLOAD_MODEL, "gigaam/v3_e2e_rnnt")
        assert new_a and new_b and a.id != b.id
        _wait(lambda: len(started) == 2)  # обе пошли, не дожидаясь друг друга
        assert q.get(a.id).state == q.get(b.id).state == jobs.RUNNING
        assert {i["id"] for i in q.listing()} == {a.id, b.id}
        release.set()
        _wait(lambda: q.get(a.id).state == jobs.DONE and q.get(b.id).state == jobs.DONE)
    finally:
        release.set()
        q.stop()


def test_same_model_is_not_downloaded_twice():
    started, release = {}, threading.Event()
    q = jobs.KeyedQueues(spawn=_gated_spawn(started, release))
    try:
        first, _ = q.submit_once(jobs.DOWNLOAD_MODEL, "m")
        again, new = q.submit_once(jobs.DOWNLOAD_MODEL, "m")
        assert again.id == first.id and new is False
        assert q.active_for("m", (jobs.DOWNLOAD_MODEL,)).id == first.id
        assert q.active_for("other", (jobs.DOWNLOAD_MODEL,)) is None
        release.set()
        _wait(lambda: q.get(first.id).state == jobs.DONE)
        # Кончилась — «Обновить» ставит новую.
        later, new = q.submit_once(jobs.DOWNLOAD_MODEL, "m")
        assert new and later.id != first.id
        _wait(lambda: q.get(later.id).state == jobs.DONE)
    finally:
        release.set()
        q.stop()


def test_cancel_one_download_keeps_the_other():
    started, release = {}, threading.Event()
    q = jobs.KeyedQueues(spawn=_gated_spawn(started, release))
    try:
        a, _ = q.submit_once(jobs.DOWNLOAD_MODEL, "a")
        b, _ = q.submit_once(jobs.DOWNLOAD_MODEL, "b")
        _wait(lambda: len(started) == 2)
        assert q.cancel(a.id) is True
        assert q.cancel("нет-такой") is False
        assert q.get(a.id).state == jobs.CANCELLED
        release.set()
        _wait(lambda: q.get(b.id).state == jobs.DONE)
    finally:
        release.set()
        q.stop()
    assert q.stopping


def test_keyed_slots_any_active_and_stop():
    started, release = {}, threading.Event()
    q = jobs.KeyedQueues(spawn=_gated_spawn(started, release))
    try:
        assert q.any_active() is False
        job, _ = q.submit_once(jobs.DOWNLOAD_MODEL, "m")
        assert q.any_active() is True
        release.set()
        _wait(lambda: q.get(job.id).state == jobs.DONE)
        assert q.any_active() is False
    finally:
        release.set()
        q.stop()
    # После stop новый слот не заводится: он жил бы сиротой.
    with pytest.raises(jobs.QueueStopped):
        q.submit_once(jobs.DOWNLOAD_MODEL, "другая")
    assert q.listing()[-1]["id"] == job.id and len(q.listing()) == 1


def test_keyed_slot_key_is_normalised_like_active_for(monkeypatch):
    """Ключ слота сравнивается как в JobQueue.active_for: тот же id в другом
    регистре на Windows — тот же слот, а не вторая параллельная загрузка."""
    monkeypatch.setattr(jobs, "_folder_key", lambda folder: folder.lower())
    started, release = {}, threading.Event()
    q = jobs.KeyedQueues(spawn=_gated_spawn(started, release))
    try:
        first, _ = q.submit_once(jobs.DOWNLOAD_MODEL, "Org/Model")
        again, new = q.submit_once(jobs.DOWNLOAD_MODEL, "org/model")
        assert new is False and again.id == first.id
    finally:
        release.set()
        q.stop()


def test_worker_argv_of_owner_voice(tmp_path):
    """Путь и устройство — одним аргументом через «=»: имя с дефисом не флаг."""
    job = jobs.Job(id="o", kind=jobs.OWNER_VOICE, folder=str(tmp_path),
                   options={"wav": str(tmp_path / "t.wav"), "device": "-Микрофон (USB)"})
    assert jobs.worker_argv(job)[-4:] == [
        "owner_voice", str(tmp_path), f"--wav={tmp_path / 't.wav'}", "--device=-Микрофон (USB)"]
    job = jobs.Job(id="o", kind=jobs.OWNER_VOICE, folder=str(tmp_path), options={"wav": "x.wav"})
    assert jobs.worker_argv(job)[-1] == "--wav=x.wav"
    assert jobs.OWNER_VOICE in jobs.KINDS
