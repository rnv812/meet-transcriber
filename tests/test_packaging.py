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


AUTHORS = ["Andrey Aleynikov", "Nikita Reznikov", "ndrsvh"]


def test_license_and_authors_are_declared_everywhere():
    """Лицензия и авторы одни и те же в пакете, оболочке и установщике."""
    import json

    project = _project()
    assert project["license"] == "Apache-2.0"
    assert [a["name"] for a in project["authors"]] == AUTHORS
    cargo = tomllib.loads((ROOT / "app" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))
    assert cargo["package"]["license"] == "Apache-2.0"
    assert cargo["package"]["authors"] == AUTHORS
    bundle = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json")
                        .read_text(encoding="utf-8"))["bundle"]
    assert bundle["license"] == "Apache-2.0"
    assert all(name in bundle["copyright"] for name in AUTHORS)
    # Издатель — тот же, что Tauri выводил из идентификатора: от него зависит
    # ключ реестра HKCU\Software\<издатель>\meet, где установщик ищет прежнюю
    # папку установки при обновлении.
    assert bundle["publisher"] == "meet"
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text and "Version 2.0, January 2004" in license_text
    assert "Copyright 2026 Andrey Aleynikov, Nikita Reznikov, ndrsvh" in license_text
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    for name in AUTHORS + ["ffmpeg", "uv", "faster-whisper", "CTranslate2", "pyannote"]:
        assert name in notice


def test_wheel_carries_license(wheel):
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        meta = z.read(next(n for n in names if n.endswith("/METADATA"))).decode()
    assert "License-Expression: Apache-2.0" in meta
    assert any(n.endswith("/LICENSE") for n in names)
    assert any(n.endswith("/NOTICE") for n in names)
