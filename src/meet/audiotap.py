"""Помощник `meet-audiotap` (macOS): системный звук и «кто держит микрофон».

На Windows системный звук пишется loopback'ом WASAPI, а занятость микрофона
видна в реестре. На macOS ни того, ни другого нет, поэтому оба сигнала даёт
маленькая программа на Swift (`mac/audiotap/main.swift`), которая лежит в
ресурсах приложения рядом с ffmpeg:

* `meet-audiotap --stream --rate 48000 --channels 1` — ScreenCaptureKit (macOS
  13+, разрешение «Запись экрана»), только звук. Первая строка stdout —
  рукопожатие JSON (`{"meet_audiotap": 1, "rate", "channels", "format":
  "s16le"}`), дальше — сырой PCM s16le до закрытия stdin или SIGTERM;
* `meet-audiotap --mic-users` — одна строка JSON: процессы, которые сейчас
  слушают микрофон (и играют звук), по CoreAudio (macOS 14+; раньше —
  `supported: false`);
* `meet-audiotap --self-test` — версия и возможности, без разрешений (CI).

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
    """Помощник не запустил захват. `notice` — текст для человека."""

    def __init__(self, notice: str, code: "int | None" = None) -> None:
        super().__init__(notice)
        self.notice = notice
        self.code = code


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
    ресурсов — с ffmpeg и помощником — в PATH резидента)."""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return override if Path(override).is_file() else None
    return shutil.which(HELPER)


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


def stream_command(helper: str, rate: int, channels: int) -> list[str]:
    return [helper, "--stream", "--rate", str(int(rate)), "--channels", str(int(channels))]


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
