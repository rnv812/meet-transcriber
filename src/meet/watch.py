"""Детектор звонка в Дионе: по нему трей сам поднимает и останавливает запись.

Два независимых сигнала, потому что ни один по отдельности не надёжен:

* **микрофон** — Windows ведёт учёт обращений к нему в реестре
  (`ConsentStore\\microphone`), и по нему видно, что приложение сейчас слушает;
* **воспроизведение** — активная WASAPI render-сессия процесса. Нужна против
  мьюта: если Дион на выключенном микрофоне закрывает поток захвата, первый
  сигнал пропадёт посреди встречи, а этот держится, пока слышно собеседников.

`в звонке = микрофон занят ИЛИ что-то воспроизводится`, но только пока процесс
жив: метки в реестре переживают смерть приложения, и без проверки процесса
незакрытый интервал означал бы «в звонке» вечно. Обе проверки возвращают None,
когда ответить нечем (ключа нет, pycaw не встал, COM отказал) — решение тогда
принимается по оставшемуся сигналу, а не по домыслу.

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
# Перечисление аудио-сессий течёт нативной памятью в comtypes/pycaw (~0.3 КБ на
# вызов, измерено) и стоит ~18 мс против 0.2 мс у реестра. Резидент живёт
# месяцами, поэтому этот сигнал опрашивается реже микрофонного: начало звонка
# почти всегда ловится микрофоном, а роль render'а — пережить мьют, где
# несколько секунд задержки ничего не решают.
RENDER_PERIOD_S = 6.0
LOG_MAX_BYTES = 1_000_000
LOG_CHECK_EVERY = 200  # тактов между проверками размера журнала

_AUDIO_SESSION_ACTIVE = 1  # AudioSessionState.Active
_com_ready = False
_pycaw_warned = False


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


def process_running(exe_name: str) -> "bool | None":
    """Запущен ли процесс с таким именем. None — psutil недоступен.

    Нужно потому, что метки в реестре переживают процесс: если Дион убит
    (питание, taskkill) до того, как Windows записала LastUsedTimeStop, пара
    остаётся в состоянии «занят» бессрочно — и запись без этой проверки шла бы
    вечно, в том числе после перезагрузки."""
    try:
        import psutil
    except Exception:
        return None
    target = exe_name.lower()
    try:
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name")
            if name and name.lower() == target:
                return True
    except Exception:
        return None
    return False


def _ensure_com() -> bool:
    """CoInitialize один раз на процесс: pycaw дёргает COM, а вотчер живёт в
    отдельном потоке трея, где COM сам собой не инициализирован. Повторные
    вызовы наращивали бы счётчик, который никто не разматывает, — поэтому флаг,
    и взводится он только после успеха, чтобы разовый сбой не отключал сигнал
    навсегда.
    IMPORTANT: инициализация привязана к потоку; вызывать render_active
    только из потока вотчера."""
    global _com_ready
    if _com_ready:
        return True
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        return False
    _com_ready = True
    return True


def render_active(exe_name: str, log=None) -> "bool | None":
    """Воспроизводит ли что-нибудь процесс с таким именем exe.

    None — если ответить нечем: pycaw не установлен или COM отказал. False —
    сессий нет или все неактивны (в том числе когда процесс не запущен)."""
    global _pycaw_warned
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception as e:
        if log is not None and not _pycaw_warned:
            _pycaw_warned = True
            log(f"pycaw недоступен ({e!r}) — сигнал воспроизведения отключён, "
                f"решения принимаются только по микрофону")
        return None
    if not _ensure_com():
        return None
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


class Signals:
    """Опрос сигналов с разной частотой и проверкой живости процесса.

    Держит последнее значение render между опросами: перечисление сессий
    дорогое и подтекающее (см. RENDER_PERIOD_S), а микрофон читается дёшево."""

    def __init__(self, exe_names, render_period: float = RENDER_PERIOD_S,
                 log=None) -> None:
        self.exe_names = list(exe_names)
        self.render_period = render_period
        self.log = log
        self._render = None
        self._render_at = None

    def read(self, now: float) -> "tuple[bool, bool | None, bool | None]":
        mic = None
        for name in self.exe_names:
            one = mic_busy(name)
            if one is not None:
                mic = bool(mic) or one
        if self._render_at is None or now - self._render_at >= self.render_period:
            self._render_at = now
            render = None
            for name in self.exe_names:
                one = render_active(name, log=self.log)
                if one is not None:
                    render = bool(render) or one
            self._render = render
        render = self._render
        call = bool(mic) or bool(render)
        if call and not self._any_running():
            # метки реестра переживают процесс: без этой проверки убитый
            # мид-звонком Дион означал бы «в звонке» до скончания века
            return False, mic, render
        return call, mic, render

    def _any_running(self) -> bool:
        for name in self.exe_names:
            running = process_running(name)
            if running is None:
                return True  # psutil нет — не мешаем детекту
            if running:
                return True
        return False


class Watcher:
    """Стейт-машина «звонок → запись» с грейсом на выходе.

    Грейс — не пауза перед решением, а продолжение записи: пока он тикает,
    запись идёт. Вернулся в комнату (сеть моргнула, перезашёл починить звук) —
    таймер сброшен, файл остаётся цельным. Не вернулся — стоп, и в хвосте
    лишние минуты фона, которые VAD в транскрипт всё равно не пустит."""

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

    def release(self) -> None:
        """Запись перестала идти не по решению машины: не стартовала из-за
        ошибки, остановлена руками, отменена. Без этого машина осталась бы
        в RECORDING до конца звонка и не подняла бы запись повторно — а звонок
        может идти ещё два часа."""
        self.state = self.IDLE
        self.grace_until = 0.0


class WatchLog:
    """Журнал детектора — `%LOCALAPPDATA%/meet/watch.log`.

    Пишется всегда, даже когда автозапись выключена: по нему видно, как ведут
    себя сигналы на живых встречах (отпускает ли Дион микрофон на мьюте,
    переживает ли реконнект), и можно калибровать, ничего не записывая.
    Ошибки журнала глотаются — он не должен мешать записи."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._writes = 0
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._rotate()

    def __call__(self, msg: str) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
        except OSError:
            return
        # резидент живёт месяцами и при старте ротацию проходит один раз —
        # без проверки на ходу журнал рос бы без предела
        self._writes += 1
        if self._writes >= LOG_CHECK_EVERY:
            self._writes = 0
            self._rotate()

    def _rotate(self) -> None:
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size <= LOG_MAX_BYTES:
                return
            backup = self.path.with_suffix(self.path.suffix + ".old")
            backup.unlink(missing_ok=True)
            self.path.rename(backup)
        except OSError:
            pass


def default_log_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "meet" / "watch.log"


def describe(signal: "bool | None") -> str:
    return {True: "да", False: "нет", None: "?"}[signal]
