"""Движок расшифровки: определение состояния и сборка команд установки.

Настоящую установку тесты не запускают — она идёт гигабайтами и минутами;
проверяется то, что решает, *что именно* будет установлено.
"""

import sys
from pathlib import Path

from meet import engine


def test_state_lists_components_and_target():
    state = engine.state()
    modules = [c["module"] for c in state["components"]]
    assert "faster_whisper" in modules and "torch" in modules
    assert state["python"] == sys.executable
    assert isinstance(state["installed"], bool)
    assert state["installed"] is (state["missing"] == [])


def test_installed_uses_find_spec_without_importing():
    """Проверка не должна тянуть CUDA в память резидента."""
    assert engine.installed("json") is True
    assert engine.installed("такого-модуля-нет") is False
    assert "torch" not in sys.modules or engine.installed("torch")


def test_cuda_flavor_takes_the_cuda_index():
    steps = engine.install_steps("cuda")
    torch_step = steps[0]
    assert "torch" in torch_step
    assert engine.TORCH_CUDA_INDEX in torch_step
    # рантайм CUDA нужен ctranslate2 — он ищет cuBLAS/cuDNN в site-packages
    assert any("nvidia-cublas-cu12" in step for step in steps)


def test_cpu_flavor_avoids_cuda_wheels():
    steps = engine.install_steps("cpu")
    assert engine.TORCH_CPU_INDEX in steps[0]
    assert not any("nvidia-cublas-cu12" in step for step in steps)


def test_torch_is_installed_separately():
    """Смешивать torch с остальными пакетами в одной команде нельзя: он живёт
    на своём индексе, и так утягивается не та сборка."""
    steps = engine.install_steps("cuda")
    assert len(steps) == 3  # torch, движок, необязательный GigaAM
    assert "faster-whisper>=1.1" not in steps[0]
    assert "--index-url" not in steps[1]
    assert steps[2][-1] == engine.GIGAAM


def test_install_stops_after_a_failed_step():
    calls = []

    def runner(argv, on_line):
        calls.append(argv)
        return 1  # первый же шаг упал

    assert engine.install("cpu", runner=runner) == 1
    assert len(calls) == 1  # второй шаг не запускался


def test_install_reports_lines():
    lines = []

    def runner(argv, on_line):
        on_line("Downloading torch-2.9.0")
        return 0

    assert engine.install("cpu", on_line=lines.append, runner=runner) == 0
    assert "Downloading torch-2.9.0" in lines


def test_missing_gpu_is_not_an_error(monkeypatch):
    """Отсутствие nvidia-smi нормально для ноутбука — это «не видно», а не сбой."""
    monkeypatch.setattr(engine.shutil, "which", lambda name: None)
    assert engine.gpu() == {"available": False, "name": None}
    assert engine.state()["flavor"] == "cpu"


# --- каталог моделей ------------------------------------------------------


def test_catalogue_covers_the_pipeline():
    from meet import models

    kinds = {model["kind"] for model in models.CATALOGUE}
    assert kinds == {models.ASR, models.DIARIZATION, models.ALIGN}
    recommended = [m for m in models.CATALOGUE if m.get("recommended")]
    assert len(recommended) == 1  # ровно одна модель по умолчанию


def test_gated_model_is_marked_and_blocked_without_token(monkeypatch):
    """Молча упереться в 401 хуже, чем заранее сказать, что нужен токен."""
    from meet import models

    monkeypatch.setattr(models, "token", lambda: None)
    state = models.state()
    gated = [m for m in state["items"] if m.get("gated")]
    assert gated and gated[0]["blocked"] is True
    monkeypatch.setattr(models, "token", lambda: "hf_xxx")
    assert models.state()["items"][3]["blocked"] is False


def test_cache_root_follows_hf_env(monkeypatch, tmp_path):
    """Свой кэш заводить нельзя: уже скачанное не должно качаться заново."""
    from meet import models

    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert models.cache_root() == tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "прямо"))
    assert models.cache_root() == tmp_path / "прямо"


