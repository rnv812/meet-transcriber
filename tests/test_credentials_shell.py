"""Токен Hugging Face на macOS: хранит оболочка Meet, `keyring` — запасной путь.

Связка ключей macOS привязывает запись к программе, которая её создала. Python
движка меняется с каждым обновлением — чтение из Python тогда открывает
системное окно и ждёт ответа. Поэтому токен держит оболочка (Meet.app) и
передаёт резиденту переменной среды; `keyring` на Mac зовётся с пределом по
времени. Настоящая связка ключей не трогается: бэкенды — заглушки.
"""

import threading
import time

import keyring
import pytest
from keyring.backend import KeyringBackend

from meet import credentials, plat, settings

TOKEN = "hf_SHELL_secret_789"


class _Recording(KeyringBackend):
    """Бэкенд-заглушка: запоминает вызовы; `gate` держит чтение, как окно
    «python хочет доступ к связке ключей», пока его не отпустят."""

    priority = 1

    def __init__(self, value=None, gate=None):
        super().__init__()
        self.value = value
        self.gate = gate
        self.calls = []
        self.threads = []

    def get_password(self, service, username):
        self.calls.append("get")
        self.threads.append(threading.current_thread())
        if self.gate is not None:
            self.gate.wait(10)
        return self.value

    def set_password(self, service, username, password):
        self.calls.append("set")
        if self.gate is not None:
            self.gate.wait(10)
        self.value = password

    def delete_password(self, service, username):
        self.calls.append("delete")
        if self.gate is not None:
            self.gate.wait(10)
        self.value = None


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    monkeypatch.setattr(credentials, "KEYRING_TIMEOUT_S", 0.2)
    # Зависший вызов прошлого теста к этому отношения не имеет.
    monkeypatch.setattr(credentials, "_waiting", None)


@pytest.fixture
def recording():
    backend = _Recording()
    keyring.set_keyring(backend)  # conftest восстановит прежний
    return backend


@pytest.fixture
def stuck():
    """Связка ключей «ждёт ответа в системном окне»; после теста — отпускаем."""
    gate = threading.Event()
    backend = _Recording(value="hf_from_python_keyring", gate=gate)
    keyring.set_keyring(backend)
    yield backend
    gate.set()


# --- токен от оболочки -------------------------------------------------------------


def test_shell_token_is_used_without_calling_keyring(monkeypatch, recording, mac):
    monkeypatch.setenv(credentials.SHELL_ENV, TOKEN)
    assert credentials.get_hf_token() == TOKEN
    assert credentials.hf_token_source() == credentials.SHELL == "shell"
    assert recording.calls == []


def test_shell_token_wins_over_keyring_env_and_config(monkeypatch, memory_keyring):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    credentials.set_hf_token("hf_keyring_value")
    monkeypatch.setenv("HF_TOKEN", "hf_env_value")
    monkeypatch.setenv(credentials.SHELL_ENV, f"  {TOKEN}\n")
    assert credentials.lookup() == (TOKEN, "shell")


def test_blank_shell_variable_means_no_shell_token(monkeypatch, memory_keyring):
    monkeypatch.setenv(credentials.SHELL_ENV, "  ")
    credentials.set_hf_token("hf_keyring_value")
    assert credentials.lookup() == ("hf_keyring_value", "keyring")


def test_use_shell_token_adopts_it_and_drops_config_copy(monkeypatch, memory_keyring):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    settings.write_hf_token("hf_old_config_copy")
    credentials.use_shell_token(f" {TOKEN} ")
    assert credentials.lookup() == (TOKEN, "shell")
    assert settings.load().integrations.hf_token == ""
    assert memory_keyring.store == {}  # в keyring Python не писали
    credentials.clear_hf_token()
    assert credentials.lookup() == (None, None)


def test_use_shell_token_rejects_empty(monkeypatch):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    with pytest.raises(ValueError):
        credentials.use_shell_token("  ")


# --- запасной путь: keyring с пределом по времени ----------------------------------


def test_mac_without_shell_token_reads_keyring_in_time(monkeypatch, recording, mac):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    recording.value = "hf_python_keyring"
    assert credentials.lookup() == ("hf_python_keyring", "keyring")
    assert recording.calls[0] == "get"


def test_mac_keyring_timeout_is_unavailable_and_does_not_hang(monkeypatch, stuck, mac):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    started = time.monotonic()
    with pytest.raises(credentials.Unavailable, match="токен недоступен"):
        credentials.keyring_get()
    assert time.monotonic() - started < 2


def test_mac_lookup_after_timeout_gives_no_token_quickly(monkeypatch, stuck, mac):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    started = time.monotonic()
    assert credentials.lookup() == (None, None)
    assert credentials.hf_token_source() is None
    assert time.monotonic() - started < 2
    # Окно связки ключей ещё висит — второго не открываем.
    assert stuck.calls == ["get"]


def test_mac_keyring_write_timeout_keeps_token_in_config(monkeypatch, stuck, mac):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    assert credentials.set_hf_token(TOKEN) == "config"
    assert settings.load().integrations.hf_token == TOKEN


def test_mac_keyring_usable_again_after_the_stuck_call_ends(monkeypatch, stuck, mac):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    with pytest.raises(credentials.Unavailable):
        credentials.keyring_get()
    stuck.gate.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            assert credentials.keyring_get() == "hf_from_python_keyring"
            break
        except credentials.Unavailable:
            time.sleep(0.02)
    else:
        pytest.fail("keyring так и не ответил после закрытия окна")


def test_windows_calls_keyring_directly_without_timeout(monkeypatch, recording):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    recording.value = "hf_windows"
    assert credentials.keyring_get() == "hf_windows"
    assert recording.threads == [threading.current_thread()]


# --- секрет не уходит наружу -------------------------------------------------------


def test_token_never_in_errors_or_output(monkeypatch, stuck, mac, capsys, caplog):
    monkeypatch.delenv(credentials.SHELL_ENV, raising=False)
    stuck.value = TOKEN
    caplog.set_level("DEBUG")
    with pytest.raises(credentials.Unavailable) as error:
        credentials.keyring_get()
    assert TOKEN not in str(error.value)
    monkeypatch.setenv(credentials.SHELL_ENV, TOKEN)
    credentials.use_shell_token(TOKEN)
    credentials.lookup()
    credentials.clear_hf_token()
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert TOKEN not in caplog.text
