"""Провайдер локальной модели через OpenAI-совместимый API (LM Studio, Ollama).

Только stdlib (urllib). Инструментов нет: базу знаний такая модель не читает,
`allowed_dirs`/`cwd`/`resume`/`max_turns` игнорируются. Имя модели —
`llm.local_model` из настроек (его подставляет `meet.llm.resolve`).
"""

import asyncio
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

from meet.llm.base import EMPTY_ERROR, TIMEOUT_ERROR, AgentReply
from meet.settings import DEFAULT_LOCAL_BASE_URL

DEFAULT_MODEL = "local-model"
_ERR_LIMIT = 500


def _post(url: str, payload: dict, timeout_s: float) -> AgentReply:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace").strip()
        return AgentReply(text="", error=f"HTTP {e.code}: {detail}"[:_ERR_LIMIT])
    except (TimeoutError, socket.timeout):
        return AgentReply(text="", error=TIMEOUT_ERROR)
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return AgentReply(text="", error=TIMEOUT_ERROR)
        return AgentReply(text="", error=f"нет связи с локальной моделью: {e.reason}")
    except (OSError, ValueError) as e:
        return AgentReply(text="", error=f"{type(e).__name__}: {e}"[:_ERR_LIMIT])
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return AgentReply(text="", error="неожиданный ответ локальной модели")
    text = text.strip()
    if not text:
        return AgentReply(text="", error=EMPTY_ERROR)
    return AgentReply(text=text)


async def run(
    prompt: str,
    *,
    system_prompt: str,
    model: str | None = None,
    resume: str | None = None,
    allowed_dirs: tuple = (),
    cwd: str | Path | None = None,
    timeout_s: float = 180.0,
    max_turns: int = 8,
    base_url: str = DEFAULT_LOCAL_BASE_URL,
    local_model: str | None = None,
) -> AgentReply:
    """POST {base_url}/chat/completions; ошибки — в AgentReply.error.

    `model` (имя модели Claude из общего контракта) не используется: имя
    локальной модели задаёт `local_model`."""
    payload = {
        "model": local_model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    url = base_url.rstrip("/") + "/chat/completions"
    return await asyncio.to_thread(_post, url, payload, timeout_s)
