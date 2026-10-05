"""Оркестратор live-ассистента: связывает LiveEngine, шину, тикер живого состояния, Q&A и веб.

`run_assist` — блокирующая точка входа команды `meet assist`: поднимает запись
с потоковой расшифровкой, дайджестер и веб-страницу, живёт до Ctrl-C или
`POST /stop`. Без флагов — как всегда: страница открывается в браузере.
Резидент запускает его дочерним процессом (`--no-browser --port 0
--endpoint-file <путь>`) и узнаёт порт из файла эндпоинта.
`AssistState` — состояние, которое видят веб-слой и линии SDK.

Живое состояние (сводка и подсказки) пишется в папку записи как
`live_state.json` после каждого изменения и при выходе: его берёт задача
итогов как черновик.

`--attach-to <папка> --tap-port <порт>` (токен — MEET_TAP_TOKEN): ассистент
включён посреди обычной записи резидента. Папка — идущей записи, звук — из
отвода (`meet.pcm_tap`), lock записи не берётся, дорожки не пишутся. Сначала
догоняется уже записанное (`meet.live_catchup`, в фоне, прогресс — в `state`),
прошлое включение ассистента в этой записи продолжается (`live_state.json`,
лента). Конец записи — конец отвода — штатная остановка.
"""

import asyncio
import contextlib
import json
import os
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from meet import paths
from meet.assist.agent import check_auth
from meet.assist.bus import TranscriptBus
from meet.assist.context import collect_task_context
from meet.assist.digester import Cadence, Digester, cadence_for
from meet.assist.kb_index import TermIndex
from meet.assist.live_state import LIVE_STATE_JSON, LiveState
from meet.assist.prompts import (
    build_hints_system,
    build_qa_system,
    build_summary_system,
    load_glossary,
)
from meet.assist.qa import QAService
from meet.assist.web import bound_port, run_web

