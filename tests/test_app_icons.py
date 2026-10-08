"""Иконки Meet (scripts/make_app_icons.py): все размеры, которые ждут Tauri,
Windows и трей, на месте; растры в репозитории — ровно то, что рисует скрипт
(правка цвета — перезапуск скрипта, а не ручная правка PNG); знак — диск
фиолетового сияния с тёмной сердцевиной (0.4, вариант B); состояния трея
различимы на 16 px."""

import colorsys
import importlib.util
import json
import math
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


def _oklch(rgb) -> tuple[float, float, float]:
    """sRGB 0…255 → OKLCh (L 0…1, C, h в градусах) — обратное к генератору,
    чтобы проверять оттенок в тех же единицах, что палитра Aurora."""

    def lin(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(float(v)) for v in rgb[:3])
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lightness, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _hsv(rgb) -> tuple[float, float, float]:
    h, s, v = colorsys.rgb_to_hsv(*(float(x) / 255 for x in rgb[:3]))
    return h * 360, s, v


def test_every_size_is_generated(made):
    gen, out, files = made
    names = {f.name for f in files}
    expected = set(gen.APP_PNG) | {"icon.ico", "icon.icns", "meet.svg", "favicon.svg"}
    expected |= {f"tray-{s}-{n}.png" for s in gen.TRAY_STATES for n in gen.TRAY_SIZES}
    expected |= {f"tray-template-{s}{x}.png" for s in gen.TRAY_STATES
                 for x in gen.TEMPLATE_SIZES.values()}
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


def test_mark_reaches_installer_windows_and_taskbar():
    """Знак — не только в exe: у установщика и деинсталлятора (иначе значок
    NSIS по умолчанию), у окон — кадр под размер из ресурса exe (generate_context!
    даёт окну первый кадр icon.ico, 16 px), а после установки Explorer
    сбрасывает кэш значков (иначе на панели задач остаётся прежний значок)."""
    tauri = ROOT / "app" / "src-tauri"
    nsis = json.loads((tauri / "tauri.conf.json").read_text(encoding="utf-8"))["bundle"][
        "windows"
    ]["nsis"]
    assert nsis["installerIcon"] == "icons/icon.ico"
    assert nsis["uninstallerIcon"] == "icons/icon.ico"
    for source in ("windows.rs", "live_panel.rs"):
        text = (tauri / "src" / source).read_text(encoding="utf-8")
        # В ветке успешного создания окна (между Ok(window) и Err(error)).
        ok_branch = text.split("Ok(window) =>", 1)[1].split("Err(error) =>", 1)[0]
        assert "crate::app_icon::apply(&window)" in ok_branch, source
    app_icon = (tauri / "src" / "app_icon.rs").read_text(encoding="utf-8")
    assert "const APP_ICON_RESOURCE: u16 = 32512;" in app_icon
    hooks = (tauri / "windows" / "hooks.nsh").read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    refresh = "System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'"
    assert refresh in hooks.split("!macro MEET_REFRESH_SHELL_ICONS\n", 1)[1].split("!macroend")[0]
    post = hooks.split("!macro NSIS_HOOK_POSTINSTALL\n", 1)[1].split("!macroend", 1)[0]
    assert post.rstrip().endswith("!insertmacro MEET_REFRESH_SHELL_ICONS")
    gui_end = hooks.split("Function .onGUIEnd", 1)[1].split("FunctionEnd", 1)[0]
    assert "!insertmacro MEET_REFRESH_SHELL_ICONS" in gui_end


def test_drawing_is_deterministic():
    """Растры воспроизводимы: зерно крупных значков — с фиксированным seed."""
    gen = _generator()
    for state in gen.TRAY_STATES:
        first = np.asarray(gen.draw_tray(state, 16))
        assert np.array_equal(first, np.asarray(gen.draw_tray(state, 16))), state
    for size in (32, 64, 128):
        assert np.array_equal(np.asarray(gen.draw_app(size)), np.asarray(gen.draw_app(size))), size


def test_oklch_matches_aurora_palette():
    """Палитра задана в OKLCh, как токены окна; перевод в sRGB — с отсечением."""
    gen = _generator()
    mid = gen.oklch_to_srgb(0.46, 0.16, 295)
    assert all(0.0 <= v <= 1.0 for v in mid)
    assert tuple(round(v * 255) for v in mid) == (0x61, 0x3E, 0xA6)
    assert gen.oklch_to_srgb(1.0, 0.0, 0) == pytest.approx((1.0, 1.0, 1.0), abs=1e-6)
    assert set(gen.VIOLET) == {"abyss", "deep", "mid", "signal", "crest", "ice"}
    for name, (l, c, h) in gen.VIOLET.items():
        back = _oklch([round(v * 255) for v in gen.oklch_to_srgb(l, c, h)])
        assert back[0] == pytest.approx(l, abs=0.01), name
        assert back[2] == pytest.approx(h, abs=4), name


def test_app_icon_is_violet_disc_with_dark_core():
    """Значок приложения — диск фиолетового сияния с тёмной сердцевиной на
    плитке Chaos Black; на 16 px сердцевина всё ещё видна."""
    gen = _generator()
    big = np.asarray(gen.draw_app(256), dtype=int)
    centre = big[127:129, 127:129]
    assert (centre[..., 3] == 255).all()
    assert np.abs(centre[..., :3] - 0x0F).max() <= 4, "сердцевина — цвет плитки #0f0f0f"
    # Кольцо пикселей на радиусе 0.2 стороны — фиолетовое сияние.
    samples = []
    for k in range(72):
        t = math.radians(k * 5)
        x, y = 128 + 51.2 * math.cos(t), 128 - 51.2 * math.sin(t)
        samples.append(big[int(y), int(x)])
    samples = np.array(samples)
    assert (samples[:, 3] == 255).all()
    lightness, chroma, hue = _oklch(samples.mean(axis=0))
    assert 270 <= hue <= 320, hue
    assert chroma > 0.08, chroma
    hues = [_oklch(s)[2] for s in samples]
    assert sum(255 <= h <= 335 for h in hues) >= 0.9 * len(hues)
    # Плитка вокруг диска — Chaos Black, угол за плиткой (поля) — прозрачный.
    assert np.abs(big[128, 30, :3] - 0x0F).max() <= 6 and big[128, 30, 3] == 255
    assert big[2, 2, 3] == 0
    # Зерно у крупных — едва заметное: соседние пиксели сияния близки.
    patch = big[70:90, 100:120, :3]
    assert np.abs(np.diff(patch, axis=1)).mean() < 6
    # 16 px: центр тёмный, вокруг — заметно светлее и фиолетовый.
    small = np.asarray(gen.draw_app(16), dtype=int)
    core = small[7:9, 7:9, :3].mean()
    disc = np.concatenate([small[4, 6:10, :3], small[11, 6:10, :3], small[6:10, 4, :3],
                           small[6:10, 11, :3]])
    assert core < 45, core
    assert disc.mean() > core + 50
    assert 255 <= _oklch(disc.mean(axis=0))[2] <= 335


def test_vector_master_is_the_disc_mark(made):
    """meet.svg и favicon.svg — один и тот же вектор знака B: радиальный
    градиент, размытые пятна сияния, клип по диску, без прежнего индиго."""
    gen, out, _ = made
    master = (out / "icons" / "source" / "meet.svg").read_text(encoding="utf-8")
    favicon = (out / "public" / "favicon.svg").read_text(encoding="utf-8")
    assert master == favicon
    assert "radialGradient" in master and "feGaussianBlur" in master
    assert "clipPath" in master and master.count("<ellipse") >= 3
    assert "#0f0f0f" in master
    mid = "#%02x%02x%02x" % tuple(round(v * 255) for v in gen.oklch_to_srgb(*gen.VIOLET["mid"]))
    assert mid in master
    for old in ("#5e6ad2", "#7c86f0", "#3f4bb8", "#a3abff"):
        assert old not in master.lower(), old


