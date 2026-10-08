"""Трей записи встреч: резидент, который сам поднимает запись на звонке в
клиенте конференций и сам её останавливает, когда звонок кончился.

Дежурит с серой иконкой, на записи — синяя с секундомером и пунктами
«Остановить и сохранить» / «Остановить без сохранения…» (внизу, с вопросом). Ярлык на
рабочем столе запускает `meet-tray` без аргументов — это по-прежнему означает «начать запись», и такую
запись детектор не останавливает никогда: писать можно не только конференцию.
Дежурный режим — флаг `--watch`, с ним трей стоит в автозагрузке.

Детектор звонка — `watch.py`; включается секцией `auto_record` в
`%LOCALAPPDATA%/meet/config.json`. Без консольного окна: точка входа meet-tray
в [project.gui-scripts].
См. спеку docs/superpowers/specs/2026-08-07-dion-auto-record-design.md
(и предшествующую 2026-07-02-tray-record-design.md)."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from meet import events, paths, pcm_tap, plat, settings, watch
from meet.output import fmt_ts
from meet.recorder import LOCK_NAME, _pid_alive, record
from meet.tray_control import AUTO, MANUAL, TrayControl

# Куда писать по умолчанию: из репозитория — его `recordings/` (ярлык
# запускается с произвольным cwd), из установленного приложения — data_dir.
# Осознанный выбор пользователя живёт в настройках, см. _out_root().
OUT_ROOT = paths.default_recordings_dir()

IDLE_COLOR = (130, 130, 130, 255)  # дежурю
REC_COLOR = (40, 110, 220, 255)  # пишу
HEARTBEAT_S = 60.0
TICK_S = 1.0
# Короче этого автозапись считается ложной тревогой (звук уведомления,
# отклонённый вызов), и пост-хук на неё не зовут: папка остаётся, но окна с
# предложением расшифровать полминуты тишины не появляется. Длительность
# берётся по звонку, а не по файлу: в файле всегда есть ещё и грейс.
# Значение — дефолт настройки auto_record.min_call_seconds.
MIN_CALL_S = settings.DEFAULT_MIN_CALL_S
# Пауза перед повторной попыткой автозаписи после сорвавшегося старта: без неё
# трей долбился бы в занятое устройство каждые poll_seconds и засыпал бы
# уведомлениями, а без повтора вовсе — потерял бы встречу целиком.
RETRY_AFTER_S = 30.0


def _state_dir() -> Path:
    """Машинно-локальное состояние трея. Не константа: LOCALAPPDATA читается
    каждый раз, иначе тесты не могут подменить его на tmp_path."""
    return paths.data_dir()


def _config() -> dict:
    """Сырое содержимое config.json — точка, которую подменяют тесты.

    Схему и дефолты держит meet.settings; здесь остаётся только чтение файла,
    чтобы монкипатч `_config` продолжал изолировать трей от реального конфига
    машины."""
    return settings.read_raw()


def _settings() -> settings.Settings:
    """Настройки поверх того же сырого конфига, что читает `_config()`.

    Именно через `_config()`, а не напрямую из файла: тесты подменяют его, и
    обход сломал бы их изоляцию."""
    return settings.Settings.from_raw(_config())


def _out_root() -> Path:
    """Куда писать запись: осознанный выбор из настроек, иначе OUT_ROOT.

    Через OUT_ROOT, а не paths напрямую: тесты подменяют именно его."""
    return _settings().recording.out_dir or OUT_ROOT


def _auto_config() -> dict:
    """Настройки автозаписи с дефолтами. Секции нет — автозапись выключена,
    но детектор всё равно опрашивается и пишет журнал: так можно неделю
    смотреть на поведение сигналов, ничего не записывая."""
    return _settings().auto_record.to_raw()


def _proc_ident(pid: int) -> dict:
    """Отпечаток процесса для lock: имя и время старта.

    Имя одно не годится — gui-script живёт под `pythonw.exe`, а таких на машине
    несколько (тот же виджет-маскот). Время старта с точностью до микросекунд
    делает пару уникальной."""
    try:
        import psutil

        proc = psutil.Process(pid)
        return {"name": proc.name(), "started": proc.create_time()}
    except Exception:
        return {}


def _resident_alive() -> bool:
    """Уже есть живой дежурный трей?

    Мало проверить, что pid жив: Windows переиспользует номера, и чужой
    процесс на месте убитого трея заставил бы ярлык слать команды в никуда —
    запись просто не начиналась бы, молча. Поэтому сверяем отпечаток."""
    try:
        data = json.loads((_state_dir() / "tray.lock").read_text(encoding="utf-8"))
        pid = int(data["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if not _pid_alive(pid):
        return False
    actual = _proc_ident(pid)
    if not actual or "name" not in data:
        return True  # psutil нет или lock старого формата — верим pid'у
    if data.get("name") != actual.get("name"):
        return False
    expected_start = data.get("started")
    if expected_start is not None and actual.get("started") is not None:
        # разные запуски одного и того же exe различаются временем старта
        return abs(float(expected_start) - float(actual["started"])) < 1.0
    return True


def _send_command(cmd: str) -> None:
    """Попросить живого дежурного что-то сделать. Файл, а не сокет: команда
    ровно одна, и дежурный всё равно тикает раз в секунду."""
    try:
        path = _state_dir() / "command"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cmd, encoding="utf-8")
    except OSError:
        pass


def _take_command() -> "str | None":
    """Прочитать и сразу снять команду (обработка — максимум один раз)."""
    path = _state_dir() / "command"
    try:
        cmd = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    return cmd or None


def _drop_command() -> None:
    """Снять команду, не выполняя. Нужно при старте резидента: команда,
    записанная в момент, когда прошлый трей умирал, пролежала бы до следующей
    загрузки Windows и запустила бы запись на пустом месте."""
    try:
        (_state_dir() / "command").unlink()
    except OSError:
        pass


def _in_recurring_window(folder: str, window: tuple[str, str] | None) -> bool:
    """Похоже ли время записи (из имени папки YYYY-MM-DD_HH-MM) на слот
    регулярной встречи.

    Окно задаётся настройкой и по умолчанию не задано вовсе: у каждого свои
    регулярные встречи, а у кого-то их нет. Это только гипотеза для подсказки —
    что за встреча на самом деле, решает календарь."""
    if not window:
        return False
    m = re.search(r"_(\d{2})-(\d{2})$", Path(folder).name)
    if not m:
        return False
    lo, hi = window
    return lo <= f"{m.group(1)}:{m.group(2)}" <= hi


def _wt_safe(text: str) -> str:
    """Обезвредить метасимволы командной строки в тексте промпта.

    IMPORTANT: ';' для wt — разделитель команд: хвост после него Windows
    Terminal пытается запустить как программу (ошибка 0x80070002), а первой
    вкладке достаётся обрезанный промпт с незакрытой кавычкой. Апостроф
    закрывает одинарные кавычки powershell — в PS он удваивается."""
    return text.replace(";", ",").replace("'", "''")


def _hook_prompt(folder: str, hooks) -> str:
    """Текст-подсказка для команды пост-хука."""
    prompt = hooks.prompt.replace("{folder}", folder)
    if _in_recurring_window(folder, hooks.recurring_window):
        prompt += hooks.recurring_prompt
    return prompt


def _hook_argv(folder: str, hooks) -> list[str]:
    """Команда пост-хука с подставленными плейсхолдерами.

    Подстановка идёт в каждый аргумент по отдельности, и запуск — без шелла:
    команда из конфига не должна превращаться в исполняемую строку. Текст
    промпта дополнительно обезврежен `_wt_safe` — он может попасть внутрь
    кавычек чужой командной строки."""
    date = Path(folder).name.split("_")[0]
    # Папка, из которой открывается команда: корень репозитория, где лежат
    # скрипты. Из установленного приложения репозитория нет — тогда родитель
    # папки записей (лучше, чем ничего: там сама запись).
    project = str(paths.repo_root() or _out_root().parent)
    values = {
        "{folder}": folder,
        "{project}": project,
        "{date}": date,
        "{prompt}": _wt_safe(_hook_prompt(folder, hooks)),
    }
    argv = []
    for part in hooks.command:
        for key, value in values.items():
            part = part.replace(key, value)
        argv.append(part)
    return argv


def _run_post_hook(folder: str) -> None:
    """Пост-хук: запустить команду из настроек по окончании записи.

    Раньше здесь был зашит один сценарий — окно Windows Terminal с Claude Code.
    Теперь это шаблон: у нового пользователя команда пуста и не запускается
    ничего, а у того, кто пользовался хуком раньше, миграция настроек оставила
    прежнюю команду. Best effort: проблемы запуска не должны мешать завершению
    записи.

    IMPORTANT: у записи с подключённым ассистентом хук зовётся сразу при её
    сохранении, а хвост ассистента (последние строки `live_transcript.md` и
    последняя сводка в `live_state.json`) дописывается после, в фоне, до
    `live_control.STOP_TIMEOUT_S`. Команда хука может увидеть ленту и сводку
    без него; дорожки (`*.opus`) к этому моменту закрыты и целы."""
    hooks = _settings().hooks
    if not hooks.post_record or not hooks.command:
        return
    try:
        subprocess.Popen(_hook_argv(folder, hooks))
    except OSError:
        pass


def _stop_reason(discard: bool, hook: bool, temporary: bool = False) -> str:
    """Причина остановки для `last_stop` в снимке состояния: "saved",
    "short", "discarded" («Остановить без сохранения») или "temporary"
    (временная встреча закончилась и удалена). Последние две — не сохранение:
    оболочка и панель не говорят «Запись сохранена».

    «interrupted» здесь не бывает: оборвавшуюся запись резидент не переживает
    и сказать о ней не может — это оболочка выводит сама, по обрыву связи."""
    if temporary:
        return "temporary"
    if discard:
        return "discarded"
    return "saved" if hook else "short"


class _BusLog:
    """Журнал дежурного с дублированием строк в шину событий.

    Панель показывает решения детектора живьём, не вычитывая `watch.log` по
    таймеру. Файл остаётся первоисточником — сюда только копия."""

    def __init__(self, log, bus) -> None:
        self._log = log
        self._bus = bus

    def __call__(self, msg: str) -> None:
        self._log(msg)
        self._bus.emit(events.LOG, text=msg, source="watch")


def _icon_image(color=REC_COLOR):
    """Кружок 64x64 — рисуем на лету, без файлов-ресурсов.

    Синий, а не красный: красным кружком запись показывают другие программы
    (диктовка, запись экрана), два одинаковых красных в трее путаются.
    Серый — дежурю, звонка нет."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=color)
    return img


