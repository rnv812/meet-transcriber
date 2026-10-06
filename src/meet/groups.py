"""Группы встреч: проект, клиент, серия — у встречи одна группа или ни одной
(категория — «тип» встречи, отдельное измерение; группа — к чему она относится).

Описания — в папке записей, `.meet-groups.json`:

    {"version": 1, "groups": [{"id": "g-3f9a1c2e", "name": "Проект Альфа",
                               "color": "#4f8cc9", "created_at": "2026-10-06T10:00:00"}]}

порядок в окне — порядок массива. Членство — в `meta.json` встречи:
`"group": "g-3f9a1c2e"`; у встречи одна группа или ни одной (категория —
отдельное измерение). Папка записи по-прежнему самодостаточна. Список
`"groups": [...]` — от ранних сборок 0.3.5: читается его первый годный id,
пишется всегда `group`.

* id — `g-` + 8 шестнадцатеричных, не переиспользуется (свободным считается
  id, которого нет ни в файле, ни в meta.json встреч); проверка — ID_RE.
* Имя — 1–NAME_MAX символов, пробелы схлопываются, уникально без учёта
  регистра и «ё»; «Все записи» — имя области «без группы», его не дать.
* Файл пишется атомарно (временный + replace с повтором) под
  `library.file_lock`: чтение-правка-запись не теряют чужую правку. Чужие
  поля (и файла, и групп — их может добавить новая версия) сохраняются;
  файл версии новее VERSION только читается, запись — отказ.
* Битый файл (не JSON или не та форма) не перезаписывается молча: окно видит
  `broken` в `GET /groups`, а первая запись откладывает его рядом
  (`.meet-groups.json.broken-<время>-…`) и называет, куда (`moved_broken`).
  Файл, который сейчас не прочитать (занят антивирусом, синхронизацией,
  индексатором), — не битый: чтение повторяется, потом — ошибка (Busy), и
  ничего не пишется.
* Удаление группы не трогает meta.json: её id у встреч становится
  «неизвестным» («Группа без названия · N встреч» в окне), и «Отменить»
  возвращает группу с тем же id, временем создания и на то же место
  (`create(gid=…, index=…, created_at=…)`). «Назвать» неизвестную — тоже
  `create` с её id.
* `kb_folder` (необязательно, 0.3.6) — папка базы знаний группы, путь
  относительно `assistant.knowledge_dir` через «/»: когда человек просит
  ассистента поискать в базе, на встрече группы он ищет сначала там
  (meet.assist.kb_prep); сам в базу он не ходит. Задаётся
  `update(kb_folder=…)`, пустое — убрать; читается `kb_folder(group)` —
  негодное (правка руками) читается как «не задано», но в файле остаётся.
* Удаление встречи уносит членство с папкой; объединение получает группу
  первой по времени части, у которой она есть (как категорию, meet.merge);
  импорт и новая запись в группы сами не попадают.
"""

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from meet import library

FILE = ".meet-groups.json"
BROKEN_MARK = ".broken-"
VERSION = 1
# id группы: `g-` + 8 шестнадцатеричных; проверка шире — на случай правки руками.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
NAME_MAX = 60
KB_FOLDER_MAX = 260
# Область «все встречи» в окне: группа с таким именем путала бы.
RESERVED = "Все записи"
# Цвета новых групп по кругу, если окно не выбрало свой.
PALETTE = ("#4f8cc9", "#d9822b", "#3f9d5d", "#b05cc6", "#c9504f", "#2a9d9b", "#c2a03a", "#7a7f87")
NO_GROUP = "группы нет"
BUSY = "Файл групп сейчас занят другой программой — повторите через минуту"
TOO_NEW = "Файл групп записан более новой версией Meet — обновите приложение, чтобы менять группы"
# Чтение занятого файла: столько попыток с растущей паузой (как library._replace).
READ_TRIES = 5


class GroupError(ValueError):
    """Негодный запрос к группам (текст — человеку, резидент отвечает 400)."""


