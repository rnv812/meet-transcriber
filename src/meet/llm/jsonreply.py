"""JSON-объект из ответа модели — терпимо к тому, как отвечают локальные модели.

Модель просили «ровно один JSON-объект», а она:

* оборачивает его в блок ```json или пишет текст до и после (со своими `{…}`);
* рассуждает в `<think>…</think>` (Qwen3, DeepSeek-R1; открывающий тег бывает
  в шаблоне чата, а в ответе — только закрывающий);
* оставляет запятую перед `}` или `]`;
* кладёт ответ внутрь обёртки (`{"analysis": {...}}`);
* обрывается на полуслове (лимит токенов) — тогда берутся целые части;
* обрывается посреди рассуждения — тогда ответа нет вовсе: черновой JSON из
  `<think>` ответом не считается;
* зацикливается (`{"title": "A", {"title": "A", …` на десятки килобайт) —
  разбор линейный: дорогие шаги (проход по скобкам, починка обрыва)
  ограничены бюджетом на ответ.

Берётся первый объект, где есть хоть одно ожидаемое поле (`expected`);
такого нет — первый объект вообще. Только stdlib.
"""

import json
import re
from collections.abc import Iterable, Iterator

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_THINK_OPEN = re.compile(r"<think>", re.I)
_THINK_CLOSE = re.compile(r"</think>", re.I)
_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_BRACE_RUN = re.compile(r"\{+")
# Бюджет дорогих шагов на один ответ: полных проходов по скобкам (`_scan`,
# O(длины)) и попыток починить обрыв (`json.loads`, O(длины)). Так разбор
# зациклившегося ответа линеен по его длине, а не кубичен.
_SCANS = 8
_CUT_TRIES = 50
# Глубже ответы моделей не бывают: зациклившийся `{"a": 1, {"a": 1, …` без
# предела растил бы стек, и снимок стека на каждой запятой делал проход
# квадратичным. Глубже — проход кончается обрывом (берутся целые части).
_MAX_DEPTH = 64
UNFINISHED = "модель не закончила рассуждение (лимит токенов или контекста) — ответа нет"


def _strip_think(text: str) -> tuple[str, bool]:
    """Текст без рассуждения → (текст, оборвано ли рассуждение)."""
    text = _THINK.sub("", text)
    closes = list(_THINK_CLOSE.finditer(text))
    if closes:
        # Открывающий тег был в шаблоне: всё до закрывающего — рассуждение.
        text = text[closes[-1].end():]
    opened = _THINK_OPEN.search(text)
    if opened:
        # Рассуждение не закрыто — ответ оборвался в нём: всё после тега — черновик.
        return text[:opened.start()], True
    return text, False


def strip_reasoning(text: str) -> tuple[str, bool]:
    """Ответ без рассуждения `<think>…</think>` (и без незакрытого — ответ
    оборвался в нём) → (текст, оборвано ли рассуждение). Одно место для всех
    ответов локальной модели: названия, итогов, ответов, тиков."""
    stripped, unfinished = _strip_think(text or "")
    return stripped.strip(), unfinished


def _scan(src: str, start: int):
    """Проход от `{` в `start`: → (конец объекта или None, места обрыва).
    Место обрыва — (позиция, открытые скобки): до неё всё целое. Глубже
    `_MAX_DEPTH` — конец прохода, как обрыв."""
    stack: list[str] = []
    cuts: list[tuple[int, str]] = []
    in_str = esc = False
    for pos in range(start, len(src)):
        ch = src[pos]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            if len(stack) >= _MAX_DEPTH:
                return None, cuts
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return None, cuts
            stack.pop()
            if not stack:
                return pos + 1, cuts
            cuts.append((pos + 1, "".join(stack)))
        elif ch == "," and stack:
            cuts.append((pos, "".join(stack)))
    return None, cuts


def _loads(text: str):
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        pass
    fixed = _TRAILING_COMMA.sub(r"\1", text)
    if fixed == text:
        return None
    try:
        return json.loads(fixed)
    except (ValueError, RecursionError):
        return None


def _closers(stack: str) -> str:
    return "".join("}" if ch == "{" else "]" for ch in reversed(stack))


def _objects(src: str, budget: dict) -> Iterator[tuple[dict, bool]]:
    """Объекты в тексте по порядку: (объект, оборван ли). `budget` — остаток
    дорогих шагов на ответ: кончился — дальше только быстрый `raw_decode`
    (на зациклившемся тексте он падает через десятки символов)."""
    decoder = json.JSONDecoder()
    i = src.find("{")
    while i != -1:
        if src.startswith("{{", i):
            # Объект не начинается с «{{»: к последней скобке подряд — без
            # исключения на каждую (зациклившийся ответ из одних «{»).
            i = _BRACE_RUN.match(src, i).end() - 1
        try:
            obj, end = decoder.raw_decode(src, i)
        except (ValueError, RecursionError):
            obj, end = None, None
            if budget["scans"] > 0:
                budget["scans"] -= 1
                end, cuts = _scan(src, i)
                if end is not None:
                    obj = _loads(src[i:end])
                else:
                    for pos, stack in reversed(cuts):
                        if budget["cuts"] <= 0:
                            break
                        budget["cuts"] -= 1
                        repaired = _loads(src[i:pos] + _closers(stack))
                        if isinstance(repaired, dict):
                            yield repaired, True
                            break
        if isinstance(obj, dict):
            yield obj, False
            i = src.find("{", end)
        elif end is not None:
            # Скобки сбалансированы, а JSON битый (комментарии, кавычки…): его
            # внутренние объекты — обрывки (глава с "title" — не ответ), мимо.
            i = src.find("{", end)
        else:
            i = src.find("{", i + 1)


def _unwrap(obj: dict, expected: tuple[str, ...]) -> dict:
    """`{"analysis": {...}}` → внутренний объект, если поля — в нём."""
    if not expected or any(k in obj for k in expected):
        return obj
    for value in obj.values():
        if isinstance(value, dict) and any(k in value for k in expected):
            return value
    return obj


def iter_objects(text: str) -> Iterator[dict]:
    """Все JSON-объекты текста по порядку (верхнего уровня; у массива — его
    объекты): терпимо к прозе вокруг, оградам ```, висячим запятым и обрыву
    (у оборванного — его целые части). Рассуждение `<think>` не снимает —
    сначала `strip_reasoning`. Разбор линейный, как у `extract_object`."""
    budget = {"scans": _SCANS, "cuts": _CUT_TRIES}
    for obj, _cut in _objects(text or "", budget):
        yield obj


def extract_object(text: str, expected: Iterable[str] = (), info: dict | None = None) -> dict:
    """Первый JSON-объект ответа с ожидаемыми полями (или первый вообще).
    Объекта нет — ValueError с текстом для человека. `info["truncated"]` —
    ответ оборван, взяты его целые части."""
    expected = tuple(expected)
    raw, unfinished = _strip_think(text or "")
    raw = raw.strip()
    sources = [m.group(1) for m in _FENCE.finditer(raw)] + [raw]
    first = None
    budget = {"scans": _SCANS, "cuts": _CUT_TRIES}
    for src in sources:
        for obj, cut in _objects(src, budget):
            obj = _unwrap(obj, expected)
            if not expected or any(k in obj for k in expected):
                if info is not None:
                    info["truncated"] = cut
                return obj
            if first is None:
                first = obj
    if first is not None:
        return first
    if unfinished:
        raise ValueError(UNFINISHED)
    if "{" not in raw:
        raise ValueError("в ответе нет JSON-объекта")
    raise ValueError("JSON не разбирается")
