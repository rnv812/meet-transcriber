"""Детектор звонка в Дионе: по нему трей сам поднимает и останавливает запись.

Два независимых сигнала, потому что ни один по отдельности не надёжен:

* **микрофон** — Windows ведёт учёт обращений к нему в реестре
  (`ConsentStore\\microphone`), и по нему видно, что приложение сейчас слушает;
* **воспроизведение** — активная WASAPI render-сессия процесса. Нужна против
  мьюта: если Дион на выключенном микрофоне закрывает поток захвата, первый
  сигнал пропадёт посреди встречи, а этот держится, пока слышно собеседников.

`в звонке = микрофон занят ИЛИ что-то воспроизводится`. Обе проверки возвращают
None, когда ответить нечем (ключа нет, pycaw не встал, COM отказал) — решение
тогда принимается по оставшемуся сигналу, а не по домыслу.

Стейт-машина (`Watcher`) отделена от чтения реестра и COM и принимает время
параметром — грейс проверяется юнитом без Диона и без ожидания.
См. спеку docs/superpowers/specs/2026-08-07-dion-auto-record-design.md."""

import os
import winreg
from datetime import datetime
from pathlib import Path

CONSENT_MIC = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone\NonPackaged"
)

# решения стейт-машины
START = "start"
STOP = "stop"
NONE = "none"

GRACE_S = 180.0  # держим запись после выхода из звонка: реконнект не рвёт файл
POLL_S = 2.0
LOG_MAX_BYTES = 1_000_000

_AUDIO_SESSION_ACTIVE = 1  # AudioSessionState.Active
_com_ready = False


def _qword(key, name: str) -> "int | None":
    try:
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, int) else None


def busy_from_times(start: "int | None", stop: "int | None") -> "bool | None":
    """Занят ли микрофон по паре FILETIME-меток ConsentStore.

    Занят = `Stop == 0` ИЛИ `Start > Stop`. Два условия, а не одно: обнуление
    Stop на время использования — известное поведение Windows, но полагаться
    на него одно нельзя, а признак «начало позже конца» верен в любом случае.
    Нет хотя бы одной метки — ответить нечем (None)."""
    if start is None or stop is None:
        return None
    return stop == 0 or start > stop


def mic_busy(exe_name: str) -> "bool | None":
    """Слушает ли микрофон процесс с таким именем exe.

    Ключи ConsentStore названы полным путём к exe с `\\`, заменённым на `#`,
    поэтому ищем по суффиксу имени: место установки Диона на разных машинах
    отличается, а имя — нет. Ни одного подходящего ключа (Дион ни разу не
    брал микрофон) → None."""
    suffix = "#" + exe_name.lower()
    verdict = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_MIC) as root:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                if not name.lower().endswith(suffix):
                    continue
                try:
                    with winreg.OpenKey(root, name) as sub:
                        busy = busy_from_times(
                            _qword(sub, "LastUsedTimeStart"),
                            _qword(sub, "LastUsedTimeStop"),
                        )
                except OSError:
                    continue
                if busy:
                    return True  # хватает одного занятого пути установки
                if busy is False:
                    verdict = False
    except OSError:
        return None
    return verdict


def _ensure_com() -> None:
    """CoInitialize один раз на процесс: pycaw дёргает COM, а вотчер живёт в
    отдельном потоке трея, где COM сам собой не инициализирован. Повторные
    вызовы наращивали бы счётчик, который никто не разматывает, — поэтому флаг.
    IMPORTANT: инициализация привязана к потоку; вызывать render_active
    только из потока вотчера."""
    global _com_ready
    if _com_ready:
        return
    _com_ready = True
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        pass


def render_active(exe_name: str) -> "bool | None":
    """Воспроизводит ли что-нибудь процесс с таким именем exe.

    None — если ответить нечем: pycaw не установлен или COM отказал. False —
    сессий нет или все неактивны (в том числе когда процесс не запущен)."""
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return None
    _ensure_com()
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        return None
    target = exe_name.lower()
    for session in sessions:
        process = getattr(session, "Process", None)
        if process is None:
            continue
        try:
            if process.name().lower() != target:
                continue
            if session.State == _AUDIO_SESSION_ACTIVE:
                return True
        except Exception:
            continue  # процесс умер между перечислением и опросом
    return False


def in_call(exe_names) -> "tuple[bool, bool | None, bool | None]":
    """(в звонке, сигнал микрофона, сигнал воспроизведения) по списку процессов.

    Оба сигнала None (нечем мерить) → не в звонке: молчим, а не выдумываем."""
    mic = None
    render = None
    for name in exe_names:
        one_mic = mic_busy(name)
        if one_mic is not None:
            mic = bool(mic) or one_mic
        one_render = render_active(name)
        if one_render is not None:
            render = bool(render) or one_render
    return bool(mic) or bool(render), mic, render


class Watcher:
    """Стейт-машина «звонок → запись» с грейсом на выходе.

    Грейс — не пауза перед решением, а продолжение записи: пока он тикает,
    запись идёт. Вернулся в комнату (сеть моргнула, перезашёл починить звук) —
    таймер сброшен, файл остаётся цельным. Не вернулся — стоп, и в хвосте
    лишние минуты фона, которые VAD в транскрипт всё равно не пустит.

    Отслеживает намерение, а не факт: решения START/STOP трей может
    проигнорировать (запись уже идёт, запущена вручную, не стартовала из-за
    ошибки устройства) — состояние машины от этого не зависит."""

    IDLE = "idle"
    RECORDING = "recording"
    GRACE = "grace"

    def __init__(self, grace_seconds: float = GRACE_S) -> None:
        self.grace_seconds = grace_seconds
        self.state = self.IDLE
        self.grace_until = 0.0

    def poll(self, call: bool, now: float) -> str:
        if self.state == self.IDLE:
            if call:
                self.state = self.RECORDING
                return START
            return NONE
        if self.state == self.RECORDING:
            if not call:
                self.state = self.GRACE
                self.grace_until = now + self.grace_seconds
            return NONE
        if call:  # GRACE: вернулся — грейс сброшен, запись не прерывалась
            self.state = self.RECORDING
            return NONE
        if now >= self.grace_until:
            self.state = self.IDLE
            return STOP
        return NONE


class WatchLog:
    """Журнал детектора — `%LOCALAPPDATA%/meet/watch.log`.

    Пишется всегда, даже когда автозапись выключена: по нему видно, как ведут
    себя сигналы на живых встречах (отпускает ли Дион микрофон на мьюте,
    переживает ли реконнект), и можно калибровать, ничего не записывая.
    Ошибки журнала глотаются — он не должен мешать записи."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                backup = path.with_suffix(path.suffix + ".old")
                backup.unlink(missing_ok=True)
                path.rename(backup)
        except OSError:
            pass

    def __call__(self, msg: str) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
        except OSError:
            pass


def default_log_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "meet" / "watch.log"


def describe(signal: "bool | None") -> str:
    return {True: "да", False: "нет", None: "?"}[signal]
