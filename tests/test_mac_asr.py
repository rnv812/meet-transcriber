"""Распознавание на Mac (0.6.2): одна выбранная модель на все встречи, без
деления по языку встречи. Ключи прежние — `asr.cpu_backend` и
`asr.cpu_model` / `asr.gigaam_model` (на Mac распознаёт строка процессора):
обновившийся получает выбранной свою прежнюю основную модель.

Движок — по модели: у Whisper-модели есть чекпоинт MLX (в каталоге или уже
скачанный) и MLX доступен (macOS 14+, extra `mlx`) — MLX на видеоядрах;
иначе faster-whisper на процессоре. torch-часть (GigaAM, выравнивание,
голоса, диаризация) — на видеоядрах (MPS) всегда, откат на процессор при
сбое — страховка, не настройка. Состояние для окна — `engine.state()["mac"]`.

macOS, torch и mlx_whisper подменены: тест идёт и на Windows."""

import json
import sys
import types

import pytest

from meet import asr, engine, gigaam_asr, live_asr, mlx_asr, models, plat, settings

TURBO = "mlx-community/whisper-large-v3-turbo"
LARGE_MLX = "mlx-community/whisper-large-v3-mlx"
MEDIUM_MLX = "mlx-community/whisper-medium-mlx"
LARGE = "Systran/faster-whisper-large-v3"
MEDIUM = "Systran/faster-whisper-medium"
RU = "bzikst/faster-whisper-large-v3-russian"