def test_downloaded_requires_a_non_empty_snapshot(monkeypatch, tmp_path):
    """Оборванная загрузка оставляет папку — «скачано» тогда означало бы «пусто»."""
    from meet import models

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    repo = "Systran/faster-whisper-medium"
    folder = tmp_path / "models--Systran--faster-whisper-medium"
    (folder / "snapshots" / "abc").mkdir(parents=True)
    assert models.downloaded(repo) is False
    (folder / "snapshots" / "abc" / "model.bin").write_bytes(b"x")
    assert models.downloaded(repo) is True
    assert models.size_on_disk(repo) == 1


def test_download_refuses_unknown_repo():
    """Скачивать что попало по строке из сети нельзя: это путь на диск и трафик."""
    from meet import models

    lines = []
    assert models.download("злой/репозиторий", on_line=lines.append) == 2
    assert "каталога" in lines[0]


def test_gated_error_is_explained():
    from meet import models

    text = models._explain("pyannote/x", RuntimeError("401 Client Error: gated repo"))
    assert "примите" in text and "токен" in text


def test_download_needs_the_engine_and_says_so(monkeypatch):
    """huggingface_hub приходит с движком: без него честная подсказка, не крэш."""
    from meet import models

    import sys
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    lines = []
    code = models.download("Systran/faster-whisper-medium", on_line=lines.append)
    assert code == 3
    assert "движок" in lines[-1]


def test_state_flags_download_capability():
    from meet import models

    state = models.state()
    assert "can_download" in state
    assert state["can_download"] == models._hub_available()


def test_pyav_is_pinned_below_19():
    """faster-whisper 1.2 зовёт av.open(metadata_errors=...), которого нет в av 19."""
    assert "av>=11,<19" in engine.PACKAGES
    extras = engine.install_steps("cpu")[1]
    assert "av>=11,<19" in extras


def test_engine_packages_have_upper_bounds():
    """Новая мажорная версия не должна приезжать к пользователю сама: каждая
    из них ломала API (pyannote 3→4, transformers 4→5)."""
    for spec in ("faster-whisper>=1.2,<2", "pyannote.audio>=4.0,<5", "transformers>=4.40,<6"):
        assert spec in engine.PACKAGES


def test_state_reports_device_speed_and_disk(monkeypatch):
    monkeypatch.setattr(engine, "_device", lambda available: "cpu")
    state = engine.state()
    assert state["device"] == "cpu"
    assert state["backend"] == "gigaam"  # движок процессора по умолчанию
    assert state["speed_factor"] == engine.SPEED_FACTOR["cpu"]["gigaam"]
    assert state["disk_free_gb"] > 0


def test_estimate_seconds_scales_with_duration():
    assert engine.estimate_seconds(600, "cuda") == 600 * engine.SPEED_FACTOR["cuda"]["faster-whisper"]
    assert engine.estimate_seconds(600, "cpu") == 600 * engine.SPEED_FACTOR["cpu"]["gigaam"]
    assert engine.estimate_seconds(600, "cpu", "faster-whisper") > engine.estimate_seconds(600, "cuda")
    assert engine.estimate_seconds(600, "непонятно") == 600 * engine.SPEED_FACTOR["cpu"]["gigaam"]


def test_speed_factor_per_backend():
    """Whisper — прежние замеры (30.09); GigaAM на процессоре в разы быстрее
    Whisper medium; «whisper.cpp» и прочее не-GigaAM считается как Whisper."""
    assert engine.speed_factor("cpu", "faster-whisper") == 1.26
    assert engine.speed_factor("cuda", "faster-whisper") == 0.22
    assert engine.speed_factor("cpu", "gigaam") < engine.speed_factor("cpu", "faster-whisper") / 2
    assert engine.speed_factor("cpu", "whisper.cpp") == engine.speed_factor("cpu", "faster-whisper")
    assert engine.speed_factor("cuda") == engine.speed_factor("cuda", "faster-whisper")
    assert engine.speed_factor("cpu") == engine.speed_factor("cpu", "gigaam")


def test_state_uses_the_backend_chosen_for_the_device(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps(
        {"asr": {"device": "cpu", "cpu_backend": "faster-whisper"}}), encoding="utf-8")
    state = engine.state()
    assert (state["device"], state["backend"]) == ("cpu", "faster-whisper")
    assert state["speed_factor"] == 1.26


