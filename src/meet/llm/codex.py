"""Провайдер Codex CLI: `codex exec` в песочнице только-чтение.

Сессий нет: `resume` и `session_id` игнорируются, память диалога вызывающий кладёт в prompt.
Рабочая папка — база знаний (первая существующая из `allowed_dirs[1:]`),
иначе `cwd`. Флаги сверены с `codex exec --help` (codex-cli 0.159.0).
`model` и `max_turns` — понятия Claude; Codex берёт модель из своего конфига.
`effort` — усилие рассуждения на один вызов (`-c model_reasoning_effort=…`),
для «Быстрее» в живых подсказках; None — как в конфиге Codex.
"""

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

from meet import netproxy
from meet.llm.base import EMPTY_ERROR, TIMEOUT_ERROR, AgentReply, drop_session_markers
from meet.llm.detect import find_codex

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_ERR_LIMIT = 500


def _kill_tree(proc) -> None:
    """Codex запускает дочерние процессы — убиваем всё дерево."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10, creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _workdir(allowed_dirs, cwd) -> str:
    for d in tuple(allowed_dirs)[1:]:
        if d and Path(d).is_dir():
            return str(d)
    if cwd and Path(cwd).is_dir():
        return str(cwd)
    if allowed_dirs and allowed_dirs[0] and Path(allowed_dirs[0]).is_dir():
        return str(allowed_dirs[0])
    return tempfile.gettempdir()


def _exec(exe: str, workdir: str, stdin_text: str, timeout_s: float,
          env: dict | None = None, effort: str | None = None) -> AgentReply:
    # ignore_cleanup_errors: убитый по таймауту Codex может ещё держать файл.
    with tempfile.TemporaryDirectory(prefix="meet-codex-",
                                     ignore_cleanup_errors=True) as tmp:
        out_file = Path(tmp) / "last-message.txt"
        cmd = [
            exe, "exec",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            # Транскрипты встреч не оседают в ~/.codex/sessions.
            "--ephemeral",
            "-C", workdir,
            "--output-last-message", str(out_file),
            *(["-c", f'model_reasoning_effort="{effort}"'] if effort else []),
            "-",
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=_NO_WINDOW, env=env,
            )
        except OSError as e:
            return AgentReply(text="", error=f"не удалось запустить Codex: {e}")
        try:
            _, err = proc.communicate(input=stdin_text.encode("utf-8"), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
            return AgentReply(text="", error=TIMEOUT_ERROR)
        stderr = (err or b"").decode("utf-8", errors="replace").strip()
        try:
            text = out_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
    if proc.returncode != 0:
        return AgentReply(
            text="",
            error=stderr[-_ERR_LIMIT:] or f"Codex завершился с кодом {proc.returncode}",
        )
    if not text:
        return AgentReply(text="", error=stderr[-_ERR_LIMIT:] or EMPTY_ERROR)
    return AgentReply(text=text)


async def run(
    prompt: str,
    *,
    system_prompt: str,
    model: str | None = None,
    resume: str | None = None,
    session_id: str | None = None,
    allowed_dirs: tuple = (),
    cwd: str | Path | None = None,
    timeout_s: float = 180.0,
    max_turns: int = 8,
    on_text=None,
    proxy: str | None = None,
    effort: str | None = None,
) -> AgentReply:
    """Один вызов `codex exec`; ошибки — в AgentReply.error. `proxy` —
    `llm.proxy`: Codex системный прокси Windows сам не видит."""
    exe = find_codex()
    if exe is None:
        return AgentReply(text="", error="не найден Codex CLI (codex)")
    stdin_text = f"{system_prompt}\n\n{prompt}"
    env = netproxy.child_env(proxy)
    drop_session_markers(env)  # сеанс сам по себе, не «вложенный» (llm.base)
    reply = await asyncio.to_thread(
        _exec, exe, _workdir(allowed_dirs, cwd), stdin_text, timeout_s, env, effort,
    )
    reply.error = netproxy.with_hint(reply.error)
    return reply
