"""Замок meta.json между процессами: задача-подпроцесс (итоги, объединение) и
резидент правят meta.json одновременно, и ни одна правка не теряет другую."""

import subprocess
import sys
import textwrap

from meet import library


def _folder(root, name):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    return folder


def test_meta_updates_from_another_process_are_not_lost(tmp_path):
    """Задача итогов (подпроцесс) пишет `summary_at`, пока резидент
    переименовывает запись: ни одна правка не теряет другую."""
    folder = _folder(tmp_path, "2026-09-30_10-00")
    library.write_meta(folder, {"source": "auto"})
    rounds = 150
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(library.Path(library.__file__).parents[1])!r})
        from pathlib import Path
        from meet import library
        folder = Path({str(folder)!r})
        for i in range({rounds}):
            library.write_meta(folder, {{"summary_at": i, f"job{{i}}": i}})
    """)
    child = subprocess.Popen([sys.executable, "-c", script])
    for i in range(rounds):
        library.write_meta(folder, {"title": f"Встреча {i}", f"ui{i}": i})
    assert child.wait(timeout=120) == 0
    meta = library.read_meta(folder)
    assert meta["source"] == "auto"
    assert meta["title"] == f"Встреча {rounds - 1}" and meta["summary_at"] == rounds - 1
    lost = [k for k in [f"ui{i}" for i in range(rounds)] + [f"job{i}" for i in range(rounds)]
            if k not in meta]
    assert lost == []
