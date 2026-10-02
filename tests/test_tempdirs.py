"""Временные файлы процессов meet (meet.tempdirs): свой корень у задачи и
ассистента, уборка папок убитых процессов — звук встречи в %TEMP% не остаётся.

Подпроцессы — только `python -c` с выдуманными данными; модели нет."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from meet import jobs, tempdirs


@pytest.fixture
def shared(tmp_path, monkeypatch):
    """Временная папка «системы» — своя у теста."""
    root = tmp_path / "temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    for name in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(name, str(root))
    monkeypatch.delenv(tempdirs.SYSTEM_TEMP_ENV, raising=False)
    return root


def test_own_root_redirects_the_process_and_its_children_and_cleans_up(shared):
    with tempdirs.own_root() as root:
        assert root.parent == shared and root.name.startswith(tempdirs.prefix())
        assert Path(tempfile.gettempdir()) == root
        assert os.environ["TEMP"] == os.environ["TMP"] == str(root)
        # Общая папка (замки meta.json, рабочая папка Claude) — прежняя.
        assert tempdirs.system_temp() == shared
        with tempdirs.temp_dir("gigaam-") as td:
            assert Path(td).parent == root
            (Path(td) / "c00000.wav").write_bytes(b"RIFF")
        child = subprocess.run([sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
                               capture_output=True, text=True, check=True)
        assert Path(child.stdout.strip()) == root
        (root / "leftover.wav").write_bytes(b"RIFF")
    assert not root.exists()
    assert Path(tempfile.gettempdir()) == shared and os.environ["TEMP"] == str(shared)
    assert tempdirs.SYSTEM_TEMP_ENV not in os.environ


def test_meta_locks_stay_in_the_shared_temp(shared, tmp_path):
    from meet import library

    with tempdirs.own_root():
        inside = library._meta_lock_path(tmp_path)
    assert inside == library._meta_lock_path(tmp_path)
    assert inside.parent.parent == shared


_KILLED_JOB = r"""
import os, sys, time
from pathlib import Path
from meet import tempdirs
with tempdirs.own_root():
    with tempdirs.temp_dir("gigaam-") as td:
        (Path(td) / "c00000.wav").write_bytes(b"RIFF" * 1000)
        print(td, flush=True)
        time.sleep(60)
"""


def test_a_killed_job_leaves_audio_that_the_sweep_removes(shared):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = subprocess.Popen([sys.executable, "-c", _KILLED_JOB], stdout=subprocess.PIPE, text=True, env=env)
    try:
        chunk_dir = Path(proc.stdout.readline().strip())
        assert (chunk_dir / "c00000.wav").exists()
    finally:
        jobs._kill_tree(proc)  # как отмена задачи
        proc.wait(timeout=10)
        proc.stdout.close()
    leftovers = sorted(d.name for d in shared.glob(f"{tempdirs.TEMP_PREFIX}*"))
    assert leftovers and chunk_dir.exists()  # убийство finally не дочистило
    deadline = time.monotonic() + 10
    while sorted(tempdirs.sweep_temp(shared)) != leftovers and time.monotonic() < deadline:
        time.sleep(0.1)  # Windows отпускает папку убитого процесса не мгновенно
    assert not chunk_dir.exists() and not list(shared.iterdir())


def test_sweep_keeps_folders_of_live_processes(shared):
    mine = shared / f"{tempdirs.TEMP_PREFIX}{os.getpid()}-root-x"
    alive = shared / f"{tempdirs.TEMP_PREFIX}424243-root-z"
    dead = shared / f"{tempdirs.TEMP_PREFIX}424242-root-y"
    for d in (mine, alive, dead):
        d.mkdir()
    assert tempdirs.sweep_temp(shared, alive=lambda pid: pid == 424243) == [dead.name]
    assert mine.exists() and alive.exists()  # свои и живых — никогда


_EXITS_DIRTY = r"""
import os
from pathlib import Path
from meet import tempdirs
d = Path(tempdirs.system_temp()) / tempdirs.prefix("gigaam-x")
d.mkdir()
(d / "c00000.wav").write_bytes(b"RIFF")
os._exit(3)
"""


def test_queue_sweeps_the_temp_of_a_finished_job(shared, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    monkeypatch.setattr(jobs, "worker_argv", lambda job: [sys.executable, "-c", _EXITS_DIRTY])
    queue = jobs.JobQueue()
    try:
        job = queue.submit(jobs.TRANSCRIBE, str(tmp_path))
        deadline = time.monotonic() + 30
        while job.state not in (jobs.DONE, jobs.FAILED) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert job.state == jobs.FAILED
    finally:
        queue.stop()
    assert not list(shared.glob(f"{tempdirs.TEMP_PREFIX}*"))
