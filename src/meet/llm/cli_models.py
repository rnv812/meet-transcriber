"""Списки моделей и уровней рассуждений из самих CLI (0.6.2) — для окна
настроек («Модели и анализ») и `/model` ассистента.

Ответ одного вида у всех провайдеров:
`{"models": [{id, label, description?, default?, efforts: [{id, label?,
description?}], default_effort?}], "error"?, "login"?}` — ошибка списка
текстом (окно тогда предлагает ввести имя вручную), `login` — команда входа.

* Codex — `codex app-server` → `model/list` (`codex_app.list_models`: id,
  displayName, isDefault, defaultReasoningEffort, supportedReasoningEfforts,
  без скрытых `hidden`); запасной путь — `codex debug models` (JSON `models:
  [{slug, display_name, visibility: list|hide, default_reasoning_level,
  supported_reasoning_levels: [{effort, description}], priority}]`).
  Поля сверены с `codex app-server generate-json-schema` (0.160.0) и живым
  ответом на этой машине.
* Antigravity CLI — ответ служебного `session/new` в своей папке данных Meet
  (`antigravity.list_models`): `models` и/или `configOptions` ACP. Нет входа —
  ошибка с командой входа.
* Claude Code — постоянный список (sonnet, opus, haiku и модель из настроек),
  уровни — `settings.CLAUDE_EFFORTS`.
* OpenCode — `opencode models` (строки «провайдер/модель»), уровней нет.
* Локальная — `local_models.list_models` по сохранённым адресу и модели.

Кэш — на CACHE_TTL_S (ошибки — на ERROR_TTL_S) по провайдеру и его
настройкам; `refresh` — спросить CLI заново. В журналы — ничего: списки
моделей не секрет, но и не нужны."""

import json
import re
import subprocess
import threading
import time

from meet.llm import PROVIDERS

CACHE_TTL_S = 300.0
ERROR_TTL_S = 30.0
CODEX_DEBUG_TIMEOUT_S = 30.0
OPENCODE_TIMEOUT_S = 60.0
CLAUDE_MODELS = (("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku"))
# Имя уровня рассуждений из CLI (как `settings._CLI_EFFORT`): остальное не показываем —
# его нельзя было бы сохранить.
_EFFORT = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _efforts(items, key: str) -> list[dict]:
    out = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        value = item.get(key)
        if isinstance(value, str) and _EFFORT.fullmatch(value) and not any(e["id"] == value for e in out):
            desc = str(item.get("description") or "").strip()
            out.append({"id": value, **({"description": desc} if desc else {})})
    return out


def _entry(mid: str, label, description, default: bool, efforts: list[dict], default_effort) -> dict:
    entry = {"id": mid, "label": str(label or mid), "default": bool(default), "efforts": efforts}
    if isinstance(description, str) and description.strip():
        entry["description"] = description.strip()
    if isinstance(default_effort, str) and any(e["id"] == default_effort for e in efforts):
        entry["default_effort"] = default_effort
    return entry


def parse_codex_model_list(data) -> list[dict]:
    """Записи `model/list` (app-server) → модели окна; скрытые и имена вне
    правил `codex.model_error` пропускаются."""
    from meet.llm import codex

    out: list[dict] = []
    for item in data or ():
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        mid = item.get("model") or item.get("id")
        if not isinstance(mid, str) or not mid.strip() or codex.model_error(mid):
            continue
        if any(m["id"] == mid for m in out):
            continue
        out.append(_entry(mid, item.get("displayName"), item.get("description"), item.get("isDefault") is True,
                          _efforts(item.get("supportedReasoningEfforts"), "reasoningEffort"),
                          item.get("defaultReasoningEffort")))
    return out


def parse_codex_debug(data) -> list[dict]:
    """Вывод `codex debug models` (JSON) → модели окна: только `visibility:
    list`, по `priority`. Модели по умолчанию этот вывод не называет."""
    from meet.llm import codex

    items = data.get("models") if isinstance(data, dict) else None
    shown = [m for m in items or () if isinstance(m, dict) and m.get("visibility") == "list"]
    shown.sort(key=lambda m: m.get("priority") if isinstance(m.get("priority"), (int, float)) else 1e9)
    out: list[dict] = []
    for item in shown:
        mid = item.get("slug")
        if not isinstance(mid, str) or not mid.strip() or codex.model_error(mid) or any(m["id"] == mid for m in out):
            continue
        out.append(_entry(mid, item.get("display_name"), item.get("description"), False,
                          _efforts(item.get("supported_reasoning_levels"), "effort"),
                          item.get("default_reasoning_level")))
    return out


def _run(cmd: list[str], timeout: float, env: dict | None) -> tuple[int, bytes, bytes]:
    """Запуск CLI с гашением дерева процессов по сроку (тесты подменяют)."""
    from meet.llm.base import run_tree

    return run_tree(cmd, timeout=timeout, env=env)