NO_PROVIDER_ERROR = "Подключите Claude Code, Codex или OpenCode в настройках"
PARENT_POLL_S = 1.0  # как резидент следит за оболочкой (tray.run_headless)
# Код выхода «повтор не поможет» (нет провайдера, вход в Claude, занятая
# запись): резидент не повторяет такой старт (meet.live_control.EXIT_FATAL).
# Строка, которой `meet assist` из резидента заканчивает вывод при
# непредвиденном исключении (cli._fatal_exit_code): «Ассистент упал: Тип: …».
CRASH_MARK = "Ассистент упал: "
EXIT_FATAL = 78  # не 3: им же падает abort() у MSVC CRT
BELOW_NORMAL_PRIORITY_CLASS = 0x4000


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс — по коду выхода, а не по одному OpenProcess: оболочка
    держит хэндл резидента, и открыть уже умерший процесс по нему удаётся.
    Так теперь проверяет и `plat.pid_alive` (на macOS — сигнал 0)."""
    from meet import plat

    return plat.pid_alive(pid)


async def _watch_parent(parent_pid: int, stop: asyncio.Event) -> None:
    """Резидент умер жёстко (без /stop) — останавливаемся сами, штатно
    дописав дорожки: сирота держал бы микрофон и `.recording.lock`, а новый
    резидент о нём не знал бы."""
    while not stop.is_set():
        if not _pid_alive(parent_pid):
            print("Резидент завершился — останавливаю ассистента", flush=True)
            stop.set()
            return
        await asyncio.sleep(PARENT_POLL_S)


class AssistState:
    """Состояние ассистента, которое видят веб-слой и линии SDK.

    set_task() пересобирает системные промпты на лету — это дёшево, потому что
    каждый тик/вопрос и так делает свежий SDK-вызов.
    """

    def __init__(self, *, bus: TranscriptBus, live: LiveState, glossary: str,
                 vault: Path | None, cwd: Path,
                 knowledge: Path | None = None,
                 vault_index: str = "Claude Docs.md", hub_prefix: str = "_",
                 prefs: dict | None = None, owner: str = "Вы") -> None:
        self.bus = bus
        self.live = live
        # Общий сигнал изменений: новая реплика, сводка, подсказки, ответы.
        self.changes = bus.changed
        self._owner = owner or "Вы"
        # Настройки окна из `assist` (не отвлекать, активность): окна берут их
        # из `state`, а не читают config.json сами.
        self.prefs = dict(prefs or {})
        self.on_change = None  # после действия человека (запись live_state.json)
        self._glossary = glossary
        self._vault = vault
        self._vault_index = vault_index
        self._hub_prefix = hub_prefix
        self._cwd = cwd
        self._knowledge = _knowledge_path(knowledge, vault)
        self._task_context = ""
        self.qa = None       # проставляет run_assist после создания QAService
        self.digester = None  # аналогично
        self.stop_event: asyncio.Event | None = None  # ставит _main
        self.loop: asyncio.AbstractEventLoop | None = None  # ставит _main
        self._stop_early = False  # остановка пришла до _main (конец записи)
        # () -> dict | None: ход догонялки начала встречи (подключён к записи).
        self.catchup = None
        # Подключённого к записи выключили, пока запись шла: сводка неполная.
        self.detached = False
        # Модель распознавания загружена (поэтапный старт, `_run_assist`);
        # пока нет — `stage`: что делается. Вопросы до готовности не принимаются.
        self.ready = True
        self.stage: str | None = None
        self.warning: str | None = None
        self._rebuild()

    @property
    def qa_allowed_dirs(self) -> tuple[Path, ...]:
        """Папка записи, база знаний, хранилище задач. Codex берёт рабочей
        папкой первую существующую из `[1:]` — базу знаний, если она есть."""
        return (self._cwd,) + tuple(
            d for d in (self._knowledge, self._vault) if d is not None)

    @property
    def knowledge(self) -> Path | None:
        """База знаний живого режима (для указателя терминов тиков)."""
        return self._knowledge

    def _rebuild(self) -> None:
        self.digester_system = build_summary_system(self._glossary, self._task_context)
        self.hints_system = build_hints_system(
            self._glossary, self._task_context, max_hints=self.live.max_hints, owner=self._owner)
        self.qa_system = build_qa_system(
            self._glossary, self._task_context, self._vault, self._knowledge, folder=self._cwd)
        if self.digester is not None:
            self.digester.set_system_prompt(self.digester_system, self.hints_system)
        if self.qa is not None:
            self.qa.set_system_prompt(self.qa_system)

    async def set_task(self, task: str) -> None:
        if self._vault is not None:
            self._task_context = await asyncio.to_thread(
                collect_task_context, self._vault, task,
                index=self._vault_index, hub_prefix=self._hub_prefix)
        self._rebuild()

    def status(self) -> str | None:
        """Строка состояния панели: дайджестера, иначе — предупреждение
        старта (вход в Claude не проверился — подсказки могут молчать)."""
        return (self.digester.status if self.digester else None) or self.warning

    def catchup_view(self) -> dict | None:
        """Ход догонялки для панели: {"active", "percent", "from_t", "to_t",
        "capped", "complete"}; догонялки нет — None."""
        progress = self.catchup() if self.catchup is not None else None
        if not progress:
            return None
        total = progress.get("total_s") or 0.0
        done = progress.get("done_s") or 0.0
        percent = 100 if not progress.get("active") else (
            min(99, int(100 * done / total)) if total > 0 else 0)
        return {"active": bool(progress.get("active")), "percent": percent,
                "from_t": progress.get("from_t"), "to_t": progress.get("to_t"),
                "capped": bool(progress.get("capped")),
                "complete": bool(progress.get("complete"))}

    def signature(self) -> tuple:
        """Меняется — пора слать клиентам новое `state`."""
        catchup = self.catchup_view()
        return (self.live.version, self.status(), self.ready, self.stage,
                None if catchup is None else (catchup["active"], catchup["percent"]))

    def view(self) -> dict:
        """Тело `event: state`: сводка, подсказки, статус. `digest` — сводка
        Markdown'ом (страница `meet assist` в браузере). История вопросов —
        отдельным событием `qa` (`qa_items`), когда меняется она."""
        data = self.live.to_dict()
        out = {**data, "digest": self.live.render_markdown(),
               "status": self.status(), "hints_enabled": self.live.hints_enabled,
               "prefs": self.prefs}
        catchup = self.catchup_view()
        if catchup is not None:
            out["catchup"] = catchup
        if not self.ready:
            out["starting"] = self.stage or "запускается…"
        return out

    def qa_version(self) -> int:
        return self.qa.version if self.qa is not None else 0

    def qa_items(self) -> list[dict]:
        return self.qa.history() if self.qa is not None else []

    def qa_partial_version(self) -> int:
        return self.qa.partial_version if self.qa is not None else 0

    def qa_partials(self) -> list[dict]:
        """Ответы, которые ещё пишутся: [{"id", "a"}]."""
        return self.qa.partials() if self.qa is not None else []

    def hint_action(self, hint_id: str, action: str) -> bool:
        """Закрепить, открепить, скрыть подсказку или вернуть только что
        скрытую. False — такой нет."""
        if action == "pin":
            changed = self.live.pin(hint_id, True)
        elif action == "unpin":
            changed = self.live.pin(hint_id, False)
        elif action == "dismiss":
            changed = self.live.dismiss(hint_id)
        elif action == "restore":
            changed = self.live.restore(hint_id)
        else:
            raise ValueError(f"неизвестное действие: {action}")
        if changed:
            if self.on_change is not None:
                self.on_change()
            self.changes.notify()
        return changed

    def request_stop(self) -> None:
        """`POST /stop`: штатная остановка (дорожки дописывает run_assist)."""
        if self.stop_event is not None:
            self.stop_event.set()
        self.changes.notify()  # открытые SSE-потоки замечают остановку сразу

    def mark_detached(self) -> None:
        """`POST /stop {"detach": true}`: запись идёт дальше без ассистента."""
        self.detached = True

    def request_stop_threadsafe(self) -> None:
        """Остановка из чужого потока (отвод записи кончился: запись
        остановлена). До старта цикла — запомнить, _main остановится сразу."""
        loop = self.loop
        if loop is None:
            self._stop_early = True
            return
        try:
            loop.call_soon_threadsafe(self.request_stop)
        except RuntimeError:
            pass  # цикл уже закрыт — выходим и так


def _knowledge_path(knowledge, vault: Path | None) -> Path | None:
    """База знаний живого режима: существующая папка, отличная от vault.

    Совпала с vault (так мигрирует прежняя настройка `assist.vault`) — её
    уже покрывают правила хранилища в Q&A; путь `meet assist` остаётся прежним."""
    if not knowledge:
        return None
    path = Path(knowledge)
    if not path.is_dir():
        return None
    if vault is not None:
        try:
            if path.resolve() == Path(vault).resolve():
                return None
        except OSError:
            pass
    return path


def write_endpoint(path: Path, *, port: int, folder: Path,
                   devices_fallback: list | None = None, ready: bool = True,
                   capturing: bool = True, stage: str | None = None) -> None:
    """Атомарно (tmp + replace) записать, где слушает ассистент: резидент
    не должен прочитать недописанный файл. `devices_fallback` — выбранные в
    настройках устройства, которых не нашлось (резидент покажет это в окне
    и в трее). Пока идёт старт, файл переписывается на каждом этапе
    (`meet.live_control`): `capturing` — звук уже пишется, `ready` — модель
    загружена, ассистент слушает, `stage` — что делается сейчас."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    info = {"port": port, "pid": os.getpid(), "folder": os.path.abspath(folder),
            "ready": bool(ready), "capturing": bool(capturing or ready)}
    if stage and not ready:
        info["stage"] = stage
    if devices_fallback:
        info["devices_fallback"] = devices_fallback
    try:
        tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # после replace его уже нет


