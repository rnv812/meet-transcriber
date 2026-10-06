"""Провайдер локальной модели через OpenAI-совместимый API (LM Studio, Ollama, vLLM).

Только stdlib (urllib). Инструментов нет: базу знаний такая модель не читает,
`allowed_dirs`/`cwd`/`resume`/`session_id`/`max_turns` игнорируются. Имя модели —
`llm.local_model` из настроек (его подставляет `meet.llm.resolve`).

Прокси: локальная модель через прокси не ходит никогда (как NO_PROXY у детей,
см. meet.netproxy) — ни на этом компьютере, ни в домашней или офисной сети
(частные адреса 10/8, 172.16/12, 192.168/16, link-local, имена без точки и
`*.local`): прокси такого адреса всё равно не увидит. Остальные адреса — как
у urllib: переменные среды и прокси системы.

Ответ по схеме (`response_schema`, анализ встречи): сначала самый строгий
режим `response_format` — `json_schema` (LM Studio, vLLM, llama.cpp, новые
Ollama); сервер его не понял (400/422) — `json_object`, затем без него (схема
остаётся в промпте). Сработавший режим запоминается на процесс.
"""

import asyncio
import ipaddress
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from meet.llm.base import EMPTY_ERROR, TIMEOUT_ERROR, AgentReply
from meet.settings import DEFAULT_LOCAL_BASE_URL

DEFAULT_MODEL = "local-model"
_ERR_LIMIT = 500

# Режимы ответа по схеме — от строгого к свободному; None — без response_format.
SCHEMA_MODES = ("json_schema", "json_object", None)
# (адрес, модель) → режим, который сервер принял (на процесс задачи).
_schema_mode: dict[tuple[str, str], str | None] = {}
# Признаки «не понял response_format» в тексте ошибки 500 (llama.cpp, старые Ollama).
_FORMAT_MARKERS = ("response_format", "json_schema", "schema", "grammar", "json_object")


def direct(url: str) -> bool:
    """Адрес — в этой сети (компьютер, LAN): к нему без прокси."""
    try:
        host = (urlparse(url).hostname or "").strip("[]").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(".local") or ("." not in host and ":" not in host):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_url(req: urllib.request.Request, timeout: float):
    """urlopen по правилу прокси выше: локальные адреса — напрямую."""
    if direct(req.full_url):
        return _DIRECT.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _post(url: str, payload: dict, timeout_s: float) -> tuple[AgentReply, int | None, str]:
    """→ (ответ, код HTTP-ошибки или None, текст ошибки сервера)."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with open_url(req, timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace").strip()
        return AgentReply(text="", error=f"HTTP {e.code}: {detail}"[:_ERR_LIMIT]), e.code, detail
    except (TimeoutError, socket.timeout):
        return AgentReply(text="", error=TIMEOUT_ERROR), None, ""
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return AgentReply(text="", error=TIMEOUT_ERROR), None, ""
        return AgentReply(text="", error=f"нет связи с локальной моделью: {e.reason}"), None, ""
    except (OSError, ValueError) as e:
        return AgentReply(text="", error=f"{type(e).__name__}: {e}"[:_ERR_LIMIT]), None, ""
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return AgentReply(text="", error="неожиданный ответ локальной модели"), None, ""
    text = text.strip()
    if not text:
        return AgentReply(text="", error=EMPTY_ERROR), None, ""
    return AgentReply(text=text), None, ""


def _format_rejected(status: int | None, detail: str) -> bool:
    """Сервер отверг сам response_format, а не запрос вообще."""
    if status in (400, 422):
        return True
    return status in (500, 501) and any(m in detail.lower() for m in _FORMAT_MARKERS)


def response_format(mode: str | None, schema: dict) -> dict | None:
    if mode == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def _complete(url: str, payload: dict, timeout_s: float, schema: dict | None) -> AgentReply:
    if schema is None:
        return _post(url, payload, timeout_s)[0]
    key = (url, str(payload.get("model")))
    modes = SCHEMA_MODES[SCHEMA_MODES.index(_schema_mode[key]):] if key in _schema_mode else SCHEMA_MODES
    reply = AgentReply(text="", error=EMPTY_ERROR)
    for mode in modes:
        fmt = response_format(mode, schema)
        reply, status, detail = _post(url, {**payload, "response_format": fmt} if fmt else payload, timeout_s)
        if mode is not None and _format_rejected(status, detail):
            continue  # следующий режим, послабее
        if status is None:
            _schema_mode[key] = mode
        return reply
    return reply


async def run(
    prompt: str,
    *,
    system_prompt: str,
    model: str | None = None,
    resume: str | None = None,
    session_id: str | None = None,
    allowed_dirs: tuple = (),
    cwd: str | Path | None = None,
    timeout_s: float = 180.0,
    max_turns: int = 8,
    on_text=None,
    base_url: str = DEFAULT_LOCAL_BASE_URL,
    local_model: str | None = None,
    response_schema: dict | None = None,
) -> AgentReply:
    """POST {base_url}/chat/completions; ошибки — в AgentReply.error.

    `model` (имя модели Claude из общего контракта) не используется: имя
    локальной модели задаёт `local_model`. `response_schema` — JSON Schema
    ответа: сервер попросят отвечать строго по ней (см. начало модуля)."""
    payload = {
        "model": local_model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    url = base_url.rstrip("/") + "/chat/completions"
    return await asyncio.to_thread(_complete, url, payload, timeout_s, response_schema)
