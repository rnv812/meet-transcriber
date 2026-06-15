import shutil
import subprocess
from pathlib import Path


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg не найден. Установите: winget install Gyan.FFmpeg "
            "и перезапустите терминал."
        )


def to_wav16k(src: Path, dst: Path, normalize: bool = False) -> Path:
    """Любой аудио/видеофайл → mono 16 kHz wav (вход для ASR и диаризации).

    normalize=True добавляет loudness-нормализацию (EBU R128, loudnorm) — для
    тихой дальней дорожки (звук собеседников из звонка), чтобы поднять её до
    уровня, на котором ASR работает стабильнее. Микрофон не нормализуем.
    """
    ensure_ffmpeg()
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000"]
    if normalize:
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd.append(str(dst))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg не смог обработать {src}:\n{proc.stderr[-500:]}")
    return dst
