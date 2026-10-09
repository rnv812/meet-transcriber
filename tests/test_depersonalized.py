"""Страж обезличивания (0.5): в коде, тестах, документации и примерах нет
названий компании, её продуктов и внутренних систем. Примеры — нейтральные
(«team-jira», «CTO Acme», «ABC-12»). Репозиторий публичный.

Слова стоп-листа — в нижнем регистре; совпадение без учёта регистра. Хранятся
в base64: открытым текстом их нет ни здесь, ни в истории git (история
вычищена заменой этих слов, и открытый список в страже она испортила бы).
"""

from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_ENCODED = (
    "dm9sZ2FibG9i", "0LLQvtC70LPQsNCx0LvQvtCx", "c21hcnQgbW9uaXRvcg==", "c21hcnRtb25pdG9y",
    "c21hcnQtbW9uaXRvcg==", "dmItamlyYQ==", "dmItZ2l0bGFi", "c21kZXYt", "ZGlvbi52Yw==",
    "dGFza3Mudm9sZ2E=", "YXRsYXMudm9sZ2E=", "Z2l0bGFiLnZvbGdh", "c3VwcG9ydC52b2xnYQ==",
    "0LPRgNGD0L/Qv9CwINC90L0y",
)
STOP = tuple(base64.b64decode(w).decode("utf-8") for w in _ENCODED)
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
    company, product = STOP[0], STOP[2]
    assert _hits(f"Отчёт для {company.title()}") == [company]
    assert _hits(f"CTO {product.title()}") == [product]
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
