"""Поиск провайдеров модели на машине — без их импорта.

Модуль читает резидент, поэтому здесь только stdlib: ни claude_agent_sdk, ни
aiohttp. `available()` — быстрая проверка «установлено/отвечает»;
`logged_in()` спрашивает CLI о входе (доли секунды, квоту не тратит) и
нужен выбору `auto` и команде «Проверить».
"""

import json
import os
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


def find_claude() -> str | None:
    """Путь к Claude Code CLI, который примет claude-agent-sdk.

    На Windows — только claude.exe: SDK отказывается запускать .cmd/.bat-шим
    от npm (`_reject_windows_batch_cli`), а `shutil.which('claude')` может
    найти bash-скрипт, который CreateProcess не исполняет (agent-sdk #252).
    Такая установка считается «не найден» — `auto` перейдёт к следующему.
    """
    if not _WINDOWS:
        return shutil.which("claude")
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
    установки по умолчанию на Windows."""
    for name in ("codex.exe", "codex.cmd", "codex"):
        p = shutil.which(name)
        if p:
            return p
    local = os.environ.get("LOCALAPPDATA")
    if local:
        exe = Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
        if exe.exists():
            return str(exe)
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


def available(base_url: str | None = None) -> dict:
    """Что установлено: CLI найдены, локальная модель отвечает."""
    url = base_url or DEFAULT_LOCAL_BASE_URL
    claude = find_claude()
    codex = find_codex()
    return {
        "claude-code": {"found": claude is not None, "path": claude},
        "codex": {"found": codex is not None, "path": codex},
        "openai-compatible": {"found": local_reachable(url), "base_url": url},
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
