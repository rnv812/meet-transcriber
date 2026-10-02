"""Иконки Meet (scripts/make_app_icons.py): все размеры, которые ждут Tauri,
Windows и трей, на месте; растры в репозитории — ровно то, что рисует скрипт
(правка цвета — перезапуск скрипта, а не ручная правка PNG); состояния трея
различимы на 16 px."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "app" / "src-tauri" / "icons"
PUBLIC = ROOT / "app" / "public"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "make_app_icons", ROOT / "scripts" / "make_app_icons.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    out = tmp_path_factory.mktemp("icons")
    gen = _generator()
    files = gen.render_all(out / "icons", out / "public")
    return gen, out, files


def _pixels(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"), dtype=int)


def test_every_size_is_generated(made):
    gen, out, files = made
    names = {f.name for f in files}
    expected = set(gen.APP_PNG) | {"icon.ico", "icon.icns", "meet.svg", "favicon.svg"}
    expected |= {f"tray-{s}-{n}.png" for s in gen.TRAY_STATES for n in gen.TRAY_SIZES}
    assert names == expected
    assert {"32x32.png", "128x128.png", "128x128@2x.png", "256x256.png", "512x512.png",
            "1024x1024.png"} <= names
    for name, size in gen.APP_PNG.items():
        assert Image.open(out / "icons" / name).size == (size, size), name
    ico = Image.open(out / "icons" / "icon.ico")
    assert set(ico.info["sizes"]) == {(s, s) for s in gen.ICO_SIZES}
    assert {16, 24, 32, 48, 256} <= set(gen.ICO_SIZES)
    assert Image.open(out / "icons" / "icon.icns").size == (1024, 1024)
    assert gen.TRAY_STATES == ("idle", "recording", "live", "busy", "offline")
    assert gen.TRAY_SIZES == (16, 20, 24, 32)
    for state in gen.TRAY_STATES:
        for size in gen.TRAY_SIZES:
            path = out / "icons" / f"tray-{state}-{size}.png"
            assert Image.open(path).size == (size, size)


def test_committed_icons_are_what_the_generator_draws(made):
    _, out, files = made
    for made_file in files:
        rel = made_file.relative_to(out)
        committed = (PUBLIC if rel.parts[0] == "public" else ICONS) / Path(*rel.parts[1:])
        assert committed.exists(), f"нет {committed} — перезапустите scripts/make_app_icons.py"
        if made_file.suffix == ".svg":
            assert committed.read_text(encoding="utf-8").replace("\r\n", "\n") == made_file.read_text(
                encoding="utf-8"
            ), committed.name
        elif made_file.suffix == ".ico":
            for size in Image.open(made_file).info["sizes"]:
                a, b = Image.open(made_file), Image.open(committed)
                a.size = b.size = size
                diff = np.abs(np.asarray(a.convert("RGBA"), int) - np.asarray(b.convert("RGBA"), int))
                assert diff.max() <= 1, f"icon.ico {size}"
        else:
            # Растр: допуск в единицу на округление другой версии numpy/scipy.
            diff = np.abs(_pixels(made_file) - _pixels(committed))
            assert diff.max() <= 1, committed.name


def test_tauri_bundles_existing_icons():
    conf = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    icons = conf["bundle"]["icon"]
    assert "icons/icon.ico" in icons and "icons/icon.icns" in icons
    for icon in icons:
        assert (ROOT / "app" / "src-tauri" / icon).exists(), icon


def test_drawing_is_deterministic():
    gen = _generator()
    for state in gen.TRAY_STATES:
        first = np.asarray(gen.draw_tray(state, 16))
        assert np.array_equal(first, np.asarray(gen.draw_tray(state, 16))), state
    assert np.array_equal(np.asarray(gen.draw_app(32)), np.asarray(gen.draw_app(32)))


def test_tray_states_read_at_16px():
    gen = _generator()
    px = {s: np.asarray(gen.draw_tray(s, 16), dtype=int) for s in gen.TRAY_STATES}

    def reddish(a):  # непрозрачные пиксели, где красный заметно больше синего
        return int(((a[..., 3] > 200) & (a[..., 0] > a[..., 2] + 80)).sum())

    # Запись — красная точка, нет связи — янтарная; у остальных тёплого нет.
    assert reddish(px["recording"]) >= 12
    assert reddish(px["offline"]) >= 12
    rec_dot = px["recording"][12:15, 12:15]
    off_dot = px["offline"][12:15, 12:15]
    assert (rec_dot[..., 1] < 120).all() and (off_dot[..., 1] > 120).all()  # красная ≠ янтарная
    for state in ("idle", "live", "busy"):
        assert reddish(px[state]) == 0, state
    # Центр кольца пуст, у ассистента — светлая точка.
    for state in ("idle", "recording", "busy", "offline"):
        assert px[state][7:9, 7:9, 3].max() == 0, state
    assert px["live"][7:9, 7:9, 3].min() == 255
    # Дуга «занят»: разрыв справа сверху прозрачен, у целого кольца там пиксели.
    assert px["busy"][2, 11, 3] == 0 and px["idle"][2, 11, 3] > 200
    # Запись и ассистент ярче ожидания (приглушённое кольцо).
    def ring_brightness(a):
        lit = a[..., 3] > 200
        return a[..., :3][lit].mean()
    assert ring_brightness(px["live"]) > ring_brightness(px["idle"]) + 10
    # Знак не вылезает за край: по периметру 16 px почти прозрачно.
    for state, a in px.items():
        border = np.concatenate([a[0, :, 3], a[-1, :, 3], a[:, 0, 3], a[:, -1, 3]])
        assert border.max() < 200 or state in ("recording", "offline"), state
