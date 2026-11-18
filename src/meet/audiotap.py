"""Помощник `meet-audiotap` (macOS): системный звук и «кто держит микрофон».

На Windows системный звук пишется loopback'ом WASAPI, а занятость микрофона
видна в реестре. На macOS ни того, ни другого нет, поэтому оба сигнала даёт
маленькая программа на Swift (`mac/audiotap/main.swift`), которая лежит в
ресурсах приложения рядом с ffmpeg:

* `meet-audiotap --stream --rate 48000 --channels 1 [--exclude-pid N]` —
  ScreenCaptureKit (macOS 13+, разрешение «Запись экрана»), только звук, без
  звука приложения с процессом N (оболочка Meet). Первая строка stdout —
  рукопожатие JSON (`{"meet_audiotap": 1, "rate", "channels", "format":
  "s16le"}`), дальше — сырой PCM s16le до закрытия stdin или SIGTERM;
* `meet-audiotap --mic-users` — одна строка JSON: процессы, которые сейчас
  слушают микрофон (и играют звук), по CoreAudio (macOS 14+; раньше —
  `supported: false`);
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
                   exclude_pid: "int | None" = None) -> list[str]:
    """`exclude_pid` — приложение, чей звук не писать (оболочка Meet с плеером, 0.5);
    прежний помощник незнакомый ключ пропускает."""
    argv = [helper, "--stream", "--rate", str(int(rate)), "--channels", str(int(channels))]
    if exclude_pid:
        argv += ["--exclude-pid", str(int(exclude_pid))]
    return argv


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
        found.append({
            "pid": pid,
            "name": str(item.get("name") or ""),
            "bundle": str(item.get("bundle") or ""),
            "input": bool(item.get("input")),
            "output": bool(item.get("output")),
        })
    return found


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
