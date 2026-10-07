"""Журнал сессии ассистента: `<папка записи>/assistant/chat.jsonl` (V4, §2).

Одна сессия на встречу: старт, отключение, повторное подключение, перезапуск
ребёнка и «Продолжить разговор» после встречи пишут в один журнал. Журнал —
запись чата для окна (лента во время встречи, вкладка «Ассистент» после неё,
`assistant_chat.md` для вкладки «Агент»).

**Контекст модели и журнал.** Контекст агента восстанавливается родным
продолжением сеанса провайдера (Claude Code `--resume <id>`, Codex
`exec resume <id>`, OpenCode `--session <id>` — задача 2). Id сеансов
хранятся здесь же, в `assistant/sessions.json`: по голове и провайдеру.
Агент один на встречу (`v4-simple.md` §1), голова по умолчанию —
`DEFAULT_HEAD = "agent"`; ключ головы оставлен гибким (`head=`). Со
временем последнего использования и любыми полями вызывающего (`cwd`,
`model`…): `set_session_id(provider, id, *, head="agent", **meta)`,
`session_id(provider)` / `session(provider)` / `sessions()`, `None` —
забыть. Файл читается и
пишется под тем же замком, что и журнал, атомарно (временный + замена);
неизвестные поля сохраняются. Нет файла или он битый — «сеанса нет» (битый
следующая запись заменяет); ошибка чтения (файл занят) — повтор, затем
OSError: файл после неудачного чтения не перезаписывается никогда. Затравка
из журнала (`context(budget)`) — **только запасной путь**: локальная модель,
сеанс истёк или пропал, у провайдера нет продолжения.

Строка журнала — один JSON-объект:

    {"v":1,"seq":57,"at":1760000000.12,"rec":"msg","id":"m31","kind":"agent",…поля}
    {"v":1,"seq":58,"at":…,"rec":"patch","id":"m31","set":{"voiced":{…}}}

- `msg` — новое сообщение (`KINDS`), `patch` — правка его полей (`set`
  сливается поверх сообщения: ключ за ключом, `null` — тоже значение).
- `seq` растёт монотонно на весь журнал. Id: `a<N>` у вложений
  (`kind: attachment`), `m<N>` у остальных — счётчики свои.
- `at` — стенное время записи, `t` (секунды записи) передаёт вызывающий.

**Ответ, который пишется** (решение для задачи 4): id у ответа есть с начала
хода — `begin_reply(...)` дописывает реплику агента со `status: "writing"`
(её id нужен `chat_partial` и `/chat/stop`), `finish_reply(id, status=…)`
правит её по концу хода: `shown`, `cancelled` (стоп, видимый текст
остаётся), `failed` (с `error`). Реплику `writing`, оставшуюся от убитого
процесса, писатель при старте закрывает `close_interrupted()` (→
`cancelled`). Читатели видят `writing` как есть (окно — пузырь «Пишет…»);
в `assistant_chat.md` и в затравку она не попадает, пока не закончена.

**Реакции человека** на реплики агента — `REACTIONS`: 👍 «норм», 👎 «не
норм», ❓ «вопрос». `react(id, emoji, on=None)` (None — переключить) пишет
`patch` реплики `reactions: {эмодзи: at}` (снятая уходит из словаря) и
событие встречи `meeting` `event: "reaction"`, `re`, `text`=эмодзи, `on` —
агент видит отклики по порядку. Повтор того же состояния ничего не пишет.
Затравка и `assistant_chat.md` показывают реакции у реплики; события
реакций в затравке — короткой строкой на своём месте (не в счёт `recent`),
в `assistant_chat.md` — только отметкой у реплики.

**Кнопки и закрепление** (`v4-simple.md` §2): у реплики агента
`buttons: [str]` (0–3, придумывает агент; лишние отбрасываются, пробелы
схлопываются, надпись — до `BUTTON_MAX_CHARS`) и `pin: bool`. Текст
реплики (`say` протокола агента) лежит в `text`. Нажатие —
`click_button(id, надпись)`: сообщение человека `kind: user`, `text` =
надпись, `via: "button"`, `re` = id реплики.

**Запросы агента и ответы Meet** (`read` / `search` / `list`) — вид `tool`:
`event: "request"` (`call`, `args`) и `event: "result"` (`re` = id запроса,
`text`, `chars`, `error`); `call` — из `TOOL_CALLS`. Текст ответа в журнале —
выдержка до `TOOL_TEXT_MAX` с пометкой обрезки (`chars` — полный размер). В
`assistant_chat.md` их нет; в затравке — по одной короткой строке на своём
месте, не в счёт `recent`; в ленте окна их нет (`visible_in_feed`, вместе с
событиями реакций и скрытыми репликами; `snapshot(feed=True)`).

Запись:
- межпроцессный замок `library.file_lock(strict=True)` с ключом
  `assistant/.chat.lock`. Сам файл замка по правилу `library` лежит во
  временной папке, а не в папке записи (не мешает удалению, не виден
  агенту). Под замком журнал сначала дочитывается (второй писатель мог
  дописать), потом пишется строка и читается обратно (проверка `seq`), —
  поэтому писателей может быть два: ребёнок и задача резидента;
- замок не взят за `CHAT_LOCK_WAIT_S` — **запись не идёт**:
  `library.FileLockTimeout` (OSError). Вызывающий отвечает 503 / повторяет
  (`client_id` делает повтор безопасным). Чтение, не дождавшись замка за
  `READ_LOCK_WAIT_S`, читает без него (оно ничего не пишет);
- строка уходит одним `write` с `\\n`, затем `flush`; у `msg` ещё
  `os.fsync`, у `patch` — нет (их потеря некритична);
- если журнал кончается строкой без `\\n` (процесс убили посреди записи
  или прямо перед `\\n`), перед новой строкой дописывается `\\n`: обрывок
  становится отдельной (битой) строкой, новая запись к нему не
  приклеивается. Строка без `\\n`, которая разбирается целиком, — записанная
  (её `seq` и id учитываются). Файл не переписывается никогда;
- одиночные суррогаты в строках (обрывок из буфера обмена) заменяются на
  U+FFFD при записи (и при чтении чужих строк): журнал, затравка и
  `assistant_chat.md` всегда кодируются в UTF-8.

Чтение (`load`, и дочитывание перед каждой операцией) не падает: битая
строка (не JSON, не объект, без `rec`/`id`/целого `seq`) пропускается,
считается (`stats()`) и пишется в журнал процесса — один раз на строку в
процессе, после снятия замка; оборванная последняя строка — так же; `patch`
к неизвестному id пропускается; неизвестный `rec` и `v` > 1 читаются по
известным полям.

Идемпотентность: `append(..., client_id=…)` с уже записанным `client_id`
ничего не пишет и возвращает прежнее сообщение (`Appended.created` = False):
повтор `POST /chat` при переподключении не дублирует сообщение.

**Все методы блокирующие** (замок, `fsync`, ожидание замка до
`CHAT_LOCK_WAIT_S`): из asyncio — через `asyncio.to_thread` или одного
исполнителя-поток (он же держит порядок записей).

Решения там, где дизайн молчит (см. отчёт задачи):
- поля вложения: `type` (`image|doc|kb_note|past_meeting`), `name`,
  `status` (`parsing|ready|failed`), прочее (`vision`, `chars`, `summary`) —
  как есть;
- события встречи: `kind: meeting`, `event` (`voiced|session`), `text`,
  у `voiced` ещё `re` (id реплики агента);
- статусы реплики агента — `STATUSES`; значения не проверяются;
- `patch` без изменений (те же значения) не пишется — `None`; `patch` к
  неизвестному id тоже `None`;
- в `assistant_chat.md` не попадают реплики агента `held` / `dropped` /
  `superseded` (их не было видно в чате) и `writing`; в затравке `context()`
  скрытые есть с пометкой: агент должен знать, что хотел сказать и почему
  это не ушло. `meeting voiced` нет ни там, ни там: «озвучено» — отметка у
  самой реплики (её `patch voiced`);
- `context()` отдаёт только часть «журнал» затравки (§3.5); шапку, материалы,
  память встречи и реплики собирает `prompts.build_participant_seed`.
  Строки `system` — одной короткой строкой и не в счёт последних 40,
  `system` с `error` — не попадают вовсе;
- `context(budget)` — бюджет в символах (~3 символа на токен);
  результат никогда не длиннее бюджета.

Модуль — только stdlib и `meet.library`: его читает и задача резидента.
"""

