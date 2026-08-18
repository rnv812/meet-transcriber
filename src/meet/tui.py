from pathlib import Path

from meet import settings

_MENU = """
1. Записать встречу (Ctrl+C — стоп)
2. Транскрибировать последнюю запись
3. Транскрибировать файл или папку
0. Выход
"""


def latest_recording(root: Path) -> Path | None:
    """Самая свежая папка записи с обеими дорожками.
    Имена вида 2026-06-12_10-59 сортируются как даты."""
    from meet.transcribe import _find_track

    if not root.is_dir():
        return None
    complete = [
        d
        for d in root.iterdir()
        if d.is_dir() and _find_track(d, "sys") and _find_track(d, "mic")
    ]
    return max(complete, key=lambda d: d.name, default=None)


def _ask_speakers() -> int | None:
    raw = input(
        "Сколько говорящих? (для папок записи — не считая вас; Enter — авто): "
    ).strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _transcribe(path: Path) -> None:
    from meet.transcribe import transcribe

    try:
        transcribe(str(path), speakers=_ask_speakers())
    except SystemExit as e:  # не закрывать меню из-за ошибки одной расшифровки
        print(e)


def main() -> None:
    print("meet — локальный транскрибатор встреч")
    try:
        while True:
            print(_MENU)
            choice = input("Выбор: ").strip()
            if choice == "1":
                from meet.recorder import record

                folder = record(str(settings.load().recording.recordings))
                answer = input("Транскрибировать сейчас? (д/н): ").strip().lower()
                if answer in ("д", "да", "y", "yes"):
                    _transcribe(folder)
            elif choice == "2":
                folder = latest_recording(settings.load().recording.recordings)
                if folder is None:
                    print("Записей пока нет.")
                else:
                    print(f"Последняя запись: {folder}")
                    _transcribe(folder)
            elif choice == "3":
                raw = input("Путь: ").strip().strip('"')
                if raw:
                    _transcribe(Path(raw))
            elif choice == "0":
                return
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == "__main__":
    main()
