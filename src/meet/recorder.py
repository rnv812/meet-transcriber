import struct
from pathlib import Path


class WavWriter:
    """WAV с заранее записанным заголовком: файл остаётся читаемым для ffmpeg,
    даже если процесс упал до close() (размеры-заглушки 0xFFFFFFFF)."""

    def __init__(self, path: Path, channels: int, rate: int) -> None:
        self._f = open(path, "wb")
        self._data_bytes = 0
        block = channels * 2  # int16
        self._f.write(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE")
        self._f.write(
            b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, 16)
        )
        self._f.write(b"data" + struct.pack("<I", 0xFFFFFFFF))

    def write(self, data: bytes) -> None:
        self._f.write(data)
        self._data_bytes += len(data)

    def close(self) -> None:
        self._f.seek(4)
        self._f.write(struct.pack("<I", 36 + self._data_bytes))
        self._f.seek(40)
        self._f.write(struct.pack("<I", self._data_bytes))
        self._f.close()
