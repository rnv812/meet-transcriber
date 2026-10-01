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
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from meet import paths
from meet.asr import CPU_MODEL_NAME as DEFAULT_CPU_WHISPER_MODEL
from meet.asr import DEVICES as ASR_DEVICES
from meet.asr import MODEL_NAME as DEFAULT_WHISPER_MODEL

SCHEMA_VERSION = 2

# Дефолты автозаписи продублированы здесь, а не взяты из meet.watch: watch.py
# импортирует winreg, то есть существует только на Windows, а настройки должны
# читаться где угодно (тесты, будущий mac/Linux). За расхождением следит
# test_settings.py — он сверяет эти числа с константами watch.
DEFAULT_GRACE_S = 180.0
DEFAULT_POLL_S = 2.0
# Короче этого звонок считается ложной тревогой (звук уведомления, отклонённый
# вызов): папка остаётся, но пост-хук на неё не зовут.
DEFAULT_MIN_CALL_S = 120.0

# Клиенты конференций, за которыми детектор следит по умолчанию.
#
# IMPORTANT: только те приложения, у которых активная звуковая сессия почти
# всегда означает разговор. Мессенджеры (Slack, Discord, Telegram) сюда
# сознательно не входят: они постоянно проигрывают звуки уведомлений, а признак
# «что-то воспроизводится» читается детектором как звонок — автозапись включалась
# бы на каждый бип. Добавить их можно руками, зная эту цену.
DEFAULT_PROCESSES = (
    "Dion.exe",
    "Teams.exe",
    "ms-teams.exe",
    "Zoom.exe",
    "Webex.exe",
)

# Текст, который пост-хук передаёт запускаемой команде. Плейсхолдеры: {folder},
# {project}, {date}, {prompt} — см. Hooks.
DEFAULT_HOOK_PROMPT = (
    "Завершилась запись встречи, папка: {folder}. Предложи её транскрибировать."
)

# Как выглядел пост-хук до того, как стал настройкой: окно Windows Terminal с
# Claude Code. Нужно для миграции — у кого он был включён, у того и останется.
HISTORIC_HOOK_COMMAND = (
    "wt",
    "-d",
    "{project}",
    "powershell",
    "-NoExit",
    "-Command",
    "claude --permission-mode auto '{prompt}'",
)
HISTORIC_HOOK_PROMPT = (
    "Завершилась запись встречи, папка: {folder}. "
    "Предложи транскрибировать её скиллом my-plugin:transcriber."
)
HISTORIC_RECURRING_PROMPT = (
    " Время похоже на слот регулярной встречи — сверься с календарём "
    "(scripts/calendar_lookup.ps1) и, если это она, веди её соответствующим "
    "режимом скилла; другую встречу в этом слоте — обычным режимом."
)
HISTORIC_RECURRING_WINDOW = ("11:00", "12:00")

ASR_BACKENDS = ("faster-whisper", "whisper.cpp")
LLM_PROVIDERS = ("auto", "claude-code", "codex", "openai-compatible")
# Провайдер для конфига без явного выбора у уже работавшего пользователя: до
# появления "auto" ассистент ходил через Claude Code, и это не должно меняться.
LEGACY_LLM_PROVIDER = "claude-code"
# Подпапка заметок по умолчанию (для нового пользователя).
DEFAULT_NOTES_SUBDIR = "Встречи"
# Локальная модель по умолчанию адресуется как OpenAI-совместимый эндпоинт:
# так работают и LM Studio (1234), и Ollama (11434) — своего рантайма не нужно.
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:1234/v1"
# Уровни уведомлений оболочки: всё; только важное (автозапись началась, ошибка
# расшифровки, сервис записи не запускается); ничего.
NOTIFICATION_LEVELS = ("all", "important", "off")


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
    min_call_seconds: float = DEFAULT_MIN_CALL_S

    @classmethod
    def from_raw(cls, raw: dict) -> "AutoRecord":
        return cls(
            enabled=as_flag(raw.get("enabled"), False),
            processes=as_str_list(raw.get("processes"), DEFAULT_PROCESSES),
            grace_seconds=as_positive(raw.get("grace_seconds"), DEFAULT_GRACE_S, 0.0),
            poll_seconds=as_positive(raw.get("poll_seconds"), DEFAULT_POLL_S, 0.5),
            min_call_seconds=as_positive(
                raw.get("min_call_seconds"), DEFAULT_MIN_CALL_S, 0.0
            ),
        )

    def to_raw(self) -> dict:
        return {
            "enabled": self.enabled,
            "processes": list(self.processes),
            "grace_seconds": self.grace_seconds,
            "poll_seconds": self.poll_seconds,
            "min_call_seconds": self.min_call_seconds,
        }


