"""Поиск провайдеров модели на машине — без их импорта.

Модуль читает резидент, поэтому здесь только stdlib: ни claude_agent_sdk, ни
aiohttp. `available()` — быстрая проверка «установлено/отвечает»;
`logged_in()` спрашивает CLI о входе (доли секунды, квоту не тратит) и
нужен выбору `auto` и команде «Проверить».

OpenCode (opencode.ai) ставится по-разному: npm -g (`opencode-ai`: в PATH —
сценарий opencode.cmd, настоящая программа — в node_modules рядом), scoop,
choco, mise, установщик `curl … | bash` (`~/.opencode/bin`), Homebrew.
Пути сверены с исходниками opencode (install, script/postinstall.mjs, 2026-10).
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from meet.settings import DEFAULT_LOCAL_BASE_URL

_WINDOWS = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _WINDOWS else 0
_BATCH_SUFFIXES = (".cmd", ".bat")

# macOS/Linux: куда ставят CLI, если PATH их не видит. Приложение, открытое из
# Finder или Dock, получает PATH launchd (`/usr/bin:/bin:/usr/sbin:/sbin`), а
# не PATH терминала. Оболочка подмешивает PATH входа (`$SHELL -ilc`), а это —
# запасной путь, если она не смогла. `~` — домашняя папка.
_UNIX_DIRS = (
    "~/.local/bin",          # родной установщик Claude Code
    "~/.claude/local",       # прежняя «локальная» установка Claude Code
    "/opt/homebrew/bin",     # Homebrew на Apple Silicon (и npm -g от его node)
    "/usr/local/bin",        # Homebrew на Intel, npm -g от node с nodejs.org
    "~/.npm-global/bin",     # npm -g с prefix в домашней папке
    "~/.bun/bin",
    "~/.volta/bin",
    "~/.opencode/bin",       # установщик OpenCode (curl -fsSL https://opencode.ai/install | bash)
)

# Страница установки OpenCode — подсказка «не найден».
OPENCODE_INSTALL_URL = "https://opencode.ai/docs/"
OPENCODE_NOT_FOUND = f"не найден OpenCode (opencode) — установите: {OPENCODE_INSTALL_URL}"
# npm-пакет OpenCode и его платформенные пакеты с настоящей программой
# (postinstall копирует её в bin/opencode.exe; без postinstall она лежит в
# пакете платформы — его выбирает сценарий bin/opencode).
_OPENCODE_NPM = "opencode-ai"
_OPENCODE_WIN_PACKAGES = ("opencode-windows-x64", "opencode-windows-x64-baseline",
                          "opencode-windows-arm64")


def _home() -> str:
    return os.environ.get("HOME") or str(Path.home())


def _unix_dirs(home: str) -> list[Path]:
    """Папки установки CLI на macOS/Linux, `~` — `home`; у nvm — все версии
    node, новые первыми."""
    dirs = [Path(home + d[1:]) if d.startswith("~") else Path(d) for d in _UNIX_DIRS]
    nvm = Path(home) / ".nvm" / "versions" / "node"
    try:
        versions = sorted(nvm.iterdir(), key=_version_key, reverse=True)
    except OSError:
        versions = []
    return dirs + [v / "bin" for v in versions]


def _version_key(folder: Path) -> tuple[int, ...]:
    """«v22.1.0» → (22, 1, 0): v9 старше v22 только по строке."""
    return tuple(int(n) for n in re.findall(r"\d+", folder.name))


def _unix_fallback(name: str) -> str | None:
    """CLI `name` в обычных папках установки (исполняемый файл). Папку найденного
    — в конец PATH процесса: npm-сценарий (`#!/usr/bin/env node`) ищет node
    там же, где лежит сам."""
    for d in _unix_dirs(_home()):
        exe = d / name
        if exe.is_file() and os.access(exe, os.X_OK):
            path = os.environ.get("PATH", "")
            if str(d) not in path.split(os.pathsep):
                os.environ["PATH"] = f"{path}{os.pathsep}{d}" if path else str(d)
            return str(exe)
    return None


def claude_not_found(detail: bool = False) -> str:
    """Текст «Claude Code CLI не найден» под ОС. `detail` — с подсказкой: на
    Windows годится только родной claude.exe."""
    if not _WINDOWS:
        return ("не найден Claude Code CLI (claude) — ни в PATH, ни в обычных папках "
                "установки (~/.local/bin, Homebrew, npm)")
    if detail:
        return ("не найден Claude Code CLI (claude.exe); npm-шим claude.cmd "
                "не подходит — нужна родная установка Claude Code")
    return "не найден Claude Code CLI (claude.exe)"


def find_claude() -> str | None:
    """Путь к Claude Code CLI, который примет claude-agent-sdk.

    На Windows — только claude.exe: SDK отказывается запускать .cmd/.bat-шим
    от npm (`_reject_windows_batch_cli`), а `shutil.which('claude')` может
    найти bash-скрипт, который CreateProcess не исполняет (agent-sdk #252).
    Такая установка считается «не найден» — `auto` перейдёт к следующему.
    На macOS/Linux — PATH, затем обычные папки установки (`_UNIX_DIRS`).
    """
    if not _WINDOWS:
        return shutil.which("claude") or _unix_fallback("claude")
    p = shutil.which("claude.exe")
    if p and not p.lower().endswith(_BATCH_SUFFIXES):
        return p
    home = os.environ.get("USERPROFILE")
    if home:
        exe = Path(home) / ".local" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return None


def find_codex() -> str | None:
    """Codex CLI в PATH (родной codex.exe раньше npm-шима), иначе — место
    установки по умолчанию на Windows, на macOS/Linux — обычные папки
    установки (`_UNIX_DIRS`)."""
    for name in ("codex.exe", "codex.cmd", "codex"):
        p = shutil.which(name)
        if p:
            return p
    local = os.environ.get("LOCALAPPDATA")
    if local:
        exe = Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
        if exe.exists():
            return str(exe)
    if not _WINDOWS:
        return _unix_fallback("codex")
    return None


def _opencode_behind_npm(node_modules: Path) -> str | None:
    """Настоящая opencode.exe в node_modules npm (рядом со сценарием opencode.cmd)."""
    pkg = node_modules / _OPENCODE_NPM
    candidates = [pkg / "bin" / "opencode.exe"]
    for name in _OPENCODE_WIN_PACKAGES:
        candidates += [pkg / "node_modules" / name / "bin" / "opencode.exe",
                       node_modules / name / "bin" / "opencode.exe"]
    for exe in candidates:
        if exe.is_file():
            return str(exe)
    return None


def find_opencode() -> str | None:
    """Путь к OpenCode CLI.

    Windows: opencode.exe в PATH (scoop, choco, mise, своя установка); иначе
    программа за npm-сценарием opencode.cmd (в node_modules рядом с ним) —
    вкладка «Агент» запускает только .exe; затем обычные папки установки
    (`~/.opencode/bin`, npm в %APPDATA%, scoop, choco); последним — сам
    сценарий opencode.cmd (фоновым задачам годится и он). macOS/Linux — PATH,
    затем обычные папки установки (`_UNIX_DIRS`)."""
    if not _WINDOWS:
        return shutil.which("opencode") or _unix_fallback("opencode")
    exe = shutil.which("opencode.exe")
    if exe:
        return exe
    shim = shutil.which("opencode.cmd")
    if shim:
        native = _opencode_behind_npm(Path(shim).parent / "node_modules")
        if native:
            return native
    home = os.environ.get("USERPROFILE")
    appdata = os.environ.get("APPDATA")
    program_data = os.environ.get("ProgramData")
    if home and (Path(home) / ".opencode" / "bin" / "opencode.exe").is_file():
        return str(Path(home) / ".opencode" / "bin" / "opencode.exe")
    if appdata:
        native = _opencode_behind_npm(Path(appdata) / "npm" / "node_modules")
        if native:
            return native
    places: list[Path] = []
    if home:
        places.append(Path(home) / "scoop" / "shims" / "opencode.exe")
    if program_data:
        places.append(Path(program_data) / "chocolatey" / "bin" / "opencode.exe")
    for place in places:
        if place.is_file():
            return str(place)
    return shim


def local_reachable(base_url: str, timeout: float = 0.5) -> bool:
    """Слушает ли кто-то адрес локальной модели (TCP-соединение, без запроса)."""
    try:
        u = urlparse(base_url)
        host = u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return False
    if not host or u.scheme not in ("http", "https"):
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def available(base_url: str | None = None, probe_local: bool = True) -> dict:
    """Что установлено: CLI найдены, локальная модель отвечает. Без
    `probe_local` локальную модель не спрашиваем (`found: None` — не
    проверялась): это сетевое соединение, а агенту в терминале нужны только
    пути к CLI."""
    url = base_url or DEFAULT_LOCAL_BASE_URL
    claude = find_claude()
    codex = find_codex()
    opencode = find_opencode()
    return {
        "claude-code": {"found": claude is not None, "path": claude},
        "codex": {"found": codex is not None, "path": codex},
        "opencode": {"found": opencode is not None, "path": opencode},
        "openai-compatible": {"found": local_reachable(url) if probe_local else None,
                              "base_url": url},
    }


def env_without_api_key() -> dict:
    """Окружение для Claude CLI: ANTHROPIC_API_KEY перебил бы подписку."""
    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def _tail(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text[-limit:]


def opencode_auth_file() -> Path:
    """Файл ключей OpenCode (`opencode auth login`): `$XDG_DATA_HOME/opencode/auth.json`,
    по умолчанию `~/.local/share/opencode/auth.json` — на всех ОС, и на Windows
    (opencode берёт пути пакета xdg-basedir)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "opencode" / "auth.json"


def opencode_auth_present() -> bool:
    """Есть ли у OpenCode хоть один сохранённый вход: `OPENCODE_AUTH_CONTENT`
    (им OpenCode и сам подменяет файл) или запись с `type` в auth.json.
    Дальше типа записи не смотрим; ключи никуда не выводим."""
    raw = os.environ.get("OPENCODE_AUTH_CONTENT")
    if raw is None:
        try:
            raw = opencode_auth_file().read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    return any(isinstance(v, dict) and isinstance(v.get("type"), str) for v in data.values())


# `opencode auth list` (packages/opencode/src/cli/cmd/providers.ts): итог
# раздела входов — «N credentials», раздела ключей в переменных среды — «N
# environment variable(s)». Это текст для человека, не формат: не нашли — по файлу.
_OC_CREDENTIALS = re.compile(r"(\d+)\s+credentials?\b")
_OC_ENV_KEYS = re.compile(r"(\d+)\s+environment variables?\b")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
OPENCODE_NO_LOGIN = ("в OpenCode нет входа ни в одного провайдера — выполните "
                     "opencode auth login")


def _opencode_logged_in(path: str) -> tuple[bool, str | None]:
    """Вход в OpenCode без вызова модели: `opencode auth list` (входы из auth.json
    и ключи провайдеров в переменных среды). Вывод не распознан или команда не
    отработала — проверяем сам файл ключей (`opencode_auth_present`)."""
    env = dict(os.environ)
    env.update({"OPENCODE_DISABLE_MODELS_FETCH": "1", "NO_COLOR": "1"})
    counts = None
    try:
        done = subprocess.run(
            [path, "auth", "list"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20, env=env, creationflags=_NO_WINDOW,
        )
        out = _ANSI.sub("", done.stdout or "")
        creds, keys = _OC_CREDENTIALS.search(out), _OC_ENV_KEYS.search(out)
        if done.returncode == 0 and creds:
            counts = int(creds.group(1)) + (int(keys.group(1)) if keys else 0)
    except (OSError, subprocess.SubprocessError):
        counts = None
    if counts is None:
        if opencode_auth_present():
            return True, None
        return False, f"{OPENCODE_NO_LOGIN} (нет записей в {opencode_auth_file()})"
    return (True, None) if counts > 0 else (False, OPENCODE_NO_LOGIN)


def opencode_model_listed(path: str, model: str, proxy: str | None = None) -> tuple[bool, str | None]:
    """Есть ли модель «провайдер/модель» у OpenCode: `opencode models <провайдер>`
    печатает модели подключённых провайдеров строками «провайдер/модель»
    (packages/opencode/src/cli/cmd/models.ts); неподключённый провайдер —
    ошибка «Provider not found». Модель не вызывается; список моделей OpenCode
    может обновить с models.dev — поэтому с прокси из настроек."""
    from meet import netproxy

    provider = model.split("/", 1)[0]
    env = netproxy.child_env(proxy)
    env["NO_COLOR"] = "1"
    try:
        done = subprocess.run(
            [path, "models", provider], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, env=env, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"OpenCode не ответил на opencode models: {type(e).__name__}: {e}"
    out = _ANSI.sub("", done.stdout or "")
    err = _ANSI.sub("", done.stderr or "")
    if done.returncode != 0:
        if "provider not found" in (err + out).lower():
            return False, (f"провайдер {provider} не подключён в OpenCode — выполните "
                           "opencode auth login")
        return False, _tail(err or out) or f"код выхода {done.returncode}"
    if model in {line.strip() for line in out.splitlines()}:
        return True, None
    return False, (f"у OpenCode нет модели {model} — список: opencode models {provider}")


def logged_in(name: str, path: str) -> tuple[bool, str | None]:
    """Вошёл ли пользователь в CLI провайдера. (True, None) или (False, текст)."""
    if name == "opencode":
        return _opencode_logged_in(path)
    if name == "codex":
        cmd = [path, "login", "status"]
        env = None
    elif name == "claude-code":
        cmd = [path, "auth", "status", "--json"]
        env = env_without_api_key()
    else:
        return True, None
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20, env=env, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"
    out = (done.stdout or "").strip()
    err = (done.stderr or "").strip()
    if name == "claude-code":
        try:
            status = json.loads(out)
            ok = bool(status.get("loggedIn"))
        except (ValueError, AttributeError):
            status, ok = None, False
        if done.returncode == 0 and ok:
            return True, None
        if isinstance(status, dict):
            # Сам JSON не показываем: в нём почта и организация.
            return False, "вход в Claude Code не выполнен (claude auth login)"
        return False, _tail(err or out) or f"код выхода {done.returncode}"
    if done.returncode == 0:
        return True, None
    return False, _tail(err or out) or f"код выхода {done.returncode}"
