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

from meet import library

LIVE_STATE_JSON = "live_state.json"

HINT_KINDS = ("ask_you", "question", "risk", "unanswered", "term", "followup")
# «Вам вопрос»: вопрос или просьба, обращённые к владельцу, — с черновиком
# ответа (`reply`). Окно ставит такие подсказки первыми и выделяет.
URGENT = "ask_you"
MAX_URGENT = 2
# Ценность вида подсказки: сверх лимита первой уходит наименее ценная.
KIND_VALUE = {"ask_you": 6, "unanswered": 5, "risk": 4, "question": 3, "followup": 2, "term": 1}

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
REPLY_MAX = 240
BRIEF_TEXT_MAX = 120
# В тексте подсказки и черновике ответа (его копируют) — ни ссылок, ни команд:
# такое могло прийти только из речи (попытка подсунуть «ответ»).
_UNSAFE = re.compile(r"://|\bwww\.|powershell|\bcmd(\.exe)?\s*/c|\bbash\b|\bsudo\b|`", re.I)
FIELD_MAX = 80        # кто / срок задачи
COMPACT_MAX = 2500       # сжатое состояние в промпте тика, символов
COMPACT_ITEM_LIMITS = (160, 90, 50)
DISMISSED_IN_PROMPT = 5
MAX_OPS = 24
MAX_SUMMARY_REMOVES = 3
DEFAULT_MAX_HINTS = 5
# Сколько секунд после «Скрыть» подсказку можно вернуть («Вернуть» в панели —
# пять секунд; запас на задержку сети и окна).
RESTORE_S = 30.0
# Возвращённую подсказку столько секунд не убирают сами (лимит, «Вам вопрос»
# устарел): человек только что попросил её обратно. Раньше — его же действие.
RESTORED_HOLD_S = 60.0

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


