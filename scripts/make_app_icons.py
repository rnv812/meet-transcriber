"""Иконки Meet (0.4, Atlas Aurora): знак B «Диск с сердцевиной» — значок
приложения, установщика и ярлыков, состояния трея, шаблоны строки меню macOS и
favicon окна.

Значок приложения — тот же знак, что в окне (`app/src/ui/MeetMark.tsx`,
вариант B «Диск с сердцевиной»), в фиолетовой палитре: диск радиусом 11/24
стороны с прозрачной сердцевиной 2.6/24, радиальный градиент с центром сверху
слева (cx 32 %, cy 26 %, r 78 % квадрата вокруг диска) по стопам --wave-6 →
--wave-5 → --wave-4 → --wave-2 палитры violet (ice → crest → signal → deep;
в светлой и тёмной теме они одинаковы). Фон прозрачный — без плитки и кромки
(`MARK`, `MARK_GLOW`, `MARK_STOPS`). Так выглядят все кадры icon.ico (заголовок
окна, панель задач, установщик) и PNG Tauri, на любом размере — один ровный
градиент, без пятен и зерна. Цвета не зависят от палитры пользователя: значки
всегда фиолетовые (MeetMark в окне берёт палитру окна).

Только icon.icns (macOS) остаётся на плитке Chaos Black `#0f0f0f` с тонкой
светлой кромкой, как принято на macOS: на ней тот же диск и градиент,
сердцевина — 2.6/11 диска, сквозь неё видна плитка (`TILE_LARGE`, `TILE_SMALL`).

Источник истины — геометрия и цвета в этом файле. Палитра задана в OKLCh, как
токены окна, и переводится в sRGB здесь же (`oklch_to_srgb`). Из неё же
пишется векторный мастер `app/src-tauri/icons/source/meet.svg` (он же
`app/public/favicon.svg`, тот же вектор, что MeetMark): правка цвета или
пропорций — здесь (и в MeetMark.tsx — их сверяет tests/test_app_icons.py),
затем перезапуск скрипта, и вектор с растрами не разойдутся.

Растры рисуются не уменьшением большой картинки, а заново для каждого размера:
покрытие пикселя считается по сетке подвыборок (точное сглаживание без
«звона» Lanczos). Центр знака — на границе пикселей (сторона чётная), поэтому
сердцевина 2.6/24 и на 16 px целиком прозрачна в 2×2 центральных пикселях.

Трей — пять состояний × четыре размера (16/20/24/32: масштаб 100/125/150/200 %).
Оболочка берёт размер по метрике значка Windows (`tray.rs`, `tray_size`):
Windows не растягивает картинку, и края остаются резкими. Состояния различимы
на 16 px не только цветом. Яркий диск трея — тот же градиент знака
(`MARK_STOPS`), но сердцевина у трея непрозрачная (тёмная или светлая — это
часть состояния):

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
PLATE = "#0f0f0f"  # Chaos Black: плитка macOS и тёмная сердцевина трея
EDGE_ALPHA = 0.09  # кромка плитки: oklch(100% 0 0 / .09)

# Знак окна (MeetMark.tsx, viewBox 24), доли стороны: диск r=11, сердцевина r=2.6.
MARK = {"disc": 11 / 24, "core": 2.6 / 24}
# Сияние знака: радиальный градиент с центром сверху слева, доли квадрата,
# описанного вокруг диска (objectBoundingBox, как в MeetMark.tsx).
MARK_GLOW = {"cx": 0.32, "cy": 0.26, "r": 0.78}
# Стопы MeetMark — --wave-6, --wave-5, --wave-4, --wave-2 палитры violet
# (palettes.css, [data-aurora='violet']: ice, crest, signal, deep).
MARK_STOPS = ((0.0, "ice"), (0.35, "crest"), (0.7, "signal"), (1.0, "deep"))

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

# Плитка macOS (icon.icns), доли стороны S: плитка с полями, диск 78/256,
# кромка 2/256; сердцевина — в той же доле диска, что у знака (2.6/11).
_CORE_OF_DISC = MARK["core"] / MARK["disc"]
TILE_LARGE = {
    "inset": 0.07,
    "corner": 0.205,  # доля стороны плитки
    "disc": 78 / 256,
    "core": 78 / 256 * _CORE_OF_DISC,
    "edge": 2 / 256,
}
# 16–48 px: поля съели бы пиксели — плитка во весь размер, диск крупнее
# (на 16 px сердцевина — около 3 px в диаметре, центр на границе пикселей).
TILE_SMALL = {
    "inset": 0.0,
    "corner": 0.22,
    "disc": 0.39,
    "core": 0.39 * _CORE_OF_DISC,
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


def _mark(c: Canvas, disc: float, core: float) -> None:
    """Положить знак: диск радиусом `disc` с прозрачной сердцевиной `core`
    (пиксели), залитый градиентом MeetMark."""
    center = c.size / 2
    ring = c.ring(center, center, disc, core)
    c.over(ring, c.glow(MARK_STOPS, MARK_GLOW, center, disc))


def draw_app(size: int) -> Image.Image:
    """Значок приложения (Windows, PNG Tauri) — знак окна MeetMark на
    прозрачном фоне: диск 11/24 стороны, сердцевина 2.6/24."""
    c = Canvas(size)
    _mark(c, MARK["disc"] * size, MARK["core"] * size)
    return c.image()


def _macos_params(size: int) -> dict:
    return TILE_SMALL if size <= SMALL_UP_TO else TILE_LARGE


def draw_macos(size: int) -> Image.Image:
    """Значок macOS (icon.icns): плитка Chaos Black с кромкой, на ней знак
    (сквозь сердцевину видна плитка)."""
    p = _macos_params(size)
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
    _mark(c, p["disc"] * s, p["core"] * s)
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
        fill = c.glow(((0.0, MUTED_HI), (1.0, MUTED_LO)), MARK_GLOW, center, g["R"])
    else:
        fill = c.glow(MARK_STOPS, MARK_GLOW, center, g["R"])
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
    """Векторный мастер знака — тот же вектор, что MeetMark.tsx (viewBox 24,
    фиолетовая палитра, прозрачный фон): favicon окна и исходник для
    дизайнеров. `size` — только ширина и высота по умолчанию."""
    v = 24  # сторона viewBox, как в MeetMark.tsx
    c = v / 2
    outer = MARK["disc"] * v
    inner = MARK["core"] * v

    def num(x: float) -> str:
        return f"{x:.2f}".rstrip("0").rstrip(".")

    stops = "".join(
        f'<stop offset="{num(o)}" stop-color="{_hex(name)}"/>' for o, name in MARK_STOPS
    )
    # Диск с дыркой-сердцевиной одним контуром (evenodd), без маски.
    disc_path = (
        f"M{num(c - outer)} {num(c)}a{num(outer)} {num(outer)} 0 1 0 {num(2 * outer)} 0"
        f"a{num(outer)} {num(outer)} 0 1 0 {num(-2 * outer)} 0z"
        f"M{num(c - inner)} {num(c)}a{num(inner)} {num(inner)} 0 1 1 {num(2 * inner)} 0"
        f"a{num(inner)} {num(inner)} 0 1 1 {num(-2 * inner)} 0z"
    )
    g = MARK_GLOW
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {v} {v}">\n'
        "<title>Meet</title>\n"
        "<defs>\n"
        f'<radialGradient id="glow" cx="{num(g["cx"])}" cy="{num(g["cy"])}" r="{num(g["r"])}">'
        f"{stops}</radialGradient>\n"
        "</defs>\n"
        f'<path fill="url(#glow)" fill-rule="evenodd" d="{disc_path}"/>\n'
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
    # macOS — тот же знак на плитке.
    icns = icons / "icon.icns"
    biggest = max(ICNS_SIZES)
    draw_macos(biggest).save(
        icns, append_images=[draw_macos(s) for s in ICNS_SIZES if s != biggest]
    )
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
