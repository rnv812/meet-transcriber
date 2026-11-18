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


# --- MLX Whisper: необязательный шаг только для macOS 14+ ---------------------

MLX_CONSTRAINTS = "/r/constraints-mac-mlx.txt"


def test_mlx_extra_mirrors_the_packages_and_stays_out_of_the_engine():
    import tomllib

    extras = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"))["project"]["optional-dependencies"]
    assert engine.MLX_PACKAGES == ("mlx>=0.30", "mlx-whisper>=0.4.3")
    assert extras["mlx"] == [f"{spec}; sys_platform == 'darwin'" for spec in engine.MLX_PACKAGES]
    for profile in ("engine-mac", "engine-cpu", "engine-cuda", "gigaam"):
        assert not any(spec.startswith("mlx") for spec in extras[profile])


def test_mlx_step_matches_the_fixtures_shared_with_rust():
    step = engine.uv_mlx_step(**MAC_INPUTS)
    assert [step] == _fixture("uv_mlx_step_mac.json")
    assert step[4] == "/env/bin/python" and step[-1].endswith("[mlx]")
    constrained = engine.uv_mlx_step(constraints=MLX_CONSTRAINTS, **MAC_INPUTS)
    assert [constrained] == _fixture("uv_mlx_step_mac_constrained.json")
    assert engine.MLX_CONSTRAINTS_PROFILE == "mac-mlx"  # constraints-mac-mlx.txt
    # Основные шаги MLX не трогают ни на одном профиле.
    for profile in ("mac", "cpu", "cuda"):
        assert not any("mlx" in arg for s in engine.uv_steps(profile=profile, **MAC_INPUTS) for arg in s)


def test_mlx_step_is_wanted_only_on_macos_14_or_newer():
    assert engine.mlx_step_wanted("mac", (14, 0)) is True
    assert engine.mlx_step_wanted("mac", (15, 3, 1)) is True
    assert engine.mlx_step_wanted("mac", (13, 6)) is False
    assert engine.mlx_step_wanted("mac", None) is False
    assert engine.mlx_step_wanted("cpu", (14, 0)) is False
    assert engine.mlx_step_wanted("cuda", None) is False


def _mac(monkeypatch, tmp_path, version):
    from meet import mlx_asr

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(mlx_asr, "macos_version", lambda: version)
    monkeypatch.setattr(engine, "gpu", lambda: {"available": False, "name": None})
    monkeypatch.setattr(engine.sys, "prefix", str(tmp_path))


def _mlx_component(state):
    return next((c for c in state["components"] if c["module"] == "mlx_whisper"), None)


def test_install_steps_add_mlx_after_the_others_on_macos_14(monkeypatch, tmp_path):
    _mac(monkeypatch, tmp_path, (14, 2))
    steps = engine.install_steps()
    assert steps[-1][-len(engine.MLX_PACKAGES):] == list(engine.MLX_PACKAGES)
    assert steps[-2][-1] == engine.GIGAAM
    _mac(monkeypatch, tmp_path, (13, 6))
    assert not any("mlx" in arg for s in engine.install_steps() for arg in s)


def test_failed_mlx_step_leaves_the_engine_installed(monkeypatch, tmp_path):
    _mac(monkeypatch, tmp_path, (14, 2))
    calls, lines = [], []

    def runner(argv, on_line):
        calls.append(argv)
        return 1 if argv[-1] == engine.MLX_PACKAGES[-1] else 0

    assert engine.install(on_line=lines.append, runner=runner) == 0
    assert len(calls) == 4
    assert lines[-1].startswith("MLX Whisper не установился")
    # Сбой GigaAM на 14+ тоже не мешает поставить MLX следующим шагом.
    calls.clear()
    assert engine.install(runner=lambda argv, _: calls.append(argv) or (
        1 if argv[-1] == engine.GIGAAM else 0)) == 0
    assert len(calls) == 4


def test_components_mark_mlx_as_needing_macos_14_on_macos_13(monkeypatch, tmp_path):
    _mac(monkeypatch, tmp_path, (13, 6))
    monkeypatch.setattr(engine, "installed", lambda module: module != "mlx_whisper")
    state = engine.state()
    mlx = _mlx_component(state)
    assert mlx["optional"] and not mlx["installed"]
    assert mlx["note"] == "нужна macOS 14 или новее"
    assert state["installed"] is True and state["missing"] == []


