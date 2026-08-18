"""Настройки meet: одна схема, один файл, миграции.

Файл — тот же `%LOCALAPPDATA%/meet/config.json`, который до этого модуля правился
блокнотом. Что изменилось: у него появилась схема с версией, значения приводятся
к типам в одном месте (а не по месту чтения), и появился путь для UI настроек —
`patch()`.

Три правила, из которых сделан этот модуль:

* **Загрузка никогда не пишет на диск.** Резидентный трей читает файл на ходу, а
  ручные правки не должны исчезать из-за того, что их кто-то прочитал. Миграция
  старого формата происходит в памяти.
* **Запись сохраняет неизвестные ключи.** В файле может лежать то, чего этот код
  ещё не знает (правка руками, настройка из будущей версии) — `save()` сливает
  свои значения в прочитанный словарь, а не заменяет его.
* **Мусор в файле не роняет процесс.** Трей запускается из автозагрузки под
  pythonw: исключение здесь означало бы резидента без иконки и без журнала.
  Поэтому любое непонятное значение молча заменяется дефолтом.

Старый формат (без `version`, с `post_record_hook` и `auto_record` на верхнем
уровне) читается как v0 и мигрируется на лету.
"""

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from meet import paths
from meet.asr import MODEL_NAME as DEFAULT_WHISPER_MODEL

SCHEMA_VERSION = 1

# Дефолты автозаписи продублированы здесь, а не взяты из meet.watch: watch.py
# импортирует winreg, то есть существует только на Windows, а настройки должны
# читаться где угодно (тесты, будущий mac/Linux). За расхождением следит
# test_settings.py — он сверяет эти числа с константами watch.
DEFAULT_GRACE_S = 180.0
DEFAULT_POLL_S = 2.0
DEFAULT_PROCESSES = ("Dion.exe",)
# Окно старта записи, в котором встреча похожа на дейлик (см. tray.DAILY_WINDOW).
DEFAULT_DAILY_WINDOW = ("11:00", "12:00")

ASR_BACKENDS = ("faster-whisper", "whisper.cpp")
LLM_PROVIDERS = ("claude-code", "openai-compatible")
# Локальная модель по умолчанию адресуется как OpenAI-совместимый эндпоинт:
# так работают и LM Studio (1234), и Ollama (11434) — своего рантайма не нужно.
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:1234/v1"


def as_flag(value, default: bool) -> bool:
    """Булево из конфига, правленного руками: строковое "false" не должно
    означать True только потому, что непустая строка истинна."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    if value is None:
        return default
    return bool(value)


def as_positive(value, default: float, minimum: float) -> float:
    """Число из конфига. Мусор и значения вне смысла — молча к дефолту."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number < minimum:  # NaN тоже сюда
        return default
    return number


