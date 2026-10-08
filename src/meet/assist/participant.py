"""Агент-участник встречи (V4, задача 4): цикл агента.

Главный документ — `v4-simple.md`: весь интеллект у агента, Meet — «тупая
труба». Один агент, одна сессия провайдера на встречу. Meet доставляет ему,
что происходит, исполняет его простые просьбы, показывает его сообщения и
хранит журнал (`ChatLog`). Никаких порогов полезности, сопоставлений и
кулдаунов — кроме одной страховки (ниже).

**Сессия.** Системный промпт — `participant_prompts.build_system` (частота
«Как часто писать», есть ли у модели свои инструменты, карта базы знаний,
имя владельца, `kb_exclude`, папки). Сессия поднимается лениво, к первому
ходу: пока на встрече тихо, модель не зовётся.

* Claude Code — постоянный процесс `claude_stream.Conversation` в режиме
  собеседника: читает сам (`add_dirs` — папка встречи, база знаний,
  библиотека встреч), `kb_exclude` — правила запрета (`deny_paths`), сеанс
  сохраняется (`persist`) и продолжается по id из `assistant/sessions.json`.
* Codex, OpenCode — вызов на ход (`runner`) с родным продолжением сеанса
  (`llm.session_kwargs`); Codex работает в своей нейтральной папке (так
  делает провайдер).
* Локальная модель — без продолжения и без инструментов: затравка из
  журнала (`participant_prompts.seed`) в каждом ходе, а запросы `read` /
  `search` / `list` исполняет Meet (`kb_prep`, вне цикла событий, со сроком).

После каждого хода id сеанса перечитывается и сохраняется: ветка сеанса
после отвергнутой картинки меняет его. Сеанс не продолжить
(`resume_failed`) — id забывается, ход повторяется в новом сеансе с
затравкой из журнала.

**Подача.** Реплики шины копятся и уходят дельтой (`participant_prompts.delta`,
владелец — «Вы (вслух)») в паузе разговора (лента молчит `pause_s`), но не
реже `max_interval_s`, пока речь идёт; нового нет — вызова нет. Сообщения
пользователя, нажатия кнопок и реакции — в ближайший ход. Сообщение
пользователя — вне очереди: идущий ход по репликам прерывается (ответ
закрывается скрытым `dropped`), сообщение уходит вместе с недоставленными репликами.

**Ответ.** Ход начинается репликой агента `writing` (`begin_reply`), текст
идёт окну событиями `chat_partial`, затем `parse_reply`: `say` — сообщение
(`finish_reply`), `silent` — скрытая реплика (`dropped`, `silent: True`),
`read` / `search` / `list` — только у модели без инструментов (иначе —
пометка в журнал процесса).

**Свобода** (0.3.7, A1; 0.4 — «как CLI»; `freedom`, настройка
`assist.agent_freedom`, по умолчанию вкл.; `agent_mode` — `assist.agent_mode`:
`auto` / `confirm`) — `meet.llm.consent`. Перед каждым ходом Meet ставит
уровень хода (`consent_level`): только реплики, 👍/👎, «Не надо», повтор
после сбоя — NONE (только чтение рабочих папок и вложений, что бы ни
прозвучало на встрече), ❓ — READ (чтение без карточек), сообщение
пользователя, кнопка агента, слэш-команда — USER (автомод: действует сам,
карточка Meet — только удаление, запись вне рабочих папок, отправка
наружу, MCP-изменения; в режиме `confirm` — на каждое действие, как
0.3.7). Claude Code: каждый вызов решают ворота (`ConsentGate`, хук
PreToolUse и `can_use_tool`), карточка — в чате (`card: "confirm"`); ответ
CLI держится до решения (не дольше `consent.CONFIRM_TIMEOUT_S`). Отказы
хода — строкой в чате. Codex и OpenCode остановить вызов не дают: в ходе
USER они правят рабочие папки и выполняют команды сами, а рискованное им
запрещено (`access`, `work_dirs`). «Личный» отличается только промптом.
Выключено — как в 0.3.6.

**Страховка** — единственная: не больше одного нового сообщения агента за
`merge_window_s` (15 с). Лишнее склеивается с последним сообщением агента
(абзацем), чтобы сломанная модель не завалила ленту. Ответ на сообщение,
нажатие или реакцию пользователя всегда получает своё сообщение.

**Реакции** сами ничего не настраивают: 👍 / 👎 / ❓ уходят агенту в ход, и
он сам решает по правилам промпта (👎 — сообщение мимо темы; частоту и длину
ответов это не меняет — частоту задаёт только «Как часто писать»). Ответ на
❓ (во время встречи — реакция, после встречи — запись `via: "reaction"`)
несёт `explains` = id поясняемой реплики. Правило связи (`_explain_targets`):
- в ходе один ❓ и больше ничего от пользователя — пояснение весь ответ:
  `explains` у реплики с начала хода (первый `say`);
- ❓ вместе с сообщением пользователя (или несколько ❓) — агента просят
  отвечать отдельными `say`, у пояснения — `"explains": "m…"`; пояснением
  считается только такой `say` (ответ на вопрос пользователя метку не получает);
- ❓, который ход так и не пояснил (молчание, ответ только на вопрос), —
  строка «нечего добавить» с `re` на него (после встречи — на просьбу
  `via: "reaction"`), «Стоп» — «Пояснение остановлено»: окно не ждёт вечно.

Журнал и вызовы модели — блокирующие: журнал пишется в одном своём потоке
(он же держит порядок записей), разбор вложений и запросы к базе — в
потоках `asyncio.to_thread`. Цикл событий не блокируется.

API для окна (задача 6): `post_user_message`, `attach`, `click`, `react`,
`stop_reply`, `set_frequency`, `set_profile`, `snapshot`, `view`; «Продолжить разговор»
после встречи — `queue_existing` и `add_note`; события —
`add_listener(fn(name, data))`: `chat` (событие журнала), `chat_partial`
(`{"id","text"}`), `agent` (`view()`).
"""

import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from meet.assist import participant_prompts as pp
from meet.llm import consent
from meet.llm.base import CANCELLED_ERROR, AgentReply, claude_model, model_matches

# --- ритм подачи ---
PAUSE_S = 4.0            # лента молчит столько — пауза в разговоре: ход
MAX_INTERVAL_S = 25.0    # речь идёт — ход не реже
MIN_GAP_S = 8.0          # ход по паузе — не чаще (ASR отдаёт реплики пачками)
MERGE_WINDOW_S = 15.0    # не больше одного нового сообщения агента за столько
TURN_TIMEOUT_S = 180.0   # ход агента (с чтением файлов) — не дольше
PARTIAL_EVERY_S = 0.1    # chat_partial — не чаще 10 раз в секунду
BACKOFF_S = (10.0, 30.0, 60.0, 120.0)
TURN_LINES_MAX_CHARS = 6_000   # реплик в одну дельту (остальное — следующим ходом)
SEED_LINES = 400               # реплик шины на затравку (дальше seed режет сам)
FRESH_AUDIO_TIMEOUT_S = 10.0   # дорасшифровка хвоста перед ответом пользователю

# --- запасной путь: запросы к Meet ---
TOOL_TIMEOUT_S = 30.0
TOOL_SEARCH_DEADLINE_S = 20.0
TOOL_ROUNDS_MAX = 3            # запросов подряд без ответа пользователю
TOOL_RESULT_MAX = 12_000       # символов ответа Meet модели

MERGED_MAX = 8_000             # склеенное сообщение — не длиннее
TASK_NOTE_MAX = 2_000          # контекст задачи в заметке агенту
WORKDIR = "meet-agent"         # рабочая папка процесса Claude Code агента
TOOL_PROVIDERS = ("claude-code", "codex", "opencode")
# Продолжение сеанса с новым системным промптом: Claude Code передаёт его при
# каждом запуске (`--system-prompt` с `--resume`). Codex (`exec resume`) и
# OpenCode (длинный промпт — коротким при продолжении) — нет.
RESUME_RESENDS_SYSTEM = ("claude-code",)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")

LISTENING, WRITING, ERROR = "listening", "writing", "error"

NOTE_INTERRUPTED = ("Твой прошлый ответ прерван новым сообщением пользователя — "
                    "реплики ниже могли уже прийти тебе.")
NOTE_STOPPED = "Пользователь остановил твой прошлый ответ — не продолжай его."
EXPLAIN_REQUEST = ("❓ к твоему сообщению: поясни его — на что ты опирался и что предлагаешь "
                   "(как реакция ❓ во время встречи).")
TOOLS_SPENT = "Слишком много запросов подряд — ответь по тому, что уже есть."
SHUTDOWN_ERROR = "ассистент остановлен"
NOTHING_TO_ADD = "Ассистенту нечего добавить"
# ❓ остался без пояснения: человек остановил ответ.
EXPLAIN_STOPPED = "Пояснение остановлено"
# Ход, остановленный Meet: вызов инструмента прошёл мимо ворот согласия
# (`claude_stream.GATE_GAP_ERROR`).
GATE_GAP = "мимо проверки согласия"
GATE_GAP_LINE = ("Meet остановил ход ассистента: инструмент выполнился мимо проверки согласия. "
                 "Обновите Meet или Claude Code; до этого лучше выключить расширенные возможности.")
_ATTACHMENT_ID = re.compile(r"^a\d{1,9}$")
# Однократная строка первой сессии после обновления до 0.4 (спец. §7, решение 1).
MODE_NOTICE = ("Ассистент теперь сам выполняет обычные действия, рискованные — с вашего разрешения. "
               "Вернуть подтверждение каждого действия — Настройки → Ассистент.")


# --- разбор потока: текст сообщения, который уже можно показать ---

_SAY_OPEN = re.compile(r'"say"\s*:\s*"')
_JSON_HINT = re.compile(r'"(say|silent|read|search|list|buttons)"\s*:')
_ESCAPES = {"n": "\n", "t": " ", "r": "", '"': '"', "\\": "\\", "/": "/", "b": "", "f": ""}


