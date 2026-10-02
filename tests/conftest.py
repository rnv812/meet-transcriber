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
  На macOS папка данных — от `HOME` (`~/Library/Application Support/meet`),
  поэтому там во временную папку указывает и `HOME`.

Временная папка системы (`tempfile`, TMP/TEMP) на всю сессию — своя папка
внутри basetemp pytest: замки meta.json, папки задач и живого режима не
копятся в настоящем %TEMP% разработчика.
"""

import sys

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


@pytest.fixture(autouse=True, scope="session")
def _isolated_temp(tmp_path_factory):
    import tempfile

    root = tmp_path_factory.mktemp("tmproot")  # basetemp уже выбран от настоящего %TEMP%
    saved = tempfile.tempdir
    with pytest.MonkeyPatch.context() as mp:
        for name in ("TMP", "TEMP", "TMPDIR"):
            mp.setenv(name, str(root))
        mp.delenv("MEET_SYSTEM_TEMP", raising=False)
        tempfile.tempdir = str(root)
        try:
            yield root
        finally:
            tempfile.tempdir = saved


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
    if sys.platform == "darwin":
        monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
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


# Адреса, на которые тестам можно подключаться: резидент, control API, живые
# серверы тестов — только локальные.
_LOCAL_HOSTS = {"localhost", "::1", "0.0.0.0", ""}


def _local(host) -> bool:
    host = str(host or "").strip("[]").lower()
    return host in _LOCAL_HOSTS or host.startswith("127.")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Сети в тестах нет: подключение сокета и urlopen к не-локальному адресу
    — ошибка теста (а не загрузка гигабайт моделей с CDN или Hugging Face,
    если в окружении стоит настоящий движок). Локальные адреса работают."""
    import socket
    import urllib.parse
    import urllib.request

    def guard(address) -> None:
        if isinstance(address, tuple) and address and not _local(address[0]):
            raise AssertionError(f"тест полез в сеть: {address!r}")

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, address):
        guard(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        guard(address)
        return real_connect_ex(self, address)

    real_urlopen = urllib.request.urlopen

    def urlopen(url, *args, **kwargs):
        full = url if isinstance(url, str) else getattr(url, "full_url", "")
        guard((urllib.parse.urlparse(full).hostname, 0))
        return real_urlopen(url, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


@pytest.fixture(autouse=True)
def _no_model_process(monkeypatch):
    """Постоянный процесс Claude Code (диалог подсказок) в тестах не
    запускается: настоящий claude.exe на машине разработчика сделал бы
    настоящий вызов модели. Тесты процесса передают свой `cli`."""
    try:
        from meet.llm import claude_stream
    except ImportError:
        return
    monkeypatch.setattr(claude_stream, "default_cli", lambda: None)


_AGENT_STEMS = ("claude", "codex")


def _agent_program(args) -> str | None:
    """Запуск Claude Code или Codex в аргументах процесса: имя программы
    claude*/codex* (не *.py — поддельные CLI тестов идут через python) или
    cli.js пакета Claude Code где-либо в командной строке."""
    import os
    import shlex

    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        try:
            argv = shlex.split(text, posix=False)
        except ValueError:
            argv = [text]
    else:
        argv = [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in args]
    if not argv:
        return None
    name = os.path.basename(argv[0].strip('"')).lower()
    if not name.endswith(".py") and name.split(".")[0] in _AGENT_STEMS:
        return argv[0]
    for arg in argv:
        low = arg.replace("\\", "/").lower()
        if low.endswith("cli.js") and ("claude" in low or "anthropic" in low):
            return arg
    return None


@pytest.fixture(autouse=True)
def _no_agent_spawn(monkeypatch):
    """Ни один тест не запускает Claude Code и Codex: настоящий CLI на машине
    разработчика сделал бы настоящий вызов модели по подписке. Перехвачены
    `subprocess.Popen` (через него идут и asyncio/anyio-подпроцессы),
    `asyncio.create_subprocess_exec` и запуск процесса транспортом Claude
    Agent SDK. Попытка — ошибка запуска в тесте и провал теста в конце, даже
    если код ошибку проглотил. Тестам с агентом — подделки."""
    import asyncio
    import subprocess

    attempts: list[str] = []
    real_init = subprocess.Popen.__init__

    def guarded_init(self, args, *a, **kw):
        program = _agent_program(args)
        if program is not None:
            attempts.append(program)
            raise OSError(f"тест запустил агента: {program}")
        return real_init(self, args, *a, **kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_init)
    real_exec = asyncio.create_subprocess_exec

    async def guarded_exec(program, *args, **kw):
        found = _agent_program([program, *args])
        if found is not None:
            attempts.append(found)
            raise OSError(f"тест запустил агента: {found}")
        return await real_exec(program, *args, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", guarded_exec)
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli
    except ImportError:
        subprocess_cli = None
    if subprocess_cli is not None:
        async def guarded_connect(self):
            attempts.append("claude_agent_sdk: SubprocessCLITransport.connect")
            raise OSError("тест запустил Claude Code через Agent SDK")

        monkeypatch.setattr(subprocess_cli.SubprocessCLITransport, "connect", guarded_connect)
    yield attempts  # проверка самой защиты очищает список после своей попытки
    if attempts:
        pytest.fail(f"тест пытался запустить агента: {attempts}")
