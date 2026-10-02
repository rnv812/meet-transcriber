import threading
import time
from collections import deque
from pathlib import Path

# Ниже этого RMS окно считаем (почти) тишиной и НЕ замораживаем по нему гейн
# far-end: иначе тихое стартовое окно навсегда зафиксировало бы gain=1.0.
CALIBRATION_MIN_RMS = 1e-3

# Частота, к которой resample_to_16k приводит аудио — в ней считаем сэмпл-офсеты.
WINDOW_RATE = 16000

# Такт рабочего потока: забрать звук из буферов и распознать созревшие окна.
# Окно режется по накопленному звуку, а не «через N секунд после прошлого
# распознавания»: окно на 20 с раньше реально шло ~23 с.
STEP_S = 0.25
# Хвост короче этого при внеочередном сливе (вопрос, остановка) не распознаём.
TAIL_MIN_S = 0.3
# Распознавание безнадёжно отстало (больше этого в очереди) — старый звук
# живой режим пропускает: лента важнее сейчас, полный текст даст офлайн-проход.
BACKLOG_MAX_S = 90.0


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

    def drain_runs(self) -> list[tuple[bytes, bool]]:
        """Всё накопленное — подряд идущими кусками: [(байты, доливка тишины)]."""
        runs: list[tuple[bytes, bool]] = []
        try:
            while True:
                chunk = self._chunks.popleft()
                pad = isinstance(chunk, _Silence)
                if runs and runs[-1][1] == pad:
                    runs[-1] = (runs[-1][0] + chunk, pad)
                else:
                    runs.append((bytes(chunk), pad))
        except IndexError:
            pass
        return runs


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


# «Взять из настроек» — отличается от None («системное устройство»).
_FROM_SETTINGS = object()


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
                 on_entry=None, out_root=None, mic_device=_FROM_SETTINGS,
                 output_device=_FROM_SETTINGS, text_fixes=None, log=print) -> None:
        # Имя владельца микрофона — из настроек, как и в офлайн-проходе, чтобы
        # живая лента и точный транскрипт называли человека одинаково. Оттуда
        # же — выбранные микрофон и устройство вывода (None — системные).
        if speaker_name is None or _FROM_SETTINGS in (mic_device, output_device):
            from meet import settings

            recording = settings.load().recording
            if speaker_name is None:
                speaker_name = recording.speaker_name
            if mic_device is _FROM_SETTINGS:
                mic_device = recording.mic_device
            if output_device is _FROM_SETTINGS:
                output_device = recording.output_device
        self.mic_device = mic_device
        self.output_device = output_device
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
        # Исправления текста реплики (правила замены, латиница после GigaAM).
        self._text_fixes = text_fixes
        self._log = log
        self.stats = {"windows": 0, "audio_s": 0.0, "asr_s": 0.0, "times": [], "skipped_s": 0.0}
        self._last_error: str | None = None
        # Время старта захвата (start()). Таймкоды ленты — позиция в звуке
        # дорожки (`pos` очереди): буфер получает тот же поток, что и файл
        # дорожки, с доливкой пауз, — лента сходится с офлайн-транскриптом.
        self._t0 = None
        self._pad_thread: "threading.Thread | None" = None
        self._tracks: dict[str, dict] = {}
        self._transcript = self.out_dir / "live_transcript.md"
        self._out = None
        self._stop = threading.Event()
        self._worker: "threading.Thread | None" = None
        self._streams: list = []
        self._p = None
        # Выбранное устройство не нашлось — пишем с системного (для резидента:
        # уходит в файл эндпоинта): [{"kind", "name", "device"}].
        self.devices_fallback: list[dict] = []

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
            # Звук, ещё не ушедший в распознавание: mono float32 в частоте
            # дорожки, кусками; `pos` — его начало, секунды от начала дорожки.
            "pending": [],
            "pending_n": 0,
            "pos": 0.0,
        }

    @property
    def policy(self):
        """Окна распознавания: у движка свои (GigaAM — ~5 с), иначе Whisper
        по `window_seconds`."""
        from meet.live_asr import whisper_policy

        return getattr(self._transcriber, "policy", None) or whisper_policy(self.window_seconds)

    def _drain(self) -> None:
        """Забрать звук из буферов дорожек в очередь распознавания."""
        from meet.audio import pcm16_to_float32_mono

        if self._t0 is None:
            self._t0 = self._clock()
        for tr in self._tracks.values():
            for raw, pad in tr["buffer"].drain_runs():
                mono = pcm16_to_float32_mono(raw, tr["channels"])
                if len(mono):
                    tr["pending"].append((mono, pad))
                    tr["pending_n"] += len(mono)

    @staticmethod
    def _audio(tr: dict):
        import numpy as np

        parts = [a for a, _ in tr["pending"]]
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def _take(self, tr: dict, seconds: float | None):
        """Снять с очереди дорожки первые `seconds` секунд (None — всё).
        → (звук, начало в секундах, окно — сплошь доливка тишины)."""
        import numpy as np

        want = tr["pending_n"] if seconds is None else min(
            tr["pending_n"], int(round(seconds * tr["rate"])))
        head: list = []
        pad_only = True
        got = 0
        while tr["pending"] and got < want:
            audio, pad = tr["pending"][0]
            n = min(len(audio), want - got)
            head.append(audio[:n])
            pad_only = pad_only and pad
            if n < len(audio):
                tr["pending"][0] = (audio[n:], pad)
            else:
                tr["pending"].pop(0)
            got += n
        tr["pending_n"] -= got
        start = tr["pos"]
        tr["pos"] += got / tr["rate"]
        window = np.concatenate(head) if len(head) > 1 else (
            head[0] if head else np.zeros(0, dtype=np.float32))
        return window, start, pad_only and got > 0

    def _next_window(self, tr: dict, final: bool):
        """Созревшее окно дорожки: (звук, начало в секундах) или None.

        Набралось больше середины между `min_s` и `max_s` и после `min_s`
        была пауза между словами — окно до неё (не ждём `max_s`); набралось
        `max_s` — резка в паузе или самом тихом месте между `min_s` и
        `max_s`. Отстали (в очереди больше `behind_s`) — окно до `merge_s`.
        `final` — забрать всё, что есть (вопрос, остановка)."""
        from meet.gigaam_asr import find_pause, quiet_cut

        rate = tr["rate"]
        have = tr["pending_n"] / rate
        if final:
            return self._take(tr, None) if have >= TAIL_MIN_S else None
        policy = self.policy
        if have < (policy.min_s + policy.max_s) / 2:
            return None
        if have < policy.max_s:
            cut = find_pause(self._audio(tr), rate, policy.min_s, have)
            return self._take(tr, cut) if cut is not None else None
        if have > BACKLOG_MAX_S:
            skip = have - policy.merge_s
            self._take(tr, skip)
            self.stats["skipped_s"] += skip
            self._log(f"живой режим: распознавание не успевает — пропущено {skip:.0f} с звука")
            have = tr["pending_n"] / rate
        if have >= policy.behind_s:
            hi = min(have, policy.merge_s)
            lo = max(policy.min_s, hi - 5.0)
        else:
            lo, hi = policy.min_s, policy.max_s
        cut = quiet_cut(self._audio(tr)[: int(hi * rate) + 1], rate, lo, hi)
        return self._take(tr, cut)

    def _recognize(self, fname: str, tr: dict, mono, start_s: float, pad_only: bool) -> None:
        import numpy as np

        from meet.asr import drop_hallucinations
        from meet.audio import apply_gain, compute_gain, resample_to_16k

        if not len(mono) or pad_only:
            return  # окно из одной доливки тишины: речи там нет
        audio = resample_to_16k(mono, tr["rate"])
        if tr["normalize"]:
            # Гейн far-end калибруем один раз и фиксируем — по первому окну с
            # реальной энергией (тихое стартовое окно дало бы gain=1.0 на всю
            # встречу). Мерим по звуку, без доливки тишины: нули паузы
            # занизили бы RMS. Точная нормализация — в офлайн-проходе.
            voiced = audio[audio != 0]
            rms = float(np.sqrt(np.mean(np.square(voiced)))) if len(voiced) else 0.0
            if tr["gain"] is None and rms >= CALIBRATION_MIN_RMS:
                tr["gain"] = compute_gain(voiced)
            audio = apply_gain(
                audio, tr["gain"] if tr["gain"] is not None else compute_gain(voiced))
        started = time.perf_counter()
        segs = drop_hallucinations(
            self._transcriber.transcribe_window(
                audio, offset_s=start_s, hotwords=self.hotwords,
                initial_prompt=tr.get("last_text"),
            )
        )
        spent = time.perf_counter() - started
        self.stats["windows"] += 1
        self.stats["audio_s"] += len(audio) / WINDOW_RATE
        self.stats["asr_s"] += spent
        self.stats["times"].append(round(spent, 3))
        if segs and self._text_fixes is not None:
            segs = self._text_fixes(segs, latin=getattr(self._transcriber, "latin_pass", False))
        if segs:
            tr["last_text"] = segs[-1].text  # хвост → контекст следующего окна
        default_speaker = self.SPEAKERS.get(fname, fname)
        for s in segs:
            speaker = default_speaker
            if tr["identify"] and self._matcher is not None:
                name = self._segment_name(audio, s.start - start_s, s.end - start_s)
                if name:
                    speaker = name
            line = format_live_line(s.start, speaker, s.text)
            self._write_line(line)
            self._notify(line, {"t": round(s.start, 2), "end": round(s.end, 2),
                                "speaker": speaker, "text": s.text})

    def _windows(self, final: bool) -> int:
        """Распознать созревшие окна дорожек по очереди (по окну за круг —
        дорожки не ждут друг друга). → сколько окон распознано."""
        done = 0
        while True:
            progressed = False
            for fname, tr in self._tracks.items():
                window = self._next_window(tr, final)
                if window is None:
                    continue
                self._recognize(fname, tr, *window)
                progressed = True
                done += 1
            if not progressed:
                return done

    def step(self) -> int:
        """Такт рабочего потока: забрать звук, распознать созревшие окна."""
        with self._window_lock:
            self._drain()
            return self._windows(final=False)

    def process_window(self) -> None:
        """Распознать всё накопленное сейчас, целиком (остановка, тесты).
        Ждёт окно, которое распознаётся в этот момент."""
        with self._window_lock:
            self._drain()
            self._windows(final=True)

    def flush_tail(self) -> bool:
        """Перед вопросом ассистенту: распознать ещё не распознанный хвост
        речи. Окно распознаётся прямо сейчас — не ждём его: ответ берёт уже
        готовые реплики. → True — хвост распознан."""
        if not self._window_lock.acquire(blocking=False):
            return False
        try:
            self._drain()
            self._windows(final=True)
            return True
        finally:
            self._window_lock.release()

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
        from meet import recorder
        from meet.recorder import OpusWriter

        # pyaudiowpatch на Windows, sounddevice и помощник ScreenCaptureKit на macOS.
        pyaudio = recorder.audio_backend()

        self._transcriber.load()
        if self._matcher is not None:
            self._matcher.load()
        # Папка встречи — после загрузки моделей (до минуты): остановка или
        # сбой на загрузке не оставляют пустую датированную папку.
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._p = pyaudio.PyAudio()
        # Выбранное в настройках — по имени; нет его — системное, с одной
        # строкой об этом (живой режим, в отличие от записи, устройство на
        # ходу не меняет).
        picked = []
        for kind, wanted in (("output", self.output_device), ("mic", self.mic_device)):
            dev, fell_back = recorder.resolve_device(self._p, kind, wanted)
            if fell_back:
                print(recorder.fallback_text(kind, wanted))
                self.devices_fallback.append(
                    {"kind": kind, "name": wanted,
                     "device": dev["name"].removesuffix(" [Loopback]")})
            picked.append(dev)
        devices = (
            (picked[0], "sys.wav", True, True),
            (picked[1], "mic.wav", False, False),
        )
        # Начало координат ленты — старт захвата, как и у дорожек на диске.
        self._t0 = self._clock()
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
        while not self._stop.wait(STEP_S):
            try:
                self.step()
                self._last_error = None
            except Exception as e:  # окно не должно валить весь режим
                text = f"{type(e).__name__}: {e}"
                if text != self._last_error:  # один и тот же сбой — один раз
                    self._last_error = text
                    self._write_line(f"<!-- ошибка окна: {e} -->")

    def stats_line(self) -> str:
        """Итог распознавания живого режима для журнала (без текста)."""
        st = self.stats
        if not st["windows"]:
            return "распознавание живого режима: окон не было"
        times = sorted(st["times"])
        p90 = times[min(len(times) - 1, int(len(times) * 0.9))]
        per_s = st["asr_s"] / st["audio_s"] if st["audio_s"] else 0.0
        name = getattr(self._transcriber, "name", "?")
        line = (f"распознавание живого режима ({name}): окон {st['windows']}, "
                f"{st['asr_s']:.1f} с на {st['audio_s']:.0f} с звука ({per_s:.3f} с на секунду), "
                f"окно p90 {p90:.2f} с, max {times[-1]:.2f} с")
        if st["skipped_s"]:
            line += f", пропущено {st['skipped_s']:.0f} с"
        return line

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
            if self.stats["windows"]:
                self._log(self.stats_line())
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
