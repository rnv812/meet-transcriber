"""Данные «прокликивания» окна (scripts/clickthrough.py): без запуска резидента и браузера."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("clickthrough", ROOT / "scripts" / "clickthrough.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_test_data_is_a_ready_library(tmp_path, monkeypatch):
    from meet import library, settings

    click = _script()
    monkeypatch.setattr(click.shutil, "which", lambda name: None)  # без ffmpeg
    click.make_data(tmp_path)
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    loaded = settings.load()
    assert loaded.auto_record.enabled is False
    assert loaded.ui.terms_accepted == click._terms_version()
    folders = sorted((tmp_path / "recordings").iterdir())
    cards = [library.describe(f) for f in folders]
    assert [c.has_transcript for c in cards] == [True, True, False]
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["ui"]["theme"] == "system"


def test_expected_responses_have_reasons():
    text = (ROOT / "app" / "scripts" / "clickthrough.mjs").read_text(encoding="utf-8")
    block = text.split("const EXPECTED = [", 1)[1].split("];", 1)[0]
    rows = [line for line in block.splitlines() if line.strip().startswith("[/")]
    assert rows and all(line.rstrip().endswith('"],') for line in rows)
