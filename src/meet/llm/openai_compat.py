"""Провайдер локальной модели через OpenAI-совместимый API (LM Studio, Ollama, vLLM).

Только stdlib (urllib). Инструментов нет: базу знаний такая модель не читает,
`allowed_dirs`/`cwd`/`resume`/`session_id`/`max_turns` игнорируются. Имя модели —
`llm.local_model` из настроек (его подставляет `meet.llm.resolve`).

Прокси: локальная модель через прокси не ходит (как NO_PROXY у детей, см.
meet.netproxy) — ни на этом компьютере, ни в домашней или офисной сети
(частные адреса 10/8, 172.16/12, 192.168/16, CGNAT 100.64/10 — Tailscale,
link-local, имена без точки и `*.local`): прокси такого адреса обычно не
видит. Где сервер модели доступен только через прокси, есть настройка
`llm.local_via_proxy` («Локальную модель — через прокси»): тогда — как у
urllib, переменные среды и прокси системы. Остальные адреса — всегда так.

Длина ответа: `max_tokens` уходит всегда (`DEFAULT_MAX_TOKENS` или своя у
шага): иначе зациклившаяся модель (бесконечные пробелы в ответе по схеме у
llama.cpp и Ollama) пишет до таймаута вызова.

Ответ по схеме (`response_schema`, анализ встречи):

* Ollama — родной `POST /api/chat`: только там можно задать окно контекста
  на запрос (`options.num_ctx`; маршрут `/v1` его не берёт, и Ollama молча
  обрезает длинный промпт до своего окна по умолчанию — 2–4 тыс. токенов),
  длину ответа (`num_predict`) и схему (`format`).
* остальные — `response_format` от строгого к свободному: `json_schema`
  (LM Studio, vLLM, llama.cpp), сервер его не понял — `json_object`, затем без
  него (схема остаётся в промпте). Сработавший режим запоминается на процесс.

Переполнение контекста (vLLM, llama.cpp отвечают на него 400) — не «формат
не понят»: ошибка `CONTEXT_ERROR` сразу, без лишних вызовов.
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
DEFAULT_MAX_TOKENS = 8192
_ERR_LIMIT = 500
CONTEXT_ERROR = "текст не помещается в контекст модели"
# Ollama: больше этого окна контекста на запрос не просим (память).
OLLAMA_MAX_CTX = 32768
# Оценка размера промпта в токенах: русский текст — около 3 символов на токен
# (с запасом: больше токенов — больше окно, не меньше).
CHARS_PER_TOKEN = 3.0

# Режимы ответа по схеме — от строгого к свободному; None — без response_format.
SCHEMA_MODES = ("json_schema", "json_object", None)
# (адрес, модель) → режим, который сервер принял (на процесс задачи).
_schema_mode: dict[tuple[str, str], str | None] = {}
# Признаки «не понял response_format» в тексте ошибки.
_FORMAT_MARKERS = ("response_format", "json_schema", "json_object", "schema", "grammar",
                   "guided", "format")
# Признаки «промпт не помещается в контекст» (vLLM, llama.cpp, LM Studio, OpenAI).
_CONTEXT_MARKERS = ("context length", "context_length", "maximum context", "context size",
                    "context window", "n_ctx", "exceed_context", "exceeds the available context",
                    "context overflow", "too many tokens", "prompt is too long",
                    "reduce the length")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def direct(url: str) -> bool:
    """Адрес — в этой сети (компьютер, LAN, Tailscale): к нему без прокси."""
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
    return ip.is_loopback or ip.is_private or ip.is_link_local or (ip.version == 4 and ip in _CGNAT)


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_url(req: urllib.request.Request, timeout: float, via_proxy: bool = False):
    """urlopen по правилу прокси выше: локальные адреса — напрямую, если не
    включено «через прокси» (`via_proxy`)."""
    if not via_proxy and direct(req.full_url):
        return _DIRECT.open(req, timeout=timeout)
    # Свой opener на вызов, а не общий urlopen: тот запоминает прокси при
    # первом вызове, а задача модели задаёт их переменными позже (netproxy.prepare).
    return urllib.request.build_opener(urllib.request.ProxyHandler()).open(req, timeout=timeout)


def _overflow(detail: str) -> bool:
    low = detail.lower()
    return any(m in low for m in _CONTEXT_MARKERS)


def _request(url: str, payload: dict, timeout_s: float, via_proxy: bool):
    """POST JSON → (тело ответа или None, AgentReply с ошибкой или None,
    код HTTP-ошибки или None, текст ошибки сервера)."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with open_url(req, timeout_s, via_proxy) as resp:
            return json.loads(resp.read().decode("utf-8")), None, None, ""
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace").strip()
        if _overflow(detail):
            return None, AgentReply(text="", error=f"{CONTEXT_ERROR}: {detail}"[:_ERR_LIMIT]), e.code, detail
        return None, AgentReply(text="", error=f"HTTP {e.code}: {detail}"[:_ERR_LIMIT]), e.code, detail
    except (TimeoutError, socket.timeout):
        return None, AgentReply(text="", error=TIMEOUT_ERROR), None, ""
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return None, AgentReply(text="", error=TIMEOUT_ERROR), None, ""
        return None, AgentReply(text="", error=f"нет связи с локальной моделью: {e.reason}"), None, ""
    except (OSError, ValueError) as e:
        return None, AgentReply(text="", error=f"{type(e).__name__}: {e}"[:_ERR_LIMIT]), None, ""


