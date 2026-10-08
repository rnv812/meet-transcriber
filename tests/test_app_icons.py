"""Иконки Meet (scripts/make_app_icons.py): все размеры, которые ждут Tauri,
Windows и трей, на месте; растры в репозитории — ровно то, что рисует скрипт
(правка цвета — перезапуск скрипта, а не ручная правка PNG); значок Windows и
PNG Tauri — тот же знак, что MeetMark в окне (фиолетовая палитра, без плитки),
icon.icns — он же на плитке; состояния трея различимы на 16 px."""

import colorsys
import importlib.util
import json
import math
import re
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
    """Растры воспроизводимы: одинаковый вызов — одинаковые пиксели."""
    gen = _generator()
    for state in gen.TRAY_STATES:
        first = np.asarray(gen.draw_tray(state, 16))
        assert np.array_equal(first, np.asarray(gen.draw_tray(state, 16))), state
    for size in (32, 64, 128):
        assert np.array_equal(np.asarray(gen.draw_app(size)), np.asarray(gen.draw_app(size))), size
        assert np.array_equal(np.asarray(gen.draw_macos(size)), np.asarray(gen.draw_macos(size))), size


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


def _mark_points(size: int) -> dict:
    """Опорные точки знака MeetMark на картинке `size`: центр градиента (сверху
    слева, там почти --wave-6) и точка снизу справа (ближе к --wave-2)."""
    gen = _generator()
    c, r = size / 2, gen.MARK["disc"] * size
    box = c - r
    glow = gen.MARK_GLOW
    up_left = (box + glow["cx"] * 2 * r, box + glow["cy"] * 2 * r)
    low_right = (c + 0.42 * r, c + 0.42 * r)
    return {"centre": c, "radius": r, "up_left": up_left, "low_right": low_right}


def _at(a: np.ndarray, point) -> np.ndarray:
    x, y = point
    return a[int(y), int(x)]


def _assert_window_mark(a: np.ndarray, name: str) -> None:
    """Картинка — знак окна MeetMark: прозрачный фон без плитки и кромки,
    прозрачная сердцевина в центре, сверху слева — светлый --wave-6
    фиолетовой палитры, снизу справа — темнее."""
    gen = _generator()
    size = a.shape[0]
    pts = _mark_points(size)
    for corner in (a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]):
        assert corner[3] == 0, f"{name}: угол не прозрачный — плитка?"
    h = size // 2
    assert (a[h - 1 : h + 1, h - 1 : h + 1, 3] == 0).all(), f"{name}: сердцевина не прозрачна"
    lit = _at(a, pts["up_left"])
    assert lit[3] == 255, name
    l_ice, _, h_ice = gen.VIOLET[gen.MARK_STOPS[0][1]]
    lightness, _, hue = _oklch(lit)
    assert lightness == pytest.approx(l_ice, abs=0.04), (name, lightness)
    assert abs((hue - h_ice + 180) % 360 - 180) <= 25, (name, hue)
    dark = _at(a, pts["low_right"])
    assert dark[3] == 255, name
    assert _oklch(dark)[0] < lightness - 0.15, name
    assert 270 <= _oklch(dark)[2] <= 335, name
    # Диск — 11/24 стороны: у края внутри непрозрачно, за краем — прозрачно.
    assert a[h, int(pts["centre"] - 0.8 * pts["radius"]), 3] == 255, name
    outside = int(pts["centre"] - pts["radius"]) - 1
    if outside >= 0:
        assert a[h, outside, 3] == 0, name


def test_mark_constants_match_meet_mark_component():
    """Генератор и MeetMark.tsx — один знак: те же радиусы (из viewBox 24),
    геометрия градиента и стопы --wave-* фиолетовой палитры окна."""
    gen = _generator()
    tsx = (ROOT / "app" / "src" / "ui" / "MeetMark.tsx").read_text(encoding="utf-8")
    assert 'viewBox="0 0 24 24"' in tsx
    assert '<circle cx="12" cy="12" r="11"' in tsx
    assert '<circle cx="12" cy="12" r="2.6"' in tsx
    assert gen.MARK == {"disc": pytest.approx(11 / 24), "core": pytest.approx(2.6 / 24)}
    m = re.search(r'radialGradient[^>]*cx="(\d+)%" cy="(\d+)%" r="(\d+)%"', tsx)
    assert m and gen.MARK_GLOW == {
        "cx": pytest.approx(int(m[1]) / 100),
        "cy": pytest.approx(int(m[2]) / 100),
        "r": pytest.approx(int(m[3]) / 100),
    }
    stops = re.findall(r'<stop offset="([\d.]+)" style=\{\{ stopColor: "var\(--wave-(\d)\)" \}\}', tsx)
    css = (ROOT / "app" / "src" / "theme" / "aurora" / "palettes.css").read_text(encoding="utf-8")
    block = css.split("[data-aurora='violet'] {", 1)[1].split("}", 1)[0]
    waves = dict(re.findall(r"--wave-(\d): var\(--violet-(\w+)\)", block))
    assert len(stops) == 4 and len(waves) == 6
    assert [(float(o), waves[w]) for o, w in stops] == [
        (pytest.approx(o), name) for o, name in gen.MARK_STOPS
    ]
    tokens = (ROOT / "app" / "src" / "theme" / "aurora" / "tokens.css").read_text(encoding="utf-8")
    for _, name in gen.MARK_STOPS:
        t = re.search(rf"--violet-{name}: oklch\(([\d.]+)% ([\d.]+) ([\d.]+)\)", tokens)
        assert t, name
        assert gen.VIOLET[name] == pytest.approx((float(t[1]) / 100, float(t[2]), float(t[3]))), name


