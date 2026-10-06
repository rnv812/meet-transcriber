"""Журнал сессии ассистента: `<папка записи>/assistant/chat.jsonl` (V4, §2).

Одна сессия на встречу: старт, отключение, повторное подключение, перезапуск
ребёнка и «Продолжить разговор» после встречи пишут в один журнал. Журнал —
запись чата для окна (лента во время встречи, вкладка «Ассистент» после неё,
`assistant_chat.md` для вкладки «Агент»).

**Контекст модели и журнал.** Контекст голов агента восстанавливается
родным продолжением сеанса провайдера (Claude Code `--resume <id>`, Codex
`exec resume <id>`, OpenCode `--session <id>` — задача 2). Id сеансов
хранятся здесь же, в `assistant/sessions.json`: по голове (`HEADS`:
`listener` — слушатель, `responder` — собеседник, как `lane` в §2.2) и
провайдеру, со временем последнего использования (`session_id` /
`set_session_id`, `None` — забыть). Файл пишется атомарно (временный +
замена) под тем же замком, что и журнал; нет файла или он битый — «сеанса
нет». Затравка из журнала (`context(budget)`) — **только запасной путь**:
локальная модель, сеанс истёк или пропал, у провайдера нет продолжения.

Строка журнала — один JSON-объект:

    {"v":1,"seq":57,"at":1760000000.12,"rec":"msg","id":"m31","kind":"agent",…поля}
    {"v":1,"seq":58,"at":…,"rec":"patch","id":"m31","set":{"voiced":{…}}}

- `msg` — новое сообщение (`KINDS`), `patch` — правка его полей (`set`
  сливается поверх сообщения: ключ за ключом, `null` — тоже значение).
- `seq` растёт монотонно на весь журнал. Id: `a<N>` у вложений
  (`kind: attachment`), `m<N>` у остальных — счётчики свои.
- `at` — стенное время записи, `t` (секунды записи) передаёт вызывающий.

Запись:
- межпроцессный замок `library.file_lock` с ключом `assistant/.chat.lock`.
  Сам файл замка по правилу `library` лежит во временной папке, а не в
  папке записи (не мешает удалению, не виден агенту). Под замком журнал
  сначала дочитывается (второй писатель мог дописать), потом пишется строка —
  поэтому писателей может быть два: ребёнок и задача резидента. Замок не
  взят за `library.META_FILE_LOCK_WAIT_S` — запись идёт без него (правило
  `library`: потерять запись хуже гонки);
- строка уходит одним `write` с `\\n`, затем `flush`; у `msg` ещё
  `os.fsync`, у `patch` — нет (их потеря некритична);
- если журнал кончается оборванной строкой (процесс убили посреди
  записи), перед новой строкой дописывается `\\n`: обрывок становится
  отдельной битой строкой, новая запись к нему не приклеивается. Файл не
  переписывается никогда.

Чтение (`load`, и дочитывание перед каждой операцией) не падает: битая
строка (не JSON, не объект, без `rec`/`id`/целого `seq`) пропускается,
считается (`stats()`) и пишется в журнал процесса; оборванная последняя
строка — так же; `patch` к неизвестному id пропускается; неизвестный `rec`
и `v` > 1 читаются по известным полям.

Идемпотентность: `append(..., client_id=…)` с уже записанным `client_id`
ничего не пишет и возвращает прежнее сообщение (`Appended.created` = False):
повтор `POST /chat` при переподключении не дублирует сообщение.

Решения там, где дизайн молчит (см. отчёт задачи):
- поля вложения: `type` (`image|doc|kb_note|past_meeting`), `name`,
  `status` (`parsing|ready|failed`), прочее (`vision`, `chars`, `summary`) —
  как есть;
- события встречи: `kind: meeting`, `event` (`voiced|session`), `text`,
  у `voiced` ещё `re` (id реплики агента);
- `patch` без изменений (те же значения) не пишется — `None`; `patch` к
  неизвестному id тоже `None`;
- в `assistant_chat.md` не попадают реплики агента `held` / `dropped` /
  `superseded` (их не было видно в чате); в затравке `context()` они есть
  с пометкой: агент должен знать, что хотел сказать и почему это не ушло.
  `meeting voiced` нет ни там, ни там: «озвучено» — отметка у самой реплики
  (её `patch voiced`);
- `context()` отдаёт только часть «журнал» затравки (§3.5); шапку, материалы,
  память встречи и реплики собирает `prompts.build_participant_seed`;
- `context(budget)` — бюджет в символах (~3 символа на токен);
  результат никогда не длиннее бюджета.

Модуль — только stdlib и `meet.library`: его читает и задача резидента.
"""

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from meet import library