REMOVE_ATTEMPTS = 5
REMOVE_RETRY_S = 0.05


def remove_endpoint(path: Path | None) -> None:
    """Убрать файл эндпоинта — сигнал резиденту «запись дописана». На Windows
    удаление падает, пока резидент читает файл (sharing violation), — тогда
    ещё пара попыток, а не молчаливый отказ."""
    if path is None:
        return
    for attempt in range(REMOVE_ATTEMPTS):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt + 1 < REMOVE_ATTEMPTS:
                time.sleep(REMOVE_RETRY_S)
        except OSError:
            return  # уборка не должна заслонять настоящую причину выхода


class _Endpoint:
    """Файл эндпоинта ребёнка резидента: порт — сразу, как поднят веб,
    дальше — этапы старта (`meet.live_control`). Без файла (страница в
    браузере) — только состояние для окна."""

    def __init__(self, path: Path | None, state: "AssistState", *, port: int,
                 folder: Path) -> None:
        self._path = path
        self._state = state
        self._fields = {"port": port, "folder": folder, "ready": False,
                        "capturing": False, "stage": None, "devices_fallback": None}

    def publish(self, **changes) -> None:
        self._fields.update(changes)
        self._state.stage = None if self._fields["ready"] else self._fields["stage"]
        self._state.ready = bool(self._fields["ready"])
        self._state.changes.notify()
        if self._path is not None:
            write_endpoint(self._path, **self._fields)


async def _main(state: AssistState, port: int, *, open_browser: bool = True,
                endpoint_file: Path | None = None,
                folder: Path | None = None,
                parent_pid: int | None = None,
                devices_fallback: list | None = None,
                prepare=None) -> None:
    """`prepare(endpoint)` — поэтапный старт (`_run_assist`): веб и файл
    эндпоинта поднимаются раньше него, остановка во время него штатная.
    Без него — ассистент уже готов (тесты, прежний путь)."""
    stop = asyncio.Event()
    state.stop_event = stop
    state.loop = asyncio.get_running_loop()
    if state._stop_early:
        stop.set()
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
        endpoint = _Endpoint(endpoint_file, state, port=actual_port, folder=folder)
        if parent_pid:
            # С самого начала: умер резидент посреди загрузки модели —
            # останавливаемся сразу, а не сиротой до готовности.
            tasks.append(asyncio.ensure_future(_watch_parent(parent_pid, stop)))
        if prepare is not None:
            # Порт — резиденту сразу: он видит этапы старта и останавливает штатно.
            endpoint.publish()
            started = asyncio.ensure_future(prepare(endpoint))
            stopped = asyncio.ensure_future(stop.wait())
            await asyncio.wait({started, stopped}, return_when=asyncio.FIRST_COMPLETED)
            stopped.cancel()
            if not started.done():
                started.cancel()  # остановили во время загрузки: её поток доработает сам
                await asyncio.gather(started, return_exceptions=True)
                return
            started.result()  # сбой старта — наружу (traceback в live.log)
            if stop.is_set():
                return
        else:
            endpoint.publish(ready=True, devices_fallback=devices_fallback)
        if open_browser:
            webbrowser.open(url)
        print(f"Ассистент: {url} (Ctrl-C — стоп)", flush=True)
        digester = asyncio.ensure_future(state.digester.run(stop))
        tasks += [digester, asyncio.ensure_future(stop.wait())]
        # Выход — по /stop (не ждём сна дайджестера и его вызова модели) или
        # если дайджестер кончился сам; его сбой пробрасываем наружу.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if digester.done():
            digester.result()
    finally:
        stop.set()
        state.changes.notify()  # SSE-потоки выходят сразу, а не к keepalive
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runner.cleanup()
        # Подменить пул на пустой: asyncio.run закроет его мгновенно, а поток
        # с вызовом модели доработает сам. Файл эндпоинта удаляет run_assist
        # при выходе процесса (после engine.stop), не здесь.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        workers.shutdown(wait=False, cancel_futures=True)


