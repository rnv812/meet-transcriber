import meet.watch as watch
from meet.watch import NONE, START, STOP, Watcher, busy_from_times


# --- признак «микрофон занят» -------------------------------------------


def test_busy_when_stop_is_zeroed():
    # Windows обнуляет Stop на время использования
    assert busy_from_times(100, 0) is True


def test_busy_when_start_is_newer_than_stop():
    # ...а если не обнуляет — начало позже конца означает то же самое
    assert busy_from_times(200, 100) is True


def test_free_when_stop_is_newer():
    assert busy_from_times(100, 200) is False


def test_unknown_without_both_marks():
    assert busy_from_times(None, 100) is None
    assert busy_from_times(100, None) is None


# --- поиск ключа в ConsentStore ------------------------------------------


class _FakeKey:
    def __init__(self, subkeys=None, values=None):
        self.subkeys = subkeys or {}
        self.values = values or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeReg:
    """Минимальный winreg поверх словаря {имя ключа: {значение: число}}."""

    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, tree):
        self.tree = tree

    def OpenKey(self, root, path):
        if root == self.HKEY_CURRENT_USER:
            if path != watch.CONSENT_MIC:
                raise OSError("нет такого ключа")
            return _FakeKey(subkeys=self.tree)
        if path not in root.subkeys:
            raise OSError("нет такого подключа")
        return _FakeKey(values=root.subkeys[path])

    def EnumKey(self, key, index):
        names = list(key.subkeys)
        if index >= len(names):
            raise OSError("больше нет подключей")
        return names[index]

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise OSError("нет такого значения")
        return key.values[name], 11  # REG_QWORD


def _reg(monkeypatch, tree):
    monkeypatch.setattr(watch, "winreg", _FakeReg(tree))


def test_mic_busy_finds_key_by_exe_suffix(monkeypatch):
    # путь установки на разных машинах отличается — ищем по имени exe
    _reg(monkeypatch, {
        "D:#Soft#Portable#Dion#Dion.exe": {
            "LastUsedTimeStart": 200, "LastUsedTimeStop": 100
        },
    })
    assert watch.mic_busy("Dion.exe") is True


def test_mic_busy_ignores_other_apps(monkeypatch):
    _reg(monkeypatch, {
        "C:#Discord#Discord.exe": {
            "LastUsedTimeStart": 200, "LastUsedTimeStop": 100
        },
    })
    assert watch.mic_busy("Dion.exe") is None


def test_mic_busy_true_if_any_install_path_is_busy(monkeypatch):
    _reg(monkeypatch, {
        "C:#Old#Dion.exe": {"LastUsedTimeStart": 100, "LastUsedTimeStop": 200},
        "C:#New#Dion.exe": {"LastUsedTimeStart": 300, "LastUsedTimeStop": 0},
    })
    assert watch.mic_busy("Dion.exe") is True


def test_mic_busy_false_when_key_exists_but_free(monkeypatch):
    _reg(monkeypatch, {
        "C:#New#Dion.exe": {"LastUsedTimeStart": 100, "LastUsedTimeStop": 200},
    })
    assert watch.mic_busy("Dion.exe") is False


def test_mic_busy_unknown_without_consent_store(monkeypatch):
    class _Broken(_FakeReg):
        def OpenKey(self, root, path):
            raise OSError("ветки нет")

    monkeypatch.setattr(watch, "winreg", _Broken({}))
    assert watch.mic_busy("Dion.exe") is None


# --- сведение сигналов ---------------------------------------------------


def _signals(monkeypatch, mic, render, running=True):
    monkeypatch.setattr(watch, "mic_busy", lambda name: mic)
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: render)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: running)
    return watch.Signals(["Dion.exe"])


def test_in_call_by_microphone_alone(monkeypatch):
    signals = _signals(monkeypatch, True, None)  # pycaw не встал — решаем по микрофону
    assert signals.read(now=0) == (True, True, None)


def test_in_call_by_playback_when_muted(monkeypatch):
    # ради этого случая второй сигнал и существует: мьют отпустил микрофон
    signals = _signals(monkeypatch, False, True)
    assert signals.read(now=0) == (True, False, True)


def test_not_in_call_when_both_signals_quiet(monkeypatch):
    signals = _signals(monkeypatch, False, False)
    assert signals.read(now=0) == (False, False, False)


def test_not_in_call_when_nothing_to_measure(monkeypatch):
    # нечем мерить — молчим, а не выдумываем звонок
    signals = _signals(monkeypatch, None, None)
    assert signals.read(now=0) == (False, None, None)


