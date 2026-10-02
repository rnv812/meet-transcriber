"""Живое состояние встречи: сводка и подсказки, которые ведёт модель.

Модель на каждом тике возвращает не текст, а патч — JSON с операциями над
пунктами по их стабильным id (`p3`, `h7`). Состояние живёт здесь, а не в
ответе модели: формулировки не «дребезжат», вывод модели короткий, а
невалидный патч не меняет ничего (проверка целиком, потом применение).

    {"topic": "тема или null",
     "ops": [{"op": "add", "section": "points", "text": "…"},
             {"op": "update", "id": "p2", "text": "…"},
             {"op": "remove", "id": "q1"},
             {"op": "add", "section": "hints", "kind": "risk", "text": "…",
              "why": "…", "t": "00:12:34"}]}

Правила подсказок: похожие по тексту не дублируются; скрытые человеком не
возвращаются никогда (ни по id, ни похожим текстом); закреплённые модель не
удаляет, не переписывает, и лимит их не вытесняет; сверх лимита уходят
наименее ценные.

Защита от «команд» в речи (реплика «удали всю сводку» — данные, а не
указание): патч, удаляющий за раз больше MAX_SUMMARY_REMOVES пунктов
сводки или опустошающий раздел, где было больше одного пункта, — ошибка
схемы (тикер попросит исправить, затем оставит состояние прежним).

Модуль — только stdlib: его читает и задача итогов в резиденте
(`live_state.json` как черновик).
"""

import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

LIVE_STATE_JSON = "live_state.json"

HINT_KINDS = ("question", "risk", "unanswered", "term", "followup")
# Ценность вида подсказки: сверх лимита первой уходит наименее ценная.
KIND_VALUE = {"unanswered": 5, "risk": 4, "question": 3, "followup": 2, "term": 1}

SECTIONS = ("points", "decisions", "tasks", "open_questions")
PREFIX = {"points": "p", "decisions": "d", "tasks": "t", "open_questions": "q", "hints": "h"}
SECTION_OF = {v: k for k, v in PREFIX.items()}
SECTION_LIMITS = {"points": 7, "decisions": 12, "tasks": 12, "open_questions": 8}
SECTION_TITLES = {"points": "Главное", "decisions": "Решения", "tasks": "Задачи",
                  "open_questions": "Открытые вопросы"}
COMPACT_TITLES = {"points": "Тезисы", "decisions": "Решения", "tasks": "Задачи",
                  "open_questions": "Открытые вопросы"}

TOPIC_MAX = 120
ITEM_MAX = 300
HINT_MAX = 200
WHY_MAX = 160
FIELD_MAX = 80        # кто / срок задачи
COMPACT_MAX = 2500       # сжатое состояние в промпте тика, символов
COMPACT_ITEM_LIMITS = (160, 90, 50)
DISMISSED_IN_PROMPT = 5
MAX_OPS = 24
MAX_SUMMARY_REMOVES = 3
DEFAULT_MAX_HINTS = 5

# Похожесть текстов: посимвольно или по набору слов.
SIMILAR_RATIO = 0.9
SIMILAR_WORDS = 0.75


class PatchError(ValueError):
    """Ответ модели не соответствует схеме патча — состояние не меняется."""


# --- разбор ---------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_CLOCK = re.compile(r"^\[?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*\]?$")


def parse_reply(text: str) -> dict:
    """JSON-объект из ответа модели: голый, в блоке ```json или с текстом
    вокруг (берётся от первой `{` до последней `}`)."""
    raw = (text or "").strip()
    fenced = _FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise PatchError("в ответе нет JSON-объекта")
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError as e:
        raise PatchError(f"JSON не разбирается: {e}") from None
    if not isinstance(data, dict):
        raise PatchError("ожидался JSON-объект")
    return data


