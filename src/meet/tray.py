"""Трей записи встреч: резидент, который сам поднимает запись на звонке в
Дионе и сам её останавливает, когда звонок кончился.

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

from meet import watch
from meet.output import fmt_ts
from meet.recorder import LOCK_NAME, _pid_alive, record

# recordings от корня репозитория: ярлык запускается с произвольным cwd
OUT_ROOT = Path(__file__).resolve().parents[2] / "recordings"

# окно старта записи, в котором встреча похожа на дейлик (сам дейлик в 11-30)
DAILY_WINDOW = ("11:00", "12:00")

IDLE_COLOR = (130, 130, 130, 255)  # дежурю
REC_COLOR = (40, 110, 220, 255)  # пишу
HEARTBEAT_S = 60.0
TICK_S = 1.0
# Короче этого автозапись считается ложной тревогой (звук уведомления Диона,
# отклонённый вызов) и Claude на неё не зовут: папка остаётся, но окна с
# предложением транскрибировать полминуты тишины не появляется. Длительность
# берётся по звонку, а не по файлу: в файле всегда есть ещё и грейс.
MIN_CALL_S = 120.0
# Пауза перед повторной попыткой автозаписи после сорвавшегося старта: без неё
# трей долбился бы в занятое устройство каждые poll_seconds и засыпал бы
# уведомлениями, а без повтора вовсе — потерял бы встречу целиком.
RETRY_AFTER_S = 30.0

AUTO = "auto"
MANUAL = "manual"


def _state_dir() -> Path:
    """Машинно-локальное состояние трея. Не константа: LOCALAPPDATA читается
    каждый раз, иначе тесты не могут подменить его на tmp_path."""
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "meet"


def _config() -> dict:
    """%LOCALAPPDATA%/meet/config.json — машинно-локальные настройки трея.

    Файла может не быть — тогда пустой конфиг."""
    try:
        return json.loads((_state_dir() / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _flag(value, default: bool) -> bool:
    """Булево из конфига, правленного руками: строковое "false" не должно
    означать True только потому, что непустая строка истинна."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    if value is None:
        return default
    return bool(value)


