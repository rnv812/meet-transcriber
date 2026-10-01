import threading
import time
from collections import deque
from pathlib import Path

# Ниже этого RMS окно считаем (почти) тишиной и НЕ замораживаем по нему гейн
# far-end: иначе тихое стартовое окно навсегда зафиксировало бы gain=1.0.
CALIBRATION_MIN_RMS = 1e-3

# Частота, к которой resample_to_16k приводит аудио — в ней считаем сэмпл-офсеты.
WINDOW_RATE = 16000


def fmt_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_live_line(start_s: float, speaker: str, text: str) -> str:
    return f"[{fmt_hms(start_s)}] {speaker}: {text}"


class _Silence(bytes):
    """Чанк доливки тишины (а не звук устройства) в TrackBuffer."""


class TrackBuffer:
    """Очередь аудио-чанков: callback докидывает push(), рабочий поток забирает
    drain(). Потокобезопасность — за счёт атомарности append/popleft у deque в
    CPython; явных локов нет, чтобы не блокировать аудио-callback.

    push_silence() — доливка тишины по стенным часам: окно видит паузу там,
    где она была (таймкоды ленты не уезжают), а окно из одной доливки
    расшифровывать незачем — drain_window() это сообщает."""

    def __init__(self) -> None:
        self._chunks: "deque[bytes]" = deque()

    def push(self, data: bytes) -> None:
        self._chunks.append(data)

    def push_silence(self, data: bytes) -> None:
        self._chunks.append(_Silence(data))

    def drain(self) -> bytes:
        return self.drain_window()[0]

    def drain_window(self) -> tuple[bytes, bool]:
        """(байты окна, окно — сплошь доливка тишины)."""
        out: list[bytes] = []
        try:
            while True:
                out.append(self._chunks.popleft())
        except IndexError:
            pass
        silent = bool(out) and all(isinstance(c, _Silence) for c in out)
        return b"".join(out), silent


# Такт тикера доливки живого режима: файл отстаёт от часов не больше чем на
# PAD_GAP_S + такт, и callback'у остаётся доливать не больше этого.
PAD_TICK_S = 0.5


class WallClockWriter:
    """Обёртка писателя дорожки: файл держится у стенных часов.

    WASAPI-loopback без системного звука не зовёт callback вовсе, микрофон
    тоже может замолчать. Без доливки тишины sys.opus оставался пустым (офлайн-
    расшифровка записи падала на ffmpeg), а после паузы реплики уезжали к
    началу дорожки — interleave sys/mic по абсолютным таймкодам врал. Тот же
    приём, что у `recorder._Track.tick_pad()`: паузу доливает тикер движка
    (`tick()` раз в PAD_TICK_S, кусками ≤1 с), а не аудио-callback — тот
    доливает перед возобновившимися данными только остаток не больше
    MAX_INLINE_PAD_S (минуты нулей в пайп ffmpeg из callback'а PortAudio
    рвали бы звук). close() дописывает лишь последний миг.

    `buffer` (TrackBuffer окна расшифровки) получает тот же поток: звук и
    доливку, в том же порядке. Точность после паузы — до PAD_GAP_S, как у
    записи.
    """

    PAD_GAP_S = 1.0  # пауза короче — латентность/джиттер callback'а, не тишина
    TAIL_GAP_S = 0.05
    MAX_INLINE_PAD_S = 2.0  # больше callback не доливает (тикер не успел)

    def __init__(self, writer, channels: int, rate: int, clock=time.monotonic,
                 buffer: "TrackBuffer | None" = None) -> None:
        self._writer = writer
        self._buffer = buffer
        self._frame = 2 * max(1, int(channels))
        self._rate = int(rate)
        self._clock = clock
        self._started = clock()
        self._written = 0
        self._closed = False
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._lock:
            self._pad(len(data) / (self._frame * self._rate), self.PAD_GAP_S,
                      limit_s=self.MAX_INLINE_PAD_S)
            self._writer.write(data)
            self._written += len(data)
            if self._buffer is not None:
                self._buffer.push(data)

    def tick(self) -> None:
        """Долить паузу до часов (поток тикера). Кусками ≤1 с, лок — на кусок:
        возобновившийся callback не ждёт всю доливку целиком."""
        with self._lock:
            if self._closed or self._gap(0.0) <= self.PAD_GAP_S:
                return
        while True:
            with self._lock:
                if self._closed or not self._pad(0.0, self.TAIL_GAP_S, limit_s=1.0):
                    return

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._pad(0.0, self.TAIL_GAP_S)
            finally:
                self._writer.close()

    def _gap(self, incoming_s: float) -> float:
        return (self._clock() - self._started) - incoming_s \
            - self._written / (self._frame * self._rate)

    def _pad(self, incoming_s: float, min_gap: float,
             limit_s: float | None = None) -> bool:
        """Тишина до момента, с которого начинаются `incoming_s` секунд данных
        (не больше `limit_s`). Кусками ≤1 с: ffmpeg читает из пайпа. True —
        что-то дописано."""
        gap = self._gap(incoming_s)
        if gap <= min_gap:
            return False
        if limit_s is not None:
            gap = min(gap, limit_s)
        frames = int(round(gap * self._rate))
        while frames > 0:
            n = min(frames, self._rate)
            chunk = b"\x00" * (n * self._frame)
            self._writer.write(chunk)
            self._written += len(chunk)
            if self._buffer is not None:
                self._buffer.push_silence(chunk)
            frames -= n
        return True


