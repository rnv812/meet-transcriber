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

__all__ = ["PROVIDERS", "AgentReply", "Runner", "provider_ready", "resolve", "runner_for"]


def runner_for(name: str, cfg: "Settings") -> Runner:
    """Функция вызова модели для провайдера (без проверки доступности)."""
    # Ссылка на модуль, а не на функцию: тесты подменяют `claude.run`.
    if name == "claude-code":
        from meet.llm import claude
        return partial(_call, claude, proxy=cfg.llm.proxy)
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
