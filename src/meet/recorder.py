import ctypes
import json
import os
import struct
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import pyaudiowpatch as pyaudio

from meet import events

# Битрейт Opus на дорожку: для речи 16 кГц моно 24 кбит/с на слух прозрачно,
# а место — ~40x меньше несжатого стерео 48 кГц wav.
OPUS_BITRATE = "24k"

# Шаг разреженной выборки при замере уровня дорожки: на буфере 1024 кадра это
# 32 сравнения вместо 1024. Уровень нужен метру в UI, а не измерению.
LEVEL_STRIDE = 32

LOCK_NAME = ".recording.lock"

POLL_S = 0.5  # такт вотчдога записи
RESTART_MIN_S = 2.0  # анти-шторм: перезапуск дорожек не чаще этого
RETRY_S = 5.0  # первый повтор, пока дорожка ждёт устройство
RETRY_MAX_S = 60.0  # потолок backoff'а повторов
MIN_GAP_S = 0.05  # паузы короче не заполняем: латентность захвата, не обрыв
TICK_PAD_S = 1.0  # живой стрим без данных дольше этого — доливать тишину


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс. IMPORTANT: os.kill(pid, 0) на Windows НЕ проверка —
    это безусловный TerminateProcess (убьёт запись); поэтому ctypes."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _acquire_lock(out_root: Path, out_dir: Path) -> Path:
    """Lock-файл записи: защита от второй записи и стоп-точка для Claude
    (сценарий «Останови запись» читает pid отсюда). Протухший lock (pid мёртв,
    процесс убили без finally) молча перезаписывается."""
    lock = out_root / LOCK_NAME
    if lock.exists():
        try:
            old = json.loads(lock.read_text(encoding="utf-8"))
            if _pid_alive(int(old["pid"])):
                raise SystemExit(f"Запись уже идёт (папка {old.get('folder', '?')})")
        except (ValueError, KeyError):
            pass  # битый lock — перезаписываем
    lock.write_text(
        json.dumps({"pid": os.getpid(), "folder": str(out_dir)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return lock


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
        # CREATE_NO_WINDOW: под meet-tray (GUI-процесс без консоли) каждый ffmpeg
        # иначе открывает собственное окно терминала; при консольном запуске
        # унаследованные хэндлы stderr продолжают работать как раньше.
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stdout=subprocess.DEVNULL,
                                      creationflags=subprocess.CREATE_NO_WINDOW)

    def write(self, data: bytes) -> None:
        try:
            self._proc.stdin.write(data)
        except (BrokenPipeError, OSError, ValueError):
            pass  # ffmpeg упал/закрыт — не роняем аудио-callback

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.terminate()


def _make_converter(src_rate: int, src_ch: int, dst_rate: int, dst_ch: int):
    """bytes→bytes: int16 PCM устройства → канонический формат дорожки
    (формат её первого устройства, с которым запущен ffmpeg на всю запись).
    На happy path (устройство не менялось) — passthrough без audioop.
    Стерео↔моно через audioop, у >2 каналов берутся первые два (полного
    даунмикса N каналов нет — экзотика), ресемплинг — audioop.ratecv
    (линейный, без ФНЧ — на понижении возможен алиасинг; основной путь
    ресемплит ffmpeg) с переносом состояния между callback'ами.
    IMPORTANT: audioop удалён в Python 3.13 (в 3.12 deprecated) — при
    апгрейде Python заменить; happy path от него не зависит."""
    if src_rate == dst_rate and src_ch == dst_ch:
        return lambda data: data
    import array

    # audioop нужен не всякой конвертации (первые два канала из N режет array):
    # не импортируем зря — на Python 3.13 модуля нет вовсе.
    if src_rate != dst_rate or min(src_ch, 2) != dst_ch:
        import audioop

    state = None

    def convert(data: bytes) -> bytes:
        nonlocal state
        ch = src_ch
        if ch > 2:
            samples = array.array("h", data)
            frames = len(samples) // ch
            stereo = array.array("h", bytes(4 * frames))
            stereo[0::2] = samples[0 : frames * ch : ch]
            stereo[1::2] = samples[1 : frames * ch : ch]
            data, ch = stereo.tobytes(), 2
        if ch == 2 and dst_ch == 1:
            data = audioop.tomono(data, 2, 0.5, 0.5)
        elif ch == 1 and dst_ch == 2:
            data = audioop.tostereo(data, 2, 1, 1)
        if src_rate != dst_rate:
            data, state = audioop.ratecv(data, 2, dst_ch, src_rate, dst_rate, state)
        return data

    return convert


def _peak(data: bytes, stride: int = LEVEL_STRIDE) -> float:
    """Пиковый уровень 0..1 из int16-PCM по каждому stride-му сэмплу.

    Зовётся из аудио-callback'а, поэтому: без аллокаций (memoryview.cast —
    представление, а не копия), без numpy (минимальная установка для записи на
    ноутбуке — только pyaudiowpatch, см. README) и без лока. Разреженная
    выборка достаточна: метру в UI нужен масштаб, а не измерение.
    """
    if len(data) % 2:  # PCM16 всегда чётный, но битый буфер не должен падать
        data = data[:-1]
    samples = memoryview(data).cast("h")
    peak = 0
    for i in range(0, len(samples), stride):
        value = samples[i]
        if value < 0:
            value = -value
        if value > peak:
            peak = value
    return peak / 32768.0


class _RecordLog:
    """Журнал записи — record.log рядом с дорожками, с дублированием в stdout.
    Файл нужен потому, что под meet-tray (pythonw) stdout'а нет и вся
    диагностика вотчдога иначе пропадает. Ошибки самого лога глотаются —
    журнал не должен ронять запись.

    Строки дублируются в шину событий (`kind: "log"`): UI показывает журнал
    живьём, не вычитывая файл по таймеру."""

    def __init__(self, out_dir: Path, bus=None) -> None:
        self.bus = bus if bus is not None else events.EventBus()
        try:
            # buffering=1: строка на диске сразу — при жёстком убийстве
            # процесса теряется максимум последняя
            self._f = open(
                out_dir / "record.log", "a", encoding="utf-8", buffering=1
            )
        except OSError:
            self._f = None

    def __call__(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)  # под pythonw молча уходит в никуда — это ок
        self.bus.emit(events.LOG, text=msg)
        if self._f is not None:
            try:
                self._f.write(line + "\n")
            except OSError:
                pass

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            except OSError:
                pass
            self._f = None


class _DefaultEndpoints:
    """ID дефолтных аудио-endpoint'ов Windows (вывод, ввод) через COM
    IMMDeviceEnumerator. Нужен потому, что PortAudio смену дефолта не видит:
    его список устройств обновляется только «холодной» инициализацией, а у
    живого инстанса заморожен. Любая ошибка COM переводит объект в сломанное
    состояние: ids() → None, вотчдог остаётся с детекцией по смерти стрима."""

    _CLSID_ENUM = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    _IID_ENUM = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    _RPC_E_CHANGED_MODE = -2147417850

    def __init__(self) -> None:
        self._enum = None
        self._uninit = False
        try:
            hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # APARTMENTTHREADED
            if hr in (0, 1):  # S_OK / S_FALSE — CoUninitialize за нами
                self._uninit = True
            elif hr != self._RPC_E_CHANGED_MODE:  # иной режим в потоке — тоже ок
                return
            enum = ctypes.c_void_p()
            hr = ctypes.windll.ole32.CoCreateInstance(
                self._guid(self._CLSID_ENUM), None, 1,  # CLSCTX_INPROC_SERVER
                self._guid(self._IID_ENUM), ctypes.byref(enum),
            )
            if hr == 0:
                self._enum = enum
        except Exception:
            self._enum = None

    @staticmethod
    def _guid(s: str):
        buf = (ctypes.c_ubyte * 16)()
        ctypes.windll.ole32.CLSIDFromString(s, buf)
        return buf

    @staticmethod
    def _method(obj, index: int, *argtypes):
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
        vtbl = ctypes.cast(
            obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        return proto(vtbl[index])

    def _default_id(self, flow: int) -> "str | None":
        dev = ctypes.c_void_p()
        get_default = self._method(  # IMMDeviceEnumerator::GetDefaultAudioEndpoint
            self._enum, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        )
        if get_default(self._enum, flow, 1, ctypes.byref(dev)) != 0 or not dev:
            return None  # устройства этой роли сейчас нет (eMultimedia)
        try:
            pstr = ctypes.c_wchar_p()
            get_id = self._method(dev, 5, ctypes.POINTER(ctypes.c_wchar_p))
            if get_id(dev, ctypes.byref(pstr)) != 0:  # IMMDevice::GetId
                return None
            device_id = pstr.value
            ctypes.windll.ole32.CoTaskMemFree(pstr)
            return device_id
        finally:
            self._method(dev, 2)(dev)  # IUnknown::Release

    def ids(self) -> "tuple | None":
        """(render_id | None, capture_id | None) или None, если COM недоступен."""
        if self._enum is None:
            return None
        try:
            return (self._default_id(0), self._default_id(1))  # eRender, eCapture
        except Exception:
            self._enum = None
            return None

    def close(self) -> None:
        try:
            if self._enum is not None:
                self._method(self._enum, 2)(self._enum)
            if self._uninit:
                ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass
        self._enum = None


class _Track:
    """Дорожка записи, переживающая смену аудио-устройства (BT-наушники ушли
    к телефону при звонке и вернулись — типичный сценарий обрыва).

    ffmpeg-энкодер один на всю запись: вход зафиксирован форматом первого
    устройства (rate и ≤2 канала); звук другого устройства приводится к нему
    в callback (_make_converter), а пауза между устройствами и периоды без
    данных заполняются тишиной по стенным часам — таймлайны sys/mic не
    разъезжаются, и слияние дорожек при транскрибации (interleave по
    абсолютным таймкодам) остаётся корректным."""

    def __init__(self, fname: str, pick_device, role: int, out_dir: Path,
                 log, bus=None) -> None:
        self.fname = fname
        self.pick = pick_device  # PyAudio -> device info (или исключение)
        self.role = role  # индекс в _DefaultEndpoints.ids(): 0 вывод, 1 ввод
        self.path = out_dir / fname
        self.log = log  # журнал записи (_RecordLog)
        self.bus = bus if bus is not None else events.EventBus()
        self.level = 0.0  # пик с прошлого опроса, см. take_level()
        self.stream = None
        self.device_name = None
        self.src_rate = None
        self.src_channels = None
        self.writer = None
        self.rate = None  # канонический формат = у первого устройства
        self.channels = None
        self.bytes_written = 0  # канонических байт ушло в ffmpeg
        self.started = 0.0
        self._lock = threading.Lock()  # callback против паддинга из вотчдога
        self._waiting = False
        self._stalled = False

    def first_open(self, p) -> None:
        """Первый запуск: устройство обязано существовать (иначе исключение)."""
        dev = self.pick(p)
        self.rate = int(dev["defaultSampleRate"])
        self.channels = min(2, max(1, int(dev["maxInputChannels"])))
        self.writer = OpusWriter(self.path, self.channels, self.rate)
        self.started = time.monotonic()
        self._open(p, dev)

    def reopen(self, p) -> bool:
        """Открыть дорожку заново после перезапуска; неудача — ждать дальше."""
        try:
            dev = self.pick(p)
            self._open(p, dev)
        except Exception:
            if not self._waiting:
                self._waiting = True
                self._log("устройство недоступно — жду (пауза уйдёт в тишину)")
                self.bus.emit(events.RECORD_WAITING, track=self.fname)
            return False
        self._waiting = False
        self._log(
            f"запись возобновлена: {self.device_name} "
            f"({self.src_rate} Hz, {self.src_channels} ch)"
        )
        self.bus.emit(
            events.RECORD_DEVICE, track=self.fname, device=self.device_name,
            rate=self.src_rate, channels=self.src_channels, resumed=True,
        )
        return True

    def alive(self) -> bool:
        if self.stream is None:
            return False
        try:
            return self.stream.is_active()
        except Exception:
            return False

    def tick_pad(self) -> None:
        """Файл держится у стенных часов даже без переоткрытий: живой, но
        молчащий стрим (loopback без системного звука, заглохший микрофон)
        и ожидание устройства доливаются тишиной — реплики после простоя
        не уезжают, а внезапная смерть процесса не оставляет файл короче."""
        if self.writer is None:
            return
        frame = 2 * self.channels
        gap = (time.monotonic() - self.started) - self.bytes_written / (
            frame * self.rate
        )
        if gap <= TICK_PAD_S:
            return
        if not self._stalled:
            self._stalled = True
            self._log(f"нет данных ~{gap:.0f} с — дополняю тишиной")
            self.bus.emit(
                events.RECORD_SILENCE, track=self.fname, gap_s=round(gap, 1)
            )
        self._pad_silence()

    def take_level(self) -> float:
        """Пик уровня с прошлого опроса — и сброс.

        Пишет аудио-callback, читает поток вотчдога; обе операции атомарны в
        CPython, а лок здесь стоил бы дороже, чем неточность в один буфер на
        границе опроса. Пик, а не мгновенное значение: при опросе дважды в
        секунду мгновенный отсчёт пропускал бы речь между тактами."""
        level, self.level = self.level, 0.0
        return level

    def close_stream(self) -> None:
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.stop_stream()
        except Exception:
            pass  # стрим на пропавшем устройстве может не остановиться штатно
        try:
            stream.close()
        except Exception:
            pass

    def close(self) -> None:
        self.close_stream()
        if self.writer is not None:
            try:
                self._pad_silence()  # хвост до момента остановки
            finally:
                self.writer.close()  # ffmpeg финализируется даже при сбое паддинга

    def _open(self, p, dev) -> None:
        src_rate = int(dev["defaultSampleRate"])
        src_ch = max(1, int(dev["maxInputChannels"]))
        convert = _make_converter(src_rate, src_ch, self.rate, self.channels)

        def cb(in_data, frame_count, time_info, status):
            try:
                data = convert(in_data)
                with self._lock:
                    self.writer.write(data)
                    self.bytes_written += len(data)
                self._stalled = False
                peak = _peak(data)
                if peak > self.level:
                    self.level = peak
            except Exception as e:
                self._log(f"ошибка в аудио-callback: {e!r} — стрим будет переоткрыт")
                return (None, pyaudio.paAbort)
            return (None, pyaudio.paContinue)

        # start=False: тишина дописывается ДО старта стрима, иначе латентность
        # переоткрытия (энумерация устройств ~0.5 с) выпадала бы из таймлайна.
        stream = p.open(
            format=pyaudio.paInt16,
            channels=src_ch,
            rate=src_rate,
            input=True,
            input_device_index=int(dev["index"]),
            frames_per_buffer=1024,
            stream_callback=cb,
            start=False,
        )
        self._pad_silence()
        stream.start_stream()
        self.device_name = dev["name"]
        self.src_rate, self.src_channels = src_rate, src_ch
        self.stream = stream  # последним: до этой строки дорожка «не открыта»

    def _pad_silence(self) -> None:
        """Дописать тишину до стенных часов. Кусками ≤1 с и под локом: пауза
        может быть длинной (сон машины), а callback возобновиться посреди
        паддинга — дефицит пересчитывается на каждом куске."""
        frame = 2 * self.channels
        while True:
            with self._lock:
                written_s = self.bytes_written / (frame * self.rate)
                gap = (time.monotonic() - self.started) - written_s
                if gap <= MIN_GAP_S:
                    return
                n = min(int(gap * self.rate), self.rate)
                pad = b"\x00" * (n * frame)
                self.writer.write(pad)
                self.bytes_written += len(pad)

    def _log(self, msg: str) -> None:
        self.log(f"{self.fname}: {msg}")


def _default_mic(p: "pyaudio.PyAudio") -> dict:
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    return p.get_device_info_by_index(wasapi["defaultInputDevice"])


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


class _Session:
    """Обе дорожки и единственный PyAudio-инстанс. Инстанс один принципиально:
    список устройств PortAudio обновляется только «холодной» инициализацией
    (пока жив любой инстанс, новые получают старый список — Pa_Initialize
    считает ссылки), поэтому перезапуск всегда полный: закрыть оба стрима →
    terminate → свежий PyAudio → переоткрыть дорожки."""

    def __init__(self, out_dir: Path, bus=None) -> None:
        self.p = None
        self.out_dir = out_dir
        self.bus = bus if bus is not None else events.EventBus()
        self.log = _RecordLog(out_dir, self.bus)
        self.endpoints = _DefaultEndpoints()
        self.ids = None  # ID дефолтных endpoint'ов на момент последнего запуска
        self.last_restart = 0.0
        self.retry_wait = RETRY_S  # растёт при безуспешных ретраях (backoff)
        self.tracks = (
            _Track("sys.opus", _find_loopback, 0, out_dir, self.log, self.bus),
            _Track("mic.opus", _default_mic, 1, out_dir, self.log, self.bus),
        )

    def start(self) -> None:
        self.log(f"запись начата {datetime.now():%Y-%m-%d}, pid {os.getpid()}")
        self.p = pyaudio.PyAudio()
        self.ids = self.endpoints.ids()
        if self.ids is None:
            self.log("COM недоступен — смену дефолтного устройства не отслеживаю")
        for t in self.tracks:
            t.first_open(self.p)
            self.log(
                f"{t.fname}: {t.device_name} "
                f"({t.src_rate} Hz, {t.src_channels} ch)"
            )
        self.bus.emit(
            events.RECORD_STARTED,
            folder=str(self.out_dir),
            pid=os.getpid(),
            tracks=[
                {"track": t.fname, "device": t.device_name,
                 "rate": t.src_rate, "channels": t.src_channels}
                for t in self.tracks
            ],
        )

    def levels(self) -> dict:
        """Уровни дорожек с прошлого опроса — для метра в UI."""
        return {t.fname: t.take_level() for t in self.tracks}

    def tick(self) -> None:
        for t in self.tracks:
            try:
                t.tick_pad()
            except Exception as e:
                t._log(f"пауза не дописана: {e!r}")
        ids = self.endpoints.ids()
        if self.ids is None:
            self.ids = ids  # COM не ответил на старте — базлайн с первого такта
        now = time.monotonic()
        changed = ids is not None and self.ids is not None and ids != self.ids
        died = any(t.stream is not None and not t.alive() for t in self.tracks)
        waiting = [t for t in self.tracks if t.stream is None]
        # Ретрай ждущих дорожек — только когда для их роли снова есть дефолтное
        # устройство (или COM сломан и проверить нечем), и с backoff'ом: каждый
        # перезапуск рвёт и здоровую дорожку (~0.5 с уходит в тишину), поэтому
        # устойчиво неоткрывающееся устройство не должно дёргать её каждые 5 с.
        retry = bool(waiting) and now - self.last_restart >= self.retry_wait and (
            ids is None or any(ids[t.role] is not None for t in waiting)
        )
        if not (changed or died or retry):
            return
        if now - self.last_restart < RESTART_MIN_S:
            return
        if changed:
            reason = "сменилось дефолтное аудио-устройство"
        elif died:
            reason = "дорожка остановилась (устройство пропало?)"
        else:
            reason = None  # тихий ретрай ждущей дорожки
        if reason:
            self.bus.emit(events.RECORD_DEVICE, reason=reason, restart=True)
        self._restart(ids, reason)

    def close(self) -> None:
        for t in self.tracks:
            try:
                t.close()
            except Exception as e:
                self.log(f"{t.fname}: закрытие: {e!r}")
        self._terminate()
        self.endpoints.close()
        self.log("запись остановлена штатно")  # нет этой строки → процесс убили
        # Длительность — от старта самой ранней открывшейся дорожки: столько и
        # длится файл (паузы долиты тишиной). Дорожка, которая не открылась,
        # держит started == 0.0 и в расчёт не идёт — иначе в длительность попало
        # бы монотонное время машины.
        started = [t.started for t in self.tracks if t.started]
        self.bus.emit(
            events.RECORD_STOPPED,
            folder=str(self.out_dir),
            duration_s=round(time.monotonic() - min(started), 1) if started else 0.0,
        )
        self.log.close()

    def _restart(self, ids, reason) -> None:
        self.last_restart = time.monotonic()
        if reason:
            self.log(f"{reason} — перезапускаю дорожки")
        for t in self.tracks:
            t.close_stream()
        self._terminate()
        try:
            self.p = pyaudio.PyAudio()  # холодный старт: свежий список устройств
        except Exception as e:
            self.log(f"PyAudio не инициализировался: {e!r}")
        if self.p is not None:
            for t in self.tracks:
                t.reopen(self.p)
            if ids is not None:
                self.ids = ids
        if self.p is not None and all(t.stream is not None for t in self.tracks):
            self.retry_wait = RETRY_S
        else:
            self.retry_wait = min(max(self.retry_wait, RETRY_S) * 2, RETRY_MAX_S)

    def _terminate(self) -> None:
        if self.p is None:
            return
        p, self.p = self.p, None
        try:
            p.terminate()
        except Exception:
            # terminate() закрывает оставшиеся стримы перед Pa_Terminate; битый
            # стрим (устройство пропало) роняет его до Pa_Terminate — счётчик
            # инициализации утёк бы, и список устройств замёрз бы до конца
            # процесса. Выбрасываем реестр стримов и повторяем.
            try:
                p._streams.clear()
                p.terminate()
            except Exception as e:
                self.log(f"PyAudio.terminate: {e!r}")


def record(out_root: str, stop_event: "threading.Event | None" = None,
           bus=None) -> Path:
    """Записать встречу двумя дорожками. `bus` — шина событий для UI: без неё
    всё работает как раньше, только события уходят в никуда."""
    out_dir = Path(out_root) / datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = _acquire_lock(Path(out_root), out_dir)

    session = _Session(out_dir, bus)
    # Машинный двойник record.log рядом с дорожками: по нему экран диагностики
    # разбирает запись после того, как она кончилась. Уровни в файл не идут.
    sink = events.JsonlSink(out_dir / "events.jsonl")
    unsubscribe = session.bus.subscribe(sink)
    try:
        session.start()
        print(f"Запись идёт... Остановить: Ctrl+C. Папка: {out_dir}")
        try:
            while not (stop_event and stop_event.is_set()):
                time.sleep(POLL_S)
                try:
                    session.tick()
                except Exception as e:  # вотчдог не должен ронять запись
                    session.log(f"вотчдог: {e!r}")
                session.bus.emit(events.RECORD_LEVEL, levels=session.levels())
        except KeyboardInterrupt:
            pass
    finally:
        session.close()
        unsubscribe()
        sink.close()
        lock.unlink(missing_ok=True)

    print(f"\nЗапись остановлена: {out_dir}")
    print(f"Транскрибировать: meet transcribe \"{out_dir}\"")
    return out_dir
