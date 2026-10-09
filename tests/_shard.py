"""Куски набора тестов для CI: MEET_TEST_SHARD="k/n" — прогнать k-й из n.

Тест попадает в кусок по CRC32 своего id: раскладка одинакова в каждом
процессе (xdist собирает набор в каждом рабочем) и не зависит от порядка.
"""

from __future__ import annotations

import zlib

ENV = "MEET_TEST_SHARD"


def parse_shard(spec: str) -> tuple[int, int] | None:
    """«k/n» → (k, n); пусто — весь набор. Неверное значение — ValueError."""
    spec = spec.strip()
    if not spec:
        return None
    k_text, sep, n_text = spec.partition("/")
    if not sep or not k_text.isdigit() or not n_text.isdigit():
        raise ValueError(f"{ENV}: нужно «k/n», а не {spec!r}")
    k, n = int(k_text), int(n_text)
    if n < 1 or not 1 <= k <= n:
        raise ValueError(f"{ENV}: кусок {k} из {n} не бывает")
    return k, n


def in_shard(nodeid: str, shard: tuple[int, int]) -> bool:
    k, n = shard
    return zlib.crc32(nodeid.encode("utf-8")) % n == k - 1
