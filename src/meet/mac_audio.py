"""Звук на macOS в обличье PyAudio: запись (`recorder`) не знает, на какой она ОС.

На Windows запись идёт через pyaudiowpatch (PortAudio + WASAPI loopback). На
macOS у PortAudio нет loopback'а, а сам PyAudio без Homebrew не ставится,
поэтому здесь — та же поверхность (`PyAudio()`, `open(..., stream_callback=)`,
`paContinue`/`paAbort`, словари устройств с `maxInputChannels` и
`defaultSampleRate`) поверх двух источников:

* микрофон и любые устройства ввода — sounddevice (колесо несёт PortAudio
  для macOS);
* системный звук — помощник `meet-audiotap` (ScreenCaptureKit), подпроцесс,
  отдающий PCM в stdout (`meet.audiotap`). В списке устройств он —
  виртуальное устройство SYSTEM_AUDIO_NAME с индексом TAP_INDEX.

Запасной путь для звука собеседников — виртуальное устройство ввода
(BlackHole): его выбирают в настройках звука как «устройство вывода», и
дорожка пишется с него как с обычного устройства ввода.

IMPORTANT: как и у PyAudio, инициализация PortAudio одна на процесс
(`_Session` в recorder); список устройств обновляется только после
`terminate()` и нового `PyAudio()`.
"""

import queue
import subprocess
import threading
import time
from collections import deque

from meet import audiotap

paInt16 = 8
paContinue = 0
paComplete = 1
paAbort = 2
# Совместимость с кодом, который спрашивает WASAPI: на macOS такого API нет.
paWASAPI = 13

TAP_INDEX = -100
SYSTEM_AUDIO_NAME = "Системный звук (ScreenCaptureKit)"
TAP_RATE = 48000
TAP_CHANNELS = 1
HANDSHAKE_TIMEOUT_S = 10.0
STOP_TIMEOUT_S = 3.0
# Помощник жив, а байтов нет столько секунд — он завис: перезапустить.
STALL_S = 10.0
# stderr помощника: в журнал записи — не чаще раза в столько секунд,
# последние строки — для текста ошибки.
STDERR_LOG_EVERY_S = 5.0
STDERR_TAIL = 20
# Запуск помощника — точка подмены для тестов.
_popen = subprocess.Popen


def _sd():
    import sounddevice

    return sounddevice


def tap_device() -> dict:
    """Виртуальное устройство «системный звук» — словарь как у PyAudio."""
    return {
        "index": TAP_INDEX,
        "name": SYSTEM_AUDIO_NAME,
        "maxInputChannels": TAP_CHANNELS,
        "maxOutputChannels": 0,
        "defaultSampleRate": float(TAP_RATE),
        "hostApi": -1,
        "isLoopbackDevice": True,
    }


def _device_info(raw, index: int) -> dict:
    """Устройство sounddevice → словарь PyAudio."""
    return {
        "index": int(raw.get("index", index)),
        "name": str(raw.get("name", "")),
        "maxInputChannels": int(raw.get("max_input_channels", 0)),
        "maxOutputChannels": int(raw.get("max_output_channels", 0)),
        "defaultSampleRate": float(raw.get("default_samplerate", 48000.0)),
        "hostApi": int(raw.get("hostapi", 0)),
        "isLoopbackDevice": False,
    }


class PyAudio:
    """PyAudio поверх sounddevice. `sd` — модуль sounddevice (тесты подменяют)."""

    _first = True

    def __init__(self, sd=None) -> None:
        self._sd = sd if sd is not None else _sd()
        self._streams: set = set()
        # Журнал записи (`recorder._RecordLog`): сюда помощник системного звука
        # пишет свои сообщения и «завис — перезапускаю». Задаёт recorder.
        self.log = None
        # Первый экземпляр: sounddevice уже инициализировал PortAudio при
        # импорте. Следующие — холодный старт после terminate(): свежий список.
        if not PyAudio._first:
            try:
                self._sd._initialize()
            except Exception:
                pass
        PyAudio._first = False

    # --- устройства -------------------------------------------------------

    def get_device_count(self) -> int:
        return len(self._sd.query_devices())

    def get_device_info_by_index(self, index: int) -> dict:
        if index == TAP_INDEX:
            return tap_device()
        return _device_info(self._sd.query_devices(index), index)

    def devices(self) -> list[dict]:
        return [_device_info(raw, i) for i, raw in enumerate(self._sd.query_devices())]

    def get_default_input_device_info(self) -> dict:
        index = self._sd.default.device[0]
        if index is None or int(index) < 0:
            raise OSError("Нет устройства ввода по умолчанию")
        return self.get_device_info_by_index(int(index))

    def get_default_output_device_info(self) -> dict:
        index = self._sd.default.device[1]
        if index is None or int(index) < 0:
            raise OSError("Нет устройства вывода по умолчанию")
        return self.get_device_info_by_index(int(index))

    # --- потоки -----------------------------------------------------------

    def open(self, format=paInt16, channels=1, rate=48000, input=True,
             input_device_index=None, frames_per_buffer=1024,
             stream_callback=None, start=True, **_ignored):
        if format != paInt16 or not input or stream_callback is None:
            raise ValueError("поддерживается только ввод int16 с callback")
        if input_device_index == TAP_INDEX:
            stream = TapStream(int(rate), int(channels), stream_callback,
                               int(frames_per_buffer), log=self.log)
        else:
            stream = DeviceStream(self._sd, input_device_index, int(rate),
                                  int(channels), stream_callback, int(frames_per_buffer))
        self._streams.add(stream)
        if start:
            stream.start_stream()
        return stream

    def terminate(self) -> None:
        for stream in list(self._streams):
            stream.close()
        self._streams.clear()
        try:
            self._sd._terminate()
        except Exception:
            pass


