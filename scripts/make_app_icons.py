"""Иконки оболочки: PNG и ICO для Tauri, плюс иконки состояний трея.

Рисуем на лету, как это делает трей (`tray._icon_image`), а не держим бинарники
в репозитории: иконка простая, а два способа её получить хуже одного.

Запуск: .venv/Scripts/python scripts/make_app_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

# Тот же синий, что у иконки записи в трее: приложение и резидент — одно целое.
BLUE = (40, 110, 220, 255)
WHITE = (255, 255, 255, 255)
# Состояния трея: серый — ждём, красный — пишем, синий — расшифровываем.
GREY = (128, 134, 142, 255)
RED = (220, 50, 47, 255)
DARK = (70, 74, 80, 255)
TRAY_SIZE = 32
SCALE = 8  # рисуем крупнее и уменьшаем — дешёвое сглаживание

OUT_DIR = Path(__file__).resolve().parents[1] / "app" / "src-tauri" / "icons"
PNG_SIZES = (32, 128, 256, 512)
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def _base(size: int, disc=BLUE, dot=WHITE) -> Image.Image:
    """Базовая иконка в крупном масштабе: круг с точкой по центру."""
    big = size * SCALE
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pen = ImageDraw.Draw(canvas)
    pen.ellipse((0, 0, big - 1, big - 1), fill=disc)
    inner = big * 0.22
    center = big / 2
    pen.ellipse(
        (center - inner, center - inner, center + inner, center + inner), fill=dot
    )
    return canvas


def draw(size: int) -> Image.Image:
    """Синий круг с белой точкой: круг читается в трее даже 16x16, а точка
    намекает на запись, не изображая микрофон, который на 16 пикселях в кашу."""
    return _base(size).resize((size, size), Image.LANCZOS)


def draw_tray(state: str, size: int = TRAY_SIZE) -> Image.Image:
    """Иконка состояния трея — та же базовая форма (круг с точкой), по-разному
    окрашенная, чтобы состояние читалось цветом и силуэтом:

    * idle — серый круг: ждём встречу;
    * recording — красная точка на белом: идёт запись;
    * busy — синее кольцо с точкой: расшифровываем;
    * offline — серый круг, перечёркнутый: сервиса записи нет.
    """
    big = size * SCALE
    if state == "idle":
        canvas = _base(size, disc=GREY)
    elif state == "recording":
        canvas = _base(size, disc=RED)
        pen = ImageDraw.Draw(canvas)
        # Белый ободок: красная точка отделяется от красной панели задач.
        ring = big * 0.07
        pen.ellipse((0, 0, big - 1, big - 1), outline=WHITE, width=int(ring))
    elif state == "busy":
        canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        pen = ImageDraw.Draw(canvas)
        pen.ellipse((0, 0, big - 1, big - 1), outline=BLUE, width=int(big * 0.16))
        inner = big * 0.18
        center = big / 2
        pen.ellipse(
            (center - inner, center - inner, center + inner, center + inner), fill=BLUE
        )
    elif state == "offline":
        canvas = _base(size, disc=GREY)
        pen = ImageDraw.Draw(canvas)
        width = int(big * 0.12)
        # Белая подложка под чертой — видна и на тёмной, и на светлой панели.
        pen.line((big * 0.12, big * 0.88, big * 0.88, big * 0.12), fill=WHITE, width=width + int(big * 0.08))
        pen.line((big * 0.12, big * 0.88, big * 0.88, big * 0.12), fill=DARK, width=width)
    else:
        raise ValueError(f"неизвестное состояние трея: {state}")
    return canvas.resize((size, size), Image.LANCZOS)


TRAY_STATES = ("idle", "recording", "busy", "offline")


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
    # Состояния трея: оболочка вшивает их в exe (`include_bytes!` в tray.rs).
    for state in TRAY_STATES:
        path = OUT_DIR / f"tray-{state}.png"
        draw_tray(state).save(path)
        print(f"написал {path}")


if __name__ == "__main__":
    main()
