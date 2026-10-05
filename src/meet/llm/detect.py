"""Поиск провайдеров модели на машине — без их импорта.

Модуль читает резидент, поэтому здесь только stdlib: ни claude_agent_sdk, ни
aiohttp. `available()` — быстрая проверка «установлено/отвечает»;
`logged_in()` спрашивает CLI о входе (доли секунды, квоту не тратит) и
нужен выбору `auto` и команде «Проверить».
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
)


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
        return ("не найден Claude Code CLI (claude) — ни в PATH, "
                "ни в ~/.local/bin и /opt/homebrew/bin")
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
    return {
        "claude-code": {"found": claude is not None, "path": claude},
        "codex": {"found": codex is not None, "path": codex},
        "openai-compatible": {"found": local_reachable(url) if probe_local else None,
                              "base_url": url},
    }


def env_without_api_key() -> dict:
    """Окружение для Claude CLI: ANTHROPIC_API_KEY перебил бы подписку."""
    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def _tail(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text[-limit:]


def logged_in(name: str, path: str) -> tuple[bool, str | None]:
    """Вошёл ли пользователь в CLI провайдера. (True, None) или (False, текст)."""
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
