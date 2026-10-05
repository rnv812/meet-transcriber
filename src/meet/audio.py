import shutil
import subprocess
import sys
from pathlib import Path


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        install = ("winget install Gyan.FFmpeg" if sys.platform == "win32"
                   else "brew install ffmpeg")
        raise SystemExit(
            f"ffmpeg не найден. Установите: {install} "
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


def pcm16_to_float32_mono(data: bytes, channels: int) -> "np.ndarray":
    """Сырые int16 PCM байты (interleaved) → mono float32 в диапазоне [-1, 1]."""
    import numpy as np

    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float32)


def resample_to_16k(audio: "np.ndarray", src_rate: int) -> "np.ndarray":
    """Передискретизация mono float32 в 16 кГц (вход Whisper). В памяти, без ffmpeg."""
    import numpy as np
    from scipy.signal import resample

    if src_rate == 16000:
        return audio.astype(np.float32)
    n = int(round(len(audio) * 16000 / src_rate))
    return resample(audio, n).astype(np.float32)


def compute_gain(audio: "np.ndarray", target_rms: float = 0.1) -> float:
    """Множитель, поднимающий RMS аудио к target_rms (грубая нормализация far-end,
    замена офлайновому EBU R128 на время живого режима). Тишина → 1.0."""
    import numpy as np

    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    if rms < 1e-6:
        return 1.0
    return target_rms / rms


def apply_gain(audio: "np.ndarray", gain: float) -> "np.ndarray":
    """Применить множитель с защитой от клиппинга."""
    import numpy as np

    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)