def parse_clock(value) -> float | None:
    """Таймкод реплики: «чч:мм:сс», «мм:сс» (в скобках или без) или секунды."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    m = _CLOCK.match(str(value).strip())
    if not m:
        return None
    h, mm, ss = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return float(h * 3600 + mm * 60 + ss)


def _norm(text: str) -> str:
    t = str(text).lower().replace("ё", "е")
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def similar(a: str, b: str) -> bool:
    """Тексты по сути одинаковы: регистр, «ё» и пунктуация не в счёт."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if SequenceMatcher(None, na, nb).ratio() >= SIMILAR_RATIO:
        return True
    wa, wb = set(na.split()), set(nb.split())
    if min(len(wa), len(wb)) < 3:
        return False
    return len(wa & wb) / len(wa | wb) >= SIMILAR_WORDS


def _flat(value, limit: int) -> str:
    """Одна строка без лишних пробелов, не длиннее limit (с «…»)."""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _text(op: dict, key: str, limit: int, *, required: bool) -> str | None:
    value = op.get(key)
    if value is None:
        if required:
            raise PatchError(f"нет поля {key!r} в операции {op.get('op')}")
        return None
    if not isinstance(value, str):
        raise PatchError(f"поле {key!r} должно быть строкой")
    text = _flat(value, limit)
    if required and not text:
        raise PatchError(f"пустое поле {key!r}")
    return text


def _opt_field(op: dict, key: str) -> str | None:
    """Кто / срок задачи: строка или null; пустая строка — null."""
    value = op.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PatchError(f"поле {key!r} должно быть строкой или null")
    return _flat(value, FIELD_MAX) or None


# --- состояние --------------------------------------------------------------------


