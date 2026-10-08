"""Слой провайдеров модели: Claude Code, Codex CLI, OpenCode, локальная OpenAI-совместимая.

Пакет импортирует резидент, поэтому провайдеры здесь подгружаются лениво
(внутри функций): ни claude_agent_sdk, ни aiohttp при импорте не тянутся.
"""

from functools import partial
from typing import TYPE_CHECKING

from meet.llm.base import DEFAULT_CLAUDE_MODEL, NO_VISION_NOTE, AgentReply, Runner, claude_model

if TYPE_CHECKING:
    from meet.settings import Settings

PROVIDERS = ("claude-code", "codex", "opencode", "openai-compatible")
# Порядок выбора для "auto": подписки CLI раньше локальной модели. OpenCode в
# «Авто» не участвует — только явным выбором: у кого «Авто» шло на локальную
# модель, текст встречи не должен сам уйти облачному провайдеру из конфига
# OpenCode, едва тот появился на машине.
AUTO_PROVIDERS = ("claude-code", "codex", "openai-compatible")

__all__ = ["AUTO_PROVIDERS", "DEFAULT_CLAUDE_MODEL", "LABELS", "NO_VISION_NOTE", "PROVIDERS", "AgentReply",
           "Runner", "agent_model", "choice_error", "claude_model", "deny_enforced", "forget_session", "not_found", "describe", "is_local", "label",
           "models", "provider_ready", "resolve", "runner_for", "session_kwargs", "supports_resume",
           "tier_kwargs", "vision"]

# Кто видит изображения (v4-design §5.1): Claude Code — блоки base64 в
# сообщении, Codex — `--image=`. OpenCode (флаг вложения не проверен) и
# локальная модель (решение концепции) — нет: runner параметр `images`
# принимает и игнорирует, окно показывает NO_VISION_NOTE.
VISION_PROVIDERS = ("claude-code", "codex")
# Кто продолжает свой сеанс нативно (v4-design §12): Claude Code `--resume`,
# Codex `exec resume`, OpenCode `--session`. Локальная модель — только затравкой.
RESUME_PROVIDERS = ("claude-code", "codex", "opencode")
# Кто сам не даёт читать закрытые папки (`deny_paths`, `kb_exclude`; v4-simple
# §6): Claude Code — правила Read(//…/**) (проверено, что CLI их принимает при
# наших флагах; что Grep их соблюдает — смоук); OpenCode — запрет в правах
# агента [не проверено]; локальная модель файлов не читает вовсе. Codex —
# только просьба в промпте (песочница read-only читает весь диск): окну
# стоит сказать «у Codex исключения — просьба, а не запрет».
DENY_ENFORCED_PROVIDERS = ("claude-code", "opencode", "openai-compatible")

# Имена моделей для человека: окно, подпись итогов, журнал.
LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "openai-compatible": "Локальная модель",
}

# «Быстрее» для живых подсказок: та же подписка, модель полегче. haiku без
# явного «не размышлять» думает по умолчанию — и тик идёт 16–90 с вместо 2–4 с.
FAST_CLAUDE_MODEL = "haiku"
FAST_CLAUDE_THINKING = "disabled"
FAST_CODEX_EFFORT = "low"


def tier_kwargs(provider: str | None, tier: str, model: str | None = None) -> dict:
    """Что добавить к вызову модели для уровня `tier` (`agent` — как у
    агента, `fast` — быстрее). «Быстрее»: Claude — модель haiku без
    размышлений, Codex — низкое усилие рассуждения. «Как у агента»: Claude — модель из настроек
    (`model` — `llm.model`); Codex берёт модель из своего конфига, OpenCode —
    `llm.opencode_model` в самом runner (уровни моделей у его провайдеров
    разные — «Быстрее» ему ничего не меняет), локальная модель одна — им
    добавлять нечего. Claude Code получает модель всегда: пустая `model` —
    DEFAULT_CLAUDE_MODEL, а не модель CLI по умолчанию."""
    if tier == "fast":
        if provider == "claude-code":
            return {"model": FAST_CLAUDE_MODEL, "thinking": FAST_CLAUDE_THINKING}
        if provider == "codex":
            return {"effort": FAST_CODEX_EFFORT}
        return {}
    if provider == "claude-code":
        return {"model": claude_model(model)}
    return {}


def agent_model(provider: str | None, cfg: "Settings") -> str | None:
    """Модель агента для явной передачи в вызов: `llm.model` у Claude Code,
    None у остальных (Codex — модель из своего конфига, OpenCode —
    `llm.opencode_model`, локальная — `llm.local_model` в самом runner)."""
    return tier_kwargs(provider, "agent", cfg.llm.model).get("model")