def cadence_of(assist) -> Cadence:
    """Каденс тиков по настройкам `assist`: активность, затем свои пределы."""
    cadence = cadence_for(assist.activity)
    if assist.max_hints:
        cadence = cadence.with_max_hints(assist.max_hints)
    if assist.min_words:
        cadence = cadence.with_min_words(assist.min_words)
    return cadence


def hints_session_for(provider: str | None, cfg, tick_kwargs: dict, log=print):
    """Фабрика диалога подсказок: у Claude Code — постоянный процесс на
    встречу (`llm.claude_stream.Conversation`) с моделью уровня «Как у
    агента»/«Быстрее»; у остальных — None (тикер зовёт runner на каждый тик)."""
    if provider != "claude-code":
        return None
    from meet.llm.claude_stream import Conversation

    def make(system_prompt: str):
        return Conversation(system_prompt=system_prompt, model=tick_kwargs.get("model"),
                            thinking=tick_kwargs.get("thinking"), proxy=cfg.llm.proxy, log=log)

    return make


def qa_workdir(provider: str | None, out_dir: Path) -> Path:
    """Рабочая папка вопросов. У Claude Code — служебная, не папка встречи:
    сохранённый сеанс вопросов лежит в истории Claude Code под своей папкой,
    и `--continue` вкладки «Агент» (она работает в папке встречи) его не
    подхватит. Файлы встречи и базы знаний вопросы читают по абсолютным
    путям (разрешённые папки). Остальным провайдерам — папка встречи."""
    if provider != "claude-code":
        return out_dir
    from meet.llm.claude_stream import workdir

    return workdir("meet-live-qa")


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


def _attach_tap(port: int, token: str):
    """Подключение к отводу звука резидента (`meet.pcm_tap.TapClient`)."""
    from meet.pcm_tap import TapClient

    return TapClient(port, token)


def _prior_entries(transcript: Path) -> list[tuple[str, dict]]:
    """Лента прошлого включения ассистента в этой записи — как реплики шины
    (контекст: подсказки и сводка по ним не тикают)."""
    import re

    try:
        text = transcript.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    pattern = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] ([^:]+): (.*)$")
    for line in text.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        h, mi, sec, speaker, said = m.groups()
        out.append((line, {"t": float(int(h) * 3600 + int(mi) * 60 + int(sec)),
                           "speaker": speaker, "text": said, "catchup": True}))
    return out


def run_assist(out_root: str = "recordings", window_seconds: float = 20.0,
               hotwords: str | None = None, task: str | None = None,
               vault: str | None = None, port: int = 8765,
               no_voices: bool = False, *, open_browser: bool = True,
               endpoint_file: str | None = None, provider: str | None = None,
               cfg=None, knowledge_dir: str | None = None,
               parent_pid: int | None = None, attach_to: str | None = None,
               tap_port: int | None = None, tap_token: str | None = None) -> None:
    """`port=0` — эфемерный порт; `endpoint_file` получает
    `{"port", "pid", "folder", "ready", "capturing", "stage"}`, как только
    поднят веб, переписывается на каждом этапе старта и удаляется при любом
    выходе; `provider` — имя провайдера вместо `llm.resolve(cfg)`;
    `knowledge_dir` — база знаний на чтение для вопросов и дайджеста;
    `parent_pid` — резидент: умер он — штатная остановка, как по /stop;
    `attach_to`/`tap_port`/`tap_token` — подключиться к идущей обычной записи
    (папка и отвод звука резидента)."""
    from meet import tempdirs

    endpoint = Path(endpoint_file) if endpoint_file else None
    _LOADERS.clear()
    try:
        # Свой корень временных файлов (окна GigaAM, ответы Codex): удаляется
        # при выходе, а убитого ассистента дочищает резидент (по pid).
        with tempdirs.own_root(), _whole_lines():
            _run_assist(out_root, window_seconds, hotwords, task, vault, port,
                        no_voices, open_browser=open_browser, endpoint=endpoint,
                        provider=provider, cfg=cfg, knowledge_dir=knowledge_dir,
                        parent_pid=parent_pid, attach_to=attach_to,
                        tap_port=tap_port, tap_token=tap_token)
    finally:
        remove_endpoint(endpoint)
        # Остановили посреди загрузки модели: её поток ещё работает. Запись
        # уже дописана (файл эндпоинта убран), а выход с недогруженным torch
        # в фоне мог бы упасть уже при завершении интерпретатора.
        _join_loaders(LOADERS_JOIN_S)