def codex_models(proxy: str | None) -> dict:
    from meet import netproxy
    from meet.llm import codex, codex_app
    from meet.llm.base import drop_session_markers

    exe = codex.find_codex()
    if exe is None:
        return {"models": [], "error": "не найден Codex CLI (codex)"}
    env = netproxy.child_env(proxy)
    drop_session_markers(env)
    try:
        models = parse_codex_model_list(codex_app.list_models(codex_app.command(exe, []), env=env))
        if models:
            return {"models": models}
        why = "Codex не сообщил ни одной модели"
    except codex_app.Unavailable as e:
        why = f"codex app-server: {e.reason}"
    # Запасной путь — `codex debug models` (без сети: каталог самого CLI).
    try:
        code, out, err = _run([exe, "debug", "models"], CODEX_DEBUG_TIMEOUT_S, env)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"models": [], "error": f"{why}; codex debug models: {type(e).__name__}"}
    if code == 0:
        try:
            models = parse_codex_debug(json.loads(out.decode("utf-8", errors="replace")))
        except ValueError:
            models = []
        if models:
            return {"models": models}
    tail = err.decode("utf-8", errors="replace").strip()[-300:] if code else ""
    return {"models": [], "error": f"{why}; codex debug models: {tail or 'список пуст'}"}


def agy_models(proxy: str | None) -> dict:
    from meet.llm import antigravity
    from meet.llm.detect import AGY_NOT_FOUND, find_agy

    exe = find_agy()
    if exe is None:
        return {"models": [], "error": AGY_NOT_FOUND}
    return antigravity.list_models(exe, proxy)


def claude_models(current: str) -> dict:
    from meet.settings import CLAUDE_EFFORTS

    efforts = [{"id": e} for e in CLAUDE_EFFORTS]
    models = [_entry(mid, label, None, mid == "sonnet", list(efforts), None) for mid, label in CLAUDE_MODELS]
    current = (current or "").strip()
    if current and not any(m["id"] == current for m in models):
        models.append(_entry(current, current, "из настроек", False, list(efforts), None))
    return {"models": models}


def parse_opencode(out: str) -> list[dict]:
    from meet.settings import opencode_model_error

    out_models: list[dict] = []
    for line in _ANSI.sub("", out).splitlines():
        name = line.strip()
        if name and "/" in name and opencode_model_error(name) is None and not any(
                m["id"] == name for m in out_models):
            out_models.append(_entry(name, name, None, False, [], None))
    return out_models


def opencode_models(proxy: str | None) -> dict:
    from meet import netproxy
    from meet.llm import detect

    path = detect.find_opencode()
    if path is None:
        return {"models": [], "error": detect.OPENCODE_NOT_FOUND}
    env = netproxy.child_env(proxy)
    env["NO_COLOR"] = "1"
    try:
        code, out, err = _run([path, "models"], OPENCODE_TIMEOUT_S, env)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"models": [], "error": f"OpenCode не ответил на opencode models: {type(e).__name__}"}
    if code != 0:
        tail = _ANSI.sub("", err.decode("utf-8", errors="replace")).strip()[-300:]
        return {"models": [], "error": tail or f"opencode models: код выхода {code}"}
    models = parse_opencode(out.decode("utf-8", errors="replace"))
    return {"models": models} if models else {"models": [], "error": "OpenCode не назвал ни одной модели"}


def local_models_of(cfg) -> dict:
    from meet.llm import local_models, local_route

    got = local_models.list_models(cfg.llm.base_url, cfg.llm.local_model, via_proxy=local_route(cfg))
    if not got.get("ok"):
        return {"models": [], "error": got.get("error") or "сервер локальной модели не ответил"}
    return {"models": [_entry(str(m["id"]), m["id"], None, False, [], None)
                       for m in got.get("models") or () if isinstance(m, dict) and m.get("id")]}


def _key(provider: str, cfg) -> tuple:
    if provider == "claude-code":
        return (provider, cfg.llm.model)
    if provider == "openai-compatible":
        return (provider, cfg.llm.base_url, cfg.llm.local_model, cfg.llm.local_via_proxy, cfg.llm.proxy)
    return (provider, cfg.llm.proxy)


def _fetch(provider: str, cfg) -> dict:
    if provider == "codex":
        return codex_models(cfg.llm.proxy)
    if provider == "agy":
        return agy_models(cfg.llm.proxy)
    if provider == "claude-code":
        return claude_models(cfg.llm.model)
    if provider == "opencode":
        return opencode_models(cfg.llm.proxy)
    return local_models_of(cfg)


def list_for(provider: str, cfg, refresh: bool = False) -> dict:
    """Модели и уровни провайдера (см. модуль). Неизвестный провайдер — ValueError.
    Один опрос CLI на провайдера за раз: второй запрос ждёт и берёт кэш."""
    if provider not in PROVIDERS:
        raise ValueError(f"неизвестный провайдер: {provider}")
    key = _key(provider, cfg)
    with _cache_lock:
        lock = _locks.setdefault(provider, threading.Lock())
    with lock:
        now = time.monotonic()
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and not refresh:
            at, value = hit
            if now - at < (ERROR_TTL_S if value.get("error") else CACHE_TTL_S):
                return {**value, "cached": True}
        try:
            value = _fetch(provider, cfg)
        except Exception as e:  # список не должен ронять маршрут трейсбеком
            value = {"models": [], "error": f"{type(e).__name__}: {e}"}
        with _cache_lock:
            _cache[key] = (time.monotonic(), value)
        return dict(value)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