class NoGroup(GroupError):
    """Такой группы нет в списке (резидент отвечает 404)."""


class Busy(OSError):
    """Файл групп не прочитать прямо сейчас (резидент отвечает 503)."""


def valid_id(gid) -> bool:
    return isinstance(gid, str) and bool(ID_RE.match(gid))


# «Без группы» в фильтре (`?groups=g-1,_none`) и счётчиках: id групп
# начинаются с буквы или цифры, так что с ними он не совпадёт.
NONE_KEY = "_none"


def of(meta: dict) -> str | None:
    """Группа встречи из meta.json: `group`, а у записей ранних сборок —
    первый годный id списка `groups`; нет или негодный — None."""
    if not isinstance(meta, dict):
        return None
    gid = meta.get("group")
    if valid_id(gid):
        return gid
    raw = meta.get("groups")
    if isinstance(raw, list):
        return next((g for g in raw if valid_id(g)), None)
    return None


def _with_group(meta: dict, gid: str | None) -> dict:
    """meta.json с группой `gid` (None — без группы); старый список `groups`
    убирается."""
    out = {k: v for k, v in meta.items() if k not in ("group", "groups")}
    return {**out, "group": gid} if gid else out


def path(root: Path) -> Path:
    return Path(root) / FILE


def _key(name: str) -> str:
    return name.casefold().replace("ё", "е")


def _name(value) -> str:
    if not isinstance(value, str):
        raise GroupError("нужно название группы")
    name = " ".join(value.split())
    if not name:
        raise GroupError("нужно название группы")
    if len(name) > NAME_MAX:
        raise GroupError(f"название группы — не длиннее {NAME_MAX} символов")
    if _key(name) == _key(RESERVED):
        raise GroupError(f"«{RESERVED}» — не название группы")
    return name


def _color(value) -> str:
    if not isinstance(value, str) or not COLOR.match(value):
        raise GroupError("цвет — в виде #RRGGBB")
    return value


def check_kb_folder(value) -> str | None:
    """Путь папки базы знаний группы (относительно базы, через «/») или None
    — «не задана». Абсолютный путь, буква диска, «..» — отказ: папка группы
    не выходит за базу знаний."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GroupError("папка базы знаний — строка")
    text = value.strip().replace("\\", "/")
    if not text or text.strip("/") == "":
        return None
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise GroupError("папка базы знаний — путь внутри базы, без «/» в начале и буквы диска")
    parts = [part.strip() for part in text.strip("/").split("/")]
    if any(part in ("", ".", "..") for part in parts):
        raise GroupError("в пути папки базы знаний нельзя «..», «.» и пустые части")
    if any(ord(ch) < 32 or ch in '<>:"|?*' for ch in text):
        raise GroupError("в пути папки базы знаний недопустимые символы")
    path = "/".join(parts)
    if len(path) > KB_FOLDER_MAX:
        raise GroupError(f"путь папки базы знаний — не длиннее {KB_FOLDER_MAX} символов")
    return path


def kb_folder(group) -> str | None:
    """Папка базы знаний группы из описания или None (нет, пусто, негодная)."""
    if not isinstance(group, dict):
        return None
    try:
        return check_kb_folder(group.get("kb_folder"))
    except GroupError:
        return None


def _created_at(value) -> str:
    if not isinstance(value, str):
        raise GroupError("время создания — строка ISO")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise GroupError("время создания — строка ISO") from None
    return value


def _clean(raw) -> list[dict]:
    """Годные описания из файла: битые записи и повторы id отбрасываются,
    чужие поля групп остаются как есть."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, dict) or not valid_id(item.get("id")) or item["id"] in seen:
            continue
        name = " ".join(str(item.get("name") or "").split())[:NAME_MAX]
        if not name:
            continue
        color = item.get("color")
        seen.add(item["id"])
        out.append({**item, "id": item["id"], "name": name,
                    "color": color if isinstance(color, str) and COLOR.match(color)
                    else PALETTE[len(out) % len(PALETTE)],
                    "created_at": str(item.get("created_at") or "")})
    return out