def _config(tmp_path, monkeypatch, section=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    raw = {"version": settings.SCHEMA_VERSION}
    if section is not None:
        raw["asr"] = section
    (data / "config.json").write_text(json.dumps(raw), encoding="utf-8")


def _mac(monkeypatch, version="14.4.1"):
    monkeypatch.setattr(plat, "is_macos", lambda: True)
    monkeypatch.setattr(mlx_asr.platform, "mac_ver", lambda: (version, ("", "", ""), "arm64"))


def _mlx_on_disk(monkeypatch, tmp_path, repo):
    monkeypatch.delenv(models.USER_HF_HUB_CACHE, raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    folder = tmp_path / "hub" / ("models--" + repo.replace("/", "--"))
    snapshot = folder / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text("abc", encoding="utf-8")
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "weights.npz").write_bytes(b"x")


@pytest.fixture
def mac_ready(monkeypatch, tmp_path):
    """Mac 14, MLX ставится, GigaAM есть; кэш моделей — свой для теста (пустой)."""
    _mac(monkeypatch)
    _config(tmp_path, monkeypatch)
    monkeypatch.delenv(models.USER_HF_HUB_CACHE, raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.ModuleType("mlx_whisper"))
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    monkeypatch.setattr(asr, "cpu_reason", lambda setting=None: None)


# --- каталог и чекпоинты MLX --------------------------------------------------------


def test_catalogue_maps_whisper_models_to_their_mlx_checkpoints():
    by_id = {m["id"]: m for m in models.CATALOGUE}
    assert by_id[LARGE]["mlx"] == LARGE_MLX and by_id[MEDIUM]["mlx"] == MEDIUM_MLX
    for repo in (TURBO, LARGE_MLX, MEDIUM_MLX):
        assert by_id[repo]["backend"] == "mlx-whisper" and by_id[repo]["platform"] == "macos"
    # Чекпоинт модели faster-whisper — не отдельная модель для выбора.
    assert by_id[LARGE_MLX]["mlx_of"] == LARGE and by_id[MEDIUM_MLX]["mlx_of"] == MEDIUM
    assert "mlx_of" not in by_id[TURBO]
    assert set(mlx_asr.MODELS) == {TURBO, LARGE_MLX, MEDIUM_MLX} and mlx_asr.MODEL == TURBO


def test_checkpoint_of_a_model(monkeypatch, tmp_path):
    monkeypatch.delenv(models.USER_HF_HUB_CACHE, raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert mlx_asr.checkpoint(LARGE) == LARGE_MLX
    assert mlx_asr.checkpoint(TURBO) == TURBO
    assert mlx_asr.checkpoint(RU) is None                  # у русского fine-tune чекпоинта MLX нет
    assert mlx_asr.checkpoint("someone/whisper-mlx") is None
    _mlx_on_disk(monkeypatch, tmp_path, "someone/whisper-mlx")  # скачанный чекпоинт MLX
    assert mlx_asr.checkpoint("someone/whisper-mlx") == "someone/whisper-mlx"
    folder = tmp_path / "my-mlx"
    folder.mkdir()
    assert mlx_asr.checkpoint(str(folder)) is None
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "weights.safetensors").write_bytes(b"x")
    assert mlx_asr.checkpoint(str(folder)) == str(folder)
    assert mlx_asr.mlx_only(TURBO) and mlx_asr.mlx_only(str(folder)) and not mlx_asr.mlx_only(LARGE)


def test_mlx_only_model_never_reaches_faster_whisper(monkeypatch, tmp_path):
    """Выбрана модель только MLX, а MLX недоступен — faster-whisper берёт
    свою модель для процессора, а не репозиторий MLX."""
    _config(tmp_path, monkeypatch, {"cpu_backend": "faster-whisper", "cpu_model": TURBO})
    assert asr._model_for("cpu", None) == asr.CPU_MODEL_NAME
    _config(tmp_path, monkeypatch, {"cpu_backend": "faster-whisper", "cpu_model": LARGE})
    assert asr._model_for("cpu", None) == LARGE


# --- выбор движка на Mac -------------------------------------------------------------


@pytest.mark.parametrize("language", ["ru", "auto"])
def test_gigaam_chosen_runs_on_every_meeting(mac_ready, monkeypatch, tmp_path, language):
    _config(tmp_path, monkeypatch, {"cpu_backend": "gigaam", "language": language})
    detect = []
    choice = asr.choose(tmp_path / "a.wav", detect=lambda p: detect.append(p) or ("en", 0.99))
    assert (choice.backend, choice.device, choice.note) == ("gigaam", "cpu", None)
    assert detect == []  # язык встречи на Mac не определяется: модель выбрана человеком


def test_gigaam_with_a_non_russian_language_setting_keeps_recognition(mac_ready, monkeypatch, tmp_path, capsys):
    """Язык речи в настройках не русский, а выбрана GigaAM (только русский) —
    распознаёт Whisper строки процессора, как и раньше: распознавание не теряется."""
    _mlx_on_disk(monkeypatch, tmp_path, MEDIUM_MLX)
    _config(tmp_path, monkeypatch, {"cpu_backend": "gigaam", "language": "en", "cpu_model": MEDIUM})
    choice = asr.choose()
    assert (choice.backend, choice.model, choice.note) == ("mlx-whisper", MEDIUM_MLX, asr.NOT_RUSSIAN)
    assert "GigaAM понимает только русский" in capsys.readouterr().out


def test_whisper_with_downloaded_checkpoint_goes_to_mlx_for_any_language(mac_ready, monkeypatch, tmp_path):
    _mlx_on_disk(monkeypatch, tmp_path, LARGE_MLX)
    for language in ("ru", "en", "auto"):
        _config(tmp_path, monkeypatch, {"cpu_backend": "faster-whisper", "cpu_model": LARGE, "language": language})
        choice = asr.choose()
        assert (choice.backend, choice.device, choice.model, choice.note) == ("mlx-whisper", "mps", LARGE_MLX, None)


def test_whisper_without_checkpoint_or_without_mlx_stays_on_faster_whisper(mac_ready, monkeypatch, tmp_path, capsys):
    _config(tmp_path, monkeypatch, {"cpu_backend": "faster-whisper", "cpu_model": RU})
    assert (asr.choose().backend, asr.choose().device) == ("faster-whisper", "cpu")
    _config(tmp_path, monkeypatch, {"cpu_backend": "faster-whisper", "cpu_model": LARGE})
    assert asr.choose().backend == "faster-whisper"          # чекпоинт не скачан
    assert "модель MLX Whisper не скачана" in capsys.readouterr().out
    _mlx_on_disk(monkeypatch, tmp_path, LARGE_MLX)
    _mac(monkeypatch, "13.6")
    assert asr.choose().backend == "faster-whisper"          # macOS 13: MLX не ставится


def test_mlx_only_model_is_used_as_is(mac_ready, monkeypatch, tmp_path):
    _mlx_on_disk(monkeypatch, tmp_path, TURBO)
    _config(tmp_path, monkeypatch, {"cpu_backend": "faster-whisper", "cpu_model": TURBO, "language": "ru"})
    choice = asr.choose()
    assert (choice.backend, choice.model) == ("mlx-whisper", TURBO)
    seen = {}
    monkeypatch.setattr(mlx_asr, "transcribe", lambda path, model, language, *a: seen.update(
        model=model, language=language) or [])
    asr.transcribe_wav(tmp_path / "a.wav", choice=choice)
    assert seen == {"model": TURBO, "language": "ru"}


def test_cuda_setting_on_mac_does_not_matter(mac_ready, monkeypatch, tmp_path):
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cuda")
    _config(tmp_path, monkeypatch, {"device": "cuda", "backend": "faster-whisper", "cpu_backend": "gigaam"})
    assert (asr.choose().backend, asr.choose().device) == ("gigaam", "cpu")


def test_windows_keeps_the_language_rule(monkeypatch, tmp_path):
    monkeypatch.setattr(plat, "is_macos", lambda: False)
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    monkeypatch.setattr(asr, "resolve_device", lambda setting=None: "cpu")
    monkeypatch.setattr(asr, "cpu_reason", lambda setting=None: None)
    _config(tmp_path, monkeypatch, {"cpu_backend": "gigaam", "language": "auto"})
    choice = asr.choose(tmp_path / "a.wav", detect=lambda p: ("en", 0.99))
    assert (choice.backend, choice.note) == ("faster-whisper", asr.NOT_RUSSIAN)


# --- видеоядра (MPS) всегда ----------------------------------------------------------


def _torch(monkeypatch, mps=True):
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))
    torch.device = lambda name: types.SimpleNamespace(type=name)
    monkeypatch.setitem(sys.modules, "torch", torch)