def test_signals_merge_several_processes(monkeypatch):
    busy = {"Teams.exe": True, "Dion.exe": False}
    monkeypatch.setattr(watch, "mic_busy", lambda name: busy[name])
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: False)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: True)
    signals = watch.Signals(["Dion.exe", "Teams.exe"])
    assert signals.read(now=0) == (True, True, False)


# --- стейт-машина --------------------------------------------------------


def test_starts_when_call_begins():
    w = Watcher(grace_seconds=180)
    assert w.poll(True, now=0) == START


def test_does_not_start_twice_while_call_continues():
    w = Watcher(grace_seconds=180)
    w.poll(True, now=0)
    assert w.poll(True, now=1) == NONE
    assert w.poll(True, now=2) == NONE


def test_grace_keeps_recording_until_it_expires():
    w = Watcher(grace_seconds=180)
    w.poll(True, now=0)
    assert w.poll(False, now=10) == NONE  # звонок кончился — грейс пошёл
    assert w.poll(False, now=100) == NONE
    assert w.poll(False, now=189) == NONE  # 179 с грейса — ещё пишем
    assert w.poll(False, now=190) == STOP  # 180 с прошло


def test_return_within_grace_never_stops_recording():
    # выкинуло по сети на две минуты — запись не должна порваться надвое
    w = Watcher(grace_seconds=180)
    w.poll(True, now=0)
    w.poll(False, now=10)
    assert w.poll(True, now=130) == NONE  # вернулся
    assert w.state == Watcher.RECORDING
    assert w.poll(True, now=400) == NONE  # старый дедлайн не выстрелил
    assert w.poll(False, now=500) == NONE  # грейс отсчитывается заново
    assert w.poll(False, now=679) == NONE
    assert w.poll(False, now=680) == STOP


def test_stop_is_issued_once():
    w = Watcher(grace_seconds=60)
    w.poll(True, now=0)
    w.poll(False, now=0)
    assert w.poll(False, now=60) == STOP
    assert w.poll(False, now=61) == NONE
    assert w.state == Watcher.IDLE


def test_next_call_starts_again():
    w = Watcher(grace_seconds=60)
    w.poll(True, now=0)
    w.poll(False, now=0)
    w.poll(False, now=60)
    assert w.poll(True, now=100) == START


# --- журнал --------------------------------------------------------------


def test_log_appends_lines(tmp_path):
    log = watch.WatchLog(tmp_path / "watch.log")
    log("первая")
    log("вторая")
    lines = (tmp_path / "watch.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("первая")


def test_log_rotates_when_too_big(tmp_path):
    path = tmp_path / "watch.log"
    path.write_text("x" * (watch.LOG_MAX_BYTES + 1), encoding="utf-8")
    watch.WatchLog(path)("после ротации")
    assert (tmp_path / "watch.log.old").exists()
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_log_survives_unwritable_path(tmp_path):
    # журнал не должен ронять запись, даже если писать некуда
    watch.WatchLog(tmp_path / "нет" / "такой" / "папки" / "watch.log")("строка")


def test_default_log_path_follows_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert watch.default_log_path() == tmp_path / "meet" / "watch.log"


def test_log_rotates_while_running(monkeypatch, tmp_path):
    # резидент живёт месяцами: ротация только при старте означала бы, что
    # журнал не ротируется никогда
    monkeypatch.setattr(watch, "LOG_MAX_BYTES", 200)
    monkeypatch.setattr(watch, "LOG_CHECK_EVERY", 5)
    path = tmp_path / "watch.log"
    log = watch.WatchLog(path)
    for i in range(40):
        log("строка достаточной длины, чтобы быстро набрать лимит " + str(i))
    log("после ротации")  # файл переименован — новый заводится следующей строкой
    assert (tmp_path / "watch.log.old").exists()
    assert path.stat().st_size <= 200 * 5  # активный журнал не растёт без предела


# --- сброс стейт-машины --------------------------------------------------


def test_release_allows_restart_within_the_same_call():
    # запись остановили руками или она не стартовала: без сброса машина
    # осталась бы в RECORDING до конца звонка, а он может идти два часа
    w = Watcher(grace_seconds=180)
    w.poll(True, now=0)
    w.release()
    assert w.state == Watcher.IDLE
    assert w.poll(True, now=1) == START


# --- живость процесса ----------------------------------------------------


def test_signals_ignore_stale_registry_marks_when_process_is_gone(monkeypatch):
    # метки ConsentStore переживают процесс: убитый мид-звонком клиент иначе
    # означал бы «в звонке» бессрочно, и запись шла бы вечно
    monkeypatch.setattr(watch, "mic_busy", lambda name: True)
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: False)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: False)
    signals = watch.Signals(["Dion.exe"])
    assert signals.read(now=0)[0] is False


