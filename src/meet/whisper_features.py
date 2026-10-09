"""Спектр Whisper блоками (0.5.1).

faster-whisper строит log-mel спектр всей записи разом: stft всей записи
комплексными числами двойной точности (на 88 минутах — 1,55 ГБ одним куском,
плюс квадраты модулей и мел той же длины). На занятой машине распознавание
длинной встречи падало с MemoryError ещё до первого слова.

Здесь — та же формула (FeatureExtractor.__call__ faster-whisper 1.2: поля
n_fft/2 отражением, окно Ханна, rfft, complex64, последний кадр отброшен,
ограничение по максимуму всей записи), но кадры считаются блоками по
`BLOCK_FRAMES` в заранее выделенный результат, а ограничение по максимуму —
в конце, на месте. Результат тот же; лишняя память — только на блок.
`install(model)` подменяет `model.feature_extractor` обёрткой.
"""

from __future__ import annotations

import numpy as np

# 30 с звука на блок при шаге 10 мс.
BLOCK_FRAMES = 3000


def log_mel(extractor, waveform: np.ndarray, padding: int = 160, chunk_length=None,
            *, block_frames: int = BLOCK_FRAMES) -> np.ndarray:
    """Log-mel спектр `waveform` — как `extractor(waveform, padding, chunk_length)`."""
    if chunk_length is not None:
        extractor.n_samples = chunk_length * extractor.sampling_rate
        extractor.nb_max_frames = extractor.n_samples // extractor.hop_length
    n_fft, hop = extractor.n_fft, extractor.hop_length
    half = n_fft // 2
    # Звук с `padding` нулями в конце и полями `half` отражением с краёв — не
    # копией всей записи: середину блоки читают из `wave` как есть, а поля нужны
    # только первым и последним кадрам (`_piece`).
    wave = waveform if waveform.dtype == np.float32 else waveform.astype(np.float32)
    length = len(wave) + (padding or 0)
    n_frames = 1 + (length + 2 * half - n_fft) // hop
    keep = n_frames - 1  # как stft[..., :-1]
    window = np.hanning(n_fft + 1)[:-1].astype("float32")
    filters = extractor.mel_filters
    out = None
    for f0 in range(0, max(keep, 0), block_frames):
        f1 = min(keep, f0 + block_frames)
        piece = _piece(wave, length, f0 * hop - half, (f1 - 1) * hop + n_fft - half)
        frames = np.lib.stride_tricks.as_strided(
            piece, (f1 - f0, n_fft), (hop * piece.strides[0], piece.strides[0]))
        spec = np.fft.rfft(frames * window, n=n_fft, axis=-1).T.astype("complex64")
        log = np.log10(np.clip(filters @ (np.abs(spec) ** 2), a_min=1e-10, a_max=None))
        if out is None:
            out = np.empty((log.shape[0], keep), dtype=log.dtype)
        out[:, f0:f1] = log
    if out is None:
        out = np.empty((filters.shape[0], 0), dtype=np.float32)
        return out
    np.maximum(out, out.max() - 8.0, out=out)
    out += 4.0
    out /= 4.0
    return out


def _piece(wave: np.ndarray, length: int, start: int, end: int) -> np.ndarray:
    """Отсчёты [start, end) звука `wave`, дополненного нулями до `length`, с
    полями отражением (как np.pad mode="reflect"): внутри — срез без копии."""
    if start >= 0 and end <= len(wave):
        return wave[start:end]
    idx = np.arange(start, end)
    idx = np.where(idx < 0, -idx, idx)
    idx = np.where(idx >= length, 2 * (length - 1) - idx, idx)
    piece = np.zeros(len(idx), dtype=np.float32)
    inside = idx < len(wave)
    piece[inside] = wave[idx[inside]]
    return piece


class BlockExtractor:
    """Обёртка над FeatureExtractor faster-whisper: вызов — `log_mel`, остальное —
    от исходного (размеры, частота, мел-фильтры)."""

    def __init__(self, base) -> None:
        self.base = base

    def __call__(self, waveform, padding=160, chunk_length=None):
        return log_mel(self.base, waveform, padding, chunk_length)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def __setattr__(self, name, value):
        if name == "base":
            object.__setattr__(self, name, value)
        else:
            setattr(self.base, name, value)


def install(model) -> bool:
    """Подменить спектр модели (WhisperModel) блочным. Незнакомый извлекатель
    (другая версия faster-whisper) — не трогаем: False."""
    current = getattr(model, "feature_extractor", None)
    if isinstance(current, BlockExtractor):
        return True
    needed = ("n_fft", "hop_length", "sampling_rate", "mel_filters")
    if current is None or not all(hasattr(current, name) for name in needed):
        return False
    model.feature_extractor = BlockExtractor(current)
    return True