def partial_text(raw: str) -> str:
    """Видимый текст ответа, который ещё пишется: строки `say` (законченные и
    начатая) через пустую строку; ответ без JSON — как есть; служебное
    (`silent`, запросы, начало объекта) — пусто."""
    from meet.llm.jsonreply import strip_reasoning

    body, _ = strip_reasoning(raw or "")
    says = []
    for m in _SAY_OPEN.finditer(body):
        out: list[str] = []
        i = m.end()
        while i < len(body):
            ch = body[i]
            if ch == "\\":
                if i + 1 >= len(body):
                    break
                nxt = body[i + 1]
                if nxt == "u":
                    code = body[i + 2:i + 6]
                    if len(code) < 4:
                        break
                    try:
                        out.append(chr(int(code, 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                out.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            if ch == '"':
                break
            out.append(ch)
            i += 1
        text = "".join(out).strip()
        if text:
            says.append(text)
    if says:
        return "\n\n".join(says)
    plain = body.strip()
    if not plain or plain.startswith(("{", "[", "`")) or _JSON_HINT.search(plain):
        return ""
    return plain


# --- сессии провайдеров ---

class _ConversationSession:
    """Claude Code: постоянный процесс `Conversation` (собеседник, сохраняемый
    сеанс)."""

    def __init__(self, conversation, gate=None) -> None:
        self.conv = conversation
        self.gate = gate
        # Ход работы в чате (`send(on_event=…)`) — если диалог его умеет.
        self._events = _accepts(conversation.send, "on_event")

    @property
    def session_id(self) -> str | None:
        return getattr(self.conv, "session_id", None)

    @property
    def mcp_servers(self) -> list[dict] | None:
        return getattr(self.conv, "mcp_servers", None)

    def take_denials(self) -> list:
        return self.gate.take_denials() if self.gate is not None else []

    @property
    def has_context(self) -> bool:
        return bool(getattr(self.conv, "has_context", False))

    async def send(self, text: str, *, images=(), on_text=None, timeout_s: float = TURN_TIMEOUT_S,
                   level: str = consent.NONE, on_event=None):
        if self.gate is not None:
            self.gate.begin(level)
        extra = {"on_event": on_event} if on_event is not None and self._events else {}
        try:
            try:
                return await self.conv.send(text, images=list(images or ()), on_text=on_text,
                                            timeout_s=timeout_s, **extra)
            except TypeError as e:
                # Диалог без хода работы (обёртка `**kw` над старым `send`):
                # аргумент отвергнут ещё до хода — без него.
                if not extra or "on_event" not in str(e):
                    raise
                self._events = False
                return await self.conv.send(text, images=list(images or ()), on_text=on_text,
                                            timeout_s=timeout_s)
        finally:
            if self.gate is not None:
                self.gate.end()

    async def interrupt(self) -> bool:
        """Ход останавливает сам процесс (`control_request`); True — `send`
        вернёт ответ `cancelled` сам."""
        await self.conv.interrupt()
        return True

    def forget(self) -> None:
        forget = getattr(self.conv, "forget_session", None)
        if forget is not None:
            forget()

    def close(self) -> None:
        self.conv.close()


class _RunnerSession:
    """Codex, OpenCode, локальная модель: вызов `runner` на ход. Сеанс
    продолжается по id (`llm.session_kwargs`), у локальной — нет (затравка в
    каждом ходе)."""

    mcp_servers = None
    gate = None

    def __init__(self, runner, provider: str, *, system, session_id: str | None,
                 resumable: bool, allowed_dirs=(), deny_paths=(), call_kwargs=None,
                 freedom: bool = False, mode: str = consent.MODE_AUTO, work_dirs=()) -> None:
        self._runner = runner
        self._provider = provider
        self._system = system          # () -> str: частота могла смениться
        self._resumable = resumable
        self.session_id = session_id if resumable else None
        self._allowed = tuple(allowed_dirs)
        self._deny = tuple(deny_paths)
        self._kwargs = dict(call_kwargs or {})
        self._freedom = freedom
        self._mode = mode
        self._work = tuple(work_dirs)

    @property
    def has_context(self) -> bool:
        return self._resumable and self.session_id is not None

    def take_denials(self) -> list:
        return []   # у Codex и OpenCode отказов по вызовам Meet не видит

    async def send(self, text: str, *, images=(), on_text=None, timeout_s: float = TURN_TIMEOUT_S,
                   level: str = consent.NONE, on_event=None):
        extra = {}
        if self._resumable:
            extra = {"resume": self.session_id} if self.session_id else {"keep_session": True}
        if self._freedom:
            # Уровень хода → флаги и права вызова. Спросить каждое действие
            # Codex и OpenCode не умеют: в режиме `confirm` просьба — только чтение.
            extra["access"] = consent.READ if level == consent.USER and self._mode != consent.MODE_AUTO else level
            extra["work_dirs"] = self._work
        reply = await self._runner(text, system_prompt=self._system(), images=list(images or ()),
                                   deny_paths=self._deny, allowed_dirs=self._allowed,
                                   timeout_s=timeout_s, max_turns=12, on_text=on_text,
                                   **self._kwargs, **extra)
        if self._resumable:
            if reply.resume_failed:
                self.session_id = None
            elif reply.session_id:
                self.session_id = reply.session_id
        # Ход работы Codex и OpenCode виден только после вызова (их события).
        if on_event is not None:
            for event in getattr(reply, "tools", None) or ():
                on_event(event)
        return reply

    def set_model(self, model: str) -> None:
        """`/model имя`: модель следующих вызовов."""
        self._kwargs["model"] = model

    async def interrupt(self) -> bool:
        return False   # вызов отменяется задачей (провайдер убивает процесс)

    def forget(self) -> None:
        self.session_id = None

    def close(self) -> None:
        pass


def _default_conversation(**kwargs):
    from meet.llm.claude_stream import Conversation, workdir

    # Своя постоянная служебная папка: сеансы агента в истории Claude Code
    # лежат под ней, а не под папкой встречи («Агент» их не подхватит).
    kwargs.setdefault("cwd", workdir(WORKDIR))
    return Conversation(**kwargs)


def _agent_cwd():
    from meet.llm.claude_stream import workdir

    return workdir(WORKDIR)


# --- ход ---

@dataclass
class _Inputs:
    lines: list = field(default_factory=list)   # записи шины (владелец помечен)
    start: int = 0                              # курсор шины до хода
    end: int = 0                                # и после него
    first_pending_at: float | None = None
    user: list = field(default_factory=list)    # сообщения и нажатия (журнал + вложения)
    reactions: list = field(default_factory=list)
    tools: list = field(default_factory=list)   # ответы Meet на запросы
    notes: list = field(default_factory=list)
    frequency: str | None = None
    profile: bool = False                       # профиль сменили: пометка агенту

    def empty(self) -> bool:
        return not (self.lines or self.user or self.reactions or self.tools)

    @property
    def images(self) -> list[str]:
        return [p for m in self.user for p in m.get("_images") or ()]


@dataclass
class _Turn:
    inputs: _Inputs
    addressed: bool                    # ход отвечает пользователю (и цепочка запросов к Meet)
    re: str | None = None              # на какое сообщение пользователя
    # ❓ хода: (поясняемая реплика, просьба `via: "reaction"` после встречи или None).
    explains: list = field(default_factory=list)
    implicit: bool = False             # один ❓ и больше ничего: пояснение — весь ответ
    explained: set = field(default_factory=set)
    silent_noted: bool = False         # «нечего добавить» к `re` уже записано
    reply_id: str | None = None
    raw: list = field(default_factory=list)
    shown: str = ""
    partial_at: float = -1e9
    stop: str | None = None            # "message" | "stop" | "shutdown"
    send_task: asyncio.Future | None = None
    task: asyncio.Future | None = None
    session: object = None
    answered: bool = False             # модель ответила: входы доставлены (ревью M5)


class Participant:
    """Цикл агента-участника одной встречи (см. модуль).

    `bus` — шина реплик; `chatlog` — `ChatLog` папки записи; `provider` —
    имя провайдера (`llm.PROVIDERS`); `runner` — вызов модели (не Claude
    Code); `conversation` — фабрика диалога Claude Code
    (`Conversation(**kwargs)`, тесты подставляют свою); `kb` —
    `kb_prep.KnowledgeBase` сессии или None; `folder` — папка записи;
    `library_root` — библиотека встреч; `group` — id группы встречи (None —
    из meta.json); `owner_*` — подпись и имена владельца; `frequency` —
    «Как часто писать»; `model`/`proxy` — Claude Code; `on_fresh_audio` —
    дорасшифровать хвост речи перед ответом пользователю; `clock` — часы
    (тесты — поддельные); `after_meeting` — разговор после встречи (задача
    «Продолжить разговор»): у записей нет секунд встречи `t`; `profile` —
    профиль сессии (`work` / `personal`, 0.3.7; 0.4 — только промпт): в
    «Личном» свой промпт без карты базы, контекста задачи и глоссария, а
    папки, MCP и ворота — те же, что в «Рабочей встрече». `agent_mode` — как
    действует ассистент по просьбе (`auto` / `confirm`, `assist.agent_mode`).
    `ephemeral` —
    временная встреча (`meet.temp_meeting`): сеанс провайдера не сохраняется
    нигде (Claude Code `--no-session-persistence`, Codex `--ephemeral`,
    OpenCode — сеанс удаляется после вызова), нового хода без живого
    контекста — затравка из журнала встречи. `freedom` — свобода по согласию
    (`assist.agent_freedom`, см. модуль); разрешения «до конца встречи» —
    записи журнала встречи, у временной встречи они удаляются вместе с её
    папкой."""

    def __init__(self, bus, chatlog, *, provider: str, folder, runner=None, conversation=None,
                 kb=None, library_root=None, group=None, owner_name: str = "",
                 owner_speaker: str = pp.OWNER_SPEAKER, owner_names=(),
                 frequency=pp.DEFAULT_FREQUENCY, model: str | None = None,
                 proxy: str | None = None, call_kwargs: dict | None = None,
                 glossary: str = "", task_context: str = "", on_fresh_audio=None,
                 clock=time.monotonic, log=print, merge_window_s: float = MERGE_WINDOW_S,
                 pause_s: float = PAUSE_S, max_interval_s: float = MAX_INTERVAL_S,
                 min_gap_s: float = MIN_GAP_S, turn_timeout_s: float = TURN_TIMEOUT_S,
                 after_meeting: bool = False, seed_until: str | None = None,
                 profile=pp.DEFAULT_PROFILE, ephemeral: bool = False,
                 freedom: bool = False, agent_mode: str = consent.MODE_AUTO,
                 roles=None, mode_notice: bool = False, on_mode_notice=None) -> None:
        from meet import llm
        from meet.assist.tool_rows import ToolRows

        self._bus = bus
        self._chatlog = chatlog
        self.provider = provider
        self._folder = Path(folder)
        self._runner = runner
        self._conversation = conversation or _default_conversation
        self._kb = kb
        self._library_root = Path(library_root) if library_root else None
        self._group = group
        self._owner_name = owner_name or ""
        self._owner_speaker = owner_speaker or pp.OWNER_SPEAKER
        self._owner_names = {n for n in (owner_speaker, *owner_names) if n}
        self.frequency = pp.normalize_frequency(frequency)
        self.profile = pp.normalize_profile(profile)
        # Профиль, с которым поднят текущий сеанс модели: сменили — сеанс
        # пересоздаётся (с продолжением), у него другие папки и промпт.
        self._session_profile: str | None = None
        # Claude Code — модель всегда явно (`--model`): пустая — модель по
        # умолчанию Meet, не модель CLI по умолчанию.
        self._model = claude_model(model) if provider == "claude-code" else model
        # Какая модель на самом деле отвечает (`system/init` Claude Code).
        self.model_actual: str | None = None
        self._proxy = proxy
        self._call_kwargs = dict(call_kwargs or {})
        self._glossary = glossary or ""
        self._task_context = task_context or ""
        self._on_fresh_audio = on_fresh_audio
        self._clock = clock
        self._log = log
        self._merge_window = merge_window_s
        self._pause = pause_s
        self._max_interval = max_interval_s
        self._min_gap = min_gap_s
        self._turn_timeout = turn_timeout_s
        # «Продолжить разговор» после встречи: у сообщений нет секунд записи.
        self._after_meeting = after_meeting
        # Затравка — журнал только до этого сообщения: более поздние ждут
        # своих задач «Продолжить разговор» (ревью M5).
        self._seed_until = seed_until

        self.tools = provider in TOOL_PROVIDERS
        # Свобода по согласию — только у модели со своими инструментами.
        self.freedom = bool(freedom) and self.tools
        self.agent_mode = agent_mode if agent_mode in consent.MODES else consent.MODE_AUTO
        self._gate: consent.ConsentGate | None = None
        self._mcp: list[str] | None = None
        # Режим, в котором CLI на самом деле запустился (`system/init` →
        # `permissionMode`, `Conversation.permission_mode`); None — ещё не известен.
        self._permission_mode: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cards: dict[str, asyncio.Event] = {}     # карточки, которые ждут решения
        self._grant_ids: dict[str, str] = {}           # id записи журнала → ключ разрешения
        self._grant_labels: dict[str, str] = {}
        if self.freedom and provider == "claude-code":
            # Ход может ждать карточку подтверждения (до CONFIRM_TIMEOUT_S).
            self._turn_timeout = max(turn_timeout_s, TURN_TIMEOUT_S + consent.CONFIRM_TIMEOUT_S)
        self.vision = llm.vision(provider)
        self.ephemeral = bool(ephemeral)
        # Временная встреча: ни родного продолжения, ни id сеанса в sessions.json.
        self.resumable = llm.supports_resume(provider) and not self.ephemeral
        self.deny_enforced = llm.deny_enforced(provider)
        self._name = llm.LABELS.get(provider, provider or "модель")

        self._io_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assist-chat")
        self._listeners: list = []
        self.version = 0
        self.state = LISTENING
        self.error: str | None = None
        self.session_state: str | None = None   # "new" | "resumed" | "seeded"
        self._started = False
        self._info: dict | None = None
        self._session = None
        self._stored_sid: str | None = None
        self._kb_map = ""
        self._deny_paths: list[str] = []
        self._materials = 0
        self._images = 0
        # подача
        self._cursor = 0
        self._scanned = 0
        self._last_line_at: float | None = None
        self._first_pending_at: float | None = None
        self._last_turn_at: float | None = None
        self._user: list[dict] = []
        self._user_fresh = False
        self._reactions: list[dict] = []
        self._tool_results: list[dict] = []
        self._notes: list[str] = []
        self._frequency_note: str | None = None
        self._profile_note = False
        self._tool_rounds = 0
        # Цепочка запросов: (addressed, re, explains, implicit) — продолжение того же хода.
        self._chain: tuple | None = None
        self._quiet_until = 0.0        # лимит запросов подряд исчерпан: пауза
        self._failures = 0
        self._retry_at = 0.0
        # ход и лента
        self._turn: _Turn | None = None
        self._partial: dict | None = None
        self._last_shown: tuple[str, float] | None = None   # (id, когда создано)
        self._shown: dict[str, dict] = {}                     # id → текст, кнопки, pin
        self._agent_texts: dict[str, str] = {}
        self._kicked: asyncio.Event | None = None
        self._background: set = set()
        self.turns = 0
        # Ход работы в чате (0.4): строки вызовов инструментов (`tool_rows`).
        self._rows = ToolRows()
        self._row_writes: set = set()
        # Слэш-команды (0.4, `slash`): команды CLI из `initialize`, смена
        # модели `/model`, затравка после хода-команды или `/clear`.
        self._cli_cmds: list[dict] = []
        self._models: list[dict] = []
        self._servers: list[dict] = []
        self.model_override: str | None = None
        self._model_configured = self._model
        self._needs_seed = False
        self._seed_history = True
        # Участники встречи (роли из «Голосов», 0.4): `roles(имена) → {имя: роль}`;
        # какие роли агент уже знает и какие имена уже проверены.
        self._roles = roles
        self._people_sent: dict[str, str] = {}
        self._people_checked: set[str] = set()
        # Однократная строка «Ассистент теперь сам выполняет обычные действия…»
        # (первая сессия после обновления до 0.4, `assist.agent_mode_noticed`).
        self._mode_notice = bool(mode_notice) and self.freedom and self.agent_mode == consent.MODE_AUTO
        self._on_mode_notice = on_mode_notice

    # --- события ---

    def add_listener(self, fn) -> None:
        """`fn(name, data)`: `chat` (событие журнала), `chat_partial`, `agent`."""
        self._listeners.append(fn)

    def _emit(self, name: str, data) -> None:
        for fn in list(self._listeners):
            try:
                fn(name, data)
            except Exception as e:  # окно не роняет цикл агента
                self._log(f"агент: сбой обработчика события {name} ({type(e).__name__}: {e})")

    def _emit_chat(self, events) -> None:
        for ev in events or ():
            if ev:
                self._emit("chat", ev)

    def _changed(self) -> None:
        self.version += 1
        self._emit("agent", self.view())

    def _set_state(self, state: str, error: str | None = None) -> None:
        if (state, error) != (self.state, self.error):
            self.state, self.error = state, error
            self._changed()

    def _kick(self) -> None:
        if self._kicked is not None:
            self._kicked.set()

    async def _io(self, fn, *args, **kwargs):
        """Журнал — в своём потоке, по одному вызову (порядок записей)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._io_pool, partial(fn, *args, **kwargs))

    # --- состояние для окна ---

    @property
    def label(self) -> str:
        """«Claude Code (claude-opus-5-5)»: модель — та, что запустил CLI
        (`system/init`), до первого хода — заданная."""
        if self.provider != "claude-code":
            return self._name
        return f"{self._name} ({self.model_actual or self._model})"

    @property
    def model_mismatch(self) -> bool:
        """CLI запустил не ту модель, что задана в настройках (или командой
        `/model` — тогда сверяется с ней: смену выбрал сам человек)."""
        return (self.provider == "claude-code" and self.model_actual is not None
                and not model_matches(self._model, self.model_actual))

    @property
    def provider_label(self) -> str:
        """Имя провайдера словами («Claude Code», «Codex»)."""
        return self._name

    def _on_model(self, actual) -> None:
        """Модель из `system/init`: показать окну (и предупредить, если не та)."""
        actual = str(actual or "").strip() or None
        if actual is None or actual == self.model_actual:
            return
        self.model_actual = actual
        if self.model_mismatch:
            self._log(f"агент: Claude Code запустил модель {actual}, а в настройках — {self._model}")
        self._changed()

    def view(self) -> dict:
        """`state.agent`: что с агентом и что он видит. `model` — модель,
        которую запустил CLI (None — ещё не известна), `model_configured` —
        заданная в настройках, `model_mismatch` — они разные."""
        claude = self.provider == "claude-code"
        # «Личный»: карта не уходит агенту — и в шапке её нет.
        kb_map = self._kb_map if self.profile == pp.WORK else ""
        return {"state": self.state, "error": self.error, "provider": self.provider,
                "label": self.label, "vision": self.vision, "tools": self.tools,
                "model": self.model_actual if claude else None,
                "model_configured": self._model_configured if claude else None,
                "model_mismatch": self.model_mismatch,
                # `/model имя` (0.4): модель, выбранная командой до конца сессии.
                "model_override": self.model_override,
                # Слэш-команды для подсказки в строке ввода (`slash`) и дополнение
                # аргументов: MCP-серверы с состоянием (`/mcp reconnect …`), модели (`/model …`).
                "commands": self.commands_view(),
                "mcp_servers": [dict(s) for s in self._servers],
                "models": [dict(m) for m in self._models],
                "deny_enforced": self.deny_enforced,
                "frequency": self.frequency, "profile": self.profile,
                "session": self.session_state,
                "freedom": self.freedom, "can": self._can(),
                "grants": [{"id": gid, "label": self._grant_labels.get(gid, key)}
                           for gid, key in self._grant_ids.items()],
                "writing": self._turn.reply_id if self._turn is not None else None,
                # kb — есть карта (база знаний и/или прошлые встречи группы);
                # kb_docs — в ней структура базы знаний, а не только встречи.
                "sees": {"conversation": True, "kb": bool(kb_map),
                         "kb_docs": _map_has_kb(kb_map),
                         "materials": self._materials, "images": self._images}}

    def _can(self) -> dict:
        """Что агент может (шапка окна «может: …»): `consent` — Claude Code
        со свободой (`mcp` — имена MCP-серверов, если CLI их назвал; None —
        неизвестно); `act` — Codex/OpenCode со свободой в автомоде: правит
        рабочие папки и выполняет команды сам, без MCP и рискованного;
        `files` — они же в режиме `confirm`: только чтение файлов по просьбе;
        `read` — без свободы: чтение встречи и базы; `meet` — просит Meet
        (локальная модель). Со свободой — ещё `agent_mode` (`auto` /
        `confirm`) и у Claude Code `auto` — работает ли автомод на деле (None
        — CLI ещё не сообщил режим; False — «Автомод недоступен — спрашиваю
        каждое действие»)."""
        if self.freedom and self.provider == "claude-code":
            auto = None
            if self.agent_mode == consent.MODE_AUTO and self._permission_mode is not None:
                auto = self._permission_mode == "auto"
            return {"mode": "consent", "mcp": list(self._mcp) if self._mcp is not None else None,
                    "agent_mode": self.agent_mode, "auto": auto}
        if self.freedom:
            mode = "act" if self.agent_mode == consent.MODE_AUTO else "files"
            return {"mode": mode, "mcp": None, "agent_mode": self.agent_mode}
        return {"mode": "read" if self.tools else "meet", "mcp": None}

    def commands_view(self) -> list[dict]:
        """Команды для подсказки на «/» (`[{name, hint, description, source}]`):
        команды Meet этого провайдера и команды CLI, если он их назвал."""
        from meet.assist import slash

        return slash.help_items(self.provider, self._cli_cmds)

    def _update_commands(self, session) -> bool:
        """Команды CLI из ответа `initialize` (Claude Code) — в `view()`;
        навыки пользователя (`system/init` → `skills`, проверено пробником на
        2.1.293: личные, проекта и плагинов есть и в `commands`) помечены
        `skill`. Модели (`initialize` → `models`) — для дополнения `/model`."""
        conv = getattr(session, "conv", None)
        found = getattr(conv, "commands", None) if conv is not None else None
        if not isinstance(found, list):
            return False
        skills = set(getattr(conv, "skills", None) or ())
        cmds = [{"name": str(c["name"]).lstrip("/"), "hint": str(c.get("hint") or ""),
                 "description": str(c.get("description") or ""),
                 **({"skill": True} if str(c["name"]).lstrip("/") in skills else {})}
                for c in found if isinstance(c, dict) and c.get("name")]
        models = [{"value": str(m["value"]), "label": str(m.get("displayName") or m.get("description") or "")}
                  for m in getattr(conv, "models", None) or () if isinstance(m, dict) and m.get("value")]
        if cmds == self._cli_cmds and models == self._models:
            return False
        self._cli_cmds, self._models = cmds, models
        return True

    def _update_mcp(self, session) -> None:
        """После хода: MCP-серверы и режим, в котором CLI на деле работает;
        команды CLI (для подсказки «/»)."""
        if self._update_commands(session):
            self._changed()
        if not self.freedom:
            return
        changed = False
        conv = getattr(session, "conv", None)
        mode = getattr(conv, "permission_mode", None) if conv is not None else None
        if isinstance(mode, str) and mode != self._permission_mode:
            self._permission_mode = mode
            changed = True
        servers = getattr(session, "mcp_servers", None)
        if isinstance(servers, list):
            names = [str(x.get("name")) for x in servers
                     if isinstance(x, dict) and x.get("name") and x.get("status") not in ("failed", "disabled")]
            if names != self._mcp:
                self._mcp = names
                changed = True
            # Все серверы с состоянием — для дополнения `/mcp reconnect …` в окне.
            full = [{"name": str(x["name"]), "status": str(x.get("status") or "")}
                    for x in servers if isinstance(x, dict) and x.get("name")]
            if full != self._servers:
                self._servers = full
                changed = True
        if changed:
            self._changed()

    def on_tool_event(self, event: dict) -> None:
        """Ход работы в чате (0.4, спец. §3 «как в Claude CLI»): событие вызова
        инструмента (`claude_stream` — `send(on_event=…)` по мере хода: `tool_use`,
        `gate`, `tool_result`, `hook`; Codex и OpenCode — после вызова, из их
        событий) → запись журнала `kind: "tool"`, `event: "call"` и её патчи по
        id вызова (`tool_rows.ToolRows`), `reply` — реплика агента этого хода.
        Зовётся в цикле событий; журнал пишется в его потоке по порядку
        событий, событие `chat` окну — по готовности. В журнал процесса —
        ничего из вызова (CLAUDE.md: ни команд, ни вывода)."""
        if not isinstance(event, dict):
            return
        turn = self._turn
        reply = turn.reply_id if turn is not None else None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        future = loop.run_in_executor(self._io_pool, partial(
            self._rows.apply, self._chatlog, event, reply=reply, t=self._now_t()))
        self._row_writes.add(future)
        future.add_done_callback(self._row_written)

    def _row_written(self, future) -> None:
        self._row_writes.discard(future)
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            self._log(f"агент: строка хода работы не записана ({type(exc).__name__})")
            return
        self._emit_chat(future.result())

    async def snapshot(self, limit: int | None = 200) -> dict:
        """Лента (`ChatLog.snapshot(feed=True)`), состояние агента и текст,
        который пишется сейчас (переподключение посреди ответа)."""
        chat = await self._io(self._chatlog.snapshot, limit, feed=True)
        return {"chat": chat, "agent": self.view(), "partial": self._partial}

    # --- старт и стоп ---

    async def start(self) -> None:
        """Писатель при старте: недописанные ответы убитого процесса →
        `cancelled`; id сохранённого сеанса провайдера. Модель не зовётся."""
        if self._started:
            return
        self._started = True
        from meet.assist.chatlog import has_chat

        try:
            if await asyncio.to_thread(has_chat, self._folder):
                self._emit_chat(await self._io(self._chatlog.close_interrupted))
            if self.resumable:
                self._stored_sid = await self._io(self._chatlog.session_id, self.provider)
        except OSError as e:
            self._log(f"агент: журнал не прочитан при старте ({type(e).__name__}: {e})")
        await self._save_profile()
        await self._load_info()
        if self._mode_notice:
            await self._note_mode_change()

    async def _note_mode_change(self) -> None:
        """Однократная строка первой сессии после обновления до 0.4: ассистент
        теперь действует сам (автомод), где вернуть подтверждение каждого
        действия. Флаг «показано» — у вызывающего (`on_mode_notice`,
        настройка `assist.agent_mode_noticed`)."""
        self._mode_notice = False
        try:
            added = await self._io(self._chatlog.append, "system", text=MODE_NOTICE, notice="agent_mode")
        except (OSError, RuntimeError, ValueError) as e:
            self._log(f"агент: строка об автомоде не записана ({type(e).__name__})")
            return
        self._emit_chat([added.event])
        if self._on_mode_notice is not None:
            try:
                await asyncio.to_thread(self._on_mode_notice)
            except Exception as e:
                self._log(f"агент: отметка о строке автомода не сохранена ({type(e).__name__})")

    async def _load_info(self) -> None:
        """Карта, запреты, материалы — один раз на сессию (в потоке): шапка
        окна знает, что агент видит, ещё до первого хода."""
        if self._info is None:
            self._info = await asyncio.to_thread(self._gather)
            self._kb_map, self._deny_paths = self._info["kb_map"], self._info["deny"]
            self._materials = self._info["materials"]
            self._changed()

    def skip_existing(self) -> None:
        """Реплики, уже лежащие в шине (лента прошлого включения ассистента),
        — не новые: агент знает их из сеанса или затравки."""
        self._cursor = self._scanned = self._bus.size()

    async def shutdown(self) -> None:
        """Штатная остановка: идущий ход закрывается (`cancelled`), процесс
        модели закрывается, поток журнала отпускается."""
        if self._cards:
            with _quiet():
                await self._cancel_cards()
        turn = self._turn
        if turn is not None and turn.task is not None and not turn.task.done():
            turn.stop = turn.stop or "shutdown"
            turn.task.cancel()
            await asyncio.gather(turn.task, return_exceptions=True)
        pending = list(self._background)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        # Ход, отменённый посреди begin_reply, мог оставить `writing` без id.
        with _quiet():
            if self._started:
                self._emit_chat(await self._io(self._chatlog.close_interrupted, SHUTDOWN_ERROR))
        await asyncio.to_thread(self.close)
        self._io_pool.shutdown(wait=False)

    def close(self) -> None:
        """Закрыть процесс модели (повторно — безопасно)."""
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception as e:
                self._log(f"агент: процесс модели не закрылся ({type(e).__name__}: {e})")

    # --- настройки на ходу ---

    def set_frequency(self, value) -> str:
        """«Как часто писать» сменили: пометка агенту в ближайшем ходе (и в
        системном промпте новых сеансов)."""
        name = pp.normalize_frequency(value)
        if name != self.frequency:
            self.frequency = name
            self._frequency_note = name
            self._changed()
        return name

    def set_profile(self, value) -> str:
        """Профиль сменили по ходу сессии: пометка агенту в ближайшем ходе
        («Профиль сменён на … — это заменяет прежние правила роли: …», как
        смена частоты), сеанс модели к ближайшему ходу пересоздаётся с
        продолжением (другие папки и системный промпт), профиль — в журнал
        встречи (`sessions.json`): «Продолжить разговор» продолжит в нём."""
        name = pp.normalize_profile(value)
        if name != self.profile:
            self.profile = name
            self._profile_note = True
            self._changed()
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                if not self.ephemeral:
                    with _quiet():
                        self._chatlog.set_profile(name)
            else:
                self._background_task(self._save_profile())
        return name

    async def _save_profile(self) -> None:
        set_profile = getattr(self._chatlog, "set_profile", None)
        # Временная встреча следов не оставляет: и профиль — только в памяти.
        if set_profile is None or self.ephemeral:
            return
        try:
            await self._io(set_profile, self.profile)
        except (OSError, ValueError) as e:
            self._log(f"агент: профиль не сохранён в журнал ({type(e).__name__}: {e})")

    def set_task_context(self, text: str) -> None:
        """Контекст задачи (`/task`): системный промпт новых сеансов и —
        если сеанс уже идёт — заметка в ближайший ход (ревью M11)."""
        text = text or ""
        changed = text != self._task_context
        self._task_context = text
        # «Личный» контекста задачи не знает (его промпт без него); при
        # переходе в «Рабочую встречу» задача придёт системным промптом сеанса.
        if changed and text and self._session is not None and self.profile == pp.WORK:
            self.add_note("Пользователь задал контекст задачи встречи (учитывай дальше):\n"
                          + text[:TASK_NOTE_MAX])

    # --- сессия ---

    def _kb_root(self):
        return self._kb.root if self._kb is not None and getattr(self._kb, "configured", False) else None

    def _folders(self) -> dict[str, str]:
        """Папки для промпта (с инструментами): у обоих профилей одни и те же
        (0.4: «Личный» — только промпт), подписи — по профилю."""
        personal = self.profile == pp.PERSONAL
        out = {}
        if self._kb_root() is not None:
            out["База знаний"] = str(self._kb_root())
        if self._library_root is not None:
            out["Прошлые записи" if personal else "Библиотека встреч"] = str(self._library_root)
        out["Эта запись" if personal else "Эта встреча"] = str(self._folder)
        return out

    def _add_dirs(self) -> list[str]:
        """Папки модели (`add_dirs` Claude Code, `allowed_dirs` остальных):
        папка записи, база знаний и библиотека встреч — у обоих профилей."""
        return [str(d) for d in (self._folder, self._kb_root(), self._library_root) if d]

    def _work_dirs(self) -> list[str]:
        """Рабочие папки кроме папки записи (ворота, Codex `--add-dir`,
        OpenCode `external_directory`): база знаний. Библиотека — нет: запись
        в неё вне своей папки идёт карточкой (у Codex и OpenCode — запрещена)."""
        return [str(self._kb_root())] if self._kb_root() is not None else []

    def _deny(self) -> list[str]:
        """Запреты сеанса: `kb_exclude`."""
        return list(self._deny_paths)

    def system_prompt(self) -> str:
        actions = self.provider == "claude-code"
        if self.profile == pp.PERSONAL:
            # Карта, контекст задачи и глоссарий — содержание промпта «Рабочей встречи».
            return pp.build_system(
                frequency=self.frequency, tools_available=self.tools, owner_name=self._owner_name,
                folders=self._folders() if self.tools else None, profile=pp.PERSONAL,
                freedom=self.freedom, actions=actions, mode=self.agent_mode)
        return pp.build_system(
            frequency=self.frequency, tools_available=self.tools, kb_map=self._kb_map,
            owner_name=self._owner_name,
            kb_exclude=tuple(getattr(self._kb, "exclude", ()) or ()) if self._kb is not None else (),
            folders=self._folders() if self.tools else None,
            glossary=self._glossary, task_context=self._task_context, freedom=self.freedom,
            actions=actions, mode=self.agent_mode)

    def _gather(self) -> dict:
        """Карта, запреты, материалы — один раз на сессию (в потоке)."""
        info = {"kb_map": "", "deny": [], "materials": 0}
        group = self._group
        if group is None and self._folder.is_dir():
            try:
                from meet import groups, library

                group = groups.of(library.read_meta(self._folder))
            except Exception:
                group = None
        if self._kb is not None:
            try:
                info["kb_map"] = self._kb.kb_map(group=group, current=self._folder)
            except Exception as e:
                self._log(f"агент: карта базы знаний не собрана ({type(e).__name__}: {e})")
            try:
                info["deny"] = list(self._kb.exclude_paths())
            except Exception as e:
                self._log(f"агент: исключения базы знаний не собраны ({type(e).__name__}: {e})")
        info["materials"] = len(self._material_records())
        return info

    def _material_records(self) -> list[dict]:
        if not self._folder.is_dir():
            return []
        try:
            from meet import materials

            return materials.records(self._folder)
        except Exception:
            return []

    async def _ensure_session(self):
        if self._session is not None and self._session_profile != self.profile:
            # Профиль сменили: у сеанса другие папки и промпт — новый процесс.
            # Claude Code продолжает тот же сеанс (системный промпт он берёт
            # заново при каждом запуске); Codex и OpenCode при продолжении
            # промпт не перечитывают — их сеанс забывается, новый получает
            # промпт профиля и затравку из журнала (ревью I1).
            await asyncio.to_thread(self.close)
            if self.resumable and self.provider not in RESUME_RESENDS_SYSTEM:
                self._log(f"агент: профиль «{pp.PROFILES[self.profile]}» — новый сеанс "
                          "модели с затравкой из журнала")
                self._stored_sid = None
                try:
                    await self._io(self._chatlog.set_session_id, self.provider, None)
                except (OSError, ValueError) as e:
                    self._log(f"агент: прежний сеанс не забыт ({type(e).__name__}: {e})")
            else:
                self._log(f"агент: профиль «{pp.PROFILES[self.profile]}» — сеанс модели заново "
                          "с продолжением")
        if self._session is not None:
            return self._session
        await self._load_info()
        dirs = self._add_dirs()
        # `kb_exclude` (правило CLI у Claude Code, права у OpenCode, просьба у Codex).
        deny = self._deny()
        if self.provider == "claude-code":
            extra = {}
            if self.freedom:
                # Свобода (0.4): каждый вызов решают ворота — сами, автомодом
                # CLI (`auto`) или карточкой Meet (рискованное; в `confirm` —
                # всё). Папки, MCP и ворота у обоих профилей одни.
                self._loop = asyncio.get_running_loop()
                cwd = await asyncio.to_thread(_agent_cwd)
                sensitive = await asyncio.to_thread(consent.sensitive_paths, library_root=self._library_root)
                self._gate = consent.ConsentGate(
                    own_dirs=[self._folder], work_dirs=self._work_dirs(), deny_paths=self._deny_paths,
                    sensitive=sensitive, cwd=cwd, log=self._log, confirmer=self._confirm_sync,
                    mode=self.agent_mode,
                    ask_text=pp.ASK_FIRST_PERSONAL if self.profile == pp.PERSONAL else consent.ASK_FIRST)
                self._gate.allow_paths(await self._io(self._attached_paths))
                for gid, key, label in await self._io(self._load_grants):
                    self._grant_ids[gid], self._grant_labels[gid] = key, label
                    self._gate.add_grant(key, label)
                extra.update(gate=self._gate, cwd=cwd, mode=self.agent_mode)
                # Закрытые данные — ещё и правилами CLI (Grep по папке выше их не прочтёт).
                deny += [str(p) for p in sensitive if p.exists()]
            conv = self._conversation(
                system_prompt=self.system_prompt(), model=self._model, proxy=self._proxy,
                log=self._log, responder=True, add_dirs=dirs, deny_paths=deny,
                persist=not self.ephemeral, resume=self._stored_sid, on_model=self._on_model,
                **extra)
            self._session = _ConversationSession(conv, self._gate)
        else:
            if self._runner is None:
                raise RuntimeError(f"нет вызова модели для {self.provider}")
            if self.freedom:
                # Codex — запрет просьбой в промпте, OpenCode — в правах.
                deny += [str(p) for p in await asyncio.to_thread(
                    consent.sensitive_paths, library_root=self._library_root) if p.exists()]
            self._session = _RunnerSession(
                self._runner, self.provider, system=self.system_prompt,
                session_id=self._stored_sid, resumable=self.resumable,
                allowed_dirs=dirs, deny_paths=deny, call_kwargs=self._call_kwargs,
                freedom=self.freedom, mode=self.agent_mode,
                work_dirs=[str(self._folder), *self._work_dirs()])
        self._session_profile = self.profile
        self._changed()
        return self._session

    def _attached_paths(self) -> list[str]:
        """Что пользователь уже приложил к чату (вложения журнала): их можно
        читать и без согласия, даже если источник лежит вне встречи."""
        out = []
        try:
            for m in self._chatlog.messages():
                if m.get("kind") == "attachment" and m.get("status") not in ("failed", "removed"):
                    out += [str(m[k]) for k in ("path", "source") if isinstance(m.get(k), str) and m[k]]
        except Exception:
            pass
        return out

    def consent_level(self, inputs) -> str:
        """Уровень хода (`meet.llm.consent`, 0.4): сообщение пользователя,
        кнопка агента (кроме отказа — `click_level`), слэш-команда — USER;
        ❓ (реакция во время встречи, `via: "reaction"` после неё) — READ;
        реплики встречи, 👍/👎 — NONE. Повтор после сбоя (`_replayed`)
        уровня не даёт: его даёт только свежий ввод пользователя. Кто
        просил, решает Meet: просьбу из речи встречи классификатор CLI не
        отличит."""
        level = consent.NONE
        for m in inputs.user:
            if m.get("_replayed"):
                continue
            if m.get("via") == "button":
                level = consent.higher(level, consent.click_level(m.get("text") or ""))
            elif m.get("via") == "reaction":
                level = consent.higher(level, consent.READ)
            else:
                level = consent.higher(level, consent.USER)
        for r in inputs.reactions:
            if r.get("emoji") == "❓" and r.get("on") is not False and not r.get("_replayed"):
                level = consent.higher(level, consent.READ)
        return level

    # --- карточки подтверждения (Claude Code, свобода по согласию) ---

    def _confirm_sync(self, card: dict) -> str:
        """Ворота (поток ответа CLI): показать карточку и дождаться решения.
        → allow / deny / timeout / cancelled."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return "cancelled"
        try:
            future = asyncio.run_coroutine_threadsafe(self._ask_card(card), loop)
            return future.result(timeout=consent.CONFIRM_TIMEOUT_S + 30)
        except Exception as e:
            self._log(f"агент: карточка подтверждения не дождалась ответа ({type(e).__name__}: {e})")
            return "cancelled"

    async def _ask_card(self, card: dict) -> str:
        """Карточка в журнал (`system`, `card: "confirm"`), ожидание решения:
        событием (`confirm` в этом процессе) или по журналу (решение записал
        резидент — чат после встречи), до CONFIRM_TIMEOUT_S."""
        import time as _time

        timeout = consent.CONFIRM_TIMEOUT_S
        fields = {"text": f"Ассистент хочет выполнить: {card.get('title') or card.get('tool')}",
                  "card": "confirm", "tool": card.get("tool"), "title": card.get("title"),
                  "args": card.get("args") or "", "what": card.get("what") or "",
                  "size": card.get("size") or "", "expires_at": round(_time.time() + timeout, 3)}
        for key in ("preview", "warnings", "grant"):
            if card.get(key):
                fields[key] = card[key]
        if card.get("tool_use_id"):
            # Окно ставит карточку в строку этого вызова (ход работы, 0.4).
            fields["tool_use_id"] = str(card["tool_use_id"])
        t = self._now_t()
        if t is not None:
            fields["t"] = t
        added = await self._io(self._chatlog.append, "system", **fields)
        mid = added.message["id"]
        event = self._cards[mid] = asyncio.Event()
        self._emit_chat([added.event])
        self._changed()
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        try:
            while True:
                left = end - loop.time()
                if left <= 0:
                    break
                try:
                    await asyncio.wait_for(event.wait(), min(left, 0.5))
                except asyncio.TimeoutError:
                    pass
                turn = self._turn
                if turn is not None and turn.stop in ("stop", "shutdown") and mid in self._cards:
                    # «Стоп» пришёл, пока карточка записывалась, — она уже не нужна.
                    with _quiet():
                        self._emit_chat([await self._io(self._chatlog.decide_card, mid, "cancelled")])
                msg = await self._io(self._chatlog.get, mid)
                decision = (msg or {}).get("decision")
                if decision == consent.ALLOW_MEETING and card.get("grant"):
                    await self._record_grant(card["grant"])
                    return consent.ALLOW_MEETING
                if decision:
                    return "allow" if decision in ("allow", consent.ALLOW_MEETING) else (
                        "timeout" if decision == "timeout" else "deny" if decision == "deny" else "cancelled")
            try:
                ev = await self._io(self._chatlog.decide_card, mid, "timeout")
                self._emit_chat([ev])
                return "timeout"
            except ValueError:   # решили в последний момент
                msg = await self._io(self._chatlog.get, mid)
                return "allow" if (msg or {}).get("decision") == "allow" else "deny"
        finally:
            self._cards.pop(mid, None)
            self._changed()

    async def confirm(self, mid: str, allow: bool, *, meeting: bool = False) -> dict:
        """Решение человека по карточке подтверждения (один раз; решённая
        карточка — ValueError): разрешить один раз, «разрешать такое до конца
        встречи» (`meeting`) или отклонить. → событие журнала."""
        decision = (consent.ALLOW_MEETING if meeting else "allow") if allow else "deny"
        ev = await self._io(self._chatlog.decide_card, mid, decision)
        self._emit_chat([ev])
        event = self._cards.get(mid)
        if event is not None:
            event.set()
        return ev

    # --- «Разрешать такое до конца встречи» ---

    async def _record_grant(self, grant: dict) -> None:
        """Разрешение до конца встречи — запись журнала (`system`, `grant`):
        переживает перезапуск ассистента в этой встрече; после встречи —
        не действует (`after_meeting`)."""
        key, label = str(grant.get("key") or ""), str(grant.get("label") or "")
        if not key or key in self._grant_ids.values():
            return
        added = await self._io(self._chatlog.append, "system", grant=key, label=label,
                               text=f"Разрешено до конца встречи: {label}", after_meeting=self._after_meeting)
        self._grant_ids[added.message["id"]] = key
        self._grant_labels[added.message["id"]] = label
        if self._gate is not None:
            self._gate.add_grant(key, label)
        self._emit_chat([added.event])
        self._changed()

    def _load_grants(self) -> list[tuple[str, str, str]]:
        """Действующие разрешения этой встречи из журнала: (id, ключ, подпись)."""
        out = []
        try:
            for m in self._chatlog.messages():
                if (m.get("kind") == "system" and isinstance(m.get("grant"), str) and not m.get("revoked")
                        and bool(m.get("after_meeting")) == self._after_meeting):
                    out.append((m["id"], str(m["grant"]), str(m.get("label") or m["grant"])))
        except Exception:
            pass
        return out

    async def revoke_grant(self, gid: str) -> dict:
        """Отозвать «разрешать до конца встречи» (× в шапке). Нет такого — ValueError."""
        if gid not in self._grant_ids:
            raise ValueError(f"{gid}: нет такого разрешения")
        ev = await self._io(self._chatlog.patch, gid, {"revoked": True})
        key = self._grant_ids.pop(gid)
        self._grant_labels.pop(gid, None)
        if self._gate is not None:
            self._gate.remove_grant(key)
        if ev:
            self._emit_chat([ev])
        self._changed()
        return ev or {}

    async def _cancel_cards(self) -> None:
        """«Стоп», остановка ассистента: карточки, которые ждут, — отменены."""
        for mid, event in list(self._cards.items()):
            try:
                self._emit_chat([await self._io(self._chatlog.decide_card, mid, "cancelled")])
            except (ValueError, OSError, RuntimeError):
                pass
            event.set()

    async def _note_denials(self, session) -> None:
        """Ворота отказали агенту в этом ходе — короткая строка в чате."""
        take = getattr(session, "take_denials", None)
        line = consent.denial_line(take() if take is not None else [])
        if not line:
            return
        try:
            added = await self._io(self._chatlog.append, "system", text=line, gate=True)
        except (OSError, RuntimeError, ValueError) as e:
            self._log(f"агент: строка об отказе не записана ({type(e).__name__}: {e})")
            return
        self._emit_chat([added.event])

    async def _store_session(self, session) -> None:
        """Id сеанса — после каждого хода: ветка после картинки его меняет."""
        if not self.resumable:
            return
        sid = session.session_id
        if sid == self._stored_sid:
            return
        try:
            await self._io(self._chatlog.set_session_id, self.provider, sid)
            self._stored_sid = sid
        except (OSError, ValueError) as e:
            self._log(f"агент: id сеанса не сохранён ({type(e).__name__}: {e})")

    # --- реплики шины ---

    def _owner(self, entry: dict) -> dict:
        voice = str(entry.get("voice") or "")
        if entry.get("speaker") in self._owner_names or voice.endswith("/mic:owner"):
            return {**entry, "owner": True}
        return entry

    def _take_lines(self, cursor: int) -> tuple[list[dict], int]:
        """Новые реплики с `cursor` (без спрятанных дублей) в пределах
        бюджета дельты → (записи, курсор после них)."""
        entries, size = self._bus.entries_since(cursor)
        hidden = self._bus.hidden() if hasattr(self._bus, "hidden") else set()
        out: list[dict] = []
        chars = 0
        end = cursor
        for i, entry in enumerate(entries, start=cursor):
            text = str(entry.get("text") or "")
            if i not in hidden and text.strip():
                if out and chars + len(text) > TURN_LINES_MAX_CHARS:
                    break
                out.append(self._owner(entry))
                chars += len(text) + 40
            end = i + 1
        if any(e.get("catchup") for e in out):
            out.sort(key=lambda e: e["t"] if isinstance(e.get("t"), (int, float)) else float("inf"))
        return out, end

    def _history(self, before: int) -> list[dict]:
        entries, _ = self._bus.entries_since(0)
        hidden = self._bus.hidden() if hasattr(self._bus, "hidden") else set()
        lines = [self._owner(e) for i, e in enumerate(entries[:before])
                 if i not in hidden and str(e.get("text") or "").strip()]
        lines = lines[-SEED_LINES:]
        if any(e.get("catchup") for e in lines):
            texts = [str(e.get("text")) for e in lines]
            _, lines = _chronological(texts, lines)
        return lines

    def _now_t(self) -> float | None:
        if self._after_meeting:
            return None
        entries, _ = self._bus.entries_since(max(0, self._bus.size() - 5))
        times = [e.get("end") if isinstance(e.get("end"), (int, float)) else e.get("t")
                 for e in entries]
        times = [float(t) for t in times if isinstance(t, (int, float)) and not isinstance(t, bool)]
        return max(times) if times else None

    def _scan(self) -> None:
        size = self._bus.size()
        if size <= self._scanned:
            return
        self._scanned = size
        now = self._clock()
        self._last_line_at = now
        if self._first_pending_at is None and size > self._cursor:
            self._first_pending_at = now

    def _has_lines(self) -> bool:
        return self._bus.size() > self._cursor and bool(self._take_lines(self._cursor)[0])

    # --- когда ход ---

    def turn_due(self, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        if self._first_pending_at is not None and not self._has_lines():
            self._first_pending_at = None   # только спрятанные дубли — не «ждут»
        if self._turn is not None:
            return False
        waiting = (self._failures and now < self._retry_at) or now < self._quiet_until
        if self._user and (self._user_fresh or not waiting):
            return True
        if waiting:
            return False
        if self._reactions or self._tool_results:
            return True
        if not self._has_lines():
            return False
        if self._first_pending_at is not None and now - self._first_pending_at >= self._max_interval:
            return True
        quiet = self._last_line_at is None or now - self._last_line_at >= self._pause
        gap = self._last_turn_at is None or now - self._last_turn_at >= self._min_gap
        return quiet and gap

    def _wake_in(self, now: float) -> float | None:
        """Через сколько секунд ход может стать нужен без нового сигнала."""
        if self._turn is not None:
            return None
        if self._first_pending_at is not None and not self._has_lines():
            self._first_pending_at = None
        wake: list[float] = []
        held = max(self._retry_at if self._failures else 0.0, self._quiet_until)
        if held > now and (self._user or self._reactions or self._tool_results or self._has_lines()):
            wake.append(held - now)
        elif self._has_lines():
            if self._last_line_at is not None:
                at = self._last_line_at + self._pause
                if self._last_turn_at is not None:
                    at = max(at, self._last_turn_at + self._min_gap)
                wake.append(at - now)
            if self._first_pending_at is not None:
                wake.append(self._first_pending_at + self._max_interval - now)
        wake = [max(w, 0.01) for w in wake]
        return min(wake) if wake else None

    # --- цикл ---

    async def run(self, stop: asyncio.Event) -> None:
        """Цикл агента до `stop`: ждёт сигнала шины, своих событий или срока."""
        loop = asyncio.get_running_loop()
        self._kicked = asyncio.Event()
        await self.start()
        signal = self._bus.changed
        signal.bind(loop)
        seen = signal.seq
        stopped = asyncio.ensure_future(stop.wait())
        try:
            while not stop.is_set():
                now = self._clock()
                self._scan()
                if self.turn_due(now):
                    self._start_turn(now)
                waiter = asyncio.ensure_future(signal.wait(seen, self._wake_in(now)))
                kick = asyncio.ensure_future(self._kicked.wait())
                await asyncio.wait({waiter, kick, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if waiter.done():
                    seen = waiter.result()
                else:
                    waiter.cancel()
                kick.cancel()
                self._kicked.clear()
        finally:
            stopped.cancel()
            await self.shutdown()

    async def tick(self) -> bool:
        """Один шаг без ожидания (тесты, поддельные часы): нужен ход — сделать
        его и дождаться. True — ход был."""
        await self.start()
        now = self._clock()
        self._scan()
        if not self.turn_due(now):
            return False
        turn = self._start_turn(now)
        if turn is None:
            return False
        await asyncio.gather(turn.task, return_exceptions=True)
        return True

    def _collect(self) -> _Inputs:
        # Ход-команда (слэш-команда CLI, `slash`) — отдельным ходом и одна:
        # без реплик и прочего ввода (они уйдут следующим ходом). Сообщения
        # перед ней — своим ходом раньше неё.
        at = next((i for i, m in enumerate(self._user) if m.get("_command")), None)
        if at == 0:
            command = self._user.pop(0)
            self._user_fresh = bool(self._user)
            return _Inputs(start=self._cursor, end=self._cursor, user=[command])
        later: list[dict] = []
        if at is not None:
            self._user, later = self._user[:at], self._user[at:]
        inputs = self._collect_all()
        self._user = later
        self._user_fresh = bool(later)
        return inputs

    def _collect_all(self) -> _Inputs:
        lines, end = self._take_lines(self._cursor)
        inputs = _Inputs(lines=lines, start=self._cursor, end=end,
                         first_pending_at=self._first_pending_at,
                         user=self._user, reactions=self._reactions, tools=self._tool_results,
                         notes=self._notes, frequency=self._frequency_note,
                         profile=self._profile_note)
        self._cursor = end
        if self._bus.size() <= end:
            self._first_pending_at = None
        self._user, self._reactions, self._tool_results, self._notes = [], [], [], []
        self._frequency_note = None
        self._profile_note = False
        self._user_fresh = False
        return inputs

    def _requeue(self, inputs: _Inputs, *, transcript=True, user=True, others=True,
                 replay: bool = False) -> None:
        """Недоставленное — назад, в начало очереди. `replay` — повтор после
        сбоя хода: согласие этого ввода не повторяется (`_replayed`)."""
        if transcript and inputs.end > inputs.start:
            self._cursor = min(self._cursor, inputs.start)
            self._first_pending_at = inputs.first_pending_at or self._clock()
        mark = (lambda xs: [{**x, "_replayed": True} for x in xs]) if replay else (lambda xs: xs)
        if user:
            self._user = mark(inputs.user) + self._user
        if others:
            self._reactions = mark(inputs.reactions) + self._reactions
            self._tool_results = inputs.tools + self._tool_results
            self._notes = inputs.notes + self._notes
            if inputs.frequency and self._frequency_note is None:
                self._frequency_note = inputs.frequency
            self._profile_note = self._profile_note or inputs.profile

    def _start_turn(self, now: float) -> _Turn | None:
        inputs = self._collect()
        if inputs.empty():
            self._notes = inputs.notes + self._notes
            if inputs.frequency and self._frequency_note is None:
                self._frequency_note = inputs.frequency
            self._profile_note = self._profile_note or inputs.profile
            return None
        addressed = bool(inputs.user or inputs.reactions)
        re_id = inputs.user[-1]["id"] if inputs.user else None
        explains = _explain_targets(inputs)
        implicit = _implicit_explain(inputs, explains)
        if not addressed and inputs.tools and self._chain is not None:
            # ответ Meet — продолжение ответа пользователю
            addressed, re_id, explains, implicit = self._chain
        turn = _Turn(inputs=inputs, addressed=addressed, re=re_id, explains=explains, implicit=implicit)
        self._turn = turn
        self._last_turn_at = now
        turn.task = asyncio.ensure_future(self._run_turn(turn))
        return turn

    async def _run_turn(self, turn: _Turn) -> None:
        try:
            await self._turn_body(turn)
        except asyncio.CancelledError:
            # Остановка ассистента посреди хода: ответ — `cancelled`.
            if turn.reply_id is not None:
                with _quiet():
                    self._emit_chat([await self._io(
                        self._chatlog.finish_reply, turn.reply_id, status="cancelled",
                        text=turn.shown, error=SHUTDOWN_ERROR)])
            raise
        except Exception as e:  # сбой цикла не роняет ассистента
            self._log(f"агент: ход упал ({type(e).__name__}: {e})")
            self._failed(f"{type(e).__name__}: {e}")
            if not turn.answered:
                # Модель ответила — входы она уже видела: повтор задублировал
                # бы показанное сообщение (сбой журнала посреди показа, M5).
                self._requeue(turn.inputs, replay=True)
            if turn.reply_id is not None:
                with _quiet():
                    self._emit_chat([await self._io(
                        self._chatlog.finish_reply, turn.reply_id,
                        status="failed" if turn.addressed else "dropped",
                        text=turn.shown, error=f"{type(e).__name__}: {e}"[:300])])
        finally:
            if self._turn is turn:
                self._turn = None
            self._partial = None
            if self.state == WRITING:
                self._set_state(LISTENING)
            else:
                self._changed()
            self._kick()

    async def _fresh_audio(self, turn: _Turn) -> None:
        """Перед ответом пользователю — дорасшифровать хвост речи (как
        вопросы): ответ видит и только что сказанное."""
        if self._on_fresh_audio is None or not turn.inputs.user:
            return
        try:
            await asyncio.wait_for(asyncio.to_thread(self._on_fresh_audio), FRESH_AUDIO_TIMEOUT_S)
        except Exception as e:
            self._log(f"агент: хвост речи не дорасшифрован ({type(e).__name__})")
            return
        more, end = self._take_lines(turn.inputs.end)
        if end > turn.inputs.end:
            turn.inputs.lines += more
            turn.inputs.end = self._cursor = end
            self._scanned = max(self._scanned, end)
            if self._bus.size() <= end:
                self._first_pending_at = None

    async def _turn_body(self, turn: _Turn) -> None:
        inputs = turn.inputs
        if inputs.user and inputs.user[0].get("_command"):
            await self._command_turn(turn)
            return
        await self._fresh_audio(turn)
        session = turn.session = await self._ensure_session()
        if turn.stop:          # прервали, пока ход собирался
            if turn.stop == "stop":
                self._requeue_after_stop(inputs)
            else:
                self._requeue(inputs)
            return
        fields = {"mode": "reply" if turn.addressed else "proactive"}
        if turn.re:
            fields["re"] = turn.re
        if turn.implicit:
            fields["explains"] = turn.explains[0][0]
        t = self._now_t()
        if t is not None:
            fields["t"] = t
        began = await self._io(self._chatlog.begin_reply, **fields)
        turn.reply_id = began.message["id"]
        self._emit_chat([began.event])
        self._set_state(WRITING)
        # Запросы к Meet подряд: ход с ответами Meet — продолжение цепочки.
        self._tool_rounds = self._tool_rounds + 1 if inputs.tools else 0
        text = await self._io(self._compose, turn, session)
        if turn.stop:          # прервали, пока ход собирался: модели не шлём
            await self._finish_stopped(turn, AgentReply(text="", error=CANCELLED_ERROR, cancelled=True))
            return
        reply = await self._send(turn, session, text)
        await self._store_session(session)
        if reply.resume_failed and not turn.stop:
            self._log(f"агент: сеанс {self.provider} не продолжить ({reply.error}) — новый с затравкой")
            session.forget()
            await self._store_session(session)
            text = await self._io(self._compose, turn, session, True)
            reply = await self._send(turn, session, text)
            await self._store_session(session)
        if reply.cancelled or turn.stop == "stop" or (turn.stop and reply.error):
            await self._finish_stopped(turn, reply)
            return
        if reply.error and GATE_GAP in reply.error:
            # Инструмент выполнился мимо ворот согласия — видно в ленте (ревью R5).
            with _quiet():
                added = await self._io(self._chatlog.append, "system", gate=True, level="error",
                                       text=GATE_GAP_LINE)
                self._emit_chat([added.event])
        if reply.error:
            await self._finish_failed(turn, reply)
            return
        self._failures = 0
        self.turns += 1
        turn.answered = True
        await self._apply(turn, reply)
        self._set_state(LISTENING)

    def _compose(self, turn: _Turn, session, force_seed: bool = False) -> str:
        """Сообщение хода: затравка (новая сессия или нет родного продолжения)
        и дельта. Блокирующее (журнал) — в потоке журнала."""
        inputs = turn.inputs
        parts = []
        if force_seed or not session.has_context or self._needs_seed:
            # После `/clear` — без переписки чата: новый разговор (спец. §6).
            journal = None
            if self._seed_history:
                journal = (_JournalUntil(self._chatlog, self._seed_until) if self._seed_until
                           else self._chatlog)
            self._needs_seed, self._seed_history = False, True
            history = self._history(inputs.start)
            # Новый сеанс не знает участников: все узнанные с ролью — в затравку.
            self._people_sent, self._people_checked = {}, set()
            people = self._new_people([*history, *inputs.lines])
            seed = pp.seed(journal, "", self._materials_summary(),
                           pp.ParticipantSettings(frequency=self.frequency,
                                                  owner_speaker=self._owner_speaker,
                                                  profile=self.profile),
                           transcript=history, t=self._now_t(), people=people)
            parts.append(seed)
            self.session_state = "seeded" if journal is not None and self._chatlog_has_history() else "new"
        elif self.session_state is None:
            self.session_state = "resumed"
        delta = pp.delta(inputs.lines, inputs.user, (), inputs.reactions,
                         owner_speaker=self._owner_speaker, tool_results=inputs.tools,
                         notes=inputs.notes, frequency=inputs.frequency,
                         agent_texts=self._agent_texts, profile=self.profile,
                         profile_changed=inputs.profile, people=self._new_people(inputs.lines))
        # Карта при возврате в «Рабочую встречу» — только системным промптом:
        # сеанс к этому ходу пересоздан с ним (Codex, OpenCode — новый сеанс).
        if delta:
            parts.append(delta)
        return "\n\n".join(parts)

    def _new_people(self, entries) -> dict[str, str]:
        """Узнанные участники с ролью («Голоса», 0.4), которых агент ещё не
        знает: имена говорящих в `entries` (кроме владельца и «Спикер N» —
        у них роли нет) → `{имя: роль}`. Каждое имя проверяется один раз за
        сеанс. Блокирующее (файлы базы голосов) — в потоке журнала. В журнал
        процесса — только счётчик (CLAUDE.md: без текста ролей)."""
        if self._roles is None:
            return {}
        names = []
        for e in entries or ():
            if not isinstance(e, dict) or e.get("owner"):
                continue
            name = str(e.get("speaker") or "").strip()
            if name and name not in self._owner_names and name not in self._people_checked and name not in names:
                names.append(name)
        if not names:
            return {}
        self._people_checked.update(names)
        try:
            found = self._roles(names) or {}
        except Exception as e:
            self._log(f"агент: роли участников не прочитаны ({type(e).__name__})")
            return {}
        new = {str(k): str(v) for k, v in found.items()
               if str(k) in names and isinstance(v, str) and v.strip() and self._people_sent.get(str(k)) != v}
        if new:
            self._people_sent.update(new)
            self._log(f"агент: роли участников агенту: {len(new)}")
        return new

    def _chatlog_has_history(self) -> bool:
        try:
            return any(m.get("kind") in ("agent", "user") and m.get("status") != "writing"
                       for m in self._chatlog.messages())
        except Exception:
            return False

    def _materials_summary(self) -> str:
        """Материалы для затравки. Id — только журнальные (как в дельте и
        журнале): у материала свой счётчик, агенту он не показывается."""
        journal = {}
        try:
            for m in self._chatlog.messages():
                if m.get("kind") == "attachment" and m.get("ref"):
                    journal[m["ref"]] = m["id"]
        except Exception:
            pass
        lines = []
        for record in self._material_records():
            meta = record.get("meta") or {}
            source = (meta.get("source") or {}).get("path") or ""
            jid = journal.get(record.get("id"))
            head = f"- {jid + ' ' if jid else ''}«{meta.get('title') or Path(source).name}»"
            if meta.get("kind"):
                head += f" ({meta['kind']})"
            text_path = _text_dump_path(self._folder, str(record.get("id") or ""))
            if text_path.is_file():
                head += f" — текст: {text_path}"
            lines.append(head)
            summary = (record.get("summary") or "").strip()
            if summary:
                lines.append("  " + " ".join(summary.split())[:300])
        return "\n".join(lines)

    async def _send(self, turn: _Turn, session, text: str) -> AgentReply:
        kwargs = {"level": self.consent_level(turn.inputs)} if self.freedom else {}
        task = asyncio.ensure_future(session.send(
            text, images=turn.inputs.images if self.vision else (),
            on_text=partial(self._on_text, turn), timeout_s=self._turn_timeout,
            on_event=self.on_tool_event, **kwargs))
        turn.send_task = task
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            turn.send_task = None
        if self.freedom:
            if self._cards:
                # Ход кончился — его карточки больше не ждут (вызов, ради
                # которого их показали, уже не выполнится; ревью R4).
                with _quiet():
                    await self._cancel_cards()
            with _quiet():
                await self._note_denials(session)
        self._update_mcp(session)
        # Строки хода работы этого хода — в журнале раньше конца ответа.
        if self._row_writes:
            await asyncio.gather(*list(self._row_writes), return_exceptions=True)
        if task.cancelled():
            return AgentReply(text="".join(turn.raw), error=CANCELLED_ERROR, cancelled=True)
        exc = task.exception()
        if exc is not None:
            return AgentReply(text="", error=f"{type(exc).__name__}: {exc}")
        reply = task.result()
        if self.provider == "claude-code" and getattr(reply, "model", None):
            self._on_model(reply.model)
        return reply

    def _on_text(self, turn: _Turn, piece) -> None:
        if piece is None:   # новое сообщение модели после инструмента
            turn.raw.clear()
            return
        turn.raw.append(piece)
        now = self._clock()
        if now - turn.partial_at < PARTIAL_EVERY_S or turn.reply_id is None:
            return
        turn.partial_at = now
        text = partial_text("".join(turn.raw))
        if text and text != turn.shown:
            turn.shown = text
            self._partial = {"id": turn.reply_id, "text": text}
            self._emit("chat_partial", dict(self._partial))

    async def _interrupt(self, turn: _Turn, why: str) -> None:
        if why in ("stop", "shutdown") and self._cards:
            await self._cancel_cards()
        if turn.stop and not (why == "stop" and turn.stop == "message"):
            return
        already = turn.stop is not None
        turn.stop = why
        if already:
            return   # остановка уже идёт — только причина другая
        task = turn.send_task
        if task is None or task.done():
            return   # ход ещё не ушёл модели: тело увидит `stop` само
        handled = False
        if turn.session is not None:
            try:
                handled = await turn.session.interrupt()
            except Exception as e:
                self._log(f"агент: остановка хода не удалась ({type(e).__name__}: {e})")
        if not handled and not task.done():
            task.cancel()

    async def _finish_stopped(self, turn: _Turn, reply: AgentReply) -> None:
        shown = partial_text(reply.text or "".join(turn.raw)) or turn.shown
        if turn.stop == "message":
            # Скрыта: её сменит ответ на сообщение (журнал и затравка помнят её).
            status, note = "dropped", "прервано сообщением пользователя"
            self._requeue(turn.inputs)
            self._notes.insert(0, NOTE_INTERRUPTED)
        elif turn.stop == "stop":
            status, note = "cancelled", "остановлено пользователем"
            self._requeue_after_stop(turn.inputs)
            if turn.implicit:      # остановленная реплика с `explains` сама закрывает ожидание
                turn.explained.add(turn.explains[0][0])
        else:
            status, note = "cancelled", "ход прерван"
            self._requeue(turn.inputs)
        self._emit_chat([await self._io(self._chatlog.finish_reply, turn.reply_id,
                                        status=status, text=shown, note=note)])
        if turn.stop == "stop":
            await self._close_explains(turn, EXPLAIN_STOPPED)
        self._set_state(LISTENING)

    def _requeue_after_stop(self, inputs: _Inputs) -> None:
        """«Стоп»: реплики, ответы Meet, заметки и смена частоты — снова в
        очередь; остановленное сообщение пользователя и реакции — нет."""
        self._requeue(inputs, user=False, others=False)
        self._tool_results = inputs.tools + self._tool_results
        self._notes = [NOTE_STOPPED, *inputs.notes, *self._notes]
        if inputs.frequency and self._frequency_note is None:
            self._frequency_note = inputs.frequency
        self._profile_note = self._profile_note or inputs.profile

    def _failed(self, error: str) -> None:
        self._failures += 1
        pause = BACKOFF_S[min(self._failures, len(BACKOFF_S)) - 1]
        self._retry_at = self._clock() + pause
        self._set_state(ERROR, (error or "ошибка модели")[:300])

    async def _finish_failed(self, turn: _Turn, reply: AgentReply) -> None:
        error = (reply.error or "ошибка модели")[:300]
        self._log(f"агент: модель не ответила ({error}); повтор позже")
        self._requeue(turn.inputs, replay=True)
        # Ответ пользователю — видимая ошибка; ход по репликам — молча.
        status = "failed" if turn.addressed else "dropped"
        self._emit_chat([await self._io(self._chatlog.finish_reply, turn.reply_id,
                                        status=status, text="", error=error)])
        self._failed(error)

    # --- ход-команда (слэш-команда CLI, `slash`) ---

    async def _command_turn(self, turn: _Turn) -> None:
        """Команда CLI отдельным ходом: дословно, без затравки и дельты,
        уровень USER (`via: "command"`); ответ — текстом CLI в ленту (не
        протокол JSON). Ход сжатия (`/compact`) — строкой «Контекст сжат».
        Сбой хода не повторяется (команду человек повторит сам)."""
        msg = turn.inputs.user[0]
        text = str(msg["_command"])
        name = text.split()[0] if text.split() else text
        turn.answered = True          # сбой — строкой, без повтора хода
        session = turn.session = await self._ensure_session()
        if turn.stop:
            return
        had = session.has_context
        fields = {"mode": "reply", "re": turn.re, "via": "command"}
        t = self._now_t()
        if t is not None:
            fields["t"] = t
        began = await self._io(self._chatlog.begin_reply, **fields)
        turn.reply_id = began.message["id"]
        self._emit_chat([began.event])
        self._set_state(WRITING)
        reply = await self._send(turn, session, text)
        await self._store_session(session)
        if not had:
            # Сеанс начался командой: обычный ход потом получит затравку.
            self._needs_seed = True
        if reply.cancelled or turn.stop:
            self._emit_chat([await self._io(self._chatlog.finish_reply, turn.reply_id, status="cancelled",
                                            text=reply.text or "", note="остановлено пользователем")])
            self.log_command(name.lstrip("/"), "остановлена")
            self._set_state(LISTENING)
            return
        if reply.error:
            self._emit_chat([await self._io(self._chatlog.finish_reply, turn.reply_id, status="failed",
                                            text="", error=reply.error[:300])])
            self.log_command(name.lstrip("/"), "ошибка")
            self._set_state(LISTENING)
            return
        self.turns += 1
        self._failures = 0
        compacted = getattr(reply, "compacted", None)
        body = (reply.text or "").strip()
        if compacted or not body:
            self._emit_chat([await self._io(self._chatlog.finish_reply, turn.reply_id, status="dropped",
                                            silent=True, note="ход-команда")])
            pre = (compacted or {}).get("pre_tokens")
            line = (f"Контекст сжат (было {pre} токенов)" if isinstance(pre, int) else "Контекст сжат") \
                if compacted else f"Команда {name} выполнена"
            await self.command_line(msg, None, line, command=name.lstrip("/"))
        else:
            self._emit_chat([await self._io(self._chatlog.finish_reply, turn.reply_id, status="shown",
                                            text=body, buttons=[], pin=False)])
        self.log_command(name.lstrip("/"), "выполнена")
        self._set_state(LISTENING)

    # --- слэш-команды: что нужно `slash` ---

    async def _slash(self, msg: dict, cmd) -> None:
        from meet.assist import slash

        try:
            how = await slash.run(self, msg, cmd)
        except Exception as e:      # команда не роняет ассистента
            self._log(f"агент: команда /{cmd.name} упала ({type(e).__name__})")
            with _quiet():
                await self.command_line(msg, cmd, f"/{cmd.name} не выполнена: {e}", level="error")
            return
        self.log_command(cmd.name, how)

    def log_command(self, name: str, outcome: str) -> None:
        """В журнал процесса — имя команды и исход, без аргументов."""
        self._log(f"агент: команда /{name} — {outcome}")

    async def command_line(self, msg: dict, cmd, text: str, *, level: str | None = None, **fields) -> None:
        """Ответ команды — системная строка ленты (`card: "command"`, `re` —
        сообщение пользователя: «Продолжить разговор» видит ответ)."""
        record = {"text": text, "card": "command", "re": msg.get("id"), **fields}
        if cmd is not None:
            record.setdefault("command", cmd.name)
        if level:
            record["level"] = level
        t = self._now_t()
        if t is not None:
            record["t"] = t
        added = await self._io(self._chatlog.append, "system", **record)
        self._emit_chat([added.event])

    def queue_command(self, msg: dict, cmd) -> None:
        """Команда CLI — в очередь отдельным ходом (идущий ход не прерывается)."""
        self._queue_user({**msg, "text": cmd.text, "via": "command", "_command": cmd.text})
        self._kick()

    async def conversation(self):
        """Диалог Claude Code этой сессии (поднимается, модель не зовётся)."""
        session = await self._ensure_session()
        conv = getattr(session, "conv", None)
        if conv is None:
            raise RuntimeError(f"у {self._name} нет управления сессией")
        return conv

    async def cli_commands(self) -> list[dict]:
        """Команды CLI (Claude Code, ответ `initialize`). Ещё не известны —
        процесс поднимается без хода модели (`mcp_status`)."""
        if self.provider != "claude-code":
            return []
        if not self._cli_cmds and self.freedom:
            try:
                conv = await self.conversation()
                await conv.mcp_status()
            except Exception as e:
                self._log(f"агент: команды Claude Code не получены ({type(e).__name__})")
            if self._session is not None and self._update_commands(self._session):
                self._changed()
        return list(self._cli_cmds)

    async def cli_command_names(self) -> set[str]:
        names = {c["name"] for c in await self.cli_commands()}
        conv = getattr(self._session, "conv", None)
        names.update(str(n).lstrip("/") for n in getattr(conv, "slash_commands", None) or () if n)
        return names

    def codex_mcp(self):
        """MCP-серверы Codex (`codex mcp list`). Блокирующее."""
        from meet.llm import codex

        return codex.mcp_servers(refresh=True)

    def mcp_changed(self) -> None:
        if self._session is not None:
            self._update_mcp(self._session)

    def need_seed(self) -> None:
        """Процесс перезапущен без прежнего контекста: следующий ход — с затравкой."""
        self._needs_seed = True

    async def halt_turn(self) -> None:
        """Остановить идущий ход и дождаться его конца (`/clear`, `/model`)."""
        turn = self._turn
        if turn is None or turn.task is None or turn.task.done():
            return
        await self._interrupt(turn, "stop")
        await asyncio.wait({turn.task}, timeout=TURN_TIMEOUT_S)

    async def clear_session(self) -> None:
        """`/clear`: новый разговор с агентом — сеанс провайдера забыт (id —
        в `past`), процесс закрыт; затравка следующего хода без переписки
        чата. Лента, разрешения «до конца встречи» и вложения остаются."""
        await self.halt_turn()
        session = self._session
        if session is not None:
            session.forget()
            await asyncio.to_thread(self.close)
        self._session = None
        self._session_profile = None
        if self.resumable and not self.ephemeral:
            try:
                await self._io(self._chatlog.set_session_id, self.provider, None)
            except (OSError, ValueError) as e:
                self._log(f"агент: прежний сеанс не забыт ({type(e).__name__}: {e})")
        self._stored_sid = None
        self._seed_history = False
        self._needs_seed = False
        self._notes = []
        self._people_sent, self._people_checked = {}, set()
        self.session_state = None
        self._changed()

    def model_choices(self) -> list[str]:
        return [m["value"] for m in self._models]

    async def model_text(self) -> str:
        if self.provider == "claude-code":
            await self.cli_commands()       # список моделей — из того же `initialize`
            lines = [f"Модель: {self.model_actual or self._model}",
                     f"В настройках: {self._model_configured}"]
        else:
            current = self._call_kwargs.get("model") or "как в настройках"
            lines = [f"Модель: {current}"]
        choices = self.model_choices()
        if choices:
            lines.append("Можно: " + ", ".join(choices))
        if self.provider in ("claude-code", "codex", "opencode"):
            lines.append("Сменить: /model <имя>")
        return "\n".join(lines)

    async def set_model(self, name: str) -> str:
        """`/model имя`: Claude Code — `set_model` до конца сессии (новые
        процессы — с ней же); Codex, OpenCode — на следующие вызовы."""
        if self.provider == "claude-code":
            conv = await self.conversation()
            resolved = await conv.set_model(name)
            self._model = claude_model(name)
            self.model_override = name
            self._changed()
            return str(resolved or name)
        self._call_kwargs["model"] = name
        session = self._session
        if session is not None and hasattr(session, "set_model"):
            session.set_model(name)
        self.model_override = name
        self._changed()
        return name

    # --- ответ ---

    async def _apply(self, turn: _Turn, reply: AgentReply) -> None:
        actions = pp.parse_reply(reply.text, log=self._log)
        says = [a for a in actions if a.kind == "say"]
        requests = [a for a in actions if a.kind in pp.TOOL_KINDS]
        if reply.dropped_images or reply.notes:
            await self._mark_dropped(turn, reply)
        if requests and self.tools:
            self._log("агент: запросы к Meet пропущены — у модели свои инструменты ("
                      + ", ".join(a.kind for a in requests) + ")")
            requests = []
        if not says:
            note = "запрос к Meet" if requests else (actions[0].note if actions else "")
            fields = {"status": "dropped", "silent": True}
            if note:
                fields["note"] = note
            self._emit_chat([await self._io(self._chatlog.finish_reply, turn.reply_id, **fields)])
            if not requests and turn.addressed and turn.re:
                # Пользователь спросил, а агент промолчал: без строки в ленте
                # вопрос остался бы без ответа (ревью I2, решение (a)).
                await self.note_nothing_to_add(turn.re)
                turn.silent_noted = True
        else:
            await self._show(turn, says)
        if requests:
            self._chain = (turn.addressed, turn.re, turn.explains, turn.implicit)
            await self._run_tools(requests)
        else:
            self._chain = None
            # ❓, которые ход так и не пояснил, — строкой, чтобы окно не ждало.
            await self._close_explains(turn, NOTHING_TO_ADD)

    async def _close_explains(self, turn: _Turn, text: str) -> None:
        """Строка `system` на каждый непоясненный ❓ хода: `re` — поясняемая
        реплика (после встречи — просьба `via: "reaction"`). Строка «нечего
        добавить» к той же просьбе (`turn.re`) уже есть — второй не пишем."""
        for target, request in turn.explains:
            if target in turn.explained:
                continue
            re_id = request or target
            if request is not None and request == turn.re and text == NOTHING_TO_ADD and turn.silent_noted:
                continue
            try:
                added = await self._io(self._chatlog.append, "system", text=text, re=re_id)
            except (OSError, RuntimeError) as e:
                self._log(f"агент: строка о пояснении не записана ({type(e).__name__}: {e})")
                continue
            self._emit_chat([added.event])
            turn.explained.add(target)

    async def _mark_dropped(self, turn: _Turn, reply: AgentReply) -> None:
        """Картинки, которые провайдер не отправил модели (негодный файл, не
        принята), — пометка у вложения в журнале."""
        for note in reply.notes:
            self._log(f"агент: {note}")
        ids = {}
        for msg in turn.inputs.user:
            ids.update(msg.get("_image_ids") or {})
        note = "; ".join(reply.notes)[:300] or "изображение не отправлено модели"
        for path in reply.dropped_images:
            aid = ids.get(str(path))
            if aid:
                with _quiet():
                    self._emit_chat([await self._io(self._chatlog.patch, aid,
                                                    {"delivered": False, "note": note})])

    async def _show(self, turn: _Turn, says) -> None:
        """Сообщения ответа с одной страховкой: не больше одного нового
        сообщения агента за `merge_window_s`, лишнее — абзацем к последнему.
        Ответ пользователю всегда начинается своим сообщением."""
        now = self._clock()
        target = None if turn.addressed else self._last_shown
        first = True
        tags = _explain_tags(turn, says)
        for action, tag in zip(says, tags):
            fields = action.journal_fields()
            if tag:
                fields["explains"] = tag
                turn.explained.add(tag)
            elif first and turn.implicit:
                fields["explains"] = None      # с начала хода стояло — пояснение в другом say
            # Пояснение — всегда своим сообщением: не склеивается ни с ответом, ни с другим пояснением.
            same = target is not None and (self._shown.get(target[0]) or {}).get("explains") == (tag or None)
            if target is not None and same and now - target[1] < self._merge_window:
                await self._merge(target[0], fields)
                if first:
                    self._emit_chat([await self._io(
                        self._chatlog.finish_reply, turn.reply_id, status="superseded",
                        merged_into=target[0])])
            else:
                if first:
                    ev = await self._io(self._chatlog.finish_reply, turn.reply_id,
                                        status="shown", **fields)
                    self._emit_chat([ev])
                    mid = turn.reply_id
                else:
                    added = await self._io(self._chatlog.append, "agent", status="shown",
                                           mode="reply" if turn.addressed else "proactive",
                                           **fields)
                    self._emit_chat([added.event])
                    mid = added.message["id"]
                self._shown[mid] = dict(fields)
                self._agent_texts[mid] = fields["text"]
                target = self._last_shown = (mid, now)
            first = False

    async def _merge(self, mid: str, fields: dict) -> None:
        old = self._shown.get(mid) or {"text": "", "buttons": [], "pin": False}
        text = f"{old['text']}\n\n{fields['text']}".strip()
        if len(text) > MERGED_MAX:
            text = text[:MERGED_MAX - 1].rstrip() + "…"
        buttons = list(dict.fromkeys([*old.get("buttons", []), *fields.get("buttons", [])]))[:3]
        merged = {"text": text, "buttons": buttons, "pin": bool(old.get("pin") or fields.get("pin"))}
        self._emit_chat([await self._io(self._chatlog.patch, mid, merged)])
        self._shown[mid] = merged
        self._agent_texts[mid] = text

    # --- запасной путь: Meet исполняет read / search / list ---

    async def _run_tools(self, requests) -> None:
        spent = self._tool_rounds >= TOOL_ROUNDS_MAX
        for action in requests:
            args = action.tool_args()
            req = await self._io(self._chatlog.append, "tool", event="request", call=action.kind, args=args)
            self._emit_chat([req.event])
            text, error = "", None
            if spent:
                error = TOOLS_SPENT
            else:
                try:
                    text, error = await asyncio.wait_for(
                        asyncio.to_thread(self._tool_call, action), TOOL_TIMEOUT_S)
                except asyncio.TimeoutError:
                    error = f"не успел за {TOOL_TIMEOUT_S:.0f} с"
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"
            if len(text) > TOOL_RESULT_MAX:
                text = text[:TOOL_RESULT_MAX].rstrip() + f"\n[… обрезано: {len(text)} симв.]"
            result = {"event": "result", "re": req.message["id"], "text": text, "chars": len(text)}
            if error:
                result["error"] = error
            res = await self._io(self._chatlog.append, "tool", call=action.kind, **result)
            self._emit_chat([res.event])
            if not spent:
                self._tool_results.append({"call": action.kind, "args": args, "text": text,
                                           "error": error})
        if spent:
            # Модель просит и просит: ответ — заметкой в ближайший ход, который
            # и так нужен (не сразу), и пауза — без нового ввода её не позовут.
            if TOOLS_SPENT not in self._notes:
                self._notes.append(TOOLS_SPENT)
            self._chain = None
            self._quiet_until = self._clock() + BACKOFF_S[0]
            self._log("агент: лимит запросов к Meet подряд — пауза")
        self._kick()

    def _tool_call(self, action) -> tuple[str, str | None]:
        from meet.assist.kb_prep import KnowledgeBase

        kb = self._kb or KnowledgeBase(None, library_root=self._library_root)
        if action.kind == "read":
            return _read_text(kb.kb_read(list(action.paths)))
        if action.kind == "search":
            return _search_text(kb.kb_search(action.query, action.where or None,
                                             deadline_s=TOOL_SEARCH_DEADLINE_S))
        return _list_text(kb.kb_list(action.where or None))

    # --- API окна (задача 6) ---

    async def post_user_message(self, text: str, attachments=(), client_id: str | None = None) -> dict:
        """Сообщение пользователя (и вложения) → журнал и ближайший ход, вне
        очереди: идущий ход по репликам прерывается. Вложение — id записи
        журнала (`a3`), путь к файлу или папке, `{"path"}`, `{"data": bytes,
        "name"}` (вставленная картинка). → `{"id", "queued", "attachments",
        "duplicate"?, "command"?}`.

        Слэш-команда (`slash.parse`: `/имя …` без вложений) — тоже запись
        журнала (`via: "command"`), но не ход по сообщению: команда Meet
        выполняется сразу (в фоне — ответ окну не ждёт), команда CLI встаёт в
        очередь отдельным ходом. `//текст` — обычный текст `/текст`."""
        from meet.assist import slash

        text = text if isinstance(text, str) else ""
        command = slash.parse(text, attachments)
        if client_id:
            old = await self._io(self._chatlog.by_client_id, client_id)
            if old is not None:
                queued = await self._requeue_unanswered(old)
                return {"id": old["id"], "queued": queued, "duplicate": True,
                        "attachments": list(old.get("attachments") or [])}
        described, images, ids, image_ids = [], [], [], {}
        for item in attachments or ():
            record, descriptor, image = await self._attach(item, len(images))
            if record is None:
                continue
            ids.append(record["id"])
            described.append(descriptor)
            if image:
                images.append(image)
                image_ids[str(image)] = record["id"]
        fields = {"text": text, "attachments": ids}
        if command is not None:
            fields["via"] = "command"
        t = self._now_t()
        if t is not None:
            fields["t"] = t
        added = await self._io(self._chatlog.append, "user", client_id=client_id, **fields)
        if not added.created:
            return {"id": added.message["id"], "queued": False, "duplicate": True, "attachments": ids}
        self._emit_chat([added.event])
        if command is not None:
            self._background_task(self._slash(dict(added.message), command))
            return {"id": added.message["id"], "queued": False, "attachments": ids, "command": command.name}
        msg = {**added.message, "text": slash.unescape(text), "attachments": described, "_images": images,
               "_image_ids": image_ids}
        self._queue_user(msg)
        self._interrupt_for_user()
        self._kick()
        return {"id": msg["id"], "queued": self._turn is not None, "attachments": ids}

    async def _requeue_unanswered(self, message: dict) -> bool:
        """Повтор `POST /chat` (тот же `client_id`) после перезапуска ребёнка:
        сообщение без ответа — снова в очередь (ревью I1). Уже ждёт, отвечается
        сейчас или отвечено — ничего."""
        mid = message.get("id")
        if message.get("kind") != "user" or any(m.get("id") == mid for m in self._user):
            return False
        if self._turn is not None and self._turn.re == mid:
            return False
        if answered(await self._io(self._chatlog.messages), mid):
            return False
        await self.queue_existing(mid)
        self._interrupt_for_user()
        return True

    def _queue_user(self, msg: dict) -> None:
        self._user.append(msg)
        self._user_fresh = True
        self._last_shown = None   # в ленте после него — не склеивать с прежним

    def _interrupt_for_user(self) -> None:
        """Идущий ход по репликам — прервать (в фоне: подтверждение остановки
        у Claude Code ждётся до 5 с, а окну нужен ответ сразу)."""
        turn = self._turn
        if turn is not None and not turn.addressed and not turn.stop:
            self._background_task(self._interrupt(turn, "message"))

    def _background_task(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def click(self, mid: str, label: str, client_id: str | None = None) -> dict:
        """Нажатие кнопки реплики агента = сообщение пользователя с её
        надписью. Нет такой кнопки — ValueError."""
        t = self._now_t()
        added = await self._io(self._chatlog.click_button, mid, label, client_id=client_id, t=t)
        if added.created:
            self._emit_chat([added.event])
            if mid not in self._agent_texts:     # цитата своего сообщения агенту
                msg = await self._io(self._chatlog.get, mid)
                self._agent_texts[mid] = (msg or {}).get("text") or ""
            self._queue_user(dict(added.message))
            self._interrupt_for_user()
            self._kick()
        return added.message

    async def react(self, mid: str, emoji: str, on: bool | None = None) -> list[dict]:
        """Реакция 👍 / 👎 / ❓ на реплику агента (None — переключить) →
        события журнала; агент узнает о ней в ближайшем ходе."""
        events = await self._io(self._chatlog.react, mid, emoji, on, t=self._now_t())
        if not events:
            return []
        self._emit_chat(events)
        state = next((ev["message"].get("on") for ev in events if ev.get("op") == "add"), on)
        re_text = self._agent_texts.get(mid)
        if re_text is None:
            msg = await self._io(self._chatlog.get, mid)
            re_text = (msg or {}).get("text") or ""
        self._reactions.append({"re": mid, "emoji": emoji, "on": state, "re_text": re_text})
        self._kick()
        return events

    async def attach(self, item) -> dict:
        """Вложение до сообщения (окно: вставка картинки, путь от доверенного
        вызывающего) → запись журнала (`kind: attachment`, id `a<N>`; не
        разобралось — `status: "failed"` с `error`). Сообщение потом ссылается
        на неё id (`post_user_message(attachments=["a3"])`). Id, которого нет
        в журнале, — ValueError."""
        record, _descriptor_, _image = await self._attach(item, 0)
        if record is None:
            raise ValueError(f"вложения {item} нет в журнале")
        return record

    async def remove_attachment(self, aid: str) -> dict | None:
        """Вложение убрали из строки ввода до отправки («×» в окне): запись
        журнала — `status: "removed"` (её нет ни в ленте, ни в затравке), файл
        картинки или материал с текстом — с диска, если на них не ссылается
        другое вложение. Уже отправленное (на него ссылается сообщение) или
        не вложение — ValueError. → событие журнала (None — уже убрано)."""
        got = await self._io(remove_attachment, self._chatlog, self._folder, aid, log=self._log)
        if got is None:
            return None
        event, record = got
        if record.get("status") == "ready":
            if record.get("type") == "image":
                self._images = max(0, self._images - 1)
            elif record.get("type") == "doc":
                self._materials = max(0, self._materials - 1)
        if event is not None:
            self._emit_chat([event])
        self._changed()
        return event

    async def queue_existing(self, mid: str) -> dict:
        """Сообщение пользователя, уже записанное в журнал другим писателем
        (резидент после встречи: «Продолжить разговор»), — в ближайший ход,
        как `post_user_message`, но без новой записи. Нет такого сообщения
        пользователя — ValueError."""
        from meet.assist import slash

        message = await self._io(self._chatlog.get, mid)
        if message is None or message.get("kind") != "user":
            raise ValueError(f"{mid}: нет такого сообщения пользователя")
        command = None
        if message.get("via") in (None, "command"):
            command = slash.parse(message.get("text"), message.get("attachments"))
        if command is not None:
            # «Продолжить разговор» слэш-командой (0.4): та же команда, что в живой панели.
            if message.get("via") != "command":
                with _quiet():
                    ev = await self._io(self._chatlog.patch, mid, {"via": "command"})
                    if ev:
                        self._emit_chat([ev])
                message = {**message, "via": "command"}
            await self._slash(message, command)
            return message
        if isinstance(message.get("text"), str):
            message = {**message, "text": slash.unescape(message["text"])}
        described, images, image_ids = [], [], {}
        for aid in message.get("attachments") or ():
            if not isinstance(aid, str):
                continue
            record, descriptor, image = await self._attach(aid, len(images))
            if record is None:
                continue
            described.append(descriptor)
            if image and len(images) < _max_images():
                images.append(image)
                image_ids[str(image)] = record["id"]
        re_id = message.get("re")
        if message.get("via") in ("button", "reaction") and isinstance(re_id, str) and re_id not in self._agent_texts:
            agent = await self._io(self._chatlog.get, re_id)
            self._agent_texts[re_id] = (agent or {}).get("text") or ""
        if message.get("via") == "reaction" and isinstance(re_id, str):
            # ❓ после встречи (ревью after-chat, I2): просьба пояснить своё сообщение.
            quote = " ".join((self._agent_texts.get(re_id) or "").split())[:400]
            message = {**message, "text": EXPLAIN_REQUEST + (f"\nТвоё сообщение: «{quote}»" if quote else "")}
        self._queue_user({**message, "attachments": described, "_images": images,
                          "_image_ids": image_ids})
        self._kick()
        return message

    async def note_nothing_to_add(self, mid: str) -> None:
        """Строка журнала `system` «Ассистенту нечего добавить» к сообщению
        пользователя `mid` (видна в ленте и в `assistant_chat.md`)."""
        try:
            added = await self._io(self._chatlog.append, "system", text=NOTHING_TO_ADD, re=mid)
        except (OSError, RuntimeError) as e:
            self._log(f"агент: строка «нечего добавить» не записана ({type(e).__name__}: {e})")
            return
        self._emit_chat([added.event])

    def add_note(self, text: str) -> None:
        """Заметка Meet агенту — в ближайший ход (сама ход не вызывает)."""
        if text and text not in self._notes:
            self._notes.append(text)

    async def stop_reply(self, mid: str | None = None) -> bool:
        """«Стоп» у ответа, который пишется. False — такого хода нет."""
        turn = self._turn
        if turn is None or (mid is not None and mid != turn.reply_id):
            return False
        self._background_task(self._interrupt(turn, "stop"))
        return True

    # --- вложения ---

    async def _attach(self, item, images: int) -> tuple[dict | None, dict, str | None]:
        """Вложение → (запись журнала, описание для дельты, путь картинки).
        `images` — сколько картинок в сообщении уже есть."""
        from meet.assist import attachments as att

        if isinstance(item, str) and _ATTACHMENT_ID.match(item):
            record = await self._io(self._chatlog.get, item)
            if record is not None and record.get("status") == "removed":
                self._log(f"агент: вложение {item} убрано до отправки")
                return None, {}, None
            if record is None or record.get("kind") != "attachment":
                self._log(f"агент: вложения {item} нет в журнале")
                return None, {}, None
            image = record.get("path") if record.get("type") == "image" and record.get("status") != "failed" else None
            return record, _descriptor(record, self.vision), image
        fields: dict
        image = None
        try:
            if _looks_image(item) and images >= att.MAX_PER_MESSAGE:
                raise att.AttachmentError(f"В сообщении — не больше {att.MAX_PER_MESSAGE} изображений")
            fields, image = await self._parse(parse_attachment, self._folder, item, vision=self.vision)
        except (ValueError, OSError, TypeError) as e:
            fields, image = failed_attachment(item, e), None
        added = await self._io(self._chatlog.append, "attachment", **fields)
        self._emit_chat([added.event])
        if self._gate is not None and fields.get("status") == "ready":
            self._gate.allow_paths([fields[k] for k in ("path", "source") if isinstance(fields.get(k), str)])
        if image:
            self._images += 1
        elif fields.get("type") == "doc" and fields.get("status") == "ready":
            self._materials += 1
        self._changed()
        return added.message, _descriptor(added.message, self.vision), image

    async def _parse(self, fn, *args, **kwargs):
        """Разбор вложения в потоке. Отменили (срок `/chat/attach`, стоп) —
        поток дорабатывает сам, а то, что он положил в папку встречи, потом
        убирается: материал без записи журнала иначе съедал бы квоту и попадал
        в затравку (ревью M3)."""
        future = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            future.add_done_callback(self._drop_orphan)
            raise

    def _drop_orphan(self, future) -> None:
        if future.cancelled() or future.exception() is not None:
            return
        result = future.result()
        drop_parsed(self._folder, result[0] if isinstance(result, tuple) else result, log=self._log)


# --- вложения: общее у живого агента и чата после встречи ---
#
# Вложение после встречи («Продолжить разговор») пишет в журнал резидент,
# а не агент, — теми же функциями: разбор, пределы и квоты те же
# (`attachments.save`, `materials.add`), что и во время встречи.

def parse_attachment(folder, item, *, vision: bool) -> tuple[dict, str | None]:
    """Новое вложение → (поля записи журнала `attachment`, путь картинки или
    None). `item` — картинка байтами (`{"data", "name"}`) или путь к файлу
    или папке. Не разобралось — исключение (ValueError, OSError, TypeError):
    из него `failed_attachment` делает запись «не разобрано». Блокирующее."""
    from meet.assist import attachments as att

    folder = Path(folder)
    if isinstance(item, dict) and isinstance(item.get("data"), (bytes, bytearray)):
        saved = att.save(folder, bytes(item["data"]), name=item.get("name"))
        return image_fields(saved, vision), saved["path"]
    path = Path(item.get("path") if isinstance(item, dict) else item)
    if path.suffix.lower() in IMAGE_SUFFIXES and not path.is_dir():
        saved = att.save_file(folder, path)
        return image_fields(saved, vision), saved["path"]
    return document_fields(folder, path), None


def failed_attachment(item, error) -> dict:
    """Поля записи журнала о вложении, которое не разобралось."""
    text = str(error)
    return {"type": "image" if _looks_image(item) else "doc", "name": _item_name(item),
            "status": "failed", "error": text[:300], "note": f"не разобрано: {text[:200]}"}


def image_fields(saved: dict, vision: bool) -> dict:
    fields = {"type": "image", "name": saved.get("name") or saved["id"], "status": "ready",
              "path": saved["path"], "ref": saved["id"], "vision": vision}
    if not vision:
        from meet.llm.base import NO_VISION_NOTE

        fields["note"] = NO_VISION_NOTE
    return fields


def document_fields(folder, path: Path) -> dict:
    """Документ или папка → `materials.add`, текст — рядом (агент с
    инструментами читает его из папки встречи), сводка — в ограду. `source` —
    исходный файл: чип-источник в окне открывает его, если оболочка пустит
    (`open_material`: база знаний, библиотека встреч), иначе текст рядом."""
    from meet import materials

    folder = Path(folder)
    desc = materials.add(folder, path)
    aid = desc["id"]
    record = materials.read(folder, aid) or {}
    summary = desc.get("summary") or materials.outline(SimpleNamespace(chunks=record.get("chunks") or []))
    dump = _text_dump_path(folder, aid)
    try:
        _write_text_dump(dump, desc.get("title") or path.name, record.get("chunks") or [])
        text_path = str(dump)
    except OSError:
        text_path = desc.get("path") or str(path)
    fields = {"type": "doc", "name": desc.get("title") or path.name, "status": "ready",
              "path": text_path, "ref": aid, "summary": summary or "",
              "chars": desc.get("chars", 0)}
    source = desc.get("path") or str(path)
    if isinstance(source, str) and source and Path(source).is_file():
        fields["source"] = source
    if desc.get("warnings"):
        fields["note"] = "; ".join(desc["warnings"])[:200]
    if desc.get("duplicate"):
        fields["duplicate"] = True
    return fields


def drop_parsed(folder, fields, *, log=print) -> None:
    """Разобранное вложение без записи журнала (отменили, вышел срок) —
    убрать то, что разбор положил в папку встречи: материал без записи иначе
    съедал бы квоту и попадал в затравку (ревью chat-api M3)."""
    from meet import materials

    if not isinstance(fields, dict) or fields.get("duplicate"):
        return
    folder = Path(folder)
    paths = []
    if fields.get("type") == "doc" and isinstance(fields.get("ref"), str):
        paths.append(materials.materials_dir(folder) / f"{fields['ref']}.json")
        paths.append(_text_dump_path(folder, fields["ref"]))
    elif fields.get("path"):
        paths.append(Path(fields["path"]))   # картинка: assistant/files/<id>.<ext>
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            log(f"агент: брошенное вложение не убрано ({path.name}: {e})")


def remove_attachment(chatlog, folder, aid: str, *, log=print) -> tuple[dict | None, dict] | None:
    """Вложение убрали до отправки: запись журнала — `status: "removed"`,
    файлы (картинка; материал с текстом) — с диска, если на них не ссылается
    другое живое вложение, и только внутри папки встречи. Уже отправленное
    или не вложение — ValueError. → (событие журнала, запись до правки);
    None — уже убрано. Блокирующее."""
    from meet.assist.chatlog import REMOVED

    if not isinstance(aid, str) or not _ATTACHMENT_ID.match(aid):
        raise ValueError("неизвестное вложение")
    messages = chatlog.messages()
    record = next((m for m in messages if m.get("id") == aid), None)
    if record is None or record.get("kind") != "attachment":
        raise ValueError(f"вложения {aid} нет в журнале")
    if record.get("status") == REMOVED:
        return None
    if any(m.get("kind") == "user" and aid in (m.get("attachments") or []) for m in messages):
        raise ValueError("вложение уже отправлено — убрать его нельзя")
    event = chatlog.patch(aid, {"status": REMOVED})
    others = [m for m in messages if m.get("kind") == "attachment" and m.get("id") != aid
              and m.get("status") != REMOVED]
    ref = record.get("ref")
    if isinstance(ref, str) and not any(m.get("ref") == ref for m in others):
        _drop_files(Path(folder), record, log)
    return event, record


def _drop_files(folder: Path, record: dict, log) -> None:
    """Файлы убранного вложения: картинка (`assistant/files/…`) или
    материал (`materials/<ref>.json` и текст рядом). Только внутри папки встречи."""
    from meet import materials

    ref = str(record.get("ref") or "")
    paths = []
    if record.get("type") == "doc" and ref:
        paths += [materials.materials_dir(folder) / f"{ref}.json", _text_dump_path(folder, ref)]
    elif record.get("type") == "image" and record.get("path"):
        paths.append(Path(record["path"]))
    root = folder.resolve()
    for path in paths:
        try:
            if not path.resolve().is_relative_to(root):
                continue
            path.unlink(missing_ok=True)
        except OSError as e:
            log(f"агент: файл убранного вложения не удалён ({path.name}: {e})")


# --- сборка по настройкам ---

def session_profile(chatlog, default, profile=None) -> str:
    """Профиль сессии: выбранный при старте (`profile`), иначе записанный в
    журнале встречи (повторное включение, «Продолжить разговор» после
    встречи); чат есть, а профиля нет (встреча до 0.3.7) — «Рабочая
    встреча», как тогда (ревью M2); иначе настройка по умолчанию
    (`assist.profile`)."""
    if profile is not None:
        return pp.normalize_profile(profile)
    stored = None
    if chatlog is not None:
        try:
            stored = chatlog.profile()
        except (OSError, AttributeError):
            stored = None
        if stored is None and _agent_spoke(chatlog):
            return pp.WORK
    return pp.normalize_profile(stored or default)


def _agent_spoke(chatlog) -> bool:
    """В журнале уже есть ответы агента: сессия была. Только что записанное
    сообщение «Продолжить разговор» к новой встрече сессией не считается."""
    path = getattr(chatlog, "path", None)
    try:
        if path is None or not Path(path).is_file():
            return False
        return any(m.get("kind") == "agent" for m in chatlog.messages())
    except Exception:
        return False


def from_settings(cfg, bus, folder, provider: str, runner, *, knowledge_dir=None,
                  glossary: str = "", on_fresh_audio=None, log=print, chatlog=None,
                  profile=None, mode_notice: bool = False, **kwargs) -> "Participant":
    """Агент-участник записи по настройкам (`assist.*`, `recording.*`,
    `llm.*`): журнал папки записи, база знаний на сессию (карта, исключения,
    запасные запросы), библиотека — папка, где лежит запись. Так его собирают
    и живой ассистент, и задача «Продолжить разговор» после встречи.
    `profile` — профиль, выбранный при старте; нет — из журнала встречи или
    `assist.profile` (`session_profile`). Роли участников — из базы голосов
    (`recording.voices`, `people.roles`). `mode_notice` — живой ассистент:
    однократная строка об автомоде, пока `assist.agent_mode_noticed` не
    отмечен (первая сессия после обновления до 0.4)."""
    from meet import people
    from meet import llm
    from meet.assist.chatlog import ChatLog
    from meet.assist.kb_prep import KnowledgeBase

    folder = Path(folder)
    knowledge = knowledge_dir or cfg.assistant.knowledge_dir
    knowledge = knowledge if knowledge and Path(knowledge).is_dir() else None
    library_root = folder.parent
    if "ephemeral" not in kwargs:
        from meet import temp_meeting

        kwargs["ephemeral"] = temp_meeting.is_temporary(folder)
    kb = KnowledgeBase(knowledge, exclude=cfg.assist.kb_exclude, library_root=library_root,
                       show_map=cfg.assist.kb_map)
    chatlog = chatlog if chatlog is not None else ChatLog(folder, log=log)
    voices = cfg.recording.voices
    kwargs.setdefault("roles", lambda names: people.roles(voices, names))
    if mode_notice and not mode_noticed(cfg):
        kwargs.setdefault("mode_notice", True)
        kwargs.setdefault("on_mode_notice", _mark_mode_noticed)
    return Participant(
        bus, chatlog, provider=provider,
        folder=folder, runner=runner, kb=kb, library_root=library_root,
        owner_name=cfg.recording.speaker_name, owner_speaker=cfg.recording.speaker_name,
        owner_names=[cfg.recording.speaker_name, *cfg.recording.former_speaker_names],
        frequency=cfg.assist.frequency, model=llm.agent_model(provider, cfg),
        proxy=cfg.llm.proxy, glossary=glossary, on_fresh_audio=on_fresh_audio, log=log,
        profile=session_profile(chatlog, cfg.assist.profile, profile),
        freedom=getattr(cfg.assist, "agent_freedom", False),
        agent_mode=getattr(cfg.assist, "agent_mode", consent.MODE_AUTO), **kwargs)


# Отметка «строку об автомоде показали» — файл в папке данных, не config.json:
# её ставит ребёнок `meet assist`, а config.json пишет резидент (замок
# `settings._FILE_LOCK` — внутри одного процесса), и запись ребёнка могла бы
# потерять одновременную правку настроек из окна.
MODE_NOTICED_MARK = "agent-mode-noticed.flag"


def _mode_noticed_path() -> Path:
    from meet import paths

    return paths.data_dir() / MODE_NOTICED_MARK


def mode_noticed(cfg) -> bool:
    """Строку об автомоде уже показывали: отметка в папке данных или прежний
    флаг `assist.agent_mode_noticed` (его ставили сборки до отметки)."""
    if getattr(cfg.assist, "agent_mode_noticed", False):
        return True
    try:
        return _mode_noticed_path().is_file()
    except OSError:
        return False


def _mark_mode_noticed() -> None:
    """Строку об автомоде показали — больше не показывать. Создание пустого
    файла атомарно и не зависит от того, кто ещё пишет настройки."""
    mark = _mode_noticed_path()
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.touch(exist_ok=True)


# --- помощники ---

def _explain_targets(inputs: _Inputs) -> list[tuple[str, str | None]]:
    """Какие реплики агента просят пояснить в этом ходе, по порядку: ❓,
    поставленные и не снятые следом (снятая уходит, остальные остаются), и
    просьбы после встречи (`via: "reaction"`) — `(реплика, id просьбы)`."""
    out: list[tuple[str, str | None]] = []
    for r in inputs.reactions:
        mid = r.get("re")
        if r.get("emoji") != "❓" or not isinstance(mid, str):
            continue
        out = [x for x in out if x[0] != mid]
        if r.get("on") is not False:
            out.append((mid, None))
    for m in inputs.user:
        mid = m.get("re")
        if m.get("via") == "reaction" and isinstance(mid, str):
            out = [x for x in out if x[0] != mid]
            out.append((mid, m.get("id") if isinstance(m.get("id"), str) else None))
    return out


def _implicit_explain(inputs: _Inputs, targets: list) -> bool:
    """Один ❓ и больше ничего от пользователя (ни сообщения, ни кнопки):
    пояснение — весь ответ, метка `explains` не нужна."""
    return len(targets) == 1 and not any(m.get("via") != "reaction" for m in inputs.user)


def _explain_tags(turn: _Turn, says) -> list[str | None]:
    """Какой `say` что поясняет: `"explains"` агента, если это ❓ этого хода
    (каждый ❓ — один раз); без меток при одном ❓ и ничего больше — первый
    `say`. Ответ на сообщение пользователя без метки пояснением не считается."""
    targets = [t for t, _r in turn.explains]
    tags: list[str | None] = []
    used: set[str] = set()
    for action in says:
        want = getattr(action, "explains", "") or ""
        if want in targets and want not in used:
            used.add(want)
            tags.append(want)
        else:
            tags.append(None)
    if not used and turn.implicit and tags:
        tags[0] = targets[0]
    return tags


def answered(messages, mid: str) -> bool:
    """Есть ли ответ на сообщение пользователя `mid`: реплика агента `re`
    (показана или пишется) или строка «нечего добавить». Остановленный,
    упавший или скрытый ответ — не ответ."""
    for m in messages or ():
        if m.get("re") != mid:
            continue
        if m.get("kind") == "agent" and m.get("status") in ("shown", "writing"):
            return True
        if m.get("kind") == "system":
            return True
    return False


class _JournalUntil:
    """Журнал для затравки до сообщения `until` включительно."""

    def __init__(self, chatlog, until: str) -> None:
        self._chatlog = chatlog
        self._until = until

    def context(self, budget: int, **kwargs) -> str:
        return self._chatlog.context(budget, until=self._until, **kwargs)


def _map_has_kb(text: str) -> bool:
    from meet.assist.kb_prep import MAP_KB_HEAD

    return bool(text) and MAP_KB_HEAD in text


def _max_images() -> int:
    from meet.assist import attachments as att

    return att.MAX_PER_MESSAGE


def _accepts(fn, name: str) -> bool:
    """Принимает ли вызов именованный аргумент `name` (поддельные диалоги
    тестов и диалоги старого вида — нет)."""
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class _quiet:
    """Уборка хода не роняет цикл (журнал мог не записаться)."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, Exception)


def _chronological(texts, entries):
    from meet.assist.bus import chronological

    return chronological(texts, entries)


def _descriptor(record: dict, vision: bool) -> dict:
    """Запись журнала о вложении → описание для дельты (`participant_prompts`)."""
    out = {"id": record.get("id"), "name": record.get("name") or "", "type": record.get("type")}
    if record.get("path") and record.get("status") != "failed":
        out["path"] = record["path"]
    if record.get("summary"):
        out["summary"] = record["summary"]
    note = record.get("note")
    if record.get("type") == "image" and not vision and not note:
        from meet.llm.base import NO_VISION_NOTE

        note = NO_VISION_NOTE
    if note:
        out["note"] = note
    return out


def _item_name(item) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or Path(str(item.get("path") or "файл")).name)
    return Path(str(item)).name


def _looks_image(item) -> bool:
    if isinstance(item, dict) and "data" in item:
        return True
    return Path(_item_name(item)).suffix.lower() in IMAGE_SUFFIXES


def _text_dump_path(folder: Path, aid: str) -> Path:
    from meet import materials

    return materials.materials_dir(folder) / f"{aid}.txt"


def _write_text_dump(path: Path, title: str, chunks) -> None:
    parts = [f"# {title}"]
    for c in chunks:
        loc = c.get("loc") if isinstance(c, dict) else None
        text = c.get("text") if isinstance(c, dict) else ""
        parts.append(f"[{loc}]\n{text}" if loc else str(text or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_text(items) -> tuple[str, str | None]:
    parts, errors = [], []
    for item in items or ():
        if item.get("error"):
            errors.append(f"{item.get('path')}: {item['error']}")
            parts.append(f"### {item.get('path')}\n(не прочитан: {item['error']})")
            continue
        warn = f"\n(пометки: {'; '.join(item['warnings'])})" if item.get("warnings") else ""
        parts.append(f"### {item.get('path')}\n{item.get('text') or ''}{warn}")
    if errors and len(errors) == len(parts):
        return "", "; ".join(errors)[:500]
    return "\n\n".join(parts), None


def _search_text(res: dict) -> tuple[str, str | None]:
    if res.get("error"):
        return "", res["error"]
    hits = res.get("hits") or []
    if not hits:
        lines = [f"Ничего не найдено (просмотрено файлов: {res.get('files', 0)})."]
    else:
        lines = [f"- {h.get('path')}{' [' + h['loc'] + ']' if h.get('loc') else ''}: {h.get('snippet')}"
                 for h in hits]
    if res.get("more"):
        lines.append(f"… и ещё совпадений: {res['more']}")
    if res.get("timed_out"):
        lines.append("(время поиска вышло — показано найденное к этому моменту)")
    return "\n".join(lines), None


def _list_text(res: dict) -> tuple[str, str | None]:
    if res.get("error"):
        return "", res["error"]
    lines = []
    if "meetings" in res:
        for m in res["meetings"]:
            group = f" · группа «{m['group']}»" if m.get("group") else ""
            lines.append(f"- {m.get('path')} · {m.get('date') or '—'} · {m.get('title')}{group}")
    else:
        if res.get("title"):
            lines.append(f"Встреча «{res['title']}» {res.get('date') or ''}".rstrip())
        for f in res.get("folders") or []:
            lines.append(f"- {f['name']}/ — документов: {f.get('docs', 0)}")
        for name in res.get("files") or []:
            lines.append(f"- {name}")
    if not lines:
        lines.append("(пусто)")
    if res.get("more"):
        lines.append(f"… и ещё: {res['more']}")
    return "\n".join(lines), None
