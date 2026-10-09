"""Куски набора для CI (MEET_TEST_SHARD="k/n"): каждый тест — ровно в одном куске."""

import pytest

from _shard import in_shard, parse_shard


def test_spec_is_parsed_and_checked():
    assert parse_shard("") is None
    assert parse_shard("2/3") == (2, 3)
    assert parse_shard(" 1/1 ") == (1, 1)
    for bad in ("0/3", "4/3", "x/3", "2", "2/0"):
        with pytest.raises(ValueError):
            parse_shard(bad)


def _collected(shard: str) -> set[str]:
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "MEET_TEST_SHARD": shard, "PYTHONPATH": str(root / "src")}
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          "tests/test_shard.py", "tests/test_backup.py"],
                         cwd=root, env=env, capture_output=True, text=True, encoding="utf-8").stdout
    return {line for line in out.splitlines() if "::" in line}


def test_conftest_splits_the_collected_suite():
    whole = _collected("")
    parts = [_collected(f"{k}/2") for k in (1, 2)]
    assert len(whole) > 5
    assert parts[0] | parts[1] == whole and not parts[0] & parts[1]


def test_every_test_lands_in_exactly_one_shard_and_shards_are_balanced():
    ids = [f"tests/test_m{i % 40}.py::test_{i}" for i in range(3000)]
    counts = []
    for k in (1, 2, 3):
        counts.append(sum(in_shard(i, (k, 3)) for i in ids))
    assert sum(counts) == len(ids)
    for nodeid in ids[:200]:
        assert sum(in_shard(nodeid, (k, 3)) for k in (1, 2, 3)) == 1
    assert max(counts) - min(counts) < len(ids) * 0.06
    # Раскладка не зависит от процесса (xdist собирает набор в каждом).
    assert in_shard("tests/test_a.py::test_b", (2, 3)) == in_shard("tests/test_a.py::test_b", (2, 3))