def _safe(text: str | None) -> str | None:
    """Текст подсказки без ссылок и команд — иначе PatchError."""
    if text and _UNSAFE.search(text):
        raise PatchError("в подсказке ссылка или команда")
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
        # Скрытые недавно — целиком, для «Вернуть» в панели (RESTORE_S секунд).
        self._recent_dismissed: dict[str, tuple[float, dict]] = {}
        # Возвращённые «Вернуть»: id -> до какого времени их не убирать сами.
        self._held_until: dict[str, float] = {}
        self._retired: set[str] = set()        # ушли (удалены, вытеснены, скрыты)
        self._next = {p: 1 for p in PREFIX.values()}
        self.version = 0
        # До какого момента записи (секунды) сводка учла реплики: черновик для
        # итогов честно говорит, где кончается (остановка не ждёт тика сводки).
        self.covered_t: float | None = None
        # Ассистент, включённый посреди записи: с какой секунды он слышал
        # встречу (начало догонялки) и неполна ли сводка (выключили, пока
        # запись шла; начало не догнано) — черновик итогов говорит об этом.
        self.covered_from: float | None = None
        self.partial = False
        # Почему неполна (`detached`, `catchup_incomplete`, `capped`,
        # `late_start`; `tail_cut` ставит резидент — убил ассистента до его
        # финального прохода): следующее включение в эту запись чинит не всё.
        self.partial_reasons: list[str] = []
        # До какой секунды записи доходит уже услышанное (конец последней
        # реплики): следующее включение догоняет с этого места, без повтора.
        self.heard_t: float | None = None

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

    def session(self, *, lane: str | None = None, allowed_refs=()) -> "PatchSession":
        """Построчное применение ответа одного тика (см. PatchSession)."""
        return PatchSession(self, lane=lane, allowed_refs=allowed_refs)

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
                "text": _safe(_text(op, "text", HINT_MAX, required=True)),
                "why": _safe(_text(op, "why", WHY_MAX, required=False) or ""),
                "reply": _safe(_text(op, "reply", REPLY_MAX, required=False)) or None,
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
                fields["text"] = _safe(_text(op, "text", HINT_MAX, required=True))
            if op.get("why") is not None:
                fields["why"] = _safe(_text(op, "why", WHY_MAX, required=False) or "")
            if op.get("reply") is not None:
                fields["reply"] = _safe(_text(op, "reply", REPLY_MAX, required=False)) or None
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
        if op["kind"] == URGENT:
            # «Вам вопрос» — не больше MAX_URGENT: свежий вопрос вытесняет
            # самый старый незакреплённый.
            urgent = [h for h in self._hints.values() if h["kind"] == URGENT]
            loose = sorted((h for h in urgent if not self._held(h)),
                           key=lambda h: (h["created_at"], int(h["id"][1:])))
            while loose and len(urgent) >= MAX_URGENT:
                oldest = loose.pop(0)
                urgent.remove(oldest)
                del self._hints[oldest["id"]]
                self._retired.add(oldest["id"])
        item_id = self._new_id("hints")
        self._hints[item_id] = {
            "id": item_id, "kind": op["kind"], "text": text, "why": op["why"],
            "reply": op.get("reply"),
            "source_t": op["source_t"], "ref": op["ref"], "pinned": False,
            "dismissed": False, "created_at": now, "updated_at": now,
        }
        return True

    def _held(self, hint: dict) -> bool:
        """Подсказку не убирают сами: закреплена или только что возвращена."""
        return hint["pinned"] or self._held_until.get(hint["id"], 0.0) > self._clock()

    def _enforce_cap(self) -> bool:
        """Сверх лимита — убрать наименее ценные незакреплённые (вид, потом давность)."""
        changed = False
        while len(self._hints) > self.max_hints:
            loose = [h for h in self._hints.values() if not self._held(h)]
            if not loose:
                break
            worst = min(loose, key=lambda h: (KIND_VALUE[h["kind"]], h["updated_at"], h["id"]))
            del self._hints[worst["id"]]
            self._retired.add(worst["id"])
            changed = True
        return changed

    # --- действия человека ---

    def pin(self, hint_id: str, pinned: bool = True) -> bool:
        self._held_until.pop(hint_id, None)  # дальше решает само закрепление
        hint = self._hints.get(hint_id)
        if hint is None or hint["pinned"] == bool(pinned):
            return False
        # Откреплённая снова подчиняется лимиту — со следующего тика, не сразу:
        # подсказка не должна исчезать из-под руки.
        hint["pinned"] = bool(pinned)
        self.version += 1
        return True

    def dismiss(self, hint_id: str) -> bool:
        self._held_until.pop(hint_id, None)
        hint = self._hints.pop(hint_id, None)
        if hint is None:
            return False
        self._dismissed[hint_id] = hint["text"]
        self._retired.add(hint_id)
        now = self._clock()
        self._recent_dismissed = {k: v for k, v in self._recent_dismissed.items() if now - v[0] <= RESTORE_S}
        self._recent_dismissed[hint_id] = (now, hint)
        self.version += 1
        return True

    def restore(self, hint_id: str) -> bool:
        """«Вернуть» сразу после «Скрыть» (не позже RESTORE_S): подсказка снова
        на месте и снова может повторяться. Позже, или такой не скрывали, — False."""
        entry = self._recent_dismissed.pop(hint_id, None)
        if entry is None or self._clock() - entry[0] > RESTORE_S or hint_id in self._hints:
            return False
        self._dismissed.pop(hint_id, None)
        self._retired.discard(hint_id)
        self._hints[hint_id] = entry[1]
        self._held_until[hint_id] = self._clock() + RESTORED_HOLD_S
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

    def render_compact(self, *, summary: bool = True, hints: bool = True) -> str:
        """Состояние для промпта тика: коротко, с id, без служебных полей.
        `summary`/`hints` — какие части (линия сводки видит только сводку).

        Не длиннее COMPACT_MAX: при полном состоянии пункты укорачиваются, но
        ни один id не пропадает (иначе модель добавила бы его заново)."""
        for limit in COMPACT_ITEM_LIMITS:
            text = self._compact(limit, summary=summary, hints=hints)
            if len(text) <= COMPACT_MAX:
                return text
        return text[:COMPACT_MAX]

    def hints_brief(self) -> str:
        """Активные подсказки для каждого тика диалога: `id · вид · текст`
        (текст до BRIEF_TEXT_MAX). id выдаёт состояние, а не модель, — без
        этого списка модель не знала бы, какой id у её же подсказки, и не
        могла бы её уточнить или убрать. Плюс id скрытых человеком."""
        lines = ["Активные подсказки (id · вид · текст):"]
        for h in self._hints.values():
            pin = " (закреплена)" if h["pinned"] else ""
            lines.append(f"{h['id']} · {h['kind']}{pin} · {_flat(h['text'], BRIEF_TEXT_MAX)}")
        if len(lines) == 1:
            lines = ["Активных подсказок нет."]
        dismissed = sorted(self._dismissed, key=lambda i: int(i[1:]))[-DISMISSED_IN_PROMPT:]
        if dismissed:
            lines.append("Скрыты пользователем (не предлагать снова): " + ", ".join(dismissed))
        return "\n".join(lines)

    def resolve(self, hint_id: str) -> bool:
        """Убрать подсказку как отработанную («Вам вопрос», на который уже
        ответили или который устарел): не скрыта человеком, просто ушла.
        Закреплённую не трогаем."""
        hint = self._hints.get(hint_id)
        if hint is None or self._held(hint):
            return False
        del self._hints[hint_id]
        self._retired.add(hint_id)
        self.version += 1
        return True

    def age(self, hint: dict) -> float:
        """Сколько секунд подсказка на экране (по часам состояния)."""
        return max(0.0, self._clock() - float(hint.get("created_at") or 0.0))

    def urgent_expiry_in(self, max_age_s: float) -> float | None:
        """Через сколько секунд устареет ближайший незакреплённый «Вам вопрос»;
        None — таких нет."""
        left = [max_age_s - self.age(h) for h in self._hints.values()
                if h["kind"] == URGENT and not h["pinned"]]
        return min(left) if left else None

    def mark_covered(self, t: float) -> None:
        """Сводка учла реплики до момента `t` (секунды записи)."""
        if t is not None and (self.covered_t is None or t > self.covered_t):
            self.covered_t = float(t)

    def heard_from(self, t: float | None) -> None:
        """Ассистент слышит запись с секунды `t` (включён посреди неё)."""
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            self.covered_from = float(t) if self.covered_from is None \
                else min(self.covered_from, float(t))

    def resume(self, data: dict) -> bool:
        """Продолжить сохранённое состояние (`live_state.json` прошлого
        включения ассистента в этой же записи): тема, пункты, активные
        подсказки, граница учтённого. Новые id — после самых больших прежних.
        → False — восстанавливать нечего."""
        summary = data.get("summary") if isinstance(data, dict) else None
        if not isinstance(summary, dict):
            return False
        self.topic = _flat(summary.get("topic") or "", TOPIC_MAX)
        for section in SECTIONS:
            for item in summary.get(section) or []:
                if isinstance(item, dict) and _restorable(item.get("id"), PREFIX[section]):
                    self._items[section][item["id"]] = dict(item)
        for hint in data.get("hints") or []:
            if (isinstance(hint, dict) and _restorable(hint.get("id"), "h")
                    and hint.get("kind") in HINT_KINDS
                    and isinstance(hint.get("text"), str) and not hint.get("dismissed")):
                self._hints[hint["id"]] = {**hint, "pinned": bool(hint.get("pinned"))}
        ids = [*self._hints, *(k for items in self._items.values() for k in items)]
        for prefix in PREFIX.values():
            used = [int(i[1:]) for i in ids if i[0] == prefix]
            self._next[prefix] = max(used, default=0) + 1
        for key in ("covered_t", "covered_from", "heard_t"):
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(self, key, float(value))
        if self.covered_from is None:
            self.covered_from = 0.0  # без пометки — слышал запись с начала
        self.partial = bool(data.get("partial"))
        reasons = data.get("partial_reasons")
        self.partial_reasons = ([str(r) for r in reasons if isinstance(r, str)]
                                if isinstance(reasons, list) else [])
        if self.partial and not self.partial_reasons:
            self.partial_reasons = ["unknown"]
        self.version += 1
        return True

    def _compact(self, item_max: int, *, summary: bool = True, hints: bool = True) -> str:
        out: list[str] = []
        if summary and self.topic:
            out.append(f"Тема: {self.topic}")
        for section in SECTIONS if summary else ():
            items = self._items[section].values()
            if not items:
                continue
            out.append(f"{COMPACT_TITLES[section]}:")
            for it in items:
                out.append(f"[{it['id']}] {_flat(_item_text(section, it), item_max)}")
        if not hints:
            return "\n".join(out) if out else "(пока пусто)"
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
        return render_markdown(self.summary(), covered_t=self.covered_t,
                               covered_from=self.covered_from, partial=self.partial)

    def save(self, path: Path) -> None:
        """Атомарно: tmp + replace (резидент может читать файл в любой момент)."""
        path = Path(path)
        data = {**self.to_dict(), "saved_at": time.time(), "covered_t": self.covered_t,
                "markdown": self.render_markdown()}
        if self.covered_from is not None:
            data["covered_from"] = self.covered_from
        if self.heard_t is not None:
            data["heard_t"] = self.heard_t
        if self.partial:
            data["partial"] = True
            data["partial_reasons"] = list(self.partial_reasons)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            library.replace_atomic(tmp, path)  # читатель держит файл — повтор
        finally:
            tmp.unlink(missing_ok=True)


