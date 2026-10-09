"""Один процесс движка на очередь задач (0.5.1). Тяжёлые задачи очереди
видеокарты (расшифровка, импорт, объединение, правка спикеров) идут подряд в
одном процессе `meet.job_worker --serve`: torch, pyannote и faster-whisper
импортируются один раз (8–15 с на задачу). Простой — процесс закрывается и
отдаёт видеопамять; упал или задачу отменили — следующая в новом процессе."""

import io
import json
import os
import sys
import textwrap
import threading
import time

import pytest

from meet import events, jobs, job_worker


def _wait(condition, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "не дождались состояния"
        time.sleep(0.02)


# --- процесс задач: протокол ----------------------------------------------------------


def _lines(capsys):
    return [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]


def test_serve_runs_requests_in_order_and_reports_codes(capsys, tmp_path):
    ran = []

    def run(argv):
        ran.append((argv, os.getcwd()))
        if argv[0] == "exit3":
            raise SystemExit(3)
        if argv[0] == "boom":
            raise MemoryError()
        return 0

    home = os.getcwd()
    requests = [{"argv": ["transcribe", "a"], "cwd": str(tmp_path)}, {"argv": ["exit3"]}, {"argv": ["boom"]},
                {"argv": ["transcribe", "b"]}]
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests) + "не json\n")
    assert job_worker.serve(stdin, run=run) == 0
    assert [a for a, _ in ran] == [r["argv"] for r in requests]
    assert ran[0][1] == str(tmp_path) and ran[1][1] == home and os.getcwd() == home
    out = _lines(capsys)
    assert [x["code"] for x in out if x["kind"] == job_worker.HOST_EXIT] == [0, 3, 1, 0]
    errors = [x["text"] for x in out if x["kind"] == "error"]
    assert errors and "оперативной памяти" in errors[0]


def test_serve_syncs_proxy_and_forgets_hf_token_between_jobs(monkeypatch, capsys):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://old:1")
    seen = []

    def run(argv):
        seen.append((os.environ.get("HTTPS_PROXY"), os.environ.get("HF_TOKEN")))
        os.environ["HF_TOKEN"] = "hf_job"  # задача кладёт токен из диспетчера
        return 0

    stdin = io.StringIO(json.dumps({"argv": ["x"], "env": {"HTTPS_PROXY": "http://new:2"}}) + "\n"
                        + json.dumps({"argv": ["y"], "env": {}}) + "\n")
    job_worker.serve(stdin, run=run)
    # Прокси — как сейчас в настройках; токен каждая задача берёт заново.
    assert seen == [("http://new:2", None), (None, None)]


# --- очередь: процесс-заглушка с тем же протоколом -------------------------------------

FAKE_HOST = textwrap.dedent('''
    import json, os, sys, time
    for line in sys.stdin:
        req = json.loads(line)
        folder = req["argv"][1]
        name = os.path.basename(folder)
        print(json.dumps({"kind": "progress", "stage": "asr", "note": json.dumps(req)}), flush=True)
        if name == "crash":
            os._exit(3)
        if name == "slow":
            time.sleep(30)
        print("обычная строка пайплайна", flush=True)
        print(json.dumps({"kind": "job.result", "path": str(os.getpid())}), flush=True)
        print(json.dumps({"kind": "worker.exit", "code": 0}), flush=True)
''')


@pytest.fixture
def host(monkeypatch, tmp_path):
    script = tmp_path / "fake_host.py"
    script.write_text(FAKE_HOST, encoding="utf-8")
    monkeypatch.setattr(jobs, "host_argv", lambda: [sys.executable, str(script)])
    monkeypatch.setattr(jobs, "HOST_IDLE_S", 0.3)
    monkeypatch.setattr(jobs, "_sweep_after", lambda: None)
    queue = jobs.JobQueue(events.EventBus(), host=True)
    yield queue
    queue.stop()


