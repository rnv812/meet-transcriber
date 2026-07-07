"""Оркестратор live-ассистента: связывает LiveEngine, шину, дайджестер, Q&A и веб.

`run_assist` — блокирующая точка входа команды `meet assist`: поднимает запись
с потоковой расшифровкой, дайджестер и веб-страницу, живёт до Ctrl-C.
`AssistState` — состояние, которое видят веб-слой и линии SDK.
"""

import asyncio
import webbrowser
from datetime import datetime
from pathlib import Path

from meet.assist.agent import check_auth, run_agent_query
from meet.assist.bus import TranscriptBus
from meet.assist.context import collect_task_context
from meet.assist.digest import Digest
from meet.assist.digester import Digester
from meet.assist.prompts import (
    build_digester_system,
    build_qa_system,
    load_glossary,
)
from meet.assist.qa import QAService
from meet.assist.web import run_web


class AssistState:
    """Состояние ассистента, которое видят веб-слой и линии SDK.

    set_task() пересобирает системные промпты на лету — это дёшево, потому что
    каждый тик/вопрос и так делает свежий SDK-вызов.
    """

    def __init__(self, *, bus: TranscriptBus, digest: Digest, glossary: str,
                 vault: Path | None, cwd: Path) -> None:
        self.bus = bus
        self.digest = digest
        self._glossary = glossary
        self._vault = vault
        self._cwd = cwd
        self._task_context = ""
        self.qa = None       # проставляет run_assist после создания QAService
        self.digester = None  # аналогично
        self._rebuild()

    @property
    def qa_allowed_dirs(self) -> tuple[Path, ...]:
        return (self._cwd, self._vault) if self._vault else (self._cwd,)

    def _rebuild(self) -> None:
        self.digester_system = build_digester_system(
            self._glossary, self._task_context)
        self.qa_system = build_qa_system(
            self._glossary, self._task_context, self._vault)
        if self.digester is not None:
            self.digester.set_system_prompt(self.digester_system)
        if self.qa is not None:
            self.qa.set_system_prompt(self.qa_system)

    async def set_task(self, task: str) -> None:
        if self._vault is not None:
            self._task_context = await asyncio.to_thread(
                collect_task_context, self._vault, task)
        self._rebuild()

    def status(self) -> str | None:
        return self.digester.status if self.digester else None


async def _main(state: AssistState, port: int) -> None:
    stop = asyncio.Event()
    runner = await run_web(state, port)
    webbrowser.open(f"http://127.0.0.1:{port}/")
    print(f"Ассистент: http://127.0.0.1:{port}/ (Ctrl-C — стоп)")
    try:
        await state.digester.run(stop)
    finally:
        stop.set()
        await runner.cleanup()


def run_assist(out_root: str = "recordings", window_seconds: float = 20.0,
               hotwords: str | None = None, task: str | None = None,
               vault: str | None = None, port: int = 8765) -> None:
    from meet.asr import Transcriber
    from meet.live import LiveEngine
    from meet.transcribe import _load_hotwords

    # Авторизация — до прогрева моделей и создания папки записи: при мёртвой
    # авторизации пользователь не должен ждать минуту и получать пустую папку.
    auth_error = asyncio.run(check_auth())
    if auth_error:
        raise SystemExit(f"Авторизация Claude не прошла: {auth_error}")

    out_dir = Path(out_root) / datetime.now().strftime("%Y-%m-%d_%H-%M")
    bus = TranscriptBus()
    digest = Digest()
    vault_path = Path(vault) if vault else None
    state = AssistState(
        bus=bus, digest=digest,
        glossary=load_glossary(Path.cwd()),
        vault=vault_path, cwd=out_dir,
    )
    engine = LiveEngine(out_dir, Transcriber(), window_seconds=window_seconds,
                        hotwords=_load_hotwords(hotwords),
                        on_line=bus.publish)
    digest_file = out_dir / "live_digest.md"

    def _write_digest() -> None:
        digest_file.write_text(digest.render(), encoding="utf-8")

    state.digester = Digester(bus, digest, system_prompt=state.digester_system,
                              runner=run_agent_query, on_update=_write_digest)
    state.qa = QAService(
        bus, digest, system_prompt=state.qa_system,
        allowed_dirs=state.qa_allowed_dirs, cwd=out_dir,
        runner=run_agent_query,
        on_fresh_audio=engine.process_window,
    )
    if task:
        asyncio.run(state.set_task(task))
    engine.start()
    try:
        asyncio.run(_main(state, port))
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        print(f"\nОстановлено: {out_dir}")
        print(f'Точный транскрипт: meet transcribe "{out_dir}"')