def _reply(text, usage: dict | None) -> AgentReply:
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        return AgentReply(text="", error=EMPTY_ERROR, usage=usage)
    return AgentReply(text=text, usage=usage)


def _post(url: str, payload: dict, timeout_s: float, via_proxy: bool = False) -> tuple[AgentReply, int | None, str]:
    """`/chat/completions` → (ответ, код HTTP-ошибки или None, текст ошибки сервера)."""
    body, failed, status, detail = _request(url, payload, timeout_s, via_proxy)
    if failed is not None:
        return failed, status, detail
    usage = body.get("usage") if isinstance(body, dict) and isinstance(body.get("usage"), dict) else None
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return AgentReply(text="", error="неожиданный ответ локальной модели"), None, ""
    return _reply(text, usage), None, ""


def _format_rejected(status: int | None, detail: str) -> bool:
    """Сервер отверг сам response_format, а не запрос вообще. Переполнение
    контекста сюда не попадает (`_overflow`, раньше)."""
    if status not in (400, 422, 500, 501) or _overflow(detail):
        return False
    low = detail.lower()
    if any(m in low for m in _FORMAT_MARKERS):
        return True
    # 400/422 без объяснения — скорее всего, не понят именно формат.
    return status in (400, 422) and not low.strip("{}[]\" \n")


def response_format(mode: str | None, schema: dict) -> dict | None:
    if mode == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def accepted_modes() -> list[str]:
    """Какими режимами ответа по схеме сервер отвечал в этом процессе
    (`none` — схема только в промпте, `ollama` — родной /api/chat): для
    журнала задачи."""
    return sorted({mode or "none" for mode in _schema_mode.values()})


def ollama_num_ctx(prompt_chars: int, max_tokens: int, trained: int | None) -> int:
    """Окно контекста Ollama на запрос: промпт + ответ + 10 %, не больше
    обученного у модели и OLLAMA_MAX_CTX."""
    need = int((prompt_chars / CHARS_PER_TOKEN + max_tokens) * 1.1)
    return max(2048, min(need, trained or OLLAMA_MAX_CTX, OLLAMA_MAX_CTX))


def _ollama_chat(base_url: str, payload: dict, timeout_s: float, schema: dict, max_tokens: int,
                 via_proxy: bool) -> AgentReply | None:
    """Родной `/api/chat` Ollama с окном контекста по промпту; не Ollama — None."""
    from meet.llm import local_models

    root = local_models.server_root(base_url)
    if not local_models.is_ollama(base_url, via_proxy=via_proxy):
        return None
    model = str(payload.get("model"))
    trained = local_models.ollama_trained_context(base_url, model, via_proxy=via_proxy)
    chars = sum(len(m.get("content") or "") for m in payload["messages"])
    body = {"model": model, "messages": payload["messages"], "stream": False, "format": schema,
            "options": {"num_ctx": ollama_num_ctx(chars, max_tokens, trained), "num_predict": max_tokens}}
    data, failed, _status, _detail = _request(f"{root}/api/chat", body, timeout_s, via_proxy)
    if failed is not None:
        return failed
    _schema_mode[(f"{root}/api/chat", model)] = "ollama"
    message = data.get("message") if isinstance(data, dict) else None
    count = data.get("prompt_eval_count") if isinstance(data, dict) else None
    usage = {"prompt_tokens": count, "completion_tokens": data.get("eval_count")} if isinstance(count, int) else None
    return _reply(message.get("content") if isinstance(message, dict) else None, usage)


def _complete(base_url: str, payload: dict, timeout_s: float, schema: dict | None, max_tokens: int,
              via_proxy: bool) -> AgentReply:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {**payload, "max_tokens": max_tokens}
    if schema is None:
        return _post(url, payload, timeout_s, via_proxy)[0]
    native = _ollama_chat(base_url, payload, timeout_s, schema, max_tokens, via_proxy)
    if native is not None:
        return native
    key = (url, str(payload.get("model")))
    modes = SCHEMA_MODES[SCHEMA_MODES.index(_schema_mode[key]):] if key in _schema_mode else SCHEMA_MODES
    reply = AgentReply(text="", error=EMPTY_ERROR)
    for mode in modes:
        fmt = response_format(mode, schema)
        reply, status, detail = _post(url, {**payload, "response_format": fmt} if fmt else payload,
                                      timeout_s, via_proxy)
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
    max_tokens: int | None = None,
    via_proxy: bool = False,
) -> AgentReply:
    """Вызов модели; ошибки — в AgentReply.error, счёт токенов — в `usage`.

    `model` (имя модели Claude из общего контракта) не используется: имя
    локальной модели задаёт `local_model`. `response_schema` — JSON Schema
    ответа (см. начало модуля); `max_tokens` — предел длины ответа."""
    payload = {
        "model": local_model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    return await asyncio.to_thread(_complete, base_url, payload, timeout_s, response_schema,
                                   max_tokens or DEFAULT_MAX_TOKENS, via_proxy)
