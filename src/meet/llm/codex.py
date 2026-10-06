"""Провайдер Codex CLI: `codex exec` в песочнице только-чтение.

По умолчанию сеанса нет (`--ephemeral`): `session_id` игнорируется, память
диалога вызывающий кладёт в prompt.
Рабочая папка — база знаний (первая существующая из `allowed_dirs[1:]`),
иначе `cwd`. Флаги сверены с `codex exec --help` (codex-cli 0.159.0).
`model` и `max_turns` — понятия Claude; Codex берёт модель из своего конфига.
`effort` — усилие рассуждения на один вызов (`-c model_reasoning_effort=…`),
для «Быстрее» в живых подсказках; None — как в конфиге Codex.

V4 (0.3.6, v4-design §5.1, §12):

* `images` — по флагу `--image=<путь>` на файл. Именно `=`: у `-i <FILE>...`
  много значений, и позиционный `-` (prompt из stdin) съелся бы как ещё
  одно изображение (проверено на 0.159.0 без модели: `-i a.png - x` берёт
  `-` и `x` в изображения, `--image=a.png - x` — «unexpected argument 'x'»).
  Запятая в пути делит значение (`value_delimiter`) — такие файлы не шлём;
* `keep_session=True` — сеанс сохраняется (без `--ephemeral`); его id Codex
  печатает в шапке (`session id: <uuid>`) — он в `AgentReply.session_id`;
* `resume=<uuid>` — `codex exec <флаги exec> resume <флаги resume> <id> -`.
  `--sandbox` и `-C` подкоманда `exec resume` не принимает — они идут до
  неё (проверено разбором с `--help`). Только UUID: имя, которого нет,
  Codex молча начинает новым сеансом (проверено). Неизвестный UUID —
  «thread/resume failed: no rollout found for thread id …», код 1 →
  `resume_failed`.
"""

import asyncio
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from meet import netproxy, tempdirs
from meet.llm.base import (
    CANCELLED_ERROR, EMPTY_ERROR, TIMEOUT_ERROR, AgentReply, drop_session_markers, image_media_type, is_uuid, kill_tree,
    resume_failure,
)
from meet.llm.detect import find_codex

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_ERR_LIMIT = 500
# Шапка `codex exec` (0.159.0): «session id: 01a112eb-…».
_SESSION_LINE = re.compile(r"^\s*session id:\s*([0-9a-fA-F-]{36})\s*$", re.MULTILINE)
# Сохранённого сеанса нет (0.159.0): «thread/resume failed: no rollout found for thread id …».
_RESUME_FAILED = re.compile(r"no rollout found|thread/resume", re.IGNORECASE)


def _kill_tree(proc) -> None:
    """Codex запускает дочерние процессы — убиваем всё дерево (llm.base)."""
    kill_tree(proc)


def _workdir(allowed_dirs, cwd) -> str:
    for d in tuple(allowed_dirs)[1:]:
        if d and Path(d).is_dir():
            return str(d)
    if cwd and Path(cwd).is_dir():
        return str(cwd)
    if allowed_dirs and allowed_dirs[0] and Path(allowed_dirs[0]).is_dir():
        return str(allowed_dirs[0])
    return str(tempdirs.system_temp())


def build_command(exe: str, workdir: str, out_file: str, *, effort: str | None = None,
                  images=(), keep_session: bool = False, resume: str | None = None) -> list[str]:
    """Командная строка `codex exec` (prompt — в stdin, `-` последним).
    Продолжение: песочница и папка — до `resume` (у подкоманды их нет),
    остальное — после неё, id сеанса — перед `-`."""
    head = [exe, "exec", "--sandbox", "read-only", "--skip-git-repo-check"]
    rest = ["--output-last-message", out_file,
            *(["-c", f'model_reasoning_effort="{effort}"'] if effort else []),
            *[f"--image={i}" for i in images or ()]]
    if resume:
        return [*head, "-C", workdir, "resume", *rest, resume, "-"]
    # Транскрипты встреч не оседают в ~/.codex/sessions, если сеанс не нужен.
    keep = [] if keep_session else ["--ephemeral"]
    return [*head, *keep, "-C", workdir, *rest, "-"]


def usable_images(images) -> tuple[list[str], list[str]]:
    """(что отправить, что пропущено): только существующие файлы PNG/JPEG/
    GIF/WebP без запятой в пути (Codex делит значение `--image` по запятым)."""
    sent, skipped = [], []
    for i in images or ():
        if not i:
            continue
        path = str(i)
        if "," in path or image_media_type(path) is None or not Path(path).is_file():
            skipped.append(path)
        else:
            sent.append(path)
    return sent, skipped


