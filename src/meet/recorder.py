import struct
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pyaudiowpatch as pyaudio

# Битрейт Opus на дорожку: для речи 16 кГц моно 24 кбит/с на слух прозрачно,
# а место — ~40x меньше несжатого стерео 48 кГц wav.
OPUS_BITRATE = "24k"


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


class OpusWriter:
    """Пишет сырой int16 PCM-поток в Ogg/Opus через ffmpeg-подпроцесс: callback
    докидывает write(bytes), а ffmpeg на лету даунмиксит в моно, ресемплит в
    16 кГц (всё, что нужно транскрибатору) и кодирует в Opus.

    Устойчивость к обрыву: Ogg постраничный, поэтому при жёстком убийстве процесса
    файл читается до последней целой страницы (потеря ≤ доли секунды). При штатном
    close() закрываем stdin → ffmpeg дописывает и финализирует контейнер сам.
    Тот же интерфейс (write/close), что у WavWriter, — callback'и не меняются."""

    def __init__(self, path: Path, channels: int, rate: int,
                 bitrate: str = OPUS_BITRATE) -> None:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-i", "pipe:0",
            "-ac", "1", "-ar", "16000",
            "-c:a", "libopus", "-b:a", bitrate, "-application", "voip",
            # Регулярно сбрасывать Ogg-страницы на диск (page_duration в мкс) и не
            # копить их в буфере AVIO: если ffmpeg убьют на лету, на диске окажется
            # почти всё, а не пусто. Потеря при обрыве ≤ ~0.5 с (одна страница).
            "-page_duration", "500000", "-flush_packets", "1",
            str(path),
        ]
        # stderr наследуется (loglevel error → тихо, пока всё хорошо; при ошибке
        # ffmpeg сообщение видно в логе записи, а не глотается).
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stdout=subprocess.DEVNULL)

    def write(self, data: bytes) -> None:
        try:
            self._proc.stdin.write(data)
        except (BrokenPipeError, OSError):
            pass  # ffmpeg упал — не роняем аудио-callback; вотчдог заметит стоп

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.terminate()


def _stopped_tracks(streams, reported: set) -> list:
    """Дорожки, чей PortAudio-стрим перестал быть активным (устройство сменилось/
    усыплено — типичная причина тихого обрыва записи) и о которых ещё не сообщали.
    Помечает их в reported, чтобы вотчдог не спамил каждый такт."""
    newly = []
    for fname, stream in streams:
        if not stream.is_active() and fname not in reported:
            reported.add(fname)
            newly.append(fname)
    return newly


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
        (_find_loopback(p), "sys.opus"),
        (p.get_device_info_by_index(wasapi["defaultInputDevice"]), "mic.opus"),
    )

    streams = []  # (fname, stream, writer)
    try:
        for dev, fname in devices:
            channels = max(1, int(dev["maxInputChannels"]))
            rate = int(dev["defaultSampleRate"])
            writer = OpusWriter(out_dir / fname, channels, rate)

            def make_cb(w: OpusWriter):
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
            streams.append((fname, stream, writer))
            print(f"  {fname}: {dev['name']} ({rate} Hz, {channels} ch)")

        print(f"Запись идёт... Остановить: Ctrl+C. Папка: {out_dir}")
        health = [(fname, stream) for fname, stream, _ in streams]
        reported: set = set()
        try:
            while True:
                time.sleep(0.5)
                for fname in _stopped_tracks(health, reported):
                    print(
                        f"[{datetime.now():%H:%M:%S}] ВНИМАНИЕ: дорожка {fname} "
                        "остановилась (сменилось/усыплено аудио-устройство?). "
                        "Записанное до этого момента сохранено.",
                        flush=True,  # предупреждение должно всплыть сразу, не в буфере
                    )
        except KeyboardInterrupt:
            pass
    finally:
        for _, stream, writer in streams:
            stream.stop_stream()
            stream.close()
            writer.close()
        p.terminate()

    print(f"\nЗапись остановлена: {out_dir}")
    print(f"Транскрибировать: meet transcribe \"{out_dir}\"")
    return out_dir
