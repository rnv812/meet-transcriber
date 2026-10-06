"""Какие модели есть на локальном OpenAI-совместимом сервере (LM Studio,
Ollama, vLLM, llama.cpp) и какое у модели окно контекста — для «Найти модели»
и «Проверить» в настройках и для нарезки встречи на куски при анализе.

Список — `GET {base_url}/models` (адрес без `/v1` — `{base_url}/v1/models`);
нет его — родной список Ollama `GET /api/tags`. Модели не вызываются. Только
stdlib: модуль зовёт резидент. Прокси — по правилу локальной модели
(`openai_compat.open_url`: свои адреса — напрямую, если не включено «через
прокси»). Короткий таймаут: окно ждёт ответа.

Размеры моделей отдаёт только Ollama (`/api/tags`: байты и число параметров) —
у сервера Ollama список `/v1/models` дополняется ими; vLLM сообщает длину
контекста (`max_model_len`). Имя `qwen3` и `qwen3:latest` у Ollama — одна
модель (`same_model`).

Окно контекста (`context_length`): Ollama — обученное окно модели
(`/api/show`; на запрос анализ задаёт его сам, не больше 32K), LM Studio —
`/api/v0/models` (загруженное окно, иначе наибольшее), vLLM —
`max_model_len`, llama.cpp — `/props` (`n_ctx`).
"""

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from meet.llm.openai_compat import OLLAMA_MAX_CTX, open_url

TIMEOUT_S = 3.0
# Окно контекста (токены): меньше MIN_CONTEXT анализ встречи не берётся (в
# кусок влезло бы несколько реплик), меньше GOOD_CONTEXT — работает, но
# встреча режется на много кусков.
MIN_CONTEXT = 6144
GOOD_CONTEXT = 16384
_OLLAMA_PORT = 11434
# Модели эмбеддингов (LM Studio и Ollama показывают их в общем списке) в чат не годятся.
_EMBEDDING_MARKERS = ("embed",)
# На процесс: сервер по адресу — Ollama или нет; окно контекста модели.
_ollama: dict[str, bool] = {}
_trained: dict[tuple[str, str], int | None] = {}


class _NotList(ValueError):
    """Ответ есть, но это не список моделей."""


def _fail(reason: str, error: str, url: str | None = None, *, timeout: bool = False) -> dict:
    return {"ok": False, "reason": reason, "error": error, "models": [], "url": url,
            "source": None, "missing": False, "warning": None, "timeout": timeout}


def model_key(name: str) -> str:
    """Имя для сравнения: у Ollama `qwen3` — это `qwen3:latest`."""
    name = (name or "").strip()
    if not name:
        return ""
    tail = name.rsplit("/", 1)[-1]
    return name if ":" in tail else f"{name}:latest"


def same_model(a: str, b: str) -> bool:
    return bool(model_key(a)) and model_key(a) == model_key(b)


def models_url(base_url: str) -> str:
    """Адрес списка: `…/v1` → `…/v1/models`, без `/v1` — `…/v1/models`."""
    root = base_url.strip().rstrip("/")
    return f"{root}/models" if root.endswith("/v1") else f"{root}/v1/models"


def server_root(base_url: str) -> str:
    """`http://host:port` адреса сервера — для родных путей (`/api/…`, `/props`)."""
    u = urlparse(base_url.strip())
    return f"{u.scheme}://{u.netloc}"


def _tags_url(base_url: str) -> str:
    return f"{server_root(base_url)}/api/tags"