def test_windows_icons_are_the_window_mark(made):
    """Значок Windows (каждый кадр icon.ico) и PNG Tauri — знак окна MeetMark
    в фиолетовой палитре на любом размере: без плитки, сердцевина прозрачна."""
    gen, out, _ = made
    ico = out / "icons" / "icon.ico"
    for size in gen.ICO_SIZES:
        image = Image.open(ico)
        image.size = (size, size)
        a = np.asarray(image.convert("RGBA"), dtype=int)
        assert a.shape[0] == size
        _assert_window_mark(a, f"icon.ico {size}")
    for name in gen.APP_PNG:
        _assert_window_mark(_pixels(out / "icons" / name), name)
    # На 16 px сердцевина — не меньше двух пикселей, по центру, вокруг — диск.
    small = np.asarray(gen.draw_app(16), dtype=int)
    assert (small[7:9, 7:9, 3] == 0).all()
    assert (small[[5, 10], 7:9, 3] > 200).all() and (small[7:9, [5, 10], 3] > 200).all()
    # Крупные — тот же ровный градиент, без пятен и зерна: соседние пиксели близки.
    big = np.asarray(gen.draw_app(256), dtype=int)
    patch = big[70:90, 100:120, :3]
    assert np.abs(np.diff(patch, axis=1)).max() <= 4


def test_macos_icon_keeps_the_tile(made):
    """icon.icns — плитка Chaos Black (так принято на macOS), на ней тот же
    знак: градиент MeetMark, сердцевина 2.6/11 диска (сквозь неё видна плитка)."""
    gen, out, _ = made
    big = np.asarray(Image.open(out / "icons" / "icon.icns").convert("RGBA"), dtype=int)
    assert big.shape[0] == 1024
    assert big[4, 4, 3] == 0, "поля за плиткой прозрачны"
    for y, x in ((512, 512), (512, 110), (110, 512)):  # сердцевина и плитка у края
        assert big[y, x, 3] == 255 and np.abs(big[y, x, :3] - 0x0F).max() <= 6, (y, x)
    for size in (16, 32, 256, 1024):
        a = np.asarray(gen.draw_macos(size), dtype=int)
        p = gen._macos_params(size)
        assert p["core"] == pytest.approx(p["disc"] * 2.6 / 11)
        c, r = size / 2, p["disc"] * size
        h = size // 2
        assert (a[h - 1 : h + 1, h - 1 : h + 1, 3] == 255).all(), size
        assert a[h - 1 : h + 1, h - 1 : h + 1, :3].max() < 45, size
        lit = a[int(c - r + gen.MARK_GLOW["cy"] * 2 * r), int(c - r + gen.MARK_GLOW["cx"] * 2 * r)]
        assert _oklch(lit)[0] == pytest.approx(gen.VIOLET["ice"][0], abs=0.05), size


def test_vector_master_is_the_window_mark(made):
    """meet.svg и favicon.svg — один и тот же вектор знака окна MeetMark: viewBox
    24, диск r=11 с прозрачной сердцевиной r=2.6, радиальный градиент
    --wave-6/5/4/2 фиолетовой палитры; без плитки, пятен и прежнего индиго."""
    gen, out, _ = made
    master = (out / "icons" / "source" / "meet.svg").read_text(encoding="utf-8")
    favicon = (out / "public" / "favicon.svg").read_text(encoding="utf-8")
    assert master == favicon
    assert 'viewBox="0 0 24 24"' in master
    assert 'cx="0.32" cy="0.26" r="0.78"' in master
    stops = re.findall(r'<stop offset="([\d.]+)" stop-color="(#[0-9a-f]{6})"/>', master)
    assert stops == [(f"{o:g}", gen._hex(name)) for o, name in gen.MARK_STOPS]
    assert 'fill-rule="evenodd"' in master and "a11 11" in master and "a2.6 2.6" in master
    for gone in ("#0f0f0f", "feGaussianBlur", "<ellipse", "<rect"):
        assert gone not in master, gone
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

    # Ожидание и «нет связи» — приглушённые: серее (меньше хрома OKLCh) и темнее
    # яркого диска, у которого градиент знака MeetMark (светлый сверху слева).
    def chroma_lightness(a):
        disc = a[:10, :, :].reshape(-1, 4)
        disc = disc[(disc[:, 3] > 200) & (disc[:, :3].max(axis=1) > 70)]
        ok = [_oklch(p) for p in disc]
        return float(np.mean([o[1] for o in ok])), float(np.mean([o[0] for o in ok]))

    for muted in ("idle", "offline"):
        for bright in ("recording", "live", "busy"):
            (c_m, l_m), (c_b, l_b) = chroma_lightness(px[muted]), chroma_lightness(px[bright])
            assert c_m + 0.04 < c_b, (muted, bright)
            assert l_m + 0.1 < l_b, (muted, bright)
    # Яркий диск — градиент знака окна: в центре градиента почти --wave-6 (ice).
    r = gen.TRAY[32]["R"]
    lit = np.asarray(gen.draw_tray("recording", 32), dtype=int)[
        int(16 - r + gen.MARK_GLOW["cy"] * 2 * r), int(16 - r + gen.MARK_GLOW["cx"] * 2 * r)
    ]
    assert _oklch(lit)[0] == pytest.approx(gen.VIOLET["ice"][0], abs=0.04)
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
