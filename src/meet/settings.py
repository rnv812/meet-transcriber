"""Настройки meet: одна схема, один файл, миграции.

Файл — тот же `%LOCALAPPDATA%/meet/config.json`, который до этого модуля правился
блокнотом. Что изменилось: у него появилась схема с версией, значения приводятся
к типам в одном месте (а не по месту чтения), и появился путь для UI настроек —
`patch()`.

Три правила, из которых сделан этот модуль:

* **Загрузка никогда не пишет на диск.** Резидентный трей читает файл на ходу, а
  ручные правки не должны исчезать из-за того, что их кто-то прочитал. Миграция
  старого формата происходит в памяти. Единственное исключение — токен HF:
  найденный в файле, он переносится в диспетчер учётных данных и стирается.
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
import re
import sys
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from meet import paths
from meet.asr import CPU_MODEL_NAME as DEFAULT_CPU_WHISPER_MODEL
from meet.asr import DEVICES as ASR_DEVICES
from meet.asr import MODEL_NAME as DEFAULT_WHISPER_MODEL
from meet.gigaam_asr import MODEL_NAME as DEFAULT_GIGAAM_MODEL
from meet.gigaam_asr import MODELS as GIGAAM_MODELS

# Порог узнавания голоса по умолчанию — тот же, что meet.voices.THRESHOLD
# (там калибровка); здесь копия, чтобы настройки не тянули numpy.
VOICE_THRESHOLD = 0.75
VOICE_THRESHOLD_RANGE = (0.5, 0.95)

SCHEMA_VERSION = 2

# Дефолты автозаписи продублированы здесь, а не взяты из meet.watch: watch.py
# импортирует winreg, то есть существует только на Windows, а настройки должны
# читаться где угодно (тесты, будущий mac/Linux). За расхождением следит
# test_settings.py — он сверяет эти числа с константами watch.
# Сколько ждать повторного подключения, прежде чем остановить автозапись
# (`auto_record.grace_minutes`, 1–60 мин). Прежний `grace_seconds` (180 с) не
# читается: 10 минут действуют и для старых конфигов — перезаход в звонок после
# сбоя сети или смены устройства занимает дольше трёх минут.
DEFAULT_GRACE_MIN = 10.0
GRACE_MIN_RANGE = (1.0, 60.0)
DEFAULT_POLL_S = 2.0
# Короче этого звонок считается ложной тревогой (звук уведомления, отклонённый
# вызов): папка остаётся, но пост-хук на неё не зовут.
DEFAULT_MIN_CALL_S = 120.0

# Клиенты конференций, за которыми детектор следит по умолчанию: набор
# распространённых клиентов для нового пользователя (exe — как у пресетов окна,
# app/src/features/settings/CallPrograms.tsx). Сохранённый список пользователя
# эти умолчания не трогают.
#
# IMPORTANT: только те приложения, у которых активная звуковая сессия почти
# всегда означает разговор. Мессенджеры (Slack, Discord, Telegram) сюда
# сознательно не входят: они постоянно проигрывают звуки уведомлений, а признак
# «что-то воспроизводится» читается детектором как звонок — автозапись включалась
# бы на каждый бип. Добавить их можно руками, зная эту цену.
DEFAULT_PROCESSES = (
    "Zoom.exe",
    "ms-teams.exe",
    "Teams.exe",
    "YandexTelemost.exe",
    "Telemost.exe",
    "Dion.exe",
)
# Прежние умолчания — для миграции: старый конфиг без списка программ жил на
# них, у него они и остаются (см. migrate).
HISTORIC_PROCESSES = (
    "Dion.exe",
    "Teams.exe",
    "ms-teams.exe",
    "Zoom.exe",
    "Webex.exe",
)

# Сайты звонков: подстрока заголовка окна браузера (без учёта регистра). По
# ним берётся начальное название записи, а в строгом режиме
# (`browser_require_site`) — решается, звонок ли это вообще. «Meet –» — так
# Google Meet подписывает вкладку («Meet – abc-defg-hij»).
DEFAULT_CALL_SITES = (
    "Google Meet",
    "Meet –",
    "Телемост",
    "Zoom",
    "Microsoft Teams",
    "Jitsi",
    "VK Звонки",
    "Контур.Толк",
    "Dion",
    "Webex",
    "Discord",
    "Яндекс Телемост",
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
    "Завершилась запись встречи, папка: {folder}. Предложи её транскрибировать."
)
HISTORIC_RECURRING_PROMPT = (
    " Время похоже на слот регулярной встречи — сверься с календарём "
    "(scripts/calendar_lookup.ps1) и, если это она, учти это в итогах; "
    "другую встречу в этом слоте веди как обычно."
)
HISTORIC_RECURRING_WINDOW = ("11:00", "12:00")

# Движок распознавания (`asr.backend` — на видеокарте, `asr.cpu_backend` — на
# процессоре). "whisper.cpp" — задел NPU-плана, не реализован: читается как
# Whisper. "whisper" — синоним "faster-whisper" (так короче писать руками).
ASR_BACKENDS = ("faster-whisper", "whisper.cpp", "gigaam")
WHISPER = "faster-whisper"
GIGAAM = "gigaam"
ASR_BACKEND_ALIASES = {"whisper": WHISPER}
LLM_PROVIDERS = ("auto", "claude-code", "codex", "openai-compatible")
# Провайдер для конфига без явного выбора у уже работавшего пользователя: до
# появления "auto" ассистент ходил через Claude Code, и это не должно меняться.
LEGACY_LLM_PROVIDER = "claude-code"
# Подпапка заметок по умолчанию (для нового пользователя).
DEFAULT_NOTES_SUBDIR = "Встречи"
# Выгрузка встреч в базу знаний: папка на встречу и имена файлов в ней.
# Подстановки — {date} {time} {year} {month} {day} {title} (см. meet.kb_export).
DEFAULT_FOLDER_TEMPLATE = "{date} - {title}"
DEFAULT_TRANSCRIPT_NAME = "Транскрипт.md"
DEFAULT_SUMMARY_NAME = "Итоги.md"
# Локальная модель по умолчанию адресуется как OpenAI-совместимый эндпоинт:
# так работают и LM Studio (1234), и Ollama (11434) — своего рантайма не нужно.
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:1234/v1"
# Уровни уведомлений оболочки: всё; только важное (автозапись началась, ошибка
# расшифровки, сервис записи не запускается); ничего.
NOTIFICATION_LEVELS = ("all", "important", "off")
# Живые подсказки (`assist`): активность и уровень модели для тиков.
ASSIST_ACTIVITIES = ("calm", "active", "summary")
HINTS_MODELS = ("agent", "fast")
MAX_HINTS_RANGE = (3, 12)

# Анализ встречи (meet.analysis): что размечать. Выключенное не запрашивается у
# модели (промпт короче) и не показывается в окне.
ANALYSIS_FEATURES = ("types", "importance", "chapters", "insights", "category", "title")
# Кривая важности над плеером: всегда, при наведении на полосу, не показывать.
CURVE_MODES = ("always", "hover", "off")
# Ссылки на задачи Jira в карточке (M3): ключ по умолчанию — «ПРОЕКТ-123».
DEFAULT_JIRA_KEYS = r"[A-Z][A-Z0-9]+-\d+"
JIRA_TEXT_MAX = 200
# https://хост[:порт][/путь] — без логина и пароля, «?» и «#». Тот же разбор у
# окна (app/src/lib/jira.ts) и у оболочки (windows.rs, jira_prefix): open_url
# пускает ссылки только на этот хост.
_JIRA_BASE = re.compile(r"https://(?![.-])[A-Za-z0-9.-]+(?<![.-])(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%/-]*)?")
_JIRA_PROJECTS = re.compile(r"[A-Z][A-Z0-9]+(?:\s*,\s*[A-Z][A-Z0-9]+)*")
# Синтаксис, которого нет в JavaScript или который понимается иначе: флаги, (?P…), комментарии.
_JIRA_NOT_PORTABLE = re.compile(r"\(\?[aiLmsux#P]")


def jira_base_error(value) -> str | None:
    """Адрес Jira из окна: None — годится (пустой — ссылки выключены)."""
    base = str(value or "").strip()
    if not base:
        return None
    if len(base) > JIRA_TEXT_MAX:
        return "Слишком длинный адрес Jira"
    if not base.lower().startswith("https://"):
        return "Адрес Jira должен начинаться с https://"
    if not _JIRA_BASE.fullmatch(base) or ".." in base:
        return "Адрес Jira — вида https://jira.example.com, без логина, пароля, «?» и «#»"
    return None


def clean_jira_base(value) -> str:
    """Адрес Jira для хранения: без «/» в конце, хост — строчными; негодный — пусто."""
    base = str(value or "").strip()
    if not base or jira_base_error(base):
        return ""
    rest = base[len("https://"):].rstrip("/")
    host, slash, path = rest.partition("/")
    return f"https://{host.lower()}{slash}{path}"


def jira_keys_error(value) -> str | None:
    """Шаблон ключей задач: список проектов через запятую или регулярное выражение."""
    keys = str(value or "").strip()
    if len(keys) > JIRA_TEXT_MAX:
        return "Слишком длинный шаблон ключей задач"
    if not keys or _JIRA_PROJECTS.fullmatch(keys):
        return None
    if _JIRA_NOT_PORTABLE.search(keys):
        return "Флаги и именованные группы в шаблоне ключей не поддерживаются"
    try:
        pattern = re.compile(keys)
    except re.error:
        return "Шаблон ключей задач не разобрался: проверьте скобки и экранирование"
    if pattern.fullmatch(""):
        return "Шаблон ключей задач находит пустую строку — уточните его"
    return None

# Категории встреч по умолчанию: id — стабильная латиница (её хранят analysis.json
# и meta.json), имя, цвет и описание — для человека и для модели. Список правит
# человек (окно настроек — M4); анализ берёт отсюда, что предложить.
DEFAULT_CATEGORIES = (
    ("daily", "Дейлик", "#4c8bf5", "Короткая регулярная встреча команды: кто что сделал, что мешает"),
    ("planning", "Планирование", "#2fa36b", "Планирование работ, спринта, сроков и приоритетов"),
    ("discussion", "Обсуждение", "#8e6cd8", "Рабочее обсуждение задачи, решения или проблемы"),
    ("client", "Встреча с клиентом", "#e08a2e",
     "Встреча с заказчиком или партнёром: требования, статус, договорённости"),
    ("presentation", "Презентация", "#d6457a",
     "Демонстрация или доклад: один рассказывает, остальные слушают и спрашивают"),
    ("sales", "Продажа", "#c9a227", "Продажа продукта или услуги: потребности клиента, предложение, условия"),
    ("interview", "Собеседование", "#3aa7b8", "Собеседование кандидата: вопросы об опыте и навыках"),
    ("retro", "Ретроспектива", "#7a8b99", "Ретроспектива: что прошло хорошо, что плохо, что изменить"),
    ("training", "Обучение", "#5a9e3a", "Обучение, разбор материала, передача знаний"),
    ("other", "Другое", "#9aa0a6", "Всё, что не подходит под другие категории"),
)
CATEGORY_ID_MAX = 32
CATEGORY_NAME_MAX = 40
CATEGORY_DESCRIPTION_MAX = 200


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


def as_clamped(value, default: float, low: float, high: float) -> float:
    """Число в пределах [low, high]: выход за край — к краю, мусор — дефолт."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(high, max(low, number))