class _NoIcon:
    """Иконка-пустышка для тикера в режиме --headless."""

    visible = False


CANCEL_TITLE = "Остановить без сохранения"
CANCEL_QUESTION = ("Остановить без сохранения? Запись и всё, что с ней связано, будут удалены "
                   "без возможности восстановления.")
# Поток записи временной встречи не закончился за join — удалить её, когда
# закончится, но не ждать дольше этого.
TEMP_LATE_WAIT_S = 600.0


def _confirm_cancel() -> bool:
    """Вопрос перед отменой записи в трее без оболочки (MessageBoxW): кнопка
    по умолчанию — «Нет», то есть продолжить запись. Своих подписей у этого
    окна нет, поэтому значение «Да»/«Нет» сказано в тексте. Не Windows —
    спросить нечем (этот трей — только Windows), отменяем сразу."""
    if sys.platform != "win32":
        return True
    import ctypes

    # MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND; 6 — IDYES.
    flags = 0x4 | 0x30 | 0x100 | 0x10000
    text = f"{CANCEL_QUESTION}\n\nДа — удалить, Нет — продолжить запись."
    return ctypes.windll.user32.MessageBoxW(None, text, CANCEL_TITLE, flags) == 6


class RecordAttempt:
    """Как идёт поток одной записи: `started` — устройства открыты (её
    `record.started`), `done` — поток кончился, `error` — не стартовала
    (lock, устройство), `folder` — сохранена. Своё у каждой попытки: общий
    `result` тикер чистит (`_collect_error`).

    IMPORTANT: отвод звука и папка появляются раньше устройств (`hub.begin()`
    — до `first_open`), поэтому «отвод открыт» ещё не значит «запись идёт»:
    подключать ассистента можно только после `started`."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.done = threading.Event()
        self.error: str | None = None
        self.folder = None

    def watch(self, bus):
        """Ловить `record.started` этого потока записи: шина зовёт подписчиков
        в потоке издателя, так что чужая запись (доживающая прошлая) не в
        счёт. → функция отписки."""
        mine = threading.current_thread()

        def on_event(event) -> None:
            if event.kind == events.RECORD_STARTED and threading.current_thread() is mine:
                self.started.set()

        return bus.subscribe(on_event)


class TrayApp:
    """Состояние трея: дежурю или пишу, и кто эту запись начал.

    Источник записи (`auto`/`manual`) решает всё про автостоп: запись, начатую
    ярлыком или из меню, детектор не останавливает — иначе надиктовка или
    телефонный разговор оборвались бы просто потому, что в клиенте
    конференций нет звонка.

    Старт и стоп ходят под общим локом: их дёргают три потока (тикер, меню
    pystray, команда снаружи), а между проверкой «пишу ли я» и фактической
    остановкой лежит join на финализацию ffmpeg — без лока в это окно
    проваливался второй стоп и папку успевали удалить и отдать Claude
    одновременно."""

    def __init__(self, start_now: bool = False) -> None:
        self.cfg = _auto_config()
        # Шина событий: по ней панель видит запись живьём (уровни дорожек,
        # решения дежурного, ступени расшифровки). Без подписчиков publish —
        # пустой цикл, так что на работу без UI она не влияет.
        self.bus = events.EventBus()
        self.log = _BusLog(watch.WatchLog(watch.default_log_path()), self.bus)
        # Вопрос «Остановить без сохранения?» на экране (см. _on_cancel_asked).
        self._cancel_asking = threading.Event()
        self.watcher = watch.Watcher(self.cfg["grace_minutes"] * 60.0)
        self.signals = watch.Signals(
            self.cfg["processes"], log=self.log,
            browsers=self.cfg.get("browsers") or (),
            require_site=bool(self.cfg.get("browser_require_site")),
            sites=self.cfg.get("call_sites") or (),
        )
        self.icon = None
        self.start_now = start_now
        self.recording = False
        self.source = None
        self.on_saved = None  # колбэк «запись сохранена»: (folder, source, full)
        # () -> bool: пишет ассистент (живой режим, дочерний процесс). Тогда
        # вторую запись не поднимаем ни из меню, ни автозаписью.
        self.live_busy = None
        # Отвод звука идущей записи для ассистента, включённого посреди неё
        # (meet.pcm_tap): запись отдаёт ему копию байтов дорожек.
        self.pcm_tap = pcm_tap.TapHub()
        # (discard) -> None: запись уже остановлена (захват кончился в момент
        # «Стоп», отвод закрыт) — что делать с подключённым к ней ассистентом
        # (TrayControl): сохраняемую запись он не задерживает — дописывает
        # хвост ленты и сводку в её папку в фоне; при отмене его убивают
        # сразу, до удаления папки. Не ждёт.
        self.after_stop = None
        # Идёт остановка записи: ассистента в неё уже не включить.
        self.stopping = False
        # Чем кончилась последняя запись: {"folder", "reason", "at"} (см.
        # stop_recording). None — остановок ещё не было.
        self.last_stop: dict | None = None
        self.thread = None
        self.stop_event = None
        self.result: dict = {}
        self.started = 0.0
        # Временная встреча (meet.temp_meeting): идёт вне библиотеки, в своей
        # папке сеанса (`temp_session`); `temporary_kept` — «Сохранить как
        # обычную встречу»: на «Стоп» переносится в библиотеку.
        self.temporary = False
        self.temporary_kept = False
        self.temp_session: Path | None = None
        # () -> None: дождаться хвоста подключённого ассистента (TrayControl)
        # перед переносом сохраняемой временной встречи в библиотеку.
        self.settle_after_stop = None
        # Фоновый перенос сохраняемой временной встречи (`_finish_kept`).
        self._keep_thread: threading.Thread | None = None
        self._mutex = threading.Lock()
        self._alive = True
        self._call_end = None
        self._retry_after = 0.0
        self._last_poll = 0.0
        self._last_beat = 0.0
        self._last_signals = None
        self._last_browser_note = None
        self._title = None
        # Начальное название идущей автозаписи (звонок в браузере: «Google
        # Meet — Планёрка»); его кладёт в meta.json сохранение записи. None — нет.
        self.recording_title: str | None = None
        # Когда пропал сигнал звонка (секунды эпохи) у автозаписи, остановленной
        # после ожидания повторного подключения; по нему резидент обрезает
        # хвост без разговора (meet.tail). None — не такая остановка.
        self.call_end_at: float | None = None

    # --- запись ---------------------------------------------------------

    def start_recording(self, source: str, attempt: "RecordAttempt | None" = None,
                        temporary: bool = False) -> bool:
        """Поднять запись. False — если запись уже идёт (своя или ассистента).
        Резидент удержан для переключения папки движка (`storage.HOLD`) —
        `storage.Held`: проверка удержания и начало записи — один шаг под
        одним замком, и причина отказа доходит до вызывающего (409 «идёт
        перенос», повтор автозаписи), а не превращается в «уже идёт».
        `attempt` — канал этой попытки: чем кончился поток записи (ошибка
        старта не теряется, даже когда тикер уже забрал её из `result`).
        `temporary` — временная встреча: пишется вне библиотеки (своя папка
        сеанса, `meet.temp_meeting`) и на «Стоп» удаляется."""
        from meet import storage

        with storage.HOLD.gate():
            return self._start_recording(source, attempt, temporary)

    def _start_recording(self, source: str, attempt: "RecordAttempt | None" = None,
                         temporary: bool = False) -> bool:
        from meet import temp_meeting

        with self._mutex:
            if self.recording or self._live_busy():
                return False
            self._clear_own_lock()
            session = temp_meeting.new_session() if temporary else None
            self.temporary = bool(temporary)
            self.temporary_kept = False
            self.temp_session = session
            result: dict = {}
            stop_event = threading.Event()
            self.result = result
            self.stop_event = stop_event
            self.source = source
            self.started = time.monotonic()
            self._call_end = None
            self.recording_title = None
            self.call_end_at = None
            self.recording = True
            # result и stop_event уходят в поток значениями, а не через self:
            # если join истечёт по таймауту, доживающий поток допишет их в свой
            # словарь, а не в состояние следующей записи
            self.thread = threading.Thread(
                target=self._run_record, args=(result, stop_event, attempt, session), daemon=True
            )
            self.thread.start()
        self._refresh()
        return True

    def _live_busy(self) -> bool:
        try:
            return bool(self.live_busy and self.live_busy())
        except Exception:
            return False  # сбой проверки не должен запрещать запись

    def _run_record(self, result: dict, stop_event: threading.Event,
                    attempt: "RecordAttempt | None" = None, root: Path | None = None) -> None:
        unwatch = attempt.watch(self.bus) if attempt is not None else None
        try:
            result["folder"] = record(
                str(root or _out_root()), stop_event=stop_event, bus=self.bus,
                pcm_tap=self.pcm_tap,
            )
            if attempt is not None:
                attempt.folder = result["folder"]
        except BaseException as e:  # и SystemExit «запись уже идёт»
            error = str(e) or repr(e)
            # Сначала в попытку, потом в общий `result`: тикер, увидевший
            # ошибку там (и снявший флаг записи), не обгонит её здесь.
            if attempt is not None:
                attempt.error = error
            result["error"] = error
        finally:
            if unwatch is not None:
                unwatch()
            if attempt is not None:
                attempt.done.set()

    def _clear_own_lock(self) -> None:
        """Снять lock записи, оставшийся от нас самих.

        Протухший lock раньше опознавался по мёртвому pid, но теперь этот pid —
        сам резидент, и он всегда жив: недоснятый lock (поток записи не успел
        отработать finally к моменту join) заблокировал бы все следующие записи
        до перезапуска трея.

        IMPORTANT: снимаем только когда прошлый поток записи действительно
        завершился. Совпадения pid мало — он общий у всех наших потоков, и у
        доживающего после истёкшего join lock ещё рабочий: сняв его, мы пустили
        бы вторую запись параллельно первой, в ту же папку."""
        if self.thread is not None and self.thread.is_alive():
            return
        lock = _out_root() / LOCK_NAME
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            if int(data["pid"]) == os.getpid():
                lock.unlink(missing_ok=True)
                self.log(f"снял свой протухший lock записи: {data.get('folder', '?')}")
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def stop_recording(self, discard: bool = False, hook: bool = True) -> None:
        """Штатно остановить запись. discard — «Остановить без сохранения»:
        удалить папку записи (и забыть сеансы её агента у провайдеров), ни
        расшифровки, ни хука.

        Временная встреча (`temporary`) не сохраняется и без discard: папка
        её сеанса удаляется целиком, сеансы агента забываются. Если во время
        неё нажали «Сохранить как обычную встречу» (`temporary_kept`), запись
        после остановки переносится в библиотеку и дальше — как обычная."""
        from meet import temp_meeting

        with self._mutex:
            if not self.recording:
                return
            temporary, kept, session = self.temporary, self.temporary_kept, self.temp_session
            # Временную встречу, которую не просили сохранить, не сохраняем никогда.
            drop = discard or (temporary and not kept)
            # Захват кончается в момент «Стоп»; подключённый ассистент своё
            # дописывает после (after_stop), сохранение записи его не ждёт.
            self.stopping = True
            self.stop_event.set()
            thread, result = self.thread, self.result
            if thread is not None:
                thread.join(timeout=60)
            alive = thread is not None and thread.is_alive()
            # Причина — раньше флага записи: снимок, увидевший «не пишу», должен
            # видеть и её, иначе отмена, пойманная опросом до удаления папки,
            # прочиталась бы оболочкой как «Запись сохранена». Поток, не
            # успевший за join, дописывает дорожки и папку не удаляет — это
            # сохранение, даже если просили отмену (кроме временной встречи:
            # её удалим, когда поток закончит).
            stopped = result.get("folder") or (self._current_folder() if alive else None)
            # Сохраняемая временная встреча: причину («saved» и папку в
            # библиотеке) скажет перенос — имя выбирается в момент переноса.
            if stopped and not (kept and not drop):
                self.last_stop = {
                    "folder": str(stopped),
                    "reason": _stop_reason(drop and (temporary or not alive), hook,
                                           temporary=temporary and drop and not discard),
                    "at": time.time(),
                }
            self.recording = False
            self.temporary = self.temporary_kept = False
            self.temp_session = None
            source, self.source = self.source, None
            # Обнуляем ссылку только если поток действительно завершился. Иначе
            # доживающий поток (join истёк за 60 с — ffmpeg ещё финализирует)
            # стал бы невидим для _clear_own_lock, и тот снял бы ещё рабочий
            # lock, пустив вторую запись в ту же папку.
            self.thread = None if not alive else thread
            self.stopping = False
        if self.after_stop is not None:
            try:
                self.after_stop(drop)
            except Exception as e:  # ассистент не должен мешать сохранению записи
                self.log(f"после остановки записи: {e!r}")
        folder = result.get("folder")
        if temporary and drop:
            self._drop_temporary(session, stopped, thread if alive else None, discard)
            self._refresh()
            return
        if kept:
            # Перенос — в фоне: хвост ассистента (до 30 с), копия на другой том —
            # «Стоп» оболочки столько не ждёт. Поток записи не успел за join —
            # перенос дождётся его.
            self._keep_thread = threading.Thread(
                target=self._finish_kept, args=(session, result, thread if alive else None,
                                                source, hook),
                name="meet-temp-keep", daemon=True)
            self._keep_thread.start()
            self._refresh()
            return
        if folder is None and alive:
            # поток жив и дописывает дорожки — данные целы, просто ещё не наши
            folder = self._current_folder()
            self.log(f"поток записи не завершился за 60 с, дописывает: {folder}")
            self._notify(f"Ещё сохраняется: {folder}")
            self._refresh()
            return
        if folder is None:
            self.log("запись остановлена, но папка не получена")
            self._notify("Запись не сохранена")
            self._refresh()
            return
        if discard:
            gone = temp_meeting.wipe(folder, log=self.log)
            if not gone:
                # Что-то держит другая программа: спрятать остаток из библиотеки
                # и доудалить, когда отпустят (или при следующем запуске).
                hidden = temp_meeting.hide_leftover(folder)
                if hidden is not None and Path(hidden) == Path(folder):
                    temp_meeting.finish_later(
                        folder, log=self.log,
                        active=lambda: self._current_folder() if self.recording else None)
            # Без пути и времени в журнале: запись удалена, и следов не нужно.
            self.log("запись остановлена без сохранения: " + (
                "удалена" if gone else "удалена не до конца — доудалю при следующем запуске"))
            self._notify("Запись удалена" if gone else "Запись удалена не до конца")
            # Отдельное событие после удаления: recorder уже отправил
            # record.stopped{folder} (он про discard не знает), и панель успела
            # предложить расшифровать уже удалённую папку. Это отменяет предложение.
            self.bus.emit(events.RECORD_DISCARDED, folder=str(folder))
            self._refresh()
            return
        self.log(f"запись остановлена ({source}): {folder}")
        self._notify(f"Сохранено: {folder}")
        if self.on_saved is not None:
            try:
                self.on_saved(str(folder), source, hook)
            except Exception as e:  # колбэк UI не должен ломать остановку
                self.log(f"после сохранения: {e!r}")
        if hook:
            _run_post_hook(str(folder))
        else:
            self.log("звонок был короткий — Claude не зову, папка осталась")
        self._refresh()

    def _drop_temporary(self, session, stopped, late_thread, discard: bool) -> None:
        """Временная встреча кончилась: ассистент уже убит (after_stop), папку
        сеанса — удалить целиком, сеансы агента — забыть. Поток записи не
        успел за join — удалить, когда он закончит (в фоне)."""
        from meet import temp_meeting

        def drop() -> None:
            if late_thread is not None:
                late_thread.join(timeout=TEMP_LATE_WAIT_S)
            gone = temp_meeting.wipe(session, log=self.log) if session is not None else True
            self.log("временная встреча закончена и удалена" if gone
                     else "временная встреча удалена не до конца — доудалю при следующем запуске")
            self.bus.emit(events.RECORD_DISCARDED, folder=str(stopped or ""), temporary=True)

        if late_thread is not None:
            threading.Thread(target=drop, name="meet-temp-drop", daemon=True).start()
        else:
            drop()
        self._notify("Остановлено без сохранения" if discard else "Временная встреча удалена")

    def _finish_kept(self, session, result: dict, late_thread, source, hook: bool) -> None:
        """«Сохранить как обычную встречу», после «Стоп» (в фоне): дождаться
        потока записи (если не успел за join) и хвоста ассистента (он пишет в
        папку записи), перенести запись в библиотеку и дальше — как обычная
        сохранённая: причина остановки, расшифровка, название, хук. Не вышло —
        отметка `keep` остаётся, перенесёт и обработает следующий запуск."""
        from meet import temp_meeting

        if late_thread is not None:
            late_thread.join(timeout=TEMP_LATE_WAIT_S)
        folder = result.get("folder")
        if folder is None:
            self.log("временная встреча не сохранена: папка записи не получена")
            self._notify("Встреча сохранится при следующем запуске Meet")
            return
        if self.settle_after_stop is not None:
            try:
                self.settle_after_stop()
            except Exception as e:
                self.log(f"хвост ассистента временной встречи: {type(e).__name__}")
        try:
            moved = temp_meeting.move_to_library(folder, _out_root(), log=self.log)
        except OSError as e:
            self.log(f"временная встреча не перенесена в библиотеку ({type(e).__name__}) — "
                     f"перенесу при следующем запуске")
            self._notify("Встреча сохранится при следующем запуске Meet")
            return
        if session is not None:
            temp_meeting._rmtree(Path(session))
        self.last_stop = {"folder": str(moved), "reason": _stop_reason(False, hook),
                          "at": time.time()}
        self.log(f"временная встреча сохранена как обычная: {moved}")
        self._notify(f"Сохранено: {moved}")
        # Окно перечитает список и снимок: запись появилась в библиотеке.
        self.bus.emit("recording.updated", id=Path(moved).name)
        if self.on_saved is not None:
            try:
                self.on_saved(str(moved), source, hook)
            except Exception as e:
                self.log(f"после сохранения: {e!r}")
        if hook:
            _run_post_hook(str(moved))
        self._refresh()

    def wait_kept(self, timeout: float) -> None:
        """Выход резидента: дать фоновому переносу сохраняемой временной
        встречи закончиться (не успеет — доделает следующий запуск)."""
        thread = getattr(self, "_keep_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def keep_temporary(self) -> bool:
        """«Сохранить как обычную встречу» посреди временной: отметка в папке
        сеанса (переживает сбой). False — временная встреча не идёт."""
        from meet import temp_meeting

        with self._mutex:
            if not (self.recording and self.temporary) or self.stopping:
                return False
            if not self.temporary_kept and self.temp_session is not None:
                temp_meeting.mark_keep(self.temp_session)
            self.temporary_kept = True
        self.log("временная встреча будет сохранена как обычная")
        self.bus.emit(events.RECORD_KEPT)
        self._refresh()
        return True


    # --- пункты меню ----------------------------------------------------

    def _on_start(self, icon=None, item=None) -> None:
        from meet import storage

        try:
            started = self.start_recording(MANUAL)
        except storage.Held:
            self.log("идёт перенос движка и моделей — запись не начата")
            self._notify("Идёт перенос движка и моделей — запись будет доступна через минуту")
            return
        if started:
            self.log("запись запущена вручную")
            return
        if self.source == AUTO:
            # нажал кнопку поверх автозаписи — значит берёт её под свою руку:
            # автостоп такую запись больше не тронет
            self.source = MANUAL
            self.watcher.suppress()
            self.log("автозапись переведена в ручную по кнопке")
            self._notify("Запись продолжается, автостоп отключён")
            return
        self._notify(f"Запись уже идёт ({self._current_folder()})")

    def _current_folder(self) -> str:
        """Папка идущей записи. В self.result она появляется только когда
        record() вернулась, то есть уже после остановки, — поэтому во время
        записи берём её из lock-файла."""
        folder = self.result.get("folder")
        if folder:
            return str(folder)
        # Временная встреча держит lock в своей папке сеанса, не в библиотеке.
        root = self.temp_session if self.temporary and self.temp_session else _out_root()
        try:
            data = json.loads((root / LOCK_NAME).read_text(encoding="utf-8"))
            return str(data.get("folder") or "папка ещё не создана")
        except (OSError, ValueError, TypeError):
            return "папка ещё не создана"

    def _on_stop(self, icon=None, item=None) -> None:
        was_auto = self.recording and self.source == AUTO
        self.stop_recording()
        if was_auto:
            # человек остановил сам, а звонок идёт: без этого машина через
            # такт выдала бы START и запись пошла бы заново
            self.watcher.suppress()

    def _on_cancel(self, icon=None, item=None) -> None:
        was_auto = self.recording and self.source == AUTO
        self.stop_recording(discard=True)
        if was_auto:
            self.watcher.suppress()

    def _on_cancel_asked(self, icon=None, item=None) -> None:
        """«Остановить без сохранения…» из меню: сначала вопрос (в своём потоке —
        цикл иконки не ждёт ответа), отмена — только по «Да» и только той
        записи, о которой спрашивали (пока вопрос висел, автозапись могла
        закончить её и начать новую). Второй вопрос поверх первого не
        открывается."""
        if not self.recording or self._cancel_asking.is_set():
            return
        self._cancel_asking.set()
        asked = self._current_folder()

        def ask() -> None:
            try:
                if _confirm_cancel() and self.recording and self._current_folder() == asked:
                    self._on_cancel()
            finally:
                self._cancel_asking.clear()

        threading.Thread(target=ask, name="meet-cancel-ask", daemon=True).start()

    def _on_exit(self, icon=None, item=None) -> None:
        if self.recording:
            self.stop_recording()
            time.sleep(3)  # дать уведомлению показаться до выхода процесса
        self.log("трей остановлен")
        self._alive = False
        if self.icon is not None:
            self.icon.stop()

    def request_exit(self) -> None:
        """Штатный выход: тикер и (если есть) иконка останавливаются."""
        self._alive = False
        if self.icon is not None:
            self.icon.stop()

    # --- цикл -----------------------------------------------------------

    def ticker(self, icon) -> None:
        """Фон pystray: команды от ярлыка, секундомер и опрос детектора."""
        try:
            icon.visible = True
            self.log(
                f"трей запущен, автозапись "
                f"{'включена' if self.cfg['enabled'] else 'выключена'}, "
                f"процессы {', '.join(self.cfg['processes'])}, "
                f"браузеры {', '.join(self.cfg.get('browsers') or []) or 'нет'}"
                f"{' (только сайты звонков)' if self.cfg.get('browser_require_site') else ''}, "
                f"ожидание повторного подключения {self.cfg['grace_minutes']:g} мин"
            )
            if self.start_now:
                self._on_start()
        except Exception as e:  # старт не должен убивать тикер насовсем
            self.log(f"старт трея: {e!r}")
        while self._alive:
            try:
                self._tick()
            except Exception as e:  # такт не должен ронять трей
                self.log(f"такт трея: {e!r}")
            time.sleep(TICK_S)

    def _tick(self) -> None:
        command = _take_command()
        if command == "start":
            self._on_start()
        elif command == "stop":
            # штатная остановка снаружи (сценарий Claude «Останови запись»).
            # Раньше запись убивали по pid из .recording.lock, но теперь этот
            # pid — сам резидент: taskkill погасил бы и дежурного вместе с ним.
            self.log("остановка по команде")
            self._on_stop()
        elif command is not None:
            self.log(f"неизвестная команда, пропускаю: {command!r}")
        self._collect_error()
        self._update_title()
        self._watch_tick()

    def _collect_error(self) -> None:
        """Запись не стартовала (нет устройства, чужой lock) — вернуться в
        дежурное состояние, а не висеть с иконкой записи."""
        if "error" not in self.result:
            return
        error = self.result.pop("error")
        if not self.recording:
            self.log(f"запись завершилась ошибкой: {error}")
            return
        self.recording = False
        was_auto = self.source == AUTO
        self.source = None
        self.log(f"запись не стартовала: {error}")
        if was_auto:
            # иначе до конца звонка повторной попытки не будет вовсе и встреча
            # не запишется; но и долбиться каждые две секунды нельзя —
            # следующая попытка не раньше RETRY_AFTER_S
            self.watcher.release()
            self._retry_after = time.monotonic() + RETRY_AFTER_S
        self._notify(error)
        self._refresh()

    def _update_title(self) -> None:
        if self.icon is None:
            return
        if self.recording:
            title = f"Запись: {fmt_ts(time.monotonic() - self.started)}"
        else:
            title = "Жду встречу"
        if title != self._title:  # сеттер pystray дёргает Shell_NotifyIcon
            self._title = title
            self.icon.title = title

    def _watch_tick(self) -> None:
        now = time.monotonic()
        if now - self._last_poll < self.cfg["poll_seconds"]:
            return
        self._last_poll = now
        call, mic, render = self.signals.read(now)
        if (mic, render) != self._last_signals:
            self._last_signals = (mic, render)
            self.log(
                f"сигналы: микрофон {watch.describe(mic)}, "
                f"звук {watch.describe(render)}"
            )
        note = getattr(self.signals, "browser_note", None)
        if note != self._last_browser_note:
            self._last_browser_note = note
            if note:
                self.log(note)
        if now - self._last_beat >= HEARTBEAT_S:
            self._last_beat = now
            self.log(
                f"состояние {self.watcher.state}, микрофон {watch.describe(mic)}, "
                f"звук {watch.describe(render)}, "
                f"запись {self.source or 'нет'}"
            )
        before = self.watcher.state
        decision = self.watcher.poll(call, now)
        if self.watcher.state != before:
            self.log(f"{before} → {self.watcher.state}")
            if self.watcher.state == watch.Watcher.GRACE:
                self._call_end = now  # длительность звонка без хвоста грейса
        # Выключатель автозаписи запрещает только новые старты. STOP детектор
        # выдаёт ровно один раз: проглоти его, пока идёт автозапись, начатая до
        # выключения, — и она писала бы до выхода трея.
        if decision == watch.START:
            self._log_call_cause(mic, render)
        if decision == watch.START and self.cfg["enabled"]:
            self._auto_start()
        elif decision == watch.STOP:
            self._auto_stop(now)

    def _log_call_cause(self, mic, render) -> None:
        """Что именно сочтено звонком: программа (по какому сигналу) или
        браузер с сайтом. Заголовок окна — обрезанный, адресов здесь нет."""
        browser = getattr(self.signals, "browser_call", None)
        if browser:
            site = browser.get("site") or "не определён"
            line = f"звонок в браузере: {browser.get('exe')}, сайт {site}"
            if browser.get("title"):
                line += f", окно «{watch.short(browser['title'], watch.LOG_TITLE_MAX)}»"
            self.log(line)
        else:
            self.log(f"звонок в программе: микрофон {watch.describe(mic)}, "
                     f"звук {watch.describe(render)}")

    def _auto_start(self) -> None:
        if self.recording:
            self.log("звонок начался, запись уже идёт — не вмешиваюсь")
            return
        if self._live_busy():
            # Нарочно: запись с ассистентом уже пишет этот звонок. START не
            # съедаем — освободился ассистент, а звонок идёт: следующий такт
            # попробует снова.
            self.log("звонок начался, пишет ассистент — не вмешиваюсь")
            self.watcher.release()
            return
        from meet import storage

        left = self._retry_after - time.monotonic()
        if left > 0:
            self.log(f"предыдущая попытка сорвалась — повторю через {left:.0f} с")
            self.watcher.release()  # чтобы попытка повторилась на этом же звонке
            return
        try:
            started = self.start_recording(AUTO)
        except storage.Held:
            # Оболочка переключает папку движка: не начнём сейчас — попробуем
            # снова на этом же звонке (новый резидент тоже его увидит).
            self.log("звонок начался, идёт перенос движка и моделей — запись позже")
            self._retry_after = time.monotonic() + RETRY_AFTER_S
            self.watcher.release()
            return
        if not started:
            return
        browser = getattr(self.signals, "browser_call", None)
        self.recording_title = (browser or {}).get("title") or None
        self.log("звонок начался — запись запущена")
        self._notify("Пишу встречу. Отменить — в меню трея")

    def _auto_stop(self, now: float) -> None:
        if not self.recording:
            return
        if self.source != AUTO:
            self.log("звонок кончился, но запись запущена вручную — не трогаю")
            return
        call_seconds = (self._call_end or now) - self.started
        self.log(f"звонок кончился ({call_seconds:.0f} с) — останавливаю запись")
        # время по стенным часам: events.jsonl записи — в них
        self.call_end_at = time.time() - (now - (self._call_end or now))
        self.stop_recording(hook=call_seconds >= self.cfg["min_call_seconds"])

    # --- UI -------------------------------------------------------------

    def _temporary_now(self) -> bool:
        """Идёт временная встреча, которую не решили сохранить."""
        return bool(self.temporary and not self.temporary_kept)

    def _notify(self, message: str) -> None:
        if self.icon is None:
            return
        try:
            self.icon.notify(message, "Запись встречи")
        except Exception:
            pass  # уведомления — не критичный путь

    def _refresh(self) -> None:
        if self.icon is None:
            return
        try:
            self.icon.icon = _icon_image(REC_COLOR if self.recording else IDLE_COLOR)
            self.icon.update_menu()
        except Exception:
            pass

    def run(self) -> None:
        import pystray

        if _resident_alive():  # успел подняться, пока мы шли сюда
            return
        lock = _state_dir() / "tray.lock"
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(
                json.dumps({"pid": os.getpid(), **_proc_ident(os.getpid())}),
                encoding="utf-8",
            )
        except OSError:
            pass
        # команда от прошлой, уже мёртвой сессии: пролежав до следующей
        # загрузки Windows, «start» запустил бы запись на пустом месте, а
        # «stop» погасил бы ту, которую только что начали ярлыком
        _drop_command()
        menu = pystray.Menu(
            pystray.MenuItem(
                "Начать запись", self._on_start, visible=lambda i: not self.recording
            ),
            pystray.MenuItem(
                "Остановить и сохранить", self._on_stop,
                visible=lambda i: self.recording and not self._temporary_now(),
            ),
            # Временная встреча удаляется на «Стоп» — так и подписано.
            pystray.MenuItem(
                "Закончить временную встречу (удалится)", self._on_stop,
                visible=lambda i: self.recording and self._temporary_now(),
            ),
            # Отмена — внизу, отдельно от остановки: рядом их легко перепутать.
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Остановить без сохранения…", self._on_cancel_asked,
                visible=lambda i: self.recording and not self._temporary_now(),
            ),
            pystray.MenuItem("Выход", self._on_exit),
        )
        self.icon = pystray.Icon(
            "meet-record", _icon_image(IDLE_COLOR), "Жду встречу", menu=menu
        )
        api = self._start_control_api()
        try:
            self.icon.run(setup=self.ticker)
        finally:
            self._alive = False
            self._stop_services(api)
            self._release_lock(lock)

    def run_headless(self, parent_pid: int | None = None) -> None:
        """Дежурный без своей иконки: иконку, меню и уведомления держит оболочка
        (Tauri), резидент отдаёт только API, запись и автозапись.

        parent_pid — оболочка: умерла она — выходим сами, штатно сохранив
        запись, иначе осиротевший резидент держал бы микрофон невидимым."""
        if _resident_alive():
            return
        lock = _state_dir() / "tray.lock"
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(
                json.dumps({"pid": os.getpid(), **_proc_ident(os.getpid())}),
                encoding="utf-8",
            )
        except OSError:
            pass
        _drop_command()
        api = self._start_control_api()
        if parent_pid:

            def watch_parent() -> None:
                while self._alive:
                    if not _pid_alive(parent_pid):
                        self.log("оболочка завершилась — выхожу")
                        if self.recording:
                            self.stop_recording()
                        self.request_exit()
                        return
                    time.sleep(1.0)

            threading.Thread(
                target=watch_parent, name="meet-parent", daemon=True
            ).start()
        try:
            self.ticker(_NoIcon())
        finally:
            self._alive = False
            self._stop_services(api)
            self._release_lock(lock)

    def _stop_services(self, api) -> None:
        """Погасить то, что резидент держит подпроцессами, и только потом API.

        Ассистент первым: запись, к которой он подключён, уже сохранена её
        остановкой (её расшифровка в очереди), осталось дописать его хвост —
        до SHUTDOWN_WAIT_S (10 с), дальше убийство дерева. Очереди задач —
        раньше сервера: иначе идущая расшифровка (подпроцесс job_worker)
        осиротеет, продолжая держать VRAM и gpu.lock, невидимая для очереди
        перезапущенного резидента."""
        if api is None:
            return
        from meet.live_control import SHUTDOWN_WAIT_S

        try:
            api.state.live.stop(wait=True, timeout=SHUTDOWN_WAIT_S)
        except Exception as e:
            self.log(f"ассистент не остановился штатно: {e!r}")
        for queue in (api.state.queue, api.state.llm_queue, getattr(api.state, "downloads", None)):
            try:
                queue.stop()
            except Exception:
                pass
        api.stop(pid=os.getpid())

    def _start_control_api(self):
        """Поднять control API для панели и окна настроек.

        Best effort: запись важнее панели. Не поднялся (порт занят, что-то ещё)
        — пишем строку в журнал и работаем как раньше, без UI."""
        from meet import control

        self._retire_profiles()
        try:
            # Фиксированный порт + постоянный токен: адрес API не меняется между
            # перезапусками, UI не дёргает переподключение. fallback — откат на
            # эфемерный, если предпочтительный порт занят.
            server = control.ControlServer(
                TrayControl(self),
                port=control.PREFERRED_PORT,
                token=control.persisted_token(),
                log=self.log,
                fallback=True,
            )
            server.start(pid=os.getpid())
        except Exception as e:
            self.log(f"control API не поднялся ({e!r}) — работаю без панели")
            return None
        try:
            # Ассистент прошлого резидента, умершего жёстко, мог остаться
            # сиротой с микрофоном и lock'ом записи — остановить штатно.
            server.state.live.adopt_orphan()
        except Exception as e:
            self.log(f"не удалось проверить прошлого ассистента: {e!r}")
        try:
            # Обрезка и задачи, прерванные прошлым выходом (обновление, сбой).
            server.state.recover_in_background()
        except Exception as e:
            self.log(f"восстановление после перезапуска не запущено: {e!r}")
        return server

    def _retire_profiles(self) -> None:
        """Профили людей убраны в 0.3.2: заметки — в один файл, остальное
        удалить (meet.retired_profiles). До control API — окно сразу видит
        отметку. Сбой уборки не мешает запуску."""
        from meet import retired_profiles

        try:
            retired_profiles.run(_state_dir(), settings.load().recording.voices, log=self.log)
        except Exception as e:
            self.log(f"профили: уборка не удалась: {type(e).__name__}: {e}")

    def _release_lock(self, lock: Path) -> None:
        """Снять свой lock — но только свой: за время работы его мог перехватить
        другой экземпляр."""
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            if int(data["pid"]) == os.getpid():
                lock.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass


# Код выхода `--headless`, когда резидент уже работает: не сбой, а отказ
# поднимать второго (см. main).
EXIT_ALREADY_RUNNING = 3


def main() -> None:
    """Без аргументов — начать запись сразу (ярлык «Запись встречи»). `--watch` —
    дежурить с иконкой pystray (автозагрузка без оболочки). `--headless` —
    дежурить без иконки под оболочкой приложения; `--parent-pid N` — pid
    оболочки, с которой резидент живёт и умирает.

    Второй экземпляр не поднимает вторую иконку: живому дежурному уходит
    команда, а сам он выходит. Поэтому ярлык работает одинаково — и когда
    дежурный висит, и когда его нет."""
    from meet import models

    # Кэш моделей Meet — в окружение до любых загрузок; задачи, ассистент и
    # проба устройств наследуют его от резидента.
    models.use_meet_cache()
    args = sys.argv[1:]
    if "--headless" in args:
        parent = None
        if "--parent-pid" in args:
            try:
                parent = int(args[args.index("--parent-pid") + 1])
            except (IndexError, ValueError):
                parent = None
        if _resident_alive():
            # Не тихий 0: оболочка должна отличить «дежурный уже есть» от
            # «поднялся и упал», иначе крутила бы перезапуски впустую.
            text = "meet-tray --headless: дежурный уже запущен (tray.lock занят), выхожу"
            print(text, file=sys.stderr)
            watch.WatchLog(watch.default_log_path())(text)
            sys.exit(EXIT_ALREADY_RUNNING)
        if parent:
            # Запись системного звука исключает звук оболочки — окно и WebView с
            # плеером (0.5: Windows — `meet.process_loopback`, macOS — помощник
            # `--exclude-pid`).
            os.environ.setdefault("MEET_EXCLUDE_PID", str(parent))
        TrayApp(start_now=False).run_headless(parent_pid=parent)
        return
    start_now = "--watch" not in args
    if _resident_alive():
        if start_now:
            _send_command("start")
        return
    if plat.is_macos():
        # pystray на macOS не ставится (значок в строке меню — у приложения
        # Meet): дежурим без значка, как под оболочкой.
        print("meet-tray: на macOS значок показывает приложение Meet — дежурю без значка",
              file=sys.stderr)
        TrayApp(start_now=False).run_headless(parent_pid=None)
        return
    TrayApp(start_now=start_now).run()