def test_components_show_mlx_install_error_on_macos_14(monkeypatch, tmp_path):
    _mac(monkeypatch, tmp_path, (14, 2))
    monkeypatch.setattr(engine, "installed", lambda module: module != "mlx_whisper")
    mlx = _mlx_component(engine.state())
    assert mlx["note"] == "не установлен — будет установлен при обновлении движка"
    (tmp_path / engine.MLX_ERROR_FILE).write_text(
        "нет связи с PyPI — проверьте подключение или прокси в настройках", encoding="utf-8")
    assert engine.mlx_install_error(tmp_path) == (
        "MLX Whisper не установился: нет связи с PyPI — проверьте подключение или прокси в "
        "настройках — используется Whisper на процессоре")
    assert _mlx_component(engine.state())["note"].startswith("MLX Whisper не установился: нет связи")
    monkeypatch.setattr(engine, "installed", lambda module: True)
    assert "note" not in _mlx_component(engine.state())


def test_windows_has_no_mlx_step_or_component(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(engine, "gpu", lambda: {"available": False, "name": None})
    # shutil.which при подменённой win32 на Mac идёт в _winapi, которого там нет.
    monkeypatch.setattr(engine.shutil, "which", lambda name, *a, **kw: None)
    assert _mlx_component(engine.state()) is None
    for flavor in ("cpu", "cuda"):
        assert not any("mlx" in arg for s in engine.install_steps(flavor) for arg in s)


def test_mac_build_compiles_mlx_constraints_for_macos_14():
    """Отдельный файл точных версий: MLX 0.30+ выпускается только под macOS
    14+, а сборка целится в 13.0; общие пакеты — те же, что у основного."""
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build_release_macos.sh").read_text(
        encoding="utf-8")
    compile_line = next(line for line in script.splitlines() if "--extra mlx" in line)
    assert compile_line.lstrip().startswith("MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile")
    assert '--constraint "$CONSTRAINTS"' in script.split("--extra mlx", 1)[1].split("\n\n", 1)[0]
    assert 'cp "$MLX_CONSTRAINTS" "$RESOURCES/constraints-mac-mlx.txt"' in script
    assert "constraints-mac.txt constraints-mac-mlx.txt; do" in script


# --- Time Machine и Spotlight: движок и модели — мимо ---


def _storage(monkeypatch, tmp_path, models=True):
    from meet import paths

    eng, mod, rec = tmp_path / "engine", tmp_path / "models", tmp_path / "recordings"
    eng.mkdir()
    rec.mkdir()
    if models:
        mod.mkdir()
    monkeypatch.setattr(paths, "engine_dir", lambda: eng)
    monkeypatch.setattr(paths, "models_dir", lambda: mod)
    return eng, mod, rec


def _tmutil(code=0, calls=None):
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=code, stdout=b"", stderr=b"")
    return run


def test_mac_engine_and_models_leave_time_machine_and_spotlight(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    eng, mod, rec = _storage(monkeypatch, tmp_path)
    calls = []
    engine.exclude_from_backups(run=_tmutil(calls=calls))
    assert calls == [["tmutil", "addexclusion", str(eng)], ["tmutil", "addexclusion", str(mod)]]
    assert all(str(rec) not in argv for argv in calls)  # записи остаются в резервной копии
    assert (eng / ".metadata_never_index").is_file()
    assert not (rec / ".metadata_never_index").exists()


def test_mac_models_not_yet_downloaded_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    eng, mod, _ = _storage(monkeypatch, tmp_path, models=False)
    calls = []
    engine.exclude_from_backups(run=_tmutil(calls=calls))
    assert calls == [["tmutil", "addexclusion", str(eng)]]
    assert not mod.exists()  # папку моделей не создаём


def test_mac_tmutil_failures_are_only_logged(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    _storage(monkeypatch, tmp_path)
    lines = []
    engine.exclude_from_backups(run=_tmutil(code=1, calls=[]), log=lines.append)
    assert len(lines) == 2

    def broken(argv, **kwargs):
        raise OSError("нет tmutil")

    lines.clear()
    engine.exclude_from_backups(run=broken, log=lines.append)
    assert len(lines) == 2


def test_windows_has_nothing_to_exclude(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    eng, _, _ = _storage(monkeypatch, tmp_path)
    engine.exclude_from_backups(run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("не Mac")))
    assert not (eng / ".metadata_never_index").exists()
