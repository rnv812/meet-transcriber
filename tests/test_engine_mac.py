"""Движок на macOS (Apple Silicon): профиль `mac` — torch с PyPI в venv с
раскладкой `bin/`, пакеты как у CPU; pyannote — на MPS, если он есть.
Шаги сверяются с фикстурами, которые читает и оболочка на Rust."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from meet import diarize, engine

FIXTURES = Path(__file__).parent / "fixtures"
MAC_INPUTS = dict(uv="/r/uv", env_dir="/env", wheel="/w/meet_transcriber-0.1.0-py3-none-any.whl")


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_mac_uv_steps_match_the_fixtures():
    steps = engine.uv_steps(profile="mac", **MAC_INPUTS)
    assert steps == _fixture("uv_steps_mac.json")
    assert steps[2][4] == "/env/bin/python"
    assert "--index-url" not in steps[2]  # torch с PyPI: колёса arm64 с MPS
    assert steps[3][-1].endswith("[engine-mac]")
    constrained = engine.uv_steps(profile="mac", constraints="/r/constraints-mac.txt", **MAC_INPUTS)
    assert constrained == _fixture("uv_steps_mac_constrained.json")
    assert [engine.uv_gigaam_step(profile="mac", **MAC_INPUTS)] == _fixture("uv_gigaam_step_mac.json")


def test_windows_steps_did_not_change():
    py = "C:\\env\\Scripts\\python.exe"
    assert engine.python_path("C:\\env", "cpu") == py
    assert engine.python_path("C:\\env", "cuda") == py
    assert engine.torch_index("cuda") == engine.TORCH_CUDA_INDEX
    assert engine.torch_index("cpu") == engine.TORCH_CPU_INDEX
    assert engine.torch_index("mac") is None


def test_mac_profile_is_chosen_on_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))  # папка данных macOS — от HOME
    assert engine.profile_for({"available": False}) == "mac"
    assert engine.flavor_for(False) == "mac"
    monkeypatch.setattr(engine, "gpu", lambda: {"available": False, "name": None})
    state = engine.state()
    assert state["flavor"] == "mac"
    assert state["download_gb"] == engine.DOWNLOAD_HINT_GB["mac"]
    steps = engine.install_steps()
    assert steps[0][-1] == "torch"  # без --index-url
    assert engine.CUDA_RUNTIME[0] not in steps[1]
    monkeypatch.setattr(sys, "platform", "win32")
    assert engine.profile_for({"available": True}) == "cuda"
    assert engine.profile_for({"available": False}) == "cpu"


class _Torch:
    def __init__(self, mps: bool):
        self.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps))

    @staticmethod
    def device(kind):
        return SimpleNamespace(type=kind)


def test_pyannote_device_prefers_mps_on_mac(monkeypatch):
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert diarize.pick_device(_Torch(mps=True), use_cuda=False).type == "mps"
    import os

    assert os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
    assert diarize.pick_device(_Torch(mps=False), use_cuda=False).type == "cpu"
    monkeypatch.setattr(sys, "platform", "win32")
    assert diarize.pick_device(_Torch(mps=True), use_cuda=False).type == "cpu"
    assert diarize.pick_device(_Torch(mps=True), use_cuda=True).type == "cuda"
