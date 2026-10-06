"""Группы встреч: проект, клиент, серия — многие-ко-многим.

Членство — в `meta.json` встречи: `"groups": ["g-3f9a1c2e", …]`.
"""

import re

# id группы: `g-` + 8 шестнадцатеричных; проверка шире — на случай правки руками.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def valid_id(gid) -> bool:
    return isinstance(gid, str) and bool(ID_RE.match(gid))


def of(meta: dict) -> list[str]:
    """Группы встречи из meta.json: годные id по порядку, без повторов."""
    raw = meta.get("groups") if isinstance(meta, dict) else None
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(g for g in raw if valid_id(g)))
