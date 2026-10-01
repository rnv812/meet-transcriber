"""Трей записи встреч: резидент, который сам поднимает запись на звонке в
клиенте конференций и сам её останавливает, когда звонок кончился.

Дежурит с серой иконкой, на записи — синяя с секундомером и пунктами
«Остановить запись» / «Отменить (удалить)». Ярлык на рабочем столе запускает
`meet-tray` без аргументов — это по-прежнему означает «начать запись», и такую
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

from meet import events, paths, settings, watch
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
    записи."""
    hooks = _settings().hooks
    if not hooks.post_record or not hooks.command:
        return
    try:
        subprocess.Popen(_hook_argv(folder, hooks))
    except OSError:
        pass


def _stop_reason(discard: bool, hook: bool) -> str:
    """Причина остановки для `last_stop` в снимке состояния.

    «interrupted» здесь не бывает: оборвавшуюся запись резидент не переживает
    и сказать о ней не может — это оболочка выводит сама, по обрыву связи."""
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

    Синий, а не красный: красным кружком запись показывает voice-control,
    два одинаковых красных в трее путаются. Серый — дежурю, звонка нет."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=color)
    return img


class _NoIcon:
    """Иконка-пустышка для тикера в режиме --headless."""

    visible = False


class TrayApp:
    """Состояние трея: дежурю или пишу, и кто эту запись начал.

    Источник записи (`auto`/`manual`) решает всё про автостоп: запись, начатую
    ярлыком или из меню, детектор не останавливает — иначе надиктовка или
    телефонный разговор оборвались бы просто потому, что в Дионе нет
    конференции.

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
        self.watcher = watch.Watcher(self.cfg["grace_seconds"])
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
        # Чем кончилась последняя запись: {"folder", "reason", "at"} (см.
        # stop_recording). None — остановок ещё не было.
        self.last_stop: dict | None = None
        self.thread = None
        self.stop_event = None
        self.result: dict = {}
        self.started = 0.0
        self._mutex = threading.Lock()
        self._alive = True
        self._call_end = None
        self._retry_after = 0.0
        self._last_poll = 0.0
        self._last_beat = 0.0
        self._last_signals = None
        self._last_browser_note = None
        self._title = None
        # Начальное название идущей автозаписи (звонок в браузере: «Dion —
        # Планёрка»); его кладёт в meta.json сохранение записи. None — нет.
        self.recording_title: str | None = None

    # --- запись ---------------------------------------------------------

    def start_recording(self, source: str) -> bool:
        """Поднять запись. False — если запись уже идёт (своя или ассистента)."""
        with self._mutex:
            if self.recording or self._live_busy():
                return False
            self._clear_own_lock()
            result: dict = {}
            stop_event = threading.Event()
            self.result = result
            self.stop_event = stop_event
            self.source = source
            self.started = time.monotonic()
            self._call_end = None
            self.recording_title = None
            self.recording = True
            # result и stop_event уходят в поток значениями, а не через self:
            # если join истечёт по таймауту, доживающий поток допишет их в свой
            # словарь, а не в состояние следующей записи
            self.thread = threading.Thread(
                target=self._run_record, args=(result, stop_event), daemon=True
            )
            self.thread.start()
        self._refresh()
        return True

    def _live_busy(self) -> bool:
        try:
            return bool(self.live_busy and self.live_busy())
        except Exception:
            return False  # сбой проверки не должен запрещать запись

    def _run_record(self, result: dict, stop_event: threading.Event) -> None:
        try:
            result["folder"] = record(
                str(_out_root()), stop_event=stop_event, bus=self.bus
            )
        except BaseException as e:  # и SystemExit «запись уже идёт»
            result["error"] = str(e) or repr(e)

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
        """Штатно остановить запись. discard — отменить: удалить папку и не
        звать Claude (автозапись поймала то, что писать не надо)."""
        with self._mutex:
            if not self.recording:
                return
            self.stop_event.set()
            thread, result = self.thread, self.result
            if thread is not None:
                thread.join(timeout=60)
            alive = thread is not None and thread.is_alive()
            # Причина — раньше флага записи: снимок, увидевший «не пишу», должен
            # видеть и её, иначе отмена, пойманная опросом до удаления папки,
            # прочиталась бы оболочкой как «Запись сохранена». Поток, не
            # успевший за join, дописывает дорожки и папку не удаляет — это
            # сохранение, даже если просили отмену.
            stopped = result.get("folder") or (self._current_folder() if alive else None)
            if stopped:
                self.last_stop = {
                    "folder": str(stopped),
                    "reason": _stop_reason(discard and not alive, hook),
                    "at": time.time(),
                }
            self.recording = False
            source, self.source = self.source, None
            # Обнуляем ссылку только если поток действительно завершился. Иначе
            # доживающий поток (join истёк за 60 с — ffmpeg ещё финализирует)
            # стал бы невидим для _clear_own_lock, и тот снял бы ещё рабочий
            # lock, пустив вторую запись в ту же папку.
            self.thread = None if not alive else thread
        folder = result.get("folder")
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
            shutil.rmtree(folder, ignore_errors=True)
            gone = not Path(folder).exists()
            self.log(f"запись отменена, папка {'удалена' if gone else 'удалена не до конца'}: {folder}")
            self._notify(f"{'Удалено' if gone else 'Удалено частично'}: {folder}")
            # Отдельное событие после удаления: recorder уже отправил
            # record.stopped{folder} (он про discard не знает), и панель успела
            # предложить расшифровать уже удалённую папку. Это отменяет предложение.
            self.bus.emit(events.RECORD_DISCARDED, folder=str(folder))
        else:
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

    # --- пункты меню ----------------------------------------------------

    def _on_start(self, icon=None, item=None) -> None:
        if self.start_recording(MANUAL):
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
        try:
            data = json.loads((_out_root() / LOCK_NAME).read_text(encoding="utf-8"))
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
                f"грейс {self.cfg['grace_seconds']:.0f} с"
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
            # Нарочно: запись с ассистентом уже пишет этот звонок.
            self.log("звонок начался, пишет ассистент — не вмешиваюсь")
            return
        left = self._retry_after - time.monotonic()
        if left > 0:
            self.log(f"предыдущая попытка сорвалась — повторю через {left:.0f} с")
            self.watcher.release()  # чтобы попытка повторилась на этом же звонке
            return
        if not self.start_recording(AUTO):
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
        self.stop_recording(hook=call_seconds >= self.cfg["min_call_seconds"])

    # --- UI -------------------------------------------------------------

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
                "Остановить запись", self._on_stop, visible=lambda i: self.recording
            ),
            pystray.MenuItem(
                "Отменить (удалить)", self._on_cancel, visible=lambda i: self.recording
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

        Ассистент первым и с ожиданием (до 60 с, дальше убийство дерева): он
        пишет встречу, и осиротевший держал бы микрофон и lock записи. Его
        `live.stopped` ставит расшифровку — очередь ещё жива. Очереди задач —
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
        for queue in (api.state.queue, api.state.llm_queue):
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
        return server

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
        TrayApp(start_now=False).run_headless(parent_pid=parent)
        return
    start_now = "--watch" not in args
    if _resident_alive():
        if start_now:
            _send_command("start")
        return
    TrayApp(start_now=start_now).run()