@dataclass(frozen=True)
class Hooks:
    """Что запускать после остановки записи.

    Раньше здесь был зашит конкретный сценарий: окно Windows Terminal с Claude
    Code и промптом про конкретный скилл. Теперь это шаблон команды —
    `["explorer", "{folder}"]` работает ровно так же, как запуск ассистента, а у
    нового пользователя по умолчанию не запускается ничего.

    Плейсхолдеры подставляются в каждый аргумент по отдельности, без шелла:
    `{folder}` — папка записи, `{project}` — корень репозитория (или папка
    записей), `{date}` — дата из имени папки, `{prompt}` — текст-подсказка.
    """

    post_record: bool = False
    command: tuple[str, ...] = ()
    prompt: str = DEFAULT_HOOK_PROMPT
    # Окно старта, в котором встреча похожа на регулярную (дейлик, статус).
    # None — про регулярность ничего не говорим.
    recurring_window: tuple[str, str] | None = None
    recurring_prompt: str = ""

    @classmethod
    def from_raw(cls, raw: dict) -> "Hooks":
        window = raw.get("recurring_window")
        recurring: tuple[str, str] | None = None
        if (
            isinstance(window, (list, tuple))
            and len(window) == 2
            and all(isinstance(x, str) and x.strip() for x in window)
        ):
            recurring = (str(window[0]), str(window[1]))
        command = raw.get("command")
        if isinstance(command, str):
            # Строку разбираем как командную строку Windows: так проще писать
            # руками, а список остаётся точным способом задать аргумент с
            # пробелами.
            import shlex

            command = shlex.split(command, posix=False)
        if not isinstance(command, list):
            command = []
        prompt = raw.get("prompt")
        return cls(
            post_record=as_flag(raw.get("post_record"), False),
            command=tuple(str(part) for part in command),
            prompt=str(prompt) if isinstance(prompt, str) and prompt else DEFAULT_HOOK_PROMPT,
            recurring_window=recurring,
            recurring_prompt=str(raw.get("recurring_prompt") or ""),
        )

    def to_raw(self) -> dict:
        return {
            "post_record": self.post_record,
            "command": list(self.command),
            "prompt": self.prompt,
            "recurring_window": list(self.recurring_window)
            if self.recurring_window
            else None,
            "recurring_prompt": self.recurring_prompt,
        }


