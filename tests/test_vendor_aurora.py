"""Перенос компонентов Atlas Aurora в окно: разделы bundle.css без правок."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("vendor_aurora", ROOT / "scripts" / "vendor_aurora.py")
vendor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vendor)

BUNDLE = """/* Atlas Aurora — компоненты. шапка */
:root { --x: 1; }

/* ── База ── */
body { margin: 0; }

/* ── Сияние ── */
.aurora { color: red; }
/* Вид по умолчанию — комментарий внутри раздела */
.aurora::before { content: ''; }

/* ── Кнопки ── */
.btn { height: 40px; }

/* ── Слои и размытие фона (v2.4) ──
   Иерархия: многострочный заголовок */
.backdrop { inset: 0; }
"""


def test_sections_split_on_headers_only():
    sections = vendor.split_sections(BUNDLE)
    assert [title for title, _ in sections] == ["База", "Сияние", "Кнопки", "Слои и размытие фона (v2.4)"]
    aurora = dict(sections)["Сияние"]
    assert aurora.startswith("/* ── Сияние ── */")
    assert ".aurora::before" in aurora and ".btn" not in aurora


def test_pick_keeps_source_order_and_text():
    sections = vendor.split_sections(BUNDLE)
    text = vendor.pick(sections, ["Кнопки", "База"])
    assert text.index("body { margin: 0; }") < text.index(".btn { height: 40px; }")
    assert vendor.pick(sections, ["Слои"]).strip().endswith(".backdrop { inset: 0; }")


def test_unknown_prefix_is_an_error():
    import pytest
    with pytest.raises(KeyError):
        vendor.pick(vendor.split_sections(BUNDLE), ["Нет такого"])


def test_cli_writes_only_requested_files(tmp_path):
    src = tmp_path / "bundle.css"
    src.write_text(BUNDLE, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    files = {"base": ["База"], "aurora": ["Сияние"], "controls": ["Кнопки"]}
    vendor.main([str(src), str(out), "--only", "base,aurora"], files=files)
    assert sorted(p.name for p in out.iterdir()) == ["aurora.css", "base.css"]
    first = (out / "base.css").read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("/* Atlas Aurora v2.6 — base: База")
