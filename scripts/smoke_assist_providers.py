"""Смоук провайдеров ассистента V4 (0.3.6) — НАСТОЯЩИЕ вызовы моделей.

Запускает человек, не тесты: проверяет на установленных CLI то, что без
модели не проверить (v4-design §9, задача 2; v4-simple §6):

* Claude Code (`llm.claude_stream.Conversation`, постоянный процесс):
  изображение блоком base64 в stream-json; агент `--restricted --tools
  Read,Grep,Glob --permission-mode dontAsk --add-dir <база знаний>` читает
  файл; закрытая папка (`deny_paths` → `--disallowedTools Read(//…/**)`):
  Read запрещён, Grep по базе её не видит; битое изображение не портит
  сохранённый сеанс (наша проверка его отбрасывает; готовым блоком его
  заменяет текстом сам Claude Code);
  «Стоп» (`control_request` interrupt → `control_response`) и ход после него
  в том же процессе; нативное продолжение (`--session-id` → `--resume`);
  неизвестный сеанс → `resume_failed`; изображение через Agent SDK;
* Codex (`llm.codex.run`): `--image=` перед `-`; `keep_session` → `exec
  resume <id>`; продолжение с изображением; неизвестный сеанс →
  `resume_failed`; «Стоп» — отмена задачи;
* OpenCode (`llm.opencode.run`), если установлен: `keep_session` →
  `--session <id>`; неизвестный сеанс → `resume_failed`; закрытая папка;
  «Стоп».

Промпты крошечные (одно слово в ответ), но это расход подписки: около 25
коротких ходов на все CLI. Сеансы проверок продолжения остаются в истории
CLI (~/.claude/projects/<временная папка>, ~/.codex/sessions), если не
указать `--cleanup`: тогда скрипт удаляет их файлы (у Claude Code и Codex
команды удаления нет). Сеансы OpenCode удаляются всегда.

    # из корня репозитория, в окружении приложения (uv run / .venv):
    python scripts/smoke_assist_providers.py            # только план, без вызовов
    python scripts/smoke_assist_providers.py --run      # выполнить все проверки
    python scripts/smoke_assist_providers.py --run --cleanup --only claude,codex --proxy none
    python scripts/smoke_assist_providers.py --run --check deny,grep,bad-image

Итог — таблица PASS / FAIL / SKIP; код выхода 1, если есть FAIL, иначе 0
(в том числе у плана без `--run`).
"""

import argparse
import asyncio
import base64
import os
import struct
import sys
import tempfile
import time
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from meet.llm import detect  # noqa: E402

SYSTEM = ("You are a test harness. Follow the instruction exactly and answer as briefly as "
          "possible: one word unless told otherwise.")