def test_signals_trust_marks_when_process_lives(monkeypatch):
    monkeypatch.setattr(watch, "mic_busy", lambda name: True)
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: False)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: True)
    signals = watch.Signals(["Dion.exe"])
    assert signals.read(now=0)[0] is True


def test_signals_do_not_block_detection_without_psutil(monkeypatch):
    monkeypatch.setattr(watch, "mic_busy", lambda name: True)
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: None)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: None)
    signals = watch.Signals(["Dion.exe"])
    assert signals.read(now=0)[0] is True


# --- троттлинг дорогого сигнала -------------------------------------------


def test_render_is_polled_less_often_than_the_microphone(monkeypatch):
    # перечисление аудио-сессий течёт нативной памятью и стоит ~18 мс —
    # опрашивать его на каждом такте нельзя
    mic_calls, render_calls = [], []
    monkeypatch.setattr(watch, "mic_busy", lambda name: mic_calls.append(name) or False)
    monkeypatch.setattr(
        watch, "render_active",
        lambda name, log=None: render_calls.append(name) or False,
    )
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: True)
    signals = watch.Signals(["Dion.exe"], render_period=6.0)
    for tick in range(0, 12, 2):  # такты 0,2,4,6,8,10 — шесть опросов
        signals.read(now=tick)
    assert len(mic_calls) == 6
    assert len(render_calls) == 2  # на 0 и на 6


def test_render_value_is_kept_between_polls(monkeypatch):
    monkeypatch.setattr(watch, "mic_busy", lambda name: False)
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: True)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: True)
    signals = watch.Signals(["Dion.exe"], render_period=6.0)
    assert signals.read(now=0) == (True, False, True)
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: 1 / 0)
    assert signals.read(now=1)[0] is True  # значение взято из кэша, не опрошено


def test_com_flag_is_not_set_on_failure(monkeypatch):
    # разовый сбой CoInitialize не должен отключать сигнал до перезапуска
    monkeypatch.setattr(watch, "_com_ready", False)
    import builtins

    real_import = builtins.__import__

    def broken(name, *args, **kwargs):
        if name == "comtypes":
            raise ImportError("нет comtypes")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", broken)
    assert watch._ensure_com() is False
    assert watch._com_ready is False


# --- звонки в браузере ----------------------------------------------------


def _browser_signals(monkeypatch, *, mic, render=True, pids=None, titles=(),
                     require_site=False, sites=("Dion", "Meet –", "Телемост", "Яндекс Телемост"),
                     min_mic_s=0.0):
    """Сигналы с одним настольным клиентом (молчит) и одним браузером.

    `mic` — занят ли микрофон браузером; `render` — играет ли браузер (видео):
    для браузера этот сигнал не должен значить ничего."""
    monkeypatch.setattr(watch, "mic_busy", lambda name: mic if name == "chrome.exe" else False)
    monkeypatch.setattr(watch, "render_active",
                        lambda name, log=None: render if name == "chrome.exe" else False)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: True)
    monkeypatch.setattr(watch, "browser_pids", lambda name: {101} if pids is None else pids)
    seen = []
    monkeypatch.setattr(watch, "window_titles",
                        lambda found: seen.append(set(found)) or list(titles))
    signals = watch.Signals(["Dion.exe"], browsers=["chrome.exe"],
                            require_site=require_site, sites=list(sites), min_mic_s=min_mic_s)
    return signals, seen


def test_browser_with_busy_microphone_is_a_call(monkeypatch):
    signals, _ = _browser_signals(monkeypatch, mic=True, render=False)
    call, mic, _render = signals.read(now=0)
    assert call is True and mic is True
    assert signals.browser_call["exe"] == "chrome.exe"


def test_browser_playing_video_without_microphone_is_not_a_call(monkeypatch):
    # видео и музыка в браузере — не звонок: render для браузеров не считается
    signals, seen = _browser_signals(monkeypatch, mic=False, render=True)
    assert signals.read(now=0)[0] is False
    assert signals.browser_call is None
    assert seen == []  # окна не перечисляются, пока микрофон свободен


