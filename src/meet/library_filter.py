"""Фильтр библиотеки по карточке записи — общий для `/recordings`, `/search`,
`/categories` и `/groups`.

Язык запроса разбирает окно (`app/src/lib/libraryQuery.ts`), резиденту
приходят структурные параметры адреса:

* `categories=daily,_none` — любая из категорий (`_none` — «Без категории»,
  туда же удалённые из настроек);
* `groups=g-1,g-2,_none` — любая из групп (у встречи одна группа; `_none` —
  «Без группы»);
* `people=Анна&people=Борис П` — участник встречи (`people` карточки) по
  началу слов имени, без учёта регистра и «ё»; по параметру на участника
  (запятая — часть имени: «Петров, Демьян»), несколько — нужны все;
* `from`/`to` — ГГГГ-ММ-ДД включительно, по дате начала (`started_at[:10]`);
* `has`/`lacks` — summary (итоги), analysis (анализ), assistant (во встрече
  работал ассистент — `has_assistant` карточки), transcript (расшифровка);
* `min_s`/`max_s` — длительность в секундах;
* `title=бюджет квартал` — в названии встречи есть все эти слова (по тем же
  правилам, что поиск: «лёгкая основа», «фразы»); остальные слова запроса `q`
  ищутся как обычно — так окно передаёт `название:бюджет`;
* `in=title` — `/search` ищет весь `q` только в названиях (прежний вид,
  оставлен для совместимости).

Запись без даты не проходит фильтр по дате, без длительности — по
длительности. Списки — через запятую или повтором параметра. Негодное
значение — FilterError с текстом для человека (резидент отвечает 400).
Фильтр проверяет только карточку: он идёт до чтения транскриптов и до лимита.
"""

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date

from meet.groups import NONE_KEY as GROUP_NONE

# Измерения фильтра: у каждого фасета «Фильтров» — своё (`has` и `lacks` — одно).
DIMENSIONS = ("categories", "groups", "people", "dates", "has", "duration", "title")
PARAMS = ("categories", "groups", "people", "from", "to", "has", "lacks", "min_s", "max_s", "in", "title")
HAS = ("summary", "analysis", "assistant", "transcript")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FilterError(ValueError):
    """Негодный параметр фильтра (текст — человеку)."""


def card_has(card: dict, what: str) -> bool:
    """Есть ли у встречи `what` (HAS)."""
    if what == "summary":
        return bool(card.get("has_summary"))
    if what == "analysis":
        return bool(card.get("has_analysis"))
    if what == "assistant":
        return bool(card.get("has_assistant"))
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
    # Слова, которые должны быть в названии (`title=`), как их написал человек.
    title: str = ""
    # id категорий из настроек: неизвестный id — «Без категории».
    known_categories: frozenset = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        """Есть ли что проверять в карточке (`in=title` — не про карточку)."""
        return bool(self.categories or self.groups or self.people or self.date_from or self.date_to
                    or self.has or self.lacks or self.min_s is not None or self.max_s is not None
                    or self.title)

    def without(self, *facets: str) -> "LibraryFilter":
        """Тот же фильтр без фасетов (`categories`, `groups`): счётчики фасета
        считаются среди найденного остальными условиями."""
        return replace(self, **{f: () for f in facets})

    def dimensions(self) -> tuple[str, ...]:
        """Измерения (DIMENSIONS), в которых у фильтра есть условия."""
        return tuple(d for d, on in (
            ("categories", self.categories), ("groups", self.groups), ("people", self.people),
            ("dates", self.date_from or self.date_to), ("has", self.has or self.lacks),
            ("duration", self.min_s is not None or self.max_s is not None), ("title", self.title)) if on)

    def check(self, dim: str, card: dict) -> bool:
        """Проходит ли карточка условие одного измерения."""
        if dim == "categories":
            from meet.categories import key_of

            return key_of(card, self.known_categories) in self.categories
        if dim == "groups":
            return (card.get("group") or GROUP_NONE) in self.groups
        if dim == "people":
            return self._people(card.get("people") or ())
        if dim == "dates":
            day = (card.get("started_at") or "")[:10]
            return bool(day) and not ((self.date_from and day < self.date_from)
                                      or (self.date_to and day > self.date_to))
        if dim == "title":
            return title_matches(card, self.title)
        if dim == "has":
            return (all(card_has(card, h) for h in self.has)
                    and not any(card_has(card, h) for h in self.lacks))
        seconds = card.get("duration_s")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            return False
        return not ((self.min_s is not None and seconds < self.min_s)
                    or (self.max_s is not None and seconds > self.max_s))

    def failing(self, card: dict) -> set[str]:
        """Измерения, условия которых карточка не проходит (для фасетов:
        счётчик измерения — среди карточек, не прошедших разве что его)."""
        return {d for d in self.dimensions() if not self.check(d, card)}

    def __call__(self, card: dict) -> bool:
        return all(self.check(d, card) for d in self.dimensions())

    def _people(self, names) -> bool:
        named = [_words(n) for n in names if isinstance(n, str)]
        return all(any(all(any(w.startswith(p) for w in name) for p in person) for name in named)
                   for person in self.people)