def session_from(*streams: str) -> str | None:
    """Id сеанса из шапки `codex exec` (`session id: <uuid>`)."""
    for text in streams:
        m = _SESSION_LINE.search(text or "")
        if m:
            return m.group(1)
    return None


class _Call:
    """Процесс вызова для остановки из цикла событий (поток to_thread не
    отменить): «Стоп» у Codex — убить дерево процессов (v4-design §3.4)."""

    def __init__(self) -> None:
        self.proc = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc is not None:
            _kill_tree(self.proc)


def _exec(exe: str, workdir: str, stdin_text: str, timeout_s: float,
          env: dict | None = None, effort: str | None = None, images=(),
          keep_session: bool = False, resume: str | None = None,
          call: _Call | None = None) -> AgentReply:
    # ignore_cleanup_errors: убитый по таймауту Codex может ещё держать файл.
    # Папка с pid в имени (meet.tempdirs): процесс убили посреди ответа — файл
    # с ответом модели о встрече удалит резидент.
    with tempfile.TemporaryDirectory(prefix=tempdirs.prefix("codex-"),
                                     ignore_cleanup_errors=True) as tmp:
        out_file = Path(tmp) / "last-message.txt"
        cmd = build_command(exe, workdir, str(out_file), effort=effort, images=images,
                            keep_session=keep_session, resume=resume)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=_NO_WINDOW, env=env,
            )
        except OSError as e:
            return AgentReply(text="", error=f"не удалось запустить Codex: {e}")
        if call is not None:
            call.proc = proc
            if call.cancelled:
                _kill_tree(proc)
        try:
            out, err = proc.communicate(input=stdin_text.encode("utf-8"), timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
            return AgentReply(text="", error=TIMEOUT_ERROR)
        stderr = (err or b"").decode("utf-8", errors="replace").strip()
        stdout = (out or b"").decode("utf-8", errors="replace")
        try:
            text = out_file.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
    sid = (session_from(stderr, stdout) or resume) if (keep_session or resume) else None
    if call is not None and call.cancelled:
        return AgentReply(text="", error=CANCELLED_ERROR, session_id=sid, cancelled=True)
    if proc.returncode != 0:
        if resume and _RESUME_FAILED.search(stderr):
            return resume_failure(stderr[-_ERR_LIMIT:])
        return AgentReply(
            text="",
            error=stderr[-_ERR_LIMIT:] or f"Codex завершился с кодом {proc.returncode}",
            session_id=sid,
        )
    if not text:
        return AgentReply(text="", error=stderr[-_ERR_LIMIT:] or EMPTY_ERROR, session_id=sid)
    return AgentReply(text=text, session_id=sid)


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
    images=(),
    keep_session: bool = False,
) -> AgentReply:
    """Один вызов `codex exec`; ошибки — в AgentReply.error. `proxy` —
    `llm.proxy`: Codex системный прокси Windows сам не видит.

    `images` — пути к изображениям (`--image=`; пропускаются нечитаемые,
    не PNG/JPEG/GIF/WebP и с запятой в пути); `keep_session` — сохранить
    сеанс и вернуть его id; `resume` — продолжить сохранённый (UUID).
    Системный промпт продолжению не повторяется: он уже в сеансе.
    Отмена задачи (CancelledError, «Стоп») убивает дерево процессов Codex."""
    if resume and not is_uuid(resume):
        return resume_failure(f"неверный id сеанса Codex: {resume!r}")
    exe = find_codex()
    if exe is None:
        return AgentReply(text="", error="не найден Codex CLI (codex)")
    stdin_text = prompt if resume else f"{system_prompt}\n\n{prompt}"
    sent, _skipped = usable_images(images)
    env = netproxy.child_env(proxy)
    drop_session_markers(env)  # сеанс сам по себе, не «вложенный» (llm.base)
    call = _Call()
    try:
        reply = await asyncio.to_thread(
            _exec, exe, _workdir(allowed_dirs, cwd), stdin_text, timeout_s, env, effort,
            sent, keep_session, resume, call,
        )
    except asyncio.CancelledError:
        call.cancel()  # «Стоп»: процесс Codex убит, поток дочитает и выйдет
        raise
    reply.error = netproxy.with_hint(reply.error)
    return reply
