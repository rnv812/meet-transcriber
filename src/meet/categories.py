"""Категории встреч: какая категория у записи и откуда она.

Список категорий — в настройках (`settings.categories`: id, имя, цвет,
описание для модели). Категория записи — в `meta.json`:

    "category": {"id": "daily", "source": "ai", "confidence": 0.8}
    "category": {"id": null, "source": "user"}   # человек выбрал «Без категории»

* `ai` — из анализа встречи (`analysis.json` → `category`): ставится, только
  если включено «Определять категорию автоматически» (`analysis.category`),
  id есть в списке и уверенность не ниже CONFIDENCE_MIN. Новый анализ
  уточняет прежнюю категорию от модели (или снимает её, если теперь уверенности
  нет).
* `user` — выбрал человек (карточка, список, `meet category`). Модель её не
  меняет никогда, в том числе выбранное вручную «Без категории». Проверка идёт
  под замком meta.json (`library.update_meta`), как у названия
  (`meet.titles`): выбор человека в ту же секунду не затирается.

Удалённая из настроек категория в meta.json остаётся: окно и выгрузка
показывают неизвестный id как «Без категории» (см. `known`). Так удаление
обратимо — «Сбросить к стандартным» возвращает встречам их категории — и не
требует обхода всей библиотеки.
"""

from pathlib import Path

from meet import library

CONFIDENCE_MIN = 0.5
SOURCES = ("ai", "user")
NONE_NAME = "Без категории"


def of(meta: dict) -> dict | None:
    """Категория из meta.json → {"id": str | None, "source": "ai" | "user"} или
    None (не задана, битая запись, «модель не уверена»)."""
    raw = meta.get("category") if isinstance(meta, dict) else None
    if not isinstance(raw, dict) or raw.get("source") not in SOURCES:
        return None
    cid = raw.get("id")
    cid = cid.strip() if isinstance(cid, str) and cid.strip() else None
    if cid is None and raw["source"] == "ai":
        return None
    return {"id": cid, "source": raw["source"]}


def ids(cfg) -> list[str]:
    return [c.id for c in getattr(cfg, "categories", ())]


def known(cfg, cid) -> bool:
    return isinstance(cid, str) and cid in ids(cfg)


def name_of(cfg, cid) -> str | None:
    """Имя категории по id; неизвестный id (категорию удалили) — None."""
    return next((c.name for c in getattr(cfg, "categories", ()) if c.id == cid), None)


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold().replace("ё", "е")


def resolve(cfg, value: str) -> str | None:
    """id категории по id или имени (без учёта регистра и «ё»); нет такой — None."""
    want = _norm(value)
    if not want:
        return None
    for c in getattr(cfg, "categories", ()):
        if c.id == want or _norm(c.name) == want:
            return c.id
    return None


def display_name(folder: Path, cfg) -> str | None:
    """Имя категории записи для выгрузки; нет категории или она удалена — None."""
    got = of(library.read_meta(Path(folder)))
    return name_of(cfg, got["id"]) if got and got["id"] else None


def set_user(folder: Path, cid: str | None) -> bool:
    """Категория, выбранная человеком (None — «Без категории»: модель её тоже
    больше не ставит). → поменялось ли что-нибудь."""
    changed = False
    want = {"id": cid, "source": "user"}

    def change(meta: dict) -> dict:
        nonlocal changed
        if meta.get("category") == want:
            return meta
        changed = True
        return {**meta, "category": want}

    library.update_meta(Path(folder), change)
    return changed


def from_analysis(doc: dict | None, cfg) -> dict | None:
    """Категория, которую предлагает анализ: id из списка и уверенность не ниже
    порога → {"id", "source": "ai", "confidence"}; иначе None («Без категории»)."""
    found = (doc or {}).get("category")
    if not isinstance(found, dict) or not known(cfg, found.get("id")):
        return None
    try:
        confidence = float(found.get("confidence"))
    except (TypeError, ValueError):
        return None
    if confidence < CONFIDENCE_MIN:
        return None
    return {"id": found["id"], "source": "ai", "confidence": round(min(confidence, 1.0), 2)}


def apply_ai(folder: Path, doc: dict | None, cfg) -> bool:
    """Анализ встречи готов: категория от модели — по правилам выше. Анализ без
    категории (её не просили) ничего не меняет. → поменялось ли что-нибудь."""
    if not getattr(getattr(cfg, "analysis", None), "category", False):
        return False
    if not isinstance(doc, dict) or "category" not in doc:
        return False
    want = from_analysis(doc, cfg)
    changed = False

    def change(meta: dict) -> dict:
        nonlocal changed
        current = meta.get("category")
        if isinstance(current, dict) and current.get("source") == "user":
            return meta  # выбор человека
        if want is None:
            if "category" not in meta:
                return meta
            changed = True
            return {k: v for k, v in meta.items() if k != "category"}
        if current == want:
            return meta
        changed = True
        return {**meta, "category": want}

    library.update_meta(Path(folder), change)
    return changed


def counts(root: Path) -> dict:
    """Сколько встреч библиотеки в каждой категории: {"counts": {id: n},
    "none": n} (для подтверждения удаления категории в настройках)."""
    out: dict[str, int] = {}
    none = 0
    for card in library.listing(Path(root), limit=10**9):
        cid = (card.get("category") or {}).get("id")
        if cid:
            out[cid] = out.get(cid, 0) + 1
        else:
            none += 1
    return {"counts": out, "none": none}