def test_tray_states_read_at_16px():
    gen = _generator()
    px = {s: np.asarray(gen.draw_tray(s, 16), dtype=int) for s in gen.TRAY_STATES}

    def warm(a):  # непрозрачные насыщенные пиксели тёплого оттенка (красный…янтарный)
        n = 0
        for p in a.reshape(-1, 4):
            h, s, _ = _hsv(p)
            n += p[3] > 200 and s > 0.35 and (h < 70 or h > 340)
        return n

    # Запись — красная точка, нет связи — янтарная; у остальных тёплого нет.
    assert warm(px["recording"]) >= 6
    assert warm(px["offline"]) >= 6
    for state in ("idle", "live", "busy"):
        assert warm(px[state]) == 0, state
    rec_hue = _hsv(px["recording"][12:15, 12:15].reshape(-1, 4).mean(axis=0))[0]
    off_hue = _hsv(px["offline"][12:15, 12:15].reshape(-1, 4).mean(axis=0))[0]
    assert rec_hue < 20 or rec_hue > 345, rec_hue  # красная
    assert 30 <= off_hue <= 60, off_hue  # янтарная
    # Точка отделена от диска прозрачным вырезом: пиксели в полосе между точкой
    # и диском почти прозрачны, а у целого диска там непрозрачно.
    dot = 16 - gen.TRAY[16]["dot"] - 0.5
    band = [(i, j) for i in range(16) for j in range(16)
            if 3.2 <= math.hypot(j + 0.5 - dot, i + 0.5 - dot) <= 3.9
            and math.hypot(j + 0.5 - 8, i + 0.5 - 8) < 5.5]
    assert len(band) >= 4
    for state in ("recording", "offline"):
        assert np.mean([px[state][i, j, 3] for i, j in band]) < 60, state
    assert min(px["idle"][i, j, 3] for i, j in band) == 255
    # Сердцевина: тёмная и непрозрачная, у ассистента — светлая.
    for state in ("idle", "recording", "offline"):
        assert (px[state][7:9, 7:9, 3] == 255).all(), state
        assert px[state][7:9, 7:9, :3].max() < 50, state
    assert (px["live"][7:9, 7:9, 3] == 255).all()
    assert px["live"][7:9, 7:9, :3].mean() > px["recording"][7:9, 7:9, :3].mean() + 120
    # «Занят» — диск с вырезанным сектором: справа сверху прозрачно, у целого — диск.
    assert px["busy"][2, 11, 3] == 0 and px["idle"][2, 11, 3] > 200
    assert px["busy"][3, 10, 3] == 0 and px["idle"][3, 10, 3] == 255

    # Ожидание и «нет связи» — приглушённые: менее насыщены, чем яркий диск.
    def saturation(a):
        disc = a[:10, :, :].reshape(-1, 4)
        disc = disc[(disc[:, 3] > 200) & (disc[:, :3].max(axis=1) > 70)]
        return float(np.mean([_hsv(p)[1] for p in disc]))

    for muted in ("idle", "offline"):
        for bright in ("recording", "live"):
            assert saturation(px[muted]) + 0.2 < saturation(px[bright]), (muted, bright)
    # Знак не вылезает за край: по периметру 16 px почти прозрачно.
    for state, a in px.items():
        border = np.concatenate([a[0, :, 3], a[-1, :, 3], a[:, 0, 3], a[:, -1, 3]])
        assert border.max() < 200 or state in ("recording", "offline"), state


def test_menu_bar_templates_are_monochrome_and_distinct(made):
    """Строка меню macOS: шаблонные картинки — только чёрный с прозрачностью
    (цвет macOS подставит сама), @1x — 18 px, @2x — 36 px, и все пять
    состояний различимы формой."""
    gen, out, _ = made
    assert gen.TEMPLATE_SIZES == {18: "", 36: "@2x"}
    for size, suffix in gen.TEMPLATE_SIZES.items():
        seen = []
        for state in gen.TRAY_STATES:
            px = _pixels(out / "icons" / f"tray-template-{state}{suffix}.png")
            assert px.shape == (size, size, 4)
            assert px[..., :3].max() == 0, f"{state}{suffix}: не чёрный"
            assert px[..., 3].max() > 200, f"{state}{suffix}: пусто"
            seen.append(px[..., 3].tobytes())
        assert len(set(seen)) == len(gen.TRAY_STATES), size
    # Диск с сердцевиной: центр шаблона прозрачен (сквозь него — строка меню),
    # у ассистента в центре — сплошная точка; «нет связи» — бледнее.
    alpha = {s: _pixels(out / "icons" / f"tray-template-{s}@2x.png")[..., 3]
             for s in gen.TRAY_STATES}
    for state in ("idle", "recording", "busy", "offline"):
        assert alpha[state][17:19, 17:19].max() == 0, state
    assert alpha["live"][17:19, 17:19].min() == 255
    assert alpha["idle"][18, 12] == 255  # диск, а не тонкое кольцо
    assert alpha["offline"][18, 8] < 160
