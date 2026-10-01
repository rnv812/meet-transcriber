"""Общая изоляция тестов от машины разработчика.

Токен Hugging Face живёт в диспетчере учётных данных Windows (`meet.credentials`)
и в переменных среды. Тест не должен ни читать настоящий токен, ни писать в
настоящий диспетчер, ни переносить токен из настоящего `config.json`:

* keyring подменён in-memory бэкендом на каждый тест (и восстановлен после);
* подпроцессы тестов получают нулевой бэкенд через `PYTHON_KEYRING_BACKEND`;
* `HF_TOKEN`/`HUGGING_FACE_HUB_TOKEN` из окружения убраны;
* `LOCALAPPDATA` указывает во временную папку (а `MEET_DATA_DIR` снят):
  `settings.load()` без пути читает не настоящий config.json и не мигрирует
  из него токен. Тесты, которым нужна своя папка, переопределяют одно из двух.
"""

import pytest


def _memory_backend():
    from keyring.backend import KeyringBackend
    from keyring.errors import PasswordDeleteError

    class Memory(KeyringBackend):
        priority = 1

        def __init__(self) -> None:
            super().__init__()
            self.store: dict = {}

        def get_password(self, service, username):
            return self.store.get((service, username))

        def set_password(self, service, username, password):
            self.store[(service, username)] = password

        def delete_password(self, service, username):
            if (service, username) not in self.store:
                raise PasswordDeleteError("нет такой записи")
            del self.store[(service, username)]

    return Memory()


@pytest.fixture(autouse=True)
def _isolated_secrets(monkeypatch, tmp_path_factory):
    import keyring

    previous = keyring.get_keyring()
    backend = _memory_backend()
    keyring.set_keyring(backend)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path_factory.mktemp("localappdata")))
    try:
        from meet import settings

        settings._reset_secret_migration()
    except (ImportError, AttributeError):
        pass
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def memory_keyring(_isolated_secrets):
    """In-memory бэкенд текущего теста: `.store[(service, username)]`."""
    return _isolated_secrets


@pytest.fixture(autouse=True)
def _no_system_proxy(monkeypatch):
    """Системный прокси машины разработчика (реестр WinINET) в тесты не
    попадает: кому он нужен, подменяет `netproxy.read_registry` сам."""
    try:
        from meet import netproxy
    except ImportError:
        return
    monkeypatch.setattr(netproxy, "read_registry", lambda: None)


@pytest.fixture(autouse=True)
def _no_background_playback_mix(monkeypatch):
    """Фоновое сведение дорожек для плеера (после расшифровки/импорта) в тестах
    не запускается: ffmpeg на фальшивых дорожках во временной папке только
    шумел бы. Тесты сведения зовут `playback` напрямую."""
    from meet import playback

    calls: list = []
    monkeypatch.setattr(playback, "schedule", calls.append)
    return calls
