"""Компоненты Atlas Aurora в окне Meet: разделы components/bundle.css без правок.

Дизайн-система хранит компоненты одним файлом; окну нужны они не все, а
классы, которые окно ещё держит само, не должны появиться дважды
(app/src/theme/cssClasses.test.ts). Скрипт режет bundle.css по заголовкам
разделов `/* ── Название ── */` и складывает выбранные разделы в файлы
app/src/theme/aurora/<имя>.css — текст разделов не меняется.

Запуск (из корня репозитория):
    .venv/Scripts/python scripts/vendor_aurora.py <bundle.css> app/src/theme/aurora --only base,aurora
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

VERSION = "v2.6"
HEADER = re.compile(r"^/\* ── (.+?) ──", re.MULTILINE)

FILES: dict[str, list[str]] = {
    "base": ["База"],
    "aurora": ["Сияние", "Стекло", "Знак ИИ", "Знак агента"],
    "controls": ["Кнопки", "Поле", "Флажки и радио", "Табы и фильтры", "Переключатель",
                 "Поиск", "Размеры полей", "Ряд контролов", "Свечение в тесных местах"],
    "feedback": ["Бейдж", "Карточка", "Состояния", "Выноска и цитата"],
    "overlays": ["Выпадающий список и меню", "Подсказка", "Модалка", "Тост", "Слои"],
}


def split_sections(css: str) -> list[tuple[str, str]]:
    """Разделы по порядку: (заголовок, текст от заголовка до следующего)."""
    marks = list(HEADER.finditer(css))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(css)
        out.append((m.group(1).strip(), css[m.start():end]))
    return out


def pick(sections: list[tuple[str, str]], prefixes: list[str]) -> str:
    """Разделы, чьи заголовки начинаются с префиксов, — в порядке исходника."""
    for prefix in prefixes:
        if not any(title.startswith(prefix) for title, _ in sections):
            raise KeyError(f"в bundle.css нет раздела «{prefix}»")
    chosen = [text for title, text in sections if any(title.startswith(p) for p in prefixes)]
    return "".join(chosen).rstrip() + "\n"


def render(name: str, prefixes: list[str], sections: list[tuple[str, str]]) -> str:
    head = (f"/* Atlas Aurora {VERSION} — {name}: {', '.join(prefixes)} "
            f"(components/bundle.css без правок; scripts/vendor_aurora.py) */\n")
    return head + pick(sections, prefixes)


def main(argv: list[str] | None = None, files: dict[str, list[str]] = FILES) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--only", default=",".join(files), help="какие файлы писать, через запятую")
    args = parser.parse_args(argv)
    sections = split_sections(args.bundle.read_text(encoding="utf-8"))
    for name in [n.strip() for n in args.only.split(",") if n.strip()]:
        target = args.out / f"{name}.css"
        target.write_text(render(name, files[name], sections), encoding="utf-8", newline="\n")
        print(f"{target}: {', '.join(files[name])}")


if __name__ == "__main__":
    main()
