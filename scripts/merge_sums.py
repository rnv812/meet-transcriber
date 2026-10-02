"""Слить SHA256SUMS.txt выпуска с суммами новых файлов (образ macOS).

    python3 scripts/merge_sums.py ОПУБЛИКОВАННЫЕ НОВЫЕ ИТОГ

Строки опубликованного файла остаются (установщик Windows ищет в нём свою
строку по точному имени), кроме строк тех же файлов, что есть в новых:
повторная публикация заменяет строку образа, а не дублирует её. Новые строки
идут в конец. Терпит BOM, CRLF и файл без перевода строки в конце; итог —
LF, одна строка на файл, печатается в stdout. Пробный прогон
(`publish-macos-dry-run` в release.yml) зовёт тот же скрипт, ничего не
загружая.

Только stdlib: скрипт запускается на чистом раннере без установки пакетов.
"""

import re
import sys
from pathlib import Path

LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


def parse(text: str, strict: bool) -> list[tuple[str, str]]:
    """Строки `<hex>  <имя>` (или `<hex> *<имя>`) → [(hex в нижнем регистре, имя)].
    Пустые строки пропускаются; непонятная — ошибка, если `strict`, иначе
    пропускается."""
    found = []
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = LINE.match(line)
        if not match:
            if strict:
                raise ValueError(f"непонятная строка сумм: {line!r}")
            continue
        found.append((match.group(1).lower(), match.group(2).strip()))
    return found


def merge(published: str, new: str) -> str:
    fresh = parse(new, strict=True)
    if not fresh:
        raise ValueError("в новых суммах нет ни одной строки")
    names = {name for _, name in fresh}
    kept = [(digest, name) for digest, name in parse(published, strict=False)
            if name not in names]
    return "".join(f"{digest}  {name}\n" for digest, name in kept + fresh)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 64
    published, new, out = (Path(a) for a in argv)
    text = merge(published.read_text(encoding="utf-8", errors="replace"),
                 new.read_text(encoding="utf-8"))
    out.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
