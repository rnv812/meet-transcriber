"""Помощник `meet-audiotap` (macOS): системный звук и «кто держит микрофон».

На Windows системный звук пишется loopback'ом WASAPI, а занятость микрофона
видна в реестре. На macOS ни того, ни другого нет, поэтому оба сигнала даёт
маленькая программа на Swift (`mac/audiotap/main.swift`), которая лежит в
ресурсах приложения рядом с ffmpeg:

* `meet-audiotap --stream --rate 48000 --channels 1 [--exclude-pid N]
  [--source process_tap]` — ScreenCaptureKit (macOS 13+, разрешение «Запись
  экрана»), только звук, без звука приложения с процессом N (оболочка Meet).
  С `--source process_tap` (macOS 14.4+, выключатель `recording.mac_capture`)
  — Core Audio process tap: глобальный перехват без процессов Meet в частном
  aggregate-устройстве на встроенном выходе, разрешение «Запись системного
  звука», без значка записи экрана. Первая строка stdout — рукопожатие JSON
  (`{"meet_audiotap": 1, "rate", "channels", "format": "s16le"}`), дальше —
  сырой PCM s16le до закрытия stdin или SIGTERM, у обоих источников одинаково;
* `meet-audiotap --check-tap --seconds 5 [--exclude-pid N]` — проверка
  process tap при включении выключателя: 5 с записи и одна строка JSON
  `{"meet_audiotap": 1, "check": "process_tap", "frames", "peak"}` (пик —
  по модулю s16). API проверки разрешения у macOS нет, а без разрешения
  буферы приходят нулями, поэтому решает Python (`check_process_tap`):
  ненулевой пик — звук есть, одни нули — разрешения, вероятно, нет;
* `meet-audiotap --mic-users` — одна строка JSON: процессы, которые сейчас
  слушают микрофон (и играют звук), по CoreAudio (macOS 14+; раньше —
  `supported: false`); у каждого — `input_devices`, UID устройств ввода,
  которые он слушает (прежний помощник их не сообщает);
* `meet-audiotap --watch-devices` — пока открыт stdin: строка JSON сразу и на
  каждую перемену — устройство ввода по умолчанию (`default_input`: uid, имя,
  тип подключения `transport`, `alive`) и все устройства ввода (`inputs`).
  Повод — `watch`: `start`, `default` (сменилось по умолчанию), `devices`
  (список), `alive` (устройство по умолчанию пропало или вернулось). Запись
  следит по нему за системным микрофоном (`mac_audio.DeviceWatcher`);
* `meet-audiotap --self-test` — версия и возможности, без разрешений (CI);
* `meet-audiotap --preflight` — дано ли разрешение «Запись экрана» (код 0 или
  77), без захвата и без системного запроса: его дёшево спрашивать посреди
  записи, ничего не трогая.
* `meet-audiotap --mic-status` — одна строка JSON `{"microphone": …}`:
  разрешение «Микрофон» (`granted`, `denied`, `notDetermined`, `restricted`),
  без системного запроса;
* `meet-audiotap --request-mic` — системный запрос «Микрофон» (ждёт ответа
  человека; уже решённое macOS не переспрашивает), та же строка с итогом;
* `meet-audiotap --request-screen` — системный запрос «Запись экрана», строка
  `{"screen": "granted"|"denied"}` (macOS применит разрешение только после
  перезапуска приложения — тогда «denied»).

Помощника запускает цепочка Meet.app → резидент, поэтому запросы и ответы
macOS относит к Meet. Прежний помощник режимов разрешений не знает (код 64) —
ответ «неизвестно» (None).

Коды выхода — `EXIT_*` ниже; отказ в разрешении узнаётся по коду, а не по
тексту: текст системной ошибки зависит от языка macOS.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

HELPER = "meet-audiotap"
ENV_OVERRIDE = "MEET_AUDIOTAP"
PROTOCOL = 1

EXIT_OK = 0
EXIT_USAGE = 64  # EX_USAGE
EXIT_UNSUPPORTED = 69  # EX_UNAVAILABLE: macOS старше 13 — ScreenCaptureKit нет
EXIT_FAILED = 70  # EX_SOFTWARE
EXIT_PERMISSION = 77  # EX_NOPERM: нет разрешения «Запись экрана»

# Источник системного звука (`recording.mac_capture`, `--source`).
SOURCE_SCREEN = "screencapturekit"
SOURCE_PROCESS_TAP = "process_tap"
SOURCES = (SOURCE_SCREEN, SOURCE_PROCESS_TAP)
# Process tap — с macOS 14.2, надёжно — с 14.4 (раньше выбор скрыт).
PROCESS_TAP_MIN_MACOS = (14, 4)

# Проверка process tap (`--check-tap`): сколько секунд пишет помощник и сколько
# ждать его целиком (старт перехвата и системный вопрос о разрешении — сверху).
CHECK_SECONDS = 5
CHECK_TIMEOUT_S = 20.0
# Итог проверки (`check_process_tap()["result"]`).
CHECK_SOUND = "sound"  # ненулевой звук — разрешение есть, выключатель включается
CHECK_SILENCE = "silence"  # одни нули — разрешения, вероятно, нет (или звук не играл)
CHECK_DENIED = "denied"  # CoreAudio отказал сразу
CHECK_UNSUPPORTED = "unsupported"  # macOS старше 14.4 или прежний помощник
CHECK_FAILED = "failed"  # помощника нет, завис, ответил не по протоколу
# Раздел «Системных настроек», который окно открывает подсказкой
# (`open_privacy_pane` оболочки).
SYSTEM_AUDIO_PANE = "system_audio"
CHECK_SOUND_NOTICE = "Звук есть — Meet будет писать звук собеседников без «Записи экрана»."
CHECK_SILENCE_NOTICE = (
    "Meet услышал только тишину. Включите любой звук (например, видео) и проверьте ещё раз. "
    "Если звук играл — разрешите Meet «Запись системного звука»: Системные настройки → "
    "Конфиденциальность и безопасность → Запись системного звука."
)
CHECK_DENIED_NOTICE = (
    "macOS не дала записать системный звук. Разрешите Meet «Запись системного звука»: "
    "Системные настройки → Конфиденциальность и безопасность → Запись системного звука."
)
CHECK_UNSUPPORTED_NOTICE = "Запись без «Записи экрана» требует macOS 14.4 или новее."
CHECK_FAILED_NOTICE = "Не удалось проверить запись системного звука — попробуйте ещё раз."

# Нули во время записи process tap: столько секунд подряд — строка в журнал
# (только счётчик; запись не откатывается и помощника не перезапускает).
ZERO_LOG_S = 5.0

MIC_USERS_TIMEOUT_S = 3.0
PREFLIGHT_TIMEOUT_S = 5.0
PERMISSION_TIMEOUT_S = 5.0  # --mic-status: без вопроса человеку
REQUEST_TIMEOUT_S = 120.0  # --request-*: системный вопрос ждёт человека

# Разрешение «Микрофон» (`--mic-status`, `--request-mic`) — как у
# AVAuthorizationStatus.
MIC_GRANTED = "granted"
MIC_DENIED = "denied"
MIC_NOT_DETERMINED = "notDetermined"
MIC_RESTRICTED = "restricted"
MIC_STATES = (MIC_GRANTED, MIC_DENIED, MIC_NOT_DETERMINED, MIC_RESTRICTED)
# Разрешение «Запись экрана» (`--request-screen`).
SCREEN_GRANTED = "granted"
SCREEN_DENIED = "denied"
SCREEN_STATES = (SCREEN_GRANTED, SCREEN_DENIED)
# Тип подключения устройства (`transport` в `--watch-devices`), как его
# называет помощник по kAudioDevicePropertyTransportType. Запись различает
# только эти два: Bluetooth-наушники и встроенный микрофон.
TRANSPORT_BLUETOOTH = "bluetooth"
TRANSPORT_BUILTIN = "builtin"
# Поводы строк `--watch-devices`.
WATCH_EVENTS = ("start", "default", "devices", "alive")

# Почему помощник не дал звук (`TapError.kind`). От этого зависит, что делает
# запись дальше: «permission» — ждать разрешения (`preflight`), «helper» и
# «unsupported» — не повторять (без переустановки или новой macOS не
# заработает), «failed» — повторять с нарастающей паузой.
KIND_PERMISSION = "permission"
KIND_UNSUPPORTED = "unsupported"
KIND_HELPER = "helper"
KIND_FAILED = "failed"

PERMISSION_NOTICE = (
    "Нет разрешения на запись системного звука. Откройте «Системные настройки → "
    "Конфиденциальность и безопасность → Запись экрана» (в macOS 15 — «Запись "
    "экрана и системного звука»), включите Meet и начните запись заново. Звук "
    "собеседников можно писать и "
    "через виртуальное устройство (например, BlackHole) — выберите его в "
    "настройках звука."
)
UNSUPPORTED_NOTICE = (
    "Запись системного звука требует macOS 13 или новее. Выберите в настройках "
    "звука виртуальное устройство (например, BlackHole) как источник звука собеседников."
)
# Постоянная плашка в окне, пока звук собеседников не пишется (запись идёт
# только с микрофона): события `record.system_audio`, снимок `/state`.
SYSTEM_AUDIO_MISSING = (
    "Звук собеседников не записывается — разрешите «Запись экрана» для Meet в "
    "Системных настройках"
)
FAILED_NOTICE = (
    "Звук собеседников пока не записывается: помощник записи системного звука "
    "не запустился — пробую снова"
)
STALL_NOTICE = (
    "Системный звук перестал поступать от помощника — перезапускаю его "
    "(пауза уйдёт в тишину)"
)
MISSING_NOTICE = (
    "Не найден помощник записи системного звука (meet-audiotap). Переустановите "
    "приложение или выберите в настройках звука виртуальное устройство "
    "(например, BlackHole)."
)


class TapError(RuntimeError):
    """Помощник не запустил захват. `notice` — текст для человека, `kind` —
    причина (KIND_*): по коду выхода, иначе «failed»."""

    def __init__(self, notice: str, code: "int | None" = None,
                 kind: "str | None" = None) -> None:
        super().__init__(notice)
        self.notice = notice
        self.code = code
        self.kind = kind or kind_for_exit(code)


def kind_for_exit(code: "int | None") -> str:
    if code == EXIT_PERMISSION:
        return KIND_PERMISSION
    if code == EXIT_UNSUPPORTED:
        return KIND_UNSUPPORTED
    return KIND_FAILED


def notice_for_exit(code: "int | None", stderr: str = "") -> str:
    """Текст для человека по коду выхода помощника."""
    if code == EXIT_PERMISSION:
        return PERMISSION_NOTICE
    if code == EXIT_UNSUPPORTED:
        return UNSUPPORTED_NOTICE
    detail = " ".join((stderr or "").split())[:200]
    base = f"Помощник записи системного звука завершился с кодом {code}"
    return f"{base}: {detail}" if detail else base


def helper_path() -> "str | None":
    """Где помощник: `MEET_AUDIOTAP`, затем PATH (оболочка кладёт папку
    ресурсов — с ffmpeg и помощником — в PATH резидента), затем ресурсы
    Meet.app в `/Applications` и `~/Applications` (`meet` из Терминала)."""
    from meet import plat

    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return override if Path(override).is_file() else None
    found = shutil.which(HELPER)
    if found:
        return found
    for folder in plat.app_resource_dirs():
        if (folder / HELPER).is_file():
            return str(folder / HELPER)
    return None


def preflight(run=None, timeout: float = PREFLIGHT_TIMEOUT_S) -> bool:
    """Дано ли разрешение «Запись экрана» (`--preflight`). Помощника нет или
    он не ответил — False: ждать дальше."""
    helper = helper_path()
    if not helper:
        return False
    run = run or subprocess.run
    try:
        out = run([helper, "--preflight"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == EXIT_OK


def parse_handshake(line: bytes) -> dict:
    """Первая строка `--stream`: {"rate", "channels"}. Не та строка — TapError
    (это не наш помощник или другой версии протокола)."""
    try:
        data = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError:
        raise TapError("Помощник записи системного звука ответил не по протоколу") from None
    if not isinstance(data, dict) or data.get("meet_audiotap") != PROTOCOL:
        raise TapError("Помощник записи системного звука другой версии — переустановите приложение")
    if data.get("format", "s16le") != "s16le":
        raise TapError("Помощник записи системного звука отдаёт неизвестный формат")
    try:
        rate = int(data["rate"])
        channels = int(data.get("channels", 1))
    except (KeyError, TypeError, ValueError):
        raise TapError("Помощник записи системного звука не сообщил формат") from None
    if rate <= 0 or channels not in (1, 2):
        raise TapError("Помощник записи системного звука сообщил странный формат")
    return {"rate": rate, "channels": channels}


def stream_command(helper: str, rate: int, channels: int,
                   exclude_pid: "int | None" = None, source: str = SOURCE_SCREEN) -> list[str]:
    """`exclude_pid` — приложение, чей звук не писать (оболочка Meet с плеером, 0.5);
    прежний помощник незнакомый ключ пропускает. `source` — SOURCE_*: ключ
    `--source` только у process tap, ScreenCaptureKit запускается как раньше."""
    argv = [helper, "--stream", "--rate", str(int(rate)), "--channels", str(int(channels))]
    if exclude_pid:
        argv += ["--exclude-pid", str(int(exclude_pid))]
    if source == SOURCE_PROCESS_TAP:
        argv += ["--source", SOURCE_PROCESS_TAP]
    return argv


def macos_version() -> "str | None":
    """Версия macOS строкой («14.4.1»); не macOS или не узнать — None."""
    import platform

    try:
        return platform.mac_ver()[0] or None
    except Exception:
        return None


def _version_tuple(text: "str | None") -> "tuple[int, ...] | None":
    try:
        return tuple(int(part) for part in str(text).split("."))
    except (TypeError, ValueError):
        return None


def process_tap_supported() -> bool:
    """Можно ли выбрать process tap: macOS 14.4+ и помощник на месте. Иначе
    выбор в окне скрыт, а запись пишет через ScreenCaptureKit."""
    from meet import plat

    if not plat.is_macos():
        return False
    version = _version_tuple(macos_version())
    if version is None or version < PROCESS_TAP_MIN_MACOS:
        return False
    return helper_path() is not None


def effective_source(choice: "str | None") -> str:
    """Источник, которым запись пишет на самом деле: process tap — только где
    он поддерживается, иначе ScreenCaptureKit."""
    if choice == SOURCE_PROCESS_TAP and process_tap_supported():
        return SOURCE_PROCESS_TAP
    return SOURCE_SCREEN


class ZeroRuns:
    """Серии сплошных нулей в PCM process tap — только для журнала.

    Без разрешения «Запись системного звука» перехват отдаёт нули, но нули
    неотличимы от тишины (автозапись до звонка), поэтому запись по ним ничего
    не меняет. `feed` → строка для журнала, когда серия дошла до
    `threshold_s` (один раз на серию) и когда после такой серии вернулся звук;
    иначе None. В строках — только числа."""

    def __init__(self, rate: int, channels: int, threshold_s: float = ZERO_LOG_S) -> None:
        self._frame_bytes = 2 * max(1, int(channels))
        self._rate = max(1, int(rate))
        self._threshold = threshold_s
        self._zero_s = 0.0
        self._logged = False
        self.runs = 0

    def feed(self, data: bytes) -> "str | None":
        seconds = len(data) // self._frame_bytes / self._rate
        if not data or data.count(0) != len(data):
            line = None
            if self._logged:
                line = f"process tap: звук после {self._zero_s:.0f} с нулей"
            self._zero_s, self._logged = 0.0, False
            return line
        self._zero_s += seconds
        if self._logged or self._zero_s < self._threshold:
            return None
        self._logged = True
        self.runs += 1
        return (f"process tap: одни нули {self._zero_s:.0f} с подряд (серия {self.runs}) — "
                "пишу как есть")


def check_command(helper: str, seconds: int = CHECK_SECONDS,
                  exclude_pid: "int | None" = None) -> list[str]:
    argv = [helper, "--check-tap", "--seconds", str(int(seconds))]
    if exclude_pid:
        argv += ["--exclude-pid", str(int(exclude_pid))]
    return argv


def parse_check(text: str) -> "dict | None":
    """Строка `--check-tap` → {"frames", "peak"} или None (не по протоколу)."""
    try:
        data = json.loads((text or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if (not isinstance(data, dict) or data.get("meet_audiotap") != PROTOCOL
            or data.get("check") != SOURCE_PROCESS_TAP):
        return None
    frames, peak = data.get("frames"), data.get("peak")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (frames, peak)):
        return None
    return {"frames": frames, "peak": peak}


def check_result(result: str) -> dict:
    """Ответ проверки process tap по итогу CHECK_*: {"ok", "result", "notice", "pane"}."""
    notices = {CHECK_SOUND: CHECK_SOUND_NOTICE, CHECK_SILENCE: CHECK_SILENCE_NOTICE,
               CHECK_DENIED: CHECK_DENIED_NOTICE, CHECK_UNSUPPORTED: CHECK_UNSUPPORTED_NOTICE,
               CHECK_FAILED: CHECK_FAILED_NOTICE}
    pane = SYSTEM_AUDIO_PANE if result in (CHECK_SILENCE, CHECK_DENIED) else None
    return {"ok": result == CHECK_SOUND, "result": result, "notice": notices[result], "pane": pane}


def check_process_tap(run=None, timeout: float = CHECK_TIMEOUT_S) -> dict:
    """Проверка при включении process tap: помощник пишет CHECK_SECONDS с и
    сообщает пик. → {"ok", "result": CHECK_*, "notice": текст человеку,
    "pane": раздел настроек для подсказки или None}. `ok` (выключатель можно
    включить) — только при ненулевом звуке; нули, отказ, сбой — выключатель
    остаётся выключенным."""
    helper = helper_path()
    if not helper:
        return check_result(CHECK_FAILED)
    from meet import process_loopback

    run = run or subprocess.run
    argv = check_command(helper, CHECK_SECONDS, exclude_pid=process_loopback.exclude_target())
    try:
        out = run(argv, capture_output=True, text=True, encoding="utf-8",
                  errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return check_result(CHECK_FAILED)
    if out.returncode == EXIT_PERMISSION:
        return check_result(CHECK_DENIED)
    if out.returncode in (EXIT_UNSUPPORTED, EXIT_USAGE):
        return check_result(CHECK_UNSUPPORTED)
    found = parse_check(out.stdout) if out.returncode == EXIT_OK else None
    if found is None or found["frames"] == 0:
        return check_result(CHECK_FAILED)
    return check_result(CHECK_SOUND if found["peak"] > 0 else CHECK_SILENCE)


def parse_mic_users(text: str) -> "list[dict] | None":
    """Ответ `--mic-users` → список процессов {pid, name, bundle, input,
    output} или None (ответить нечем: macOS старше 14, сбой, мусор)."""
    try:
        data = json.loads((text or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if not isinstance(data, dict) or not data.get("supported"):
        return None
    found = []
    for item in data.get("processes") or ():
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("pid", 0))
        except (TypeError, ValueError):
            pid = 0
        user = {
            "pid": pid,
            "name": str(item.get("name") or ""),
            "bundle": str(item.get("bundle") or ""),
            "input": bool(item.get("input")),
            "output": bool(item.get("output")),
        }
        devices = item.get("input_devices")
        if isinstance(devices, list):
            user["input_devices"] = [str(uid) for uid in devices if isinstance(uid, str)]
        found.append(user)
    return found


def bluetooth_mic_in_use(users: "list[dict] | None", uid: str,
                         own_pids: "set[int] | frozenset[int]" = frozenset()) -> bool:
    """Слушает ли кто-то, кроме самой записи (`own_pids`), микрофон с этим UID.

    Процесс без `input_devices` (прежний помощник) слушает, считаем, системный
    — то есть этот. `users` None — узнать нечем (macOS 13, сбой помощника):
    тоже «слушает» — запись остаётся на системном, как раньше."""
    if users is None:
        return True
    for user in users:
        if not user.get("input") or user.get("pid") in own_pids:
            continue
        devices = user.get("input_devices")
        if devices is None or uid in devices:
            return True
    return False


def watch_command(helper: str) -> list[str]:
    return [helper, "--watch-devices"]


def _device_of(item) -> "dict | None":
    """Устройство из строки `--watch-devices` → {uid, name, transport, alive}
    или None (не по протоколу)."""
    if not isinstance(item, dict):
        return None
    uid, name = item.get("uid"), item.get("name")
    if not isinstance(uid, str) or not uid or not isinstance(name, str):
        return None
    transport = item.get("transport")
    return {"uid": uid, "name": name,
            "transport": transport if isinstance(transport, str) else "",
            "alive": item.get("alive") is not False}


def parse_devices(text: str) -> "dict | None":
    """Строка `--watch-devices` → {"event", "default": устройство | None,
    "inputs": [устройство]} или None: не по протоколу (шум в stdout,
    рукопожатие другого режима, другая версия)."""
    try:
        data = json.loads((text or "").strip())
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("meet_audiotap") != PROTOCOL:
        return None
    event, raw_default, raw_inputs = data.get("watch"), data.get("default_input"), data.get("inputs")
    if event not in WATCH_EVENTS or not isinstance(raw_inputs, list):
        return None
    default = None
    if raw_default is not None:
        default = _device_of(raw_default)
        if default is None:
            return None
    inputs = [dev for dev in map(_device_of, raw_inputs) if dev is not None]
    return {"event": event, "default": default, "inputs": inputs}


def mic_users(run=None, timeout: float = MIC_USERS_TIMEOUT_S) -> "list[dict] | None":
    """Процессы со звуком по CoreAudio — подпроцессом помощника. None —
    помощника нет или ответить нечем (детектор решает по остальным сигналам)."""
    helper = helper_path()
    if not helper:
        return None
    run = run or subprocess.run
    try:
        out = run([helper, "--mic-users"], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != EXIT_OK:
        return None
    return parse_mic_users(out.stdout)


def parse_permission(text: str, key: str) -> "str | None":
    """Ответ режима разрешений — последняя строка JSON `{key: состояние}` →
    состояние из известных (`MIC_STATES` для «microphone», `SCREEN_STATES` для
    «screen») или None: не по протоколу."""
    try:
        data = json.loads((text or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    known = MIC_STATES if key == "microphone" else SCREEN_STATES
    return value if isinstance(value, str) and value in known else None


def _ask_permission(flag: str, key: str, run, timeout: float) -> "str | None":
    """Запустить режим разрешений помощника и разобрать ответ. Помощника нет,
    он прежний (не знает режима), завис или ответил мусором — None."""
    helper = helper_path()
    if not helper:
        return None
    run = run or subprocess.run
    try:
        out = run([helper, flag], capture_output=True, text=True,
                  encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != EXIT_OK:
        return None
    return parse_permission(out.stdout, key)


def mic_status(run=None, timeout: float = PERMISSION_TIMEOUT_S) -> "str | None":
    """Разрешение «Микрофон» (`--mic-status`, без системного вопроса): одно
    из `MIC_STATES` или None — узнать нечем."""
    return _ask_permission("--mic-status", "microphone", run, timeout)


def request_mic(run=None, timeout: float = REQUEST_TIMEOUT_S) -> "str | None":
    """Системный вопрос «Микрофон» (`--request-mic`) и его итог — одно из
    `MIC_STATES` или None. Ждёт ответа человека."""
    return _ask_permission("--request-mic", "microphone", run, timeout)


def request_screen(run=None, timeout: float = REQUEST_TIMEOUT_S) -> "str | None":
    """Системный вопрос «Запись экрана» (`--request-screen`): `SCREEN_GRANTED`,
    `SCREEN_DENIED` (в том числе «дал, но нужен перезапуск») или None."""
    return _ask_permission("--request-screen", "screen", run, timeout)


def screen_status(run=None, timeout: float = PREFLIGHT_TIMEOUT_S) -> "str | None":
    """Разрешение «Запись экрана» по `--preflight`, без системного вопроса:
    код 0 — `SCREEN_GRANTED`, 77 — `SCREEN_DENIED`. Помощника нет, он завис
    или ответил другим кодом — None (в отличие от `preflight`, где это «ждать
    дальше», здесь это «неизвестно»). «Ещё не спрашивали» macOS для записи
    экрана не различает — это тоже «denied»."""
    helper = helper_path()
    if not helper:
        return None
    run = run or subprocess.run
    try:
        out = run([helper, "--preflight"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode == EXIT_OK:
        return SCREEN_GRANTED
    if out.returncode == EXIT_PERMISSION:
        return SCREEN_DENIED
    return None


def permissions(run=None) -> dict:
    """Оба разрешения записи без системных вопросов: {"microphone": одно из
    `MIC_STATES` | None, "screen": одно из `SCREEN_STATES` | None}."""
    return {"microphone": mic_status(run=run), "screen": screen_status(run=run)}


def missing_permissions(states: dict) -> list[str]:
    """Какие разрешения из `permissions()` не выданы: ключи "microphone",
    "screen" по порядку. «Неизвестно» (None) не считается: прежний помощник
    или его отсутствие — не повод тревожить человека."""
    return [key for key in ("microphone", "screen")
            if states.get(key) is not None and states.get(key) != MIC_GRANTED]