def test_gigaam_is_pinned_to_a_commit_archive_in_both_profiles():
    """GigaAM — архив зафиксированного коммита (не git+https: git у
    пользователя может не стоять) в обоих профилях движка."""
    assert len(engine.GIGAAM_COMMIT) == 40
    assert engine.GIGAAM == ("gigaam @ https://github.com/salute-developers/GigaAM/archive/"
                             f"{engine.GIGAAM_COMMIT}.zip#sha256={engine.GIGAAM_SHA256}")
    for flavor in ("cpu", "cuda"):
        steps = engine.install_steps(flavor)
        assert engine.GIGAAM not in steps[1] and steps[2][-1] == engine.GIGAAM
    assert "gigaam" in {module for module, _ in engine.COMPONENTS}


def _with_setting(monkeypatch, value, gpu_available):
    from meet import settings

    monkeypatch.setattr(
        settings, "load",
        lambda *a, **k: type("S", (), {"asr": type("A", (), {"device": value})()})(),
    )
    monkeypatch.setattr(engine, "gpu", lambda: {"available": gpu_available, "name": None})


def test_auto_device_follows_nvidia_smi(monkeypatch):
    from meet import asr

    monkeypatch.setattr(asr, "cuda_runtime_ok", lambda **kw: True)
    _with_setting(monkeypatch, "auto", False)
    assert engine.state()["device"] == "cpu"
    _with_setting(monkeypatch, "auto", True)
    assert engine.state()["device"] == "cuda"


def test_explicit_device_setting_wins_over_gpu_probe(monkeypatch):
    _with_setting(monkeypatch, "cuda", False)
    assert engine.state()["device"] == "cuda"
    _with_setting(monkeypatch, "cpu", True)
    assert engine.state()["device"] == "cpu"


def test_state_does_not_import_ctranslate2(monkeypatch):
    from meet import asr

    def boom():
        raise AssertionError("state() не должен трогать ctranslate2")

    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    monkeypatch.setattr(asr, "cuda_available", boom)
    _with_setting(monkeypatch, "auto", False)
    assert engine.state()["device"] == "cpu"


def _unavailable_disk(path):
    raise FileNotFoundError(2, "Не удаётся найти указанный путь", str(path))


def test_free_gb_is_none_for_unavailable_location(monkeypatch, tmp_path):
    """Отключённый диск или недоступная UNC-шара — «не знаю», а не исключение:
    иначе снимок состояния не собирается вовсе."""
    monkeypatch.setattr(engine.shutil, "disk_usage", _unavailable_disk)
    assert engine._free_gb(tmp_path / "нет" / "папки") is None


def test_state_carries_unknown_free_space(monkeypatch):
    monkeypatch.setattr(engine, "_device", lambda available: "cpu")
    monkeypatch.setattr(engine.shutil, "disk_usage", _unavailable_disk)
    assert engine.state()["disk_free_gb"] is None


# --- установка колеса в приватный venv (инсталлятор) ---

UV_INPUTS = dict(
    uv="uv.exe",
    env_dir="C:\\env",
    wheel="C:\\w\\meet_transcriber-0.1.0-py3-none-any.whl",
)
UV_CONSTRAINTS = {p: f"C:\\r\\constraints-{p}.txt" for p in ("cuda", "cpu")}


def test_profile_for_follows_gpu_shape():
    assert engine.profile_for({"available": True, "name": "RTX"}) == "cuda"
    assert engine.profile_for({"available": False, "name": None}) == "cpu"
    assert engine.profile_for({}) == "cpu"


def test_uv_steps_cuda_shape():
    py = "C:\\env\\Scripts\\python.exe"
    assert engine.uv_steps(profile="cuda", **UV_INPUTS) == [
        ["uv.exe", "python", "install", "3.12"],
        ["uv.exe", "venv", "--python", "3.12", "C:\\env"],
        ["uv.exe", "pip", "install", "--python", py, "torch==2.11.*", "torchaudio==2.11.*",
         "--index-url", engine.TORCH_CUDA_INDEX],
        ["uv.exe", "pip", "install", "--python", py,
         UV_INPUTS["wheel"] + "[engine-cuda]"],
    ]


def test_uv_steps_cpu_uses_cpu_index_and_extra():
    steps = engine.uv_steps(profile="cpu", **UV_INPUTS)
    assert steps[2][-1] == engine.TORCH_CPU_INDEX
    assert steps[3][-1].endswith("[engine-cpu]")