LANES = ("hints", "summary")


class PatchSession:
    """Ответ одного тика, применяемый по строке: одна JSON-операция — одна
    строка (`{"op": "add", ...}`), каждая проверенная строка меняет состояние
    сразу, как только модель её дописала (окно видит подсказку, не дожидаясь
    конца ответа). Невалидная строка — PatchError, состояние не тронуто; ей
    положена одна попытка исправления у тикера.

    `lane` — чья это линия: `hints` трогает только подсказки, `summary` —
    только сводку и тему (`{"op": "topic", "text": …}`); None — всё.
    `{"op": "none"}` — «ничего ценного», ничего не меняется. Старый формат
    (целый патч `{"topic", "ops"}` одной строкой) тоже принимается.

    Защита сводки — на весь ответ, а не на строку: не больше
    MAX_SUMMARY_REMOVES удалений и ни одного раздела (из двух и больше
    пунктов на начало тика) целиком; не больше MAX_OPS операций."""

    def __init__(self, state: LiveState, *, lane: str | None = None, allowed_refs=()) -> None:
        if lane is not None and lane not in LANES:
            raise ValueError(f"неизвестная линия: {lane}")
        self._state = state
        self._lane = lane
        self._refs = set(allowed_refs)
        self._ops = 0
        self._removed: list[str] = []
        self._start = {s: set(state._items[s]) for s in SECTIONS}
        self.applied = 0

    def apply(self, obj) -> bool:
        """Одна операция (dict). True — состояние изменилось."""
        if not isinstance(obj, dict):
            raise PatchError("строка должна быть JSON-объектом")
        if "op" not in obj and ("ops" in obj or "topic" in obj):
            return self._apply_patch(obj)
        kind = obj.get("op")
        if kind == "none":
            return False
        if kind == "topic":
            return self._topic(obj.get("text"))
        if self._lane == "hints" and kind == "add" and "section" not in obj:
            obj = {**obj, "section": "hints"}  # у линии подсказок секция одна
        self._ops += 1
        if self._ops > MAX_OPS:
            raise PatchError(f"слишком много операций ({self._ops} > {MAX_OPS})")
        state = self._state
        op = state._check_op(obj)
        section = op["section"] if op["op"] == "add" else SECTION_OF[op["id"][0]]
        self._check_lane(section)
        if op["op"] == "remove" and section != "hints":
            self._check_removal(op["id"], section)
        changed = state._apply_op(op, self._refs)
        changed |= state._enforce_cap()
        if changed:
            state.version += 1
            self.applied += 1
        return changed

    def _apply_patch(self, patch: dict) -> bool:
        ops = patch.get("ops", [])
        if not isinstance(ops, list):
            raise PatchError("ops должен быть списком")
        changed = False
        if patch.get("topic") is not None and self._lane != "hints":
            changed |= self._topic(patch.get("topic"))
        for op in ops:
            changed |= self.apply(op)
        return changed

    def _topic(self, value) -> bool:
        if value is None:
            return False
        if self._lane == "hints":
            raise PatchError("тему ведёт линия сводки, а не подсказок")
        if not isinstance(value, str):
            raise PatchError("topic должен быть строкой или null")
        topic = _flat(value, TOPIC_MAX)
        if not topic or topic == self._state.topic:
            return False
        self._state.topic = topic
        self._state.version += 1
        self.applied += 1
        return True

    def _check_lane(self, section: str) -> None:
        if self._lane == "hints" and section != "hints":
            raise PatchError("линия подсказок меняет только подсказки")
        if self._lane == "summary" and section == "hints":
            raise PatchError("линия сводки не меняет подсказки")

    def _check_removal(self, item_id: str, section: str) -> None:
        removed = self._removed + [item_id]
        if len(removed) > MAX_SUMMARY_REMOVES:
            raise PatchError(f"слишком много удалений из сводки за раз ({len(removed)})")
        live = set(self._state._items[section])
        if len(self._start[section]) > 1 and live and live <= set(removed):
            raise PatchError(f"удаление всего раздела {section} за раз")
        self._removed = removed


