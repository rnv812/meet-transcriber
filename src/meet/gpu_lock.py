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
    контракт с внешним наблюдателем, и переносить его по своей воле нельзя.
    Настройка `integrations.gpu_marker_path` меняет путь осознанно — например,
    если наблюдатель ждёт маркер в другом месте."""
    try:
        from meet import settings

        custom = settings.load().integrations.gpu_marker_path
        if custom is not None:
            return custom
    except Exception:  # настройки не должны мешать GPU-работе
        pass
    return paths.data_dir() / "gpu.lock"


def marker_enabled() -> bool:
    """Нужен ли маркер вообще. Кому нечем его читать — выключает, и файла нет."""
    try:
        from meet import settings

        return settings.load().integrations.gpu_marker
    except Exception:
        return True


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с этим pid (Windows). Копия из recorder, но без импорта
    recorder — тот тянет PyAudio, а gpu_lock грузится рано."""
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def held_by_live_process() -> bool:
    """Держит ли маркер живой процесс. Голое `.exists()` врёт: убитый без
    finally процесс (напр. отменённая расшифровка) оставляет файл, и панель
    показывала бы «GPU занят» вечно. По pid — как это делает voice-control."""
    path = lock_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = int(data["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    try:
        return _pid_alive(pid)
    except Exception:
        return True  # проверить нечем — верим наличию файла


@contextmanager
def hold_gpu_lock(reason: str):
    """Держать маркер на время GPU-работы; снять в finally.

    Маркер — best effort: любая проблема с его записью не должна мешать
    самой транскрибации.
    """
    if not marker_enabled():
        yield
        return
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
            # Снимаем маркер, только если он всё ещё наш: иначе осиротевший
            # процесс, доживающий после нас, удалил бы маркер новой работы, и
            # voice-control загрузил бы Whisper в VRAM посреди активного прохода.
            try:
                data = json.loads(lock.read_text(encoding="utf-8"))
                if int(data.get("pid", -1)) == os.getpid():
                    lock.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass
