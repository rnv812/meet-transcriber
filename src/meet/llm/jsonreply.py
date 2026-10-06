"""JSON-объект из ответа модели — терпимо к тому, как отвечают локальные модели.

Модель просили «ровно один JSON-объект», а она:

* оборачивает его в блок ```json или пишет текст до и после (со своими `{…}`);
* рассуждает в `<think>…</think>` (Qwen3, DeepSeek-R1; открывающий тег бывает
  в шаблоне чата, а в ответе — только закрывающий);
* оставляет запятую перед `}` или `]`;
* кладёт ответ внутрь обёртки (`{"analysis": {...}}`);
* обрывается на полуслове (лимит токенов) — тогда берутся целые части.

Берётся первый объект, где есть хоть одно ожидаемое поле (`expected`);
такого нет — первый объект вообще. Только stdlib.
"""

import json
import re
from collections.abc import Iterable, Iterator

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# Сколько мест обрыва пробовать с конца, прежде чем сдаться.
_MAX_CUTS = 400


def _strip_think(text: str) -> str:
    text = _THINK.sub("", text)
    low = text.lower()
    if "</think>" in low:
        # Открывающий тег был в шаблоне: всё до закрывающего — рассуждение.
        text = text[low.rindex("</think>") + len("</think>"):]
    return re.sub(r"</?think>", "", text, flags=re.I)


def _scan(src: str, start: int):
    """Проход от `{` в `start`: → (конец объекта или None, места обрыва).
    Место обрыва — (позиция, открытые скобки): до неё всё целое."""
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
    except ValueError:
        try:
            return json.loads(_TRAILING_COMMA.sub(r"\1", text))
        except ValueError:
            return None


def _closers(stack: str) -> str:
    return "".join("}" if ch == "{" else "]" for ch in reversed(stack))


def _objects(src: str) -> Iterator[tuple[dict, bool]]:
    """Объекты в тексте по порядку: (объект, оборван ли)."""
    decoder = json.JSONDecoder()
    i = src.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(src, i)
        except ValueError:
            obj = None
            end, cuts = _scan(src, i)
            if end is not None:
                obj = _loads(src[i:end])
            else:
                for pos, stack in reversed(cuts[-_MAX_CUTS:]):
                    obj = _loads(src[i:pos] + _closers(stack))
                    if isinstance(obj, dict):
                        yield obj, True
                        break
                obj = None
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


def extract_object(text: str, expected: Iterable[str] = (), info: dict | None = None) -> dict:
    """Первый JSON-объект ответа с ожидаемыми полями (или первый вообще).
    Объекта нет — ValueError с текстом для человека. `info["truncated"]` —
    ответ оборван, взяты его целые части."""
    expected = tuple(expected)
    raw = _strip_think(text or "").strip()
    sources = [m.group(1) for m in _FENCE.finditer(raw)] + [raw]
    first = None
    for src in sources:
        for obj, cut in _objects(src):
            obj = _unwrap(obj, expected)
            if not expected or any(k in obj for k in expected):
                if info is not None:
                    info["truncated"] = cut
                return obj
            if first is None:
                first = obj
    if first is not None:
        return first
    if "{" not in raw:
        raise ValueError("в ответе нет JSON-объекта")
    raise ValueError("JSON не разбирается")