class _File:
    """Прочитанный файл групп: группы, прочие поля файла, битый ли, новее ли."""

    __slots__ = ("items", "extra", "broken", "newer")

    def __init__(self, items=None, extra=None, broken=False, newer=False) -> None:
        self.items = items or []
        self.extra = extra or {}
        self.broken = broken
        self.newer = newer


def _read(file: Path, *, sleep=time.sleep) -> _File:
    """Нет файла — пусто. Не JSON или не та форма — `broken`. Не прочитать
    (занят) — несколько попыток, потом Busy: такой файл не битый, трогать его
    нельзя."""
    for attempt in range(READ_TRIES):
        try:
            text = file.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return _File()
        except OSError:
            if attempt == READ_TRIES - 1:
                raise Busy(BUSY) from None
            sleep(0.05 * (attempt + 1))
    try:
        data = json.loads(text)
    except ValueError:
        return _File(broken=True)
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return _File(broken=True)
    version = data.get("version")
    newer = isinstance(version, int) and not isinstance(version, bool) and version > VERSION
    extra = {k: v for k, v in data.items() if k not in ("version", "groups")}
    return _File(_clean(data["groups"]), extra, newer=newer)


def load(root: Path) -> list[dict]:
    """Группы библиотеки по порядку; нет файла или он битый — пусто (битый
    при этом не трогается). Занят — Busy."""
    return _read(path(root)).items


def broken_copies(root: Path) -> list[Path]:
    """Отложенные битые файлы групп, свежие первыми."""
    try:
        found = [p for p in Path(root).iterdir() if p.name.startswith(FILE + BROKEN_MARK)]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.name, reverse=True)


def state(root: Path) -> dict:
    """Группы и состояние файла для `GET /groups`: {"items", "broken",
    "broken_copy"?, "newer"}. `broken` — файл не прочитать как список групп
    (первая запись отложит его); `broken_copy` — последний отложенный."""
    got = _read(path(root))
    out = {"items": got.items, "broken": got.broken, "newer": got.newer}
    if got.broken:
        copies = broken_copies(root)
        if copies:
            out["broken_copy"] = str(copies[0])
    return out