def title_query(text: str):
    """Слова `title=` как запрос поиска (meet.search.parse_query): спикеров нет."""
    from meet import search

    q = search.parse_query(text)
    return search.Query(q.phrases, q.keywords, q.stems, [])


def title_matches(card: dict, text: str) -> bool:
    """В названии встречи есть все слова (и фразы) `text` — как ищет поиск."""
    from meet import search

    q = title_query(text)
    if not q.has_text:
        return True
    title = search.nfc(card.get("title") or "")
    return bool(title) and search.match_tokens(search.tokenize(title), q) is not None


def _values(params: dict, name: str, split: bool = True) -> list[str]:
    raw = params.get(name)
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    parts = (part for item in items for part in (str(item).split(",") if split else [str(item)]))
    return [part.strip() for part in parts if part.strip()]


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
        if gid != groups.NONE_KEY and not groups.valid_id(gid):
            raise FilterError(f"негодный id группы «{gid}»")
    people = tuple(dict.fromkeys(w for w in (tuple(_words(v)) for v in _values(params, "people", split=False)) if w))
    where = _one(params, "in").lower()
    if where not in ("", "title"):
        raise FilterError(f"искать можно везде или только в названиях (in=title), а не «{where}»")
    return LibraryFilter(
        categories=categories, groups=group_ids, people=people,
        date_from=_date(params, "from", "с"), date_to=_date(params, "to", "по"),
        has=_what(params, "has"), lacks=_what(params, "lacks"),
        min_s=_seconds(params, "min_s"), max_s=_seconds(params, "max_s"),
        title_only=where == "title", known_categories=known,
        title=" ".join(_values(params, "title", split=False)),
    )


# Панель «Фильтры» (GET /facets): участников — столько самых частых.
FACET_PEOPLE = 20


def near(flt: LibraryFilter):
    """Отбор карточек для фасетов: не прошедшие разве что одно условие (другие
    ни в один счётчик не попадут, их тексты и проверять незачем). Фильтра нет —
    None."""
    dims = flt.dimensions()
    if not dims:
        return None

    def keep(card: dict) -> bool:
        misses = 0
        for dim in dims:
            if not flt.check(dim, card):
                misses += 1
                if misses > 1:
                    return False
        return True

    return keep


def facets(cards, flt: LibraryFilter, category_ids, group_items) -> dict:
    """Счётчики панели «Фильтры» среди `cards` (уже найденных запросом):
    измерение — по карточкам, не прошедшим разве что его собственное условие,
    `total` — прошедшим все. Категории и группы — в порядке настроек и списка
    групп (с нулями), неизвестные группы — по убыванию, участники — FACET_PEOPLE
    самых частых, длительность — до 15 мин, 15–60 мин, больше часа."""
    from meet.categories import NONE_KEY, key_of

    known_categories = set(category_ids)
    known_groups = {g["id"] for g in group_items}
    cats: dict[str, int] = {}
    grp: dict[str, int] = {}
    people: dict[str, int] = {}
    has = {h: 0 for h in HAS}
    duration = {"lt15": 0, "m15_60": 0, "gt60": 0}
    cats_none = grp_none = total = 0
    active = bool(flt.dimensions())
    for card in cards:
        fails = flt.failing(card) if active else set()
        if not fails:
            total += 1
        if fails <= {"categories"}:
            key = key_of(card, known_categories)
            if key == NONE_KEY:
                cats_none += 1
            else:
                cats[key] = cats.get(key, 0) + 1
        if fails <= {"groups"}:
            gid = card.get("group")
            if gid:
                grp[gid] = grp.get(gid, 0) + 1
            else:
                grp_none += 1
        if fails <= {"people"}:
            for name in card.get("people") or ():
                people[name] = people.get(name, 0) + 1
        if fails <= {"has"}:
            for h in has:
                has[h] += card_has(card, h)
        if fails <= {"duration"}:
            seconds = card.get("duration_s")
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                duration["lt15" if seconds < 900 else "m15_60" if seconds <= 3600 else "gt60"] += 1
    top = sorted(people.items(), key=lambda item: (-item[1], item[0].casefold()))[:FACET_PEOPLE]
    unknown = sorted(((g, n) for g, n in grp.items() if g not in known_groups), key=lambda x: (-x[1], x[0]))
    return {
        "total": total,
        "categories": {"items": [{"id": c, "count": cats.get(c, 0)} for c in category_ids], "none": cats_none},
        "groups": {"items": [{"id": g["id"], "count": grp.get(g["id"], 0)} for g in group_items],
                   "unknown": [{"id": g, "count": n} for g, n in unknown], "none": grp_none},
        "people": [{"name": name, "count": n} for name, n in top],
        "has": has,
        "duration": duration,
    }


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
