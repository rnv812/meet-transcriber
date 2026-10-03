"""Слой провайдеров модели: Claude Code, Codex CLI, локальная OpenAI-совместимая.

Пакет импортирует резидент, поэтому провайдеры здесь подгружаются лениво
(внутри функций): ни claude_agent_sdk, ни aiohttp при импорте не тянутся.
"""

from functools import partial
from typing import TYPE_CHECKING

from meet.llm.base import AgentReply, Runner

if TYPE_CHECKING:
    from meet.settings import Settings

# Порядок выбора для "auto": подписки CLI раньше локальной модели.
PROVIDERS = ("claude-code", "codex", "openai-compatible")

__all__ = ["PROVIDERS", "AgentReply", "Runner", "agent_model", "provider_ready", "resolve",
           "runner_for", "tier_kwargs"]

# «Быстрее» для живых подсказок: та же подписка, модель полегче. haiku без
# явного «не размышлять» думает по умолчанию — и тик идёт 16–90 с вместо 2–4 с.
FAST_CLAUDE_MODEL = "haiku"
FAST_CLAUDE_THINKING = "disabled"
FAST_CODEX_EFFORT = "low"


def tier_kwargs(provider: str | None, tier: str, model: str | None = None) -> dict:
    """Что добавить к вызову модели для уровня `tier` (`agent` — как у
    агента, `fast` — быстрее). «Быстрее»: Claude — модель haiku без
    размышлений, Codex — низкое усилие рассуждения. «Как у агента»: Claude — модель из настроек
    (`model` — `llm.model`); Codex берёт модель из своего конфига, локальная
    модель одна — им добавлять нечего."""
    if tier == "fast":
        if provider == "claude-code":
            return {"model": FAST_CLAUDE_MODEL, "thinking": FAST_CLAUDE_THINKING}
        if provider == "codex":
            return {"effort": FAST_CODEX_EFFORT}
        return {}
    if provider == "claude-code" and model:
        return {"model": model}
    return {}


def agent_model(provider: str | None, cfg: "Settings") -> str | None:
    """Модель агента для явной передачи в вызов: `llm.model` у Claude Code,
    None у остальных (Codex — модель из своего конфига, локальная —
    `llm.local_model` в самом runner)."""
    return tier_kwargs(provider, "agent", cfg.llm.model).get("model")


def runner_for(name: str, cfg: "Settings") -> Runner:
    """Функция вызова модели для провайдера (без проверки доступности).

    Claude Code получает модель из настроек (`llm.model`) — её берут все
    вызовы: итоги, вопросы, анализ, названия, улучшение, живой ассистент.
    Явный `model=` в вызове её перекрывает («Быстрее» — haiku)."""
    # Ссылка на модуль, а не на функцию: тесты подменяют `claude.run`.
    if name == "claude-code":
        from meet.llm import claude
        return partial(_call, claude, proxy=cfg.llm.proxy, model=cfg.llm.model)
    if name == "codex":
        from meet.llm import codex
        return partial(_call, codex, proxy=cfg.llm.proxy)
    if name == "openai-compatible":
        from meet.llm import openai_compat
        return partial(openai_compat.run, base_url=cfg.llm.base_url,
                       local_model=cfg.llm.local_model)
    raise ValueError(f"неизвестный провайдер: {name}")


async def _call(module, *args, **kwargs):
    """`module.run` с прокси из настроек (`llm.proxy`)."""
    return await module.run(*args, **kwargs)


def provider_ready(name: str, cfg: "Settings", *, need_login: bool) -> bool:
    """Установлен (CLI найден / локальная модель отвечает) и, если
    `need_login`, в CLI выполнен вход."""
    from meet.llm import detect

    if name == "openai-compatible":
        return detect.local_reachable(cfg.llm.base_url)
    path = detect.find_claude() if name == "claude-code" else (
        detect.find_codex() if name == "codex" else None)
    if path is None:
        return False
    if need_login:
        return detect.logged_in(name, path)[0]
    return True


def resolve(cfg: "Settings") -> tuple[str | None, Runner | None]:
    """Кто будет отвечать. `auto` — первый готовый из claude-code → codex →
    openai-compatible (CLI без входа пропускается: выбирается следующий).
    Явный провайдер — он, если найден, иначе (None, None)."""
    choice = cfg.llm.provider
    if choice == "auto":
        for name in PROVIDERS:
            if provider_ready(name, cfg, need_login=True):
                return name, runner_for(name, cfg)
        return None, None
    if choice in PROVIDERS and provider_ready(choice, cfg, need_login=False):
        return choice, runner_for(choice, cfg)
    return None, None
