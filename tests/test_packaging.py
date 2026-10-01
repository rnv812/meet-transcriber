"""Колесо пакета: то, что ставит инсталлятор, должно собираться и нести всё нужное."""

import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

from meet import engine

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _norm(req: str) -> str:
    return re.sub(r"\s+", "", req).lower()


def test_extras_mirror_engine_constants():
    extras = _project()["optional-dependencies"]
    base = [_norm(r) for r in engine.PACKAGES]
    assert [_norm(r) for r in extras["engine-cpu"]] == base
    assert [_norm(r) for r in extras["engine-cuda"]] == base + list(engine.CUDA_RUNTIME)


def test_core_dependencies_cover_resident_imports():
    names = {re.split(r"[<>=\[ ]", d)[0].lower() for d in _project()["dependencies"]}
    for needed in ("pystray", "pillow", "pycaw", "psutil", "pyaudiowpatch", "numpy",
                   "scipy", "aiohttp", "claude-agent-sdk", "keyring"):
        assert needed in names


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv не найден в PATH")
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(out)], cwd=ROOT, check=True,
                   capture_output=True)
    (path,) = out.glob("meet_transcriber-*-py3-none-any.whl")
    return path


def test_wheel_contents_and_entry_points(wheel):
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        assert "meet/__init__.py" in names
        assert any(n.startswith("meet/llm/") for n in names)
        assert any(n.startswith("meet/assist/") for n in names)
        entry = next(n for n in names if n.endswith("entry_points.txt"))
        text = z.read(entry).decode()
        meta = z.read(next(n for n in names if n.endswith("/METADATA"))).decode()
    scripts, _, gui = text.partition("[gui_scripts]")
    assert "meet = meet.cli:main" in scripts
    assert "meet-tray = meet.tray:main" in gui
    assert "Provides-Extra: engine-cuda" in meta and "Provides-Extra: engine-cpu" in meta