def runner_for(name: str, cfg: "Settings") -> Runner:
    """Функция вызова модели для провайдера (без проверки доступности).

    Claude Code получает модель из настроек (`llm.model`) — её берут все
    вызовы: итоги, вопросы, анализ, названия, улучшение, живой ассистент.
    Явный `model=` в вызове её перекрывает («Быстрее» — haiku)."""
    # Ссылка на модуль, а не на функцию: тесты подменяют `claude.run`.
    if name == "claude-code":
        from meet.llm import claude
        return partial(_call, claude, proxy=cfg.llm.proxy, model=claude_model(cfg.llm.model))
    if name == "codex":
        from meet.llm import codex
        return partial(_call, codex, proxy=cfg.llm.proxy)
    if name == "opencode":
        from meet.llm import opencode
        return partial(_call, opencode, proxy=cfg.llm.proxy, model=cfg.llm.opencode_model or None)
    if name == "openai-compatible":
        from meet.llm import openai_compat
        return partial(openai_compat.run, base_url=cfg.llm.base_url,
                       local_model=cfg.llm.local_model, via_proxy=local_route(cfg))
    raise ValueError(f"неизвестный провайдер: {name}")


def local_route(cfg: "Settings", via_proxy: bool | None = None) -> bool | str:
    """Как идти к серверу локальной модели (`openai_compat.open_url`): False —
    свои адреса напрямую; «через прокси» (`llm.local_via_proxy`, или
    `via_proxy` из черновика окна) — адрес из `llm.proxy`, если он задан
    явно, иначе True (прокси системы); `llm.proxy = none` — напрямую."""
    on = cfg.llm.local_via_proxy if via_proxy is None else via_proxy
    if not on or cfg.llm.proxy == "none":
        return False
    if cfg.llm.proxy == "system" or cfg.llm.proxy.lower().startswith("socks"):
        # SOCKS urllib не умеет (окно не даст сохранить; файл правили руками) — прокси системы.
        return True
    return cfg.llm.proxy


# Параметры вызова, которые понимает только локальная модель: назначение и
# предел ответа, схема, обрезка. CLI-провайдерам их не передаём.
LOCAL_ONLY = ("purpose", "max_tokens", "response_schema", "on_cut")


async def _call(module, *args, **kwargs):
    """`module.run` с прокси из настроек (`llm.proxy`), без параметров только
    для локальной модели (`LOCAL_ONLY`)."""
    for key in LOCAL_ONLY:
        kwargs.pop(key, None)
    return await module.run(*args, **kwargs)


def provider_ready(name: str, cfg: "Settings", *, need_login: bool) -> bool:
    """Установлен (CLI найден / локальная модель отвечает) и, если
    `need_login`, в CLI выполнен вход."""
    from meet.llm import detect

    if name == "openai-compatible":
        return detect.local_reachable(cfg.llm.base_url, via_proxy=local_route(cfg))
    finders = {"claude-code": detect.find_claude, "codex": detect.find_codex,
               "opencode": detect.find_opencode}
    path = finders[name]() if name in finders else None
    if path is None:
        return False
    if need_login:
        return detect.logged_in(name, path)[0]
    return True


def resolve(cfg: "Settings", provider: str | None = None) -> tuple[str | None, Runner | None]:
    """Кто будет отвечать.

    Без `provider` — модель по умолчанию (`llm.provider`): `auto` — первый
    готовый из включённых по порядку claude-code → codex → openai-compatible
    (OpenCode — только явным выбором; CLI без входа пропускается: выбирается
    следующий). Это выбор до вызова: упавший вызов на другую модель не
    переходит. Явный провайдер по умолчанию — он, если найден, иначе (None, None).

    `provider` — модель, выбранная человеком для одного действия: она, если
    включена в настройках и найдена, иначе (None, None) — никакого перехода на
    модель по умолчанию или «Авто» (почему — `choice_error`)."""
    if provider is not None:
        if choice_error(cfg, provider) is not None:
            return None, None
        return provider, runner_for(provider, cfg)
    choice = cfg.llm.provider
    if choice == "auto":
        for name in AUTO_PROVIDERS:
            if name in cfg.llm.enabled and provider_ready(name, cfg, need_login=True):
                return name, runner_for(name, cfg)
        return None, None
    if choice in PROVIDERS and provider_ready(choice, cfg, need_login=False):
        return choice, runner_for(choice, cfg)
    return None, None


def choice_error(cfg: "Settings", name: str) -> str | None:
    """Почему выбранной человеком модели `name` нельзя дать задачу; None — можно.
    Неизвестная, не включённая в настройках, не найдена на машине (вход в CLI
    не проверяется: упадёт сам вызов — с текстом CLI)."""
    if name not in PROVIDERS:
        return f"неизвестная модель: {name}"
    if name not in cfg.llm.enabled:
        return f"модель «{LABELS[name]}» не включена в настройках («Настройки → Модели ИИ»)"
    if provider_ready(name, cfg, need_login=False):
        return None
    return not_found(name, cfg)


