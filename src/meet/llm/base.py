"""Общий контракт провайдеров модели.

Каждый провайдер — async-функция с одной сигнатурой (её уже потребляют
Digester и QAService):

    runner(prompt, *, system_prompt, model, resume, session_id, allowed_dirs,
           cwd, timeout_s, max_turns, on_text, images, keep_session) -> AgentReply

Сеансы (нативное продолжение, v4-design §12):
* `keep_session=True` — начать сохраняемый сеанс провайдера; его id — в
  `AgentReply.session_id`. Claude Code принимает и свой id нового сеанса
  (`session_id`, UUID); Codex и OpenCode id выдают сами.
* `resume=<id>` — продолжить сохранённый сеанс. Не вышло (сеанс неизвестен,
  истёк, CLI отказал до начала хода) — `AgentReply.resume_failed`, ошибка
  начинается с RESUME_ERROR: вызывающий начинает новый сеанс с затравкой
  из журнала.
* Без них — вызов без сохранения сеанса, как раньше. `session_id` без
  `keep_session` у Codex и OpenCode по-прежнему игнорируется.
* Локальная модель сеансов не держит (`supports_resume` — False).

`images` — пути к изображениям для этого сообщения. Видят их Claude Code и
Codex (`llm.vision`); остальные параметр принимают и игнорируют — окно
показывает «модель не видит изображения».
`on_text(кусок)` — текст ответа по мере генерации (Claude Code; Codex и
локальная модель отдают ответ целиком, параметр принимают и не зовут).
`on_text(None)` — новое сообщение модели (после инструмента): показанный
текст был пояснением, ответ начинается заново.

Ошибки не бросаются, а возвращаются в `AgentReply.error`.
"""

import re
import subprocess
import sys
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

# Одинаковый текст таймаута у всех провайдеров: по нему вызывающий отличает
# «модель не успела» от прочих ошибок.
TIMEOUT_ERROR = "таймаут вызова модели"
EMPTY_ERROR = "модель вернула пустой ответ"
# Ход остановлен по просьбе (кнопка «Стоп»): не сбой модели.
CANCELLED_ERROR = "вызов отменён"
# Начало текста ошибки «сохранённый сеанс не продолжить» (+ AgentReply.resume_failed).
RESUME_ERROR = "сеанс модели не продолжить"

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Изображения, которые принимают модели (Anthropic API и Codex): расширение → тип.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}


@dataclass
class AgentReply:
    text: str
    session_id: str | None = None
    error: str | None = None
    # Сколько токенов насчитал сервер (`prompt_tokens`, `completion_tokens`) —
    # только у локальной модели: по `prompt_tokens` видно, что промпт обрезан.
    usage: dict | None = None
    # Продолжить сохранённый сеанс (`resume`) не вышло: сеанс неизвестен,
    # истёк или CLI отказал до начала хода. Вызывающий начинает новый сеанс
    # и кладёт контекст затравкой из журнала.
    resume_failed: bool = False
    # Ход остановлен по просьбе (`Conversation.interrupt`); `text` — что успело прийти.
    cancelled: bool = False


def resume_failure(detail: str | None) -> AgentReply:
    """Ответ «сохранённый сеанс не продолжить» с подробностями CLI."""
    detail = (detail or "").strip()
    return AgentReply(text="", error=f"{RESUME_ERROR}: {detail}" if detail else RESUME_ERROR,
                      resume_failed=True)


def is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID.match(value))


def image_media_type(path) -> str | None:
    """MIME-тип изображения по расширению; не изображение для модели — None."""
    return IMAGE_TYPES.get(Path(str(path)).suffix.lower())


Runner = Callable[..., Awaitable[AgentReply]]


# Метки чужого сеанса Claude Code / Codex, унаследованные процессом (резидент
# запустили из терминала агента или из его команды). Вызовы модели — свежие
# независимые сеансы, а не «вложенные» в чужой: метки связывают их с сеансом,
# который запустил резидент (канал сообщений, порт IDE, id). Сохранение сеанса
# вызовы Claude выключают сами и явно (`--no-session-persistence`, llm.claude),
# а не через метку. Тот же список, что у оболочки (`pty.rs`, SESSION_MARKERS):
# имена из строк claude.exe и codex.exe. Вход и настройки (ANTHROPIC_*,
# CLAUDE_CONFIG_DIR, CODEX_HOME, прокси) не трогаются.
SESSION_MARKERS = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_EVAL_INTERVIEW_SESSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "AI_AGENT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
)


def kill_tree(proc) -> None:
    """Убить процесс CLI со всеми детьми: Codex и OpenCode запускают свои
    процессы, а у npm-сценария (.cmd) прямой ребёнок — cmd.exe."""
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


def run_tree(cmd: list[str], *, timeout: float, **popen) -> tuple[int, bytes, bytes]:
    """Короткая служебная команда CLI: (код выхода, stdout, stderr). Не
    уложилась в `timeout` — дерево процессов убито и брошен
    `subprocess.TimeoutExpired` (`subprocess.run` убил бы только прямого
    ребёнка, а node и opencode.exe за сценарием .cmd остались бы жить)."""
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=_NO_WINDOW, **popen)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    return proc.returncode, out or b"", err or b""


def drop_session_markers(env: MutableMapping[str, str]) -> list[str]:
    """Убрать из `env` метки чужого сеанса (без учёта регистра); → что убрано."""
    wanted = {name.upper() for name in SESSION_MARKERS}
    gone = [key for key in list(env) if key.upper() in wanted]
    for key in gone:
        env.pop(key, None)
    return gone
