"""Модели GigaAM в каталоге «Движок и модели»: размер, скачано, выбор,
скачивание и удаление — в папке моделей приложения, без сети в тестах."""

import json
import sys
import types
import urllib.request

import pytest

from meet import gigaam_asr, models


BLOBS = {"ckpt": b"x" * 100, "tok": b"t"}


@pytest.fixture(autouse=True)
def _small_models_no_network(monkeypatch):
    """Модели по 101 байту и никакой сети: загрузка — только из BLOBS."""
    import hashlib

    files = {}
    for name in gigaam_asr.MODELS:
        files[name] = (
            (f"{name}.ckpt", 100, "md5", hashlib.md5(BLOBS["ckpt"]).hexdigest()),
            (f"{name}_tokenizer.model", 1, "sha256", hashlib.sha256(BLOBS["tok"]).hexdigest()),
        )
    monkeypatch.setattr(gigaam_asr, "FILES", files)

    class Response:
        def __init__(self, data):
            self.data = data

        def read(self, n=-1):
            data, self.data = self.data, b""
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    real = urllib.request.urlopen

    def urlopen(url, *a, **kw):
        if not isinstance(url, str):  # запросы к своему control API — как есть
            return real(url, *a, **kw)
        if "offline" in str(gigaam_asr.URL):
            raise OSError("нет связи")
        return Response(BLOBS["ckpt"] if url.endswith(".ckpt") else BLOBS["tok"])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    return tmp_path


def _fake_files(name: str) -> None:
    root = gigaam_asr.cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.ckpt").write_bytes(BLOBS["ckpt"])
    (root / f"{name}_tokenizer.model").write_bytes(BLOBS["tok"])


def _fake_gigaam(monkeypatch, load_model):
    decoding = types.SimpleNamespace(SentencePieceProcessor=type("P", (), {}))
    monkeypatch.setitem(sys.modules, "gigaam", types.SimpleNamespace(load_model=load_model,
                                                                     decoding=decoding))
    monkeypatch.setitem(sys.modules, "gigaam.decoding", decoding)


def _item(state, model_id):
    return next(m for m in state["items"] if m["id"] == model_id)


def test_catalogue_lists_both_gigaam_models_with_sizes():
    gigaam = [m for m in models.CATALOGUE if m.get("backend") == "gigaam"]
    assert [m["id"] for m in gigaam] == ["gigaam/v3_e2e_rnnt", "gigaam/v3_e2e_ctc"]
    assert all(m["kind"] == models.ASR and m["size_gb"] > 0 for m in gigaam)
    assert "быстрее, чуть менее точно" in gigaam[1]["note"]
    assert {models.gigaam_name(m["id"]) for m in gigaam} == set(gigaam_asr.MODELS)


def test_state_marks_downloaded_selected_and_removable(data):
    _fake_files("v3_e2e_ctc")
    state = models.state(selected="bzikst/faster-whisper-large-v3-russian",
                         selected_gigaam="v3_e2e_ctc")
    ctc, rnnt = _item(state, "gigaam/v3_e2e_ctc"), _item(state, "gigaam/v3_e2e_rnnt")
    assert ctc["downloaded"] and ctc["size_on_disk"] == 101 and ctc["removable"]
    assert ctc["selected"] and not rnnt["selected"]
    assert not rnnt["downloaded"] and not rnnt["removable"]
    whisper = _item(state, "bzikst/faster-whisper-large-v3-russian")
    assert whisper["selected"] and not whisper["removable"]  # общий кэш HF не трогаем
    assert state["gigaam_cache"] == str(data / "models" / "gigaam")
    assert "can_download_gigaam" in state


def test_broken_download_is_removable_but_not_downloaded(data):
    root = gigaam_asr.cache_dir()
    root.mkdir(parents=True)
    (root / "v3_e2e_rnnt.ckpt.part").write_bytes(b"x" * 40)  # оборванная загрузка
    rnnt = _item(models.state(selected_gigaam="v3_e2e_rnnt"), "gigaam/v3_e2e_rnnt")
    assert not rnnt["downloaded"] and rnnt["removable"] and rnnt["size_on_disk"] == 40
    assert models.remove("gigaam/v3_e2e_rnnt")["ok"]
    assert not gigaam_asr.present("v3_e2e_rnnt")


def test_remove_deletes_only_gigaam_files(data):
    _fake_files("v3_e2e_rnnt")
    assert models.remove("gigaam/v3_e2e_rnnt") == {"ok": True, "id": "gigaam/v3_e2e_rnnt", "removed": 2}
    assert not models.downloaded("gigaam/v3_e2e_rnnt")
    assert models.remove("Systran/faster-whisper-medium")["ok"] is False
    assert models.remove("gigaam/../../evil")["ok"] is False


def test_download_uses_public_load_model_into_app_folder(data, monkeypatch):
    seen = {}

    def load_model(name, device=None, download_root=None, **kw):
        seen.update(name=name, device=device, root=download_root)
        return object()

    _fake_gigaam(monkeypatch, load_model)
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    lines = []
    assert models.download("gigaam/v3_e2e_rnnt", on_line=lines.append) == 0
    assert models.downloaded("gigaam/v3_e2e_rnnt")
    assert seen == {"name": "v3_e2e_rnnt", "device": "cpu", "root": str(data / "models" / "gigaam")}
    assert lines[-1].startswith("скачано:")


def test_download_without_engine_says_so(data, monkeypatch):
    monkeypatch.setattr(gigaam_asr, "installed", lambda: False)
    lines = []
    assert models.download("gigaam/v3_e2e_ctc", on_line=lines.append) == 3
    assert "движок" in lines[0]


def test_download_error_is_explained(data, monkeypatch):
    def load_model(*a, **kw):
        raise OSError("connection reset")

    _fake_gigaam(monkeypatch, load_model)
    monkeypatch.setattr(gigaam_asr, "installed", lambda: True)
    lines = []
    assert models.download("gigaam/v3_e2e_rnnt", on_line=lines.append) == 1
    assert "не загрузилась" in lines[-1]
    monkeypatch.setattr(gigaam_asr, "URL", "https://offline.invalid/GigaAM")
    gigaam_asr.remove("v3_e2e_rnnt")
    lines.clear()
    assert models.download("gigaam/v3_e2e_rnnt", on_line=lines.append) == 1
    assert "не удалось скачать" in lines[-1]


def test_remove_route_over_http(monkeypatch, tmp_path):
    from meet import control, tray, tray_control

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    (tmp_path / "meet").mkdir()
    _fake_files("v3_e2e_ctc")

    class Queue:
        def submit(self, *a, **kw):
            raise AssertionError("удаление — не задача")

    state = tray_control.TrayControl(tray.TrayApp(), queue=Queue())
    srv = control.ControlServer(state)
    srv.start(publish=False)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/models/remove",
            data=json.dumps({"id": "gigaam/v3_e2e_ctc"}).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {srv.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read().decode("utf-8"))["ok"] is True
        listing = state.models()
        assert not _item(listing, "gigaam/v3_e2e_ctc")["downloaded"]
        assert _item(listing, "gigaam/v3_e2e_rnnt")["selected"]  # дефолт настроек
    finally:
        srv.stop()
