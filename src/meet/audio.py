import shutil
import subprocess
from pathlib import Path


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg не найден. Установите: winget install Gyan.FFmpeg "
            "и перезапустите терминал."
        )


def to_wav16k(src: Path, dst: Path) -> Path:
    """Любой аудио/видеофайл → mono 16 kHz wav (вход для ASR и диаризации)."""
    ensure_ffmpeg()
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg не смог обработать {src}:\n{proc.stderr[-500:]}")
    return dst