_FENCE_LINE = re.compile(r"^\s*```")


def parse_line(line: str) -> dict | None:
    """Строка ответа → JSON-объект операции; не JSON (пустая, ограда ```,
    пояснение без «{») — None. Строка с «{», которая не разбирается, —
    PatchError (её исправит повторный запрос)."""
    raw = (line or "").strip()
    if not raw or _FENCE_LINE.match(raw) or "{" not in raw or raw.strip("{}[],") == "":
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if end <= start:
        raise PatchError("строка JSON оборвана")
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError as e:
        raise PatchError(f"JSON не разбирается: {e}") from None
    if not isinstance(data, dict):
        raise PatchError("ожидался JSON-объект")
    return data


class LineSplitter:
    """Текст, приходящий кусками (поток модели), → законченные строки."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text or ""
        if "\n" not in self._buf:
            return []
        *done, self._buf = self._buf.split("\n")
        return done

    def finish(self) -> list[str]:
        rest, self._buf = self._buf, ""
        return [rest] if rest.strip() else []


def _item_text(section: str, item: dict) -> str:
    if section == "tasks":
        return f"кто: {item.get('who') or '—'}; что: {item.get('what')}; срок: {item.get('due') or '—'}"
    return str(item.get("text") or "")


_RESTORE_ID = re.compile(r"[pdtqh][1-9][0-9]{0,5}")


def _restorable(item_id, prefix: str) -> bool:
    return isinstance(item_id, str) and bool(_RESTORE_ID.fullmatch(item_id)) \
        and item_id[0] == prefix


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"[{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}]"


def _seconds(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# Сводка начинается не с начала записи (ассистента включили позже): с этой
# секунды и дальше черновик говорит, откуда он.
FROM_MIN_S = 1.0


def covered_note(covered_t, covered_from=None, partial: bool = False) -> str:
    """Где кончается черновик: сводка живого режима не успевает за последними
    репликами (её тик — раз в ~минуту речи, при остановке не ждём). У
    ассистента, включённого посреди записи, — и где начинается; `partial` —
    ассистент слышал встречу не целиком."""
    end, start = _seconds(covered_t), _seconds(covered_from)
    lines: list[str] = []
    if end is not None and start is not None and start >= FROM_MIN_S:
        lines.append(f"_Сводка учитывает реплики с {_clock(start)} до {_clock(end)}; "
                     "остальное — только в расшифровке._")
    elif end is not None:
        lines.append(f"_Сводка учитывает реплики до {_clock(end)}; "
                     "более поздние — только в расшифровке._")
    if partial:
        lines.append("_Ассистент слышал встречу не целиком — черновик неполный._")
    return "\n\n".join(lines)


def render_markdown(summary: dict, covered_t=None, covered_from=None,
                    partial: bool = False) -> str:
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
    text = "\n".join(out).strip()
    if not text:
        return "_Пока пусто — сводка появится по ходу разговора._"
    note = covered_note(covered_t, covered_from, partial)
    return f"{text}\n\n{note}" if note else text


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
    data["markdown"] = render_markdown(data["summary"], covered_t=data.get("covered_t"),
                                       covered_from=data.get("covered_from"),
                                       partial=bool(data.get("partial")))
    return data
