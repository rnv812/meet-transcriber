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

**Браузеры** (`browsers` в настройках) идут по своему правилу: звонок — только
занятый микрофон. Воспроизведение у браузера не значит ничего — это любое видео
или музыка. Строгий режим (`browser_require_site`) вдобавок требует, чтобы в
заголовке одного из окон этого браузера был сайт звонка (`call_sites`). Окна
перечисляются только пока браузер держит микрофон: в остальное время детектор
стоит столько же, сколько без браузеров. Из заголовка окна берётся и начальное
название записи («Dion — Планёрка»).

Стейт-машина (`Watcher`) отделена от чтения реестра и COM и принимает время
параметром — грейс проверяется юнитом без Диона и без ожидания.
См. спеку docs/superpowers/specs/2026-08-07-dion-auto-record-design.md."""

import re
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
# Начальное название записи из заголовка окна браузера — не длиннее этого.
CALL_TITLE_MAX = 80
# Заголовок окна в журнале — не длиннее этого: там бывает лишнее.
LOG_TITLE_MAX = 60
LOG_MAX_BYTES = 1_000_000
LOG_CHECK_EVERY = 200  # записей в журнал между проверками его размера

_AUDIO_SESSION_ACTIVE = 1  # AudioSessionState.Active
_com_ready = False
_pycaw_warned = False
_psutil_warned = False


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


def process_running(exe_name: str, log=None) -> "bool | None":
    """Запущен ли процесс с таким именем. None — psutil недоступен.

    Нужно потому, что метки в реестре переживают процесс: если Дион убит
    (питание, taskkill) до того, как Windows записала LastUsedTimeStop, пара
    остаётся в состоянии «занят» бессрочно — и запись без этой проверки шла бы
    вечно, в том числе после перезагрузки."""
    global _psutil_warned
    try:
        import psutil
    except Exception as e:
        if log is not None and not _psutil_warned:
            _psutil_warned = True
            log(f"psutil недоступен ({e!r}) — живость процесса не проверяю, "
                f"незакрытая метка в реестре может означать «в звонке» бессрочно")
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


class Signals:
    """Опрос сигналов с разной частотой и проверкой живости процесса.

    Держит последнее значение render между опросами: перечисление сессий
    дорогое и подтекающее (см. RENDER_PERIOD_S), а микрофон читается дёшево.

    Браузеры (`browsers`) — отдельно и только по микрофону (см. docstring
    модуля). Подробности браузерного звонка последнего опроса — в
    `browser_call` ({"exe", "site", "title"} или None), а почему занятый
    браузером микрофон звонком не счёлся — в `browser_note` (для журнала)."""

    def __init__(self, exe_names, render_period: float = RENDER_PERIOD_S,
                 log=None, browsers=(), require_site: bool = False,
                 sites=()) -> None:
        self.exe_names = list(exe_names)
        self.browsers = list(browsers)
        self.require_site = bool(require_site)
        self.sites = list(sites)
        self.render_period = render_period
        self.log = log
        self._render = None
        self._render_at = None
        self.browser_call: "dict | None" = None
        self.browser_note: "str | None" = None

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
            call = False
        self.browser_note = None
        self.browser_call = self._browser() if self.browsers else None
        if self.browser_call is not None:
            return True, True, render
        return call, mic, render

    def _browser(self) -> "dict | None":
        """Звонок в браузере: микрофон занят, процесс жив и (в строгом режиме)
        в заголовке его окна есть сайт звонка. Окна перечисляются только здесь,
        то есть только пока какой-то браузер держит микрофон."""
        for name in self.browsers:
            if not mic_busy(name):
                continue
            pids = browser_pids(name)
            if pids is not None and not pids:
                continue  # метка в реестре от закрытого браузера
            site = title = None
            for window in window_titles(pids) if pids else []:
                found = match_site(window, self.sites)
                if found:
                    site, title = site_label(found), call_title(window, found)
                    break
            if site is None and self.require_site:
                self.browser_note = (
                    f"{name}: микрофон занят, сайта звонка в заголовках окон нет")
                continue
            return {"exe": name, "site": site, "title": title}
        return None

    def _any_running(self) -> bool:
        for name in self.exe_names:
            running = process_running(name, log=self.log)
            if running is None:
                return True  # psutil нет — не мешаем детекту
            if running:
                return True
        return False


# --- браузеры: процессы, окна, сайт звонка ----------------------------------

# Имя браузера в конце заголовка окна: «Вкладка - Google Chrome». У Edge перед
# ним бывает профиль («- Профиль 1 - Microsoft Edge», с невидимым пробелом в
# имени) — его убирает _PROFILE_TAIL следом.
_BROWSER_TAIL = re.compile(
    r"\s+[-–—]\s+(?:google chrome|chromium|mozilla firefox|firefox|"
    r"microsoft\s*edge|яндекс[ .]?браузер|yandex(?: browser)?|"
    r"opera(?: gx)?|brave|vivaldi)\s*$",
    re.IGNORECASE,
)
_PROFILE_TAIL = re.compile(
    r"\s+[-–—]\s+(?:(?:профиль|profile)\s*\d*|личный|рабочий|personal|work)\s*$",
    re.IGNORECASE,
)
_SEPARATORS = " \t-–—|·:•"
# Управляющие и невидимые символы (у Edge в имени — пробел нулевой ширины).
_CONTROL = re.compile("[\x00-\x1f\x7f​-‏ -‮]")


def browser_pids(exe_name: str) -> "set[int] | None":
    """Процессы браузера с таким именем exe. None — psutil недоступен."""
    try:
        import psutil
    except Exception:
        return None
    target = exe_name.lower()
    found: set[int] = set()
    try:
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name")
            if name and name.lower() == target:
                found.add(proc.pid)
    except Exception:
        return None
    return found


def window_titles(pids) -> "list[str]":
    """Заголовки видимых окон верхнего уровня, принадлежащих процессам `pids`.

    EnumWindows через ctypes: без новых зависимостей и дёшево (~мс). Сбой —
    пустой список: сайт тогда не определён (в строгом режиме это «не звонок»)."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
    except Exception:
        return []
    wanted = set(pids)
    titles: list[str] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _param):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in wanted:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value:
                titles.append(buf.value)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(proc_type(visit), 0)
    except Exception:
        return []
    return titles