def test_uv_steps_pin_torch_to_one_minor_for_both_profiles():
    # Без пина CPU-индекс отдавал torch 2.14, CUDA-индекс — 2.11: два разных
    # движка под одной версией приложения.
    assert list(engine.TORCH_SPECS) == ["torch==2.11.*", "torchaudio==2.11.*"]
    for profile in ("cuda", "cpu"):
        step = engine.uv_steps(profile=profile, **UV_INPUTS)[2]
        assert step[5:7] == list(engine.TORCH_SPECS)


def test_uv_steps_add_constraints_to_torch_and_engine_steps():
    c = UV_CONSTRAINTS["cuda"]
    steps = engine.uv_steps(profile="cuda", constraints=c, **UV_INPUTS)
    assert steps[:2] == engine.uv_steps(profile="cuda", **UV_INPUTS)[:2]
    assert steps[2][-2:] == ["--constraint", c]
    assert steps[3][-2:] == ["--constraint", c]
    assert steps[3][-3].endswith("[engine-cuda]")


def test_build_script_compiles_constraints_with_the_same_torch_pins():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build_release.ps1").read_text(
        encoding="utf-8-sig")
    for spec in engine.TORCH_SPECS:
        assert f"'{spec}'" in script
    assert engine.TORCH_CUDA_INDEX in script and engine.TORCH_CPU_INDEX in script
    assert "constraints-$flavor.txt" in script


def test_installer_resources_carry_the_licenses():
    """Установщик несёт лицензии всего, что в нём лежит: ffmpeg (из его
    архива), uv (в архиве uv её нет — текст в репозитории) и самого meet."""
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_release.ps1").read_text(encoding="utf-8-sig")
    for name in ("'ffmpeg-LICENSE.txt'", "'uv-LICENSE.txt'", "'LICENSE'", "'NOTICE'"):
        assert f"(Join-Path $Resources {name})" in script
    uv = (root / "scripts" / "licenses" / "uv-LICENSE.txt").read_text(encoding="utf-8")
    assert "MIT License" in uv and "Apache License" in uv and "Astral Software Inc." in uv
    notice = (root / "NOTICE").read_text(encoding="utf-8")
    for part in ("Onest", "SIL Open Font License", "wav2vec2-large-xlsr-53-russian",
                 "faster-whisper-large-v3-russian", "faster-whisper-medium", "CC-BY-4.0"):
        assert part in notice


def test_estimate_text_cpu():
    text = engine.estimate_text(2264, "cpu")  # GigaAM — движок процессора по умолчанию
    assert "38 мин" in text and "18 мин" in text


def test_estimate_text_cuda_and_minimum():
    assert "8 мин обработки" in engine.estimate_text(2264, "cuda")
    assert "1 мин обработки" in engine.estimate_text(10, "cuda")


def test_uv_steps_fixtures_are_current():
    import json
    from pathlib import Path

    for profile in ("cuda", "cpu"):
        path = Path(__file__).parent / "fixtures" / f"uv_steps_{profile}.json"
        expected = engine.uv_steps(profile=profile, **UV_INPUTS)
        assert json.loads(path.read_text(encoding="utf-8")) == expected
        # С файлом ограничений версий: его кладёт в ресурсы релизная сборка.
        path = Path(__file__).parent / "fixtures" / f"uv_steps_{profile}_constrained.json"
        expected = engine.uv_steps(profile=profile, constraints=UV_CONSTRAINTS[profile],
                                   **UV_INPUTS)
        assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_cuda_download_hint_matches_the_measured_wheels():
    # Сухой прогон 01.10.2026: колёса CUDA-окружения — 4,4 ГБ; подсказка
    # «~3 ГБ» обещала меньше, чем качается на самом деле.
    assert engine.DOWNLOAD_HINT_GB["cuda"] >= 4.4
    assert engine.DOWNLOAD_HINT_GB["cpu"] < engine.DOWNLOAD_HINT_GB["cuda"]


