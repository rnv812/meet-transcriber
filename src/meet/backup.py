"""Копия настроек перед откатом версии (0.5, «О программе» → «Установить эту версию»).

Старая версия может не понять настройки новой — перед откатом окно просит
резидента (`POST /backup`) сложить в zip всё, что вернуть руками дорого:
`config.json`, базу голосов и служебные файлы папки данных. Записи, движок,
журналы и временные встречи не трогаются — они большие, и откат их не портит.
Секреты (`api.token`, `daemon.json` с токеном control API) в архив не идут.
Ссылки (symlink, junction) не проходятся.
"""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

FOLDER = "backups"
# Папки данных, которые в копию не идут: большие или пересоздаются сами.
SKIP_DIRS = frozenset({"engine", "logs", "recordings", "tmp-meetings", FOLDER, "models", "cache"})
# Секреты и то, что принадлежит работающему процессу.
SKIP_FILES = frozenset({"api.token", "daemon.json", "tray.lock"})
SKIP_SUFFIXES = (".token", ".lock", ".exe", ".dll", ".part", ".tmp")
# Служебный файл больше этого — не настройка, а данные (не копируем).
MAX_FILE = 50 * 1024 * 1024


def _plain_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_FILE
    except OSError:
        return False


def _service_files(data_dir: Path) -> list[Path]:
    out = []
    for path in sorted(data_dir.iterdir()):
        name = path.name.lower()
        if name in SKIP_FILES or name.endswith(SKIP_SUFFIXES) or "token" in name:
            continue
        if _plain_file(path):
            out.append(path)
    return out


def _voice_files(voices: Path) -> list[tuple[Path, str]]:
    if not voices.is_dir() or voices.is_symlink() or _is_junction(voices):
        return []
    out = []
    for root, dirs, files in os.walk(voices, followlinks=False):
        here = Path(root)
        dirs[:] = sorted(d for d in dirs if not (here / d).is_symlink() and not _is_junction(here / d))
        for name in sorted(files):
            path = here / name
            if _plain_file(path):
                out.append((path, "voices/" + path.relative_to(voices).as_posix()))
    return out


def _is_junction(path: Path) -> bool:
    try:
        return path.is_junction()
    except (AttributeError, OSError):
        return False


def _safe_label(label: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", label).strip("._") or "version"


def make(data_dir: Path, voices: Path | None, target: str, *, now: datetime | None = None) -> Path:
    """Архив `<data_dir>/backups/<время>-before-<версия>.zip`; путь к нему."""
    out_dir = data_dir / FOLDER
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    path = out_dir / f"{stamp}-before-{_safe_label(target)}.zip"
    partial = path.with_name(path.name + ".part")
    entries = [(p, p.name) for p in _service_files(data_dir)]
    if voices is not None:
        entries += _voice_files(voices)
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            for source, name in entries:
                z.write(source, name)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path