def as_int(value, default: int, minimum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= minimum else default


def as_ratio(value, default: float, low: float, high: float) -> float:
    """Доля из конфига (порог 0..1): мусор — значение по умолчанию, выход за
    пределы — ближайшая граница."""
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return round(min(high, max(low, number)), 3)


def as_str_list(value, default: tuple[str, ...]) -> list[str]:
    """Список строк; одна строка — список из неё (частая правка руками)."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        return list(default)
    return [str(v) for v in value]


def as_process_list(value) -> list[str]:
    """Программы звонков (имена exe). Пробелы по краям и повторы (без учёта
    регистра — детектор сравнивает имена так же) убираются. Пустой список, как
    и мусор, — список по умолчанию: детектор без программ не видел бы звонков
    вовсе, а выключатель для этого есть отдельный (`enabled`)."""
    return _unique(as_str_list(value, DEFAULT_PROCESSES)) or list(DEFAULT_PROCESSES)


def _unique(values) -> list[str]:
    """Строки без пробелов по краям, пустых и повторов (без учёта регистра)."""
    seen: set[str] = set()
    names: list[str] = []
    for name in values:
        name = str(name).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def as_browser_list(value) -> list[str]:
    """Браузеры для звонков (имена exe). В отличие от программ звонков пустой
    список — норма: по умолчанию браузеры не отслеживаются."""
    if isinstance(value, str):
        value = [value]
    return _unique(value) if isinstance(value, list) else []


def as_site_list(value) -> list[str]:
    """Сайты звонков; пусто или мусор — список по умолчанию."""
    return _unique(as_str_list(value, DEFAULT_CALL_SITES)) or list(DEFAULT_CALL_SITES)


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


def as_device(value) -> str | None:
    """Закреплённое аудио-устройство: `{"name": str}` (или просто строка —
    правка руками). Индексы PortAudio меняются от запуска к запуску, поэтому
    храним только имя. Пусто и мусор — None, то есть «как в системе»."""
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
    grace_minutes: float = DEFAULT_GRACE_MIN
    poll_seconds: float = DEFAULT_POLL_S
    min_call_seconds: float = DEFAULT_MIN_CALL_S
    # Браузеры — отдельно от программ: у них звонок только по микрофону
    # (см. meet.watch). Ключ новый, по умолчанию пусто — старые конфиги как были.
    browsers: list[str] = field(default_factory=list)
    browser_require_site: bool = False
    call_sites: list[str] = field(default_factory=lambda: list(DEFAULT_CALL_SITES))

    @classmethod
    def from_raw(cls, raw: dict) -> "AutoRecord":
        return cls(
            enabled=as_flag(raw.get("enabled"), False),
            processes=as_process_list(raw.get("processes")),
            grace_minutes=as_clamped(raw.get("grace_minutes"), DEFAULT_GRACE_MIN,
                                     *GRACE_MIN_RANGE),
            poll_seconds=as_positive(raw.get("poll_seconds"), DEFAULT_POLL_S, 0.5),
            min_call_seconds=as_positive(
                raw.get("min_call_seconds"), DEFAULT_MIN_CALL_S, 0.0
            ),
            browsers=as_browser_list(raw.get("browsers")),
            browser_require_site=as_flag(raw.get("browser_require_site"), False),
            call_sites=as_site_list(raw.get("call_sites")),
        )

    @property
    def grace_seconds(self) -> float:
        return self.grace_minutes * 60.0

    def to_raw(self) -> dict:
        return {
            "enabled": self.enabled,
            "processes": list(self.processes),
            "grace_minutes": self.grace_minutes,
            "poll_seconds": self.poll_seconds,
            "min_call_seconds": self.min_call_seconds,
            "browsers": list(self.browsers),
            "browser_require_site": self.browser_require_site,
            "call_sites": list(self.call_sites),
        }


@dataclass(frozen=True)
class Hooks:
    """Что запускать после остановки записи.

    Раньше здесь был зашит конкретный сценарий: окно Windows Terminal с Claude
    Code и готовым промптом. Теперь это шаблон команды —
    `["explorer", "{folder}"]` работает ровно так же, как запуск ассистента, а у
    нового пользователя по умолчанию не запускается ничего.

    Плейсхолдеры подставляются в каждый аргумент по отдельности, без шелла:
    `{folder}` — папка записи, `{project}` — корень репозитория (или папка
    записей), `{date}` — дата из имени папки, `{prompt}` — текст-подсказка.
    """

    post_record: bool = False
    command: tuple[str, ...] = ()
    prompt: str = DEFAULT_HOOK_PROMPT
    # Окно старта, в котором встреча похожа на регулярную (ежедневная встреча,
    # статус).
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
    # Прежние значения speaker_name: ими подписаны микрофонные реплики старых
    # записей — по ним старые расшифровки узнают владельца (meet.segvoices).
    former_speaker_names: tuple[str, ...] = ()
    # Расшифровывать сразу после записи. Если ключа нет в сыром конфиге —
    # пользователь не решал, и дефолт вычисляет Settings.from_raw.
    auto_transcribe: bool = True
    # Закреплённые устройства по имени: микрофон и устройство вывода (его звук
    # пишется через WASAPI loopback — это голоса собеседников). None — «как в
    # системе»: запись следует за дефолтными устройствами Windows и переживает
    # их смену. Закреплённого нет — пишем с системного (см. recorder).
    mic_device: str | None = None
    output_device: str | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> "Recording":
        name = raw.get("speaker_name")
        speaker_name = str(name).strip() if name and str(name).strip() else "Вы"
        former = raw.get("former_speaker_names")
        former = [str(x).strip() for x in former if isinstance(x, str) and x.strip()]             if isinstance(former, list) else []
        return cls(
            out_dir=as_path(raw.get("out_dir")),
            voices_dir=as_path(raw.get("voices_dir")),
            speaker_name=speaker_name,
            former_speaker_names=tuple(dict.fromkeys(x for x in former if x != speaker_name)),
            auto_transcribe=as_flag(raw.get("auto_transcribe"), True),
            mic_device=as_device(raw.get("mic_device")),
            output_device=as_device(raw.get("output_device")),
        )

    def to_raw(self) -> dict:
        return {
            "out_dir": str(self.out_dir) if self.out_dir else None,
            "voices_dir": str(self.voices_dir) if self.voices_dir else None,
            "speaker_name": self.speaker_name,
            "former_speaker_names": list(self.former_speaker_names),
            "auto_transcribe": self.auto_transcribe,
            "mic_device": {"name": self.mic_device} if self.mic_device else None,
            "output_device": {"name": self.output_device} if self.output_device else None,
        }

    @property
    def recordings(self) -> Path:
        return self.out_dir or paths.default_recordings_dir()

    @property
    def voices(self) -> Path:
        return self.voices_dir or paths.default_voices_dir()


@dataclass(frozen=True)
class Asr:
    """Распознавание: движок и модель — свои для видеокарты и для процессора.

    Движок (`whisper` или `gigaam`): `backend` — на видеокарте (по умолчанию
    Whisper: русский fine-tune large-v3 лучше на латинских терминах и берёт
    подсказки), `cpu_backend` — на процессоре (по умолчанию GigaAM: распознаёт
    примерно в 10 раз быстрее Whisper medium (весь пайплайн — примерно втрое)
    и точнее на русском). Модели: `model`/`cpu_model` —
    Whisper, `gigaam_model` — GigaAM. `align_after_gigaam` — уточнять время слов
    wav2vec2 и после GigaAM (свои пословные таймкоды у него есть).
    """

    backend: str = WHISPER
    model: str = DEFAULT_WHISPER_MODEL
    language: str = "ru"
    align: bool = True
    overlap: bool = True
    device: str = "auto"
    cpu_model: str = DEFAULT_CPU_WHISPER_MODEL
    cpu_backend: str = GIGAAM
    gigaam_model: str = DEFAULT_GIGAAM_MODEL
    align_after_gigaam: bool = False
    # Порог узнавания голоса по базе (косинусная близость кластера к образцам
    # человека): ниже — честный «Спикер N». Калибровка — meet.voices.THRESHOLD;
    # у встречи может быть свой (панель «Спикеры»).
    voice_threshold: float = VOICE_THRESHOLD
    # Правила замены распознанного для новых расшифровок: ({"from", "to"}, …),
    # «Исправлять так же в будущих встречах» в «Исправить…» (meet.replacements).
    replacements: tuple[dict, ...] = ()

    @classmethod
    def from_raw(cls, raw: dict) -> "Asr":
        from meet.replacements import clean_rules

        model = raw.get("model")
        cpu_model = str(raw.get("cpu_model") or "").strip() or DEFAULT_CPU_WHISPER_MODEL
        return cls(
            backend=_asr_backend(raw.get("backend"), WHISPER),
            model=str(model).strip() if model else DEFAULT_WHISPER_MODEL,
            language=str(raw.get("language") or "ru").strip() or "ru",
            align=as_flag(raw.get("align"), True),
            overlap=as_flag(raw.get("overlap"), True),
            device=as_choice(raw.get("device"), ASR_DEVICES, "auto"),
            cpu_model=cpu_model,
            cpu_backend=_asr_backend(raw.get("cpu_backend"), _legacy_cpu_backend(cpu_model)),
            gigaam_model=as_choice(raw.get("gigaam_model"), GIGAAM_MODELS, DEFAULT_GIGAAM_MODEL),
            align_after_gigaam=as_flag(raw.get("align_after_gigaam"), False),
            voice_threshold=as_ratio(raw.get("voice_threshold"), VOICE_THRESHOLD, *VOICE_THRESHOLD_RANGE),
            replacements=tuple(clean_rules(raw.get("replacements"))),
        )

    def backend_for(self, device: str) -> str:
        """Движок распознавания для устройства ("cuda" или "cpu")."""
        return self.backend if device == "cuda" else self.cpu_backend

    def to_raw(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "language": self.language,
            "align": self.align,
            "overlap": self.overlap,
            "device": self.device,
            "cpu_model": self.cpu_model,
            "cpu_backend": self.cpu_backend,
            "gigaam_model": self.gigaam_model,
            "align_after_gigaam": self.align_after_gigaam,
            "voice_threshold": self.voice_threshold,
            "replacements": [dict(r) for r in self.replacements],
        }


def _asr_backend(value, default: str) -> str:
    """Движок распознавания из закрытого списка; "whisper" — как
    "faster-whisper", опечатка — дефолт."""
    if isinstance(value, str) and value.strip() in ASR_BACKEND_ALIASES:
        return ASR_BACKEND_ALIASES[value.strip()]
    return as_choice(value, ASR_BACKENDS, default)


def _legacy_cpu_backend(cpu_model: str) -> str:
    """Движок на процессоре для конфига без `cpu_backend` (до 0.3.0).

    GigaAM получает тот, у кого модель для процессора — поставляемая по
    умолчанию (Whisper medium): он её не выбирал. Кто выбрал свою модель
    Whisper, тот на ней и остаётся — его выбор молча не меняем."""
    return GIGAAM if cpu_model == DEFAULT_CPU_WHISPER_MODEL else WHISPER


@dataclass(frozen=True)
class Llm:
    """Кто отвечает на вопросы по встрече и ведёт дайджест.

    `claude-code` — нынешний путь через claude-agent-sdk (нужен установленный
    Claude Code CLI). `openai-compatible` — локальная модель через LM Studio или
    Ollama: своего рантайма не тащим, а инструменты чтения хранилища такой
    провайдер не поддерживает — это учитывает слой assist.

    `proxy` — прокси для Claude Code/Codex (и загрузок моделей задачами):
    `system` — как в Windows, `none` — без прокси, или адрес; см. meet.netproxy.
    """

    provider: str = "auto"
    model: str = "sonnet"
    base_url: str = DEFAULT_LOCAL_BASE_URL
    local_model: str | None = None
    proxy: str = "system"

    @classmethod
    def from_raw(cls, raw: dict, default_provider: str = "auto") -> "Llm":
        from meet import netproxy

        local = raw.get("local_model")
        base = raw.get("base_url")
        return cls(
            provider=as_choice(raw.get("provider"), LLM_PROVIDERS, default_provider),
            model=str(raw.get("model") or "sonnet").strip() or "sonnet",
            base_url=str(base).strip() if base else DEFAULT_LOCAL_BASE_URL,
            local_model=str(local).strip() if local else None,
            proxy=netproxy.normalize(raw.get("proxy")),
        )

    @staticmethod
    def check(update: dict) -> None:
        """Правка из окна: ValueError с текстом для человека (негодный адрес прокси)."""
        from meet import netproxy

        if "proxy" in update:
            error = netproxy.check(update["proxy"])
            if error:
                raise ValueError(error)

    def to_raw(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "local_model": self.local_model,
            "proxy": self.proxy,
        }


@dataclass(frozen=True)
class Assist:
    """Живой ассистент. `vault` наследуется из переменной среды MEET_VAULT,
    если в файле его нет: так продолжают работать нынешние запуски.

    Живые подсказки: `activity` — «Сдержанно» (`calm`), «Активно» (`active`)
    или «Только сводка» (`summary`); `hints_model` — модель для тиков: «Как у
    агента» (`agent`) или «Быстрее» (`fast`: Claude — haiku, Codex — низкое
    усилие рассуждения, локальная — без изменений); `max_hints` — сколько
    подсказок держать (0 — по активности: 5 или 8); `min_words` — сколько
    новых слов нужно для тика (0 — по активности); `quiet_default` —
    «Не отвлекать» включено при открытии панели."""

    vault: Path | None = None
    window_seconds: float = 20.0
    port: int = 8765
    voices: bool = True
    # Необязательная конвенция хранилища задач (meet.assist.context): заметка-
    # индекс в корне и приставка имени заметки-хаба задачи.
    vault_index: str = "Claude Docs.md"
    hub_prefix: str = "_"
    activity: str = "calm"
    hints_model: str = "agent"
    max_hints: int = 0
    min_words: int = 0
    quiet_default: bool = False

    @classmethod
    def from_raw(cls, raw: dict) -> "Assist":
        vault = as_path(raw.get("vault"))
        if vault is None:
            vault = as_path(os.environ.get("MEET_VAULT"))
        index = raw.get("vault_index")
        prefix = raw.get("hub_prefix")
        return cls(
            vault=vault,
            window_seconds=as_positive(raw.get("window_seconds"), 20.0, 1.0),
            port=as_int(raw.get("port"), 8765, 1024),
            voices=as_flag(raw.get("voices"), True),
            vault_index=index.strip() if isinstance(index, str) else "Claude Docs.md",
            hub_prefix=prefix if isinstance(prefix, str) else "_",
            activity=as_choice(raw.get("activity"), ASSIST_ACTIVITIES, "calm"),
            hints_model=as_choice(raw.get("hints_model"), HINTS_MODELS, "agent"),
            max_hints=_max_hints(raw.get("max_hints")),
            min_words=as_int(raw.get("min_words"), 0, 0),
            quiet_default=as_flag(raw.get("quiet_default"), False),
        )

    def to_raw(self) -> dict:
        return {
            "vault": str(self.vault) if self.vault else None,
            "window_seconds": self.window_seconds,
            "port": self.port,
            "voices": self.voices,
            "vault_index": self.vault_index,
            "hub_prefix": self.hub_prefix,
            "activity": self.activity,
            "hints_model": self.hints_model,
            "max_hints": self.max_hints,
            "min_words": self.min_words,
            "quiet_default": self.quiet_default,
        }


def _max_hints(value) -> int:
    """0 — по активности; иначе в пределах MAX_HINTS_RANGE."""
    if isinstance(value, bool):
        return 0
    number = as_int(value, 0, 0)
    if number == 0:
        return 0
    low, high = MAX_HINTS_RANGE
    return min(high, max(low, number))


@dataclass(frozen=True)
class Assistant:
    """Где ассистент берёт знания и куда кладёт заметки о встречах.

    `knowledge_dir` — папка с материалами (читает ассистент), `notes_dir` —
    корень заметок, `notes_subdir` — подпапка внутри него. None — не задано.
    """

    knowledge_dir: Path | None = None
    notes_dir: Path | None = None
    notes_subdir: str = DEFAULT_NOTES_SUBDIR
    # «Придумывать название встречи» (meet.titles): название от модели — из
    # анализа, итогов или темы живого режима. Названия, которые человек задал
    # сам, не меняются никогда.
    auto_title: bool = False

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
            # Раскладка внутри прежнего vault остаётся такой, какой была.
            subdir = "" if "notes_dir" not in raw and vault else DEFAULT_NOTES_SUBDIR
        return cls(knowledge_dir=knowledge, notes_dir=notes, notes_subdir=subdir,
                   auto_title=as_flag(raw.get("auto_title"), False))

    def to_raw(self) -> dict:
        return {
            "knowledge_dir": str(self.knowledge_dir) if self.knowledge_dir else None,
            "notes_dir": str(self.notes_dir) if self.notes_dir else None,
            "notes_subdir": self.notes_subdir,
            "auto_title": self.auto_title,
        }


@dataclass(frozen=True)
class Analysis:
    """«Анализ встречи» (meet.analysis): разметка готовой расшифровки моделью.

    `auto` — ставить анализ сам после расшифровки, импорта, объединения и
    переразделения на спикеров (если подключена модель и встреча не короче
    `auto_record.min_call_seconds`). Остальные флаги — что размечать: типы
    реплик, важность, главы, наблюдения, категория, название. Выключенное не
    запрашивается и не показывается.

    `improve_auto` — «Улучшать расшифровку автоматически после распознавания»
    (meet.improve): после расшифровки модель сама готовит список исправлений
    терминов; применяет их человек. По умолчанию выключено."""

    auto: bool = True
    types: bool = True
    importance: bool = True
    chapters: bool = True
    insights: bool = True
    category: bool = True
    title: bool = True
    improve_auto: bool = False

    @classmethod
    def from_raw(cls, raw: dict) -> "Analysis":
        return cls(auto=as_flag(raw.get("auto"), True), improve_auto=as_flag(raw.get("improve_auto"), False),
                   **{name: as_flag(raw.get(name), True) for name in ANALYSIS_FEATURES})

    def features(self) -> tuple[str, ...]:
        """Включённые части разметки — в порядке ANALYSIS_FEATURES."""
        return tuple(name for name in ANALYSIS_FEATURES if getattr(self, name))

    def to_raw(self) -> dict:
        return {"auto": self.auto, **{name: getattr(self, name) for name in ANALYSIS_FEATURES},
                "improve_auto": self.improve_auto}


@dataclass(frozen=True)
class TranscriptView:
    """«Расшифровка: подсветка и разметка» — что из анализа встречи окно
    показывает в карточке: значки типов реплик (и фильтры по ним), полосу у
    важных реплик, заголовки глав, блок «Наблюдения», кривую важности над
    плеером (`curve`: всегда, при наведении, нет), подписи глав на полосе
    плеера и ссылки на задачи Jira. Только отображение: что размечать, решает
    секция `analysis`."""

    types: bool = True
    importance: bool = True
    chapters: bool = True
    insights: bool = True
    curve: str = CURVE_MODES[1]
    bar_labels: bool = True
    jira: bool = True

    @classmethod
    def from_raw(cls, raw: dict) -> "TranscriptView":
        return cls(
            **{name: as_flag(raw.get(name), True)
               for name in ("types", "importance", "chapters", "insights", "bar_labels", "jira")},
            curve=as_choice(raw.get("curve"), CURVE_MODES, CURVE_MODES[1]),
        )

    def to_raw(self) -> dict:
        return {
            "types": self.types,
            "importance": self.importance,
            "chapters": self.chapters,
            "insights": self.insights,
            "curve": self.curve,
            "bar_labels": self.bar_labels,
            "jira": self.jira,
        }


@dataclass(frozen=True)
class Category:
    """Категория встреч: `id` — латиница (хранится в analysis.json и meta.json),
    `name` и `color` — для окна, `description` — подсказка модели."""

    id: str
    name: str
    color: str = "#9aa0a6"
    description: str = ""

    def to_raw(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color,
                "description": self.description}


_CATEGORY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_categories() -> tuple[Category, ...]:
    return tuple(Category(*item) for item in DEFAULT_CATEGORIES)


def as_categories(value) -> tuple[Category, ...]:
    """Список категорий из конфига. Нет ключа или не список — умолчания; пустой
    список — осознанное «без категорий». Битые записи и повторы id
    отбрасываются."""
    if not isinstance(value, list):
        return default_categories()
    out: list[Category] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip().lower()
        name = " ".join(str(item.get("name") or "").split())[:CATEGORY_NAME_MAX]
        if (not cid or len(cid) > CATEGORY_ID_MAX or not _CATEGORY_ID.match(cid) or not name
                or cid in seen):
            continue
        color = str(item.get("color") or "").strip()
        description = " ".join(str(item.get("description") or "").split())
        seen.add(cid)
        out.append(Category(id=cid, name=name, color=color if _COLOR.match(color) else "#9aa0a6",
                            description=description[:CATEGORY_DESCRIPTION_MAX]))
    return tuple(out)


@dataclass(frozen=True)
class Export:
    """Выгрузка встреч в базу знаний (Obsidian и т. п.): папка на встречу по
    шаблону имени внутри `meetings_dir`, в ней — выбранные файлы.

    Шаблоны и имена файлов проверяет `meet.kb_export`; негодное значение из
    файла (правка руками) молча заменяется дефолтом, а из окна — отказ с
    объяснением (`check`)."""

    meetings_dir: Path | None = None
    folder_template: str = DEFAULT_FOLDER_TEMPLATE
    transcript_name: str = DEFAULT_TRANSCRIPT_NAME
    summary_name: str = DEFAULT_SUMMARY_NAME
    include_transcript: bool = True
    include_summary: bool = True
    include_audio: bool = False
    include_srt: bool = False
    # Действует, только когда задана meetings_dir.
    auto_export: bool = True

    @classmethod
    def from_raw(cls, raw: dict, legacy_dir: Path | None = None) -> "Export":
        """`legacy_dir` — прежняя папка заметок (assistant.notes_dir/notes_subdir):
        ею заполняется meetings_dir, если ключа в секции нет вовсе. Явный null —
        осознанное «не задано» и из старых ключей не воскрешается."""
        from meet import kb_export

        def text(key: str, default: str, check) -> str:
            value = raw.get(key)
            if not isinstance(value, str) or check(value) is not None:
                return default
            return value.strip()

        transcript_name = text("transcript_name", DEFAULT_TRANSCRIPT_NAME,
                               kb_export.check_file_name)
        summary_name = text("summary_name", DEFAULT_SUMMARY_NAME, kb_export.check_file_name)
        if kb_export.check_names(transcript_name, summary_name):
            transcript_name, summary_name = DEFAULT_TRANSCRIPT_NAME, DEFAULT_SUMMARY_NAME
        return cls(
            meetings_dir=as_path(raw["meetings_dir"]) if "meetings_dir" in raw else legacy_dir,
            folder_template=text("folder_template", DEFAULT_FOLDER_TEMPLATE,
                                 kb_export.check_folder_template),
            transcript_name=transcript_name,
            summary_name=summary_name,
            include_transcript=as_flag(raw.get("include_transcript"), True),
            include_summary=as_flag(raw.get("include_summary"), True),
            include_audio=as_flag(raw.get("include_audio"), False),
            include_srt=as_flag(raw.get("include_srt"), False),
            auto_export=as_flag(raw.get("auto_export"), True),
        )

    @staticmethod
    def check(update: dict, current: "Export | None" = None) -> None:
        """Правка из окна: ValueError с текстом для человека, если её нельзя
        сохранить (шаблон выходит за папку, неизвестная подстановка, имена
        файлов совпадают). `current` — сохранённые значения: имя проверяется
        в паре с тем, что уже лежит в настройках."""
        from meet import kb_export

        current = current or Export()

        if "folder_template" in update:
            error = kb_export.check_folder_template(str(update["folder_template"] or ""))
            if error:
                raise ValueError(error)
        for key, what in (("transcript_name", "транскрипта"), ("summary_name", "итогов")):
            if key in update:
                error = kb_export.check_file_name(str(update[key] or ""))
                if error:
                    raise ValueError(f"Имя файла {what}: {error[0].lower()}{error[1:]}")
        if "transcript_name" in update or "summary_name" in update:
            error = kb_export.check_names(
                str(update.get("transcript_name", current.transcript_name) or ""),
                str(update.get("summary_name", current.summary_name) or ""))
            if error:
                raise ValueError(error)
        if "meetings_dir" in update:
            folder = as_path(update["meetings_dir"])
            if folder is not None and not folder.is_absolute():
                raise ValueError("Папка для встреч должна быть указана полным путём")

    def to_raw(self) -> dict:
        return {
            "meetings_dir": str(self.meetings_dir) if self.meetings_dir else None,
            "folder_template": self.folder_template,
            "transcript_name": self.transcript_name,
            "summary_name": self.summary_name,
            "include_transcript": self.include_transcript,
            "include_summary": self.include_summary,
            "include_audio": self.include_audio,
            "include_srt": self.include_srt,
            "auto_export": self.auto_export,
        }


@dataclass(frozen=True)
class Integrations:
    """Связи с чужими программами. Все выключаемые: приложение обязано быть
    полезным само по себе."""

    # Маркер «GPU занят» для внешнего наблюдателя (например, утилиты голосового
    # ввода, которая по нему выгружает свою копию Whisper из видеопамяти). Кому
    # это не нужно — выключает, и файл не создаётся вовсе.
    gpu_marker: bool = True
    gpu_marker_path: Path | None = None
    # Токен Hugging Face из config.json — только запасной путь: его место —
    # диспетчер учётных данных (meet.credentials), куда load() переносит его
    # при первой встрече. В файле он остаётся, лишь если диспетчер недоступен.
    # Наружу (to_raw, GET /settings) не отдаётся никогда; пишут его только
    # write_hf_token/drop_hf_token.
    hf_token: str = ""
    # Ссылки на задачи Jira в карточке записи (M3): адрес (пусто — ссылок нет)
    # и шаблон ключей. Негодное значение из файла — по умолчанию; из окна —
    # отказ с текстом (check). Оболочка читает адрес отсюда же и открывает
    # ссылки только на его хост.
    jira_base_url: str = ""
    jira_keys: str = DEFAULT_JIRA_KEYS

    @classmethod
    def from_raw(cls, raw: dict) -> "Integrations":
        token = raw.get("hf_token")
        keys = str(raw.get("jira_keys") or "").strip()
        return cls(
            gpu_marker=as_flag(raw.get("gpu_marker"), True),
            gpu_marker_path=as_path(raw.get("gpu_marker_path")),
            hf_token=str(token).strip() if token else "",
            jira_base_url=clean_jira_base(raw.get("jira_base_url")),
            jira_keys=keys if keys and not jira_keys_error(keys) else DEFAULT_JIRA_KEYS,
        )

    @staticmethod
    def check(update: dict) -> None:
        """Правка из окна: ValueError с текстом для человека (адрес Jira, шаблон ключей)."""
        for key, error_of in (("jira_base_url", jira_base_error), ("jira_keys", jira_keys_error)):
            if key in update:
                error = error_of(update[key])
                if error:
                    raise ValueError(error)

    def to_raw(self) -> dict:
        return {
            "gpu_marker": self.gpu_marker,
            "gpu_marker_path": str(self.gpu_marker_path)
            if self.gpu_marker_path
            else None,
            "jira_base_url": self.jira_base_url,
            "jira_keys": self.jira_keys,
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
    export: Export = field(default_factory=Export)
    integrations: Integrations = field(default_factory=Integrations)
    ui: Ui = field(default_factory=Ui)
    analysis: Analysis = field(default_factory=Analysis)
    transcript_view: TranscriptView = field(default_factory=TranscriptView)
    # Не секция, а список (см. as_categories): patch() заменяет его целиком.
    categories: tuple[Category, ...] = field(default_factory=default_categories)

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
        assistant = Assistant.from_raw(_section(raw, "assistant"), vault=assist.vault)
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
            assistant=assistant,
            export=Export.from_raw(_section(raw, "export"), legacy_dir=_legacy_notes(assistant)),
            integrations=Integrations.from_raw(_section(raw, "integrations")),
            ui=Ui.from_raw(_section(raw, "ui")),
            analysis=Analysis.from_raw(_section(raw, "analysis")),
            transcript_view=TranscriptView.from_raw(_section(raw, "transcript_view")),
            categories=as_categories(raw.get("categories")),
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
            "export": self.export.to_raw(),
            "integrations": self.integrations.to_raw(),
            "ui": self.ui.to_raw(),
            "analysis": self.analysis.to_raw(),
            "transcript_view": self.transcript_view.to_raw(),
            "categories": [c.to_raw() for c in self.categories],
        }


def _legacy_notes(assistant: Assistant) -> Path | None:
    """Прежняя «папка заметок» как папка для встреч: notes_dir/notes_subdir.
    Старые ключи читаются только ради этого — окно их больше не показывает."""
    if assistant.notes_dir is None:
        return None
    sub = (assistant.notes_subdir or "").strip()
    return assistant.notes_dir / sub if sub else assistant.notes_dir


def migrate(raw: dict) -> dict:
    """Старый формат → текущая схема, в памяти.

    Главное правило миграции: **у того, кто уже пользуется meet, поведение не
    меняется**. Дефолты новой схемы рассчитаны на человека, который ставит
    приложение впервые (пост-хук ничего не запускает, про регулярные встречи
    ничего не знает), поэтому существующему конфигу мы явным образом
    достраиваем то, что раньше было зашито в коде.

    * v0 — файл без `version`: `post_record_hook` на верхнем уровне, зашитый
      запуск Claude Code в Windows Terminal, окно регулярной встречи 11:00–12:00.
    * v1 — первая схема: то же, но флаг уже в `hooks.post_record`, а окно
      называлось `daily_window`.
    * v2 версии 0.1.0 — без `auto_record.processes`: прежние умолчания.

    Старые ключи из словаря не выбрасываем: `save()` их сохранит, и файл
    останется понятным предыдущей версии кода.
    """
    if not isinstance(raw, dict):
        return {}
    version = raw.get("version")
    if version == SCHEMA_VERSION:
        # v2 0.1.0 без сохранённого списка программ жил на прежних умолчаниях
        # (с Webex) — с ними и остаётся. Эта версия пишет список всегда, так
        # что новые умолчания достаются только новой установке.
        auto_record = _section(raw, "auto_record")
        if "processes" in auto_record:
            return raw
        return {**raw, "auto_record": {**auto_record, "processes": list(HISTORIC_PROCESSES)}}
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
        # Список программ звонков без явного значения — прежние умолчания.
        auto_record = dict(_section(migrated, "auto_record"))
        auto_record.setdefault("processes", list(HISTORIC_PROCESSES))
        migrated["auto_record"] = auto_record
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
    """Настройки из файла. Файла нет — дефолты.

    На диск не пишет — с одним исключением: токен HF, найденный в config.json
    приложения, переносится в диспетчер учётных данных и из файла стирается
    (см. _migrate_hf_token). Секрет открытым текстом важнее правила."""
    raw = read_raw(path)
    if _is_app_config(path):
        raw = _migrate_hf_token(raw)
    return Settings.from_raw(raw)


def _write_raw(target: Path, data: dict) -> None:
    """Атомарная запись словаря в файл настроек.

    Через временный файл и `os.replace`: резидент читает `config.json` в любой
    момент, и он не должен увидеть половину записи."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, target)


def save(settings: Settings, path: Path | None = None) -> None:
    """Записать настройки, сохранив неизвестные ключи из файла."""
    target = Path(path or paths.config_path())
    with _FILE_LOCK:
        merged = dict(read_raw(target))
        merged.update(settings.to_raw())
        # Integrations.to_raw токена не содержит: запасная копия токена
        # (диспетчер недоступен) приходит из файла выше и так и сохраняется.
        # post_record_hook больше не читается (его место — hooks.post_record), но
        # и не удаляется: файл остаётся понятным старой версии кода.
        if "post_record_hook" in merged:
            merged["post_record_hook"] = settings.hooks.post_record
        _write_raw(target, merged)


# --- токен Hugging Face в файле: только запасной путь ------------------------

# Чтение-правка-запись config.json из разных потоков резидента (PATCH из окна,
# миграция при загрузке) — под одним замком, иначе одна правка теряет другую.
_FILE_LOCK = threading.RLock()

MIGRATED_LINE = "токен HF перенесён в диспетчер учётных данных"
KEYRING_UNAVAILABLE_LINE = (
    "диспетчер учётных данных недоступен — токен остаётся в config.json"
)
# Диспетчер не принял токен: до конца процесса не пробуем снова (load зовут
# часто). Строка в журнал — одна на data dir, а не на процесс: иначе каждая
# задача расшифровки (свой подпроцесс) дописывала бы её заново. Отметка —
# файл рядом с журналом; диспетчер заработал — файл стирается.
_MIGRATION_FAILED = False
KEYRING_UNAVAILABLE_MARK = "keyring-unavailable.flag"


def _reset_secret_migration() -> None:
    """Для тестов: забыть, что диспетчер уже отказал."""
    global _MIGRATION_FAILED
    _MIGRATION_FAILED = False


def _is_app_config(path) -> bool:
    """Файл настроек приложения, а не чужой (тест, утилита сравнения)."""
    if path is None:
        return True
    try:
        return Path(path).resolve() == paths.config_path().resolve()
    except OSError:
        return False


def _mark_path() -> Path:
    return paths.data_dir() / KEYRING_UNAVAILABLE_MARK


def _report_keyring_unavailable() -> None:
    """Строка о недоступном диспетчере — один раз, пока он не заработает."""
    mark = _mark_path()
    if mark.exists():
        return
    _log(KEYRING_UNAVAILABLE_LINE)
    try:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text("", encoding="utf-8")
    except OSError:
        pass


def keyring_works_again() -> None:
    """Диспетчер принял токен: следующий отказ снова стоит строки в журнале."""
    try:
        _mark_path().unlink(missing_ok=True)
    except OSError:
        pass


def _log(line: str) -> None:
    """Строка в watch.log — журнал резидента (под pythonw stderr не видно)."""
    try:
        from meet import watch

        watch.WatchLog(watch.default_log_path())(line)
    except Exception:
        pass  # журнал не должен мешать работе


def _hf_token_in(raw: dict) -> str:
    value = _section(raw, "integrations").get("hf_token")
    return str(value).strip() if value else ""


def _without_hf_token(raw: dict) -> dict:
    integrations = dict(_section(raw, "integrations"))
    integrations.pop("hf_token", None)
    return {**raw, "integrations": integrations}


def _migrate_hf_token(raw: dict) -> dict:
    """Перенести токен HF из config.json в диспетчер учётных данных.

    Молча: ни уведомлений, ни вопросов — одна строка в watch.log. Диспетчер
    недоступен — файл не трогаем, токен работает оттуда (credentials читает
    его последним), строка в журнале — одна на процесс."""
    global _MIGRATION_FAILED
    if _MIGRATION_FAILED or not _hf_token_in(raw):
        return raw
    from meet import credentials

    with _FILE_LOCK:
        target = paths.config_path()
        current = read_raw(target)
        token = _hf_token_in(current)
        if not token:  # другой поток уже перенёс
            return current
        try:
            # keyring_set читает записанное обратно: поле в файле стираем, только
            # если диспетчер действительно отдаёт тот же токен.
            credentials.keyring_set(token)
        except credentials.Unavailable:
            _MIGRATION_FAILED = True
            _report_keyring_unavailable()
            return raw
        keyring_works_again()
        cleaned = _without_hf_token(current)
        try:
            _write_raw(target, cleaned)
        except OSError:
            # Токен уже в диспетчере; стереть копию не вышло — попробуем при
            # следующей загрузке. Работать это не мешает.
            return raw
    _log(MIGRATED_LINE)
    return cleaned


def drop_hf_token(path: Path | None = None) -> None:
    """Стереть копию токена из файла настроек, если она там есть."""
    target = Path(path or paths.config_path())
    with _FILE_LOCK:
        raw = read_raw(target)
        if "hf_token" in _section(raw, "integrations"):
            _write_raw(target, _without_hf_token(raw))


def write_hf_token(token: str, path: Path | None = None) -> None:
    """Запасной путь: диспетчер недоступен — токен в файл настроек."""
    target = Path(path or paths.config_path())
    with _FILE_LOCK:
        raw = read_raw(target)
        integrations = dict(_section(raw, "integrations"))
        integrations["hf_token"] = token
        _write_raw(target, {**raw, "integrations": integrations})


# Секции, которые умеет обновлять patch(). Выводятся из самой схемы, а не
# перечислены руками: раньше список отставал (integrations добавили в схему, а
# сюда забыли — и токен HF молча не сохранялся). `version` — не секция.
PATCHABLE_SECTIONS = tuple(
    name for name in Settings.__dataclass_fields__ if name not in ("version", "categories")
)


def patch(updates: dict, path: Path | None = None) -> Settings:
    """Точка для UI настроек: частичное обновление по секциям.

    `{"auto_record": {"enabled": True}}` меняет один флаг, не затрагивая
    остальные поля секции. Неизвестные секции игнорируются — валидацию имён
    делает вызывающий (API), а не файл.
    """
    updates = _route_hf_token(updates, path)
    current = load(path)
    changed = {}
    for name in PATCHABLE_SECTIONS:
        section_update = updates.get(name)
        if not isinstance(section_update, dict):
            continue
        if name == "export":
            Export.check(section_update, current.export)
        if name == "llm":
            Llm.check(section_update)
        if name == "integrations":
            Integrations.check(section_update)
        merged = getattr(current, name).to_raw()
        merged.update(section_update)
        if name == "recording":
            merged["former_speaker_names"] = _former_names(current.recording, merged)
        changed[name] = type(getattr(current, name)).from_raw(merged)
    if isinstance(updates.get("categories"), list):
        # Список целиком: порядок — порядок в окне, удалённое просто не пришло.
        changed["categories"] = as_categories(updates["categories"])
    updated = replace(current, **changed) if changed else current
    save(updated, path)
    return updated


# Сколько прежних имён владельца помнить.
FORMER_NAMES_MAX = 10


def _former_names(current: "Recording", merged: dict) -> list[str]:
    """Сменилось имя владельца — прежнее в список прежних (окно его не шлёт и
    стереть не может): реплики старых записей подписаны им."""
    former = list(current.former_speaker_names)
    new = str(merged.get("speaker_name") or "").strip() or "Вы"
    if new != current.speaker_name and current.speaker_name != "Вы":
        former.append(current.speaker_name)
    out = [x for x in dict.fromkeys(former) if x != new]
    return out[-FORMER_NAMES_MAX:]


def _route_hf_token(updates: dict, path: Path | None) -> dict:
    """`integrations.hf_token` из PATCH уходит в диспетчер учётных данных, а
    не в файл: окно настроек до мастера токена шлёт его именно так.

    Пустая строка — ничего не делать: прежнее окно шлёт поле пустым, просто
    не показав сохранённый токен, и стирать из-за этого секрет нельзя.
    Забыть токен — только явно, `DELETE /hf/token`."""
    integrations = updates.get("integrations") if isinstance(updates, dict) else None
    if not isinstance(integrations, dict) or "hf_token" not in integrations:
        return updates
    from meet import credentials

    rest = dict(integrations)
    token = str(rest.pop("hf_token") or "").strip()
    if token:
        credentials.set_hf_token(token, path)
    return {**updates, "integrations": rest}