@dataclass(frozen=True)
class Recording:
    """Куда пишем и где база голосов. None — путь по умолчанию из paths.py:
    настройка хранит только осознанный выбор пользователя, поэтому смена
    режима (dev/installed) не тянет за собой прописанный когда-то путь."""

    out_dir: Path | None = None
    voices_dir: Path | None = None
    # Как подписывать микрофонную дорожку в транскрипте: это всегда владелец
    # машины. «Вы» — обращение к читателю транскрипта, но кому-то удобнее имя.
    speaker_name: str = "Вы"
    # Расшифровывать сразу после записи. Если ключа нет в сыром конфиге —
    # пользователь не решал, и дефолт вычисляет Settings.from_raw.
    auto_transcribe: bool = True

    @classmethod
    def from_raw(cls, raw: dict) -> "Recording":
        name = raw.get("speaker_name")
        return cls(
            out_dir=as_path(raw.get("out_dir")),
            voices_dir=as_path(raw.get("voices_dir")),
            speaker_name=str(name).strip() if name and str(name).strip() else "Вы",
            auto_transcribe=as_flag(raw.get("auto_transcribe"), True),
        )

    def to_raw(self) -> dict:
        return {
            "out_dir": str(self.out_dir) if self.out_dir else None,
            "voices_dir": str(self.voices_dir) if self.voices_dir else None,
            "speaker_name": self.speaker_name,
            "auto_transcribe": self.auto_transcribe,
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
    device: str = "auto"
    cpu_model: str = DEFAULT_CPU_WHISPER_MODEL

    @classmethod
    def from_raw(cls, raw: dict) -> "Asr":
        model = raw.get("model")
        return cls(
            backend=as_choice(raw.get("backend"), ASR_BACKENDS, ASR_BACKENDS[0]),
            model=str(model).strip() if model else DEFAULT_WHISPER_MODEL,
            language=str(raw.get("language") or "ru").strip() or "ru",
            align=as_flag(raw.get("align"), True),
            overlap=as_flag(raw.get("overlap"), True),
            device=as_choice(raw.get("device"), ASR_DEVICES, "auto"),
            cpu_model=str(raw.get("cpu_model") or "").strip() or DEFAULT_CPU_WHISPER_MODEL,
        )

    def to_raw(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "language": self.language,
            "align": self.align,
            "overlap": self.overlap,
            "device": self.device,
            "cpu_model": self.cpu_model,
        }


@dataclass(frozen=True)
class Llm:
    """Кто отвечает на вопросы по встрече и ведёт дайджест.

    `claude-code` — нынешний путь через claude-agent-sdk (нужен установленный
    Claude Code CLI). `openai-compatible` — локальная модель через LM Studio или
    Ollama: своего рантайма не тащим, а инструменты чтения хранилища такой
    провайдер не поддерживает — это учитывает слой assist.
    """

    provider: str = "auto"
    model: str = "sonnet"
    base_url: str = DEFAULT_LOCAL_BASE_URL
    local_model: str | None = None

    @classmethod
    def from_raw(cls, raw: dict, default_provider: str = "auto") -> "Llm":
        local = raw.get("local_model")
        base = raw.get("base_url")
        return cls(
            provider=as_choice(raw.get("provider"), LLM_PROVIDERS, default_provider),
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
class Assistant:
    """Где ассистент берёт знания и куда кладёт заметки о встречах.

    `knowledge_dir` — папка с материалами (читает ассистент), `notes_dir` —
    корень заметок, `notes_subdir` — подпапка внутри него. None — не задано.
    """

    knowledge_dir: Path | None = None
    notes_dir: Path | None = None
    notes_subdir: str = DEFAULT_NOTES_SUBDIR

    @classmethod
    def from_raw(cls, raw: dict, vault: Path | None = None) -> "Assistant":
        """`vault` — прежний `assist.vault`: им заполняются папки, которых в
        секции нет вовсе. Ключ, явно записанный как null, — осознанный выбор
        «не задано» и из vault не воскрешается."""
        knowledge = as_path(raw["knowledge_dir"]) if "knowledge_dir" in raw else vault
        notes = as_path(raw["notes_dir"]) if "notes_dir" in raw else vault
        if "notes_subdir" in raw:
            subdir = str(raw["notes_subdir"] or "").strip()
        else:
            # Раскладка по задачам внутри vault остаётся за прежним владельцем.
            subdir = "" if "notes_dir" not in raw and vault else DEFAULT_NOTES_SUBDIR
        return cls(knowledge_dir=knowledge, notes_dir=notes, notes_subdir=subdir)

    def to_raw(self) -> dict:
        return {
            "knowledge_dir": str(self.knowledge_dir) if self.knowledge_dir else None,
            "notes_dir": str(self.notes_dir) if self.notes_dir else None,
            "notes_subdir": self.notes_subdir,
        }


@dataclass(frozen=True)
class Integrations:
    """Связи с чужими программами. Все выключаемые: приложение обязано быть
    полезным само по себе."""

    # Маркер «GPU занят» для внешнего наблюдателя (у автора — voice-control,
    # который по нему выгружает свою копию Whisper из видеопамяти). Кому это не
    # нужно — выключает, и файл не создаётся вовсе.
    gpu_marker: bool = True
    gpu_marker_path: Path | None = None
    # Токен Hugging Face: нужен только гейтед-модели диаризации. Хранится в
    # config.json рядом с настройками — не идеал, но честнее прежнего: раньше
    # он жил переменной среды пользователя, которая тоже лежит открытым
    # текстом. Переменная продолжает работать и имеет приоритет, если поле
    # пустое (см. models.token).
    hf_token: str = ""

    @classmethod
    def from_raw(cls, raw: dict) -> "Integrations":
        token = raw.get("hf_token")
        return cls(
            gpu_marker=as_flag(raw.get("gpu_marker"), True),
            gpu_marker_path=as_path(raw.get("gpu_marker_path")),
            hf_token=str(token).strip() if token else "",
        )

    def to_raw(self) -> dict:
        return {
            "gpu_marker": self.gpu_marker,
            "gpu_marker_path": str(self.gpu_marker_path)
            if self.gpu_marker_path
            else None,
            "hf_token": self.hf_token,
        }


@dataclass(frozen=True)
class Ui:
    """Настройки самого приложения, а не записи: их читает оболочка (уровень
    уведомлений трея) и окно (пройден ли мастер первого запуска)."""

    notifications: str = NOTIFICATION_LEVELS[0]
    wizard_done: bool = False

    @classmethod
    def from_raw(cls, raw: dict) -> "Ui":
        return cls(
            notifications=as_choice(
                raw.get("notifications"), NOTIFICATION_LEVELS, NOTIFICATION_LEVELS[0]
            ),
            wizard_done=as_flag(raw.get("wizard_done"), False),
        )

    def to_raw(self) -> dict:
        return {
            "notifications": self.notifications,
            "wizard_done": self.wizard_done,
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
    assistant: Assistant = field(default_factory=Assistant)
    integrations: Integrations = field(default_factory=Integrations)
    ui: Ui = field(default_factory=Ui)

    @classmethod
    def from_raw(cls, raw: dict) -> "Settings":
        # Конфиг «новый», если это первый запуск: нет `version` и нет ни одной
        # секции, которую писал прежний код (llm/assist/hooks/auto_record, а
        # также top-level post_record_hook). Такому достаётся провайдер "auto".
        # Любой другой — существующий пользователь (v2 или старше): без явного
        # провайдера у него остаётся "claude-code", как работало раньше.
        is_new = not isinstance(raw, dict) or (
            "version" not in raw
            and not any(
                key in raw
                for key in ("llm", "assist", "hooks", "auto_record", "post_record_hook")
            )
        )
        raw = migrate(raw)
        assist = Assist.from_raw(_section(raw, "assist"))
        hooks = Hooks.from_raw(_section(raw, "hooks"))
        recording = Recording.from_raw(_section(raw, "recording"))
        if "auto_transcribe" not in _section(raw, "recording") and hooks.post_record:
            # Хук Claude уже расшифровывает запись — вторая автоматическая
            # расшифровка была бы дублем. Поведение меняет только явный выбор.
            recording = replace(recording, auto_transcribe=False)
        return cls(
            version=SCHEMA_VERSION,
            auto_record=AutoRecord.from_raw(_section(raw, "auto_record")),
            hooks=hooks,
            recording=recording,
            asr=Asr.from_raw(_section(raw, "asr")),
            llm=Llm.from_raw(
                _section(raw, "llm"),
                default_provider="auto" if is_new else LEGACY_LLM_PROVIDER,
            ),
            assist=assist,
            assistant=Assistant.from_raw(_section(raw, "assistant"), vault=assist.vault),
            integrations=Integrations.from_raw(_section(raw, "integrations")),
            ui=Ui.from_raw(_section(raw, "ui")),
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
            "assistant": self.assistant.to_raw(),
            "integrations": self.integrations.to_raw(),
            "ui": self.ui.to_raw(),
        }


def migrate(raw: dict) -> dict:
    """Старый формат → текущая схема, в памяти.

    Главное правило миграции: **у того, кто уже пользуется meet, поведение не
    меняется**. Дефолты новой схемы рассчитаны на человека, который ставит
    приложение впервые (пост-хук ничего не запускает, про регулярные встречи
    ничего не знает), поэтому существующему конфигу мы явным образом
    достраиваем то, что раньше было зашито в коде.

    * v0 — файл без `version`: `post_record_hook` на верхнем уровне, зашитый
      запуск Claude Code в Windows Terminal, окно дейлика 11:00–12:00.
    * v1 — первая схема: то же, но флаг уже в `hooks.post_record`, а окно
      называлось `daily_window`.

    Старые ключи из словаря не выбрасываем: `save()` их сохранит, и файл
    останется понятным предыдущей версии кода.
    """
    if not isinstance(raw, dict):
        return {}
    version = raw.get("version")
    if version == SCHEMA_VERSION:
        return raw
    migrated = dict(raw)
    hooks = dict(_section(migrated, "hooks"))
    if "post_record_hook" in raw:  # v0
        hooks.setdefault("post_record", raw["post_record_hook"])
    # Пустой конфиг мигрировать не от чего — это новая установка, и ей
    # достаются новые дефолты, а не чужой сценарий из прошлого.
    if raw:
        hooks.setdefault("command", list(HISTORIC_HOOK_COMMAND))
        hooks.setdefault("prompt", HISTORIC_HOOK_PROMPT)
        hooks.setdefault("recurring_prompt", HISTORIC_RECURRING_PROMPT)
        window = hooks.pop("daily_window", None)  # v1
        hooks.setdefault(
            "recurring_window", list(window) if window else list(HISTORIC_RECURRING_WINDOW)
        )
        migrated["hooks"] = hooks
    return migrated


# Уже сообщённые (файл, причина): резидент читает настройки часто, а строка
# о битом файле нужна одна — до исправления файла или новой причины.
_REPORTED: set = set()


def _report_unreadable(target: Path, reason: str) -> None:
    """Битый config.json — дефолты, но не молча: строка в stderr и, для
    config.json этого data dir, в watch.log (журнал резидента: под pythonw
    stderr не видно)."""
    key = (str(target), reason)
    if key in _REPORTED:
        return
    _REPORTED.add(key)
    line = f"настройки: {target} не читается ({reason}) — работаю с настройками по умолчанию"
    if sys.stderr is not None:
        try:
            print(line, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass
    try:
        if Path(target).resolve() != paths.config_path().resolve():
            return
        from meet import watch

        watch.WatchLog(watch.default_log_path())(line)
    except Exception:
        pass  # журнал не должен мешать работе


def read_raw(path: Path | None = None) -> dict:
    """Сырое содержимое файла настроек. Нет файла — пустой словарь; мусор —
    тоже, плюс одна строка о нём (см. _report_unreadable)."""
    target = Path(path or paths.config_path())
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        _report_unreadable(target, f"{type(e).__name__}: {e}")
        return {}
    if not isinstance(data, dict):
        _report_unreadable(target, "ожидался JSON-объект")
        return {}
    return data


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


# Секции, которые умеет обновлять patch(). Выводятся из самой схемы, а не
# перечислены руками: раньше список отставал (integrations добавили в схему, а
# сюда забыли — и токен HF молча не сохранялся). `version` — не секция.
PATCHABLE_SECTIONS = tuple(
    name for name in Settings.__dataclass_fields__ if name != "version"
)


def patch(updates: dict, path: Path | None = None) -> Settings:
    """Точка для UI настроек: частичное обновление по секциям.

    `{"auto_record": {"enabled": True}}` меняет один флаг, не затрагивая
    остальные поля секции. Неизвестные секции игнорируются — валидацию имён
    делает вызывающий (API), а не файл.
    """
    current = load(path)
    changed = {}
    for name in PATCHABLE_SECTIONS:
        section_update = updates.get(name)
        if not isinstance(section_update, dict):
            continue
        merged = getattr(current, name).to_raw()
        merged.update(section_update)
        changed[name] = type(getattr(current, name)).from_raw(merged)
    updated = replace(current, **changed) if changed else current
    save(updated, path)
    return updated