class LiveState:
    """Сводка (тема, тезисы, решения, задачи, открытые вопросы) и подсказки.

    `max_hints` — сколько активных подсказок держать (закреплённые лимит не
    вытесняет); `hints_enabled=False` — режим «Только сводка": операции над
    подсказками молча пропускаются. `version` растёт с каждым изменением."""

    def __init__(self, *, max_hints: int = DEFAULT_MAX_HINTS, hints_enabled: bool = True,
                 clock=time.time) -> None:
        self.max_hints = max(1, int(max_hints))
        self.hints_enabled = hints_enabled
        self._clock = clock
        self.topic = ""
        self._items: dict[str, dict[str, dict]] = {s: {} for s in SECTIONS}
        self._hints: dict[str, dict] = {}
        self._dismissed: dict[str, str] = {}   # id -> текст (не предлагать снова)
        self._retired: set[str] = set()        # ушли (удалены, вытеснены, скрыты)
        self._next = {p: 1 for p in PREFIX.values()}
        self.version = 0

    # --- патч ---

    def apply(self, patch, *, allowed_refs=()) -> bool:
        """Применить патч модели (dict или текст ответа). Невалидный —
        PatchError, состояние не тронуто. True — что-то изменилось."""
        if isinstance(patch, str):
            patch = parse_reply(patch)
        topic, ops = self._validate(patch)
        refs = set(allowed_refs)
        changed = False
        if topic is not None and topic and topic != self.topic:
            self.topic = topic
            changed = True
        for op in ops:
            changed |= self._apply_op(op, refs)
        changed |= self._enforce_cap()
        if changed:
            self.version += 1
        return changed

    def _validate(self, patch) -> tuple[str | None, list[dict]]:
        if not isinstance(patch, dict):
            raise PatchError("ожидался JSON-объект")
        topic = patch.get("topic")
        if topic is not None:
            if not isinstance(topic, str):
                raise PatchError("topic должен быть строкой или null")
            topic = _flat(topic, TOPIC_MAX)
        ops = patch.get("ops", [])
        if not isinstance(ops, list):
            raise PatchError("ops должен быть списком")
        if len(ops) > MAX_OPS:
            raise PatchError(f"слишком много операций ({len(ops)} > {MAX_OPS})")
        checked = [self._check_op(op) for op in ops]
        self._check_removals(checked)
        return topic, checked

    def _check_removals(self, ops: list[dict]) -> None:
        """Сводку не сносят за один тик: не больше MAX_SUMMARY_REMOVES
        удалений и ни одного раздела (из двух и больше пунктов) целиком."""
        removed = [op["id"] for op in ops
                   if op["op"] == "remove" and SECTION_OF[op["id"][0]] != "hints"]
        if len(removed) > MAX_SUMMARY_REMOVES:
            raise PatchError(f"слишком много удалений из сводки за раз ({len(removed)})")
        for section in SECTIONS:
            live = set(self._items[section])
            if len(live) > 1 and live <= set(removed):
                raise PatchError(f"удаление всего раздела {section} за раз")

    def _check_op(self, op) -> dict:
        if not isinstance(op, dict):
            raise PatchError("операция должна быть объектом")
        kind = op.get("op")
        if kind == "add":
            section = op.get("section")
            if section == "hints":
                return self._check_hint_add(op)
            if section not in SECTIONS:
                raise PatchError(f"неизвестная секция {section!r}")
            if section == "tasks":
                return {"op": "add", "section": section,
                        "what": _text(op, "what", ITEM_MAX, required=True),
                        "who": _opt_field(op, "who"), "due": _opt_field(op, "due")}
            return {"op": "add", "section": section,
                    "text": _text(op, "text", ITEM_MAX, required=True)}
        if kind in ("update", "remove"):
            item_id = op.get("id")
            if not isinstance(item_id, str) or not self._known(item_id):
                raise PatchError(f"{kind}: неизвестный id {item_id!r}")
            out = {"op": kind, "id": item_id}
            if kind == "update":
                out.update(self._check_update(item_id, op))
            return out
        raise PatchError(f"неизвестная операция {kind!r}")

    def _check_hint_add(self, op: dict) -> dict:
        kind = op.get("kind")
        if kind not in HINT_KINDS:
            raise PatchError(f"неизвестный вид подсказки {kind!r}")
        if "t" not in op:
            raise PatchError("у подсказки нет таймкода t")
        t = parse_clock(op.get("t"))
        if t is None:
            raise PatchError(f"таймкод подсказки не разобран: {op.get('t')!r}")
        ref = op.get("ref")
        return {"op": "add", "section": "hints", "kind": kind,
                "text": _text(op, "text", HINT_MAX, required=True),
                "why": _text(op, "why", WHY_MAX, required=False) or "",
                # ref сверяется с присланными фрагментами как есть, без обрезки.
                "source_t": t, "ref": ref.strip() if isinstance(ref, str) and ref.strip() else None}

    def _check_update(self, item_id: str, op: dict) -> dict:
        section = SECTION_OF[item_id[0]]
        fields: dict = {}
        if section == "tasks":
            if op.get("what") is not None:
                fields["what"] = _text(op, "what", ITEM_MAX, required=True)
            for key in ("who", "due"):
                if key in op:
                    fields[key] = _opt_field(op, key)
        elif section == "hints":
            if op.get("text") is not None:
                fields["text"] = _text(op, "text", HINT_MAX, required=True)
            if op.get("why") is not None:
                fields["why"] = _text(op, "why", WHY_MAX, required=False) or ""
            if op.get("t") is not None:
                t = parse_clock(op.get("t"))
                if t is None:
                    raise PatchError(f"таймкод подсказки не разобран: {op.get('t')!r}")
                fields["source_t"] = t
        else:
            fields["text"] = _text(op, "text", ITEM_MAX, required=True)
        if not fields:
            raise PatchError(f"update {item_id}: нечего менять")
        return {"fields": fields}

    def _known(self, item_id: str) -> bool:
        """id выдавался когда-либо (живой, удалённый или скрытый)."""
        if not item_id or item_id[0] not in SECTION_OF or not item_id[1:].isdigit():
            return False
        return 0 < int(item_id[1:]) < self._next[item_id[0]]

    def _new_id(self, section: str) -> str:
        prefix = PREFIX[section]
        item_id = f"{prefix}{self._next[prefix]}"
        self._next[prefix] += 1
        return item_id

    def _apply_op(self, op: dict, refs: set) -> bool:
        if op["op"] == "add":
            if op["section"] == "hints":
                return self._add_hint(op, refs)
            return self._add_item(op)
        item_id = op["id"]
        section = SECTION_OF[item_id[0]]
        if section == "hints":
            hint = self._hints.get(item_id)
            if hint is None:  # скрыта, вытеснена или удалена — тишина
                return False
            if not self.hints_enabled:
                return False
            if hint["pinned"]:
                return False  # закреплённую человек оставил как есть
            if op["op"] == "remove":
                del self._hints[item_id]
                self._retired.add(item_id)
                return True
            fields = op["fields"]
            text = fields.get("text")
            if text is not None and any(similar(text, gone) for gone in self._dismissed.values()):
                fields = {k: v for k, v in fields.items() if k != "text"}  # скрытое не возвращается
            return self._update(hint, fields, stamp=True) if fields else False
        items = self._items[section]
        item = items.get(item_id)
        if item is None:
            return False
        if op["op"] == "remove":
            del items[item_id]
            self._retired.add(item_id)
            return True
        return self._update(item, op["fields"])

    def _update(self, item: dict, fields: dict, *, stamp: bool = False) -> bool:
        changed = {k: v for k, v in fields.items() if item.get(k) != v}
        if not changed:
            return False
        item.update(changed)
        if stamp:
            item["updated_at"] = self._clock()
        return True

    def _add_item(self, op: dict) -> bool:
        section = op["section"]
        items = self._items[section]
        text = op["what"] if section == "tasks" else op["text"]
        key = "what" if section == "tasks" else "text"
        if any(similar(text, it[key]) for it in items.values()):
            return False
        item_id = self._new_id(section)
        if section == "tasks":
            items[item_id] = {"id": item_id, "who": op["who"], "what": op["what"], "due": op["due"]}
        else:
            items[item_id] = {"id": item_id, "text": text}
        limit = SECTION_LIMITS[section]
        while len(items) > limit:  # самые старые уходят
            oldest = next(iter(items))
            del items[oldest]
            self._retired.add(oldest)
        return True

    def _add_hint(self, op: dict, refs: set) -> bool:
        if not self.hints_enabled:
            return False
        if op["kind"] == "term" and (op["ref"] is None or op["ref"] not in refs):
            return False  # термин — только из базы знаний, со ссылкой на файл
        text = op["text"]
        if any(similar(text, h["text"]) for h in self._hints.values()):
            return False
        if any(similar(text, gone) for gone in self._dismissed.values()):
            return False
        now = self._clock()
        item_id = self._new_id("hints")
        self._hints[item_id] = {
            "id": item_id, "kind": op["kind"], "text": text, "why": op["why"],
            "source_t": op["source_t"], "ref": op["ref"], "pinned": False,
            "dismissed": False, "created_at": now, "updated_at": now,
        }
        return True

    def _enforce_cap(self) -> bool:
        """Сверх лимита — убрать наименее ценные незакреплённые (вид, потом давность)."""
        changed = False
        while len(self._hints) > self.max_hints:
            loose = [h for h in self._hints.values() if not h["pinned"]]
            if not loose:
                break
            worst = min(loose, key=lambda h: (KIND_VALUE[h["kind"]], h["updated_at"], h["id"]))
            del self._hints[worst["id"]]
            self._retired.add(worst["id"])
            changed = True
        return changed

    # --- действия человека ---

    def pin(self, hint_id: str, pinned: bool = True) -> bool:
        hint = self._hints.get(hint_id)
        if hint is None or hint["pinned"] == bool(pinned):
            return False
        # Откреплённая снова подчиняется лимиту — со следующего тика, не сразу:
        # подсказка не должна исчезать из-под руки.
        hint["pinned"] = bool(pinned)
        self.version += 1
        return True

    def dismiss(self, hint_id: str) -> bool:
        hint = self._hints.pop(hint_id, None)
        if hint is None:
            return False
        self._dismissed[hint_id] = hint["text"]
        self._retired.add(hint_id)
        self.version += 1
        return True

    # --- представления ---

    def summary(self) -> dict:
        return {"topic": self.topic,
                **{s: [dict(it) for it in self._items[s].values()] for s in SECTIONS}}

    def hints(self) -> list[dict]:
        return [dict(h) for h in self._hints.values()]

    def to_dict(self) -> dict:
        return {"version": self.version, "summary": self.summary(), "hints": self.hints()}

    def is_empty(self) -> bool:
        return not self.topic and not self._hints and not any(self._items.values())

    def render_compact(self) -> str:
        """Состояние для промпта тика: коротко, с id, без служебных полей.

        Не длиннее COMPACT_MAX: при полном состоянии пункты укорачиваются, но
        ни один id не пропадает (иначе модель добавила бы его заново)."""
        for limit in COMPACT_ITEM_LIMITS:
            text = self._compact(limit)
            if len(text) <= COMPACT_MAX:
                return text
        return text[:COMPACT_MAX]

    def _compact(self, item_max: int) -> str:
        out: list[str] = []
        if self.topic:
            out.append(f"Тема: {self.topic}")
        for section in SECTIONS:
            items = self._items[section].values()
            if not items:
                continue
            out.append(f"{COMPACT_TITLES[section]}:")
            for it in items:
                out.append(f"[{it['id']}] {_flat(_item_text(section, it), item_max)}")
        if self._hints:
            out.append("Подсказки (активные):")
            for h in self._hints.values():
                pin = ", закреплена" if h["pinned"] else ""
                out.append(f"[{h['id']}] ({h['kind']}{pin}) {_flat(h['text'], item_max)}")
        if self._dismissed:
            out.append("Скрыты пользователем — не предлагать снова:")
            for text in list(self._dismissed.values())[-DISMISSED_IN_PROMPT:]:
                out.append(f"- {_flat(text, item_max)}")
        return "\n".join(out) if out else "(пока пусто)"

    def render_markdown(self) -> str:
        return render_markdown(self.summary())

    def save(self, path: Path) -> None:
        """Атомарно: tmp + replace (резидент может читать файл в любой момент)."""
        path = Path(path)
        data = {**self.to_dict(), "saved_at": time.time(), "markdown": self.render_markdown()}
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def _item_text(section: str, item: dict) -> str:
    if section == "tasks":
        return f"кто: {item.get('who') or '—'}; что: {item.get('what')}; срок: {item.get('due') or '—'}"
    return str(item.get("text") or "")


