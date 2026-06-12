import struct
import time
from datetime import datetime
from pathlib import Path

import pyaudiowpatch as pyaudio


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


def _find_loopback(p: "pyaudio.PyAudio") -> dict:
    """Loopback-устройство для текущего устройства вывода (то, что слышно в наушниках)."""
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if speakers.get("isLoopbackDevice"):
        return speakers
    for lb in p.get_loopback_device_info_generator():
        if speakers["name"] in lb["name"]:
            return lb
    raise RuntimeError(f"Не найден loopback для устройства: {speakers['name']}")


def record(out_root: str) -> Path:
    out_dir = Path(out_root) / datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_dir.mkdir(parents=True, exist_ok=True)

    p = pyaudio.PyAudio()
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    devices = (
        (_find_loopback(p), "sys.wav"),
        (p.get_device_info_by_index(wasapi["defaultInputDevice"]), "mic.wav"),
    )

    streams = []
    try:
        for dev, fname in devices:
            channels = max(1, int(dev["maxInputChannels"]))
            rate = int(dev["defaultSampleRate"])
            writer = WavWriter(out_dir / fname, channels, rate)

            def make_cb(w: WavWriter):
                def cb(in_data, frame_count, time_info, status):
                    w.write(in_data)
                    return (None, pyaudio.paContinue)

                return cb

            stream = p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(dev["index"]),
                frames_per_buffer=1024,
                stream_callback=make_cb(writer),
            )
            streams.append((stream, writer))
            print(f"  {fname}: {dev['name']} ({rate} Hz, {channels} ch)")

        print(f"Запись идёт... Остановить: Ctrl+C. Папка: {out_dir}")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    finally:
        for stream, writer in streams:
            stream.stop_stream()
            stream.close()
            writer.close()
        p.terminate()

    print(f"\nЗапись остановлена: {out_dir}")
    print(f"Транскрибировать: meet transcribe \"{out_dir}\"")
    return out_dir