def test_mps_on_mac_whatever_the_device_setting(monkeypatch, tmp_path):
    asr._reset_cuda_state()
    monkeypatch.setattr(asr, "engine_profile", lambda prefix=None: "mac")
    _torch(monkeypatch)
    for device in ("auto", "cpu", "cuda"):
        _config(tmp_path, monkeypatch, {"device": device})
        assert asr.torch_device() == "mps"
    _torch(monkeypatch, mps=False)
    assert asr.torch_device() == "cpu"
    asr._reset_cuda_state()


# --- живой режим ---------------------------------------------------------------------


def test_live_follows_the_chosen_model(mac_ready, monkeypatch, tmp_path):
    class FakeWhisper:
        name = "Whisper"

    monkeypatch.setattr(asr, "Transcriber", lambda *a, **kw: FakeWhisper())
    monkeypatch.setattr(live_asr, "_on_disk", lambda name: True)
    monkeypatch.setattr(gigaam_asr, "downloaded", lambda name: True)
    pick = lambda raw: live_asr.pick(settings.Settings.from_raw({"asr": raw}), log=lambda _l: None)  # noqa: E731
    assert isinstance(pick({"cpu_backend": "gigaam", "language": "auto"}), live_asr.GigaamLive)
    assert isinstance(pick({"cpu_backend": "faster-whisper", "cpu_model": LARGE, "language": "en"}), FakeWhisper)
    _mlx_on_disk(monkeypatch, tmp_path, LARGE_MLX)
    live = pick({"cpu_backend": "faster-whisper", "cpu_model": LARGE, "language": "ru"})
    assert isinstance(live, live_asr.MlxLive) and live.model_name == LARGE_MLX and live.language == "ru"
    assert pick({"cpu_backend": "faster-whisper", "cpu_model": LARGE, "language": "auto"}).language is None


# --- сведения для окна -------------------------------------------------------------


def _engine_mac(monkeypatch, tmp_path, version, machine="arm64"):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(mlx_asr, "macos_version", lambda: version)
    monkeypatch.setattr(engine, "gpu", lambda: {"available": False, "name": None})
    monkeypatch.setattr(engine.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(engine.platform, "machine", lambda: machine)


def test_engine_state_tells_the_window_what_the_mac_can_do(monkeypatch, tmp_path):
    _engine_mac(monkeypatch, tmp_path, (14, 4, 1))
    monkeypatch.setattr(engine, "installed", lambda module: True)
    assert engine.state()["mac"] == {"macos": "14.4.1", "mps": True, "mps_reason": None,
                                     "mlx_supported": True, "mlx_installed": True, "mlx_reason": None}

    monkeypatch.setattr(engine, "installed", lambda module: module != "mlx_whisper")
    mac = engine.state()["mac"]
    assert mac["mlx_installed"] is False and mac["mlx_reason"] == "MLX Whisper не установлен в движке"
    (tmp_path / engine.MLX_ERROR_FILE).write_text("нет связи с PyPI", encoding="utf-8")
    assert engine.state()["mac"]["mlx_reason"].startswith("MLX Whisper не установился: нет связи с PyPI")

    _engine_mac(monkeypatch, tmp_path, (13, 6))
    mac = engine.state()["mac"]
    assert mac["macos"] == "13.6" and mac["mlx_supported"] is False
    assert mac["mlx_reason"] == "нужна macOS 14 или новее" and mac["mps"] is True

    _engine_mac(monkeypatch, tmp_path, (14, 2), machine="x86_64")
    mac = engine.state()["mac"]
    assert mac["mps"] is False and "Apple Silicon" in mac["mps_reason"]
    monkeypatch.setattr(engine, "installed", lambda module: module != "torch")
    _engine_mac(monkeypatch, tmp_path, (14, 2))
    assert engine.state()["mac"]["mps_reason"] == "PyTorch не установлен в движке"


def test_windows_engine_state_has_no_mac_part(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(engine, "gpu", lambda: {"available": False, "name": None})
    monkeypatch.setattr(engine.shutil, "which", lambda name, *a, **kw: None)
    assert "mac" not in engine.state()