CHAT_DIR = "assistant"
CHAT_JSONL = "chat.jsonl"
SESSIONS_JSON = "sessions.json"
LOCK_NAME = ".chat.lock"
ASSISTANT_CHAT_MD = "assistant_chat.md"
VERSION = 1

KINDS = ("agent", "user", "attachment", "meeting", "system")
HEADS = ("listener", "responder")
# Реплики агента, которых в чате не было видно.
HIDDEN_STATUSES = ("held", "dropped", "superseded")
# Поля записи, которые ставит журнал: ни в `append(**fields)`, ни в `patch`.
RECORD_KEYS = ("v", "seq", "at", "rec")
PROTECTED = (*RECORD_KEYS, "id", "kind", "client_id")

RECENT_MESSAGES = 40          # затравка: столько последних — дословно
COMPRESSED_LINE_MAX = 160     # сжатая строка раньшего сообщения
VERBATIM_SHARE = 3            # одно дословное сообщение — не больше budget // 3
VERBATIM_MIN = 200
COUNTS_RESERVE = 240         # место под строку счётчиков раньшего

_ID = re.compile(r"^([a-z]+)(\d+)$")
_MODE = {"proactive": "сам", "reply": "ответ", "followup": "досказ", "ask_you": "«Вам вопрос»"}
_QUICK = {"more": "подробнее", "not_now": "не сейчас", "missed": "что я пропустил",
          "brief": "кратко", "reply": "что мне ответить"}
_FEEDBACK = {"more": "подробнее", "not_now": "не сейчас", "copied": "скопировал"}
_TYPE = {"image": "изображение", "doc": "документ", "kb_note": "заметка базы знаний",
         "past_meeting": "прошлая встреча"}
_STATUS = {"parsing": "разбирается", "ready": "готово", "failed": "не разобрано"}


