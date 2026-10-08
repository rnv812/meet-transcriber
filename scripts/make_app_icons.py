"""Иконки Meet (0.4, Atlas Aurora): знак B «Диск с сердцевиной» — значок
приложения, установщика и ярлыков, состояния трея, шаблоны строки меню macOS и
favicon окна.

Знак — диск фиолетового сияния Aurora с маленькой тёмной сердцевиной на
плитке Chaos Black `#0f0f0f` с тонкой светлой кромкой (страница вариантов —
docs/superpowers/specs/assets/2026-10-08-logo-variants.html, вариант B).
Сияние: основа `mid` (радиальный градиент к `deep` снизу справа), мягкие
размытые пятна `signal` (сверху слева), `crest` (сверху справа), блик `ice` и
тень `abyss` снизу слева; у крупных значков (от 64 px) — едва заметное зерно с
фиксированным seed. Цвета не зависят от палитры пользователя: значки всегда
фиолетовые (знак в окне — `app/src/ui/MeetMark.tsx`, он берёт палитру окна).

Источник истины — геометрия и цвета в этом файле. Палитра задана в OKLCh, как
токены окна, и переводится в sRGB здесь же (`oklch_to_srgb`). Из неё же
пишется векторный мастер `app/src-tauri/icons/source/meet.svg` (он же
`app/public/favicon.svg`, без зерна): правка цвета или пропорций — здесь,
затем перезапуск скрипта, и вектор с растрами не разойдутся.

Растры рисуются не уменьшением большой картинки, а заново для каждого размера:
покрытие пикселя считается по сетке подвыборок (точное сглаживание без
«звона» Lanczos). На 16–48 px пятна сияния размылись бы в кашу, поэтому диск —
упрощённый радиальный градиент signal → mid → deep, без зерна, крупнее (поля
меньше), а сердцевина не меньше двух пикселей на 16 (`APP_SMALL`, `TRAY`).

Трей — пять состояний × четыре размера (16/20/24/32: масштаб 100/125/150/200 %).
Оболочка берёт размер по метрике значка Windows (`tray.rs`, `tray_size`):
Windows не растягивает картинку, и края остаются резкими. Состояния различимы
на 16 px не только цветом:

* idle — диск приглушённый (нейтрально-фиолетовый серый, без сияния): ждём встречу;
* recording — яркий диск и красная точка `danger` в правом нижнем углу,
  отделённая прозрачным вырезом: идёт запись;
* live — яркий диск, сердцевина светлая (`ice`) вместо тёмной: слушает ассистент;
* busy — яркий диск с вырезанным сектором справа сверху: расшифровываем,
  запускаемся, обновляемся;
* offline — приглушённый диск и янтарная точка `warning`: нет движка или
  службы записи.

Строка меню macOS (экспериментально) — те же пять состояний шаблонными
картинками (template image): одноцветные (чёрный с прозрачностью), macOS сама
красит их под светлую и тёмную строку меню. Цвета там нет, поэтому состояния
различаются формой: сердцевина — прозрачная дырка; точка записи — сплошная;
«нет связи» — бледный диск с точкой; ассистент — дырка шире и сплошная точка в
ней; «занят» — вырезанный сектор. Размеры 18 и 36 px (@1x/@2x: строка меню — 18 pt).

Запуск: .venv/Scripts/python scripts/make_app_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, map_coordinates

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "app" / "src-tauri" / "icons"
PUBLIC = ROOT / "app" / "public"


# --- цвета ----------------------------------------------------------------


def oklch_to_srgb(l: float, c: float, h: float) -> tuple[float, float, float]:
    """OKLCh (L 0…1, C, h в градусах) → sRGB 0…1: OKLab → линейный sRGB →
    гамма; цвета вне охвата sRGB отсекаются в [0, 1]."""
    a = c * math.cos(math.radians(h))
    b = c * math.sin(math.radians(h))
    l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (l - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )

    def gamma(v: float) -> float:
        v = min(max(v, 0.0), 1.0)
        return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055

    r, g, bl = (min(max(gamma(v), 0.0), 1.0) for v in linear)
    return r, g, bl


# Фиолетовая палитра Aurora (OKLCh: L, C, h) — как --wave-* в окне при violet.
VIOLET = {
    "abyss": (0.20, 0.06, 290),
    "deep": (0.34, 0.11, 292),
    "mid": (0.46, 0.16, 295),
    "signal": (0.75, 0.12, 300),
    "crest": (0.81, 0.10, 325),
    "ice": (0.93, 0.04, 315),
}
DANGER = (0.72, 0.16, 25)  # точка записи
WARNING = (0.84, 0.14, 82)  # точка «нет связи»
# Приглушённый диск трея: нейтрально-фиолетовый серый (светлее сверху слева).
MUTED_HI = (0.71, 0.035, 292)
MUTED_LO = (0.49, 0.04, 290)
PLATE = "#0f0f0f"  # Chaos Black: плитка и тёмная сердцевина
EDGE_ALPHA = 0.09  # кромка плитки: oklch(100% 0 0 / .09)

# Упрощённое сияние (16–48 px и трей): радиальный градиент с центром сверху
# слева, доли квадрата, описанного вокруг диска (как в MeetMark.tsx).
SMALL_GLOW = {"cx": 0.32, "cy": 0.26, "r": 0.78}
SMALL_STOPS = ((0.0, "signal"), (0.5, "mid"), (1.0, "deep"))
# Крупное сияние: основа — радиальный градиент mid → deep, сверху — размытые
# пятна (координаты и полуоси — в единицах плитки 256, как на странице вариантов).
LARGE_GLOW = {"cx": 0.36, "cy": 0.30, "r": 0.85}
LARGE_STOPS = ((0.0, "mid"), (0.55, "mid"), (1.0, "deep"))
SPOTS = (  # (палитра, cx, cy, rx, ry) — в порядке наложения
    ("abyss", 70, 190, 70, 60),
    ("signal", 96, 78, 62, 44),
    ("crest", 176, 96, 48, 40),
    ("ice", 120, 58, 26, 16),
)
SPOT_BLUR = 18  # σ размытия пятен, единиц плитки 256
GRAIN_FROM = 64  # зерно — только у крупных значков
GRAIN_SEED = 7
GRAIN_ALPHA = 0.06  # едва заметная светлая «пыль»

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

# Значок приложения, доли стороны S: плитка с полями (macOS и крупные плитки
# Windows), диск 78/256, сердцевина 18/256, кромка 2/256.
APP_LARGE = {
    "inset": 0.07,
    "corner": 0.205,  # доля стороны плитки
    "disc": 78 / 256,
    "core": 18 / 256,
    "edge": 2 / 256,
}
# 16–48 px: поля съели бы пиксели — плитка во весь размер, диск крупнее,
# сердцевина толще (на 16 px — 3 px в диаметре, центр на границе пикселей).
APP_SMALL = {
    "inset": 0.0,
    "corner": 0.22,
    "disc": 0.39,
    "core": 0.095,
    "edge": 2 / 256,
}
SMALL_UP_TO = 48

# Трей, в пикселях — подобрано вручную для каждого размера: R — радиус диска,
# core — тёмная сердцевина, light — светлая сердцевина ассистента, dot — радиус
# точки состояния (её центр — в правом нижнем углу), knock — вырез вокруг точки
# (точка отделена от диска прозрачным зазором и не сливается с ним).
TRAY = {
    16: {"R": 7.0, "core": 1.6, "light": 2.1, "dot": 2.9, "knock": 4.15},
    20: {"R": 8.75, "core": 2.0, "light": 2.6, "dot": 3.6, "knock": 5.1},
    24: {"R": 10.5, "core": 2.4, "light": 3.1, "dot": 4.3, "knock": 6.1},
    32: {"R": 14.0, "core": 3.2, "light": 4.1, "dot": 5.7, "knock": 8.1},
}
# Шаблоны строки меню macOS: та же геометрия в масштабе 18/36; hole — дырка
# ассистента (шире сердцевины), в ней сплошная точка light.
TEMPLATE = {
    18: {"R": 7.9, "core": 1.85, "hole": 3.7, "light": 2.0, "dot": 3.1, "knock": 4.5},
    36: {"R": 15.8, "core": 3.7, "hole": 7.4, "light": 4.0, "dot": 6.2, "knock": 9.0},
}
# Диск «нет связи» в шаблоне — бледнее остальных (цвета в шаблоне нет).
TEMPLATE_MUTED_ALPHA = 0.45
# Вырезанный сектор «занят»: центр и половина угла, градусы (0 — вправо, против часовой).
BUSY_GAP_AT = 60.0
BUSY_GAP_HALF = 42.0


# --- растр ----------------------------------------------------------------


def _rgb(color) -> np.ndarray:
    """Цвет в sRGB 0…1: hex-строка, имя палитры VIOLET или тройка OKLCh."""
    if isinstance(color, str) and color.startswith("#"):
        value = color.lstrip("#")
        return np.array([int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)])
    if isinstance(color, str):
        return np.array(oklch_to_srgb(*VIOLET[color]))
    return np.array(oklch_to_srgb(*color))


def _hex(color) -> str:
    return "#" + "".join(f"{round(v * 255):02x}" for v in _rgb(color))


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

    def over(self, coverage: np.ndarray, color, alpha: float = 1.0) -> None:
        """Положить цвет (один или поле RGB) с покрытием `coverage` поверх."""
        a = np.clip(coverage, 0.0, 1.0) * alpha
        rgb = color if isinstance(color, np.ndarray) else _rgb(color)
        src = np.empty_like(self.rgba)
        src[..., :3] = rgb * a[..., None]
        src[..., 3] = a
        self.rgba = src + self.rgba * (1.0 - a[..., None])

    def erase(self, coverage: np.ndarray) -> None:
        """Вырезать до прозрачности (зазор вокруг точки состояния)."""
        self.rgba *= 1.0 - np.clip(coverage, 0.0, 1.0)[..., None]

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

    def radial(self, stops, cx: float, cy: float, radius: float) -> np.ndarray:
        """Радиальный градиент из (cx, cy) радиусом `radius`: поле RGB."""
        t = np.clip(np.sqrt((self.x - cx) ** 2 + (self.y - cy) ** 2) / radius, 0.0, 1.0)
        positions = [p for p, _ in stops]
        colors = np.array([_rgb(c) for _, c in stops])
        return np.stack([np.interp(t, positions, colors[:, k]) for k in range(3)], axis=-1)

    def glow(self, stops, glow: dict, center: float, radius: float) -> np.ndarray:
        """Радиальный градиент в долях квадрата, описанного вокруг диска
        (как objectBoundingBox в SVG)."""
        side = 2 * radius
        x0 = center - radius
        return self.radial(stops, x0 + glow["cx"] * side, x0 + glow["cy"] * side, glow["r"] * side)


def _sector_gap(c: Canvas, center: float) -> np.ndarray:
    """Сектор «занят»: справа сверху, BUSY_GAP_AT ± BUSY_GAP_HALF."""
    return np.abs((c.angle(center, center) - BUSY_GAP_AT + 180.0) % 360.0 - 180.0) < BUSY_GAP_HALF


# Пятна сияния считаются на грубой сетке в единицах плитки 256 (они всё равно
# размыты σ=18), с запасом за края — размытие не упирается в границу.
_SPOT_PAD = 64
_SPOT_CELLS = 256 + 2 * _SPOT_PAD


def _spots_layer() -> np.ndarray:
    """Премультиплицированный RGBA размытых пятен на грубой сетке."""
    axis = np.arange(_SPOT_CELLS) + 0.5 - _SPOT_PAD
    x, y = np.meshgrid(axis, axis)
    layer = np.zeros((_SPOT_CELLS, _SPOT_CELLS, 4))
    for name, cx, cy, rx, ry in SPOTS:
        a = (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0).astype(float)
        src = np.zeros_like(layer)
        src[..., :3] = _rgb(name) * a[..., None]
        src[..., 3] = a
        layer = src + layer * (1.0 - a[..., None])
    return np.stack(
        [gaussian_filter(layer[..., k], SPOT_BLUR, mode="constant") for k in range(4)], axis=-1
    )


def _large_aurora(c: Canvas, center: float, radius: float) -> np.ndarray:
    """Сияние крупного значка: основа-градиент и размытые пятна поверх (поле RGB)."""
    base = c.glow(LARGE_STOPS, LARGE_GLOW, center, radius)
    layer = _spots_layer()
    scale = 256 / c.size
    # Индекс грубой ячейки для каждой подвыборки: центр ячейки i — i + 0.5 - pad.
    coords = [c.y * scale + _SPOT_PAD - 0.5, c.x * scale + _SPOT_PAD - 0.5]
    spots = np.stack(
        [map_coordinates(layer[..., k], coords, order=1, mode="nearest") for k in range(4)],
        axis=-1,
    )
    return spots[..., :3] + base * (1.0 - spots[..., 3:4])


def _app_params(size: int) -> dict:
    return APP_SMALL if size <= SMALL_UP_TO else APP_LARGE


def draw_app(size: int) -> Image.Image:
    """Значок приложения: плитка Chaos Black с кромкой и диск сияния с тёмной
    сердцевиной (сквозь неё видна плитка)."""
    p = _app_params(size)
    c = Canvas(size)
    s = float(size)
    inset = p["inset"] * s
    x0, x1 = inset, s - inset
    corner = p["corner"] * (x1 - x0)
    plate = c.rounded_rect(x0, x0, x1, x1, corner)
    c.over(plate, PLATE)
    # Кромка — тонкий светлый край по всему периметру плитки, как у карточек окна.
    w = p["edge"] * s
    edge = plate & ~c.rounded_rect(x0 + w, x0 + w, x1 - w, x1 - w, max(corner - w, 0.0))
    c.over(edge, "#ffffff", EDGE_ALPHA)
    center = s / 2
    radius = p["disc"] * s
    disc = c.ring(center, center, radius, p["core"] * s)
    if size <= SMALL_UP_TO:
        c.over(disc, c.glow(SMALL_STOPS, SMALL_GLOW, center, radius))
    else:
        c.over(disc, _large_aurora(c, center, radius))
        rng = np.random.default_rng(GRAIN_SEED)
        noise = rng.random((size, size)).repeat(c.ss, axis=0).repeat(c.ss, axis=1)
        c.over(disc * noise, "#ffffff", GRAIN_ALPHA)
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
    disc = c.disc(center, center, g["R"])
    if state == "busy":
        disc = disc & ~(_sector_gap(c, center) & ~c.disc(center, center, g["core"]))
    if state in ("idle", "offline"):
        fill = c.glow(((0.0, MUTED_HI), (1.0, MUTED_LO)), SMALL_GLOW, center, g["R"])
    else:
        fill = c.glow(SMALL_STOPS, SMALL_GLOW, center, g["R"])
    c.over(disc, fill)
    if state == "live":
        c.over(c.disc(center, center, g["light"]), "ice")
    else:
        c.over(c.disc(center, center, g["core"]), PLATE)
    if state in ("recording", "offline"):
        dot_at = size - g["dot"] - size / 32
        c.erase(c.disc(dot_at, dot_at, g["knock"]))
        c.over(c.disc(dot_at, dot_at, g["dot"]), DANGER if state == "recording" else WARNING)
    return c.image()


def draw_tray_template(state: str, size: int) -> Image.Image:
    """Шаблон строки меню macOS: чёрный с прозрачностью, состояния — формой."""
    if state not in TRAY_STATES:
        raise ValueError(f"неизвестное состояние трея: {state}")
    if size not in TEMPLATE:
        raise ValueError(f"нет размера шаблона {size}: есть {sorted(TEMPLATE)}")
    g = TEMPLATE[size]
    c = Canvas(size)
    center = size / 2
    hole = g["hole"] if state == "live" else g["core"]
    shape = c.ring(center, center, g["R"], hole)
    if state == "busy":
        shape = shape & ~_sector_gap(c, center)
    c.over(shape, "#000000", TEMPLATE_MUTED_ALPHA if state == "offline" else 1.0)
    if state == "live":
        c.over(c.disc(center, center, g["light"]), "#000000")
    if state in ("recording", "offline"):
        dot_at = size - g["dot"] - size / 32
        c.erase(c.disc(dot_at, dot_at, g["knock"]))
        c.over(c.disc(dot_at, dot_at, g["dot"]), "#000000")
    return c.image()


# --- вектор ---------------------------------------------------------------


def svg(size: int = 256) -> str:
    """Векторный мастер знака (крупная геометрия APP_LARGE, без зерна): favicon
    окна и исходник для дизайнеров. Те же цвета и пропорции, что у растров."""
    p = APP_LARGE
    s = size
    k = s / 256  # пятна заданы в единицах плитки 256
    inset = p["inset"] * s
    side = s - 2 * inset
    corner = p["corner"] * side
    w = p["edge"] * s
    c = s / 2
    outer = p["disc"] * s
    inner = p["core"] * s

    def num(v: float) -> str:
        return f"{v:.2f}".rstrip("0").rstrip(".")

    stops = "".join(
        f'<stop offset="{num(o)}" stop-color="{_hex(name)}"/>' for o, name in LARGE_STOPS
    )
    disc_path = (
        f"M{num(c - outer)} {num(c)}a{num(outer)} {num(outer)} 0 1 0 {num(2 * outer)} 0"
        f"a{num(outer)} {num(outer)} 0 1 0 {num(-2 * outer)} 0z"
        f"M{num(c - inner)} {num(c)}a{num(inner)} {num(inner)} 0 1 1 {num(2 * inner)} 0"
        f"a{num(inner)} {num(inner)} 0 1 1 {num(-2 * inner)} 0z"
    )
    spots = "".join(
        f'<ellipse cx="{num(cx * k)}" cy="{num(cy * k)}" rx="{num(rx * k)}" ry="{num(ry * k)}" '
        f'fill="{_hex(name)}"/>\n'
        for name, cx, cy, rx, ry in SPOTS
    )
    g = LARGE_GLOW
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" viewBox="0 0 {s} {s}">\n'
        "<title>Meet</title>\n"
        "<defs>\n"
        f'<radialGradient id="glow" cx="{num(g["cx"])}" cy="{num(g["cy"])}" r="{num(g["r"])}">'
        f"{stops}</radialGradient>\n"
        f'<filter id="soft" x="-50%" y="-50%" width="200%" height="200%">'
        f'<feGaussianBlur stdDeviation="{num(SPOT_BLUR * k)}"/></filter>\n'
        f'<clipPath id="disc"><path fill-rule="evenodd" clip-rule="evenodd" d="{disc_path}"/></clipPath>\n'
        "</defs>\n"
        f'<rect x="{num(inset)}" y="{num(inset)}" width="{num(side)}" height="{num(side)}" '
        f'rx="{num(corner)}" fill="{PLATE}"/>\n'
        f'<rect x="{num(inset + w / 2)}" y="{num(inset + w / 2)}" width="{num(side - w)}" '
        f'height="{num(side - w)}" rx="{num(corner - w / 2)}" fill="none" stroke="#ffffff" '
        f'stroke-opacity="{num(EDGE_ALPHA)}" stroke-width="{num(w)}"/>\n'
        '<g clip-path="url(#disc)">\n'
        f'<rect x="{num(c - outer)}" y="{num(c - outer)}" width="{num(2 * outer)}" '
        f'height="{num(2 * outer)}" fill="url(#glow)"/>\n'
        f'<g filter="url(#soft)">\n{spots}</g>\n'
        "</g>\n"
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