def not_found(name: str, cfg: "Settings") -> str:
    """Почему модели `name` нет на машине — текст для человека."""
    from meet.llm import detect

    if name == "claude-code":
        return detect.claude_not_found()
    if name == "codex":
        return "не найден Codex CLI (codex)"
    if name == "opencode":
        return detect.OPENCODE_NOT_FOUND
    return f"локальная модель не отвечает: {cfg.llm.base_url}"


def describe(provider: str | None, cfg: "Settings") -> dict:
    """Происхождение результата: {"provider", "model"} — какая модель его
    сделала. `model` — имя из настроек (у Codex — из его конфига: None)."""
    model = None
    if provider == "claude-code":
        model = claude_model(cfg.llm.model)
    elif provider == "opencode":
        model = cfg.llm.opencode_model or None
    elif provider == "openai-compatible":
        model = cfg.llm.local_model or None
    return {"provider": provider, "model": model}


def label(origin: dict | None) -> str:
    """«Claude Code (sonnet)», «Codex», «Локальная модель (qwen3)»."""
    if not isinstance(origin, dict) or not origin.get("provider"):
        return "модель"
    name = LABELS.get(str(origin["provider"]), str(origin["provider"]))
    model = origin.get("model")
    return f"{name} ({model})" if model else name


_LOOPBACK = ("localhost", "127.0.0.1", "::1")


def is_local(provider: str | None, cfg: "Settings") -> bool:
    """Данные не покидают компьютер: локальная модель на этом же компьютере
    (адрес — localhost/127.0.0.1). Сервер в сети, облачные CLI и OpenCode
    (у него и провайдер может быть облачным) — нет."""
    if provider != "openai-compatible":
        return False
    from urllib.parse import urlparse

    try:
        host = urlparse(cfg.llm.base_url).hostname or ""
    except ValueError:
        return False
    return host.lower() in _LOOPBACK or host.startswith("127.")


def vision(provider: str | None) -> bool:
    """Видит ли модель изображения (`images=` у runner, `Conversation.send`)."""
    return provider in VISION_PROVIDERS


def supports_resume(provider: str | None) -> bool:
    """Продолжает ли провайдер сохранённый сеанс по id (`resume=`)."""
    return provider in RESUME_PROVIDERS


def session_kwargs(provider: str | None, session_id: str | None) -> dict:
    """Что передать runner, чтобы разговор шёл в сеансе провайдера:
    известен id — продолжить его (`resume`); нет — начать сохраняемый
    (`keep_session`, id придёт в `AgentReply.session_id`); провайдер без
    сеансов — ничего (контекст — затравкой из журнала). Ответ с
    `resume_failed` — забыть id (`ChatLog.set_session_id(…, None)`) и
    повторить с затравкой: `session_kwargs(provider, None)`."""
    if not supports_resume(provider):
        return {}
    if session_id:
        return {"resume": session_id}
    return {"keep_session": True}


def deny_enforced(provider: str | None) -> bool:
    """Соблюдает ли провайдер `deny_paths` сам (не только по просьбе в промпте)."""
    return provider in DENY_ENFORCED_PROVIDERS


def forget_session(provider: str | None, session_id: str | None) -> int:
    """Удалить сохранённый сеанс провайдера (удалили чат встречи; смоук с
    `--cleanup`): Claude Code и Codex — файлы сеанса в их хранилище (команды
    удаления у CLI нет), OpenCode — `opencode session delete`. → сколько
    удалено (0 — нечего, не тот id или провайдер без сеансов)."""
    if not session_id:
        return 0
    if provider == "claude-code":
        from meet.llm import claude
        return claude.forget_session(session_id)
    if provider == "codex":
        from meet.llm import codex
        return codex.forget_session(session_id)
    if provider == "opencode":
        from meet.llm import opencode
        return opencode.forget_session(session_id)
    return 0


def models(cfg: "Settings", found: dict, auto_pick: str | None = None) -> list[dict]:
    """Включённые модели для окна (выбор модели у действий карточки): имя,
    модель, по умолчанию ли, локальная ли, есть ли на машине и почему нет.
    `found` — `detect.available()`; вход в CLI не проверяется (это секунды).
    `auto_pick` — кого сейчас выбирает «Авто» (если оно по умолчанию): он и
    отмечен как модель по умолчанию."""
    out = []
    for name in cfg.llm.enabled:
        origin = describe(name, cfg)
        available = bool((found.get(name) or {}).get("found"))
        out.append({
            "provider": name,
            "model": origin["model"],
            "label": label(origin),
            "default": cfg.llm.provider == name or (cfg.llm.provider == "auto" and auto_pick == name),
            "local": is_local(name, cfg),
            "available": available,
            "reason": None if available else not_found(name, cfg),
        })
    return out