class Appended(NamedTuple):
    """Итог `append`: сообщение (свёрнутое, копия) и событие для SSE `chat`
    (`{"seq","op":"add","message"}`), `None` — если `client_id` уже был."""
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
    return text[:max(limit - len(mark), 0)] + mark if limit > len(mark) else text[:limit]


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
    try:
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return (text + "\n").encode("utf-8")
    except UnicodeEncodeError:
        # Одиночные суррогаты (обрывок из буфера обмена): \\udXXX-экранами.
        text = json.dumps(record, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        return (text + "\n").encode("ascii")


class ChatLog:
    """Журнал сессии одной записи. Потокобезопасен; несколько экземпляров
    (в том числе в разных процессах) на одну папку пишут через общий замок.

    Свёртка в памяти дочитывается из файла перед каждой операцией — видны
    записи второго писателя."""

    def __init__(self, folder: Path, *, clock=time.time, log=None):
        self.folder = Path(folder)
        self.dir = chat_dir(self.folder)
        self.path = self.dir / CHAT_JSONL
        self.sessions_path = self.dir / SESSIONS_JSON
        self._clock = clock
        self._log = log or _print
        self._mem = threading.RLock()
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
        self._torn_logged: int | None = None
        self._stats = {"broken": 0, "orphan_patches": 0, "duplicates": 0, "unknown": 0}

    @contextmanager
    def _locked(self):
        with library.file_lock(self.dir / LOCK_NAME), self._mem:
            yield

    def _warn(self, text: str) -> None:
        try:
            self._log(f"{CHAT_DIR}/{CHAT_JSONL}: {text}")
        except Exception:
            pass

    def _sync(self) -> None:
        """Дочитать файл с последнего места. Только под замком."""
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
        self._tail = tail
        if tail and self._torn_logged != self._offset:
            self._torn_logged = self._offset
            self._warn(f"оборванная последняя строка ({len(tail)} байт с байта {self._offset}) "
                       "пропущена")

    def _broken(self, pos: int, why: str) -> None:
        self._stats["broken"] += 1
        self._warn(f"битая строка {self._line_no} (байт {pos}) пропущена: {why}")

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
        kind_of_rec, rid, seq = rec.get("rec"), rec.get("id"), rec.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(rid, str) or not rid:
            self._broken(pos, "нет seq или id")
            return
        if kind_of_rec == "msg":
            if not isinstance(rec.get("kind"), str):
                self._broken(pos, "нет kind")
                return
            self._seq = max(self._seq, seq)
            self._add(rec)
        elif kind_of_rec == "patch":
            changes = rec.get("set")
            if not isinstance(changes, dict):
                self._broken(pos, "patch без set")
                return
            self._seq = max(self._seq, seq)
            msg = self._view.get(rid)
            if msg is None:
                self._stats["orphan_patches"] += 1
                self._warn(f"patch к неизвестному {rid} (строка {self._line_no}) пропущен")
                return
            for key, value in changes.items():
                if key not in PROTECTED:
                    msg[key] = value
        else:
            # Запись будущей версии: seq учитываем, содержимое — нет.
            self._seq = max(self._seq, seq)
            self._stats["unknown"] += 1

    def _add(self, rec: dict) -> None:
        rid = rec["id"]
        if rid in self._view:
            self._stats["duplicates"] += 1
            self._warn(f"повтор id {rid} (строка {self._line_no}) пропущен")
            return
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
        if self._tail:
            # Оборванная строка убитого писателя: закрываем её, а не клеим к ней.
            data = b"\n" + data
        with open(self.path, "ab") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())

    # --- чтение ---

    def load(self) -> list[dict]:
        """Перечитать журнал целиком: сообщения, следующий `seq` и id."""
        with self._locked():
            self._reset()
            self._sync()
            return self._copy()

    def _copy(self) -> list[dict]:
        return [json.loads(json.dumps(self._view[rid])) for rid in self._order]

    def messages(self) -> list[dict]:
        """Свёртка (копии), в порядке записи. Все виды и статусы — что
        показывать, решает потребитель (`HIDDEN_STATUSES`)."""
        with self._locked():
            self._sync()
            return self._copy()

    def snapshot(self, limit: int | None = None) -> dict:
        """`{"messages":[…],"seq":N}` — для `GET /chat` и `chat_snapshot`."""
        with self._locked():
            self._sync()
            msgs = self._copy()
            if limit is not None:
                msgs = msgs[-limit:] if limit > 0 else []
            return {"messages": msgs, "seq": self._seq}

    def get(self, mid: str) -> dict | None:
        with self._locked():
            self._sync()
            msg = self._view.get(mid)
            return json.loads(json.dumps(msg)) if msg is not None else None

    def by_client_id(self, client_id: str) -> dict | None:
        with self._locked():
            self._sync()
            rid = self._by_client.get(client_id)
            return json.loads(json.dumps(self._view[rid])) if rid else None

    @property
    def seq(self) -> int:
        """Последний `seq` (дочитав файл)."""
        with self._locked():
            self._sync()
            return self._seq

    def stats(self) -> dict:
        """Сколько пропущено при чтении: битые строки (и оборванный хвост —
        `torn_tail`), patch к неизвестному id, повторы id, неизвестные rec."""
        with self._locked():
            self._sync()
            return {**self._stats, "torn_tail": bool(self._tail)}

    # --- запись ---

    def append(self, kind: str, /, *, client_id: str | None = None, **fields) -> Appended:
        """Новое сообщение (`rec: msg`, с fsync). `client_id` — идемпотентность:
        уже записанный возвращает прежнее сообщение без записи."""
        if kind not in KINDS:
            raise ValueError(f"неизвестный вид сообщения: {kind!r}")
        bad = [k for k in fields if k in PROTECTED]
        if bad:
            raise ValueError(f"поля ставит журнал: {', '.join(bad)}")
        if client_id is not None and (not isinstance(client_id, str) or not client_id):
            raise ValueError("client_id — непустая строка")
        with self._locked():
            self._sync()
            if client_id and client_id in self._by_client:
                rid = self._by_client[client_id]
                return Appended(json.loads(json.dumps(self._view[rid])), None)
            prefix = "a" if kind == "attachment" else "m"
            rid = f"{prefix}{self._counters.get(prefix, 0) + 1}"
            seq = self._seq + 1
            rec = {"v": VERSION, "seq": seq, "at": round(float(self._clock()), 3),
                   "rec": "msg", "id": rid, "kind": kind}
            if client_id:
                rec["client_id"] = client_id
            rec.update(fields)
            self._write(_encode(rec), fsync=True)
            self._sync()
            msg = self._view.get(rid)
            message = json.loads(json.dumps(msg if msg is not None else
                                            {k: v for k, v in rec.items() if k not in ("v", "rec")}))
            return Appended(message, {"seq": seq, "op": "add", "message": message})

    def patch(self, mid: str, changes: dict) -> dict | None:
        """Правка полей сообщения (`rec: patch`, без fsync). Пишется только
        то, что меняется; ничего или неизвестный id — `None`. Иначе событие
        SSE `chat`: `{"seq","op":"patch","id","set"}`."""
        if not isinstance(changes, dict):
            raise ValueError("changes — словарь полей")
        bad = [k for k in changes if k in PROTECTED]
        if bad:
            raise ValueError(f"эти поля не правятся: {', '.join(bad)}")
        with self._locked():
            self._sync()
            msg = self._view.get(mid)
            if msg is None:
                return None
            missing = object()
            diff = {k: v for k, v in changes.items() if msg.get(k, missing) != v}
            if not diff:
                return None
            seq = self._seq + 1
            rec = {"v": VERSION, "seq": seq, "at": round(float(self._clock()), 3),
                   "rec": "patch", "id": mid, "set": diff}
            data = _encode(rec)
            self._write(data, fsync=False)
            self._sync()
            return {"seq": seq, "op": "patch", "id": mid, "set": json.loads(json.dumps(diff))}

    # --- сеансы провайдеров (родное продолжение контекста) ---

    def _read_sessions(self) -> dict:
        """`{"v":1,"heads":{head:{provider:{"id","used_at"}}}}`; нет файла или
        он битый — пустой (то есть «сеанса нет»)."""
        try:
            data = json.loads(self.sessions_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeDecodeError) as e:
            self._warn(f"{SESSIONS_JSON} не читается ({e.__class__.__name__}) — сеансов нет")
            return {}
        heads = data.get("heads") if isinstance(data, dict) else None
        if not isinstance(heads, dict):
            return {}
        out: dict = {}
        for head, providers in heads.items():
            if not isinstance(providers, dict):
                continue
            for provider, entry in providers.items():
                if (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                        and entry["id"]):
                    out.setdefault(head, {})[provider] = {
                        "id": entry["id"], "used_at": _num(entry.get("used_at"))}
        return out

    def session_id(self, head: str, provider: str) -> str | None:
        """Id сеанса провайдера для головы — для `--resume` и т. п.; нет — None."""
        entry = self._read_sessions().get(head, {}).get(provider)
        return entry["id"] if entry else None

    def sessions(self) -> dict:
        """Все сохранённые сеансы: `{head: {provider: {"id","used_at"}}}`."""
        return self._read_sessions()

    def set_session_id(self, head: str, provider: str, session_id: str | None) -> None:
        """Запомнить (и отметить время использования) или забыть (`None`) сеанс.
        Атомарно, под замком журнала."""
        if not isinstance(head, str) or not head or not isinstance(provider, str) or not provider:
            raise ValueError("head и provider — непустые строки")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("session_id — непустая строка или None")
        with self._locked():
            heads = self._read_sessions()
            if session_id is None:
                if provider not in heads.get(head, {}):
                    return
                del heads[head][provider]
                if not heads[head]:
                    del heads[head]
            else:
                heads.setdefault(head, {})[provider] = {
                    "id": session_id, "used_at": round(float(self._clock()), 3)}
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"{SESSIONS_JSON}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"v": VERSION, "heads": heads}, f, ensure_ascii=False, indent=1)
                    f.flush()
                    os.fsync(f.fileno())
                library._replace(tmp, self.sessions_path)
            finally:
                tmp.unlink(missing_ok=True)

    # --- затравка (запасной путь) ---

    def context(self, budget: int, *, recent: int = RECENT_MESSAGES) -> str:
        """Журнал для затравки головы — **запасной путь**, когда родного
        продолжения сеанса нет (локальная модель, сеанс истёк, провайдер без
        resume). Последние `recent` сообщений — дословно (вложения —
        подписью; одно сообщение — не больше трети бюджета), раньше — по
        строке, а что не уместилось — счётчиками. `budget` — символы; длина
        результата никогда его не превышает. Порядок — хронологический."""
        if budget <= 0:
            return ""
        everything = self.messages()
        view = {m["id"]: m for m in everything}
        msgs = [m for m in everything
                if not (m.get("kind") == "meeting" and m.get("event") == "voiced")]
        if not msgs:
            return ""
        split = max(len(msgs) - max(recent, 0), 0)
        cap = max(budget // VERBATIM_SHARE, VERBATIM_MIN)
        verbatim_title = "Последние сообщения чата — дословно:"
        verbatim: list[str] = []
        size = len(verbatim_title) + 1
        first_verbatim = len(msgs)
        for i in range(len(msgs) - 1, split - 1, -1):
            block = _cut(self._verbatim(msgs[i], view), cap)
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

    def _attachment_names(self, ids, view: dict) -> str:
        if not isinstance(ids, list):
            return ""
        names = []
        for aid in ids:
            a = view.get(aid) if isinstance(aid, str) else None
            label = _one_line((a or {}).get("name") or "", 80)
            names.append(f"{aid} «{label}»" if label else str(aid))
        return ", ".join(names)

    def _verbatim(self, msg: dict, view: dict) -> str:
        kind = msg.get("kind")
        stamp = f"[{msg['id']} · {_when(msg)}]"
        text = _unfence(msg.get("text") or "").strip()
        if kind == "agent":
            mode = _MODE.get(msg.get("mode"), msg.get("mode") or "")
            re_ = f", к {msg['re']}" if isinstance(msg.get("re"), str) else ""
            lines = [f"{stamp} Ты писал ({mode}{re_}): {text}" if mode or re_
                     else f"{stamp} Ты писал: {text}"]
            if msg.get("say"):
                lines.append(f"  Сказать: «{_one_line(msg['say'], 400)}»")
            if msg.get("reply"):
                lines.append(f"  Черновик ответа: «{_one_line(msg['reply'], 400)}»")
            status = msg.get("status")
            if status in HIDDEN_STATUSES or status in ("cancelled", "dismissed"):
                lines.append(f"  [в чате: {status}]")
            voiced = msg.get("voiced")
            if isinstance(voiced, dict):
                vt = _num(voiced.get("t"))
                line = _one_line(voiced.get("line") or "", 200)
                lines.append(f"  [Озвучено{' [' + _clock(vt) + ']' if vt is not None else ''}"
                             f"{': «' + line + '»' if line else ''}]")
            if msg.get("feedback"):
                lines.append(f"  [Отклик: {_FEEDBACK.get(msg['feedback'], msg['feedback'])}]")
            if msg.get("error"):
                lines.append(f"  [Ошибка: {_one_line(msg['error'], 200)}]")
            return "\n".join(lines)
        if kind == "user":
            extra = []
            if msg.get("quick"):
                extra.append(f"быстрый ответ: {_QUICK.get(msg['quick'], msg['quick'])}")
            if isinstance(msg.get("re"), str):
                extra.append(f"к {msg['re']}")
            note = f" ({', '.join(extra)})" if extra else ""
            lines = [f"{stamp} Ты получил сообщение{note}: {text}"]
            names = self._attachment_names(msg.get("attachments"), view)
            if names:
                lines.append(f"  Вложения: {names}")
            return "\n".join(lines)
        if kind == "attachment":
            return f"{stamp} {self._caption(msg)}"
        if kind == "meeting":
            what = text or msg.get("event") or "событие"
            return f"{stamp} Встреча: {_one_line(what, 400)}"
        return f"{stamp} Система: {_one_line(text, 400)}"

    def _compressed(self, msg: dict) -> str:
        kind = msg.get("kind")
        who = {"agent": "ты", "user": "человек", "meeting": "встреча",
               "system": "система"}.get(kind, kind)
        if kind == "attachment":
            return _one_line(f"{msg['id']} {_when(msg)}: {self._caption(msg)}", COMPRESSED_LINE_MAX)
        else:
            body = msg.get("text") or msg.get("event") or ""
            marks = []
            if kind == "agent":
                if msg.get("voiced"):
                    marks.append("озвучено")
                if msg.get("feedback") == "not_now" or msg.get("status") == "dismissed":
                    marks.append("отклонено")
                if msg.get("status") in HIDDEN_STATUSES:
                    marks.append(msg["status"])
            if marks:
                body = f"({', '.join(marks)}) {body}"
        return _one_line(f"{msg['id']} {_when(msg)} {who}: {body}", COMPRESSED_LINE_MAX)

    # --- assistant_chat.md ---

    def render_md(self, title: str | None = None) -> str:
        """Переписка в читаемом виде (для вкладки «Агент», `agent_context`).
        Только то, что было видно в чате: без `held`/`dropped`/`superseded`."""
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
                if m.get("status") in HIDDEN_STATUSES:
                    continue
                mode = " · «Вам вопрос»" if m.get("mode") == "ask_you" else ""
                block = [f"**Ассистент**{mode} · {when} · {m['id']}", "",
                         (m.get("text") or "").strip()]
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
                if m.get("error"):
                    notes.append(f"ошибка: {_one_line(m['error'], 200)}")
                if notes:
                    block += ["", f"_{' · '.join(notes)}_"]
            elif kind == "user":
                quick = f" · {_QUICK.get(m['quick'], m['quick'])}" if m.get("quick") else ""
                block = [f"**Вы**{quick} · {when} · {m['id']}", "", (m.get("text") or "").strip()]
                ids = m.get("attachments") if isinstance(m.get("attachments"), list) else []
                names = [_one_line((view.get(a) or {}).get("name") or a, 120)
                         for a in ids if isinstance(a, str)]
                if names:
                    block += ["", "Вложения: " + ", ".join(f"«{n}»" for n in names)]
            elif kind == "attachment":
                block = [f"_{self._caption(m)} · {when} · {m['id']}_"]
            elif kind == "meeting":
                if m.get("event") == "voiced":
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
        """Записать `assistant_chat.md` атомарно (по умолчанию — в папку записи)."""
        dest = Path(dest) if dest is not None else self.folder / ASSISTANT_CHAT_MD
        text = self.render_md(title)
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8", newline="\n")
            library._replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest
