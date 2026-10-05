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
# Догонялка (ассистент включён посреди записи): за такт рабочего потока —
# окна уже записанного не дольше этого, потом снова живой звук. Живая лента
# отстаёт от речи не больше чем на это время плюс одно окно.
CATCHUP_SLICE_S = 1.0
# Доля времени рабочего потока, которую догонялка может занять: после куска
# окон она ждёт не меньше, чем на него ушло (не больше половины процессорного
# времени потока) — живому звуку и чужим процессам остаётся место.
CATCHUP_DUTY = 0.5
# Потоков torch на CPU, пока идёт догонялка (обычно — live_asr.CPU_THREADS):
# фоновая работа не должна занимать все ядра, пока идёт запись.
CATCHUP_THREADS = 4
# Строки догонялки по мере распознавания — в этот файл рядом с лентой (ассистент
# убит посреди догонялки — они не пропадут; слияние — в конце или при новом
# включении ассистента в эту запись).
CATCHUP_SIDE = "live_transcript.catchup.md"
# Дорожки записи резидента (отвод `meet.pcm_tap`) → ключ дорожки движка,
# нормализовать ли, опознавать ли голос (как у собственного захвата).
# Микрофон тоже опознаётся: голоса делит meet.live_voices — только при
# образце владельца, без него микрофон весь владельца, как раньше.
TAP_TRACKS = {"sys.opus": ("sys.wav", True, True), "mic.opus": ("mic.wav", False, True)}


def fmt_hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_live_line(start_s: float, speaker: str, text: str) -> str:
    return f"[{fmt_hms(start_s)}] {speaker}: {text}"


def relabel_line(line: str, old: str, new: str) -> str:
    """`[чч:мм:сс] Старый: текст` → `[чч:мм:сс] Новый: текст`; строка другого
    вида — без изменений."""
    head, sep, rest = line.partition("] ")
    if not sep or not head.startswith("[") or not rest.startswith(f"{old}: "):
        return line
    return f"{head}{sep}{new}: {rest[len(old) + 2:]}"


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
            if self._closed:
                return  # callback стрима, запоздавший к остановке
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

    def catch_up(self) -> None:
        """Долить всю паузу до часов сейчас — перед стартом переоткрытого
        стрима. Перезапуск короче PAD_GAP_S ни callback, ни тикер не долили
        бы: дорожка с непрерывным звуком осталась бы сдвинутой на его длину."""
        with self._lock:
            if not self._closed:
                self._pad(0.0, self.TAIL_GAP_S)

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


def _track_converter(src_rate: int, src_ch: int, rate: int, channels: int):
    """bytes→bytes: int16 PCM устройства → формат дорожки. То же устройство —
    без изменений. Дорожка шире стерео (массив микрофонов, 5.1), а устройство
    другое: `recorder._make_converter` даёт не больше двух каналов, поэтому
    звук сводится в стерео и раскладывается по каналам дорожки парами
    (левый — в чётные, правый — в нечётные). Моно-источник после этого
    сводится в моно без потерь; у стерео веса каналов в сведении могут
    отличаться (ffmpeg сводит по раскладке каналов, а не средним)."""
    from meet import recorder

    if channels <= 2 or (src_rate, src_ch) == (rate, channels):
        return recorder._make_converter(src_rate, src_ch, rate, channels)
    from array import array

    to_stereo = recorder._make_converter(src_rate, src_ch, rate, 2)

    def convert(data: bytes) -> bytes:
        stereo = array("h", to_stereo(data))
        out = array("h", bytes(len(stereo) * channels))
        for ch in range(channels):
            out[ch::channels] = stereo[ch % 2::2]
        return out.tobytes()

    return convert


class _CaptureTrack:
    """Дорожка собственного захвата живого режима. Устройство под ней может
    смениться (BT-наушники отключились и вернулись), а файл и буфер окна —
    одни на всю запись: формат дорожки — у первого устройства, звук другого
    приводится к нему (`_track_converter`)."""

    def __init__(self, fname: str, pick, role: int) -> None:
        self.fname = fname  # ключ дорожки движка: sys.wav / mic.wav
        self.pick = pick  # recorder._Picker: устройство на каждое (пере)открытие
        self.role = role  # индекс в ids() endpoint'ов: 0 вывод, 1 ввод
        self.writer: "WallClockWriter | None" = None
        self.rate = 0
        self.channels = 0
        self.stream = None
        self.device: str | None = None
        self.waiting = False  # устройства нет — ждём (одна строка в журнал)
        self.in_fallback = False  # пишет с системного вместо выбранного

    def alive(self) -> bool:
        if self.stream is None:
            return False
        try:
            return bool(self.stream.is_active())
        except Exception:
            return False


# «Взять из настроек» — отличается от None («системное устройство»).
_FROM_SETTINGS = object()


class LiveEngine:
    """Движок живого режима: callback пишет дорожки и копит аудио в буферы,
    рабочий поток окнами расшифровывает и дописывает live_transcript.md.

    Живой режим — тоже запись: start() берёт общий `.recording.lock` в папке
    записей (`out_root`, по умолчанию — родитель `out_dir`), поэтому
    резидентная запись и второй живой режим получают «Запись уже идёт».
    stop() снимает lock в finally.

    `tap_connect` — ассистент включён посреди обычной записи: `() ->
    TapClient` (`meet.pcm_tap`). Тогда движок устройств не открывает, lock не
    берёт и дорожек не пишет — звук идёт из отвода записи резидента, с той же
    позиции дорожки (таймкоды ленты — время записи). Конец записи — конец
    отвода: `on_source_end()`. Уже записанное до подключения догоняет
    `start_catchup()` (`meet.live_catchup`)."""

    SPEAKERS = {"sys.wav": "Собеседник", "mic.wav": "Вы"}

    def __init__(self, out_dir, transcriber, window_seconds: float = 20.0,
                 hotwords: str | None = None, clock=None,
                 on_line=None, voice_matcher=None, speaker_name=None,
                 on_entry=None, out_root=None, mic_device=_FROM_SETTINGS,
                 output_device=_FROM_SETTINGS, text_fixes=None, log=print,
                 tap_connect=None, on_source_end=None, on_relabel=None, on_hide=None,
                 live_dedupe: bool = False) -> None:
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
        # Колбэк (line, {"t", "speaker", "text", "voice"?}): та же строка и её структура.
        self.on_entry = on_entry
        # Колбэк (voice, speaker): голос переименован задним числом (meet.live_voices).
        self.on_relabel = on_relabel
        # Колбэк ([номер строки у on_entry]): показанные строки-дубли спрятать.
        self.on_hide = on_hide
        # Дубли соседа в живой ленте (meet.live_dedupe, `asr.live_mic_dedupe`):
        # строки людей рядом ждут, пока распознается звук собеседников.
        self._dupes = None
        if live_dedupe:
            from meet.live_dedupe import LiveDuplicates

            self._dupes = LiveDuplicates()
        # Строки ленты с ключом голоса: {"voice", "speaker", "line"} — их
        # подпись может смениться задним числом.
        self._voiced: list[dict] = []
        self.out_root = Path(out_root) if out_root is not None else self.out_dir.parent
        self._lock_path: Path | None = None  # наш .recording.lock, пока держим
        # Голоса (meet.voice_id.VoiceMatcher, duck-typed): онлайн-кластеры
        # дорожек (meet.live_voices) — когда матчер загрузится (в фоне).
        self._matcher = voice_matcher
        self._voices = None
        self._voices_error: str | None = None
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
        self._capture: list[_CaptureTrack] = []
        self._backend = None  # модуль звука этой ОС (recorder.audio_backend())
        self._p = None
        # Надзор за устройствами (поток доливки): ID дефолтных endpoint'ов на
        # момент последнего (пере)запуска дорожек и расписание повторов.
        self._ids = None
        self._last_restart = 0.0
        self._retry_wait = 0.0
        self._watch_error: str | None = None
        # Выбранное устройство не нашлось — пишем с системного (для резидента:
        # уходит в файл эндпоинта): [{"kind", "name", "device"}].
        self.devices_fallback: list[dict] = []
        # Подключение к идущей записи (см. докстринг класса).
        self._tap_connect = tap_connect
        self._on_source_end = on_source_end
        self._tap = None
        self._tap_reader: "threading.Thread | None" = None
        self._tap_tracks: dict[int, dict] = {}
        self.source_ended = threading.Event()
        # С какой секунды дорожки (ключ — sys.wav/mic.wav) пошёл живой звук.
        self.attach_positions: dict[str, float] = {}
        # Догонялка: дорожки уже записанного, строки ленты до слияния с файлом
        # и итог для панели.
        self._catch: dict[str, dict] = {}
        self._catch_lines: list[str] = []
        self._catch_info: dict | None = None
        self._catch_error: str | None = None
        self._catch_resume = 0.0  # раньше этого (monotonic) догонялка не продолжается
        self._catch_threads = False  # потоки torch урезаны на время догонялки
        self.on_catchup = None  # () -> None после каждого догнанного окна
        # Поэтапный старт (open_source → load_asr → begin): открытие источника
        # и остановка не идут одновременно; распознавать — только после модели.
        self._start_lock = threading.Lock()
        self._asr_loaded = False
        self._staged = False  # стартовали через open_source(), а не start()
        self._asr_ready = False
        # Живой звук отстал в этом такте (в очереди дорожки больше двух окон):
        # догонялка в этот такт не идёт.
        self._live_lag = False

    def register_track(self, fname: str, rate: int, channels: int,
                       normalize: bool = False, identify: bool = False) -> None:
        self._tracks[fname] = {
            "buffer": TrackBuffer(),
            "rate": rate,
            "channels": channels,
            "normalize": normalize,
            "identify": identify,  # голоса дорожки — в meet.live_voices
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
            # Хвост длиннее `merge_s` (после долгой задержки) — частями: GigaAM
            # больше ~25 с за вызов не принимает.
            return self._take(tr, min(have, self.policy.merge_s)) if have >= TAIL_MIN_S else None
        policy = self.policy
        if "reader" not in tr and have >= policy.behind_s:
            self._live_lag = True  # живой звук отстал — догонялка подождёт
        if have < (policy.min_s + policy.max_s) / 2:
            return None
        if have < policy.max_s:
            cut = find_pause(self._audio(tr), rate, policy.min_s, have)
            return self._take(tr, cut) if cut is not None else None
        # Звук, накопленный, пока грузилась модель (до `protect_until`), не
        # пропускаем: он распознаётся целиком, пропуск — только для отставания потом.
        if have > BACKLOG_MAX_S and tr["pos"] >= tr.get("protect_until", 0.0):
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

    def _recognize(self, fname: str, tr: dict, mono, start_s: float, pad_only: bool,
                   catchup: bool = False) -> None:
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
        voices = self._live_voices() if tr["identify"] else None
        dupes = self._dupes if not catchup else None
        if dupes is not None:
            dupes.sound(fname.removesuffix(".wav"), start_s, audio)
        for s in segs:
            speaker, voice = default_speaker, None
            if voices is not None:
                got = self._assign(voices, fname, audio, s.start - start_s, s.end - start_s,
                                   s.start, s.end)
                if got is not None and got.voice is not None:
                    speaker, voice = got.speaker, got.voice
            line = format_live_line(s.start, speaker, s.text)
            entry = {"t": round(s.start, 2), "end": round(s.end, 2),
                     "speaker": speaker, "text": s.text}
            if voice is not None:
                entry["voice"] = voice  # подпись голоса может смениться задним числом
            if catchup:
                entry["catchup"] = True
            if dupes is not None and fname == "mic.wav" and voice is not None \
                    and dupes.wants(got.role, s):
                # Человек рядом: строка ждёт звук собеседников — вдруг она копия.
                dupes.hold({"entry": entry, "start": s.start}, s, got.role)
                continue
            self._emit(line, entry)
        if dupes is not None and fname == "sys.wav":
            dupes.heard_sys(segs, start_s + len(audio) / WINDOW_RATE)
        if voices is not None:
            self._apply_renames(voices)
        if dupes is not None:
            self._flush_dupes()

    def _emit(self, line: str, entry: dict) -> tuple:
        """Строка — в ленту (файл) и потребителям. → (номер строки у
        `on_entry` или None, запись голоса или None)."""
        if entry.get("catchup"):
            # Начало встречи: в файл ленты — по времени, слиянием в конце
            # догонялки; потребителям — с пометкой (подсказки по нему не
            # тикают, лента ставит его выше живых строк).
            self._catch_lines.append(line)
            self._catch_side(line)
        else:
            self._write_line(line)
        record = None
        if entry.get("voice"):
            # Подпись голоса может смениться задним числом — помним строку.
            record = {"voice": entry["voice"], "speaker": entry["speaker"], "line": line}
            self._voiced.append(record)
        return self._notify(line, entry), record

    def _flush_dupes(self, final: bool = False) -> None:
        """Задержанные строки людей рядом, которым пора (meet.live_dedupe): не
        копии — в ленту с подписью на сейчас; показанные раньше времени и
        оказавшиеся копией — спрятать (файл, `on_hide`)."""
        dupes = self._dupes
        for item in dupes.release(final):
            entry = dict(item["entry"])
            voice = entry.get("voice")
            if voice and self._voices is not None:
                try:
                    entry["speaker"] = self._voices.speaker(voice)
                except Exception as e:
                    self._voice_failed(e)
            if len(item["words"]) < len(item["toks"]):
                entry["text"] = "".join(t.text for t in item["words"]).strip()
            line = format_live_line(item["start"], entry["speaker"], entry["text"])
            bus_id, record = self._emit(line, entry)
            if not item["due"]:
                item["bus_id"], item["record"] = bus_id, record
                dupes.shown(item)
        for item in dupes.recheck():
            record = item.get("record")
            if record is not None:
                self._voiced = [r for r in self._voiced if r is not record]
                self._swap_lines([(record["line"], None)])
            if item.get("bus_id") is not None and self.on_hide is not None:
                try:
                    self.on_hide([item["bus_id"]])
                except Exception:
                    pass  # потребитель не должен валить запись

    def _apply_renames(self, voices) -> None:
        """Переименования голосов после окна: строки ленты (файл, строки
        догонялки) и потребители (`on_relabel`) — задним числом."""
        try:
            renames = dict(voices.drain())
        except Exception as e:
            self._voice_failed(e)
            return
        if not renames:
            return
        swaps = []
        for rec in self._voiced:
            speaker = renames.get(rec["voice"])
            if speaker is None or speaker == rec["speaker"]:
                continue
            new = relabel_line(rec["line"], rec["speaker"], speaker)
            swaps.append((rec["line"], new))
            rec["line"], rec["speaker"] = new, speaker
        self._swap_lines(swaps)
        for voice, speaker in renames.items():
            if self.on_relabel is not None:
                try:
                    self.on_relabel(voice, speaker)
                except Exception:
                    pass  # потребитель не должен валить запись

    def _swap_lines(self, swaps: list) -> None:
        """Заменить строки ленты: [(старая, новая | None — убрать)]. Строки
        догонялки, ещё не слитые в файл, — в памяти (и в её запасном файле),
        остальные — атомарной перезаписью `live_transcript.md` (под
        `_window_lock`: его держит распознавание окна)."""
        if not swaps:
            return
        in_file, in_side = [], []
        for old, new in swaps:
            if old in self._catch_lines:
                i = self._catch_lines.index(old)
                if new is None:
                    self._catch_lines.pop(i)
                else:
                    self._catch_lines[i] = new
                in_side.append((old, new))
            else:
                in_file.append((old, new))
        if in_file:
            if self._out is not None:
                self._out.close()
                self._out = None
            self._rewrite(self._transcript, in_file)
        if in_side:
            self._rewrite(self.out_dir / CATCHUP_SIDE, in_side)

    def _rewrite(self, path: Path, swaps: list) -> None:
        """Атомарно (tmp + replace) заменить строки файла; каждая замена — у
        последнего ещё не тронутого вхождения (наши строки дописаны после
        ленты прошлого включения ассистента). Сбой диска — одна строка в
        журнал: лента в памяти и у потребителей уже верная."""
        import os

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        used: set[int] = set()
        drop: set[int] = set()
        for old, new in swaps:
            i = next((k for k in range(len(lines) - 1, -1, -1)
                      if k not in used and lines[k] == old), None)
            if i is None:
                continue
            used.add(i)
            if new is None:
                drop.add(i)
            else:
                lines[i] = new
        if not used:
            return
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text("".join(line + "\n" for k, line in enumerate(lines) if k not in drop),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            self._say(f"лента не переписана ({type(e).__name__}: {e})")

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
            self._live_lag = False
            self._drain()
            done = self._windows(final=False)
            if self._dupes is not None and self._dupes.pending():
                self._flush_dupes()  # задержанные строки — не дольше HOLD_MAX_S
            return done

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

    def _notify(self, line: str, entry: dict):
        """→ что вернул `on_entry` (шина — номер строки), иначе None."""
        got = None
        for callback, args in ((self.on_line, (line,)),
                               (self.on_entry, (line, entry))):
            if callback is None:
                continue
            try:
                got = callback(*args)
            except Exception:
                pass  # потребитель не должен валить запись
        return got if isinstance(got, int) and not isinstance(got, bool) else None

    def _live_voices(self):
        """Голоса дорожек (meet.live_voices.LiveVoices), как только матчер
        загрузился; до того и без матчера — None (подписи по умолчанию)."""
        if self._voices is None and self._matcher is not None                 and getattr(self._matcher, "enabled", False):
            make = getattr(self._matcher, "live_voices", None)
            try:
                self._voices = make(
                    {"sys": self.SPEAKERS["sys.wav"], "mic": self.SPEAKERS["mic.wav"]},
                    log=self._log) if make is not None else None
            except Exception as e:
                self._voice_failed(e)
        return self._voices

    def _assign(self, voices, fname: str, audio, rel_start: float, rel_end: float,
                start: float, end: float):
        """Голос сегмента; любой сбой → None (окно важнее имени)."""
        try:
            lo = max(0, int(rel_start * WINDOW_RATE))
            hi = min(len(audio), int(rel_end * WINDOW_RATE))
            return voices.assign(fname.removesuffix(".wav"), audio[lo:hi], start, end)
        except Exception as e:
            self._voice_failed(e)
            return None

    def _voice_failed(self, e: Exception) -> None:
        text = f"{type(e).__name__}: {e}"
        if text != self._voices_error:  # один и тот же сбой — одна строка
            self._voices_error = text
            self._log(f"голоса: сбой живых имён ({text})")

    def _write_line(self, line: str) -> None:
        if self._out is None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self._out = open(self._transcript, "a", encoding="utf-8")
        self._out.write(line + "\n")
        self._out.flush()

    def start(self) -> None:
        """Всё сразу (`meet live`, тесты): модели, затем захват и рабочий
        поток. Ассистент стартует поэтапно — `open_source()` до моделей, см.
        `meet.assist.app`."""
        if self._tap_connect is not None:
            try:
                self.load_asr()
                self.load_voices()
                self._open_tap()
                self.begin()
            except BaseException:
                self._close_tap()
                self._unload()
                raise
            return
        from meet.recorder import _acquire_lock

        # Lock — первым делом: при идущей записи отказ мгновенный, без
        # минуты загрузки моделей и без пустой папки встречи.
        self.out_root.mkdir(parents=True, exist_ok=True)
        self._lock_path = _acquire_lock(self.out_root, self.out_dir)
        try:
            from meet import recorder

            recorder.audio_backend()  # нет звуковой библиотеки — отказ до моделей
            self.load_asr()
            self.load_voices()
            # Папка встречи — после загрузки моделей (до минуты): остановка или
            # сбой на загрузке не оставляют пустую датированную папку.
            self._open_devices()
            self.begin()
        except BaseException:
            # Частичный старт: закрыть то, что успело открыться, и отдать lock;
            # модель распознавания — выгрузить (и её временную папку удалить).
            self._close_capture()
            self._unload()
            self._release_lock()
            raise

    # --- поэтапный старт (ассистент) --------------------------------------

    def open_source(self) -> None:
        """Источник звука — до моделей: отвод записи резидента или свои
        устройства (lock записи, папка встречи, дорожки). Звук с этого
        момента пишется и копится в буферах окон; распознавать его начнёт
        `begin()`, когда загрузится модель, — ничего не теряется."""
        with self._start_lock:
            if self._stop.is_set():
                return
            self._staged = True
            if self._tap_connect is not None:
                try:
                    self._open_tap()
                except BaseException:
                    self._close_tap()
                    raise
                return
            from meet.recorder import _acquire_lock

            self.out_root.mkdir(parents=True, exist_ok=True)
            self._lock_path = _acquire_lock(self.out_root, self.out_dir)
            try:
                self._open_devices()
            except BaseException:
                self._close_capture()
                self._release_lock()
                raise

    def load_asr(self) -> None:
        """Модель распознавания (самое долгое в старте)."""
        self._transcriber.load()
        self._asr_loaded = True

    def load_voices(self) -> None:
        """Эмбеддер голосов для live-имён. Ассистенту — в фоне, после модели
        распознавания: окна до него идут без имён (имена даст расшифровка)."""
        if self._matcher is not None:
            self._matcher.load()

    def begin(self) -> None:
        """Модель загружена — рабочий поток: сначала звук, накопленный за
        загрузку (целиком, без пропуска «не успевает»), дальше — по такту."""
        with self._start_lock:
            if self._stop.is_set() or self._worker is not None:
                return
            with self._window_lock:
                self._drain()
                for tr in self._tracks.values():
                    tr["protect_until"] = tr["pos"] + tr["pending_n"] / tr["rate"]
            self._asr_ready = True
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()
        if self._tap_connect is None:
            print(f"Живой режим идёт. Транскрипт: {self._transcript}")

    def _unload(self) -> None:
        if self._transcriber is not None:
            try:
                self._transcriber.unload()
            except Exception:
                pass

    def _open_devices(self) -> None:
        from meet import recorder
        from meet.recorder import OpusWriter

        # pyaudiowpatch на Windows, sounddevice и помощник ScreenCaptureKit на macOS.
        pyaudio = recorder.audio_backend()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._backend = pyaudio
        self._p = pyaudio.PyAudio()
        # Системные устройства — до выбора своих: смена между выбором и первым
        # тактом надзора не потеряется.
        self._ids = self._default_ids()
        # Выбранное в настройках — по имени; нет его — системное, с одной
        # строкой об этом. Дальше за устройствами следит поток доливки
        # (`_watch_devices`): сменилось системное или стрим умер — дорожки
        # переоткрываются.
        tracks = (
            (_CaptureTrack("sys.wav", recorder._Picker("output", self.output_device), 0),
             True, True),
            (_CaptureTrack("mic.wav", recorder._Picker("mic", self.mic_device), 1),
             False, True),
        )
        picked = []
        for track, _, _ in tracks:
            dev = track.pick(self._p)
            if track.pick.fallback:
                track.in_fallback = True
                print(recorder.fallback_text(track.pick.kind, track.pick.wanted))
                self.devices_fallback.append(
                    {"kind": track.pick.kind, "name": track.pick.wanted,
                     "device": dev["name"].removesuffix(" [Loopback]")})
            picked.append(dev)
        self._retry_wait = recorder.RETRY_S
        # Начало координат ленты — старт захвата, как и у дорожек на диске.
        self._t0 = self._clock()
        for (track, normalize, identify), dev in zip(tracks, picked):
            fname = track.fname
            track.rate = int(dev["defaultSampleRate"])
            track.channels = max(1, int(dev["maxInputChannels"]))
            self.register_track(fname, track.rate, track.channels, normalize=normalize,
                                identify=identify)
            # fname — внутренний ключ дорожки (завязан на SPEAKERS); на диск для
            # офлайн-прохода пишем сжатый .opus. Буфер окна получает тот же
            # поток, что и файл, — с доливкой пауз.
            track.writer = WallClockWriter(
                OpusWriter(self.out_dir / fname.replace(".wav", ".opus"),
                           track.channels, track.rate),
                track.channels, track.rate, buffer=self._tracks[fname]["buffer"])
            self._capture.append(track)  # до открытия: сбой старта закроет и файл
            src_rate, src_ch = self._open_stream(track, dev)
            print(f"  {fname}: {dev['name']} ({src_rate} Hz, {src_ch} ch)")

        self._pad_thread = threading.Thread(target=self._pad_loop,
                                            name="meet-live-pad", daemon=True)
        self._pad_thread.start()

    def _catchup_due(self) -> bool:
        """Догонять в этот такт: есть что, живой звук не отстал, вышла пауза доли."""
        return bool(self._catch) and not self._live_lag and \
            time.monotonic() >= self._catch_resume

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
            # Живой звук отстал — в этот такт не догоняем: иначе его старый
            # звук пропускался бы («не успевает»), а живая лента важнее начала
            # встречи (оно и так будет в полной расшифровке).
            if self._catchup_due():
                began = time.monotonic()
                try:
                    self.catchup_step()
                except Exception as e:  # догонялка — не повод ронять живой режим
                    self._abort_catchup(f"{type(e).__name__}: {e}")
                spent = time.monotonic() - began
                # Не больше CATCHUP_DUTY времени потока: пауза — по куску.
                self._catch_resume = time.monotonic() + spent * (1 - CATCHUP_DUTY) / CATCHUP_DUTY

    # --- подключение к идущей записи ------------------------------------

    def _open_tap(self) -> None:
        """Звук — из отвода записи резидента (с этого места пойдёт живой
        звук; всё раньше — догонялке)."""
        client = self._tap_connect()
        self._tap = client
        for info in client.tracks:
            known = TAP_TRACKS.get(str(info.get("name")))
            if known is None:
                continue
            key, normalize, identify = known
            rate, channels = int(info["rate"]), max(1, int(info["channels"]))
            frame = 2 * channels
            self.register_track(key, rate, channels, normalize=normalize, identify=identify)
            pos = int(info.get("pos") or 0)
            # Таймкоды ленты — время записи: очередь дорожки начинается там же,
            # где первый кадр отвода.
            self._tracks[key]["pos"] = pos / (frame * rate)
            self.attach_positions[key] = pos / (frame * rate)
            self._tap_tracks[int(info["index"])] = {"key": key, "next": pos,
                                                    "frame": frame, "rate": rate}
        if not self._tracks:
            raise RuntimeError("Отвод записи без знакомых дорожек")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._t0 = self._clock()
        self._tap_reader = threading.Thread(target=self._read_tap, name="meet-live-tap",
                                            daemon=True)
        self._tap_reader.start()
        print(f"Ассистент подключён к записи: {self.out_dir}")

    def _read_tap(self) -> None:
        """Кадры отвода → буферы дорожек. Дыру (резидент выбросил кадры, пока
        мы не успевали читать) заполняем тишиной: таймкоды не уезжают."""
        try:
            for index, pad, pos, data in self._tap.frames():
                track = self._tap_tracks.get(index)
                if track is None:
                    continue
                buffer = self._tracks[track["key"]]["buffer"]
                end = pos + len(data)
                if pos > track["next"]:
                    gap = pos - track["next"]
                    gap -= gap % track["frame"]
                    step = track["rate"] * track["frame"]
                    while gap > 0:
                        n = min(gap, step)
                        buffer.push_silence(b"\x00" * n)
                        gap -= n
                elif pos < track["next"]:
                    data = data[track["next"] - pos:]
                if data:
                    (buffer.push_silence if pad else buffer.push)(data)
                track["next"] = max(track["next"], end)
        except Exception as e:
            self._log(f"отвод записи: {type(e).__name__}: {e}")
        finally:
            self.source_ended.set()
            if not self._stop.is_set() and self._on_source_end is not None:
                try:
                    self._on_source_end()  # запись кончилась — ассистенту пора стоп
                except Exception:
                    pass

    def _close_tap(self) -> None:
        tap, self._tap = self._tap, None
        if tap is not None:
            try:
                tap.close()
            except Exception:
                pass
        if self._tap_reader is not None and self._tap_reader is not threading.current_thread():
            self._tap_reader.join(timeout=5)

    # --- догонялка ------------------------------------------------------

    def start_catchup(self, tracks: dict, info: dict | None = None, reader=None) -> bool:
        """Догнать уже записанное: `tracks` — {ключ: (путь, начало, конец)} из
        `live_catchup.plan`, `info` — сам план (для панели). Окна режутся и
        распознаются тем же движком в рабочем потоке, между живыми окнами.
        `reader(path, start, end)` — подмена `PcmReader` в тестах. → False —
        догонять нечего."""
        from meet.live_catchup import PcmReader

        make = reader or PcmReader
        catch: dict[str, dict] = {}
        for key, (path, start, end) in tracks.items():
            base = self._tracks.get(key) or {}
            catch[key] = {
                "rate": 16000, "channels": 1,
                "normalize": base.get("normalize", key == "sys.wav"),
                "identify": base.get("identify", True),
                "gain": None, "last_text": None,
                "pending": [], "pending_n": 0, "pos": float(start),
                "start": float(start), "end": float(end),
                "reader": make(path, start, end), "done": False,
            }
        if not catch:
            return False
        with self._window_lock:
            self._catch = catch
            self._catch_error = None
            self._catch_info = {**(info or {}), "from_t": min(c["start"] for c in catch.values()),
                                "to_t": max(c["end"] for c in catch.values()),
                                "total_s": sum(c["end"] - c["start"] for c in catch.values())}
        self._log(f"догоняю начало встречи: {self._catch_info['total_s']:.0f} с звука дорожек")
        return True

    def _fill(self, tr: dict) -> None:
        """Очередь догоняемой дорожки — на одно обычное окно с запасом (окна
        обычной длины: кусок догонялки короткий, живой звук ждёт его меньше),
        из ffmpeg."""
        policy = self.policy
        target = policy.max_s + policy.min_s
        need = target - tr["pending_n"] / tr["rate"]
        reader = tr["reader"]
        if need > 0 and not reader.eof:
            audio = reader.read(need)
            if len(audio):
                tr["pending"].append((audio, False))
                tr["pending_n"] += len(audio)

    def catchup_step(self, budget_s: float = CATCHUP_SLICE_S) -> bool:
        """Окна догонялки, пока не вышло `budget_s` секунд. → True — ещё есть
        что догонять."""
        deadline = time.perf_counter() + budget_s
        if not self._catch_threads:
            self._catch_threads = True
            self._cpu_threads(CATCHUP_THREADS)
        while not self._stop.is_set():
            with self._window_lock:
                left = [k for k, tr in self._catch.items() if not tr["done"]]
                if not left:
                    self._finish_catchup()
                    return False
                # Дорожки — вровень по времени: лента начала встречи идёт по порядку.
                key = min(left, key=lambda k: self._catch[k]["pos"])
                tr = self._catch[key]
                self._fill(tr)
                window = self._next_window(tr, final=tr["reader"].eof)
                if window is None:
                    if tr["reader"].eof:
                        tr["done"] = True
                        tr["reader"].close()
                else:
                    self._recognize(key, tr, *window, catchup=True)
            self._catchup_changed()
            if time.perf_counter() >= deadline:
                return True
        return bool(self._catch)

    def _cpu_threads(self, n: int | None) -> None:
        """Потоки torch распознавания (None — обычные); у кого их нет — ничего."""
        set_threads = getattr(self._transcriber, "set_cpu_threads", None)
        if set_threads is not None:
            try:
                set_threads(n)
            except Exception:
                pass

    def _catch_side(self, line: str) -> None:
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            with open(self.out_dir / CATCHUP_SIDE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # строка всё равно в памяти и уйдёт в ленту слиянием

    def _catchup_changed(self) -> None:
        if self.on_catchup is not None:
            try:
                self.on_catchup()
            except Exception:
                pass

    def _abort_catchup(self, error: str) -> None:
        with self._window_lock:
            for tr in self._catch.values():
                tr["reader"].close()
            if self._catch:
                self._log(f"догонялка остановлена: {error}")
            self._catch_error = error
            self._finish_catchup(complete=False)
        self._catchup_changed()

    def _finish_catchup(self, complete: bool = True) -> None:
        """Под `_window_lock`: строки начала встречи — в файл ленты по времени."""
        if self._catch_info is not None and not self._catch_info.get("finished"):
            done = self._catch_done_s()
            self._catch_info = {**self._catch_info, "finished": True, "done_s": done,
                                "complete": complete and not self._catch_error}
            if complete:
                self._log("начало встречи догнано")
        for tr in self._catch.values():
            if not tr["done"]:
                tr["reader"].close()
                tr["done"] = True
        self._catch = {}
        if self._catch_threads:
            self._catch_threads = False
            self._cpu_threads(None)
        self._merge_catchup_lines()

    def _catch_done_s(self) -> float:
        done = 0.0
        for tr in self._catch.values():
            done += tr["end"] - tr["start"] if tr["done"] else \
                max(0.0, min(tr["pos"], tr["end"]) - tr["start"])
        return done

    def _merge_catchup_lines(self) -> None:
        from meet.live_catchup import merge_lines

        lines, self._catch_lines = self._catch_lines, []
        if not lines:
            return
        if self._out is not None:
            self._out.close()
            self._out = None
        merge_lines(self._transcript, lines)
        (self.out_dir / CATCHUP_SIDE).unlink(missing_ok=True)

    def catchup_progress(self) -> dict | None:
        """Для панели: {"active", "done_s", "total_s", "from_t", "to_t",
        "capped", "complete"}; догонялки не было — None."""
        info = self._catch_info
        if info is None:
            return None
        if info.get("finished"):
            return {"active": False, "done_s": info.get("done_s", info["total_s"]),
                    "total_s": info["total_s"], "from_t": info["from_t"], "to_t": info["to_t"],
                    "capped": bool(info.get("capped")), "complete": bool(info.get("complete"))}
        return {"active": True, "done_s": round(self._catch_done_s(), 1),
                "total_s": info["total_s"], "from_t": info["from_t"], "to_t": info["to_t"],
                "capped": bool(info.get("capped")), "complete": False}

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
        from meet import recorder

        # Endpoint'ы — COM-объект этого потока: здесь создаётся, здесь и
        # закрывается. На macOS надзора нет, ни за системным звуком, ни за
        # микрофоном: системный звук там идёт через помощник, и обычная
        # запись перезапускает его отдельно от микрофона
        # (`recorder._Session._tick_tap`) — здесь это не повторено.
        endpoints = None if recorder._MAC else recorder._DefaultEndpoints()
        com_ok = endpoints is not None and endpoints.ids() is not None
        if endpoints is not None and not com_ok:
            self._say("COM недоступен — смену дефолтного устройства не отслеживаю")
        try:
            while not self._stop.wait(PAD_TICK_S):
                for track in list(self._capture):
                    try:
                        track.writer.tick()
                    except Exception:
                        pass  # сбой доливки не валит запись; хвост дольёт close()
                if endpoints is None:
                    continue
                try:
                    ids = endpoints.ids()
                    if com_ok and ids is None:
                        com_ok = False
                        self._say("COM перестал отвечать — смену дефолтного "
                                  "устройства больше не отслеживаю")
                    self._watch_devices(ids)
                    self._watch_error = None
                except Exception as e:  # надзор не должен валить доливку
                    text = f"{type(e).__name__}: {e}"
                    if text != self._watch_error:  # один и тот же сбой — один раз
                        self._watch_error = text
                        self._say(f"надзор за устройствами: {text}")
        finally:
            if endpoints is not None:
                endpoints.close()

    # --- смена аудио-устройства посреди записи ---------------------------

    def _say(self, line: str) -> None:
        """Строка надзора в журнал. Сбой журнала не должен остановить поток
        доливки и аудио-callback."""
        try:
            self._log(line)
        except Exception:
            pass

    @staticmethod
    def _default_ids():
        """ID системных endpoint'ов прямо сейчас, из потока вызывающего.
        COM-объект — на один вызов: у надзора свой, в его потоке."""
        from meet import recorder

        if recorder._MAC:
            return None
        endpoints = recorder._DefaultEndpoints()
        try:
            return endpoints.ids()
        finally:
            endpoints.close()

    def _open_stream(self, track: _CaptureTrack, dev: dict,
                     resume: bool = False) -> tuple[int, int]:
        """Стрим устройства `dev` в дорожку; → (частота, каналы) устройства.
        `resume` — переоткрытие посреди записи: пауза перезапуска доливается
        тишиной до старта стрима, иначе она выпала бы из таймлайна дорожки."""
        pyaudio = self._backend
        src_rate = int(dev["defaultSampleRate"])
        src_ch = max(1, int(dev["maxInputChannels"]))
        convert = _track_converter(src_rate, src_ch, track.rate, track.channels)
        writer = track.writer

        def cb(in_data, frame_count, time_info, status):
            try:
                writer.write(convert(in_data))
            except Exception as e:
                self._say(f"{track.fname}: ошибка в аудио-callback: {e!r} — "
                          "стрим будет переоткрыт")
                return (None, pyaudio.paAbort)
            return (None, pyaudio.paContinue)

        params = dict(
            format=pyaudio.paInt16,
            channels=src_ch,
            rate=src_rate,
            input=True,
            input_device_index=int(dev["index"]),
            frames_per_buffer=1024,
            stream_callback=cb,
        )
        if resume:
            stream = self._p.open(start=False, **params)
            try:
                writer.catch_up()
                stream.start_stream()
            except Exception:
                try:
                    stream.close()
                except Exception:
                    pass
                raise
        else:
            stream = self._p.open(**params)
        track.stream = stream
        track.device = dev["name"]
        return src_rate, src_ch

    def _watch_devices(self, ids) -> None:
        """Такт надзора (поток доливки), та же логика, что у записи
        (`recorder._Session.tick`): сменилось системное устройство дорожки,
        которая за ним следит, стрим умер или дорожка ждёт устройство —
        полный перезапуск захвата. Файлы дорожек при этом не трогаются:
        паузу доливает `WallClockWriter`."""
        from meet import recorder

        if self._ids is None:
            self._ids = ids  # COM не ответил на старте — базлайн с первого такта
        now = time.monotonic()
        # Закреплённое (и найденное) устройство от системного не зависит.
        changed = ids is not None and self._ids is not None and any(
            ids[t.role] != self._ids[t.role] for t in self._capture if not t.pick.pinned
        )
        if ids is not None and self._ids is not None and not changed:
            self._ids = ids
        died = any(t.stream is not None and not t.alive() for t in self._capture)
        waiting = [t for t in self._capture if t.stream is None]
        # Повтор ждущих — когда для их роли снова есть системное устройство (или
        # COM сломан и проверить нечем), с нарастающей паузой: перезапуск рвёт
        # и здоровую дорожку.
        retry = bool(waiting) and now - self._last_restart >= self._retry_wait and (
            ids is None or any(ids[t.role] is not None for t in waiting)
        )
        if not (changed or died or retry):
            return
        if now - self._last_restart < recorder.RESTART_MIN_S:
            return
        if changed:
            reason = "сменилось дефолтное аудио-устройство"
        elif died:
            reason = "дорожка остановилась (устройство пропало?)"
        else:
            reason = None  # тихий повтор ждущей дорожки
        self._restart_capture(ids, reason)

    def _restart_capture(self, ids, reason: str | None) -> None:
        """Закрыть оба стрима → terminate → свежий PyAudio → переоткрыть.
        Перезапуск всегда полный: список устройств PortAudio обновляет только
        «холодная» инициализация (см. `recorder._Session`)."""
        from meet import recorder

        self._last_restart = time.monotonic()
        if reason:
            self._say(f"{reason} — перезапускаю дорожки")
        for track in self._capture:
            self._close_stream(track)
        error = self._terminate_audio()
        if error is not None:
            self._say(f"PyAudio.terminate: {error!r}")
        if self._stop.is_set():
            return  # остановка: поднимать звук заново незачем, закроет stop()
        try:
            self._p = self._backend.PyAudio()
        except Exception as e:
            self._say(f"PyAudio не инициализировался: {e!r}")
        if self._stop.is_set():
            self._terminate_audio()  # остановка пришла, пока поднимался PyAudio
            return
        if self._p is not None:
            for track in self._capture:
                if self._stop.is_set():
                    return
                self._reopen(track)
            if ids is not None:
                self._ids = ids
        if self._p is not None and all(t.stream is not None for t in self._capture):
            self._retry_wait = recorder.RETRY_S
        else:
            self._retry_wait = min(max(self._retry_wait, recorder.RETRY_S) * 2,
                                   recorder.RETRY_MAX_S)

    def _reopen(self, track: _CaptureTrack) -> bool:
        """Открыть дорожку заново после перезапуска; неудача — ждать дальше."""
        from meet import recorder

        try:
            dev = track.pick(self._p)
            src_rate, src_ch = self._open_stream(track, dev, resume=True)
        except Exception as e:
            if not track.waiting:
                track.waiting = True
                self._say(f"{track.fname}: устройство недоступно — жду "
                          f"(пауза уйдёт в тишину): {e!r}")
            return False
        track.waiting = False
        fallback = bool(track.pick.fallback)
        if fallback != track.in_fallback:
            track.in_fallback = fallback
            text = recorder.fallback_text if fallback else recorder.restored_text
            self._say(text(track.pick.kind, track.pick.wanted))
        self._say(f"{track.fname}: запись возобновлена: {track.device} "
                  f"({src_rate} Hz, {src_ch} ch)")
        return True

    @staticmethod
    def _close_stream(track: _CaptureTrack) -> None:
        stream, track.stream = track.stream, None
        if stream is None:
            return
        for close in (stream.stop_stream, stream.close):
            try:
                close()
            except Exception:
                pass  # стрим на пропавшем устройстве может не закрыться штатно

    def _terminate_audio(self) -> Exception | None:
        """Завершить PyAudio; → ошибка, если Pa_Terminate так и не прошёл."""
        p, self._p = self._p, None
        if p is None:
            return None
        try:
            p.terminate()
        except Exception:
            # Битый стрим (устройство пропало) роняет terminate() до
            # Pa_Terminate — список устройств замёрз бы до конца процесса
            # (см. `recorder._Session._terminate`). Выбрасываем реестр стримов
            # и повторяем.
            try:
                p._streams.clear()
                p.terminate()
            except Exception as e:
                return e
        return None

    def _close_capture(self) -> Exception | None:
        """Закрыть стримы, дорожки и PyAudio; безопасно при частичном старте
        и повторном вызове. Сбой одной дорожки не мешает закрыть остальные —
        первая ошибка возвращается вызывающему."""
        first: Exception | None = None
        tracks, self._capture = self._capture, []
        closers = []
        for track in tracks:
            stream, track.stream = track.stream, None
            if stream is not None:
                closers += [stream.stop_stream, stream.close]
            if track.writer is not None:
                closers.append(track.writer.close)
        for close in closers:
            try:
                close()
            except Exception as e:
                first = first or e
        # Стрим на пропавшем устройстве роняет terminate() до Pa_Terminate —
        # `_terminate_audio` это обходит, как и при перезапуске.
        error = self._terminate_audio()
        return first or error

    def _release_lock(self) -> None:
        lock, self._lock_path = self._lock_path, None
        if lock is not None:
            lock.unlink(missing_ok=True)

    def stop(self) -> Path:
        """Остановить и дописать хвост. Безопасен после неудачного start()
        и повторно; чужой lock (start отказал) не трогает."""
        try:
            self._stop.set()
            # Источник ещё открывается в другом потоке (поэтапный старт) —
            # дождаться: закрывать полуоткрытое нельзя.
            with self._start_lock:
                pass
            if self._worker is not None:
                self._worker.join(timeout=self.window_seconds + 30)
            if self._pad_thread is not None:
                self._pad_thread.join(timeout=10)
            # Подключённый к записи: отвод больше не читаем (что пришло — в буферах).
            self._close_tap()
            # Поэтапный старт, остановленный до модели: распознавать нечем —
            # звук остаётся в дорожках (у подключённого — в записи резидента).
            if not self._staged or self._asr_ready:
                try:
                    self.process_window()  # финальный слив остатка буфера
                except Exception:
                    pass
            if self._dupes is not None:
                try:
                    with self._window_lock:
                        self._flush_dupes(final=True)  # задержанные строки — в ленту
                except Exception:
                    pass
            if self._catch:
                # Остановили, не догнав начало: что успели — в ленту, сводка —
                # с пометкой о неполноте (её ставит ассистент).
                self._abort_catchup("ассистент остановлен раньше")
            elif self._catch_lines:
                with self._window_lock:
                    self._merge_catchup_lines()
            if self.stats["windows"]:
                self._log(self.stats_line())
            for extra in (self._voices, self._dupes):
                extra_line = extra.stats_line() if extra is not None else None
                if extra_line:
                    self._log(extra_line)
            close_error = self._close_capture()
            if self._out is not None:
                self._out.close()
                self._out = None
            if self._transcriber is not None and (not self._staged or self._asr_loaded):
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
