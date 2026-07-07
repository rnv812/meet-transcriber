import threading
import time
from collections import deque
from pathlib import Path

# Ниже этого RMS окно считаем (почти) тишиной и НЕ замораживаем по нему гейн
# far-end: иначе тихое стартовое окно навсегда зафиксировало бы gain=1.0.
CALIBRATION_MIN_RMS = 1e-3


def fmt_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_live_line(start_s: float, speaker: str, text: str) -> str:
    return f"[{fmt_hms(start_s)}] {speaker}: {text}"


class TrackBuffer:
    """Очередь аудио-чанков: callback докидывает push(), рабочий поток забирает
    drain(). Потокобезопасность — за счёт атомарности append/popleft у deque в
    CPython; явных локов нет, чтобы не блокировать аудио-callback."""

    def __init__(self) -> None:
        self._chunks: "deque[bytes]" = deque()

    def push(self, data: bytes) -> None:
        self._chunks.append(data)

    def drain(self) -> bytes:
        out: list[bytes] = []
        try:
            while True:
                out.append(self._chunks.popleft())
        except IndexError:
            pass
        return b"".join(out)


class LiveEngine:
    """Движок живого режима: callback пишет дорожки и копит аудио в буферы,
    рабочий поток окнами расшифровывает и дописывает live_transcript.md."""

    SPEAKERS = {"sys.wav": "Собеседник", "mic.wav": "Вы"}

    def __init__(self, out_dir, transcriber, window_seconds: float = 20.0,
                 hotwords: str | None = None, clock=None,
                 on_line=None) -> None:
        self.out_dir = Path(out_dir)
        self._transcriber = transcriber
        self.window_seconds = window_seconds
        self.hotwords = hotwords
        self._clock = clock or time.monotonic
        self.on_line = on_line  # колбэк на каждую записанную строку (Q&A-сервис)
        self._window_lock = threading.Lock()  # process_window зовут и внеочередно
        self._t0 = None  # wall-clock at first process_window tick
        self._tick_origin = None  # clock at previous tick → this window's start offset
        self._tracks: dict[str, dict] = {}
        self._transcript = self.out_dir / "live_transcript.md"
        self._out = None
        self._stop = threading.Event()
        self._worker: "threading.Thread | None" = None
        self._streams: list = []
        self._p = None

    def register_track(self, fname: str, rate: int, channels: int,
                       normalize: bool = False) -> None:
        self._tracks[fname] = {
            "buffer": TrackBuffer(),
            "rate": rate,
            "channels": channels,
            "normalize": normalize,
            "gain": None,
            "last_text": None,  # хвост прошлого окна → initial_prompt следующего
        }

    def process_window(self) -> None:
        import numpy as np

        from meet.asr import drop_hallucinations
        from meet.audio import (
            apply_gain,
            compute_gain,
            pcm16_to_float32_mono,
            resample_to_16k,
        )

        # Лок на всё тело: Q&A-сервис может звать process_window() внеочередно
        # из другого потока — сериализуем с фоновым тиком, чтобы не путать
        # offset/tick_origin и не писать строки вперемешку.
        with self._window_lock:
            # Окно N покрывает интервал стенных часов [tick_{N-1}, tick_N], поэтому
            # стартовый offset окна — это часы предыдущего тика. Один offset на весь
            # тик → обе дорожки используют общее начало координат и не расходятся,
            # даже если одна дорожка в начале почти молчит (loopback без звука).
            if self._t0 is None:
                self._t0 = self._clock()
                self._tick_origin = self._t0
            offset = self._tick_origin - self._t0
            now = self._clock()

            for fname, tr in self._tracks.items():
                raw = tr["buffer"].drain()
                if not raw:
                    continue
                mono = pcm16_to_float32_mono(raw, tr["channels"])
                audio = resample_to_16k(mono, tr["rate"])
                if tr["normalize"]:
                    # Гейн far-end калибруем один раз и фиксируем. Сознательное
                    # упрощение спекового «измерить по первым ~30 с»: калибруемся по
                    # первому окну с реальной энергией far-end (тихое стартовое окно
                    # дало бы gain=1.0 на всю встречу). Точная EBU R128-нормализация
                    # всё равно делается в офлайн-проходе.
                    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
                    if tr["gain"] is None and rms >= CALIBRATION_MIN_RMS:
                        tr["gain"] = compute_gain(audio)
                    # На текущее окно применяем зафиксированный гейн, иначе разовый
                    # для этого окна (на тихом окне даст ~1.0 — ничего не ломает).
                    audio = apply_gain(
                        audio, tr["gain"] if tr["gain"] is not None else compute_gain(audio)
                    )
                segs = drop_hallucinations(
                    self._transcriber.transcribe_window(
                        audio,
                        offset_s=offset,
                        hotwords=self.hotwords,
                        initial_prompt=tr.get("last_text"),
                    )
                )
                if segs:
                    tr["last_text"] = segs[-1].text  # хвост → контекст следующего окна
                speaker = self.SPEAKERS.get(fname, fname)
                for s in segs:
                    line = format_live_line(s.start, speaker, s.text)
                    self._write_line(line)
                    if self.on_line is not None:
                        try:
                            self.on_line(line)
                        except Exception:
                            pass  # потребитель не должен валить запись

            self._tick_origin = now  # этот тик станет origin для следующего окна

    def _write_line(self, line: str) -> None:
        if self._out is None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self._out = open(self._transcript, "a", encoding="utf-8")
        self._out.write(line + "\n")
        self._out.flush()

    def start(self) -> None:
        import pyaudiowpatch as pyaudio

        from meet.recorder import OpusWriter, _find_loopback

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._transcriber.load()
        self._p = pyaudio.PyAudio()
        wasapi = self._p.get_host_api_info_by_type(pyaudio.paWASAPI)
        devices = (
            (_find_loopback(self._p), "sys.wav", True),
            (self._p.get_device_info_by_index(wasapi["defaultInputDevice"]), "mic.wav", False),
        )
        for dev, fname, normalize in devices:
            channels = max(1, int(dev["maxInputChannels"]))
            rate = int(dev["defaultSampleRate"])
            # fname — внутренний ключ дорожки (завязан на SPEAKERS); на диск для
            # офлайн-прохода пишем сжатый .opus.
            writer = OpusWriter(self.out_dir / fname.replace(".wav", ".opus"),
                                channels, rate)
            self.register_track(fname, rate, channels, normalize=normalize)
            buf = self._tracks[fname]["buffer"]

            def make_cb(w: OpusWriter, b: TrackBuffer):
                def cb(in_data, frame_count, time_info, status):
                    w.write(in_data)
                    b.push(in_data)
                    return (None, pyaudio.paContinue)

                return cb

            stream = self._p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(dev["index"]),
                frames_per_buffer=1024,
                stream_callback=make_cb(writer, buf),
            )
            self._streams.append((stream, writer))
            print(f"  {fname}: {dev['name']} ({rate} Hz, {channels} ch)")

        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        print(f"Живой режим идёт. Транскрипт: {self._transcript}")

    def _run(self) -> None:
        while not self._stop.wait(self.window_seconds):
            try:
                self.process_window()
            except Exception as e:  # окно не должно валить весь режим
                self._write_line(f"<!-- ошибка окна: {e} -->")

    def stop(self) -> Path:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=self.window_seconds + 30)
        try:
            self.process_window()  # финальный слив остатка буфера
        except Exception:
            pass
        for stream, writer in self._streams:
            stream.stop_stream()
            stream.close()
            writer.close()
        if self._p is not None:
            self._p.terminate()
        if self._out is not None:
            self._out.close()
        self._transcriber.unload()
        return self.out_dir


def run_live(out_root: str, window_seconds: float = 20.0,
             hotwords: str | None = None) -> Path:
    from datetime import datetime

    from meet.asr import Transcriber
    from meet.transcribe import _load_hotwords

    out_dir = Path(out_root) / datetime.now().strftime("%Y-%m-%d_%H-%M")
    engine = LiveEngine(
        out_dir,
        Transcriber(),
        window_seconds=window_seconds,
        hotwords=_load_hotwords(hotwords),
    )
    engine.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    engine.stop()
    print(f"\nОстановлено: {out_dir}")
    print(f'Точный транскрипт: meet transcribe "{out_dir}"')
    return out_dir
