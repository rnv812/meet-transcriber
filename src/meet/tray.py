"""Кнопка записи в трее: ярлык запускает запись сразу, красная иконка видна
пока идёт запись, тултип показывает длительность, правый клик ->
«Остановить запись» (чистая финализация) -> уведомление с папкой -> выход.
Без консольного окна: точка входа meet-tray в [project.gui-scripts].
См. спеку docs/superpowers/specs/2026-07-02-tray-record-design.md."""

import threading
import time
from pathlib import Path

from meet.output import fmt_ts
from meet.recorder import record

# recordings от корня репозитория: ярлык запускается с произвольным cwd
OUT_ROOT = Path(__file__).resolve().parents[2] / "recordings"


def _icon_image():
    """Красный кружок 64x64 — рисуем на лету, без файлов-ресурсов."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=(220, 40, 40, 255))
    return img


def main() -> None:
    import pystray

    stop_event = threading.Event()
    result: dict = {}  # folder | error — из потока записи

    def run_record() -> None:
        try:
            result["folder"] = record(str(OUT_ROOT), stop_event=stop_event)
        except BaseException as e:  # и SystemExit «запись уже идёт»
            result["error"] = str(e) or repr(e)

    thread = threading.Thread(target=run_record, daemon=True)

    def stop_action(icon, item) -> None:
        icon.title = "Останавливаю..."
        stop_event.set()
        thread.join(timeout=60)
        if "folder" in result:
            icon.notify(f"Сохранено: {result['folder']}", "Запись встречи")
            time.sleep(3)  # дать уведомлению показаться до выхода процесса
        icon.stop()

    def ticker(icon) -> None:
        """Фон pystray: старт записи и тултип-секундомер."""
        icon.visible = True
        thread.start()
        started = time.monotonic()
        while not stop_event.is_set():
            if "error" in result:  # запись не стартовала или упала
                icon.notify(result["error"], "Запись встречи")
                time.sleep(3)
                icon.stop()
                return
            icon.title = f"Запись: {fmt_ts(time.monotonic() - started)}"
            time.sleep(1)

    icon = pystray.Icon(
        "meet-record",
        _icon_image(),
        "Запись: 00:00",
        menu=pystray.Menu(pystray.MenuItem("Остановить запись", stop_action)),
    )
    icon.run(setup=ticker)