class LiveEngine:
    """Движок живого режима: callback пишет дорожки и копит аудио в буферы,
    рабочий поток окнами расшифровывает и дописывает live_transcript.md.

    Живой режим — тоже запись: start() берёт общий `.recording.lock` в папке
    записей (`out_root`, по умолчанию — родитель `out_dir`), поэтому
    резидентная запись и второй живой режим получают «Запись уже идёт».
    stop() снимает lock в finally."""

    SPEAKERS = {"sys.wav": "Собеседник", "mic.wav": "Вы"}

    def __init__(self, out_dir, transcriber, window_seconds: float = 20.0,
                 hotwords: str | None = None, clock=None,
                 on_line=None, voice_matcher=None, speaker_name=None,
                 on_entry=None, out_root=None) -> None:
        # Имя владельца микрофона — из настроек, как и в офлайн-проходе, чтобы
        # живая лента и точный транскрипт называли человека одинаково.
        if speaker_name is None:
            from meet import settings

            speaker_name = settings.load().recording.speaker_name
        self.SPEAKERS = {**LiveEngine.SPEAKERS, "mic.wav": speaker_name}
        self.out_dir = Path(out_dir)
        self._transcriber = transcriber
        self.window_seconds = window_seconds
        self.hotwords = hotwords
        self._clock = clock or time.monotonic
        self.on_line = on_line  # колбэк на каждую записанную строку (Q&A-сервис)
        # Колбэк (line, {"t", "speaker", "text"}): та же строка и её структура.
        self.on_entry = on_entry
        self.out_root = Path(out_root) if out_root is not None else self.out_dir.parent
        self._lock_path: Path | None = None  # наш .recording.lock, пока держим
        self._matcher = voice_matcher  # опознание голоса far-end (duck-typed)
        self._window_lock = threading.Lock()  # process_window зовут и внеочередно
        # Начало координат ленты: старт захвата (start()), без него — первый
        # тик (тесты, которые кормят буферы руками).
        self._t0 = None
        self._tick_origin = None  # clock at previous tick → this window's start offset
        self._pad_thread: "threading.Thread | None" = None
        self._tracks: dict[str, dict] = {}
        self._transcript = self.out_dir / "live_transcript.md"
        self._out = None
        self._stop = threading.Event()
        self._worker: "threading.Thread | None" = None
        self._streams: list = []
        self._p = None

    def register_track(self, fname: str, rate: int, channels: int,
                       normalize: bool = False, identify: bool = False) -> None:
        self._tracks[fname] = {
            "buffer": TrackBuffer(),
            "rate": rate,
            "channels": channels,
            "normalize": normalize,
            "identify": identify,  # опознавать говорящего по голосу (far-end)
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
                raw, silent = tr["buffer"].drain_window()
                if not raw or silent:
                    continue  # окно из одной доливки тишины: речи там нет
                mono = pcm16_to_float32_mono(raw, tr["channels"])
                audio = resample_to_16k(mono, tr["rate"])
                if tr["normalize"]:
                    # Гейн far-end калибруем один раз и фиксируем. Сознательное
                    # упрощение спекового «измерить по первым ~30 с»: калибруемся по
                    # первому окну с реальной энергией far-end (тихое стартовое окно
                    # дало бы gain=1.0 на всю встречу). Точная EBU R128-нормализация
                    # всё равно делается в офлайн-проходе.
                    # Мерим по звуку, без доливки тишины: нули паузы занизили
                    # бы RMS и завысили гейн на всю встречу.
                    voiced = audio[audio != 0]
                    rms = float(np.sqrt(np.mean(np.square(voiced)))) if len(voiced) else 0.0
                    if tr["gain"] is None and rms >= CALIBRATION_MIN_RMS:
                        tr["gain"] = compute_gain(voiced)
                    # На текущее окно применяем зафиксированный гейн, иначе разовый
                    # для этого окна (на тихом окне даст ~1.0 — ничего не ломает).
                    audio = apply_gain(
                        audio, tr["gain"] if tr["gain"] is not None else compute_gain(voiced)
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
                default_speaker = self.SPEAKERS.get(fname, fname)
                for s in segs:
                    speaker = default_speaker
                    if tr["identify"] and self._matcher is not None:
                        name = self._segment_name(audio, s.start - offset, s.end - offset)
                        if name:
                            speaker = name
                    line = format_live_line(s.start, speaker, s.text)
                    self._write_line(line)
                    self._notify(line, {"t": round(s.start, 2),
                                        "speaker": speaker, "text": s.text})

            self._tick_origin = now  # этот тик станет origin для следующего окна

    def _notify(self, line: str, entry: dict) -> None:
        for callback, args in ((self.on_line, (line,)),
                               (self.on_entry, (line, entry))):
            if callback is None:
                continue
            try:
                callback(*args)
            except Exception:
                pass  # потребитель не должен валить запись

    def _segment_name(self, audio, start_s: float, end_s: float):
        """Имя по голосу сегмента; любой сбой -> None (окно важнее имени)."""
        try:
            lo = max(0, int(start_s * WINDOW_RATE))
            hi = min(len(audio), int(end_s * WINDOW_RATE))
            return self._matcher.name_for(audio[lo:hi])
        except Exception:
            return None

    def _write_line(self, line: str) -> None:
        if self._out is None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self._out = open(self._transcript, "a", encoding="utf-8")
        self._out.write(line + "\n")
        self._out.flush()

    def start(self) -> None:
        from meet.recorder import _acquire_lock

        # Lock — первым делом: при идущей записи отказ мгновенный, без
        # минуты загрузки моделей и без пустой папки встречи.
        self.out_root.mkdir(parents=True, exist_ok=True)
        self._lock_path = _acquire_lock(self.out_root, self.out_dir)
        try:
            self._start_capture()
        except BaseException:
            # Частичный старт: закрыть то, что успело открыться, и отдать lock.
            self._close_capture()
            self._release_lock()
            raise

    def _start_capture(self) -> None:
        import pyaudiowpatch as pyaudio

        from meet.recorder import OpusWriter, _find_loopback

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._transcriber.load()
        if self._matcher is not None:
            self._matcher.load()
        self._p = pyaudio.PyAudio()
        wasapi = self._p.get_host_api_info_by_type(pyaudio.paWASAPI)
        devices = (
            (_find_loopback(self._p), "sys.wav", True, True),
            (self._p.get_device_info_by_index(wasapi["defaultInputDevice"]), "mic.wav", False, False),
        )
        # Начало координат ленты — старт захвата: окно N покрывает
        # [tick_{N-1}, tick_N] от него, как и дорожки на диске.
        self._t0 = self._tick_origin = self._clock()
        for dev, fname, normalize, identify in devices:
            channels = max(1, int(dev["maxInputChannels"]))
            rate = int(dev["defaultSampleRate"])
            self.register_track(fname, rate, channels, normalize=normalize,
                                identify=identify)
            # fname — внутренний ключ дорожки (завязан на SPEAKERS); на диск для
            # офлайн-прохода пишем сжатый .opus. Буфер окна получает тот же
            # поток, что и файл, — с доливкой пауз.
            writer = WallClockWriter(
                OpusWriter(self.out_dir / fname.replace(".wav", ".opus"),
                           channels, rate),
                channels, rate, buffer=self._tracks[fname]["buffer"])

            def make_cb(w: WallClockWriter):
                def cb(in_data, frame_count, time_info, status):
                    w.write(in_data)
                    return (None, pyaudio.paContinue)

                return cb

            stream = self._p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(dev["index"]),
                frames_per_buffer=1024,
                stream_callback=make_cb(writer),
            )
            self._streams.append((stream, writer))
            print(f"  {fname}: {dev['name']} ({rate} Hz, {channels} ch)")

        self._pad_thread = threading.Thread(target=self._pad_loop,
                                            name="meet-live-pad", daemon=True)
        self._pad_thread.start()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()
        print(f"Живой режим идёт. Транскрипт: {self._transcript}")

    def _run(self) -> None:
        while not self._stop.wait(self.window_seconds):
            try:
                self.process_window()
            except Exception as e:  # окно не должно валить весь режим
                self._write_line(f"<!-- ошибка окна: {e} -->")

    def _pad_loop(self) -> None:
        """Тикер доливки: молчащие дорожки растут по часам кусками, а не
        одним залпом из callback'а или при остановке."""
        while not self._stop.wait(PAD_TICK_S):
            for _, writer in list(self._streams):
                try:
                    writer.tick()
                except Exception:
                    pass  # сбой доливки не валит запись; хвост дольёт close()

    def _close_capture(self) -> Exception | None:
        """Закрыть стримы, дорожки и PyAudio; безопасно при частичном старте
        и повторном вызове. Сбой одной дорожки не мешает закрыть остальные —
        первая ошибка возвращается вызывающему."""
        first: Exception | None = None
        streams, self._streams = self._streams, []
        closers = [c for stream, writer in streams
                   for c in (stream.stop_stream, stream.close, writer.close)]
        p, self._p = self._p, None
        if p is not None:
            closers.append(p.terminate)
        for close in closers:
            try:
                close()
            except Exception as e:
                first = first or e
        return first

    def _release_lock(self) -> None:
        lock, self._lock_path = self._lock_path, None
        if lock is not None:
            lock.unlink(missing_ok=True)

    def stop(self) -> Path:
        """Остановить и дописать хвост. Безопасен после неудачного start()
        и повторно; чужой lock (start отказал) не трогает."""
        try:
            self._stop.set()
            if self._worker is not None:
                self._worker.join(timeout=self.window_seconds + 30)
            if self._pad_thread is not None:
                self._pad_thread.join(timeout=10)
            try:
                self.process_window()  # финальный слив остатка буфера
            except Exception:
                pass
            close_error = self._close_capture()
            if self._out is not None:
                self._out.close()
                self._out = None
            if self._transcriber is not None:
                self._transcriber.unload()
            if close_error is not None:
                raise close_error  # всё закрыто; сбой дорожки не прячем
        finally:
            self._release_lock()
        return self.out_dir


def run_live(out_root: str, window_seconds: float = 20.0,
             hotwords: str | None = None, no_voices: bool = False) -> Path:
    from datetime import datetime

    from meet.asr import Transcriber
    from meet.transcribe import _load_hotwords
    from meet.voice_id import VoiceMatcher

    out_dir = Path(out_root) / datetime.now().strftime("%Y-%m-%d_%H-%M")
    engine = LiveEngine(
        out_dir,
        Transcriber(),
        window_seconds=window_seconds,
        hotwords=_load_hotwords(hotwords),
        voice_matcher=None if no_voices else VoiceMatcher(),
    )
    try:
        engine.start()
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
    print(f"\nОстановлено: {out_dir}")
    print(f'Точный транскрипт: meet transcribe "{out_dir}"')
    return out_dir