import json
import os
import re
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from meet import library
from meet.library import FileLockTimeout

__all__ = ["ChatLog", "Appended", "ChatLogError", "FileLockTimeout", "chat_dir", "has_chat",
           "visible_in_feed"]

CHAT_DIR = "assistant"
CHAT_JSONL = "chat.jsonl"
SESSIONS_JSON = "sessions.json"
LOCK_NAME = ".chat.lock"
ASSISTANT_CHAT_MD = "assistant_chat.md"
VERSION = 1

KINDS = ("agent", "user", "attachment", "meeting", "system", "tool")
# Один агент на встречу (v4-simple §1): голова по умолчанию — "agent".
# Ключ головы оставлен гибким (любая непустая строка).
DEFAULT_HEAD = "agent"
HEADS = (DEFAULT_HEAD,)
# Кнопки реплики агента (v4-simple §2): 0–3, придумывает агент.
BUTTONS_MAX = 3
BUTTON_MAX_CHARS = 60
# Запросы агента и ответы Meet (`kind: tool`): `event` — request | result.
TOOL_EVENTS = ("request", "result")
TOOL_CALLS = ("read", "search", "list")
# Ответ Meet в журнале — выдержка: полный результат уходит модели сразу,
# затравке хватает строки, окну он не нужен.
TOOL_TEXT_MAX = 4000
WRITING = "writing"
# Статусы реплики агента: `writing` — ответ пишется (§3.4, id нужен с начала
# хода); `failed` — ход упал (с `error`); остальные — §2.3 и §3.4.
STATUSES = (WRITING, "shown", "held", "dropped", "superseded", "dismissed", "cancelled", "failed")
# Реплики агента, которых в чате не было видно.
HIDDEN_STATUSES = ("held", "dropped", "superseded")
# Реакции человека на реплики агента (`react`): эмодзи → подпись.
REACTIONS = {"👍": "норм", "👎": "не норм", "❓": "вопрос"}
# Поля записи, которые ставит журнал: ни в `append(**fields)`, ни в `patch`.
RECORD_KEYS = ("v", "seq", "at", "rec")
PROTECTED = (*RECORD_KEYS, "id", "kind", "client_id")
SESSION_KEYS = ("id", "used_at")

# Запись ждёт замок столько (fsync на медленном диске, антивирус), потом
# FileLockTimeout; чтение — столько, потом читает без замка.
CHAT_LOCK_WAIT_S = 20.0
READ_LOCK_WAIT_S = 2.0
SESSIONS_READ_TRIES = 3
SESSIONS_READ_PAUSE_S = 0.05

RECENT_MESSAGES = 40          # затравка: столько последних — дословно
COMPRESSED_LINE_MAX = 160     # сжатая строка раньшего сообщения (и строки system)
VERBATIM_SHARE = 3            # одно дословное сообщение — не больше budget // 3
VERBATIM_MIN = 200
COUNTS_RESERVE = 240          # место под строку счётчиков раньшего

_ID = re.compile(r"^([a-z]+)(\d+)$")
_SURROGATE_ESCAPE = re.compile(rb"\\u[dD][89a-fA-F]")
_MODE = {"proactive": "сам", "reply": "ответ", "followup": "досказ", "ask_you": "«Вам вопрос»"}
_QUICK = {"more": "подробнее", "not_now": "не сейчас", "missed": "что я пропустил",
          "brief": "кратко", "reply": "что мне ответить"}
_FEEDBACK = {"more": "подробнее", "not_now": "не сейчас", "copied": "скопировал"}
_TYPE = {"image": "изображение", "doc": "документ", "kb_note": "заметка базы знаний",
         "past_meeting": "прошлая встреча"}
_STATUS = {"parsing": "разбирается", "ready": "готово", "failed": "не разобрано"}

# Что уже сказано в журнал процесса о битых строках: (файл, inode, байт) —
# свежий ChatLog на каждый GET резидента не повторяет те же предупреждения.
_REPORTED: set[tuple] = set()
_REPORTED_GUARD = threading.Lock()
_REPORTED_MAX = 10_000


class ChatLogError(OSError):
    """Запись не подтвердилась при чтении обратно — сообщение не возвращается
    как записанное."""


class Appended(NamedTuple):
    """Итог `append`: сообщение (свёрнутое, копия) и событие для SSE `chat`
    (`{"seq","op":"add","message"}`, своя копия), `None` — если `client_id`
    уже был."""
    message: dict
    event: dict | None

    @property
    def created(self) -> bool:
        return self.event is not None


def chat_dir(folder: Path) -> Path:
    return Path(folder) / CHAT_DIR


def has_chat(folder: Path) -> bool:
    """Есть ли журнал (новая встреча) — без создания файлов (§8)."""
    return (chat_dir(folder) / CHAT_JSONL).is_file()


def _print(text: str) -> None:
    print(text, flush=True)


