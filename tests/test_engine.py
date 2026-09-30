"""Движок расшифровки: определение состояния и сборка команд установки.

Настоящую установку тесты не запускают — она идёт гигабайтами и минутами;
проверяется то, что решает, *что именно* будет установлено.
"""

import sys

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
    assert len(steps) == 2
    assert "faster-whisper>=1.1" not in steps[0]
    assert "--index-url" not in steps[1]


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
