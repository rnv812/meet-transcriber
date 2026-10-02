"""Иконки Meet: светящееся кольцо из баннера — значок приложения, состояния
трея и favicon окна.

Источник истины — геометрия и цвета в этом файле. Из них же пишется векторный
мастер `app/src-tauri/icons/source/meet.svg` (он же `app/public/favicon.svg`):
правка цвета или пропорций — здесь, затем перезапуск скрипта, и вектор с
растрами не разойдутся.

Растры рисуются не уменьшением большой картинки, а заново для каждого размера:
покрытие пикселя считается по сетке подвыборок (точное сглаживание без
«звона» Lanczos), а для 16–48 px пропорции кольца подобраны по пикселям
(`APP_SMALL`, `TRAY`) — на 16 px кольцо читается, а не расплывается.

Трей — пять состояний × четыре размера (16/20/24/32: масштаб 100/125/150/200 %).
Оболочка берёт размер по метрике значка Windows (`tray.rs`, `tray_size`):
Windows не растягивает картинку, и края остаются резкими.

* idle — кольцо приглушённое: ждём встречу;
* recording — кольцо яркое, со свечением, и красная точка: идёт запись;
* live — кольцо яркое, со свечением, и светлая точка в центре: слушает ассистент;
* busy — кольцо с разрывом (дуга): расшифровываем, запускаемся, обновляемся;
* offline — кольцо приглушённое и янтарная точка: нет движка или службы записи.

Строка меню macOS (экспериментально) — те же пять состояний шаблонными
картинками (template image): одноцветные, macOS сама красит их под светлую и
тёмную строку меню. Цвета там нет, поэтому состояния различаются формой: точка
записи — сплошная, «нет связи» — бледное кольцо с точкой, ассистент — точка в
центре, «занят» — дуга. Размеры 18 и 36 px (@1x/@2x: строка меню — 18 pt).

Запуск: .venv/Scripts/python scripts/make_app_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "app" / "src-tauri" / "icons"
PUBLIC = ROOT / "app" / "public"

# --- цвета (баннер: docs/images/banner.png, токены окна: app/src/theme/tokens.css)
BG_TOP = "#1a1b21"  # --surface-2
BG_BOTTOM = "#0f1012"  # --bg
ACCENT = "#5e6ad2"  # --accent
ACCENT_HI = "#7c86f0"  # --accent-hi: свечение
VIOLET = "#3f4bb8"  # --violet: тень кольца
# Кольцо — линейный градиент сверху-слева вниз-вправо: светлый край «ловит свет».
RING_STOPS = ((0.0, "#a3abff"), (0.5, ACCENT), (1.0, VIOLET))
# Трей: яркое кольцо (запись, ассистент), рабочее (дуга), приглушённое.
TRAY_BRIGHT = ((0.0, "#a3abff"), (0.55, "#6a76e6"), (1.0, "#4652c8"))
TRAY_WORK = ((0.0, "#8f98f6"), (1.0, ACCENT))
TRAY_MUTED = ((0.0, "#9195c2"), (1.0, "#5c6190"))
RED = "#ef4b51"  # чуть светлее --red: на 16 px точка маленькая
AMBER = "#f5a524"  # --amber
# Точка ассистента — светлый акцент: видна и в тёмной, и в светлой панели задач.
LIVE_DOT = "#8d96ff"

APP_PNG = {  # имя файла → размер
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "256x256.png": 256,
    "512x512.png": 512,
    "1024x1024.png": 1024,
    "icon.png": 512,  # Tauri берёт его на не-Windows
}
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)
TRAY_STATES = ("idle", "recording", "live", "busy", "offline")
TRAY_SIZES = (16, 20, 24, 32)
# Строка меню macOS: шаблонные картинки @1x и @2x.
TEMPLATE_SIZES = {18: "", 36: "@2x"}

# Значок приложения, доли стороны S. Тёмный скруглённый квадрат, под кольцом —
# мягкое пятно акцента, вокруг кольца — свечение (как у логотипа в баннере).
APP_LARGE = {
    "inset": 0.07,  # поля (macOS и крупные плитки Windows)
    "corner": 0.205,
    "ring": 0.255,  # внешний радиус кольца
    "hole": 0.44,  # внутренний радиус — доля внешнего (в баннере 14/32)
    "glow": 0.055,  # σ свечения кольца
    "glow_alpha": 0.75,
    "spot": 0.36,  # радиус пятна под кольцом
    "spot_alpha": 0.32,
}
# 16–48 px: поля съели бы пиксели, свечение — резкость. Кольцо крупнее,
# центр — на границе пикселей (S чётное), ширина кольца — около трёх пикселей
# на 16.
APP_SMALL = {
    "inset": 0.0,
    "corner": 0.22,
    "ring": 0.345,
    "hole": 0.45,
    "glow": 0.035,
    "glow_alpha": 0.55,
    "spot": 0.42,
    "spot_alpha": 0.22,
}
SMALL_UP_TO = 48

# Трей, в пикселях — подобрано вручную для каждого размера: R — внешний радиус
# кольца, r — внутренний, dot — радиус точки состояния (её центр — в правом
# нижнем углу), knock — вырез вокруг точки (точка отделена от кольца
# прозрачным зазором и не сливается с ним), center — точка ассистента,
# halo — σ свечения.
TRAY = {
    16: {"R": 7.0, "r": 3.25, "dot": 2.9, "knock": 4.15, "center": 1.75, "halo": 0.55},
    20: {"R": 8.75, "r": 4.05, "dot": 3.6, "knock": 5.1, "center": 2.15, "halo": 0.7},
    24: {"R": 10.5, "r": 4.85, "dot": 4.3, "knock": 6.1, "center": 2.6, "halo": 0.85},
    32: {"R": 14.0, "r": 6.5, "dot": 5.7, "knock": 8.1, "center": 3.4, "halo": 1.1},
}
# Шаблоны строки меню macOS: та же геометрия, что у трея 16/32, в масштабе 18/36.
TEMPLATE = {
    18: {"R": 7.9, "r": 4.0, "dot": 3.1, "knock": 4.5, "center": 1.9},
    36: {"R": 15.8, "r": 8.0, "dot": 6.2, "knock": 9.0, "center": 3.8},
}
# Кольцо «нет связи» в шаблоне — бледнее остальных (цвета в шаблоне нет).
TEMPLATE_MUTED_ALPHA = 0.45
# Свечение — тонкая каёмка: на светлой панели задач шире оно читается как размытие.
HALO_ALPHA = 0.4
# Разрыв дуги «занят»: центр и половина угла, градусы (0 — вправо, против часовой).
BUSY_GAP_AT = 60.0
BUSY_GAP_HALF = 42.0


# --- растр ----------------------------------------------------------------


def _rgb(hex_color: str) -> np.ndarray:
    value = hex_color.lstrip("#")
    return np.array([int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)])


def _supersample(size: int) -> int:
    """Подвыборок на пиксель по стороне: мелким — много, крупным — хватит двух."""
    return max(2, min(16, 1024 // size))


class Canvas:
    """Премультиплицированный RGBA на сетке подвыборок; координаты — в пикселях
    итоговой картинки (0…size), центр пикселя i — i + 0.5."""

    def __init__(self, size: int):
        self.size = size
        self.ss = _supersample(size)
        n = size * self.ss
        axis = (np.arange(n) + 0.5) / self.ss
        self.x, self.y = np.meshgrid(axis, axis)
        self.rgba = np.zeros((n, n, 4))

    def over(self, coverage: np.ndarray, color: np.ndarray | str, alpha: float = 1.0) -> None:
        """Положить цвет (один или поле RGB) с покрытием `coverage` поверх."""
        a = np.clip(coverage, 0.0, 1.0) * alpha
        rgb = _rgb(color) if isinstance(color, str) else color
        src = np.empty_like(self.rgba)
        src[..., :3] = rgb * a[..., None]
        src[..., 3] = a
        self.rgba = src + self.rgba * (1.0 - a[..., None])

    def erase(self, coverage: np.ndarray) -> None:
        """Вырезать до прозрачности (зазор вокруг точки состояния)."""
        self.rgba *= 1.0 - np.clip(coverage, 0.0, 1.0)[..., None]

    def blur(self, coverage: np.ndarray, sigma_px: float) -> np.ndarray:
        return gaussian_filter(coverage.astype(float), sigma_px * self.ss, mode="constant")

    def image(self) -> Image.Image:
        n, ss = self.size, self.ss
        pixels = self.rgba.reshape(n, ss, n, ss, 4).mean(axis=(1, 3))
        alpha = pixels[..., 3:4]
        rgb = np.where(alpha > 0, pixels[..., :3] / np.maximum(alpha, 1e-12), 0.0)
        out = np.concatenate([rgb, alpha], axis=-1)
        return Image.fromarray(np.round(np.clip(out, 0, 1) * 255).astype(np.uint8), "RGBA")

    # фигуры: булевы маски на сетке подвыборок
    def disc(self, cx: float, cy: float, radius: float) -> np.ndarray:
        return (self.x - cx) ** 2 + (self.y - cy) ** 2 <= radius**2

    def ring(self, cx: float, cy: float, outer: float, inner: float) -> np.ndarray:
        d2 = (self.x - cx) ** 2 + (self.y - cy) ** 2
        return (d2 <= outer**2) & (d2 >= inner**2)

    def rounded_rect(self, x0: float, y0: float, x1: float, y1: float, radius: float) -> np.ndarray:
        qx = np.clip(self.x, x0 + radius, x1 - radius)
        qy = np.clip(self.y, y0 + radius, y1 - radius)
        return (self.x - qx) ** 2 + (self.y - qy) ** 2 <= radius**2

    def angle(self, cx: float, cy: float) -> np.ndarray:
        """Угол точки от центра, градусы 0…360 (0 — вправо, против часовой)."""
        return np.degrees(np.arctan2(cy - self.y, self.x - cx)) % 360.0

    def gradient(self, stops, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
        """Линейный градиент от (x0, y0) к (x1, y1): поле RGB."""
        dx, dy = x1 - x0, y1 - y0
        t = np.clip(((self.x - x0) * dx + (self.y - y0) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
        positions = [p for p, _ in stops]
        colors = np.array([_rgb(c) for _, c in stops])
        return np.stack([np.interp(t, positions, colors[:, k]) for k in range(3)], axis=-1)


def _app_params(size: int) -> dict:
    return APP_SMALL if size <= SMALL_UP_TO else APP_LARGE


def draw_app(size: int) -> Image.Image:
    """Значок приложения: тёмный скруглённый квадрат и светящееся кольцо."""
    p = _app_params(size)
    c = Canvas(size)
    s = float(size)
    inset = round(p["inset"] * s)
    x0, x1 = inset, s - inset
    side = x1 - x0
    plate = c.rounded_rect(x0, x0, x1, x1, p["corner"] * side)
    c.over(plate, c.gradient(((0.0, BG_TOP), (1.0, BG_BOTTOM)), 0, x0, 0, x1))
    # Пятно акцента под кольцом — только внутри плитки.
    center = s / 2
    d = np.sqrt((c.x - center) ** 2 + (c.y - center) ** 2)
    spot = np.clip(1.0 - d / (p["spot"] * s), 0.0, 1.0) ** 2 * plate
    c.over(spot, ACCENT, p["spot_alpha"])
    outer = p["ring"] * s
    ring = c.ring(center, center, outer, outer * p["hole"])
    glow = c.blur(ring, p["glow"] * s) * plate
    c.over(glow, ACCENT_HI, p["glow_alpha"])
    c.over(ring, c.gradient(RING_STOPS, center - outer, center - outer, center + outer, center + outer))
    if size > SMALL_UP_TO:
        # Тонкий светлый кант по верху плитки — как край карточки в окне.
        edge = plate & ~c.rounded_rect(x0, x0 + s / 256, x1, x1, p["corner"] * side)
        c.over(edge, "#ffffff", 0.08)
    return c.image()


def draw_tray(state: str, size: int) -> Image.Image:
    """Значок трея для состояния `state` (см. TRAY_STATES) размером `size`."""
    if state not in TRAY_STATES:
        raise ValueError(f"неизвестное состояние трея: {state}")
    if size not in TRAY:
        raise ValueError(f"нет размера трея {size}: есть {sorted(TRAY)}")
    g = TRAY[size]
    c = Canvas(size)
    center = size / 2
    ring = c.ring(center, center, g["R"], g["r"])
    if state == "busy":
        gap = np.abs((c.angle(center, center) - BUSY_GAP_AT + 180.0) % 360.0 - 180.0) < BUSY_GAP_HALF
        ring = ring & ~gap
        mid, half = (g["R"] + g["r"]) / 2, (g["R"] - g["r"]) / 2
        for edge in (BUSY_GAP_AT - BUSY_GAP_HALF, BUSY_GAP_AT + BUSY_GAP_HALF):
            ax = center + mid * math.cos(math.radians(edge))
            ay = center - mid * math.sin(math.radians(edge))
            ring = ring | c.disc(ax, ay, half)
    stops = {
        "idle": TRAY_MUTED,
        "offline": TRAY_MUTED,
        "busy": TRAY_WORK,
        "recording": TRAY_BRIGHT,
        "live": TRAY_BRIGHT,
    }[state]
    if state in ("recording", "live"):
        c.over(c.blur(ring, g["halo"]), ACCENT_HI, HALO_ALPHA)
    lo, hi = center - g["R"], center + g["R"]
    c.over(ring, c.gradient(stops, lo, lo, hi, hi))
    if state == "live":
        c.over(c.disc(center, center, g["center"]), LIVE_DOT)
    if state in ("recording", "offline"):
        dot_at = size - g["dot"] - size / 32
        c.erase(c.disc(dot_at, dot_at, g["knock"]))
        c.over(c.disc(dot_at, dot_at, g["dot"]), RED if state == "recording" else AMBER)
    return c.image()


def _ring_shape(c: "Canvas", state: str, center: float, outer: float, inner: float) -> np.ndarray:
    """Кольцо состояния: у «занят» — дуга с разрывом и скруглёнными концами."""
    ring = c.ring(center, center, outer, inner)
    if state == "busy":
        gap = np.abs((c.angle(center, center) - BUSY_GAP_AT + 180.0) % 360.0 - 180.0) < BUSY_GAP_HALF
        ring = ring & ~gap
        mid, half = (outer + inner) / 2, (outer - inner) / 2
        for edge in (BUSY_GAP_AT - BUSY_GAP_HALF, BUSY_GAP_AT + BUSY_GAP_HALF):
            ax = center + mid * math.cos(math.radians(edge))
            ay = center - mid * math.sin(math.radians(edge))
            ring = ring | c.disc(ax, ay, half)
    return ring


def draw_tray_template(state: str, size: int) -> Image.Image:
    """Шаблон строки меню macOS: чёрный с прозрачностью, состояния — формой."""
    if state not in TRAY_STATES:
        raise ValueError(f"неизвестное состояние трея: {state}")
    if size not in TEMPLATE:
        raise ValueError(f"нет размера шаблона {size}: есть {sorted(TEMPLATE)}")
    g = TEMPLATE[size]
    c = Canvas(size)
    center = size / 2
    ring = _ring_shape(c, state, center, g["R"], g["r"])
    c.over(ring, "#000000", TEMPLATE_MUTED_ALPHA if state == "offline" else 1.0)
    if state == "live":
        c.over(c.disc(center, center, g["center"]), "#000000")
    if state in ("recording", "offline"):
        dot_at = size - g["dot"] - size / 32
        c.erase(c.disc(dot_at, dot_at, g["knock"]))
        c.over(c.disc(dot_at, dot_at, g["dot"]), "#000000")
    return c.image()


# --- вектор ---------------------------------------------------------------


def svg(size: int = 256) -> str:
    """Векторный мастер значка (крупная геометрия APP_LARGE): favicon окна и
    исходник для дизайнеров. Те же цвета и пропорции, что у растров."""
    p = APP_LARGE
    s = size
    inset = p["inset"] * s
    side = s - 2 * inset
    corner = p["corner"] * side
    c = s / 2
    outer = p["ring"] * s
    inner = outer * p["hole"]

    def num(v: float) -> str:
        return f"{v:.2f}".rstrip("0").rstrip(".")

    stops = "".join(
        f'<stop offset="{num(o)}" stop-color="{col}"/>' for o, col in RING_STOPS
    )
    ring_path = (
        f"M{num(c - outer)} {num(c)}a{num(outer)} {num(outer)} 0 1 0 {num(2 * outer)} 0"
        f"a{num(outer)} {num(outer)} 0 1 0 {num(-2 * outer)} 0z"
        f"M{num(c - inner)} {num(c)}a{num(inner)} {num(inner)} 0 1 1 {num(2 * inner)} 0"
        f"a{num(inner)} {num(inner)} 0 1 1 {num(-2 * inner)} 0z"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" viewBox="0 0 {s} {s}">\n'
        "<title>Meet</title>\n"
        "<defs>\n"
        f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{BG_TOP}"/><stop offset="1" stop-color="{BG_BOTTOM}"/></linearGradient>\n'
        f'<radialGradient id="spot" cx="0.5" cy="0.5" r="{num(p["spot"] * s / side)}">'
        f'<stop offset="0" stop-color="{ACCENT}" stop-opacity="{num(p["spot_alpha"])}"/>'
        f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>\n'
        f'<linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">{stops}</linearGradient>\n'
        f'<filter id="glow" x="-50%" y="-50%" width="200%" height="200%">'
        f'<feGaussianBlur stdDeviation="{num(p["glow"] * s)}"/></filter>\n'
        f'<clipPath id="plate"><rect x="{num(inset)}" y="{num(inset)}" width="{num(side)}" '
        f'height="{num(side)}" rx="{num(corner)}"/></clipPath>\n'
        "</defs>\n"
        f'<rect x="{num(inset)}" y="{num(inset)}" width="{num(side)}" height="{num(side)}" '
        f'rx="{num(corner)}" fill="url(#bg)"/>\n'
        '<g clip-path="url(#plate)">\n'
        f'<rect x="{num(inset)}" y="{num(inset)}" width="{num(side)}" height="{num(side)}" fill="url(#spot)"/>\n'
        f'<path d="{ring_path}" fill="{ACCENT_HI}" fill-rule="evenodd" '
        f'opacity="{num(p["glow_alpha"])}" filter="url(#glow)"/>\n'
        "</g>\n"
        f'<path d="{ring_path}" fill="url(#ring)" fill-rule="evenodd"/>\n'
        "</svg>\n"
    )


# --- запись ---------------------------------------------------------------


def render_all(icons: Path = ICONS, public: Path = PUBLIC) -> list[Path]:
    """Нарисовать всё и записать; вернуть записанные файлы."""
    icons.mkdir(parents=True, exist_ok=True)
    (icons / "source").mkdir(exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    cache: dict[int, Image.Image] = {}

    def app(size: int) -> Image.Image:
        if size not in cache:
            cache[size] = draw_app(size)
        return cache[size]

    for name, size in APP_PNG.items():
        path = icons / name
        app(size).save(path, optimize=True)
        written.append(path)
    # ICO и ICNS — из отдельно нарисованных слоёв, а не уменьшением одного.
    ico = icons / "icon.ico"
    largest = max(ICO_SIZES)
    app(largest).save(
        ico,
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[app(s) for s in ICO_SIZES if s != largest],
    )
    written.append(ico)
    icns = icons / "icon.icns"
    biggest = max(ICNS_SIZES)
    app(biggest).save(icns, append_images=[app(s) for s in ICNS_SIZES if s != biggest])
    written.append(icns)
    for state in TRAY_STATES:
        for size in TRAY_SIZES:
            path = icons / f"tray-{state}-{size}.png"
            draw_tray(state, size).save(path, optimize=True)
            written.append(path)
        for size, suffix in TEMPLATE_SIZES.items():
            path = icons / f"tray-template-{state}{suffix}.png"
            draw_tray_template(state, size).save(path, optimize=True)
            written.append(path)
    text = svg()
    for path in (icons / "source" / "meet.svg", public / "favicon.svg"):
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def main() -> None:
    for path in render_all():
        print(f"написал {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