def as_int(value, default: int, minimum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= minimum else default


def as_str_list(value, default: tuple[str, ...]) -> list[str]:
    """Список строк; одна строка — список из неё (частая правка руками)."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        return list(default)
    return [str(v) for v in value]


def as_choice(value, allowed: tuple[str, ...], default: str) -> str:
    """Значение из закрытого списка: опечатка не должна ломать запуск."""
    if isinstance(value, str) and value.strip() in allowed:
        return value.strip()
    return default


def as_path(value) -> Path | None:
    """Необязательный путь: пустая строка и мусор — как будто не задан."""
    if value is None:
        return None
    text = str(value).strip()
    return Path(text) if text else None


def _section(raw: dict, name: str) -> dict:
    section = raw.get(name)
    return section if isinstance(section, dict) else {}


@dataclass(frozen=True)
class AutoRecord:
    """Автозапись звонков. Секции нет — выключена, но детектор всё равно
    опрашивается и пишет журнал (так можно неделю смотреть на сигналы,
    ничего не записывая)."""

    enabled: bool = False
    processes: list[str] = field(default_factory=lambda: list(DEFAULT_PROCESSES))
    grace_seconds: float = DEFAULT_GRACE_S
    poll_seconds: float = DEFAULT_POLL_S

    @classmethod
    def from_raw(cls, raw: dict) -> "AutoRecord":
        return cls(
            enabled=as_flag(raw.get("enabled"), False),
            processes=as_str_list(raw.get("processes"), DEFAULT_PROCESSES),
            grace_seconds=as_positive(raw.get("grace_seconds"), DEFAULT_GRACE_S, 0.0),
            poll_seconds=as_positive(raw.get("poll_seconds"), DEFAULT_POLL_S, 0.5),
        )

    def to_raw(self) -> dict:
        return {
            "enabled": self.enabled,
            "processes": list(self.processes),
            "grace_seconds": self.grace_seconds,
            "poll_seconds": self.poll_seconds,
        }


@dataclass(frozen=True)
class Hooks:
    """Что происходит после остановки записи."""

    post_record: bool = False
    daily_window: tuple[str, str] = DEFAULT_DAILY_WINDOW

    @classmethod
    def from_raw(cls, raw: dict) -> "Hooks":
        window = raw.get("daily_window")
        if (
            isinstance(window, (list, tuple))
            and len(window) == 2
            and all(isinstance(x, str) for x in window)
        ):
            daily = (window[0], window[1])
        else:
            daily = DEFAULT_DAILY_WINDOW
        return cls(
            post_record=as_flag(raw.get("post_record"), False),
            daily_window=daily,
        )

    def to_raw(self) -> dict:
        return {
            "post_record": self.post_record,
            "daily_window": list(self.daily_window),
        }


@dataclass(frozen=True)
class Recording:
    """Куда пишем и где база голосов. None — путь по умолчанию из paths.py:
    настройка хранит только осознанный выбор пользователя, поэтому смена
    режима (dev/installed) не тянет за собой прописанный когда-то путь."""

    out_dir: Path | None = None
    voices_dir: Path | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> "Recording":
        return cls(
            out_dir=as_path(raw.get("out_dir")),
            voices_dir=as_path(raw.get("voices_dir")),
        )

    def to_raw(self) -> dict:
        return {
            "out_dir": str(self.out_dir) if self.out_dir else None,
            "voices_dir": str(self.voices_dir) if self.voices_dir else None,
        }

    @property
    def recordings(self) -> Path:
        return self.out_dir or paths.default_recordings_dir()

    @property
    def voices(self) -> Path:
        return self.voices_dir or paths.default_voices_dir()


@dataclass(frozen=True)
class Asr:
    """Распознавание: сменный бэкенд и модель.

    `faster-whisper` — эталонный путь на CUDA (русский fine-tune large-v3).
    `whisper.cpp` — путь для машин без CUDA: по замерам NPU-плана iGPU через
    Vulkan даёт RTF ~0.08 на ASR, но качество зависит от ggml-модели, поэтому
    модель — тоже настройка, а не константа.
    """

    backend: str = ASR_BACKENDS[0]
    model: str = DEFAULT_WHISPER_MODEL
    language: str = "ru"
    align: bool = True
    overlap: bool = True

    @classmethod
    def from_raw(cls, raw: dict) -> "Asr":
        model = raw.get("model")
        return cls(
            backend=as_choice(raw.get("backend"), ASR_BACKENDS, ASR_BACKENDS[0]),
            model=str(model).strip() if model else DEFAULT_WHISPER_MODEL,
            language=str(raw.get("language") or "ru").strip() or "ru",
            align=as_flag(raw.get("align"), True),
            overlap=as_flag(raw.get("overlap"), True),
        )

    def to_raw(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "language": self.language,
            "align": self.align,
            "overlap": self.overlap,
        }


@dataclass(frozen=True)
class Llm:
    """Кто отвечает на вопросы по встрече и ведёт дайджест.

    `claude-code` — нынешний путь через claude-agent-sdk (нужен установленный
    Claude Code CLI). `openai-compatible` — локальная модель через LM Studio или
    Ollama: своего рантайма не тащим, а инструменты чтения хранилища такой
    провайдер не поддерживает — это учитывает слой assist.
    """

    provider: str = LLM_PROVIDERS[0]
    model: str = "sonnet"
    base_url: str = DEFAULT_LOCAL_BASE_URL
    local_model: str | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> "Llm":
        local = raw.get("local_model")
        base = raw.get("base_url")
        return cls(
            provider=as_choice(raw.get("provider"), LLM_PROVIDERS, LLM_PROVIDERS[0]),
            model=str(raw.get("model") or "sonnet").strip() or "sonnet",
            base_url=str(base).strip() if base else DEFAULT_LOCAL_BASE_URL,
            local_model=str(local).strip() if local else None,
        )

    def to_raw(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "local_model": self.local_model,
        }


@dataclass(frozen=True)
class Assist:
    """Живой ассистент. `vault` наследуется из переменной среды MEET_VAULT,
    если в файле его нет: так продолжают работать нынешние запуски."""

    vault: Path | None = None
    window_seconds: float = 20.0
    port: int = 8765
    voices: bool = True

    @classmethod
    def from_raw(cls, raw: dict) -> "Assist":
        vault = as_path(raw.get("vault"))
        if vault is None:
            vault = as_path(os.environ.get("MEET_VAULT"))
        return cls(
            vault=vault,
            window_seconds=as_positive(raw.get("window_seconds"), 20.0, 1.0),
            port=as_int(raw.get("port"), 8765, 1024),
            voices=as_flag(raw.get("voices"), True),
        )

    def to_raw(self) -> dict:
        return {
            "vault": str(self.vault) if self.vault else None,
            "window_seconds": self.window_seconds,
            "port": self.port,
            "voices": self.voices,
        }


@dataclass(frozen=True)
class Settings:
    version: int = SCHEMA_VERSION
    auto_record: AutoRecord = field(default_factory=AutoRecord)
    hooks: Hooks = field(default_factory=Hooks)
    recording: Recording = field(default_factory=Recording)
    asr: Asr = field(default_factory=Asr)
    llm: Llm = field(default_factory=Llm)
    assist: Assist = field(default_factory=Assist)

    @classmethod
    def from_raw(cls, raw: dict) -> "Settings":
        raw = migrate(raw)
        return cls(
            version=SCHEMA_VERSION,
            auto_record=AutoRecord.from_raw(_section(raw, "auto_record")),
            hooks=Hooks.from_raw(_section(raw, "hooks")),
            recording=Recording.from_raw(_section(raw, "recording")),
            asr=Asr.from_raw(_section(raw, "asr")),
            llm=Llm.from_raw(_section(raw, "llm")),
            assist=Assist.from_raw(_section(raw, "assist")),
        )

    def to_raw(self) -> dict:
        return {
            "version": self.version,
            "auto_record": self.auto_record.to_raw(),
            "hooks": self.hooks.to_raw(),
            "recording": self.recording.to_raw(),
            "asr": self.asr.to_raw(),
            "llm": self.llm.to_raw(),
            "assist": self.assist.to_raw(),
        }


def migrate(raw: dict) -> dict:
    """Старый формат → текущая схема, в памяти.

    v0 — файл без `version`: `post_record_hook` лежал на верхнем уровне, а
    секция `auto_record` уже имела нынешний вид. Старые ключи оставляем в
    словаре: `save()` их сохранит, а следующие версии смогут доложить миграции,
    не потеряв то, чего не поняли.
    """
    if not isinstance(raw, dict):
        return {}
    if raw.get("version") == SCHEMA_VERSION:
        return raw
    migrated = dict(raw)
    if "post_record_hook" in raw:
        hooks = dict(_section(migrated, "hooks"))
        hooks.setdefault("post_record", raw["post_record_hook"])
        migrated["hooks"] = hooks
    return migrated


def read_raw(path: Path | None = None) -> dict:
    """Сырое содержимое файла настроек. Нет файла или мусор — пустой словарь."""
    target = path or paths.config_path()
    try:
        data = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(path: Path | None = None) -> Settings:
    """Настройки из файла. Файла нет — дефолты. На диск ничего не пишет."""
    return Settings.from_raw(read_raw(path))


def save(settings: Settings, path: Path | None = None) -> None:
    """Записать настройки, сохранив неизвестные ключи из файла.

    Пишем через временный файл и `os.replace`: резидент читает `config.json`
    в любой момент, и он не должен увидеть половину записи.
    """
    target = Path(path or paths.config_path())
    merged = dict(read_raw(target))
    merged.update(settings.to_raw())
    # post_record_hook больше не читается (его место — hooks.post_record), но и
    # не удаляется: файл остаётся понятным старой версии кода.
    if "post_record_hook" in merged:
        merged["post_record_hook"] = settings.hooks.post_record
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, target)


def patch(updates: dict, path: Path | None = None) -> Settings:
    """Точка для UI настроек: частичное обновление по секциям.

    `{"auto_record": {"enabled": True}}` меняет один флаг, не затрагивая
    остальные поля секции. Неизвестные секции игнорируются — валидацию имён
    делает вызывающий (API), а не файл.
    """
    current = load(path)
    changed = {}
    for name in ("auto_record", "hooks", "recording", "asr", "llm", "assist"):
        section_update = updates.get(name)
        if not isinstance(section_update, dict):
            continue
        merged = getattr(current, name).to_raw()
        merged.update(section_update)
        changed[name] = type(getattr(current, name)).from_raw(merged)
    updated = replace(current, **changed) if changed else current
    save(updated, path)
    return updated
