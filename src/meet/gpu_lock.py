"""GPU-lock: маркер «meet занял GPU» для внешних приложений.

voice-control по этому файлу выгружает свою
копию Whisper на время тяжёлой GPU-работы транскрайбера и грузит обратно
после. Формат — JSON
{"pid", "reason"}: pid позволяет наблюдателю игнорировать протухший маркер,
если процесс убили без finally.
"""
import json
import os
from contextlib import contextmanager
from pathlib import Path

from meet import paths


def lock_path() -> Path:
    """%LOCALAPPDATA%/meet/gpu.lock — путь известен обоим проектам.

    Идёт через paths.data_dir(), но остаётся тем же файлом: это публичный
    контракт с voice-control, и переносить его нельзя."""
    return paths.data_dir() / "gpu.lock"


@contextmanager
def hold_gpu_lock(reason: str):
    """Держать маркер на время GPU-работы; снять в finally.

    Маркер — best effort: любая проблема с его записью не должна мешать
    самой транскрибации.
    """
    lock: Path | None = lock_path()
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"pid": os.getpid(), "reason": reason}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        lock = None
    try:
        yield
    finally:
        if lock is not None:
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass
