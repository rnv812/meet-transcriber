"""Страж обезличивания (0.5): в коде, тестах, документации и примерах нет
названий компании, её продуктов и внутренних систем. Примеры — нейтральные
(«team-jira», «CTO Acme», «ABC-12»). Репозиторий публичный.

Слова стоп-листа — в нижнем регистре; совпадение без учёта регистра. Этот
файл сам стоп-лист содержит — он не проверяется.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STOP = (
    "Acme", "Acme", "Acme", "Acme", "Acme",
    "team-jira", "team-gitlab", "ABC-", "meet.example.com", "tasks.example.com", "atlas.example.com", "gitlab.example.com",
    "support.example.com", "партнёр",
)
BINARY = {".png", ".ico", ".icns", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".ttf", ".otf",
          ".ogg", ".opus", ".wav", ".mp3", ".mp4", ".pdf", ".zip", ".exe", ".dll", ".dmg", ".lock"}


def _tracked() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [ROOT / name for name in out.decode("utf-8").split("\0") if name]


def _hits(text: str) -> list[str]:
    low = text.lower()
    return [w for w in STOP if w in low]


def test_stop_list_matcher():
    assert _hits("Отчёт для Acme") == ["Acme"]
    assert _hits("CTO Acme") == ["Acme"]
    assert _hits("MCP team-jira, CTO Acme, ABC-12") == []


def test_no_company_names_in_tracked_files():
    files = _tracked()
    if not files:
        import pytest

        pytest.skip("не git-checkout: список файлов не получить")
    me = Path(__file__).resolve()
    bad = []
    for path in files:
        if path.resolve() == me or path.suffix.lower() in BINARY or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for word in _hits(text):
            line = next(i for i, ln in enumerate(text.splitlines(), 1) if word in ln.lower())
            bad.append(f"{path.relative_to(ROOT).as_posix()}:{line}: {word}")
    assert bad == [], "\n".join(bad)


def test_stop_words_are_plain_lowercase():
    assert all(w == w.lower() and not re.search(r"\s{2,}", w) for w in STOP)
