"""Калибровка поиска по прошлым встречам (scripts/owner_derive_calib.py):
только синтетика — встречи из test_owner_derive, поддельный эмбеддер."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_owner_derive import OTHER, OWNER, FakeEmbed, Library

ROOT = Path(__file__).resolve().parent.parent


def _calib():
    spec = importlib.util.spec_from_file_location("owner_derive_calib", ROOT / "scripts" / "owner_derive_calib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _five_and_one(lib):
    for day in range(1, 6):
        lib.meeting(f"2026-09-0{day}_10-00", [OWNER] * 20)
    lib.meeting("2026-09-06_10-00", [OTHER] * 20)


def test_run_prints_numbers_only_and_decisions(tmp_path, capsys):
    calib = _calib()
    lib = Library(tmp_path / "recordings")
    _five_and_one(lib)
    folders = sorted((tmp_path / "recordings").iterdir())
    embed = FakeEmbed()
    calib.run(lib.root, folders, {"Вы"}, SimpleNamespace(max=10, out=None), embed=embed, decode=lib.decode)
    out = capsys.readouterr().out
    assert "2026-09" not in out  # встречи обезличены
    rows = [json.loads(line) for line in out.strip().splitlines()]
    assert rows[0]["find"] == {"status": "suggested", "checked": 6, "used": 6, "found": 5}
    assert rows[0]["current"] == {"stop": 0.35, "group_cos": 0.75}
    meetings = [r for r in rows if "meeting" in r]
    assert len(meetings) == 6 and meetings[0]["clusters_s"]["0.35"] == [80.0]
    stops = {r["stop"]: r for r in rows if "stop" in r}
    assert set(stops) == {0.30, 0.35, 0.45, 0.55}
    assert stops[0.35]["decide"]["0.75"] == {"status": "suggested", "group": 5, "need": 4}
    assert stops[0.35]["pair_cos"]["n"] == 15
    # Голос куска считается один раз (память по содержимому): в синтетике у
    # голоса все куски одинаковые — два голоса, два расчёта на поиск и перебор.
    assert embed.calls == 2


def test_calibrate_reads_copies_and_deletes_them(tmp_path, monkeypatch):
    calib = _calib()
    from meet import credentials

    monkeypatch.setattr(credentials, "get_hf_token", lambda: "секрет")
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    lib = Library(tmp_path / "source")
    _five_and_one(lib)
    before = sorted((p.relative_to(lib.root), p.stat().st_mtime_ns, p.read_bytes())
                    for p in lib.root.rglob("*") if p.is_file())
    seen = {}

    def fake_run(root, folders, labels, args, **kw):
        seen.update(root=root, names=[f.name for f in folders], labels=labels,
                    copies=all((f / "mic.opus").exists() and lib.root not in f.parents for f in folders),
                    data=os.environ["MEET_DATA_DIR"], offline=os.environ["HF_HUB_OFFLINE"],
                    token=credentials.get_hf_token())
        return 0

    monkeypatch.setattr(calib, "run", fake_run)
    assert calib.main(["--calibrate", "--recordings", str(lib.root), "--owner", "Вы"]) == 0
    assert seen["copies"] and len(seen["names"]) == 6 and seen["labels"] == {"Вы"}
    assert seen["offline"] == "1" and seen["token"] is None and "owner-derive-calib-" in seen["data"]
    assert not seen["root"].exists() and not seen["root"].parent.exists()
    after = sorted((p.relative_to(lib.root), p.stat().st_mtime_ns, p.read_bytes())
                   for p in lib.root.rglob("*") if p.is_file())
    assert after == before


def test_calibrate_mode_is_required():
    with pytest.raises(SystemExit):
        _calib().main([])