def _clean(value):
    """Строки без одиночных суррогатов (→ U+FFFD; пара суррогатов — символ)."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:
            return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, dict):
        return {_clean(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def _unfence(text) -> str:
    """Без разделителей ограды реплик (как prompts.unfence): текст чата не
    должен «закрыть» блок данных в затравке."""
    return str(text).replace("<<<", "‹‹‹").replace(">>>", "›››")


def _one_line(text, limit: int) -> str:
    flat = " ".join(_unfence(text).split())
    return flat if len(flat) <= limit else flat[:max(limit - 1, 0)] + "…"


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    mark = f"… [обрезано {len(text) - limit} симв.]"
    return text[:max(limit - len(mark), 0)] + mark if limit > len(mark) else text[:max(limit, 0)]


def _clock(seconds) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _when(msg: dict) -> str:
    """Время сообщения: секунды встречи, иначе стенное (после встречи `t` нет)."""
    t = _num(msg.get("t"))
    if t is not None and t >= 0:
        return _clock(t)
    at = _num(msg.get("at"))
    if at is not None:
        try:
            return datetime.fromtimestamp(at).strftime("%d.%m %H:%M")
        except (OverflowError, OSError, ValueError):
            pass
    return "—"


def _encode(record: dict) -> bytes:
    text = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (text + "\n").encode("utf-8")


def _is_reaction(msg: dict) -> bool:
    return msg.get("kind") == "meeting" and msg.get("event") == "reaction"


def _short(msg: dict) -> bool:
    """Служебная строка затравки: одной короткой строкой, не в счёт `recent`."""
    return msg.get("kind") in ("system", "tool") or _is_reaction(msg)


def _tool_line(msg: dict) -> str:
    """Запрос агента или ответ Meet — одной строкой (сжато)."""
    if msg.get("event") == "result":
        target = msg.get("re") if isinstance(msg.get("re"), str) else "?"
        if msg.get("error"):
            return f"Meet: запрос {target} не выполнен — {_one_line(msg['error'], 120)}"
        size = f" ({msg['chars']} симв.)" if isinstance(msg.get("chars"), int) else ""
        return f"Meet: ответ на {target}{size}: {_one_line(msg.get('text') or '', COMPRESSED_LINE_MAX)}"
    call = msg.get("call") or "запрос"
    args = msg.get("args")
    what = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False) if args else ""
    return f"Ты запросил {call}: {_one_line(what, 120)}"


def _reaction_line(msg: dict) -> str:
    emoji = str(msg.get("text") or "")
    label = REACTIONS.get(emoji)
    what = f"{emoji} «{label}»" if label else _one_line(emoji, 20)
    target = msg.get("re") if isinstance(msg.get("re"), str) else "?"
    return (f"Реакция человека на {target}: {what}" if msg.get("on") is not False
            else f"Человек снял реакцию с {target}: {what}")


def _reactions_text(msg: dict) -> str:
    reactions = msg.get("reactions")
    if not isinstance(reactions, dict) or not reactions:
        return ""
    return ", ".join(f"{e} {REACTIONS[e]}" if e in REACTIONS else _one_line(e, 20)
                     for e in reactions)


def _system_error(msg: dict) -> bool:
    return msg.get("kind") == "system" and bool(msg.get("error") or msg.get("level") == "error")


def _check_fields(kind, fields: dict) -> dict:
    """Поля, у которых есть форма: кнопки и `pin` реплики агента, `event`
    записи `tool`. Неверный тип — ValueError (ошибка вызывающего)."""
    if kind == "agent" and "buttons" in fields:
        buttons = fields["buttons"]
        if buttons is None:
            buttons = []
        if not isinstance(buttons, (list, tuple)):
            raise ValueError("buttons — список строк")
        out: list[str] = []
        for b in buttons:
            if not isinstance(b, str):
                raise ValueError("buttons — список строк")
            label = " ".join(b.split())[:BUTTON_MAX_CHARS]
            if label and label not in out:
                out.append(label)
        # «Тупая труба»: лишние кнопки модели отбрасываются, а не ошибка.
        fields = {**fields, "buttons": out[:BUTTONS_MAX]}
    if kind == "agent" and "pin" in fields and not isinstance(fields["pin"], bool):
        raise ValueError("pin — True или False")
    if kind == "tool" and "event" in fields and fields["event"] not in TOOL_EVENTS:
        raise ValueError(f"event записи tool — {' | '.join(TOOL_EVENTS)}")
    if kind == "tool" and "call" in fields and fields["call"] not in TOOL_CALLS:
        raise ValueError(f"call записи tool — {' | '.join(TOOL_CALLS)}")
    if kind == "tool" and isinstance(fields.get("text"), str) and len(fields["text"]) > TOOL_TEXT_MAX:
        text = fields["text"]
        mark = f"… [обрезано: в журнале {TOOL_TEXT_MAX} из {len(text)} симв.]"
        fields = {**fields, "text": text[:TOOL_TEXT_MAX - len(mark)] + mark}
        fields.setdefault("chars", len(text))
    return fields


def visible_in_feed(record: dict) -> bool:
    """Показывать ли сообщение в ленте окна (`snapshot(feed=True)`, SSE
    `chat` add): нет служебных записей `tool`, событий реакций и «озвучено»
    (это отметки у реплики) и реплик, которых в чате не было видно
    (`HIDDEN_STATUSES`). Пишущийся ответ (`writing`) виден — пузырь
    «Пишет…». Окно держит те же правила (задача 7)."""
    if not isinstance(record, dict):
        return False
    kind = record.get("kind")
    if kind == "tool":
        return False
    if kind == "meeting" and record.get("event") in ("reaction", "voiced"):
        return False
    return not (kind == "agent" and record.get("status") in HIDDEN_STATUSES)


class ChatLog:
    """Журнал сессии одной записи. Потокобезопасен; несколько экземпляров
    (в том числе в разных процессах) на одну папку пишут через общий замок.

    Свёртка в памяти дочитывается из файла перед каждой операцией — видны
    записи второго писателя. Предупреждения (`log`) зовутся после снятия
    замка: `log` может сам обращаться к журналу."""

    def __init__(self, folder: Path, *, clock=time.time, log=None,
                 lock_wait: float = CHAT_LOCK_WAIT_S, read_wait: float = READ_LOCK_WAIT_S):
        self.folder = Path(folder)
        self.dir = chat_dir(self.folder)
        self.path = self.dir / CHAT_JSONL
        self.sessions_path = self.dir / SESSIONS_JSON
        self._lock_key = self.dir / LOCK_NAME
        self._clock = clock
        self._log = log or _print
        self._lock_wait = lock_wait
        self._read_wait = read_wait
        self._mem = threading.RLock()
        self._pending: list[str] = []
        self._reset()

    # --- состояние свёртки ---

    def _reset(self) -> None:
        self._order: list[str] = []
        self._view: dict[str, dict] = {}
        self._by_client: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._seq = 0
        self._offset = 0
        self._ino = None
        self._line_no = 0
        self._tail = b""
        self._needs_newline = False
        self._last_seen: tuple | None = None
        self._stats = {"broken": 0, "orphan_patches": 0, "duplicates": 0, "unknown": 0}

    # --- замки и предупреждения ---

    @contextmanager
    def _write_lock(self):
        """Строгий замок записи: не взят за lock_wait — FileLockTimeout."""
        try:
            with library.file_lock(self._lock_key, strict=True, wait=self._lock_wait), self._mem:
                yield
        finally:
            self._flush_warnings()

    @contextmanager
    def _read_lock(self):
        """Замок чтения: не взят за read_wait — читаем без него (чтение не пишет;
        недописанную чужую строку дочитает следующая операция)."""
        try:
            with ExitStack() as stack:
                try:
                    stack.enter_context(library.file_lock(self._lock_key, strict=True,
                                                          wait=self._read_wait))
                except FileLockTimeout:
                    self._warn("замок занят — читаю без него")
                with self._mem:
                    yield
        finally:
            self._flush_warnings()

    def _warn(self, text: str) -> None:
        self._pending.append(f"{CHAT_DIR}/{CHAT_JSONL}: {text}")

    def _warn_once(self, key: tuple, text: str) -> None:
        full = (os.path.normcase(str(self.path)), self._ino, *key)
        with _REPORTED_GUARD:
            if full in _REPORTED:
                return
            if len(_REPORTED) >= _REPORTED_MAX:
                _REPORTED.clear()
            _REPORTED.add(full)
        self._warn(text)

    def _flush_warnings(self) -> None:
        while self._pending:
            try:
                text = self._pending.pop(0)
            except IndexError:
                return
            try:
                self._log(text)
            except Exception:
                pass

    # --- дочитывание ---

    def _sync(self) -> None:
        """Дочитать файл с последнего места. Только под замком (или self._mem)."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            if self._offset or self._order:
                self._warn("журнал исчез — свёртка сброшена")
                self._reset()
            return
        except OSError as e:
            self._warn(f"не читается: {e}")
            return
        ino = st.st_ino or None
        if st.st_size < self._offset or (self._ino and ino and ino != self._ino):
            self._warn("журнал заменён — перечитываю целиком")
            self._reset()
        self._ino = ino
        if st.st_size == self._offset + len(self._tail):
            return
        try:
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError as e:
            self._warn(f"не читается: {e}")
            return
        end = data.rfind(b"\n")
        complete, tail = (data[:end + 1], data[end + 1:]) if end >= 0 else (b"", data)
        pos = self._offset
        for raw in complete.split(b"\n")[:-1]:
            self._line_no += 1
            self._consume(raw, pos)
            pos += len(raw) + 1
        self._offset += len(complete)
        self._tail = b""
        self._needs_newline = bool(tail)
        if not tail:
            return
        if self._is_record(tail):
            # Убили ровно перед `\n`: строка целая — записанная.
            self._line_no += 1
            self._consume(tail, self._offset)
            self._offset += len(tail)
            return
        self._tail = tail
        self._warn_once(("torn", self._offset, len(tail)),
                        f"оборванная последняя строка ({len(tail)} байт с байта {self._offset}) "
                        "пропущена")

    @staticmethod
    def _is_record(raw: bytes) -> bool:
        try:
            return isinstance(json.loads(raw.strip().decode("utf-8")), dict)
        except (UnicodeDecodeError, ValueError):
            return False

    def _broken(self, pos: int, why: str) -> None:
        self._stats["broken"] += 1
        self._warn_once(("line", pos), f"битая строка {self._line_no} (байт {pos}) пропущена: {why}")

    def _consume(self, raw: bytes, pos: int) -> None:
        line = raw.strip()
        if not line:
            return
        try:
            rec = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            self._broken(pos, f"не JSON ({e.__class__.__name__})")
            return
        if not isinstance(rec, dict):
            self._broken(pos, "не объект")
            return
        if _SURROGATE_ESCAPE.search(line):
            rec = _clean(rec)
        kind_of_rec, rid, seq = rec.get("rec"), rec.get("id"), rec.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(rid, str) or not rid:
            self._broken(pos, "нет seq или id")
            return
        if kind_of_rec == "msg":
            if not isinstance(rec.get("kind"), str):
                self._broken(pos, "нет kind")
                return
            self._seq = max(self._seq, seq)
            if rid in self._view:
                self._stats["duplicates"] += 1
                self._warn_once(("dup", pos), f"повтор id {rid} (строка {self._line_no}) пропущен")
                return
            self._add(rec)
            self._last_seen = ("msg", rid, seq)
        elif kind_of_rec == "patch":
            changes = rec.get("set")
            if not isinstance(changes, dict):
                self._broken(pos, "patch без set")
                return
            self._seq = max(self._seq, seq)
            msg = self._view.get(rid)
            if msg is None:
                self._stats["orphan_patches"] += 1
                self._warn_once(("orphan", pos),
                                f"patch к неизвестному {rid} (строка {self._line_no}) пропущен")
                return
            for key, value in changes.items():
                if key not in PROTECTED:
                    msg[key] = value
            self._last_seen = ("patch", rid, seq)
        else:
            # Запись будущей версии: seq учитываем, содержимое — нет.
            self._seq = max(self._seq, seq)
            self._stats["unknown"] += 1

    def _add(self, rec: dict) -> None:
        rid = rec["id"]
        msg = {k: v for k, v in rec.items() if k not in ("v", "rec")}
        self._view[rid] = msg
        self._order.append(rid)
        m = _ID.match(rid)
        if m:
            prefix, n = m.group(1), int(m.group(2))
            self._counters[prefix] = max(self._counters.get(prefix, 0), n)
        cid = rec.get("client_id")
        if isinstance(cid, str) and cid and cid not in self._by_client:
            self._by_client[cid] = rid

    def _write(self, data: bytes, *, fsync: bool) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if self._needs_newline:
            # Строка убитого писателя без `\n`: закрываем её, а не клеим к ней.
            data = b"\n" + data
        with open(self.path, "ab") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())

    def _confirm(self, rec: str, rid: str, seq: int) -> None:
        """Записанная строка прочитана обратно как своя (тот же seq)."""
        self._sync()
        if self._last_seen != (rec, rid, seq) or (rec == "msg" and self._view.get(rid, {}).get("seq") != seq):
            raise ChatLogError(f"{self.path}: запись {rec} {rid} seq {seq} не подтвердилась "
                               f"(прочитано {self._last_seen})")

    # --- чтение ---

    def load(self) -> list[dict]:
        """Перечитать журнал целиком: сообщения, следующий `seq` и id."""
        with self._read_lock():
            self._reset()
            self._sync()
            return self._copy_view()

    def _copy_view(self, ids=None) -> list[dict]:
        return [_copy(self._view[rid]) for rid in (self._order if ids is None else ids)]

    def messages(self) -> list[dict]:
        """Свёртка (копии), в порядке записи. Все виды и статусы — что
        показывать, решает потребитель (`HIDDEN_STATUSES`, `WRITING`)."""
        with self._read_lock():
            self._sync()
            return self._copy_view()

    def snapshot(self, limit: int | None = None, *, feed: bool = False) -> dict:
        """`{"messages":[…],"seq":N}` — для `GET /chat` и `chat_snapshot`.
        `limit` — последние N (0 и меньше — ни одного); `feed=True` — только
        то, что видно в ленте (`visible_in_feed`), limit — после отбора."""
        with self._read_lock():
            self._sync()
            ids = self._order
            if feed:
                ids = [rid for rid in ids if visible_in_feed(self._view[rid])]
            if limit is not None:
                ids = ids[-limit:] if limit > 0 else []
            return {"messages": self._copy_view(ids), "seq": self._seq}

    def get(self, mid: str) -> dict | None:
        with self._read_lock():
            self._sync()
            msg = self._view.get(mid)
            return _copy(msg) if msg is not None else None

    def by_client_id(self, client_id: str) -> dict | None:
        with self._read_lock():
            self._sync()
            rid = self._by_client.get(client_id)
            return _copy(self._view[rid]) if rid else None

    @property
    def seq(self) -> int:
        """Последний `seq` (дочитав файл)."""
        with self._read_lock():
            self._sync()
            return self._seq

    def stats(self) -> dict:
        """Сколько пропущено при чтении: битые строки (и оборванный хвост —
        `torn_tail`), patch к неизвестному id, повторы id, неизвестные rec."""
        with self._read_lock():
            self._sync()
            return {**self._stats, "torn_tail": bool(self._tail)}

    # --- запись ---

    def append(self, kind: str, /, *, client_id: str | None = None, **fields) -> Appended:
        """Новое сообщение (`rec: msg`, с fsync). `client_id` — идемпотентность:
        уже записанный возвращает прежнее сообщение без записи. Замок не взят —
        FileLockTimeout; запись не подтвердилась — ChatLogError."""
        if kind not in KINDS:
            raise ValueError(f"неизвестный вид сообщения: {kind!r}")
        bad = [k for k in fields if k in PROTECTED]
        if bad:
            raise ValueError(f"поля ставит журнал: {', '.join(bad)}")
        if client_id is not None and (not isinstance(client_id, str) or not client_id):
            raise ValueError("client_id — непустая строка")
        fields = _check_fields(kind, _clean(fields))
        client_id = _clean(client_id)
        with self._write_lock():
            self._sync()
            return self._append_locked(kind, client_id, fields)

    def _append_locked(self, kind: str, client_id: str | None, fields: dict) -> Appended:
        if client_id and client_id in self._by_client:
            return Appended(_copy(self._view[self._by_client[client_id]]), None)
        prefix = "a" if kind == "attachment" else "m"
        rid = f"{prefix}{self._counters.get(prefix, 0) + 1}"
        seq = self._seq + 1
        rec = {"v": VERSION, "seq": seq, "at": round(float(self._clock()), 3),
               "rec": "msg", "id": rid, "kind": kind}
        if client_id:
            rec["client_id"] = client_id
        rec.update(fields)
        self._write(_encode(rec), fsync=True)
        self._confirm("msg", rid, seq)
        message = _copy(self._view[rid])
        return Appended(message, {"seq": seq, "op": "add", "message": _copy(message)})

    def write_journal(self, messages) -> int:
        """Низкоуровневое: новый журнал из готовых сообщений (свёрнутых, как
        их отдаёт `load`) — объединение записей (`merge.merge_chats`). Каждое
        — одна запись `msg` с тем же `id`, `kind`, `at`, `client_id` и полями,
        `seq` — по порядку с 1 (порядок задаёт вызывающий). Id — `a<N>` у
        вложений и `m<N>` у остальных, без повторов; повтор `client_id` —
        у первого. Журнал уже есть — FileExistsError: он не переписывается
        никогда. Файл пишется целиком атомарно (временный + замена), под
        строгим замком. `sessions.json` не трогается. → сколько записано."""
        records: list[dict] = []
        ids: set[str] = set()
        clients: set[str] = set()
        for n, msg in enumerate(messages or (), start=1):
            if not isinstance(msg, dict):
                raise ValueError(f"сообщение {n}: не словарь")
            kind, rid = msg.get("kind"), msg.get("id")
            if kind not in KINDS:
                raise ValueError(f"сообщение {n}: неизвестный вид {kind!r}")
            m = _ID.match(rid) if isinstance(rid, str) else None
            if m is None or m.group(1) != ("a" if kind == "attachment" else "m"):
                raise ValueError(f"сообщение {n}: негодный id {rid!r} для {kind}")
            if rid in ids:
                raise ValueError(f"сообщение {n}: повтор id {rid}")
            ids.add(rid)
            at = _num(msg.get("at"))
            rec = {"v": VERSION, "seq": n, "at": round(at if at is not None else float(self._clock()), 3),
                   "rec": "msg", "id": rid, "kind": kind}
            cid = msg.get("client_id")
            if isinstance(cid, str) and cid and cid not in clients:
                clients.add(cid)
                rec["client_id"] = _clean(cid)
            fields = {k: v for k, v in msg.items() if k not in (*PROTECTED, "seq")}
            rec.update(_check_fields(kind, _clean(fields)))
            records.append(rec)
        data = b"".join(_encode(r) for r in records)
        with self._write_lock():
            if self.path.exists():
                raise FileExistsError(f"{self.path}: журнал уже есть — не переписывается")
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                library.replace_atomic(tmp, self.path)
            finally:
                tmp.unlink(missing_ok=True)
            self._reset()
            self._sync()
        return len(records)

    # --- реакции человека на реплики агента ---

    def react(self, mid: str, emoji: str, on: bool | None = None, *, t: float | None = None) -> list[dict]:
        """Реакция человека на реплику агента (`REACTIONS`): `on=None` —
        переключить, True/False — поставить/снять. Пишется двумя записями
        под одним замком: `patch` реплики (`reactions: {эмодзи: at}`; снятая
        уходит из словаря) и событие встречи `meeting` (`event: "reaction"`,
        `re`, `text`=эмодзи, `on`) — агент видит отклики по порядку. Ничего не
        меняется (повтор, неизвестный id) — `[]`; иначе события SSE `chat`."""
        if emoji not in REACTIONS:
            raise ValueError(f"неизвестная реакция: {emoji!r}")
        if on is not None and not isinstance(on, bool):
            raise ValueError("on — True, False или None")
        with self._write_lock():
            self._sync()
            msg = self._view.get(mid)
            if msg is None:
                return []
            if msg.get("kind") != "agent":
                raise ValueError(f"реакции — только на реплики агента, {mid} — {msg.get('kind')}")
            current = msg.get("reactions") if isinstance(msg.get("reactions"), dict) else {}
            want = (emoji not in current) if on is None else on
            if want == (emoji in current):
                return []
            now = round(float(self._clock()), 3)
            reactions = {k: v for k, v in current.items() if k != emoji}
            if want:
                reactions[emoji] = now
            events = []
            ev = self._patch_locked(mid, {"reactions": reactions})
            if ev is not None:
                events.append(ev)
            fields = {"event": "reaction", "re": mid, "text": emoji, "on": want}
            if t is not None:
                fields["t"] = t
            events.append(self._append_locked("meeting", None, fields).event)
            return events

    def patch(self, mid: str, changes: dict) -> dict | None:
        """Правка полей сообщения (`rec: patch`, без fsync). Пишется только
        то, что меняется; ничего или неизвестный id — `None`. Иначе событие
        SSE `chat`: `{"seq","op":"patch","id","set"}`."""
        changes = self._check_changes(changes)
        with self._write_lock():
            self._sync()
            return self._patch_locked(mid, changes)

    @staticmethod
    def _check_changes(changes) -> dict:
        if not isinstance(changes, dict):
            raise ValueError("changes — словарь полей")
        bad = [k for k in changes if k in PROTECTED]
        if bad:
            raise ValueError(f"эти поля не правятся: {', '.join(bad)}")
        return _clean(changes)

    def _patch_locked(self, mid: str, changes: dict) -> dict | None:
        msg = self._view.get(mid)
        if msg is None:
            return None
        changes = _check_fields(msg.get("kind"), changes)
        missing = object()
        diff = {k: v for k, v in changes.items() if msg.get(k, missing) != v}
        if not diff:
            return None
        seq = self._seq + 1
        rec = {"v": VERSION, "seq": seq, "at": round(float(self._clock()), 3),
               "rec": "patch", "id": mid, "set": diff}
        self._write(_encode(rec), fsync=False)
        self._confirm("patch", mid, seq)
        return {"seq": seq, "op": "patch", "id": mid, "set": _copy(diff)}

    # --- ответ, который пишется (§3.4) ---

    def begin_reply(self, *, client_id: str | None = None, **fields) -> Appended:
        """Реплика агента в начале хода: `status: "writing"`, текст пустой
        (или начальный). Её id — для `chat_partial` и `/chat/stop`."""
        fields.setdefault("text", "")
        fields["status"] = WRITING
        return self.append("agent", client_id=client_id, **fields)

    def finish_reply(self, mid: str, *, status: str = "shown", **fields) -> dict | None:
        """Конец хода: готовый текст и статус — `shown`, `cancelled` (стоп;
        видимый текст остаётся) или `failed` (с `error`). Правит только
        реплику в статусе `writing`; иначе (ход уже закрыт) — `None`."""
        if status == WRITING:
            raise ValueError("finish_reply закрывает ход: статус не writing")
        changes = self._check_changes({**fields, "status": status})
        with self._write_lock():
            self._sync()
            msg = self._view.get(mid)
            if msg is None or msg.get("status") != WRITING:
                # Ход уже закрыт (close_interrupted, стоп) — не воскрешаем.
                return None
            return self._patch_locked(mid, changes)

    def close_interrupted(self, error: str = "ответ прерван: ассистент перезапущен") -> list[dict]:
        """Писатель при старте: реплики `writing`, оставшиеся от убитого
        процесса, → `cancelled` с `error`. События patch (для SSE). Только
        для единственного живого писателя (резидент во время живой записи
        отвечает 409, §7.2): чужой идущий ход тоже был бы закрыт."""
        with self._write_lock():
            self._sync()
            stuck = [rid for rid in self._order if self._view[rid].get("status") == WRITING]
            events = []
            for rid in stuck:
                ev = self._patch_locked(rid, {"status": "cancelled", "error": error})
                if ev is not None:
                    events.append(ev)
            return events

    # --- кнопки реплик агента ---

    def click_button(self, mid: str, label: str, *, client_id: str | None = None,
                     t: float | None = None) -> Appended:
        """Нажатие кнопки реплики агента — сообщение человека: `text` = надпись,
        `via: "button"`, `re` = id реплики (v4-simple §2). Кнопки нет у этой
        реплики или реплика не агента — ValueError. `client_id` — как у
        `append` (повторное нажатие при переподключении не дублируется)."""
        if not isinstance(label, str) or not label:
            raise ValueError("надпись кнопки — непустая строка")
        if client_id is not None and (not isinstance(client_id, str) or not client_id):
            raise ValueError("client_id — непустая строка")
        label = _clean(label)
        fields = {"text": label, "via": "button", "re": mid}
        if t is not None:
            fields["t"] = t
        with self._write_lock():
            self._sync()
            msg = self._view.get(mid)
            if msg is None or msg.get("kind") != "agent":
                raise ValueError(f"{mid}: нет такой реплики агента")
            if label not in (msg.get("buttons") or []):
                raise ValueError(f"у {mid} нет кнопки {label!r}")
            return self._append_locked("user", _clean(client_id), fields)

    # --- сеансы провайдеров (родное продолжение контекста) ---

    def _load_sessions(self) -> dict | None:
        """Весь `sessions.json`: `{}` — нет файла, `None` — битый (не JSON,
        не та форма). Ошибка чтения (занят) — повтор, затем OSError."""
        for attempt in range(SESSIONS_READ_TRIES):
            try:
                raw = self.sessions_path.read_bytes()
                break
            except FileNotFoundError:
                return {}
            except OSError:
                if attempt == SESSIONS_READ_TRIES - 1:
                    raise
                time.sleep(SESSIONS_READ_PAUSE_S * (attempt + 1))
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            self._warn(f"{SESSIONS_JSON} битый ({e.__class__.__name__}) — сеансов нет")
            return None
        if not isinstance(data, dict) or not isinstance(data.get("heads", {}), dict):
            self._warn(f"{SESSIONS_JSON}: не та форма — сеансов нет")
            return None
        return data

    @staticmethod
    def _entries(doc: dict | None) -> dict:
        out: dict = {}
        heads = (doc or {}).get("heads") or {}
        for head, providers in heads.items():
            if not isinstance(providers, dict):
                continue
            for provider, entry in providers.items():
                if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]:
                    out.setdefault(head, {})[provider] = _copy(entry)
        return out

    def session(self, provider: str, *, head: str = DEFAULT_HEAD) -> dict | None:
        """Вся запись сеанса: `{"id","used_at",…поля вызывающего}`; нет — None.
        Файл занят дольше повторов — OSError."""
        with self._read_lock():
            return self._entries(self._load_sessions()).get(head, {}).get(provider)

    def session_id(self, provider: str, *, head: str = DEFAULT_HEAD) -> str | None:
        """Id сеанса провайдера (для `--resume` и т. п.); нет — None."""
        entry = self.session(provider, head=head)
        return entry["id"] if entry else None

    def sessions(self) -> dict:
        """Все сохранённые сеансы: `{head: {provider: {"id","used_at",…}}}`."""
        with self._read_lock():
            return self._entries(self._load_sessions())

    def set_session_id(self, provider: str, session_id: str | None, *,
                       head: str = DEFAULT_HEAD, **meta) -> None:
        """Запомнить сеанс (`used_at` — сейчас; `meta` — свои поля: `cwd`,
        `model`…) или забыть (`None`). Тот же id — поля прежней записи
        остаются, `meta` поверх. Атомарно, под строгим замком журнала;
        неизвестные поля файла сохраняются. Файл не прочитался — OSError, и
        он не перезаписывается."""
        if not isinstance(head, str) or not head or not isinstance(provider, str) or not provider:
            raise ValueError("head и provider — непустые строки")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("session_id — непустая строка или None")
        bad = [k for k in meta if k in SESSION_KEYS]
        if bad:
            raise ValueError(f"поля ставит журнал: {', '.join(bad)}")
        meta = _clean(meta)
        json.dumps(meta, allow_nan=False)        # несериализуемое — ошибка до записи
        with self._write_lock():
            doc = self._load_sessions()
            if doc is None:
                doc = {}                          # битый — заменяется
            heads = doc.get("heads") if isinstance(doc.get("heads"), dict) else {}
            providers = heads.get(head) if isinstance(heads.get(head), dict) else {}
            if session_id is None:
                if provider not in providers:
                    return
                del providers[provider]
                if providers:
                    heads[head] = providers
                else:
                    heads.pop(head, None)
            else:
                old = providers.get(provider)
                base = old if isinstance(old, dict) and old.get("id") == session_id else {}
                providers[provider] = {**base, **meta, "id": session_id,
                                       "used_at": round(float(self._clock()), 3)}
                heads[head] = providers
            doc.setdefault("v", VERSION)
            doc["heads"] = heads
            self._write_json(self.sessions_path, doc)

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            library.replace_atomic(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    # --- затравка (запасной путь) ---

    def context(self, budget: int, *, recent: int = RECENT_MESSAGES) -> str:
        """Журнал для затравки головы — **запасной путь**, когда родного
        продолжения сеанса нет (локальная модель, сеанс истёк, провайдер без
        resume). Последние `recent` сообщений — дословно (вложения —
        подписью; одно сообщение — не больше трети бюджета, пометки статуса
        не обрезаются; `system` — короткой строкой и не в счёт `recent`),
        раньше — по строке, а что не уместилось — счётчиками. `budget` —
        символы; длина результата никогда его не превышает. Порядок —
        хронологический. Пишущийся ответ (`writing`), `meeting voiced` и
        `system` с ошибкой не попадают."""
        if budget <= 0:
            return ""
        everything = self.messages()
        view = {m["id"]: m for m in everything}
        msgs = [m for m in everything
                if not (m.get("kind") == "meeting" and m.get("event") == "voiced")
                and m.get("status") != WRITING and not _system_error(m)]
        if not msgs:
            return ""
        split, counted = len(msgs), 0
        for i in range(len(msgs) - 1, -1, -1):
            if counted >= max(recent, 0):
                break
            split = i
            if not _short(msgs[i]):
                counted += 1
        cap = max(budget // VERBATIM_SHARE, VERBATIM_MIN)
        verbatim_title = "Последние сообщения чата — дословно:"
        verbatim: list[str] = []
        size = len(verbatim_title) + 1
        first_verbatim = len(msgs)
        for i in range(len(msgs) - 1, split - 1, -1):
            block = self._verbatim(msgs[i], view, cap)
            # Останутся раньшие — нужно место под строку их счётчиков.
            reserve = COUNTS_RESERVE if i > 0 else 0
            if size + len(block) + 1 + reserve > budget:
                break
            verbatim.insert(0, block)
            size += len(block) + 1
            first_verbatim = i
        older = msgs[:first_verbatim]
        compressed: list[str] = []
        head = ""
        if older:
            room = budget - size - COUNTS_RESERVE
            for m in reversed(older):
                line = "- " + self._compressed(m)
                if len(line) + 1 > room:
                    break
                compressed.insert(0, line)
                room -= len(line) + 1
            omitted = len(older) - len(compressed)
            head = "Раньше в чате — сжато (" + self._counts(older)
            head += f"; ещё {omitted} не уместились):" if omitted else "):"

        def compose() -> str:
            parts = [head, *compressed] if head else []
            if verbatim:
                parts += [verbatim_title, *verbatim]
            return "\n".join(parts)

        text = compose()
        while len(text) > budget and compressed:
            compressed.pop(0)     # крайний случай: первыми уходят сжатые строки
            text = compose()
        if len(text) > budget and head and verbatim:
            head = ""             # затем счётчики раньшего
            text = compose()
        # Крошечный бюджет: режем начало — новое важнее.
        return text if len(text) <= budget else text[len(text) - budget:]

    @staticmethod
    def _counts(msgs: list[dict]) -> str:
        agent = [m for m in msgs if m.get("kind") == "agent"]
        voiced = sum(1 for m in agent if m.get("voiced"))
        rejected = sum(1 for m in agent
                       if m.get("feedback") == "not_now" or m.get("status") == "dismissed")
        users = sum(1 for m in msgs if m.get("kind") == "user")
        attachments = sum(1 for m in msgs if m.get("kind") == "attachment")
        return (f"мои реплики: {len(agent)}, озвучено: {voiced}, отклонено: {rejected}, "
                f"сообщений человека: {users}, вложений: {attachments}")

    def _caption(self, msg: dict) -> str:
        kind = _TYPE.get(msg.get("type"), msg.get("type") or "файл")
        status = _STATUS.get(msg.get("status"), msg.get("status") or "")
        name = _one_line(msg.get("name") or msg.get("title") or msg["id"], 120)
        return f"Вложение «{name}» ({kind}{', ' + status if status else ''})"

    @staticmethod
    def _attachment_names(ids, view: dict) -> str:
        if not isinstance(ids, list):
            return ""
        names = []
        for aid in ids:
            a = view.get(aid) if isinstance(aid, str) else None
            label = _one_line((a or {}).get("name") or "", 80)
            names.append(f"{aid} «{label}»" if label else str(aid))
        return ", ".join(names)

    @staticmethod
    def _agent_marks(msg: dict) -> list[str]:
        marks = []
        buttons = msg.get("buttons")
        if isinstance(buttons, list) and buttons:
            marks.append("  Кнопки: " + " ".join(f"[{_one_line(b, BUTTON_MAX_CHARS)}]" for b in buttons))
        if msg.get("pin") is True:
            marks.append("  [закреплено]")
        status = msg.get("status")
        if status in HIDDEN_STATUSES or status in ("cancelled", "dismissed", "failed"):
            marks.append(f"  [в чате: {status}]")
        voiced = msg.get("voiced")
        if isinstance(voiced, dict):
            vt = _num(voiced.get("t"))
            line = _one_line(voiced.get("line") or "", 200)
            marks.append(f"  [Озвучено{' [' + _clock(vt) + ']' if vt is not None else ''}"
                         f"{': «' + line + '»' if line else ''}]")
        if msg.get("feedback"):
            marks.append(f"  [Отклик: {_one_line(_FEEDBACK.get(msg['feedback'], msg['feedback']), 60)}]")
        if _reactions_text(msg):
            marks.append(f"  [Реакции человека: {_reactions_text(msg)}]")
        if msg.get("error"):
            marks.append(f"  [Ошибка: {_one_line(msg['error'], 200)}]")
        return marks

    def _verbatim(self, msg: dict, view: dict, cap: int) -> str:
        """Блок сообщения не длиннее cap; пометки (статус, озвучено, отклик,
        ошибка) не обрезаются — режется текст."""
        kind = msg.get("kind")
        stamp = f"[{msg['id']} · {_when(msg)}]"
        text = _unfence(msg.get("text") or "").strip()
        marks: list[str] = []
        if kind == "agent":
            mode = _MODE.get(msg.get("mode"), msg.get("mode") or "")
            re_ = f", к {msg['re']}" if isinstance(msg.get("re"), str) else ""
            lines = [f"{stamp} Ты писал ({mode}{re_}): {text}" if mode or re_
                     else f"{stamp} Ты писал: {text}"]
            if msg.get("say"):
                lines.append(f"  Сказать: «{_one_line(msg['say'], 400)}»")
            if msg.get("reply"):
                lines.append(f"  Черновик ответа: «{_one_line(msg['reply'], 400)}»")
            marks = self._agent_marks(msg)
        elif kind == "user":
            extra = []
            if msg.get("quick"):
                extra.append(f"быстрый ответ: {_QUICK.get(msg['quick'], msg['quick'])}")
            if msg.get("via") == "button":
                extra.append("кнопка")
            if isinstance(msg.get("re"), str):
                extra.append(f"к {msg['re']}")
            note = f" ({', '.join(extra)})" if extra else ""
            lines = [f"{stamp} Ты получил сообщение{note}: {text}"]
            names = self._attachment_names(msg.get("attachments"), view)
            if names:
                marks = [f"  Вложения: {_one_line(names, 400)}"]
        elif kind == "attachment":
            lines = [f"{stamp} {self._caption(msg)}"]
        elif _is_reaction(msg):
            lines = [f"{stamp} {_reaction_line(msg)}"]
        elif kind == "tool":
            lines = [_one_line(f"{stamp} {_tool_line(msg)}", COMPRESSED_LINE_MAX + 40)]
        elif kind == "meeting":
            lines = [f"{stamp} Встреча: {_one_line(text or msg.get('event') or 'событие', 400)}"]
        else:
            lines = [f"{stamp} Система: {_one_line(text, COMPRESSED_LINE_MAX)}"]
        body = "\n".join(lines)
        tail = "\n".join(marks)
        room = cap - (len(tail) + 1 if tail else 0)
        if room < VERBATIM_MIN // 2:
            return _cut("\n".join([body, tail]) if tail else body, cap)
        body = _cut(body, room)
        return f"{body}\n{tail}" if tail else body

    def _compressed(self, msg: dict) -> str:
        kind = msg.get("kind")
        who = {"agent": "ты", "user": "человек", "meeting": "встреча",
               "system": "система"}.get(kind, kind)
        if kind == "attachment":
            return _one_line(f"{msg['id']} {_when(msg)}: {self._caption(msg)}", COMPRESSED_LINE_MAX)
        if _is_reaction(msg):
            return _one_line(f"{msg['id']} {_when(msg)}: {_reaction_line(msg)}", COMPRESSED_LINE_MAX)
        if kind == "tool":
            return _one_line(f"{msg['id']} {_when(msg)}: {_tool_line(msg)}", COMPRESSED_LINE_MAX)
        body = msg.get("text") or msg.get("event") or ""
        marks = []
        if kind == "agent":
            if msg.get("voiced"):
                marks.append("озвучено")
            if msg.get("feedback") == "not_now" or msg.get("status") == "dismissed":
                marks.append("отклонено")
            if msg.get("status") in (*HIDDEN_STATUSES, "cancelled", "failed"):
                marks.append(msg["status"])
        if marks:
            body = f"({', '.join(marks)}) {body}"
        return _one_line(f"{msg['id']} {_when(msg)} {who}: {body}", COMPRESSED_LINE_MAX)

    # --- assistant_chat.md ---

    def render_md(self, title: str | None = None) -> str:
        """Переписка в читаемом виде (для вкладки «Агент», `agent_context`).
        Только то, что было видно в чате: без `held`/`dropped`/`superseded`
        и без незаконченного ответа (`writing`)."""
        msgs = self.messages()
        view = {m["id"]: m for m in msgs}
        out = [f"# {title or 'Разговор с ассистентом'}", "",
               f"Журнал чата ассистента этой встречи ({CHAT_DIR}/{CHAT_JSONL}) в читаемом "
               "виде. Только для чтения: правки здесь никуда не попадут.", ""]
        shown = 0
        for m in msgs:
            kind = m.get("kind")
            when = _when(m)
            if kind == "agent":
                if m.get("status") in HIDDEN_STATUSES or m.get("status") == WRITING:
                    continue
                mode = " · «Вам вопрос»" if m.get("mode") == "ask_you" else ""
                pin = " · закреплено" if m.get("pin") is True else ""
                block = [f"**Ассистент**{mode}{pin} · {when} · {m['id']}", "",
                         (m.get("text") or "").strip()]
                buttons = m.get("buttons") if isinstance(m.get("buttons"), list) else []
                if buttons:
                    block += ["", "Кнопки: " + " · ".join(f"«{_one_line(b, BUTTON_MAX_CHARS)}»"
                                                         for b in buttons)]
                if m.get("say"):
                    block += ["", f"> Сказать: «{_one_line(m['say'], 400)}»"]
                if m.get("reply"):
                    block += ["", f"> Черновик ответа: «{_one_line(m['reply'], 400)}»"]
                notes = []
                voiced = m.get("voiced")
                if isinstance(voiced, dict):
                    vt = _num(voiced.get("t"))
                    notes.append("✓ вы это сказали" + (f" · {_clock(vt)}" if vt is not None else ""))
                if m.get("feedback") == "not_now" or m.get("status") == "dismissed":
                    notes.append("скрыто («не сейчас»)")
                if m.get("status") == "cancelled":
                    notes.append("остановлено")
                if _reactions_text(m):
                    notes.append(f"реакции: {_reactions_text(m)}")
                if m.get("status") == "failed" and not m.get("error"):
                    notes.append("ответ не получен")
                if m.get("error"):
                    notes.append(f"ошибка: {_one_line(m['error'], 200)}")
                if notes:
                    block += ["", f"_{' · '.join(notes)}_"]
            elif kind == "user":
                quick = f" · {_QUICK.get(m['quick'], m['quick'])}" if m.get("quick") else ""
                if m.get("via") == "button":
                    quick += f" · кнопка{' к ' + m['re'] if isinstance(m.get('re'), str) else ''}"
                block = [f"**Вы**{quick} · {when} · {m['id']}", "", (m.get("text") or "").strip()]
                ids = m.get("attachments") if isinstance(m.get("attachments"), list) else []
                names = [_one_line((view.get(a) or {}).get("name") or a, 120)
                         for a in ids if isinstance(a, str)]
                if names:
                    block += ["", "Вложения: " + ", ".join(f"«{n}»" for n in names)]
            elif kind == "attachment":
                block = [f"_{self._caption(m)} · {when} · {m['id']}_"]
            elif kind == "tool":
                continue          # запросы агента и ответы Meet — служебные
            elif kind == "meeting":
                if m.get("event") in ("voiced", "reaction"):
                    continue
                block = [f"— {_one_line(m.get('text') or m.get('event') or 'встреча', 200)} · {when} —"]
            else:
                block = [f"_Система: {_one_line(m.get('text') or '', 400)} · {when}_"]
            out += [*block, ""]
            shown += 1
        if not shown:
            out += ["_Сообщений нет._", ""]
        return "\n".join(out)

    def write_md(self, dest: Path | None = None, title: str | None = None) -> Path:
        """Записать `assistant_chat.md` атомарно (по умолчанию — в папку записи).
        Пишет всегда, даже пустой чат: у старых встреч (§8) вызывающий
        сначала проверяет `has_chat`."""
        dest = Path(dest) if dest is not None else self.folder / ASSISTANT_CHAT_MD
        text = self.render_md(title)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with self._write_lock():
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                library.replace_atomic(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
        return dest
