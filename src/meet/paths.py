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


def models_dir() -> Path:
    """Модели, которые качает приложение (кэш HuggingFace тут не при чём:
    faster-whisper и pyannote продолжают жить в своём кэше, чтобы уже
    скачанные гигабайты не качались заново)."""
    return data_dir() / "models"


def logs_dir() -> Path:
    return data_dir() / "logs"


def engine_dir() -> Path:
    """Приватный venv движка, который тонкий инсталлятор ставит при первом
    запуске: `<data_dir>/engine/<версия>`."""
    return data_dir() / "engine"
