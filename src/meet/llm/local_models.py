"""Какие модели есть на локальном OpenAI-совместимом сервере (LM Studio,
Ollama, vLLM, llama.cpp) — для «Найти модели» в настройках и «Проверить».

Список — `GET {base_url}/models` (адрес без `/v1` — `{base_url}/v1/models`);
нет его — родной список Ollama `GET /api/tags`. Модели не вызываются. Только
stdlib: модуль зовёт резидент. Прокси — по правилу локальной модели
(`openai_compat.direct`: свои адреса — напрямую). Короткий таймаут: окно
ждёт ответа.

Размеры моделей отдаёт только Ollama (`/api/tags`: байты и число параметров) —
у сервера Ollama список `/v1/models` дополняется ими; vLLM сообщает длину
контекста (`max_model_len`).
"""

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from meet.llm.openai_compat import open_url

TIMEOUT_S = 3.0
_OLLAMA_PORT = 11434
# Модели эмбеддингов (LM Studio и Ollama показывают их в общем списке) в чат не годятся.
_EMBEDDING_MARKERS = ("embed",)


class _NotList(ValueError):
    """Ответ есть, но это не список моделей."""


def _fail(reason: str, error: str, url: str | None = None) -> dict:
    return {"ok": False, "reason": reason, "error": error, "models": [], "url": url,
            "source": None, "missing": False, "warning": None}


def models_url(base_url: str) -> str:
    """Адрес списка: `…/v1` → `…/v1/models`, без `/v1` — `…/v1/models`."""
    root = base_url.strip().rstrip("/")
    return f"{root}/models" if root.endswith("/v1") else f"{root}/v1/models"


def _tags_url(base_url: str) -> str:
    u = urlparse(base_url.strip())
    return f"{u.scheme}://{u.netloc}/api/tags"


def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with open_url(req, timeout) as resp:
        raw = resp.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _NotList("не JSON") from None


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _from_openai(body) -> list[dict]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise _NotList("нет поля data")
    out = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            out.append({"id": item["id"].strip(), "size": _int(item.get("size")), "params": None,
                        "context": _int(item.get("max_model_len")) or _int(item.get("context_length")),
                        "_owner": item.get("owned_by")})
    return out


def _from_ollama(body) -> list[dict]:
    data = body.get("models") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise _NotList("нет поля models")
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip():
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            params = details.get("parameter_size")
            out.append({"id": name.strip(), "size": _int(item.get("size")),
                        "params": params if isinstance(params, str) and params else None, "context": None})
    return out


def _is_ollama(base_url: str, models: list[dict]) -> bool:
    try:
        port = urlparse(base_url).port
    except ValueError:
        port = None
    return port == _OLLAMA_PORT or any(m.get("_owner") == "library" for m in models)


def _unreachable(base_url: str, e: BaseException, timeout: float) -> dict:
    reason = getattr(e, "reason", e)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return _fail("unreachable", f"Сервер не ответил за {timeout:g} с: {base_url}")
    return _fail("unreachable", f"Сервер не отвечает: {base_url} — запущены ли LM Studio "
                                "(сервер включён) или Ollama?")


def _auth(code: int, url: str) -> dict:
    return _fail("auth", f"Сервер требует ключ доступа (HTTP {code}): Meet подключается к "
                         "локальной модели без ключа — выключите проверку ключа в настройках сервера", url)


def list_models(base_url: str, model: str | None = None, timeout: float = TIMEOUT_S) -> dict:
    """Модели сервера → {"ok", "models": [{"id", "size", "params", "context"}],
    "source": "openai"|"ollama", "url", "error", "reason", "missing", "warning"}.

    `reason` при ошибке: `bad_url`, `unreachable` (не отвечает, таймаут),
    `auth` (401/403), `not_openai` (отвечает, но списка моделей нет), `empty`.
    `model` — выбранная модель: нет её в списке — `missing` и `warning`."""
    base = (base_url or "").strip()
    try:
        parsed = urlparse(base)
        ok_url = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        ok_url = False
    if not ok_url:
        return _fail("bad_url", "Адрес сервера должен начинаться с http:// или https://, "
                                "например http://localhost:1234/v1")
    url = models_url(base)
    models: list[dict] | None = None
    source = "openai"
    try:
        models = _from_openai(_get(url, timeout))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return _auth(e.code, url)
    except _NotList:
        pass
    except (urllib.error.URLError, OSError) as e:
        return _unreachable(base, e, timeout)
    tags = None
    if models is None or _is_ollama(base, models):
        try:
            tags = _from_ollama(_get(_tags_url(base), timeout))
        except urllib.error.HTTPError as e:
            if models is None and e.code in (401, 403):
                return _auth(e.code, _tags_url(base))
        except (_NotList, urllib.error.URLError, OSError):
            pass
    if models is None:
        if tags is None:
            return _fail("not_openai", f"По адресу {base} отвечает сервер, но не OpenAI-совместимый: "
                                       f"списка моделей нет ({url}). Проверьте адрес — обычно он "
                                       "кончается на /v1", url)
        models, source, url = tags, "ollama", _tags_url(base)
    elif tags:
        # Ollama: размеры и число параметров — из её родного списка.
        extra = {t["id"]: t for t in tags}
        models = [{**m, "size": m["size"] or extra.get(m["id"], {}).get("size"),
                   "params": extra.get(m["id"], {}).get("params")} for m in models]
    models = [{k: v for k, v in m.items() if not k.startswith("_")} for m in models]
    chat = [m for m in models if not any(x in m["id"].lower() for x in _EMBEDDING_MARKERS)]
    if not chat:
        error = ("На сервере только модели эмбеддингов — загрузите языковую модель" if models else
                 "Сервер отвечает, но моделей нет: загрузите модель в LM Studio или скачайте в Ollama "
                 "(ollama pull …)")
        return {**_fail("empty", error, url), "source": source}
    wanted = (model or "").strip()
    missing = bool(wanted) and wanted not in {m["id"] for m in chat}
    return {"ok": True, "reason": None, "error": None, "models": chat, "url": url, "source": source,
            "missing": missing,
            "warning": f"Модели «{wanted}» на сервере нет — выберите другую из списка" if missing else None}
