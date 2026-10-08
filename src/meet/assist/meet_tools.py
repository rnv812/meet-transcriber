"""Инструменты Meet для ассистента (0.5): открыть файл, показать в папке,
открыть ссылку, запустить программу, прочитать настройки Meet.

Здесь — проверки и действия; MCP-обвязка — `meet.assist.meet_mcp`, решение,
можно ли (уровень хода, карточка «Разрешить»), — ворота `meet.llm.consent`.
Проверки — вторая линия: и после «Разрешить» Meet не откроет сетевой путь,
исполняемый файл как документ или локальный адрес.

- `open_file` — документ, картинку, текст программой по умолчанию; только
  локальный абсолютный путь, файл, расширение из `OPEN_EXTS`.
- `show_in_folder` — папку файла с выделенным файлом (ничего не запускает).
- `open_url` — http(s) в браузере; локальные адреса (API Meet) — нет.
- `launch_app` — программу (.exe, .lnk, .app); ворота пускают её только после
  «Разрешить».
- `settings_view` — настройки Meet без секретов (ключи с token, key,
  password, secret выброшены на любой глубине).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

# Что открывается программой по умолчанию: документы, таблицы, картинки, текст.
OPEN_EXTS = frozenset({
    "pdf", "doc", "docx", "odt", "rtf", "txt", "md", "csv", "tsv", "xls", "xlsx", "ods", "ppt", "pptx", "odp",
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "json", "yaml", "yml", "xml", "log", "html", "htm",
    "mp3", "wav", "ogg", "opus", "m4a", "mp4", "mkv", "webm", "mov", "drawio", "vsdx",
})
# Что запускается как программа (`launch_app`).
APP_EXTS = frozenset({"exe", "lnk", "app"})
_SECRET_KEY = re.compile(r"(token|secret|password|passwd|api[_-]?key|apikey|credential)", re.I)


class ToolRefusal(ValueError):
    """Meet не выполнит вызов — текст для агента (и строки вызова в чате)."""


def _local_file(path: str) -> Path:
    text = str(path or "").strip().strip('"')
    if not text or "\0" in text:
        raise ToolRefusal("нужен путь к файлу")
    if text.startswith(("\\\\", "//")) or re.match(r"(?i)^[a-z][a-z0-9+.-]+://", text):
        raise ToolRefusal("сетевые пути и адреса Meet не открывает — только файлы на этом компьютере")
    p = Path(os.path.expanduser(text))
    if not p.is_absolute():
        raise ToolRefusal("нужен полный путь к файлу")
    rest = str(p)[2:] if re.match(r"^[A-Za-z]:", str(p)) else str(p)
    if ":" in rest:
        raise ToolRefusal("такой путь Meet не открывает")       # поток NTFS: file.txt:stream
    p = p.resolve()
    if not p.exists():
        raise ToolRefusal(f"файла нет: {p}")
    return p


def check_open(path: str) -> Path:
    p = _local_file(path)
    if not p.is_file():
        raise ToolRefusal(f"это не файл: {p}")
    ext = p.suffix.lower().lstrip(".")
    if ext not in OPEN_EXTS:
        raise ToolRefusal(f"файлы .{ext or '?'} Meet не открывает — только документы, картинки и текст")
    return p


def check_reveal(path: str) -> Path:
    return _local_file(path)


def check_url(url: str) -> str:
    from meet.llm.consent import is_local_host

    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ToolRefusal("открыть можно только адрес http(s)")
    if is_local_host(parts.hostname):
        raise ToolRefusal("локальные адреса Meet не открывает")
    return text


def check_app(path: str) -> Path:
    p = _local_file(path)
    ext = p.suffix.lower().lstrip(".")
    if ext not in APP_EXTS:
        raise ToolRefusal("запустить можно только программу (.exe, .lnk, .app)")
    return p


def _system_open(target: str) -> None:
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 — проверено выше
    else:
        subprocess.Popen(["open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _system_reveal(p: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{p}"])
    else:
        subprocess.Popen(["open", "-R", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_file(path: str, *, opener=_system_open) -> str:
    p = check_open(path)
    opener(str(p))
    return f"Открыт файл {p.name}"


def show_in_folder(path: str, *, revealer=_system_reveal) -> str:
    p = check_reveal(path)
    revealer(p)
    return f"Показана папка {p.parent if p.is_file() else p}"


def open_url(url: str, *, opener=webbrowser.open) -> str:
    text = check_url(url)
    opener(text)
    return f"Открыта ссылка {urlsplit(text).hostname}"


def launch_app(path: str, *, opener=_system_open) -> str:
    p = check_app(path)
    opener(str(p))
    return f"Запущена программа {p.stem}"


def redact(value):
    """Без секретов: ключи с token/key/password/secret выброшены на любой глубине."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if not _SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def settings_view(section: str | None = None) -> dict:
    """Настройки Meet (как `GET /settings`) без секретов; `section` — одна секция."""
    from meet import settings

    raw = redact(settings.load().to_raw())
    if section:
        if section not in raw:
            raise ToolRefusal(f"нет секции «{section}»; есть: {', '.join(sorted(raw))}")
        return {section: raw[section]}
    return raw
