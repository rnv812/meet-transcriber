"""Иконки оболочки: PNG и ICO для Tauri.

Рисуем на лету, как это делает трей (`tray._icon_image`), а не держим бинарники
в репозитории: иконка простая, а два способа её получить хуже одного.

Запуск: .venv/Scripts/python scripts/make_app_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

# Тот же синий, что у иконки записи в трее: приложение и резидент — одно целое.
BLUE = (40, 110, 220, 255)
WHITE = (255, 255, 255, 255)

OUT_DIR = Path(__file__).resolve().parents[1] / "app" / "src-tauri" / "icons"
PNG_SIZES = (32, 128, 256, 512)
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def draw(size: int) -> Image.Image:
    """Синий круг с белой точкой: круг читается в трее даже 16x16, а точка
    намекает на запись, не изображая микрофон, который на 16 пикселях в кашу."""
    scale = 8  # рисуем крупнее и уменьшаем — дешёвое сглаживание
    canvas = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    pen = ImageDraw.Draw(canvas)
    pen.ellipse((0, 0, size * scale - 1, size * scale - 1), fill=BLUE)
    inner = size * scale * 0.22
    center = size * scale / 2
    pen.ellipse(
        (center - inner, center - inner, center + inner, center + inner), fill=WHITE
    )
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in PNG_SIZES:
        path = OUT_DIR / f"{size}x{size}.png"
        draw(size).save(path)
        print(f"написал {path}")
    # ICO — многоразмерный: Windows берёт нужный слой сам.
    ico = OUT_DIR / "icon.ico"
    draw(256).save(ico, sizes=[(s, s) for s in ICO_SIZES])
    print(f"написал {ico}")
    # icon.png того же размера ждёт сборка бандла на не-Windows.
    draw(512).save(OUT_DIR / "icon.png")


if __name__ == "__main__":
    main()