def test_python_side_install_gets_proxy_env(monkeypatch):
    """pip/uv из окна настроек и CLI качают через тот же прокси, что и модели."""
    from meet import engine, netproxy

    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(netproxy, "read_registry",
                        lambda: {"ProxyEnable": 1, "ProxyServer": "127.0.0.1:3067"})
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            seen.update(kwargs)
            self.stdout = iter(["ok\n"])

        def wait(self):
            return 0

    monkeypatch.setattr(engine.subprocess, "Popen", FakePopen)
    assert engine._run(["pip", "install", "x"], None) == 0
    env = {k.upper(): v for k, v in seen["env"].items()}
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3067"


def test_gigaam_archive_is_hash_pinned_everywhere():
    """SHA-256 архива GigaAM — во фрагменте адреса: его сверяют и pip, и uv."""
    import tomllib

    assert engine.GIGAAM.endswith(f"#sha256={engine.GIGAAM_SHA256}")
    assert len(engine.GIGAAM_SHA256) == 64
    root = Path(__file__).resolve().parents[1]
    extras = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["optional-dependencies"]
    assert extras["gigaam"] == [engine.GIGAAM]
    for profile in ("engine-cpu", "engine-cuda"):
        assert engine.GIGAAM not in extras[profile]  # необязательный шаг, не основной


def test_engine_without_gigaam_is_still_installed(monkeypatch):
    """GigaAM — необязательный компонент: без него распознаёт Whisper, и
    движок не объявляется «не установленным»."""
    monkeypatch.setattr(engine, "installed", lambda module: module != "gigaam")
    state = engine.state()
    assert state["installed"] is True and state["missing"] == []
    gigaam = next(c for c in state["components"] if c["module"] == "gigaam")
    assert gigaam["optional"] and not gigaam["installed"]
    assert gigaam["note"] == "не установлена — будет установлена при обновлении движка"
    monkeypatch.setattr(engine, "installed", lambda module: module != "torch")
    assert engine.state()["missing"] == ["torch"]


def test_gigaam_is_a_separate_optional_install_step():
    """Сбой GigaAM (сеть, перепакованный архив) не валит установку движка:
    он ставится отдельным шагом — extra `gigaam` того же колеса."""
    import json

    for name, constraints in (("uv_gigaam_step.json", None),
                              ("uv_gigaam_step_constrained.json", UV_CONSTRAINTS["cpu"])):
        fixture = json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))
        assert fixture == [engine.uv_gigaam_step(constraints=constraints, **UV_INPUTS)]
    step = engine.uv_gigaam_step(**UV_INPUTS)
    assert step[-1].endswith("[gigaam]")
    for profile in ("cpu", "cuda"):
        assert not any("gigaam" in arg for s in engine.uv_steps(profile=profile, **UV_INPUTS) for arg in s)


def test_cli_install_survives_a_failed_gigaam_step():
    calls, lines = [], []

    def runner(argv, on_line):
        calls.append(argv)
        return 1 if argv[-1] == engine.GIGAAM else 0

    assert engine.install("cpu", on_line=lines.append, runner=runner) == 0
    assert len(calls) == 3 and "GigaAM не установилась" in lines[-1]


def test_build_script_pins_gigaam_hash_in_constraints():
    """Ограничения собираются с extra gigaam, и сборка падает, если в них
    нет строки gigaam с #sha256 (иначе хеш архива не сверялся бы)."""
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build_release.ps1").read_text(
        encoding="utf-8-sig")
    assert '--extra "engine-$flavor" --extra gigaam' in script
    assert "'^gigaam @ .+#sha256=[0-9a-f]{64}$'" in script


def test_gigaam_install_error_is_reported(monkeypatch, tmp_path):
    (tmp_path / engine.GIGAAM_ERROR_FILE).write_text(
        "архив компонента GigaAM на GitHub изменился — нужна новая версия приложения", encoding="utf-8")
    assert engine.gigaam_install_error(tmp_path) == (
        "GigaAM не установилась: архив компонента GigaAM на GitHub изменился — нужна новая "
        "версия приложения — используется Whisper")
    monkeypatch.setattr(engine.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(engine, "installed", lambda module: module != "gigaam")
    gigaam = next(c for c in engine.state()["components"] if c["module"] == "gigaam")
    assert gigaam["note"].startswith("GigaAM не установилась: архив")
    (tmp_path / engine.GIGAAM_ERROR_FILE).unlink()
    assert engine.gigaam_install_error(tmp_path) is None
