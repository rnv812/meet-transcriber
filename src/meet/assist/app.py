"""Оркестратор live-ассистента: связывает LiveEngine, шину, дайджестер, Q&A и веб.

`run_assist` — блокирующая точка входа команды `meet assist`: поднимает запись
с потоковой расшифровкой, дайджестер и веб-страницу, живёт до Ctrl-C или
`POST /stop`. Без флагов — как всегда: страница открывается в браузере.
Резидент запускает его дочерним процессом (`--no-browser --port 0
--endpoint-file <путь>`) и узнаёт порт из файла эндпоинта.
`AssistState` — состояние, которое видят веб-слой и линии SDK.
"""

import asyncio
import json
import os
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from meet import paths
from meet.assist.agent import check_auth
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
from meet.assist.web import bound_port, run_web

NO_PROVIDER_ERROR = "Подключите Claude Code или Codex в настройках"


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
        self.stop_event: asyncio.Event | None = None  # ставит _main
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

    def request_stop(self) -> None:
        """`POST /stop`: штатная остановка (дорожки дописывает run_assist)."""
        if self.stop_event is not None:
            self.stop_event.set()


def write_endpoint(path: Path, *, port: int, folder: Path) -> None:
    """Атомарно (tmp + replace) записать, где слушает ассистент: резидент
    не должен прочитать недописанный файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(
            {"port": port, "pid": os.getpid(), "folder": os.path.abspath(folder)},
            ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # после replace его уже нет


def remove_endpoint(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # уборка не должна заслонять настоящую причину выхода


async def _main(state: AssistState, port: int, *, open_browser: bool = True,
                endpoint_file: Path | None = None,
                folder: Path | None = None) -> None:
    stop = asyncio.Event()
    state.stop_event = stop
    # Свой пул для asyncio.to_thread (вызовы Codex/локальной модели, дослив
    # окна для Q&A): при выходе его бросаем без ожидания. Иначе asyncio.run
    # ждал бы застрявший вызов модели (до 180 с), а engine.stop() — хвост
    # ленты и снятие lock'а — стоял бы за ним.
    loop = asyncio.get_running_loop()
    workers = ThreadPoolExecutor(thread_name_prefix="assist-model")
    loop.set_default_executor(workers)
    runner = await run_web(state, port)
    tasks: list[asyncio.Future] = []
    try:
        actual_port = bound_port(runner)
        url = f"http://127.0.0.1:{actual_port}/"
        if endpoint_file is not None:
            write_endpoint(endpoint_file, port=actual_port, folder=folder)
        if open_browser:
            webbrowser.open(url)
        print(f"Ассистент: {url} (Ctrl-C — стоп)", flush=True)
        digester = asyncio.ensure_future(state.digester.run(stop))
        tasks = [digester, asyncio.ensure_future(stop.wait())]
        # Выход — по /stop (не ждём сна дайджестера и его вызова модели) или
        # если дайджестер кончился сам; его сбой пробрасываем наружу.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if digester.done():
            digester.result()
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()
        # Подменить пул на пустой: asyncio.run закроет его мгновенно, а поток
        # с вызовом модели доработает сам. Файл эндпоинта удаляет run_assist
        # при выходе процесса (после engine.stop), не здесь.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        workers.shutdown(wait=False, cancel_futures=True)


def _pick_runner(provider: str | None, cfg):
    """Кто отвечает: явный `--provider`, иначе `llm.resolve(настройки)`."""
    from meet import llm

    if provider:
        try:
            return provider, llm.runner_for(provider, cfg)
        except ValueError as e:
            raise SystemExit(str(e)) from None
    name, runner = llm.resolve(cfg)
    if runner is None:
        raise SystemExit(NO_PROVIDER_ERROR)
    return name, runner


def run_assist(out_root: str = "recordings", window_seconds: float = 20.0,
               hotwords: str | None = None, task: str | None = None,
               vault: str | None = None, port: int = 8765,
               no_voices: bool = False, *, open_browser: bool = True,
               endpoint_file: str | None = None, provider: str | None = None,
               cfg=None) -> None:
    """`port=0` — эфемерный порт; `endpoint_file` получает
    `{"port", "pid", "folder"}` после старта сервера и удаляется при любом
    выходе; `provider` — имя провайдера вместо `llm.resolve(cfg)`."""
    endpoint = Path(endpoint_file) if endpoint_file else None
    try:
        _run_assist(out_root, window_seconds, hotwords, task, vault, port,
                    no_voices, open_browser=open_browser, endpoint=endpoint,
                    provider=provider, cfg=cfg)
    finally:
        remove_endpoint(endpoint)


def _run_assist(out_root, window_seconds, hotwords, task, vault, port,
                no_voices, *, open_browser, endpoint, provider, cfg) -> None:
    from meet import settings
    from meet.asr import Transcriber
    from meet.live import LiveEngine
    from meet.transcribe import _load_hotwords
    from meet.voice_id import VoiceMatcher

    if cfg is None:
        cfg = settings.load()
    # Провайдер и авторизация — до прогрева моделей и создания папки записи:
    # без модели пользователь не должен ждать минуту и получать пустую папку.
    provider_name, runner = _pick_runner(provider, cfg)
    if provider_name == "claude-code":
        auth_error = asyncio.run(check_auth())
        if auth_error:
            raise SystemExit(f"Авторизация Claude не прошла: {auth_error}")

    out_dir = Path(out_root) / datetime.now().strftime("%Y-%m-%d_%H-%M")
    bus = TranscriptBus()
    digest = Digest()
    vault_path = Path(vault) if vault else None
    state = AssistState(
        bus=bus, digest=digest,
        # Лексика лежит рядом с настройками (в dev-режиме — в корне репозитория),
        # а не в рабочей папке процесса: под треем и из установленного
        # приложения cwd произвольная.
        glossary=load_glossary(paths.lexicon_dir()),
        vault=vault_path, cwd=out_dir,
    )
    engine = LiveEngine(out_dir, Transcriber(), window_seconds=window_seconds,
                        hotwords=_load_hotwords(hotwords),
                        on_entry=bus.publish,
                        voice_matcher=None if no_voices else VoiceMatcher())
    digest_file = out_dir / "live_digest.md"

    def _write_digest() -> None:
        digest_file.write_text(digest.render(), encoding="utf-8")

    state.digester = Digester(bus, digest, system_prompt=state.digester_system,
                              runner=runner, on_update=_write_digest)
    state.qa = QAService(
        bus, digest, system_prompt=state.qa_system,
        allowed_dirs=state.qa_allowed_dirs, cwd=out_dir,
        runner=runner,
        on_fresh_audio=engine.process_window,
    )
    if task:
        asyncio.run(state.set_task(task))
    started = False
    try:
        # start() берёт общий lock записи («Запись уже идёт» — SystemExit);
        # stop() в finally безопасен и после частичного старта.
        engine.start()
        started = True
        asyncio.run(_main(state, port, open_browser=open_browser,
                          endpoint_file=endpoint, folder=out_dir))
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        if started:
            print(f"\nОстановлено: {out_dir}", flush=True)
            print(f'Точный транскрипт: meet transcribe "{out_dir}"', flush=True)