def _positive(value, default: float, minimum: float) -> float:
    """Число из конфига. Мусор и значения вне смысла — молча к дефолту:
    трей запускается из автозагрузки под pythonw, и исключение здесь означало
    бы, что резидент не поднялся вообще, без иконки и без строки в журнале."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number < minimum:  # NaN тоже сюда
        return default
    return number


def _auto_config() -> dict:
    """Настройки автозаписи с дефолтами. Секции нет — автозапись выключена,
    но детектор всё равно опрашивается и пишет журнал: так можно неделю
    смотреть на поведение сигналов, ничего не записывая."""
    section = _config().get("auto_record")
    if not isinstance(section, dict):
        section = {}
    processes = section.get("processes")
    if isinstance(processes, str):
        processes = [processes]
    if not isinstance(processes, list) or not processes:
        processes = ["Dion.exe"]
    return {
        "enabled": _flag(section.get("enabled"), False),
        "processes": [str(p) for p in processes],
        "grace_seconds": _positive(section.get("grace_seconds"), watch.GRACE_S, 0.0),
        "poll_seconds": _positive(section.get("poll_seconds"), watch.POLL_S, 0.5),
    }


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


def _in_daily_window(folder: str) -> bool:
    """Похоже ли время записи (из имени папки YYYY-MM-DD_HH-MM) на слот дейлика.

    Только гипотеза для промпта — что за встреча на самом деле, решает
    календарь; см. скилл transcriber (режим дейлика)."""
    m = re.search(r"_(\d{2})-(\d{2})$", Path(folder).name)
    if not m:
        return False
    return DAILY_WINDOW[0] <= f"{m.group(1)}:{m.group(2)}" <= DAILY_WINDOW[1]


def _wt_safe(text: str) -> str:
    """Обезвредить метасимволы командной строки в тексте промпта.

    IMPORTANT: ';' для wt — разделитель команд: хвост после него Windows
    Terminal пытается запустить как программу (ошибка 0x80070002), а первой
    вкладке достаётся обрезанный промпт с незакрытой кавычкой. Апостроф
    закрывает одинарные кавычки powershell — в PS он удваивается."""
    return text.replace(";", ",").replace("'", "''")


def _launch_claude(folder: str) -> None:
    """Post-recording hook: окно терминала с Claude Code, который предлагает
    транскрибировать свежую запись. Включается флагом post_record_hook в
    config.json — на ноуте он выключен: транскрибация идёт на десктопе, куда
    запись потом забирают. Best effort: проблемы запуска не должны мешать
    завершению записи."""
    if not _config().get("post_record_hook"):
        return
    prompt = (
        f"Завершилась запись встречи, папка: {folder}. "
        "Предложи транскрибировать её скиллом my-plugin:transcriber."
    )
    if _in_daily_window(folder):
        prompt += (
            " Время похоже на слот дейлика — сверься с календарём "
            "(scripts/calendar_lookup.ps1) и, если это дейлик, веди его "
            "режимом дейлика из того же скилла; другую встречу в этом "
            "слоте — обычным режимом."
        )
    project_root = OUT_ROOT.parent
    try:
        # powershell с профилем: там proxy-переменные; -NoExit — не закрывать
        # окно при ошибке. claude напрямую, не через шорткат cc: у CLI один
        # позиционный промпт, и "/color red" внутри cc вытесняет наш.
        # Промпт в одинарных кавычках PS, метасимволы — через _wt_safe
        subprocess.Popen(
            [
                "wt",
                "-d",
                str(project_root),
                "powershell",
                "-NoExit",
                "-Command",
                f"claude --permission-mode auto '{_wt_safe(prompt)}'",
            ]
        )
    except OSError:
        pass


def _icon_image(color=REC_COLOR):
    """Кружок 64x64 — рисуем на лету, без файлов-ресурсов.

    Синий, а не красный: красным кружком запись показывает voice-control,
    два одинаковых красных в трее путаются. Серый — дежурю, звонка нет."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=color)
    return img


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
        self.log = watch.WatchLog(watch.default_log_path())
        self.watcher = watch.Watcher(self.cfg["grace_seconds"])
        self.signals = watch.Signals(self.cfg["processes"], log=self.log)
        self.icon = None
        self.start_now = start_now
        self.recording = False
        self.source = None
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
        self._title = None

    # --- запись ---------------------------------------------------------

    def start_recording(self, source: str) -> bool:
        """Поднять запись. False — если запись уже идёт."""
        with self._mutex:
            if self.recording:
                return False
            self._clear_own_lock()
            result: dict = {}
            stop_event = threading.Event()
            self.result = result
            self.stop_event = stop_event
            self.source = source
            self.started = time.monotonic()
            self._call_end = None
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

    def _run_record(self, result: dict, stop_event: threading.Event) -> None:
        try:
            result["folder"] = record(str(OUT_ROOT), stop_event=stop_event)
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
        lock = OUT_ROOT / LOCK_NAME
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
            self.recording = False
            source, self.source = self.source, None
            self.thread = None
            alive = thread is not None and thread.is_alive()
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
        else:
            self.log(f"запись остановлена ({source}): {folder}")
            self._notify(f"Сохранено: {folder}")
            if hook:
                _launch_claude(str(folder))
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
            data = json.loads((OUT_ROOT / LOCK_NAME).read_text(encoding="utf-8"))
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

    # --- цикл -----------------------------------------------------------

    def ticker(self, icon) -> None:
        """Фон pystray: команды от ярлыка, секундомер и опрос детектора."""
        try:
            icon.visible = True
            self.log(
                f"трей запущен, автозапись "
                f"{'включена' if self.cfg['enabled'] else 'выключена'}, "
                f"процессы {', '.join(self.cfg['processes'])}, "
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
        if not self.cfg["enabled"]:
            return
        if decision == watch.START:
            self._auto_start()
        elif decision == watch.STOP:
            self._auto_stop(now)

    def _auto_start(self) -> None:
        if self.recording:
            self.log("звонок начался, запись уже идёт — не вмешиваюсь")
            return
        left = self._retry_after - time.monotonic()
        if left > 0:
            self.log(f"предыдущая попытка сорвалась — повторю через {left:.0f} с")
            self.watcher.release()  # чтобы попытка повторилась на этом же звонке
            return
        if not self.start_recording(AUTO):
            return
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
        self.stop_recording(hook=call_seconds >= MIN_CALL_S)

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
        try:
            self.icon.run(setup=self.ticker)
        finally:
            self._alive = False
            self._release_lock(lock)

    def _release_lock(self, lock: Path) -> None:
        """Снять свой lock — но только свой: за время работы его мог перехватить
        другой экземпляр."""
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            if int(data["pid"]) == os.getpid():
                lock.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError):
            pass


def main() -> None:
    """Без аргументов — начать запись сразу: так работает ярлык «Запись
    встречи», и это поведение сохранено с прежней версии трея. `--watch` —
    дежурить и ждать звонка, с этим флагом трей стоит в автозагрузке.

    Второй экземпляр не поднимает вторую иконку: живому дежурному уходит
    команда, а сам он выходит. Поэтому ярлык работает одинаково — и когда
    дежурный висит, и когда его нет."""
    start_now = "--watch" not in sys.argv[1:]
    if _resident_alive():
        if start_now:
            _send_command("start")
        return
    TrayApp(start_now=start_now).run()
