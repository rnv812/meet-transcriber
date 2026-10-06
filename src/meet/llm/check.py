"""Проверка провайдера: `python -m meet.llm.check <provider>`.

Печатает одну строку JSON `{"ok": bool, "error": str|None, "provider": str}`;
код выхода 0 — всё хорошо, 1 — нет. Запускается резидентом подпроцессом
(кнопка «Проверить»), поэтому вызывать модель здесь можно.

OpenCode проверяется без вызова модели (квоту не тратит): найден ли он, есть
ли у него модель из настроек (`opencode models <провайдер>`), а без своей
модели — есть ли вход (записи в его auth.json). Годен ли сам ключ, так не
узнать: это скажет первый настоящий вызов.
"""

import asyncio
import json
import sys

from meet.llm import PROVIDERS, detect

PROBE_PROMPT = "Ответь одним словом: да"
PROBE_SYSTEM = "Отвечай одним словом."
PROBE_TIMEOUT_S = 60.0


def _proxy() -> str:
    """`llm.proxy` из настроек: проверка идёт тем же путём, что и работа."""
    from meet import settings

    return settings.load().llm.proxy


def _model() -> str:
    """`llm.model` из настроек: «Проверить» Claude Code — той же моделью, что работа."""
    from meet import settings

    return settings.load().llm.model


def _result(provider: str, error: str | None) -> dict:
    return {"ok": error is None, "error": error, "provider": provider}


async def _check_claude() -> str | None:
    from meet.llm import claude

    claude.drop_api_key()
    path = detect.find_claude()
    if path is None:
        return detect.claude_not_found()
    ok, why = detect.logged_in("claude-code", path)
    if not ok:
        return f"не авторизован: {why}"
    return await claude.check_auth(proxy=_proxy(), model=_model())


async def _check_codex() -> str | None:
    from meet.llm import codex

    path = detect.find_codex()
    if path is None:
        return "не найден Codex CLI (codex)"
    ok, why = detect.logged_in("codex", path)
    if not ok:
        return f"не авторизован: {why}"
    reply = await codex.run(PROBE_PROMPT, system_prompt=PROBE_SYSTEM,
                            max_turns=1, timeout_s=PROBE_TIMEOUT_S, proxy=_proxy())
    return reply.error


async def _check_opencode() -> str | None:
    from meet import settings

    path = detect.find_opencode()
    if path is None:
        return detect.OPENCODE_NOT_FOUND
    llm = settings.load().llm
    if llm.opencode_model:
        ok, why = detect.opencode_model_listed(path, llm.opencode_model, llm.proxy)
        if ok:
            return None
        # Модели нет — подскажем, если у её провайдера нет и входа (запись в
        # auth.json или его ключ в переменной среды). Локальным провайдерам
        # (ollama и т. п.) ключ не нужен — поэтому это только подсказка.
        provider = llm.opencode_model.split("/", 1)[0]
        if not detect.opencode_auth_present(provider):
            keys = " или ".join(detect.opencode_provider_env(provider))
            why = (f"{why}; входа для {provider} в OpenCode нет — выполните "
                   f"opencode auth login или задайте {keys}")
        return why
    ok, why = detect.logged_in("opencode", path)
    return None if ok else f"не авторизован: {why}"


async def _check_local() -> str | None:
    from meet import settings
    from meet.llm import local_models, openai_compat

    cfg = settings.load()
    # Сначала список моделей сервера (выбранная должна в нём быть), затем
    # короткий вызов. Сервер без списка моделей (не всякий его отдаёт) —
    # решает сам вызов.
    listed = await asyncio.to_thread(local_models.list_models, cfg.llm.base_url, cfg.llm.local_model)
    if not listed["ok"] and listed["reason"] != "not_openai":
        return listed["error"]
    if listed["ok"]:
        ids = [m["id"] for m in listed["models"]]
        shown = ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")
        if listed["missing"]:
            return f"{listed['warning']} (на сервере: {shown})"
        if not cfg.llm.local_model and len(ids) > 1:
            return f"Модель не выбрана, а на сервере их несколько ({shown}) — выберите в «Имя модели»"
    reply = await openai_compat.run(
        PROBE_PROMPT, system_prompt=PROBE_SYSTEM, max_turns=1,
        timeout_s=PROBE_TIMEOUT_S, base_url=cfg.llm.base_url,
        local_model=cfg.llm.local_model,
    )
    return reply.error


async def check(provider: str) -> dict:
    """Проверить провайдера коротким вызовом модели. `auto` — того, кого
    выбрал бы `resolve`."""
    if provider == "auto":
        from meet import settings
        from meet.llm import resolve

        name, _ = resolve(settings.load())
        if name is None:
            return _result("auto", "нет доступного провайдера: "
                                   "подключите Claude Code или Codex либо выберите OpenCode")
        provider = name
    if provider not in PROVIDERS:
        return _result(provider, f"неизвестный провайдер: {provider}")
    try:
        if provider == "claude-code":
            error = await _check_claude()
        elif provider == "codex":
            error = await _check_codex()
        elif provider == "opencode":
            error = await _check_opencode()
        else:
            error = await _check_local()
    except Exception as e:  # проверка не должна падать трейсбеком
        error = f"{type(e).__name__}: {e}"
    return _result(provider, error)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    provider = args[0] if args else "auto"
    res = asyncio.run(check(provider))
    # ensure_ascii: читатель — резидент через pipe, кодировка консоли неважна.
    print(json.dumps(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
