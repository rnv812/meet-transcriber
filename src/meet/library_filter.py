"""Фильтр библиотеки по карточке записи — общий для `/recordings`, `/search`,
`/categories` и `/groups`.

Язык запроса разбирает окно (`app/src/lib/libraryQuery.ts`), резиденту
приходят структурные параметры адреса:

* `categories=daily,_none` — любая из категорий (`_none` — «Без категории»,
  туда же удалённые из настроек);
* `groups=g-1,g-2` — любая из групп;
* `people=Анна,Борис П` — участник встречи (`people` карточки) по началу слов
  имени, без учёта регистра и «ё»; несколько — нужны все;
* `from`/`to` — ГГГГ-ММ-ДД включительно, по дате начала (`started_at[:10]`);
* `has`/`lacks` — summary (итоги), analysis (анализ), assistant (запись с
  ассистентом), transcript (расшифровка);
* `min_s`/`max_s` — длительность в секундах;
* `in=title` — `/search` ищет только в названиях.

Запись без даты не проходит фильтр по дате, без длительности — по
длительности. Списки — через запятую или повтором параметра. Негодное
значение — FilterError с текстом для человека (резидент отвечает 400).
Фильтр проверяет только карточку: он идёт до чтения транскриптов и до лимита.
"""

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date

PARAMS = ("categories", "groups", "people", "from", "to", "has", "lacks", "min_s", "max_s", "in")
HAS = ("summary", "analysis", "assistant", "transcript")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FilterError(ValueError):
    """Негодный параметр фильтра (текст — человеку)."""


def _has(card: dict, what: str) -> bool:
    if what == "summary":
        return bool(card.get("has_summary"))
    if what == "analysis":
        return bool(card.get("has_analysis"))
    if what == "assistant":
        return card.get("source") == "live"
    return bool(card.get("has_transcript"))


def _words(text: str) -> list[str]:
    from meet import search

    return [t[0] for t in search.tokenize(search.nfc(text))]


@dataclass(frozen=True)
class LibraryFilter:
    categories: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    # Участники: слова каждого (нормализованные), все должны найтись.
    people: tuple[tuple[str, ...], ...] = ()
    date_from: str | None = None
    date_to: str | None = None
    has: tuple[str, ...] = ()
    lacks: tuple[str, ...] = ()
    min_s: float | None = None
    max_s: float | None = None
    title_only: bool = False
    # id категорий из настроек: неизвестный id — «Без категории».
    known_categories: frozenset = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        """Есть ли что проверять в карточке (`in=title` — не про карточку)."""
        return bool(self.categories or self.groups or self.people or self.date_from or self.date_to
                    or self.has or self.lacks or self.min_s is not None or self.max_s is not None)

    def without(self, *facets: str) -> "LibraryFilter":
        """Тот же фильтр без фасетов (`categories`, `groups`): счётчики фасета
        считаются среди найденного остальными условиями."""
        return replace(self, **{f: () for f in facets})

    def __call__(self, card: dict) -> bool:
        if self.categories:
            from meet.categories import key_of

            if key_of(card, self.known_categories) not in self.categories:
                return False
        if self.groups and not any(g in self.groups for g in card.get("groups") or ()):
            return False
        if self.people and not self._people(card.get("people") or ()):
            return False
        if self.date_from or self.date_to:
            day = (card.get("started_at") or "")[:10]
            if not day or (self.date_from and day < self.date_from) or (self.date_to and day > self.date_to):
                return False
        if any(not _has(card, h) for h in self.has) or any(_has(card, h) for h in self.lacks):
            return False
        if self.min_s is not None or self.max_s is not None:
            seconds = card.get("duration_s")
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
                return False
            if (self.min_s is not None and seconds < self.min_s) or (
                    self.max_s is not None and seconds > self.max_s):
                return False
        return True

    def _people(self, names) -> bool:
        named = [_words(n) for n in names if isinstance(n, str)]
        return all(any(all(any(w.startswith(p) for w in name) for p in person) for name in named)
                   for person in self.people)


def _values(params: dict, name: str) -> list[str]:
    raw = params.get(name)
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    return [part.strip() for item in items for part in str(item).split(",") if part.strip()]


def _one(params: dict, name: str) -> str:
    values = _values(params, name)
    return values[-1] if values else ""


def _date(params: dict, name: str, label: str) -> str | None:
    value = _one(params, name)
    if not value:
        return None
    try:
        if not _DATE.match(value):
            raise ValueError
        date.fromisoformat(value)
    except ValueError:
        raise FilterError(f"дата «{label}» — в виде ГГГГ-ММ-ДД, а не «{value}»") from None
    return value


def _seconds(params: dict, name: str) -> float | None:
    value = _one(params, name)
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        raise FilterError(f"длительность — число секунд, а не «{value}»")
    return seconds


def _what(params: dict, name: str) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(v.lower() for v in _values(params, name)))
    for value in values:
        if value not in HAS:
            raise FilterError(f"неизвестное условие «{value}»: можно {', '.join(HAS)}")
    return values


def from_params(params: dict | None, cfg=None) -> LibraryFilter:
    """Фильтр из параметров адреса (`{имя: значение}` или `{имя: [значения]}`,
    как у parse_qs). Посторонние параметры (q, limit, token) не мешают."""
    from meet import groups

    params = params or {}
    categories = tuple(dict.fromkeys(_values(params, "categories")))
    known: frozenset = frozenset()
    if categories:
        from meet import categories as cats, settings

        known = frozenset(cats.ids(settings.load() if cfg is None else cfg))
    group_ids = tuple(dict.fromkeys(_values(params, "groups")))
    for gid in group_ids:
        if not groups.valid_id(gid):
            raise FilterError(f"негодный id группы «{gid}»")
    people = tuple(dict.fromkeys(w for w in (tuple(_words(v)) for v in _values(params, "people")) if w))
    where = _one(params, "in").lower()
    if where not in ("", "title"):
        raise FilterError(f"искать можно везде или только в названиях (in=title), а не «{where}»")
    return LibraryFilter(
        categories=categories, groups=group_ids, people=people,
        date_from=_date(params, "from", "с"), date_to=_date(params, "to", "по"),
        has=_what(params, "has"), lacks=_what(params, "lacks"),
        min_s=_seconds(params, "min_s"), max_s=_seconds(params, "max_s"),
        title_only=where == "title", known_categories=known,
    )


def participants(cards, q: str = "", limit: int = 20, owners=()) -> list[dict]:
    """Участники встреч для подсказок `участник:` — только имена из
    расшифровок (`people` карточек): [{name, meetings, last_at, owner}], чаще
    встречавшиеся раньше (поровну — с кем виделись позже), владелец
    (`owners` — его подписи) последним. `q` — начало слов имени."""
    want = _words(q or "")
    seen: dict[str, dict] = {}
    for card in cards:
        for name in card.get("people") or ():
            if not isinstance(name, str):
                continue
            item = seen.setdefault(name, {"name": name, "meetings": 0, "last_at": None,
                                          "owner": name in owners})
            item["meetings"] += 1
            at = card.get("started_at")
            if isinstance(at, str) and (item["last_at"] is None or at > item["last_at"]):
                item["last_at"] = at
    found = [p for p in seen.values()
             if all(any(w.startswith(x) for w in _words(p["name"])) for x in want)]
    # Сортировка устойчива: сначала имя, потом «позже — раньше», потом число встреч.
    found.sort(key=lambda p: p["name"].casefold())
    found.sort(key=lambda p: p["last_at"] or "", reverse=True)
    found.sort(key=lambda p: (p["owner"], -p["meetings"]))
    return found[:max(0, limit)]