def match_site(title: str, sites) -> "str | None":
    """Сайт звонка, упомянутый в заголовке (без учёта регистра). Подходят
    несколько — самый длинный: «Яндекс Телемост» точнее, чем «Телемост»."""
    low = title.lower()
    best = None
    for site in sites:
        site = str(site).strip()
        if site and site.lower() in low and (best is None or len(site) > len(best)):
            best = site
    return best


def site_label(site: str) -> str:
    """Сайт для названия записи: «Meet –» (так Google Meet подписывает
    вкладку) → «Meet»."""
    return site.strip(_SEPARATORS) or site.strip()


def short(text: str, limit: int) -> str:
    """Не длиннее `limit` символов; обрезанное кончается многоточием."""
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def call_title(window: str, site: str) -> str:
    """Начальное название записи из заголовка окна браузера:
    «<сайт> — <остаток заголовка>», без имени браузера и профиля, без
    управляющих символов, не длиннее CALL_TITLE_MAX."""
    text = " ".join(_CONTROL.sub(" ", window).split())
    text = _BROWSER_TAIL.sub("", text)
    text = _PROFILE_TAIL.sub("", text)
    needle = site.strip()
    at = text.lower().find(needle.lower())
    rest = f"{text[:at]} {text[at + len(needle):]}" if at >= 0 else text
    rest = " ".join(rest.split()).strip(_SEPARATORS)
    label = site_label(site)
    title = f"{label} — {rest}" if rest and rest.lower() != label.lower() else label
    return short(title, CALL_TITLE_MAX)


class Watcher:
    """Стейт-машина «звонок → запись» с грейсом на выходе.

    Грейс — не пауза перед решением, а продолжение записи: пока он тикает,
    запись идёт. Вернулся в комнату (сеть моргнула, перезашёл починить звук) —
    таймер сброшен, файл остаётся цельным. Не вернулся — стоп, и в хвосте
    лишние минуты фона, которые VAD в транскрипт всё равно не пустит."""

    IDLE = "idle"
    RECORDING = "recording"
    GRACE = "grace"
    SUPPRESSED = "suppressed"

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
        if self.state == self.SUPPRESSED:
            if not call:  # звонок кончился — со следующего начинаем как обычно
                self.state = self.IDLE
            return NONE
        if call:  # GRACE: вернулся — грейс сброшен, запись не прерывалась
            self.state = self.RECORDING
            return NONE
        if now >= self.grace_until:
            self.state = self.IDLE
            return STOP
        return NONE

    def suppress(self) -> None:
        """Человек сам остановил или отменил запись посреди звонка.

        Возвращать машину в IDLE здесь нельзя: звонок-то продолжается, и на
        следующем такте она немедленно выдала бы START — кнопка «Остановить»
        не работала бы вовсе, а «Отменить (удалить)» удаляла бы папку, после
        чего запись начиналась заново. Поэтому до конца этого звонка не
        стартуем, а со следующего работаем как обычно."""
        self.state = self.SUPPRESSED
        self.grace_until = 0.0

    def release(self) -> None:
        """Запись не пошла не по решению человека — например, не стартовала
        из-за занятого устройства. Машина возвращается в IDLE, чтобы попытку
        можно было повторить: иначе встреча не записалась бы вовсе, а звонок
        может идти ещё два часа. Темп повторов ограничивает вызывающий."""
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
    """Журнал решений дежурного рядом с остальным состоянием.

    Через paths.data_dir(), а не напрямую по LOCALAPPDATA: иначе журнал остаётся
    единственным файлом, который не слушается MEET_DATA_DIR, и портативный режим
    (как и изолированный прогон) пишет его в чужое место. В обычном запуске путь
    тот же, что был."""
    from meet import paths

    return paths.data_dir() / "watch.log"


def describe(signal: "bool | None") -> str:
    return {True: "да", False: "нет", None: "?"}[signal]