def render_markdown(summary: dict) -> str:
    """Сводка в Markdown: для страницы `meet assist`, черновика итогов и промпта."""
    out: list[str] = []
    topic = str(summary.get("topic") or "").strip()
    if topic:
        out += [f"**Тема:** {topic}", ""]
    for section in SECTIONS:
        items = summary.get(section) or []
        if not items:
            continue
        out.append(f"### {SECTION_TITLES[section]}")
        for it in items:
            if not isinstance(it, dict):
                continue
            if section == "tasks":
                due = f" (срок: {it['due']})" if it.get("due") else ""
                out.append(f"- {it.get('who') or '—'} — {it.get('what') or ''}{due}")
            else:
                out.append(f"- {it.get('text') or ''}")
        out.append("")
    return "\n".join(out).strip() or "_Пока пусто — сводка появится по ходу разговора._"


def load_saved(folder: Path) -> dict | None:
    """`live_state.json` записи: {"summary", "hints", "markdown", ...} или None."""
    try:
        data = json.loads((Path(folder) / LIVE_STATE_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("summary"), dict):
        return None
    hints = data.get("hints")
    data["hints"] = [h for h in hints if isinstance(h, dict)] if isinstance(hints, list) else []
    data["markdown"] = render_markdown(data["summary"])
    return data