class _LineWriter:
    """Поток вывода, в который строки уходят целиком: print() пишет текст и
    перевод строки двумя вызовами, и строки фоновых потоков старта
    (модель, голоса, база знаний) перемешивались в live.log."""

    def __init__(self, stream) -> None:
        import threading

        self._stream = stream
        self._lock = threading.Lock()
        self._local = threading.local()

    def write(self, text: str) -> int:
        buf = getattr(self._local, "buf", "") + text
        head, sep, tail = buf.rpartition("\n")
        self._local.buf = tail
        if sep:
            with self._lock:
                self._stream.write(head + sep)
                self._stream.flush()
        return len(text)

    def flush(self) -> None:
        buf = getattr(self._local, "buf", "")
        self._local.buf = ""
        with self._lock:
            if buf:
                self._stream.write(buf)
            self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextlib.contextmanager
def _whole_lines():
    import sys

    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _LineWriter(saved[0]), _LineWriter(saved[1])
    try:
        yield
    finally:
        for writer in (sys.stdout, sys.stderr):
            try:
                writer.flush()
            except Exception:
                pass
        sys.stdout, sys.stderr = saved


# Потоки поэтапного старта (модель, голоса, база знаний): ждём их при выходе.
_LOADERS: list = []
LOADERS_JOIN_S = 30.0


# Ответ проверки входа, который повтор не исправит (а не сеть или перегрузка).
_AUTH_DEFINITE = ("login", "logged in", "log in", "authenticat", "unauthorized", "401",
                  "403", "invalid api key", "oauth", "вход", "не найден claude code cli")


def _auth_is_definite(error: str) -> bool:
    text = error.lower()
    return any(mark in text for mark in _AUTH_DEFINITE)


def _claude_login_problem() -> str | None:
    """CLI Claude Code нет или вход в нём не выполнен — текст; иначе (и если
    сама проверка не удалась) None. `claude auth status` — без вызова модели."""
    from meet.llm import claude, detect

    path = claude.find_cli()
    if path is None:
        return ("не найден Claude Code CLI (claude.exe); npm-шим claude.cmd "
                "не подходит — нужна родная установка Claude Code")
    ok, text = detect.logged_in("claude-code", str(path))
    if ok or not text or "(claude auth login)" not in text:
        return None  # вошёл — или проверка сама не удалась: решит вызов модели
    return text


def _refusing(load):
    """Загрузка модели, сбой которой повтор не исправит (нет пакета движка,
    нет модели на диске), — отказ (EXIT_FATAL), а не повод грузить ещё раз."""
    def run():
        from meet.live_asr import ModelMissing

        try:
            return load()
        except (ImportError, ModelMissing) as e:
            raise StartRefused(f"Ассистент не запустился: {type(e).__name__}: {e}"
                               if isinstance(e, ImportError) else
                               f"Ассистент не запустился: {e}") from e
    return run


class StartRefused(Exception):
    """Старт невозможен по причине, которую повтор не исправит (вход в
    Claude, запись уже идёт): `_run_assist` превращает её в SystemExit с
    текстом уже вне цикла asyncio."""