def _get(url: str, timeout: float, via_proxy: bool = False, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Accept": "application/json", "Content-Type": "application/json"})
    with open_url(req, timeout, via_proxy) as resp:
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


def _looks_ollama(base_url: str, models: list[dict]) -> bool:
    try:
        port = urlparse(base_url).port
    except ValueError:
        port = None
    return port == _OLLAMA_PORT or any(m.get("_owner") == "library" for m in models)


def is_ollama(base_url: str, *, via_proxy: bool = False, timeout: float = TIMEOUT_S) -> bool:
    """Сервер — Ollama (отвечает на `GET /api/version`); запоминается на процесс."""
    root = server_root(base_url)
    if root not in _ollama:
        try:
            body = _get(f"{root}/api/version", timeout, via_proxy)
            _ollama[root] = isinstance(body, dict) and isinstance(body.get("version"), str)
        except (_NotList, urllib.error.URLError, OSError, ValueError):
            _ollama[root] = False
    return _ollama[root]


def ollama_trained_context(base_url: str, model: str, *, via_proxy: bool = False,
                           timeout: float = TIMEOUT_S) -> int | None:
    """Обученное окно контекста модели Ollama (`/api/show` → `model_info`
    `<архитектура>.context_length`); не узнать — None."""
    key = (server_root(base_url), model_key(model))
    if key not in _trained:
        found = None
        try:
            body = _get(f"{key[0]}/api/show", timeout, via_proxy, body={"model": model})
            info = body.get("model_info") if isinstance(body, dict) else None
            if isinstance(info, dict):
                found = next((_int(v) for k, v in info.items()
                              if str(k).endswith(".context_length") and _int(v)), None)
        except (_NotList, urllib.error.URLError, OSError, ValueError):
            found = None
        _trained[key] = found
    return _trained[key]


def _lmstudio_context(base_url: str, model: str, via_proxy: bool, timeout: float) -> int | None:
    body = _get(f"{server_root(base_url)}/api/v0/models", timeout, via_proxy)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None
    items = [m for m in data if isinstance(m, dict) and m.get("type", "llm") in ("llm", "vlm")]
    item = next((m for m in items if same_model(str(m.get("id") or ""), model)), None)
    if item is None:
        loaded = [m for m in items if m.get("state") == "loaded"]
        item = loaded[0] if len(loaded) == 1 else None
    if item is None:
        return None
    return (_int(item.get("loaded_context_length")) or _int(item.get("context_length"))
            or _int(item.get("max_context_length")))


def _vllm_context(base_url: str, model: str, via_proxy: bool, timeout: float) -> int | None:
    models = _from_openai(_get(models_url(base_url), timeout, via_proxy))
    item = next((m for m in models if same_model(m["id"], model)), models[0] if len(models) == 1 else None)
    return item["context"] if item else None


def _llamacpp_context(base_url: str, model: str, via_proxy: bool, timeout: float) -> int | None:
    body = _get(f"{server_root(base_url)}/props", timeout, via_proxy)
    if not isinstance(body, dict):
        return None
    settings = body.get("default_generation_settings")
    return _int(body.get("n_ctx")) or (_int(settings.get("n_ctx")) if isinstance(settings, dict) else None)


def context_length(base_url: str, model: str | None, *, via_proxy: bool = False,
                   timeout: float = TIMEOUT_S) -> dict:
    """Окно контекста модели → {"tokens": int|None, "source": "ollama"|"lmstudio"|
    "vllm"|"llamacpp"|None}. Ollama — сколько анализ сможет попросить на запрос
    (обученное окно, не больше OLLAMA_MAX_CTX); остальные — окно сервера."""
    name = (model or "").strip()
    try:
        if is_ollama(base_url, via_proxy=via_proxy, timeout=timeout):
            trained = ollama_trained_context(base_url, name, via_proxy=via_proxy, timeout=timeout)
            return {"tokens": min(trained or OLLAMA_MAX_CTX, OLLAMA_MAX_CTX), "source": "ollama"}
    except ValueError:
        return {"tokens": None, "source": None}
    for source, read in (("vllm", _vllm_context), ("lmstudio", _lmstudio_context),
                         ("llamacpp", _llamacpp_context)):
        try:
            tokens = read(base_url, name, via_proxy, timeout)
        except (_NotList, urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            continue
        if tokens:
            return {"tokens": tokens, "source": source}
    return {"tokens": None, "source": None}


def context_text(tokens: int | None) -> str:
    """Окно контекста для человека («Проверить», отказ анализа)."""
    if not tokens:
        return "окно контекста модели узнать не удалось"
    if tokens < MIN_CONTEXT:
        return (f"у модели слишком маленькое окно контекста: {tokens} токенов — для анализа встречи "
                "увеличьте до 16K+")
    if tokens < GOOD_CONTEXT:
        return f"окно контекста модели: {tokens} токенов — для длинных встреч лучше 16K+"
    return f"окно контекста модели: {tokens} токенов"


def _unreachable(base_url: str, e: BaseException, timeout: float) -> dict:
    reason = getattr(e, "reason", e)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return _fail("unreachable", f"Сервер не ответил за {timeout:g} с: {base_url}", timeout=True)
    return _fail("unreachable", f"Сервер не отвечает: {base_url} — запущены ли LM Studio "
                                "(сервер включён) или Ollama?")


def _auth(code: int, url: str) -> dict:
    return _fail("auth", f"Сервер требует ключ доступа (HTTP {code}): Meet подключается к "
                         "локальной модели без ключа — выключите проверку ключа в настройках сервера", url)


def list_models(base_url: str, model: str | None = None, timeout: float = TIMEOUT_S,
                via_proxy: bool = False) -> dict:
    """Модели сервера → {"ok", "models": [{"id", "size", "params", "context"}],
    "source": "openai"|"ollama", "url", "error", "reason", "missing", "warning",
    "timeout"}.

    `reason` при ошибке: `bad_url`, `unreachable` (не отвечает; `timeout` —
    не уложился в срок), `auth` (401/403), `not_openai` (отвечает, но списка
    моделей нет), `empty`. `model` — выбранная модель: нет её в списке —
    `missing` и `warning` (`qwen3` и `qwen3:latest` — одна модель)."""
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
        models = _from_openai(_get(url, timeout, via_proxy))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return _auth(e.code, url)
    except _NotList:
        pass
    except (urllib.error.URLError, OSError) as e:
        return _unreachable(base, e, timeout)
    tags = None
    if models is None or _looks_ollama(base, models):
        try:
            tags = _from_ollama(_get(_tags_url(base), timeout, via_proxy))
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
        extra = {model_key(t["id"]): t for t in tags}
        models = [{**m, "size": m["size"] or extra.get(model_key(m["id"]), {}).get("size"),
                   "params": extra.get(model_key(m["id"]), {}).get("params")} for m in models]
    models = [{k: v for k, v in m.items() if not k.startswith("_")} for m in models]
    chat = [m for m in models if not any(x in m["id"].lower() for x in _EMBEDDING_MARKERS)]
    if not chat:
        error = ("На сервере только модели эмбеддингов — загрузите языковую модель" if models else
                 "Сервер отвечает, но моделей нет: загрузите модель в LM Studio или скачайте в Ollama "
                 "(ollama pull …)")
        return {**_fail("empty", error, url), "source": source}
    wanted = (model or "").strip()
    missing = bool(wanted) and not any(same_model(wanted, m["id"]) for m in chat)
    return {"ok": True, "reason": None, "error": None, "models": chat, "url": url, "source": source,
            "missing": missing, "timeout": False,
            "warning": f"Модели «{wanted}» на сервере нет — выберите другую из списка" if missing else None}