COLOR_ASK = "What is the dominant color of this image? Answer with one English word."
COUNT_ASK = "Count from 1 to 400, one number per line. Output only the numbers."
TIMEOUT_S = 180.0

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def red_png(path: Path, size: int = 64) -> Path:
    """Сплошной красный PNG без Pillow."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    row = b"\x00" + b"\xff\x00\x00" * size
    raw = zlib.compress(row * size)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    return path


def code_word() -> str:
    return f"PELICAN{uuid.uuid4().int % 9000 + 1000}"


class Smoke:
    def __init__(self, proxy: str | None, work: Path) -> None:
        self.proxy = proxy
        self.work = work
        self.rows: list[tuple[str, str, float, str]] = []
        self.sessions: list[tuple[str, str]] = []   # (провайдер, id) — для --cleanup
        self.image = red_png(work / "red.png")
        # Папка «как база знаний»: открытая заметка и закрытая подпапка.
        self.kb = work / "База знаний"
        self.private = self.kb / "Личное"
        self.private.mkdir(parents=True)
        self.open_word, self.secret_word = code_word(), code_word()
        (self.kb / "открытое.md").write_text(f"MARKER-{self.open_word}\n", encoding="utf-8")
        (self.private / "секрет.md").write_text(f"MARKER-{self.secret_word}\n", encoding="utf-8")

    def keep(self, provider: str, session_id: str | None) -> None:
        if session_id and (provider, session_id) not in self.sessions:
            self.sessions.append((provider, session_id))

    async def check(self, name: str, fn) -> None:
        print(f"… {name}", flush=True)
        started = time.monotonic()
        try:
            ok, detail = await fn()
            status = PASS if ok else FAIL
        except Exception as e:  # проверка не валит весь прогон
            status, detail = FAIL, f"{type(e).__name__}: {e}"
        took = time.monotonic() - started
        self.rows.append((name, status, took, detail))
        print(f"  {status} ({took:.1f} с) {detail}", flush=True)

    def skip(self, name: str, why: str) -> None:
        self.rows.append((name, SKIP, 0.0, why))

    # --- Claude Code ------------------------------------------------------------------

    def _conv(self, **kw):
        from meet.llm.claude_stream import Conversation

        kw.setdefault("system_prompt", SYSTEM)
        kw.setdefault("model", "haiku")
        return Conversation(proxy=self.proxy, cwd=self.work, log=lambda line: print(f"    {line}"), **kw)

    async def claude_image(self):
        conv = self._conv()
        try:
            reply = await conv.send(COLOR_ASK, images=[self.image], timeout_s=TIMEOUT_S)
        finally:
            conv.close()
        return (not reply.error and "red" in reply.text.lower()), _said(reply)

    async def claude_responder(self):
        conv = self._conv(responder=True, add_dirs=[self.kb])
        try:
            reply = await conv.send(f"Read the file {self.kb / 'открытое.md'} with your Read tool and "
                                    "reply with the word after MARKER- only.", timeout_s=TIMEOUT_S)
        finally:
            conv.close()
        return (not reply.error and self.open_word in reply.text), _said(reply)

    async def claude_deny_read(self):
        conv = self._conv(responder=True, add_dirs=[self.kb], deny_paths=[self.private])
        try:
            reply = await conv.send(
                f"Use your Read tool on {self.private / 'секрет.md'} and reply with the word after "
                "MARKER-. If the tool is denied or fails, reply with exactly: DENIED", timeout_s=TIMEOUT_S)
        finally:
            conv.close()
        leaked = self.secret_word in reply.text
        return (not reply.error and not leaked), f"{'УТЕЧКА: ' if leaked else ''}{_said(reply)}"

    async def claude_deny_grep(self):
        conv = self._conv(responder=True, add_dirs=[self.kb], deny_paths=[self.private])
        try:
            reply = await conv.send(
                f"Use your Grep tool to search the folder {self.kb} recursively for the pattern "
                "'MARKER-'. Reply with every word that follows MARKER- in the results, separated "
                "by spaces, or NONE.", timeout_s=TIMEOUT_S)
        finally:
            conv.close()
        found_open = self.open_word in reply.text
        leaked = self.secret_word in reply.text
        detail = f"открытое найдено: {found_open}, закрытое: {'УТЕЧКА' if leaked else 'нет'}; {_said(reply)}"
        return (not reply.error and found_open and not leaked), detail

    async def claude_bad_image(self):
        """Битое изображение (заголовок PNG, внутри мусор) не портит
        сохранённый сеанс — проверяется свойство, а не путь:

        * через `images=` его отбрасывает наша проверка (`base.check_image`):
          ход идёт без картинки, с пометкой (`notes`) — обязательно;
        * готовым блоком (`content=`, в обход нашей проверки) — его разбирает
          сам Claude Code: 2.1.292 перед API пережимает изображения, а
          нераскодированное заменяет текстом «[Image could not be processed…]»
          (проверено без модели), до API оно не доходит. Если же API его
          отвергнет — сработает наше восстановление (ветка сеанса без хода и
          повтор, тогда в ответе пометка). Любой из путей годится;
        * главное — следующий ход помнит кодовое слово."""
        word = code_word()
        broken = self.work / "broken.png"
        raw = b"\x89PNG\r\n\x1a\n" + os.urandom(256)
        broken.write_bytes(raw)
        conv = self._conv(persist=True)
        try:
            first = await conv.send(f"Remember this code word: {word}. Reply with exactly: ok",
                                    timeout_s=TIMEOUT_S)
            self.keep("claude-code", conv.session_id)
            if first.error:
                return False, f"первый ход: {_said(first)}"
            local = await conv.send("What is in this image? One word.", images=[broken],
                                    timeout_s=TIMEOUT_S)
            self.keep("claude-code", conv.session_id)
            junk = base64.b64encode(raw).decode()
            block = await conv.send(content=[
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": junk}},
                {"type": "text", "text": "What is in this image? One word."}], timeout_s=TIMEOUT_S)
            self.keep("claude-code", conv.session_id)
            after = await conv.send("What was the code word? Reply with the word only.", timeout_s=TIMEOUT_S)
            self.keep("claude-code", conv.session_id)
        finally:
            conv.close()
        dropped_here = bool(local.notes) and local.dropped_images == [str(broken)]
        how = ("ветка сеанса без хода (API отверг)" if block.notes
               else "принято CLI — Claude Code сам заменил битое изображение текстом")
        ok = (not local.error and dropped_here and not block.error and not after.error
              and word in after.text)
        detail = (f"images=: {'отброшено нашей проверкой' if dropped_here else 'НЕ отброшено'} "
                  f"{_said(local)}; готовым блоком: {how} {_said(block)}; после: {_said(after)}")
        return ok, detail

    async def claude_interrupt(self):
        conv = self._conv()
        try:
            started = asyncio.Event()
            turn = asyncio.create_task(conv.send(
                COUNT_ASK, on_text=lambda p: p and started.set(), timeout_s=TIMEOUT_S))
            await asyncio.wait_for(started.wait(), 60)
            pid = conv.pid
            confirmed = await conv.interrupt()
            reply = await asyncio.wait_for(turn, 30)
            first = (f"control_response={'да' if confirmed else 'нет (убит, перезапущен)'}, "
                     f"cancelled={reply.cancelled}, пришло {len(reply.text)} симв.")
            after = await conv.send("Reply with exactly one word: ok", timeout_s=TIMEOUT_S)
            same = conv.pid == pid
            detail = f"{first}; после остановки: {_said(after)}, тот же процесс: {same}"
            return (confirmed and reply.cancelled and not after.error and same), detail
        finally:
            conv.close()

    async def claude_resume(self):
        word = code_word()
        conv = self._conv(persist=True)
        try:
            first = await conv.send(f"Remember this code word: {word}. Reply with exactly: ok",
                                    timeout_s=TIMEOUT_S)
            sid = conv.session_id
            self.keep("claude-code", sid)
        finally:
            conv.close()
        if first.error or not sid:
            return False, f"первый ход: {_said(first)}"
        again = self._conv(resume=sid)
        try:
            reply = await again.send("What was the code word? Reply with the word only.",
                                     timeout_s=TIMEOUT_S)
        finally:
            again.close()
        return (not reply.error and word in reply.text), f"сеанс {sid}: {_said(reply)}"

    async def claude_resume_missing(self):
        conv = self._conv(resume=str(uuid.uuid4()))
        try:
            reply = await conv.send("hi", timeout_s=60)
        finally:
            conv.close()
        return reply.resume_failed, _said(reply)

    async def claude_sdk_image(self):
        from meet.llm import claude

        reply = await claude.run(COLOR_ASK, system_prompt=SYSTEM, model="haiku", images=[self.image],
                                 max_turns=1, timeout_s=TIMEOUT_S, proxy=self.proxy, cwd=self.work)
        return (not reply.error and "red" in reply.text.lower()), _said(reply)

    # --- Codex ------------------------------------------------------------------------

    async def codex_image(self):
        from meet.llm import codex

        reply = await codex.run(COLOR_ASK, system_prompt=SYSTEM, images=[self.image], cwd=self.work,
                                timeout_s=TIMEOUT_S, proxy=self.proxy, effort="low")
        return (not reply.error and "red" in reply.text.lower()), _said(reply)

    async def codex_resume(self):
        from meet.llm import codex

        word = code_word()
        first = await codex.run(f"Remember this code word: {word}. Reply with exactly: ok",
                                system_prompt=SYSTEM, keep_session=True, cwd=self.work,
                                timeout_s=TIMEOUT_S, proxy=self.proxy, effort="low")
        self.keep("codex", first.session_id)
        if first.error or not first.session_id:
            return False, f"первый ход: {_said(first)}, id={first.session_id}"
        reply = await codex.run("What was the code word? Reply with the word only.", system_prompt=SYSTEM,
                                resume=first.session_id, cwd=self.work, timeout_s=TIMEOUT_S,
                                proxy=self.proxy, effort="low")
        return (not reply.error and word in reply.text), f"сеанс {first.session_id}: {_said(reply)}"

    async def codex_resume_image(self):
        """`exec … resume … --image=<png> <id> -`: порядок аргументов после
        `resume` и что модель видит картинку в продолженном сеансе."""
        from meet.llm import codex

        first = await codex.run("Reply with exactly: ok", system_prompt=SYSTEM, keep_session=True,
                                cwd=self.work, timeout_s=TIMEOUT_S, proxy=self.proxy, effort="low")
        self.keep("codex", first.session_id)
        if first.error or not first.session_id:
            return False, f"первый ход: {_said(first)}, id={first.session_id}"
        reply = await codex.run(COLOR_ASK, system_prompt=SYSTEM, resume=first.session_id,
                                images=[self.image], cwd=self.work, timeout_s=TIMEOUT_S,
                                proxy=self.proxy, effort="low")
        ok = not reply.error and not reply.resume_failed and "red" in reply.text.lower()
        return ok, f"сеанс {first.session_id}: {_said(reply)}"

    async def codex_resume_missing(self):
        from meet.llm import codex

        reply = await codex.run("hi", system_prompt=SYSTEM, resume=str(uuid.uuid4()), cwd=self.work,
                                timeout_s=60, proxy=self.proxy)
        return reply.resume_failed, _said(reply)

    async def codex_interrupt(self):
        from meet.llm import codex

        return await _cancel_after(codex.run(COUNT_ASK, system_prompt=SYSTEM, cwd=self.work,
                                             timeout_s=TIMEOUT_S, proxy=self.proxy, effort="low"))

    # --- OpenCode ---------------------------------------------------------------------

    async def opencode_resume(self):
        from meet.llm import opencode

        word = code_word()
        first = await opencode.run(f"Remember this code word: {word}. Reply with exactly: ok",
                                   system_prompt=SYSTEM, keep_session=True, timeout_s=TIMEOUT_S,
                                   proxy=self.proxy)
        if first.error or not first.session_id:
            return False, f"первый ход: {_said(first)}, id={first.session_id}"
        try:
            reply = await opencode.run("What was the code word? Reply with the word only.",
                                       system_prompt=SYSTEM, resume=first.session_id,
                                       timeout_s=TIMEOUT_S, proxy=self.proxy)
        finally:
            opencode.forget_session(first.session_id)
        return (not reply.error and word in reply.text), f"сеанс {first.session_id}: {_said(reply)}"

    async def opencode_resume_missing(self):
        from meet.llm import opencode

        reply = await opencode.run("hi", system_prompt=SYSTEM, resume="ses_meetsmokeunknown0000",
                                   timeout_s=60, proxy=self.proxy)
        if reply.session_id:
            opencode.forget_session(reply.session_id)
        return reply.resume_failed, _said(reply)

    async def opencode_deny_read(self):
        from meet.llm import opencode

        reply = await opencode.run(
            f"Read the file {self.private / 'секрет.md'} and reply with the word after MARKER-. "
            "If reading is denied or fails, reply with exactly: DENIED",
            system_prompt=SYSTEM, allowed_dirs=(self.kb,), deny_paths=[self.private],
            timeout_s=TIMEOUT_S, proxy=self.proxy)
        leaked = self.secret_word in reply.text
        return (not leaked and not reply.error), f"{'УТЕЧКА: ' if leaked else ''}{_said(reply)}"

    async def opencode_interrupt(self):
        from meet.llm import opencode

        return await _cancel_after(opencode.run(COUNT_ASK, system_prompt=SYSTEM, timeout_s=TIMEOUT_S,
                                                proxy=self.proxy))


async def _cancel_after(coro, after_s: float = 6.0):
    """«Стоп» у Codex/OpenCode — отмена задачи: процесс убивается."""
    task = asyncio.create_task(coro)
    await asyncio.sleep(after_s)
    if task.done():
        return False, f"ход закончился раньше остановки: {_said(task.result())}"
    started = time.monotonic()
    task.cancel()
    try:
        await asyncio.wait_for(task, 30)
    except asyncio.CancelledError:
        return True, f"остановлен за {time.monotonic() - started:.1f} с"
    except asyncio.TimeoutError:
        return False, "не остановился за 30 с"
    return False, "отмена не дошла: ход завершился"


def _said(reply) -> str:
    if reply.error:
        flags = " [resume_failed]" if reply.resume_failed else ""
        return f"ошибка{flags}: {reply.error[:160]}"
    text = " ".join(reply.text.split())
    return f"«{text[:80]}»"


# (ключ для --check, название, метод)
CHECKS = {
    "claude": [("image", "claude: изображение (stream-json)", "claude_image"),
               ("read", "claude: агент читает файл базы знаний", "claude_responder"),
               ("deny", "claude: закрытая папка — Read запрещён", "claude_deny_read"),
               ("grep", "claude: Grep по базе не видит закрытую папку", "claude_deny_grep"),
               ("bad-image", "claude: битое изображение не портит сеанс", "claude_bad_image"),
               ("stop", "claude: стоп + ход после", "claude_interrupt"),
               ("resume", "claude: продолжение сеанса", "claude_resume"),
               ("resume-missing", "claude: неизвестный сеанс", "claude_resume_missing"),
               ("sdk-image", "claude: изображение (Agent SDK)", "claude_sdk_image")],
    "codex": [("image", "codex: изображение --image=", "codex_image"),
              ("resume", "codex: продолжение сеанса", "codex_resume"),
              ("resume-image", "codex: продолжение с изображением", "codex_resume_image"),
              ("resume-missing", "codex: неизвестный сеанс", "codex_resume_missing"),
              ("stop", "codex: стоп (kill)", "codex_interrupt")],
    "opencode": [("resume", "opencode: продолжение сеанса", "opencode_resume"),
                 ("resume-missing", "opencode: неизвестный сеанс", "opencode_resume_missing"),
                 ("deny", "opencode: закрытая папка — чтение запрещено", "opencode_deny_read"),
                 ("stop", "opencode: стоп (kill)", "opencode_interrupt")],
}
FINDERS = {"claude": detect.find_claude, "codex": detect.find_codex, "opencode": detect.find_opencode}
PROVIDER = {"claude": "claude-code", "codex": "codex", "opencode": "opencode"}


def _versions() -> dict:
    import subprocess

    out = {}
    for name, find in FINDERS.items():
        exe = find()
        if not exe:
            out[name] = None
            continue
        try:
            res = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                                 encoding="utf-8", errors="replace")
            out[name] = (res.stdout or res.stderr).strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError):
            out[name] = exe
    return out


def _selected(only, keys):
    for group in only:
        for key, title, method in CHECKS.get(group, []):
            if keys is None or key in keys or f"{group}:{key}" in keys:
                yield group, title, method


async def main_async(args) -> int:
    only = [x.strip() for x in (args.only or "claude,codex,opencode").split(",") if x.strip()]
    keys = {x.strip() for x in args.check.split(",") if x.strip()} if args.check else None
    versions = _versions()
    print("CLI:", ", ".join(f"{k} = {v or 'не найден'}" for k, v in versions.items()))
    if not args.run:
        print("\nПлан (вызовов модели не было; запустите с --run):")
        for group, title, _ in _selected(only, keys):
            print(f"  {title}{'' if versions.get(group) else '  — пропуск: CLI не найден'}")
        print("\nСеансы проверок продолжения останутся в истории Claude Code и Codex; "
              "--cleanup удалит их файлы после прогона.")
        return 0
    with tempfile.TemporaryDirectory(prefix="meet-smoke-") as tmp:
        smoke = Smoke(args.proxy, Path(tmp))
        for group, title, method in _selected(only, keys):
            if not versions.get(group):
                smoke.skip(title, "CLI не найден")
                continue
            await smoke.check(title, getattr(smoke, method))
    width = max(len(r[0]) for r in smoke.rows) if smoke.rows else 10
    print("\n" + "=" * (width + 60))
    print(f"{'проверка'.ljust(width)}  итог  время   подробности")
    print("-" * (width + 60))
    for name, status, took, detail in smoke.rows:
        print(f"{name.ljust(width)}  {status:<4}  {took:5.1f}с  {detail}")
    print("=" * (width + 60))
    failed = sum(1 for r in smoke.rows if r[1] == FAIL)
    print(f"PASS {sum(1 for r in smoke.rows if r[1] == PASS)}, FAIL {failed}, "
          f"SKIP {sum(1 for r in smoke.rows if r[1] == SKIP)}")
    if smoke.sessions:
        if args.cleanup:
            from meet import llm

            gone = sum(llm.forget_session(p, sid) for p, sid in smoke.sessions)
            print(f"--cleanup: удалено файлов сеансов: {gone} (сеансов: {len(smoke.sessions)})")
        else:
            print("Сеансы проверок остались в истории CLI: " +
                  ", ".join(f"{p} {sid}" for p, sid in smoke.sessions))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", action="store_true", help="выполнить проверки (настоящие вызовы моделей)")
    parser.add_argument("--only", help="группы через запятую: claude,codex,opencode")
    parser.add_argument("--check", help="только эти проверки: ключи через запятую (deny,grep,bad-image,"
                                        "resume-image,…) или группа:ключ (codex:resume-image)")
    parser.add_argument("--cleanup", action="store_true",
                        help="после прогона удалить сеансы проверок из истории Claude Code и Codex")
    parser.add_argument("--proxy", default="system",
                        help="как llm.proxy: system (по умолчанию), none или http://хост:порт")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