def _join_loaders(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    for thread in list(_LOADERS):
        thread.join(max(0.0, deadline - time.monotonic()))


def _in_thread(fn, name: str) -> "asyncio.Future":
    """`fn()` в своём потоке → future цикла. Не пул: поток, который не
    дождались (остановка во время загрузки модели), не держит asyncio.run."""
    import threading

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(ok: bool, value) -> None:
        if not future.done():
            (future.set_result if ok else future.set_exception)(value)

    def run() -> None:
        try:
            result = fn()
        except SystemExit as e:
            # Отказ с текстом («Запись уже идёт») — обычным исключением: SystemExit
            # внутри цикла asyncio вылетел бы мимо его уборки.
            outcome = (False, StartRefused(e.code) if isinstance(e.code, str) else e)
        except BaseException as e:  # noqa: BLE001
            outcome = (False, e)
        else:
            outcome = (True, result)
        try:
            loop.call_soon_threadsafe(settle, *outcome)
        except RuntimeError:
            pass  # цикл уже закрыт — нас не ждут

    thread = threading.Thread(target=run, name=f"meet-assist-{name}", daemon=True)
    _LOADERS.append(thread)
    thread.start()
    return future


def _process_age() -> float | None:
    """Сколько живёт процесс (запуск интерпретатора и импорты до старта)."""
    try:
        import psutil

        return max(0.0, time.time() - psutil.Process().create_time())
    except Exception:
        return None


class StartClock:
    """Этапы старта со временем — в live.log (`старт: … за N.N с`): по ним
    видно, куда уходят секунды до «ассистент готов»."""

    def __init__(self, log=print) -> None:
        self._log = log
        self._began = time.monotonic()
        self._age = _process_age()  # к этому моменту процесс уже жил столько
        if self._age is not None:
            self._log(f"старт: запуск процесса и импорты — за {self._age:.1f} с")

    def mark(self, what: str, since: float) -> None:
        self._log(f"старт: {what} — за {time.monotonic() - since:.1f} с")

    def timed(self, what: str, fn):
        """fn, который отмечает в журнале своё время."""
        def run():
            began = time.monotonic()
            result = fn()
            self.mark(what, began)
            return result
        return run

    def total(self, what: str) -> None:
        spent = time.monotonic() - self._began + (self._age or 0.0)
        self._log(f"старт: {what} — за {spent:.1f} с от запуска процесса")


def _run_assist(out_root, window_seconds, hotwords, task, vault, port,
                no_voices, *, open_browser, endpoint, provider, cfg,
                knowledge_dir=None, parent_pid=None, attach_to=None,
                tap_port=None, tap_token=None) -> None:
    def log(line: str) -> None:
        # Одной записью со своим переводом строки: строки этапов пишут и
        # фоновые потоки старта, print() кусками их перемешивал бы.
        print(line + "\n", end="", flush=True)

    clock = StartClock(log)
    from meet import live_asr, live_catchup, settings
    from meet.live import LiveEngine
    from meet.transcribe import _load_hotwords
    from meet.voice_id import VoiceMatcher

    if cfg is None:
        cfg = settings.load()
    # Провайдер — до всего остального: без него пользователь не должен ждать
    # загрузки распознавания. Вход в Claude Code (вызов модели, секунды)
    # проверяется параллельно с загрузкой модели распознавания — см. prepare.
    provider_name, runner = _pick_runner(provider, cfg)
    if provider_name == "claude-code":
        # Быстро и до захвата звука: CLI нет или вход не выполнен — повтор не
        # поможет. Сбой самой проверки (тайм-аут) — не повод не запускаться.
        problem = _claude_login_problem()
        if problem:
            raise SystemExit(f"Авторизация Claude не прошла: {problem}")

    attached = attach_to is not None
    if attached:
        out_dir = Path(attach_to)
        if not out_dir.is_dir():
            raise SystemExit(f"Папка записи не найдена: {out_dir}")
        if not tap_port or not tap_token:
            raise SystemExit("Нет отвода звука записи (порт и токен) — включите "
                             "ассистента из приложения или `meet assist --attach`")
    else:
        out_dir = Path(out_root) / datetime.now().strftime("%Y-%m-%d_%H-%M")
    bus = TranscriptBus()
    cadence = cadence_of(cfg.assist)
    live = LiveState(max_hints=cadence.max_hints, hints_enabled=cadence.hints)
    transcript_path = out_dir / "live_transcript.md"
    heard = None
    prior: list[tuple[str, dict]] = []
    if attached:
        # Ассистента в этой записи уже включали: продолжаем его сводку и
        # подсказки, догоняем только то, чего он не слышал.
        from meet.assist.live_state import load_saved

        # Строки догонялки убитого посреди неё прошлого ассистента — в ленту.
        live_catchup.recover_side(out_dir)
        saved = load_saved(out_dir)
        if saved is not None and live.resume(saved):
            print("сводка прошлого включения ассистента продолжена", flush=True)
        # Догоняем с конца уже услышанного (конец последней реплики), без
        # повтора её самой; старый live_state.json — по метке последней строки.
        heard = live.heard_t if live.heard_t is not None else \
            live_catchup.heard_until(transcript_path)
        prior = _prior_entries(transcript_path)
    vault_path = Path(vault) if vault else None
    state = AssistState(
        bus=bus, live=live,
        # Лексика лежит рядом с настройками (в dev-режиме — в корне репозитория),
        # а не в рабочей папке процесса: под треем и из установленного
        # приложения cwd произвольная.
        glossary=load_glossary(paths.lexicon_dir()),
        vault=vault_path, cwd=out_dir,
        knowledge=Path(knowledge_dir) if knowledge_dir else None,
        vault_index=cfg.assist.vault_index, hub_prefix=cfg.assist.hub_prefix,
        prefs={"quiet_default": cfg.assist.quiet_default, "activity": cfg.assist.activity},
        owner=cfg.recording.speaker_name,
    )
    state.ready = False
    # Распознавание: GigaAM короткими окнами для русского (если скачана),
    # иначе Whisper; правила замены и латиница — к каждой реплике. Здесь
    # модель только выбирается — грузится она в prepare.
    try:
        transcriber = live_asr.pick(cfg, log=log)
    except live_asr.ModelMissing as e:
        raise SystemExit(f"Ассистент не запустился: {e}") from None
    engine = LiveEngine(out_dir, transcriber,
                        window_seconds=window_seconds,
                        hotwords=_load_hotwords(hotwords),
                        on_entry=bus.publish,
                        # Имена голосов задним числом — в шину (SSE `voices`).
                        on_relabel=bus.relabel,
                        voice_matcher=None if no_voices else VoiceMatcher(log=log),
                        text_fixes=live_asr.TextFixes.from_settings(hotwords, latin=True),
                        log=log,
                        tap_connect=(lambda: _attach_tap(tap_port, tap_token)) if attached else None,
                        on_source_end=state.request_stop_threadsafe if attached else None)
    if attached:
        # Прогресс догонялки — в `state` панели (сигнал — общий сигнал изменений).
        engine.on_catchup = bus.changed.notify
        state.catchup = engine.catchup_progress
    state_file = out_dir / LIVE_STATE_JSON

    def _save_state() -> None:
        # Папку создаёт открытие источника звука; до неё (и при сбое диска) —
        # молча: состояние в памяти, следующее изменение запишет его снова.
        if not out_dir.is_dir():
            return
        try:
            live.save(state_file)
        except OSError as e:
            print(f"live_state.json не записан: {e}", flush=True)

    state.on_change = _save_state
    from meet import llm

    # «Как у агента» — модель из настроек (llm.model), «Быстрее» — своя.
    tick_kwargs = llm.tier_kwargs(provider_name, cfg.assist.hints_model, cfg.llm.model)
    print(f"живые подсказки: {cfg.assist.activity}, модель тиков: "
          f"{cfg.assist.hints_model} {tick_kwargs or ''}".rstrip(), flush=True)
    state.digester = Digester(bus, live, system_prompt=state.digester_system,
                              hints_system=state.hints_system,
                              hints_session=hints_session_for(provider_name, cfg, tick_kwargs, log),
                              runner=runner, cadence=cadence, kb=None,
                              call_kwargs=tick_kwargs,
                              owner_speaker=cfg.recording.speaker_name,
                              # Имя из настроек и прежние имена владельца в базе голосов.
                              owner_names=[cfg.recording.speaker_name,
                                           *cfg.recording.former_speaker_names],
                              on_update=_save_state, log=log)
    state.qa = QAService(
        bus, live, system_prompt=state.qa_system,
        allowed_dirs=state.qa_allowed_dirs, cwd=qa_workdir(provider_name, out_dir),
        runner=runner, model=llm.agent_model(provider_name, cfg),
        on_fresh_audio=getattr(engine, "flush_tail", engine.process_window),
        owner=cfg.recording.speaker_name, changed=bus.changed,
    )
    if prior:
        for line, entry in prior:
            bus.publish(line, entry)
        skip = getattr(state.digester, "skip_existing", None)
        if skip is not None:
            skip()  # прошлая лента — контекст, не новые реплики
    run = {"started": False, "plan": None}
    clock.mark("настройки, провайдер и ассистент собраны", clock._began)

    async def prepare(endpoint_file: _Endpoint) -> None:
        """Поэтапный старт: звук пишется сразу, модель грузится параллельно со
        входом в Claude и указателем базы знаний; голоса — уже после
        готовности, в фоне. Каждый этап — в файл эндпоинта (окно видит его)
        и со временем — в live.log."""
        staged = hasattr(engine, "open_source")
        auth = None
        if provider_name == "claude-code":
            # Тот же прокси, что у вызовов модели (llm.proxy), иначе проверка
            # пошла бы «как в системе» при выбранном «Без прокси». Той же
            # моделью, что работает ассистент (`llm.model`): модели «haiku»
            # может не быть в разрешённых у организации или прокси.
            async def checked():
                began = time.monotonic()
                error = await check_auth(proxy=cfg.llm.proxy, model=cfg.llm.model)
                clock.mark("вход в Claude Code проверен", began)
                return error

            auth = asyncio.ensure_future(checked())
        if state.knowledge:
            # Указатель терминов базы знаний — в фоне: тики по базе не ходят,
            # а первый тик всё равно не раньше первых реплик.
            def build_kb():
                kb = TermIndex.build(state.knowledge)
                print(f"база знаний: {len(kb)} терминов в указателе", flush=True)
                setter = getattr(state.digester, "set_kb", None)
                if setter is not None:
                    setter(kb)

            _in_thread(clock.timed("указатель базы знаний построен", build_kb), "kb")
        if task:
            asyncio.ensure_future(state.set_task(task))
        if staged:
            asr = _in_thread(clock.timed("модель распознавания загружена",
                                         _refusing(engine.load_asr)), "asr")
            endpoint_file.publish(stage=STAGE_ATTACH if attached else STAGE_DEVICES)
            await _in_thread(clock.timed("звук пошёл" if not attached else
                                         "подключение к записи", engine.open_source), "source")
            run["started"] = True
            endpoint_file.publish(capturing=True, stage=STAGE_ASR,
                                  devices_fallback=getattr(engine, "devices_fallback", None))
            await asr
        else:
            endpoint_file.publish(stage=STAGE_ASR)
            await _in_thread(engine.start, "start")
            run["started"] = True
            endpoint_file.publish(capturing=True,
                                  devices_fallback=getattr(engine, "devices_fallback", None))
        if auth is not None:
            if not auth.done():
                endpoint_file.publish(stage=STAGE_AUTH)
            auth_error = await auth
            if auth_error and _auth_is_definite(auth_error):
                raise StartRefused(f"Авторизация Claude не прошла: {auth_error}")
            if auth_error:
                # Сеть, прокси, перегрузка: распознавание и сводка не ждут — ассистент
                # запускается, подсказки и ответы заработают, когда модель ответит.
                state.warning = f"Claude Code пока не ответил: {auth_error}"
                print(f"предупреждение: {state.warning}", flush=True)
        if staged:
            engine.begin()
        if attached:
            plan = live_catchup.plan(engine.attach_positions, out_dir, heard=heard)
            run["plan"] = plan
            positions = list(engine.attach_positions.values())
            live.heard_from(plan["from_t"] if plan["tracks"] else
                            (heard if heard is not None else min(positions, default=None)))
            if plan["tracks"]:
                engine.start_catchup(plan["tracks"], plan)
            # Дальше — фоновая работа (догонялка минутами грузит процессор):
            # приоритет ниже обычного, запись резидента и звонок важнее.
            _lower_priority()
        endpoint_file.publish(ready=True, stage=None)
        clock.total("ассистент готов")
        if staged:
            # Голоса — после модели (не грузим два больших пакета torch
            # одновременно) и не задерживая готовность: окна до них — без имён.
            _in_thread(clock.timed("голоса загружены", engine.load_voices), "voices")

    try:
        # Подключённый к записи lock не берёт (запись ведёт резидент); свой —
        # берёт при открытии источника («Запись уже идёт» — SystemExit), а
        # stop() в finally безопасен и после частичного старта.
        asyncio.run(_main(state, port, open_browser=open_browser,
                          endpoint_file=endpoint, folder=out_dir,
                          parent_pid=parent_pid, prepare=prepare))
    except KeyboardInterrupt:
        pass
    except StartRefused as e:
        raise SystemExit(str(e)) from None
    finally:
        close = getattr(state.digester, "close", None)
        if close is not None:
            close()  # процесс диалога подсказок — не сирота, даже при сбое
        engine.stop()
        started, plan = run["started"], run["plan"]
        if started and attached:
            live.partial_reasons = _attached_reasons(live, state, engine, plan)
            live.partial = bool(live.partial_reasons)
            live.heard_t = _heard_end(bus, live.heard_t)
        if started and (attached or not live.is_empty()):
            # Итог живого режима — черновик для задачи итогов; у подключённого
            # к записи — и пустой: граница услышанного и пометка о неполноте
            # нужны следующему включению.
            _save_state()
        if started:
            print(f"\nОстановлено: {out_dir}", flush=True)
            print(f'Точный транскрипт: meet transcribe "{out_dir}"', flush=True)


# Этапы старта для окна (`live.stage`): что ассистент делает сейчас.
STAGE_DEVICES = "открываю микрофон и звук…"
STAGE_ATTACH = "подключаюсь к записи…"
STAGE_ASR = "загружаю модель распознавания…"
STAGE_AUTH = "проверяю вход в Claude Code…"


# Причины неполноты, которые следующее включение в эту запись не чинит: его
# догонялка начинается после уже услышанного, а дыра — раньше.
PERMANENT_REASONS = ("catchup_incomplete", "capped", "unknown")


def _attached_reasons(live: LiveState, state: AssistState, engine, plan) -> list[str]:
    """Почему сводка ассистента, включённого посреди записи, неполна (пусто —
    полна): его выключили, пока запись шла (`detached`); начало встречи не
    догнано — не дождались (`catchup_incomplete`) или дальше CAP_S
    (`capped`); он слышит запись не с начала (`late_start`). Из прошлого
    включения остаются причины, которые нынешняя догонялка не чинит."""
    from meet.assist.live_state import FROM_MIN_S

    reasons = {r for r in live.partial_reasons if r in PERMANENT_REASONS}
    if state.detached:
        reasons.add("detached")
    progress = engine.catchup_progress() if hasattr(engine, "catchup_progress") else None
    if progress is not None and not progress.get("complete"):
        reasons.add("catchup_incomplete")
    if plan is not None and plan.get("capped"):
        reasons.add("capped")
    if (live.covered_from or 0.0) >= FROM_MIN_S:
        reasons.add("late_start")
    return sorted(reasons)


def _heard_end(bus: TranscriptBus, before: float | None) -> float | None:
    """Конец последней услышанной реплики (секунды записи)."""
    entries, _ = bus.entries_since(0)
    ends = [float(e.get("end") if isinstance(e.get("end"), (int, float)) else e["t"])
            for e in entries if isinstance(e.get("t"), (int, float))]
    if before is not None:
        ends.append(before)
    return max(ends) if ends else None


def _lower_priority() -> None:
    """Ассистент, подключённый к записи, после старта — фоновая работа:
    приоритет ниже обычного (его ffmpeg догонялки — тоже). Старт идёт с
    обычным: с пониженным на занятом звонком процессоре загрузка моделей
    растягивалась за минуту. На macOS — nice."""
    from meet import plat

    if plat.is_windows():
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
        return
    try:
        os.nice(5)
    except (OSError, AttributeError):
        pass