def test_browser_render_is_never_polled(monkeypatch):
    polled = []
    signals, _ = _browser_signals(monkeypatch, mic=False)
    monkeypatch.setattr(watch, "render_active",
                        lambda name, log=None: polled.append(name) or False)
    signals.read(now=0)
    assert polled == ["Dion.exe"]


def test_browser_stale_microphone_mark_without_process_is_not_a_call(monkeypatch):
    signals, _ = _browser_signals(monkeypatch, mic=True, pids=set())
    assert signals.read(now=0)[0] is False


def test_browser_title_gives_site_and_recording_title(monkeypatch):
    signals, seen = _browser_signals(
        monkeypatch, mic=True,
        titles=["Почта - Google Chrome", "Dion — Планёрка отдела - Google Chrome"])
    assert signals.read(now=0)[0] is True
    assert seen == [{101}]  # только окна процессов этого браузера
    assert signals.browser_call == {
        "exe": "chrome.exe", "site": "Dion", "title": "Dion — Планёрка отдела"}


def test_strict_mode_requires_a_call_site_in_window_titles(monkeypatch):
    signals, _ = _browser_signals(monkeypatch, mic=True, require_site=True,
                                  titles=["Диктофон онлайн - Google Chrome"])
    assert signals.read(now=0)[0] is False
    assert signals.browser_call is None
    assert signals.browser_note == "chrome.exe: микрофон занят, сайта звонка в заголовках окон нет"


def test_strict_mode_with_matching_title_is_a_call(monkeypatch):
    signals, _ = _browser_signals(monkeypatch, mic=True, require_site=True,
                                  titles=["Meet – abc-defg-hij - Google Chrome"])
    assert signals.read(now=0)[0] is True
    assert signals.browser_call["site"] == "Meet"


def test_desktop_call_has_no_browser_details(monkeypatch):
    monkeypatch.setattr(watch, "mic_busy", lambda name: name == "Dion.exe")
    monkeypatch.setattr(watch, "render_active", lambda name, log=None: False)
    monkeypatch.setattr(watch, "process_running", lambda name, log=None: True)
    signals = watch.Signals(["Dion.exe"], browsers=["chrome.exe"])
    assert signals.read(now=0)[0] is True
    assert signals.browser_call is None


# --- сайт и название записи из заголовка окна -------------------------------


def test_match_site_is_case_insensitive_and_prefers_the_longest():
    sites = ["Телемост", "Яндекс Телемост", "Dion"]
    assert watch.match_site("яндекс телемост — встреча", sites) == "Яндекс Телемост"
    assert watch.match_site("DION — встреча", sites) == "Dion"
    assert watch.match_site("Новости", sites) is None
    assert watch.match_site("что угодно", ["", "  "]) is None


def test_call_title_strips_browser_name_and_site():
    assert watch.call_title("Dion — Планёрка отдела - Google Chrome", "Dion") == "Dion — Планёрка отдела"
    assert watch.call_title("Meet – abc-defg-hij — Mozilla Firefox", "Meet –") == "Meet — abc-defg-hij"
    assert watch.call_title("Яндекс Телемост - Яндекс Браузер", "Яндекс Телемост") == "Яндекс Телемост"
    assert watch.call_title("Обзор | Microsoft Teams - Профиль 1 - Microsoft\u200b Edge",
                            "Microsoft Teams") == "Microsoft Teams — Обзор"


def test_call_title_is_sanitized_and_short():
    long = "Dion — " + "очень длинное название встречи " * 10 + "- Google Chrome"
    title = watch.call_title(long, "Dion")
    assert len(title) <= watch.CALL_TITLE_MAX
    assert title.endswith("…")
    assert watch.call_title("Dion\t—\x07  Встреча\n - Opera", "Dion") == "Dion — Встреча"


def test_short_title_for_log_is_truncated():
    assert watch.short("а" * 100, 10) == "ааааааааа…"
    assert watch.short("коротко", 10) == "коротко"


# --- звонок в браузере: дребезг и «липкость» ------------------------------------


def _switch(monkeypatch, *, mic=None, render=None, titles=None):
    """Поменять сигналы браузера посреди теста."""
    if mic is not None:
        monkeypatch.setattr(watch, "mic_busy", lambda name: mic if name == "chrome.exe" else False)
    if render is not None:
        monkeypatch.setattr(watch, "render_active",
                            lambda name, log=None: render if name == "chrome.exe" else False)
    if titles is not None:
        monkeypatch.setattr(watch, "window_titles", lambda found: list(titles))


