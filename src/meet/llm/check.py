"""Проверка провайдера: `python -m meet.llm.check <provider>`.

Печатает одну строку JSON `{"ok": bool, "error": str|None, "provider": str}`;
код выхода 0 — всё хорошо, 1 — нет. Запускается резидентом подпроцессом
(кнопка «Проверить»), поэтому вызывать модель здесь можно.
"""

import asyncio
import json
import sys

from meet.llm import PROVIDERS, detect

PROBE_PROMPT = "Ответь одним словом: да"
PROBE_SYSTEM = "Отвечай одним словом."
PROBE_TIMEOUT_S = 60.0


def _result(provider: str, error: str | None) -> dict:
    return {"ok": error is None, "error": error, "provider": provider}


async def _check_claude() -> str | None:
    from meet.llm import claude

    claude.drop_api_key()
    path = detect.find_claude()
    if path is None:
        return "не найден Claude Code CLI (claude)"
    ok, why = detect.logged_in("claude-code", path)
    if not ok:
        return f"не авторизован: {why}"
    return await claude.check_auth()


async def _check_codex() -> str | None:
    from meet.llm import codex

    path = detect.find_codex()
    if path is None:
        return "не найден Codex CLI (codex)"
    ok, why = detect.logged_in("codex", path)
    if not ok:
        return f"не авторизован: {why}"
    reply = await codex.run(PROBE_PROMPT, system_prompt=PROBE_SYSTEM,
                            max_turns=1, timeout_s=PROBE_TIMEOUT_S)
    return reply.error


async def _check_local() -> str | None:
    from meet import settings
    from meet.llm import openai_compat

    cfg = settings.load()
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
                                   "подключите Claude Code или Codex")
        provider = name
    if provider not in PROVIDERS:
        return _result(provider, f"неизвестный провайдер: {provider}")
    try:
        if provider == "claude-code":
            error = await _check_claude()
        elif provider == "codex":
            error = await _check_codex()
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
