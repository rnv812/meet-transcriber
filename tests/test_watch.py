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
    # метки ConsentStore переживают процесс: убитый мид-звонком Дион иначе
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
