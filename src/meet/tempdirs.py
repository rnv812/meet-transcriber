"""Временные файлы процессов meet: звук встречи и ответы модели в %TEMP% не
остаются, даже если процесс убили.

Задачу расшифровки снимают отменой, выходом резидента или обновлением — убийством
дерева процессов, и ни `finally`, ни `TemporaryDirectory` не успевают. Поэтому у
каждой временной папки в имени — pid процесса (`meet-job-<pid>-…`), а резидент
удаляет папки умерших процессов: при запуске, перед живым режимом и сразу после
конца каждой задачи и ассистента (`sweep_temp`). Чужие процессы не мешают: пока
pid жив, его папку не трогают.

Подпроцесс задачи и ассистент живого режима берут **свой корень** временных
файлов (`own_root`): `tempfile.tempdir` и TMP/TEMP указывают внутрь
`meet-job-<pid>-…`, так что туда же пишут и их дети (ffmpeg, Claude Code, Codex),
и всё это уходит одной папкой.

То, что делят процессы между собой (замки meta.json, рабочая папка Claude для
живого режима), лежит в общей временной папке системы — `system_temp()`: в
своём корне процесс задачи её бы не нашёл.
"""

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

TEMP_PREFIX = "meet-job-"
# Общая временная папка, какой она была до своего корня (см. own_root).
SYSTEM_TEMP_ENV = "MEET_SYSTEM_TEMP"
_TEMP_VARS = ("TMP", "TEMP", "TMPDIR")


def system_temp() -> Path:
    """Временная папка системы — общая для резидента, задач и ассистента."""
    return Path(os.environ.get(SYSTEM_TEMP_ENV) or tempfile.gettempdir())


def prefix(tag: str = "") -> str:
    """Префикс временной папки этого процесса: её подберёт sweep, когда он умрёт."""
    return f"{TEMP_PREFIX}{os.getpid()}-{tag}"


@contextmanager
def temp_dir(tag: str = ""):
    with tempfile.TemporaryDirectory(prefix=prefix(tag), ignore_cleanup_errors=True) as td:
        yield td


@contextmanager
def own_root():
    """Свой корень временных файлов процесса (и его детей) на время блока.

    По выходе папка удаляется, а `tempfile.tempdir` и TMP/TEMP возвращаются как
    были. Процесс убили — папку удалит резидент (sweep по pid)."""
    shared = str(system_temp())
    root = tempfile.mkdtemp(prefix=prefix("root-"), dir=shared)
    saved_dir = tempfile.tempdir
    saved_env = {name: os.environ.get(name) for name in (*_TEMP_VARS, SYSTEM_TEMP_ENV)}
    tempfile.tempdir = root
    for name in _TEMP_VARS:
        os.environ[name] = root
    os.environ[SYSTEM_TEMP_ENV] = shared
    try:
        yield Path(root)
    finally:
        tempfile.tempdir = saved_dir
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(root, ignore_errors=True)


def _pid_of(name: str) -> int | None:
    try:
        return int(name[len(TEMP_PREFIX):].split("-", 1)[0])
    except ValueError:
        return None


def sweep_temp(root: Path | None = None, alive=None) -> list[str]:
    """Удалить временные папки умерших процессов meet. → имена удалённых."""
    if alive is None:
        from meet.gpu_lock import _pid_alive as alive
    root = Path(root or system_temp())
    removed = []
    for d in root.glob(f"{TEMP_PREFIX}*"):
        pid = _pid_of(d.name)
        if pid is None or not d.is_dir() or pid == os.getpid():
            continue
        try:
            if alive(pid):
                continue
        except Exception:
            continue
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            removed.append(d.name)
    return removed
