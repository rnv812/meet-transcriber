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


def _signals(monkeypatch, mic, render):
    monkeypatch.setattr(watch, "mic_busy", lambda name: mic)
    monkeypatch.setattr(watch, "render_active", lambda name: render)


def test_in_call_by_microphone_alone(monkeypatch):
    _signals(monkeypatch, True, None)  # pycaw не встал — решаем по микрофону
    assert watch.in_call(["Dion.exe"]) == (True, True, None)


def test_in_call_by_playback_when_muted(monkeypatch):
    # ради этого случая второй сигнал и существует: мьют отпустил микрофон
    _signals(monkeypatch, False, True)
    assert watch.in_call(["Dion.exe"]) == (True, False, True)


def test_not_in_call_when_both_signals_quiet(monkeypatch):
    _signals(monkeypatch, False, False)
    assert watch.in_call(["Dion.exe"]) == (False, False, False)


def test_not_in_call_when_nothing_to_measure(monkeypatch):
    # нечем мерить — молчим, а не выдумываем звонок
    _signals(monkeypatch, None, None)
    assert watch.in_call(["Dion.exe"]) == (False, None, None)


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