class DeviceStream:
    """Устройство ввода через sounddevice.RawInputStream, callback как у PyAudio."""

    def __init__(self, sd, device, rate, channels, callback, frames) -> None:
        self._sd = sd

        def cb(indata, frame_count, time_info, status):
            result = callback(bytes(indata), frame_count, time_info, status)
            if result and result[1] == paAbort:
                raise sd.CallbackAbort

        self._stream = sd.RawInputStream(
            samplerate=rate, channels=channels, dtype="int16", device=device,
            blocksize=frames, callback=cb,
        )

    def start_stream(self) -> None:
        self._stream.start()

    def stop_stream(self) -> None:
        self._stream.stop()

    def close(self) -> None:
        self._stream.close()

    def is_active(self) -> bool:
        return bool(self._stream.active)


class TapStream:
    """Системный звук от `meet-audiotap --stream`: читатель stdout в своём
    потоке зовёт callback кусками по `frames` кадров.

    Захват стартует в `start_stream()` и ждёт рукопожатия: отказ в
    разрешении «Запись экрана» узнаётся сразу, по коду выхода, — TapError с
    понятным текстом, а не тихая дорожка из тишины.

    stderr помощника читается всё время, пока он жив (`_drain`): непрочитанный
    канал заполнился бы за минуту частых сообщений, помощник встал бы на
    записи в stderr, а дорожка тихо писала бы тишину до конца встречи.
    Строки уходят в журнал записи (`log`) не чаще раза в STDERR_LOG_EVERY_S.

    Помощник жив, а звука нет дольше STALL_S (он сам дописывает тишину, когда
    системный звук молчит, — поток не пустеет) — `is_active()` отвечает False:
    вотчдог записи перезапустит помощника, в журнале — STALL_NOTICE."""

    def __init__(self, rate: int, channels: int, callback, frames: int,
                 popen=None, helper: "str | None" = None, log=None) -> None:
        self.rate, self.channels = rate, channels
        self._callback = callback
        self._frames = max(1, frames)
        self._popen = popen or _popen
        self._helper = helper
        self._log = log
        self._proc = None
        self._reader = None
        self._drainer = None
        self._stopping = threading.Event()
        self.stderr_tail: "deque[str]" = deque(maxlen=STDERR_TAIL)
        self.stderr_lines = 0
        self._stderr_logged_at: "float | None" = None
        self._stderr_skipped = 0
        self._last_data = 0.0
        self.stalled = False
        self.exit_code: "int | None" = None
        self.notice: "str | None" = None

    def start_stream(self) -> None:
        helper = self._helper or audiotap.helper_path()
        if not helper:
            raise audiotap.TapError(audiotap.MISSING_NOTICE)
        self._stopping.clear()
        self.stalled = False
        self._proc = self._popen(
            audiotap.stream_command(helper, self.rate, self.channels),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._drainer = threading.Thread(target=self._drain, name="meet-audiotap-stderr",
                                         daemon=True)
        self._drainer.start()
        line = self._handshake()
        try:
            fmt = audiotap.parse_handshake(line)
        except audiotap.TapError:
            self._kill()
            raise
        if (fmt["rate"], fmt["channels"]) != (self.rate, self.channels):
            self._kill()
            raise audiotap.TapError(
                f"Помощник записи системного звука отдаёт {fmt['rate']} Гц, "
                f"{fmt['channels']} кан. вместо {self.rate} Гц, {self.channels} кан.")
        self._last_data = time.monotonic()
        self._reader = threading.Thread(target=self._pump, name="meet-audiotap",
                                        daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        """stderr помощника — в хвост (для текста ошибки) и в журнал, реже."""
        stream = self._proc.stderr
        while True:
            try:
                raw = stream.readline()
            except (OSError, ValueError):
                return
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            self.stderr_lines += 1
            self.stderr_tail.append(text)
            self._log_stderr(text)

    def _log_stderr(self, text: str) -> None:
        if self._log is None:
            return
        now = time.monotonic()
        if self._stderr_logged_at is not None and now - self._stderr_logged_at < STDERR_LOG_EVERY_S:
            self._stderr_skipped += 1
            return
        skipped, self._stderr_skipped = self._stderr_skipped, 0
        self._stderr_logged_at = now
        more = f" (и ещё {skipped} сообщ.)" if skipped else ""
        try:
            self._log(f"meet-audiotap: {text[:200]}{more}")
        except Exception:
            pass

    def _handshake(self) -> bytes:
        """Первая строка stdout, не дольше HANDSHAKE_TIMEOUT_S. Помощник вышел
        раньше — TapError с текстом по коду выхода."""
        box: "queue.Queue[bytes]" = queue.Queue()
        proc = self._proc
        threading.Thread(target=lambda: box.put(proc.stdout.readline()),
                         daemon=True).start()
        try:
            line = box.get(timeout=HANDSHAKE_TIMEOUT_S)
        except queue.Empty:
            self._kill()
            raise audiotap.TapError(
                "Помощник записи системного звука не ответил — запись системного звука недоступна"
            ) from None
        if line:
            return line
        code = self._wait_exit()
        if self._drainer is not None:
            self._drainer.join(timeout=STOP_TIMEOUT_S)  # stderr закрылся вместе с процессом
        self.exit_code = code
        self.notice = audiotap.notice_for_exit(code, " ".join(self.stderr_tail))
        raise audiotap.TapError(self.notice, code)

    def _wait_exit(self) -> "int | None":
        try:
            return self._proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._kill()
            return None

    def _pump(self) -> None:
        size = self._frames * 2 * self.channels
        stdout = self._proc.stdout
        while not self._stopping.is_set():
            try:
                data = stdout.read(size)
            except (OSError, ValueError):
                break
            if not data:
                break
            data = data[: len(data) - len(data) % (2 * self.channels)]
            if not data:
                continue
            self._last_data = time.monotonic()
            try:
                result = self._callback(data, len(data) // (2 * self.channels), None, 0)
            except Exception:
                result = (None, paAbort)
            if result and result[1] == paAbort:
                break
        if not self._stopping.is_set():
            # Помощник замолчал сам (отозвали разрешение, сбой): дорожка
            # увидит is_active() == False и переоткроется вотчдогом.
            code = self._proc.poll()
            if code is not None:
                self.exit_code = code
                self.notice = audiotap.notice_for_exit(code, " ".join(self.stderr_tail))

    def is_active(self) -> bool:
        if not (self._proc is not None and self._proc.poll() is None
                and self._reader is not None and self._reader.is_alive()):
            return False
        if time.monotonic() - self._last_data > STALL_S:
            if not self.stalled:
                self.stalled = True
                self.notice = audiotap.STALL_NOTICE
                if self._log is not None:
                    try:
                        self._log(audiotap.STALL_NOTICE)
                    except Exception:
                        pass
            return False
        return True

    def stop_stream(self) -> None:
        self._stopping.set()
        proc = self._proc
        if proc is None:
            return
        try:
            proc.stdin.close()  # помощник выходит по концу stdin
        except Exception:
            pass
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._kill()

    def close(self) -> None:
        self.stop_stream()
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=STOP_TIMEOUT_S)

    def _kill(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=STOP_TIMEOUT_S)
        except Exception:
            pass


# --- выбор устройств для recorder ---------------------------------------------


def default_mic(p: PyAudio) -> dict:
    return p.get_default_input_device_info()


def system_audio(p: PyAudio) -> dict:
    """Звук собеседников по умолчанию — помощник ScreenCaptureKit. Его нет —
    исключение с подсказкой про виртуальное устройство."""
    if not audiotap.helper_path():
        raise audiotap.TapError(audiotap.MISSING_NOTICE)
    return tap_device()


def find_input(p: PyAudio, name: str) -> "dict | None":
    """Устройство ввода по имени (микрофон или BlackHole); имя виртуального
    «системного звука» — помощник."""
    if name == SYSTEM_AUDIO_NAME:
        return tap_device() if audiotap.helper_path() else None
    for dev in p.devices():
        if dev["name"] == name and dev["maxInputChannels"] > 0:
            return dev
    return None


def list_devices(p: PyAudio) -> dict:
    """Для настроек звука: микрофоны и источники звука собеседников. На macOS
    «устройство вывода» — откуда брать звук собеседников: системный звук
    (помощник, по умолчанию) или любое устройство ввода вроде BlackHole."""
    try:
        default_in = p.get_default_input_device_info()["name"]
    except Exception:
        default_in = None
    inputs: list[str] = []
    for dev in p.devices():
        if dev["maxInputChannels"] > 0 and dev["name"] not in inputs:
            inputs.append(dev["name"])
    outputs = [{"name": SYSTEM_AUDIO_NAME, "default": True}] if audiotap.helper_path() else []
    outputs += [{"name": n, "default": False} for n in inputs]
    return {
        "inputs": [{"name": n, "default": n == default_in} for n in inputs],
        "outputs": outputs,
    }