def test_short_microphone_use_in_the_browser_is_not_a_call(monkeypatch):
    # голосовой поиск, короткое голосовое сообщение
    signals, _ = _browser_signals(monkeypatch, mic=True, min_mic_s=15.0)
    assert signals.read(now=0)[0] is False
    assert signals.read(now=14)[0] is False
    assert "15" in signals.browser_note
    _switch(monkeypatch, mic=False)
    assert signals.read(now=16)[0] is False  # отпустил раньше — счёт заново
    _switch(monkeypatch, mic=True)
    assert signals.read(now=20)[0] is False
    assert signals.read(now=35)[0] is True


def test_strict_call_survives_a_title_change(monkeypatch):
    # начался по сайту в заголовке — человек переключил вкладку, звонок идёт
    signals, _ = _browser_signals(monkeypatch, mic=True, require_site=True,
                                  titles=["Dion — Планёрка - Google Chrome"])
    assert signals.read(now=0)[0] is True
    _switch(monkeypatch, titles=["Почта - Google Chrome"])
    assert signals.read(now=2)[0] is True
    assert signals.browser_call["site"] == "Dion"


def test_established_call_continues_on_playback_when_the_mic_is_released(monkeypatch):
    # веб-клиент на мьюте отпустил микрофон, собеседников слышно
    signals, _ = _browser_signals(monkeypatch, mic=True, render=False)
    assert signals.read(now=0)[0] is True
    _switch(monkeypatch, mic=False, render=True)
    assert signals.read(now=2)[0] is True
    _switch(monkeypatch, render=False)
    assert signals.read(now=10)[0] is False  # ни микрофона, ни звука — звонок кончился
    _switch(monkeypatch, render=True)
    assert signals.read(now=20)[0] is False  # звук без микрофона звонок не начинает


def test_browser_playback_alone_never_starts_a_call(monkeypatch):
    signals, _ = _browser_signals(monkeypatch, mic=False, render=True)
    for now in (0, 10, 20, 30):
        assert signals.read(now=now)[0] is False


def test_call_title_drops_invisible_characters():
    title = watch.call_title("﻿Dion⁠ — Встреча⁤ - Google Chrome", "Dion")
    assert title == "Dion — Встреча"


class _FakeUser32:
    """Окна: (hwnd, pid, видно ли, заголовок) в Z-порядке; передний план — `front`."""

    def __init__(self, windows, front):
        self.windows = windows
        self.front = front

    def EnumWindows(self, callback, _param):
        for hwnd, *_ in self.windows:
            if not callback(hwnd, 0):
                break
        return True

    def _window(self, hwnd):
        return next(w for w in self.windows if w[0] == hwnd)

    def IsWindowVisible(self, hwnd):
        return self._window(hwnd)[2]

    def GetWindowThreadProcessId(self, hwnd, ref):
        ref._obj.value = self._window(hwnd)[1]
        return 1

    def GetWindowTextLengthW(self, hwnd):
        return len(self._window(hwnd)[3])

    def GetWindowTextW(self, hwnd, buf, size):
        buf.value = self._window(hwnd)[3][:size - 1]
        return len(buf.value)

    def GetForegroundWindow(self):
        return self.front


def test_window_titles_put_the_foreground_window_first(monkeypatch):
    windows = [(1, 101, True, "Почта - Google Chrome"),
               (2, 999, True, "Чужое окно"),
               (3, 101, False, "Скрытое"),
               (4, 102, True, "Dion — Планёрка - Google Chrome")]
    monkeypatch.setattr(watch, "_USER32", (_FakeUser32(windows, front=4), lambda fn: fn))
    assert watch.window_titles({101, 102}) == ["Dion — Планёрка - Google Chrome", "Почта - Google Chrome"]


def test_non_strict_title_comes_only_from_a_window_with_a_call_site(monkeypatch):
    # на переднем плане окно без сайта — название записи берётся из окна со звонком
    signals, _ = _browser_signals(monkeypatch, mic=True,
                                  titles=["Почта - Google Chrome", "Телемост — Обзор - Google Chrome"])
    signals.read(now=0)
    assert signals.browser_call["title"] == "Телемост — Обзор"
    signals2, _ = _browser_signals(monkeypatch, mic=True, titles=["Почта - Google Chrome"])
    signals2.read(now=0)
    assert signals2.browser_call == {"exe": "chrome.exe", "site": None, "title": None}