def _write(file: Path, items: list[dict], extra: dict) -> None:
    tmp = file.with_name(f"{file.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps({**extra, "version": VERSION, "groups": items}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        library._replace(tmp, file)
    finally:
        tmp.unlink(missing_ok=True)


def _aside(file: Path) -> Path:
    """Свободное имя для отложенного битого файла: время, номер процесса и,
    если и такое занято, счётчик."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{file.name}{BROKEN_MARK}{stamp}-{os.getpid()}"
    target, n = file.with_name(base), 2
    while target.exists():
        target, n = file.with_name(f"{base}-{n}"), n + 1
    return target


def _change(root: Path, change) -> tuple[object, str | None]:
    """Прочитать, поправить (`change(items) -> (items | None, ответ)`; None —
    ничего не менять) и записать под замком → (ответ, куда отложен битый файл
    или None). Занятый файл — Busy, файл новее этой версии — отказ; в обоих
    случаях ничего не пишется."""
    file = path(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    with library.file_lock(file):
        got = _read(file)
        if got.newer:
            raise GroupError(TOO_NEW)
        items, out = change([dict(g) for g in got.items])
        if items is None:
            return out, None
        moved = None
        if got.broken:
            target = _aside(file)
            library._replace(file, target)
            moved = str(target)
        _write(file, items, got.extra)
        return out, moved


def _with_moved(out: dict, moved: str | None) -> dict:
    return {**out, "moved_broken": moved} if moved else out


def _find(items: list[dict], gid) -> int:
    for i, item in enumerate(items):
        if item["id"] == gid:
            return i
    raise NoGroup(NO_GROUP)


def _unique(items: list[dict], name: str, skip: str | None = None) -> None:
    if any(_key(g["name"]) == _key(name) and g["id"] != skip for g in items):
        raise GroupError(f"группа «{name}» уже есть")


# Поля группы, которые задаёт сам create; прочие (`extra`) — чужие: их могла
# добавить новая версия Meet (например, `parent` у дерева групп).
OWN_FIELDS = ("id", "name", "color", "created_at")


def create(root: Path, name, color=None, *, gid=None, index=None, taken=(), created_at=None,
           extra: dict | None = None) -> dict:
    """Новая группа (в конец или на место `index`). `gid` — вернуть удалённую
    («Отменить», с её `created_at` и прочими полями `extra` — как было) или
    назвать неизвестную с тем же id.
    `taken` — id, которые уже встречаются в meta.json встреч: новый id их не
    повторит. Отложен битый файл — в ответе `moved_broken`."""
    name = _name(name)
    color = None if color is None else _color(color)
    created_at = None if created_at is None else _created_at(created_at)
    if gid is not None and not valid_id(gid):
        raise GroupError("негодный id группы")
    if index is not None and (not isinstance(index, int) or isinstance(index, bool)):
        raise GroupError("место группы — число")

    def change(items):
        _unique(items, name)
        if gid is not None and any(g["id"] == gid for g in items):
            raise GroupError("такая группа уже есть")
        new_id = gid
        while new_id is None or (gid is None and (new_id in taken or any(g["id"] == new_id for g in items))):
            new_id = f"g-{secrets.token_hex(4)}"
        kept = {k: v for k, v in (extra or {}).items() if k not in OWN_FIELDS} if gid is not None else {}
        group = {**kept, "id": new_id, "name": name, "color": color or PALETTE[len(items) % len(PALETTE)],
                 "created_at": created_at or datetime.now().isoformat(timespec="seconds")}
        at = len(items) if index is None else max(0, min(index, len(items)))
        items.insert(at, group)
        return items, group

    return _with_moved(*_change(root, change))


# «kb_folder не передан» (None и "" значат «убрать папку»).
KEEP = object()


def update(root: Path, gid, *, name=None, color=None, kb_folder=KEEP, kb_root=None) -> dict:
    """Переименовать, перекрасить и (или) задать папку базы знаний
    (`kb_folder`: путь — задать, None или "" — убрать). `kb_root` — корень
    базы знаний: если папка там есть, сохраняется так, как она записана на
    диске («проекты/альфа» → «Проекты/Альфа»). Нет группы — NoGroup."""
    if name is None and color is None and kb_folder is KEEP:
        raise GroupError("нечего менять: нужно название, цвет или папка базы знаний")
    name = None if name is None else _name(name)
    color = None if color is None else _color(color)
    folder = KEEP if kb_folder is KEEP else check_kb_folder(kb_folder)
    if folder not in (KEEP, None) and kb_root:
        from meet.assist.kb_index import ondisk

        folder = ondisk(kb_root, folder) or folder

    def change(items):
        at = _find(items, gid)
        if name is not None:
            _unique(items, name, skip=gid)
            items[at]["name"] = name
        if color is not None:
            items[at]["color"] = color
        if folder is not KEEP:
            if folder is None:
                items[at].pop("kb_folder", None)
            else:
                items[at]["kb_folder"] = folder
        return items, items[at]

    return _with_moved(*_change(root, change))


def delete(root: Path, gid) -> dict:
    """Убрать группу из списка → {"group", "index"} (для «Отменить»).
    meta.json встреч не трогается."""
    def change(items):
        at = _find(items, gid)
        group = items.pop(at)
        return items, {"group": group, "index": at}

    return _with_moved(*_change(root, change))


def reorder(root: Path, ids) -> dict:
    """Новый порядок: `ids` — группы по порядку; не названные остаются за ними
    в прежнем порядке → {"groups", "changed"}. Тот же порядок — файл не
    пишется. Неизвестный id или повтор — GroupError."""
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise GroupError("нужен список id групп")
    if len(set(ids)) != len(ids):
        raise GroupError("группа в списке дважды")

    def change(items):
        by_id = {g["id"]: g for g in items}
        if any(i not in by_id for i in ids):
            raise GroupError("таких групп нет — обновите список")
        ordered = [by_id[i] for i in ids] + [g for g in items if g["id"] not in set(ids)]
        if [g["id"] for g in ordered] == [g["id"] for g in items]:
            return None, {"groups": items, "changed": False}
        return ordered, {"groups": ordered, "changed": True}

    return _with_moved(*_change(root, change))


def _ids(value, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
        raise GroupError(f"{what} — список id записей")
    return list(dict.fromkeys(value))


def _recording(root: Path, rid: str) -> Path | None:
    """Папка записи по id — только внутри папки записей (id приходит из сети)
    и не служебная (с точки: отложенная к удалению или на проверку)."""
    if not rid or rid.startswith("."):
        return None
    try:
        base = Path(root).resolve()
        folder = (base / rid).resolve()
    except (OSError, ValueError):
        return None
    if folder.parent != base or folder.name.startswith(".") or not library.is_recording(folder):
        return None
    return folder


def members(root: Path, gid, add=None, remove=None, restore=False) -> dict:
    """Перенести встречи в группу (`add`: прежняя группа встречи заменяется)
    и (или) убрать из неё (`remove`: только если встреча сейчас в этой
    группе) → {"changed": [id…], "failed": [{"id", "error"}]}: каждая запись —
    отдельно, неудача одной не отменяет остальные и названа честно. Добавлять
    — только в группу из списка; убирать можно и неизвестную («Убрать из
    встреч»). `restore` — «Отменить» перенос: вернуть встречи в группу, id
    которой только что был у них в meta.json, даже если её нет в списке
    (неизвестная): пишется только meta.json, запись в файле групп не
    появляется. Без него в неизвестную группу — по-прежнему отказ."""
    if not valid_id(gid):
        raise GroupError("негодный id группы")
    add, remove = _ids(add, "add"), _ids(remove, "remove")
    if not add and not remove:
        raise GroupError("нечего менять: нужны add или remove")
    if set(add) & set(remove):
        raise GroupError("запись и в add, и в remove")
    if add and restore is not True and not any(g["id"] == gid for g in load(root)):
        raise GroupError(f"{NO_GROUP} — обновите список")
    changed: list[str] = []
    failed: list[dict] = []
    for rid, adding in [(r, True) for r in add] + [(r, False) for r in remove]:
        folder = _recording(root, rid)
        if folder is None:
            failed.append({"id": rid, "error": "записи нет"})
            continue
        moved = False

        def change(meta: dict) -> dict:
            nonlocal moved
            now = of(meta)
            if adding and (now != gid or "groups" in meta):
                moved = now != gid
                return _with_group(meta, gid)
            if not adding and now == gid:
                moved = True
                return _with_group(meta, None)
            return meta

        try:
            library.update_meta(folder, change)
        except OSError as e:
            failed.append({"id": rid, "error": f"не удалось записать meta.json: {e}"})
            continue
        if moved:
            changed.append(rid)
    return {"changed": changed, "failed": failed}


def summary(items: list[dict], cards) -> dict:
    """Группы со счётчиками встреч (среди `cards`), неизвестные id из
    meta.json и встречи без группы: {"groups": [{id, name, color, count}],
    "unknown": [{id, count}], "none": n} — неизвестные по убыванию числа встреч."""
    counts: dict[str, int] = {}
    none = 0
    for card in cards:
        gid = card.get("group")
        if gid:
            counts[gid] = counts.get(gid, 0) + 1
        else:
            none += 1
    known = {g["id"] for g in items}
    unknown = sorted(((gid, n) for gid, n in counts.items() if gid not in known), key=lambda x: (-x[1], x[0]))
    return {"groups": [{"id": g["id"], "name": g["name"], "color": g["color"], "count": counts.get(g["id"], 0)}
                       for g in items],
            "unknown": [{"id": gid, "count": n} for gid, n in unknown], "none": none}
