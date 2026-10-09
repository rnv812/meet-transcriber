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

Параллельный прогон (`-n auto`, pytest-xdist): процессов не больше
`MAX_WORKERS` — каждый при сборе грузит тяжёлые модули, и на 20 ядрах при
10 ГБ свободной памяти `-n 8` уже падал с MemoryError.
"""

import os
import sys

# Один поток BLAS на процесс тестов — до первого импорта numpy. Иначе OpenBLAS на
# каждом процессе xdist держит буферы на все ядра; при занятой памяти машины
# (локальная модель, WSL) он падает «Memory allocation still failed».
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import pytest  # noqa: E402

MAX_WORKERS = 6


def pytest_xdist_auto_num_workers(config):
    """`-n auto` — по ядрам, но не больше `MAX_WORKERS` (память)."""
    return max(1, min(os.cpu_count() or 1, MAX_WORKERS))


def pytest_collection_modifyitems(config, items):
    """CI делит набор на куски по машинам: MEET_TEST_SHARD="k/n" (`_shard`)."""
    from _shard import ENV, in_shard, parse_shard

    shard = parse_shard(os.environ.get(ENV, ""))
    if shard is None:
        return
    keep = [item for item in items if in_shard(item.nodeid, shard)]
    dropped = [item for item in items if not in_shard(item.nodeid, shard)]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = keep


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


@pytest.fixture(autouse=True)
def _isolated_agent_homes(monkeypatch, tmp_path_factory):
    """Папки настроек Claude Code, Codex и OpenCode — временные: «Остановить без
    сохранения» и временная встреча забывают сеансы и проект вкладки «Агент»
    (`claude.forget_project` читает `history.jsonl`), и тесты не должны даже
    читать настоящие `~/.claude` и `~/.codex` разработчика. Тесты со своими
    папками ставят переменные сами."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path_factory.mktemp("codex-home")))
    # OpenCode хранит сеансы в `$XDG_DATA_HOME/opencode` (по умолчанию
    # ~/.local/share/opencode): тоже временная.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("xdg-data")))


@pytest.fixture(autouse=True)
def _isolated_repo_root(monkeypatch, tmp_path_factory):
    """Корень репозитория dev-режима (`paths.repo_root()`) — пустая временная
    папка с `pyproject.toml`: режим остаётся dev, но `voices/`, `recordings/`,
    `hotwords.txt` и `glossary.txt` по умолчанию лежат в ней, а не в настоящем
    репозитории. Иначе старая база голосов разработчика подмешивалась бы в тесты
    (образцы другой размерности), а замки и файлы тестов оседали бы в ней.
    Тесты со своей папкой голосов переопределяют `recording.voices` как раньше."""
    from meet import paths

    root = tmp_path_factory.mktemp("repo-root")
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(paths, "repo_root", lambda: root)
    return root


@pytest.fixture
def memory_keyring(_isolated_secrets):
    """In-memory бэкенд текущего теста: `.store[(service, username)]`."""
    return _isolated_secrets


@pytest.fixture(autouse=True)
def _isolated_hf_cache(monkeypatch, tmp_path_factory):
    """Кэш Hugging Face — пустая временная папка: скачанная на машине модель
    диаризации иначе грузилась бы с диска без токена (meet.diarize) там, где
    тест ждёт «нет токена — без спикеров». Кому нужна настоящая модель
    (диаризации, а в будущем и Whisper, выравнивания: они теперь тоже ищут
    себя в кэше), ставит `HF_HUB_CACHE` сам — на кэш, прочитанный при импорте
    модуля теста (`models.cache_root()`), как tests/test_diarize_fast.py."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path_factory.mktemp("hf-cache")))


@pytest.fixture(autouse=True)
def _no_diarize_log_sink(monkeypatch):
    """Приёмник строк диаризации, который ставит подпроцесс задачи
    (job_worker), не переживает тест: иначе JSON-строки `log` попадали бы в
    вывод следующих тестов."""
    try:
        from meet import diarize
    except ImportError:
        return
    monkeypatch.setattr(diarize, "_log_sink", None)


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
    try:
        from meet.llm import codex
    except ImportError:
        return
    # Список MCP-серверов Codex (свобода по согласию, 0.3.7) — без запуска CLI.
    monkeypatch.setattr(codex, "_mcp_list_json", lambda exe, env: "[]")
    monkeypatch.setattr(codex, "_mcp_cache", {})


_AGENT_STEMS = ("claude", "codex", "opencode")


def _agent_program(args) -> str | None:
    """Запуск Claude Code, Codex или OpenCode в аргументах процесса: имя программы
    claude*/codex*/opencode* (не *.py — поддельные CLI тестов идут через python) или
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
        if low.endswith("/opencode-ai/bin/opencode"):  # сценарий npm-пакета OpenCode через node
            return arg
    return None


@pytest.fixture(autouse=True)
def _no_agent_spawn(monkeypatch):
    """Ни один тест не запускает Claude Code, Codex и OpenCode: настоящий CLI на машине
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


@pytest.fixture(autouse=True)
def _no_machine_cuda(monkeypatch):
    """Библиотеки CUDA машины разработчика (пакеты nvidia-*, CUDA Toolkit в
    PATH) в тесты не попадают: по умолчанию их «нет», а итог проверки и сбой
    CUDA (`asr.cuda_failed`) не переходят из теста в тест. Кому библиотеки
    нужны, подменяет `asr._library_dirs` / `asr.find_cuda_libraries` сам."""
    from meet import asr

    asr._reset_cuda_state()
    monkeypatch.setattr(asr, "_library_dirs", lambda: [])
    yield
    asr._reset_cuda_state()
