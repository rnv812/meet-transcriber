"""Единый источник путей meet: записи, лексика, база голосов, модели, логи.

Два режима, различаются автоматически по наличию корня репозитория:

* **dev** — meet запущен из исходников (рядом с пакетом лежит `pyproject.toml`).
  Записи, `hotwords.txt`, `glossary.txt` и `voices/` берутся из корня
  репозитория — ровно там, где они лежали до этого модуля. Записи и база голосов
  тех, кто запускал meet из исходников, не переезжают никуда.
* **installed** — meet запущен из установленного приложения (пакет в
  site-packages, корня репозитория рядом нет). Тогда всё это лежит в
  `%LOCALAPPDATA%/meet`, потому что у установленного приложения нет и не может
  быть «корня проекта», а `Path("hotwords.txt")` от рабочей папки означал бы
  разные файлы при запуске из разных мест.

Машинно-локальное состояние (`config.json`, `tray.lock`, `command`, `gpu.lock`,
`watch.log`) в обоих режимах лежит в `%LOCALAPPDATA%/meet` — так было и до этого
модуля, менять незачем: путь `gpu.lock` известен ещё и внешним программам.

IMPORTANT: ни одна функция не кэширует результат. `LOCALAPPDATA` и
`MEET_DATA_DIR` подменяются в тестах monkeypatch'ем, а кэш сделал бы подмену
невидимой — по той же причине, по которой `tray._state_dir()` никогда не был
константой.
"""

import os
from pathlib import Path

from meet import plat

APP_DIR_NAME = plat.APP_DIR_NAME


def _env_path(name: str) -> Path | None:
    """Путь из переменной среды; пустая строка — как будто переменной нет."""
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else None


def repo_root() -> Path | None:
    """Корень репозитория, если meet запущен из исходников, иначе None.

    Опознаём по `pyproject.toml` на два уровня выше пакета (`src/meet/paths.py`
    → корень). Проверка именно файла, а не папки: в site-packages пакет лежит
    без него, и режим честно определяется как installed.
    """
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def is_dev() -> bool:
    """Запущены из репозитория (пути исторические), а не из приложения."""
    return repo_root() is not None


def data_dir() -> Path:
    """Машинно-локальное состояние: `%LOCALAPPDATA%/meet` (на macOS —
    `~/Library/Application Support/meet`, см. `plat.data_root`).

    `MEET_DATA_DIR` — override для тестов и портативного режима. Фоллбэк на "."
    сохранён от `tray._state_dir()`: под pythonw в автозагрузке переменной среды
    может не оказаться, и падать из-за этого резидент не должен.
    """
    override = _env_path("MEET_DATA_DIR")
    if override is not None:
        return override
    return plat.data_root() / APP_DIR_NAME


def config_path() -> Path:
    """Файл настроек — тот же `config.json`, что правился руками до схемы."""
    return data_dir() / "config.json"


def lexicon_dir() -> Path:
    """Где лежат `hotwords.txt` и `glossary.txt`: корень репо или data_dir."""
    return repo_root() or data_dir()


def hotwords_path() -> Path:
    return lexicon_dir() / "hotwords.txt"


def glossary_path() -> Path:
    return lexicon_dir() / "glossary.txt"


def default_voices_dir() -> Path:
    """База голосов. Значение по умолчанию: настройки могут его переопределить."""
    root = repo_root()
    return (root / "voices") if root else (data_dir() / "voices")


def default_recordings_dir() -> Path:
    """Куда писать записи по умолчанию.

    В installed-режиме это `%LOCALAPPDATA%/meet/recordings`, а не «Документы»:
    часовая запись весит десятки мегабайт, а Документы у многих синхронизируются
    OneDrive. Явный выбор папки — за пользователем через настройки.
    """
    root = repo_root()
    return (root / "recordings") if root else (data_dir() / "recordings")


# Где лежат движок и модели (0.3.3): выбранная человеком папка — например,
# другой диск, когда на C: тесно. Файл пишет оболочка (её перенос, `storage.rs`):
# она же должна знать, где движок, ещё до запуска резидента. Нет файла — всё
# там же, где было до этой настройки.
STORAGE_FILE = "storage.json"


def storage_file() -> Path:
    return data_dir() / STORAGE_FILE


# Версия формата storage.json. Неизвестная — «файл не прочитан», а не «по
# умолчанию»: новая версия приложения могла записать то, чего мы не понимаем.
STORAGE_VERSION = 1


def _read_storage() -> tuple[str, Path | None]:
    """("absent" | "ok" | "bad", папка). "bad" — файл есть, но не прочитан:
    оборванная запись, чужой формат, пустой или относительный путь."""
    import json

    try:
        text = storage_file().read_text(encoding="utf-8")
    except FileNotFoundError:
        return "absent", None
    except (OSError, UnicodeError):
        return "bad", None
    try:
        raw = json.loads(text)
    except ValueError:
        return "bad", None
    if not isinstance(raw, dict) or raw.get("version", STORAGE_VERSION) != STORAGE_VERSION:
        return "bad", None
    value = raw.get("root")
    if not isinstance(value, str) or not value.strip():
        return "bad", None
    root = Path(value.strip())
    # Относительный путь значил бы «от рабочей папки» — разное место у
    # разных процессов.
    return ("ok", root) if root.is_absolute() else ("bad", None)


def storage_root() -> Path | None:
    """Выбранная папка движка и моделей или None (по умолчанию: data_dir и
    общий кэш Hugging Face; и когда файл выбора не прочитан — см.
    `storage_unreadable`)."""
    return _read_storage()[1]


def storage_unreadable() -> bool:
    """storage.json есть, но не прочитан. Это не «по умолчанию»: движок там
    уже удалён переносом, и молча ставить его заново на системный диск
    нельзя — решает человек (окно: «Указать папку» / «Вернуть на системный
    диск»)."""
    return _read_storage()[0] == "bad"


def storage_home() -> Path:
    """Папка, в которой лежат `engine/` и `models/`."""
    return storage_root() or data_dir()


def storage_missing() -> Path | None:
    """Выбранная папка, которой сейчас нет (внешний диск отключён), или None.
    Её не создаём: на macOS это была бы папка в /Volumes на системном диске,
    и туда молча поехали бы гигабайты. Файл выбора не прочитан — сам файл:
    куда класть модели, неизвестно."""
    state, root = _read_storage()
    if state == "bad":
        return storage_file()
    return root if root is not None and not root.is_dir() else None


def models_dir() -> Path:
    """Модели, которые качает приложение: GigaAM, а при выбранной папке — и
    свой кэш Hugging Face (`models/hf`, см. `models.cache_root`). Без выбора
    faster-whisper и pyannote живут в общем кэше HF, как раньше: уже скачанные
    гигабайты не качаются заново."""
    return storage_home() / "models"


def logs_dir() -> Path:
    return data_dir() / "logs"


def engine_dir() -> Path:
    """Приватный venv движка, который тонкий инсталлятор ставит при первом
    запуске: `<папка движка и моделей>/engine/<версия>`."""
    return storage_home() / "engine"