def _folder(tmp_path, name):
    path = tmp_path / name
    path.mkdir(exist_ok=True)
    return str(path)


def _run(queue, kind, folder):
    job = queue.submit(kind, folder)
    _wait(lambda: queue.get(job.id).finished_at is not None)
    return queue.get(job.id)


def test_consecutive_jobs_share_one_process_and_it_closes_when_idle(host, tmp_path):
    first = host.submit(jobs.TRANSCRIBE, _folder(tmp_path, "a"))
    second = host.submit(jobs.REDIARIZE, _folder(tmp_path, "b"))
    _wait(lambda: host.get(second.id).finished_at is not None)
    a, b = host.get(first.id), host.get(second.id)
    assert a.state == b.state == jobs.DONE
    assert a.result == b.result  # pid процесса задач
    process = host._host
    _wait(lambda: host._host is None)  # простой — процесс закрыт
    assert process.poll() is not None


def test_crashed_process_fails_the_job_and_the_next_one_gets_a_new_process(host, tmp_path):
    first = _run(host, jobs.TRANSCRIBE, _folder(tmp_path, "a"))
    crashed = _run(host, jobs.TRANSCRIBE, _folder(tmp_path, "crash"))
    after = _run(host, jobs.TRANSCRIBE, _folder(tmp_path, "b"))
    assert crashed.state == jobs.FAILED
    assert after.state == jobs.DONE and after.result != first.result


def test_cancel_kills_the_process_and_the_queue_goes_on(host, tmp_path):
    slow = host.submit(jobs.TRANSCRIBE, _folder(tmp_path, "slow"))
    _wait(lambda: host.get(slow.id).stage == "asr")
    assert host.cancel(slow.id)
    _wait(lambda: host.get(slow.id).finished_at is not None)
    after = _run(host, jobs.TRANSCRIBE, _folder(tmp_path, "b"))
    assert host.get(slow.id).state == jobs.CANCELLED and after.state == jobs.DONE


def test_request_carries_job_arguments_cwd_and_proxy(host, tmp_path):
    from meet import settings

    settings.patch({"llm": {"proxy": "http://10.1.1.1:3128"}})
    folder = _folder(tmp_path, "a")
    job = host.submit(jobs.TRANSCRIBE, folder, {"speakers": 3})
    _wait(lambda: host.get(job.id).finished_at is not None)
    req = json.loads(host.get(job.id).note)
    assert req["argv"] == jobs.worker_argv(host.get(job.id))[3:]
    assert req["cwd"] == folder
    assert {k.upper(): v for k, v in req["env"].items()}.get("HTTPS_PROXY") == "http://10.1.1.1:3128"
    assert "PATH" not in req["env"]  # только прокси, окружение процесса — своё


def test_other_kinds_still_get_their_own_process(monkeypatch, tmp_path):
    """Установка движка меняет сам движок, загрузки и задачи модели torch не
    грузят — им отдельный процесс, как раньше."""
    started = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            started.append(argv)
            self.stdout = iter(())

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(jobs, "_sweep_after", lambda: None)
    queue = jobs.JobQueue(host=True)
    for kind in (jobs.INSTALL_ENGINE, jobs.SUMMARY, jobs.DOWNLOAD_MODEL):
        queue._spawn_subprocess(jobs.Job(id=kind, kind=kind, folder=str(tmp_path)), lambda line: None)
    assert all("--serve" not in argv for argv in started) and len(started) == 3
    assert set(jobs.HOST_KINDS) == {jobs.TRANSCRIBE, jobs.IMPORT, jobs.MERGE, jobs.REDIARIZE, jobs.SPEAKER_SPLIT}


def test_resident_main_queue_uses_the_host_and_model_queue_does_not():
    from meet import tray_control

    src = open(tray_control.__file__, encoding="utf-8").read()
    assert "jobs.JobQueue(self.bus, host=True)" in src
