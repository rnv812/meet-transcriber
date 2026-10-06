"""Провайдер локальной модели через OpenAI-совместимый API (LM Studio, Ollama, vLLM).

Только stdlib (urllib). Инструментов нет: базу знаний такая модель не читает,
`allowed_dirs`/`cwd`/`session_id`/`max_turns`/`images` игнорируются (`resume` —
ошибка `resume_failed`: сеансов нет). Имя модели —
`llm.local_model` из настроек (его подставляет `meet.llm.resolve`).

Прокси: локальная модель через прокси не ходит (как NO_PROXY у детей, см.
meet.netproxy) — ни на этом компьютере, ни в домашней или офисной сети
(частные адреса 10/8, 172.16/12, 192.168/16, CGNAT 100.64/10 — Tailscale,
link-local, имена без точки и `*.local`): прокси такого адреса обычно не
видит. Где сервер модели доступен только через прокси, есть настройка
`llm.local_via_proxy` («Локальную модель — через прокси»; `via_proxy` здесь:
True — прокси системы, строка — свой адрес из `llm.proxy`, см.
`meet.llm.local_route`). Остальные адреса — как у urllib.

Длина ответа — по назначению вызова (`purpose`, `REPLY_BUDGET`: название,
тик живого ассистента, ответ, итоги) или своя у шага (`max_tokens`: анализ,
улучшение). Окно контекста известно — предел ответа не больше того, что в
окне остаётся после промпта; не остаётся и минимума — ошибка «не
помещается» сразу, без вызова. Без предела зациклившаяся модель (бесконечные
пробелы в ответе по схеме у llama.cpp и Ollama) пишет до таймаута, а vLLM
отвергает промпт + `max_tokens` больше окна.

Ollama — каждый вызов идёт в родной `POST /api/chat` (`_ollama_chat`): только
там можно задать окно контекста на запрос (`options.num_ctx`; маршрут `/v1`
его не берёт, и Ollama молча обрезает длинный промпт до своего окна по
умолчанию — 2–4 тыс. токенов), длину ответа (`num_predict`) и схему
(`format`). Окно — промпт + ответ этого вызова, ступенями 4K/8K/16K/32K, и
на модель в процессе не уменьшается (`_num_ctx`): Ollama перезагружает
модель при смене окна, а тики живого ассистента идут подряд. Окно у разных
процессов (задача анализа, живой ассистент) своё — при чередовании Ollama
модель перезагрузит. Старая Ollama без `/api/chat` (404/405) — путь `/v1`.

Ответ по схеме (`response_schema`, анализ встречи) у остальных серверов —
`response_format` от строгого к свободному: `json_schema` (LM Studio, vLLM,
llama.cpp), сервер его не понял — `json_object`, затем без него (схема
остаётся в промпте). Сработавший режим запоминается на процесс.

Сервер насчитал промпту намного меньше токенов, чем в нём есть, и при этом
заполнил окно (`usage.prompt_tokens`) — промпт обрезан: ошибка (анализ сам
отмечает это в analysis.json: `on_cut="keep"`, обрезка — в `usage["cut"]`).
Переполнение контекста (vLLM, llama.cpp отвечают на него 400) — не «формат
не понят»: ошибка сразу, без лишних вызовов.
"""

import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from meet.llm.base import EMPTY_ERROR, TIMEOUT_ERROR, AgentReply, resume_failure
from meet.settings import DEFAULT_LOCAL_BASE_URL

log = logging.getLogger(__name__)

DEFAULT_MODEL = "local-model"
DEFAULT_MAX_TOKENS = 8192
_ERR_LIMIT = 500
CONTEXT_ERROR = "текст не помещается в контекст модели"
# Ollama: больше этого окна контекста на запрос не просим (память).
OLLAMA_MAX_CTX = 32768
# Оценка размера текста в токенах — с запасом: русский текст у моделей с
# большим словарём ~3–3,5 символа на токен, у моделей со словарём 32K
# (Mistral-7B, Llama-2) ~2–2,3; считаем по 2,5 — окно скорее больше нужного.
CHARS_PER_TOKEN = 2.5
# Предел ответа по назначению вызова; что не получится, если не влезет.
REPLY_BUDGET = {"title": 256, "tick": 600, "answer": 1500, "summary": 3000}
WHAT = {"title": "название", "tick": "подсказки", "answer": "ответ", "summary": "итоги",
        "summary_part": "итоги",
        "improve": "улучшение расшифровки", "analysis": "анализ"}
# Меньше этого на ответ в окне не оставляем; запас окна на шаблон чата.
REPLY_FLOOR = {"title": 64, "tick": 256, "answer": 400, "summary": 800, "summary_part": 400}
DEFAULT_FLOOR = 128
WINDOW_MARGIN = 64
# Влезет ли запрос — по оптимистичной оценке (у моделей с большим словарём
# ~3–3,5 символа на токен): иначе отказ длинным встречам, которые влезают.
# Предел ответа — по осторожной (CHARS_PER_TOKEN) с запасом CLAMP_MARGIN:
# vLLM отвергает промпт + max_tokens больше окна.
FIT_CHARS_PER_TOKEN = 3.0
CLAMP_MARGIN = 1.05
# Рассуждающие модели (Qwen3, DeepSeek-R1, gpt-oss, QwQ): рассуждение
# тратит предел ответа. Ollama — `think: false` (кроме анализа); остальным —
# запас к пределу и подсказка шаблону чата выключить рассуждение.
REASONING_ALLOWANCE = 2048
_REASONING_NAMES = re.compile(r"qwen3|deepseek-r1|gpt-oss|qwq|thinking", re.I)
# Ответ упёрся в предел: что сказать в конце (итоги, ответ); название и тик — не годятся.
LENGTH_NOTE = {"summary": "_Итоги обрезаны: модели не хватило места для ответа._",
               "answer": "(Ответ обрезан: модели не хватило места для ответа.)"}
LENGTH_ERROR = "ответ модели оборван: не хватило места для ответа"

# Режимы ответа по схеме — от строгого к свободному; None — без response_format.
SCHEMA_MODES = ("json_schema", "json_object", None)
# (адрес, модель) → режим, который сервер принял (на процесс задачи).
_schema_mode: dict[tuple[str, str], str | None] = {}
# Признаки «не понял response_format» в тексте ошибки.
_FORMAT_MARKERS = ("response_format", "json_schema", "json_object", "schema", "grammar", "guided")
# Признаки «промпт не помещается в контекст» (vLLM, llama.cpp, LM Studio, OpenAI).
_CONTEXT_MARKERS = ("context length", "context_length", "maximum context", "context size",
                    "context window", "n_ctx", "exceed_context", "exceeds the available context",
                    "context overflow", "too many tokens", "prompt is too long",
                    "reduce the length")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
# Неудачные сведения о сервере (таймаут, нет связи) помним недолго: в живом
# ассистенте один сбой не должен до конца встречи увести вызовы не туда.
FAIL_TTL_S = 30.0


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


def open_url(req: urllib.request.Request, timeout: float, via_proxy: bool | str = False):
    """urlopen по правилу прокси выше: локальные адреса — напрямую, если не
    включено «через прокси» (`via_proxy`: True — прокси системы и переменных
    среды, строка — этот адрес)."""
    if not via_proxy and direct(req.full_url):
        return _DIRECT.open(req, timeout=timeout)
    # Свой opener на вызов, а не общий urlopen: тот запоминает прокси при
    # первом вызове, а задача модели задаёт их переменными позже (netproxy.prepare).
    proxies = {"http": via_proxy, "https": via_proxy} if isinstance(via_proxy, str) else None
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies)).open(req, timeout=timeout)


def tokens_of(chars: int) -> int:
    """Оценка числа токенов текста (с запасом, см. CHARS_PER_TOKEN)."""
    return int(chars / CHARS_PER_TOKEN)


def _overflow(detail: str) -> bool:
    low = detail.lower()
    return any(m in low for m in _CONTEXT_MARKERS)


def _request(url: str, payload: dict, timeout_s: float, via_proxy: bool | str):
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
    """Текст ответа без рассуждения `<think>` (jsonreply.strip_reasoning); всё
    ушло на рассуждение — ошибка «не закончила рассуждение»."""
    from meet.llm.jsonreply import UNFINISHED, strip_reasoning

    text, unfinished = strip_reasoning(text if isinstance(text, str) else "")
    if not text:
        return AgentReply(text="", error=UNFINISHED if unfinished else EMPTY_ERROR, usage=usage)
    return AgentReply(text=text, usage=usage)


def _post(url: str, payload: dict, timeout_s: float, via_proxy: bool | str = False
          ) -> tuple[AgentReply, int | None, str]:
    """`/chat/completions` → (ответ, код HTTP-ошибки или None, текст ошибки сервера)."""
    body, failed, status, detail = _request(url, payload, timeout_s, via_proxy)
    if failed is not None:
        return failed, status, detail
    usage = dict(body["usage"]) if isinstance(body, dict) and isinstance(body.get("usage"), dict) else None
    try:
        choice = body["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return AgentReply(text="", error="неожиданный ответ локальной модели"), None, ""
    if isinstance(choice, dict) and choice.get("finish_reason") == "length":
        usage = {**(usage or {}), "length": True}
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
    need = int((tokens_of(prompt_chars) + max_tokens) * 1.1)
    return max(2048, min(need, trained or OLLAMA_MAX_CTX, OLLAMA_MAX_CTX))


# Ступени окна Ollama и уже выбранное окно модели (адрес, модель) → num_ctx.
_CTX_STEPS = (4096, 8192, 16384, 32768)
_num_ctx: dict[tuple[str, str], int] = {}
# Сервер по адресу — Ollama без родного /api/chat (старая): дальше — /v1.
_no_native: set[str] = set()


def sticky_num_ctx(key: tuple[str, str], need: int, cap: int) -> int:
    """Окно на запрос: ступень не меньше `need`, не меньше уже выбранного для
    модели (Ollama не перезагружает модель) и не больше `cap`."""
    step = next((s for s in _CTX_STEPS if s >= need), _CTX_STEPS[-1])
    value = min(cap, max(step, _num_ctx.get(key, 0)))
    _num_ctx[key] = value
    return value


def _ollama_chat(base_url: str, payload: dict, timeout_s: float, schema: dict | None, max_tokens: int,
                 via_proxy: bool | str, think_off: bool = False) -> AgentReply | None:
    """Любой вызов локальной модели на Ollama — родной `/api/chat` с окном
    контекста по промпту и ответу; сервер не Ollama (или Ollama без
    `/api/chat`) — None: тогда `/v1`. Окно — в `usage["window"]`."""
    from meet.llm import local_models

    root = local_models.server_root(base_url)
    if root in _no_native or not local_models.is_ollama(base_url, via_proxy=via_proxy):
        return None
    model = str(payload.get("model"))
    trained = local_models.ollama_trained_context(base_url, model, via_proxy=via_proxy)
    chars = sum(len(m.get("content") or "") for m in payload["messages"])
    cap = min(trained or OLLAMA_MAX_CTX, OLLAMA_MAX_CTX)
    num_ctx = sticky_num_ctx((root, local_models.model_key(model)), ollama_num_ctx(chars, max_tokens, trained), cap)
    body = {"model": model, "messages": payload["messages"], "stream": False,
            "options": {"num_ctx": num_ctx, "num_predict": max_tokens}}
    if schema is not None:
        body["format"] = schema
    if think_off:
        body["think"] = False  # рассуждающая модель (capabilities: thinking) — без рассуждения
    data, failed, status, detail = _request(f"{root}/api/chat", body, timeout_s, via_proxy)
    if failed is not None and status in (404, 405) and "model" not in detail.lower():
        # Старая Ollama (или прокси, который пропускает только часть путей).
        _no_native.add(root)
        log.info("Ollama по адресу %s без /api/chat (HTTP %s) — вызовы идут через /v1", root, status)
        return None
    if failed is not None and status == 400 and schema is not None and "format" in detail.lower():
        # Ollama до 0.5 не берёт схему в `format` — только "json".
        data, failed, _status, _detail = _request(f"{root}/api/chat", {**body, "format": "json"},
                                                  timeout_s, via_proxy)
    if failed is not None:
        return failed
    if schema is not None:
        _schema_mode[(f"{root}/api/chat", model)] = "ollama"
    message = data.get("message") if isinstance(data, dict) else None
    count = data.get("prompt_eval_count") if isinstance(data, dict) else None
    usage = {"window": num_ctx, "ollama": True}
    if isinstance(count, int):
        usage.update(prompt_tokens=count, completion_tokens=data.get("eval_count"))
    if isinstance(data, dict) and data.get("done_reason") == "length":
        usage["length"] = True
    return _reply(message.get("content") if isinstance(message, dict) else None, usage)


# (адрес, модель) → (окно контекста модели или None, когда забыть; None — никогда).
_context: dict[tuple[str, str], tuple[int | None, float | None]] = {}
# Промпт не короче этого (токенов по оценке) проверяется на обрезку: у
# коротких шаблон чата добавляет больше, чем ошибается оценка.
CUT_MIN_TOKENS = 1000
CUT_SHARE = 0.5
# Обрезанный промпт заполняет окно: сервер, считающий только не кэшированные
# токены (общее начало промпта у тиков), под это не попадает.
CUT_FILL = 0.9


def known_context(base_url: str, model: str, via_proxy: bool | str = False) -> int | None:
    """Окно контекста модели (`local_models.context_length`): узнанное —
    раз на процесс (живому ассистенту без лишних запросов на тик), не
    узнанное — снова через FAIL_TTL_S."""
    from meet.llm import local_models

    key = (base_url.rstrip("/"), local_models.model_key(model))
    cached = _context.get(key)
    if cached is not None and (cached[1] is None or cached[1] > time.monotonic()):
        return cached[0]
    try:
        tokens = local_models.context_length(base_url, model, via_proxy=via_proxy)["tokens"]
    except Exception:  # сведения необязательные
        tokens = None
    _context[key] = (tokens, None if tokens else time.monotonic() + FAIL_TTL_S)
    return tokens


def prompt_cut(usage: dict | None, prompt_chars: int, window: int | None = None,
               max_tokens: int = 0) -> dict | None:
    """Сервер обрезал промпт: насчитал ему меньше половины оценки и (если окно
    известно) заполнил окно → {"seen", "need", "window"}; иначе — None."""
    seen = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    need = tokens_of(prompt_chars)
    if not isinstance(seen, int) or isinstance(seen, bool) or need < CUT_MIN_TOKENS or seen >= need * CUT_SHARE:
        return None
    if window and seen < CUT_FILL * max(window - max_tokens, window // 2):
        return None  # окно не заполнено — сервер просто считает иначе (кэш начала)
    return {"seen": seen, "need": need, "window": window}


def cut_advice(ollama: bool, capped: bool = False) -> str:
    """Что делать, если встреча не влезает. У Ollama окно ставит сам Meet:
    упёрлись в его предел (`capped`: модель умеет больше OLLAMA_MAX_CTX) — так и
    сказать; упёрлись в окно самой модели — нужна модель с бо́льшим окном."""
    if ollama and capped:
        return f"встреча длиннее окна, которое Meet запрашивает у Ollama ({OLLAMA_MAX_CTX // 1024}K)"
    if ollama:
        return "окно этой модели меньше нужного — возьмите модель с бо́льшим окном контекста"
    return "увеличьте контекст модели до 16K+"


def cut_error(cut: dict, ollama: bool = False, capped: bool = False) -> str:
    return (f"модель видела только часть текста: ~{cut['seen']} из ~{cut['need']} токенов — "
            f"{cut_advice(ollama, capped)}")


def fit_error(purpose: str | None, context: int, ollama: bool, capped: bool = False) -> str:
    """Промпт с минимальным ответом не влезает в окно — что не получится и почему."""
    what = WHAT.get(purpose or "", "ответ")
    return (f"текст не помещается в окно контекста модели ({context} токенов): {what} не получить — "
            f"{cut_advice(ollama, capped)}")


def fits(chars: int, context: int, purpose: str | None = None) -> bool:
    """Влезет ли промпт из `chars` символов с минимальным ответом `purpose` в
    окно `context` (оптимистичная оценка, как у отказа в `_complete`): итоги
    решают по ней, резать ли встречу на части."""
    floor = REPLY_FLOOR.get(purpose or "", DEFAULT_FLOOR)
    return context - int(chars / FIT_CHARS_PER_TOKEN) - WINDOW_MARGIN >= floor


def _reasons_on_ollama(base_url: str, model: str, via_proxy: bool | str) -> bool:
    from meet.llm import local_models

    caps = local_models.ollama_show(base_url, model, via_proxy=via_proxy)["capabilities"]
    return "thinking" in caps if caps else reasons_by_name(model)


def reasons_by_name(model: str) -> bool:
    """По имени — рассуждающая модель (Qwen3, DeepSeek-R1, gpt-oss, QwQ, *-thinking)."""
    return bool(_REASONING_NAMES.search(model or ""))


def _complete(base_url: str, payload: dict, timeout_s: float, schema: dict | None, max_tokens: int | None,
              via_proxy: bool | str, on_cut: str = "error", purpose: str | None = None) -> AgentReply:
    """Один вызов: предел ответа по назначению (с запасом на рассуждение) и по
    окну; не влезает и минимум — ошибка без вызова; Ollama — родной путь;
    промпт обрезан сервером — ошибка (`on_cut="keep"` — ответ как есть,
    обрезка — в `usage["cut"]`); ответ упёрся в предел — пометка или ошибка."""
    from meet.llm import local_models

    model = str(payload.get("model"))
    chars = sum(len(m.get("content") or "") for m in payload["messages"])
    budget = max_tokens or REPLY_BUDGET.get(purpose or "", DEFAULT_MAX_TOKENS)
    floor = min(REPLY_FLOOR.get(purpose or "", DEFAULT_FLOOR), budget)
    context = known_context(base_url, model, via_proxy)
    ollama = local_models.is_ollama(base_url, via_proxy=via_proxy)
    trained = local_models.ollama_trained_context(base_url, model, via_proxy=via_proxy) if ollama else None
    # Предел Meet (32K) виноват, только когда модель точно умеет больше.
    capped = ollama and trained is not None and trained > OLLAMA_MAX_CTX
    # Рассуждение: Ollama его выключает (`think: false`), кроме анализа; там и
    # у остальных серверов (подсказка шаблону не везде действует) — запас.
    # Старая Ollama без `capabilities` — по имени; gpt-oss булево `think` не
    # слушает (только уровни усилия) — запас ему всегда.
    reasoning = _reasons_on_ollama(base_url, model, via_proxy) if ollama else reasons_by_name(model)
    think_off = reasoning and purpose != "analysis"
    if reasoning and (not (ollama and think_off) or "gpt-oss" in model.lower()):
        budget += REASONING_ALLOWANCE
    if context:
        if context - int(chars / FIT_CHARS_PER_TOKEN) - WINDOW_MARGIN < floor:
            return AgentReply(text="", error=fit_error(purpose, context, ollama, capped))
        room = context - int(tokens_of(chars) * CLAMP_MARGIN) - WINDOW_MARGIN
        budget = max(floor, min(budget, room))
    reply = _send(base_url, {**payload, "max_tokens": budget}, timeout_s, schema, budget, via_proxy, think_off)
    if reply.error:
        return reply
    usage = reply.usage or {}
    cut = prompt_cut(usage, chars, usage.get("window") or context, budget)
    if cut:
        cut["ollama"] = bool(usage.get("ollama"))
        cut["capped"] = bool(cut["ollama"] and capped)
        if on_cut != "keep":
            return AgentReply(text="", error=cut_error(cut, cut["ollama"], cut["capped"]), usage=reply.usage)
        reply.usage = {**usage, "cut": cut}
    if usage.get("length"):
        if purpose in ("title", "tick"):
            return AgentReply(text="", error=LENGTH_ERROR, usage=reply.usage)  # обрывок не годится
        if purpose in LENGTH_NOTE:
            reply.text = f"{reply.text}\n\n{LENGTH_NOTE[purpose]}"
    return reply


# Подсказка шаблону чата выключить рассуждение (vLLM, llama.cpp: Qwen3 и
# другие с `enable_thinking`). Не проверено на LM Studio: незнакомое поле он,
# как и прочие серверы, обычно пропускает; отверг — повтор без неё.
_THINK_OFF_HINT = {"chat_template_kwargs": {"enable_thinking": False}}
# (адрес, модель) → сервер отверг подсказку (ответ сервера — помним на процесс).
_hint_rejected: dict[tuple[str, str], bool] = {}


def _send(base_url: str, payload: dict, timeout_s: float, schema: dict | None, max_tokens: int,
          via_proxy: bool | str, think_off: bool = False) -> AgentReply:
    native = _ollama_chat(base_url, payload, timeout_s, schema, max_tokens, via_proxy, think_off)
    if native is not None:
        return native
    url = base_url.rstrip("/") + "/chat/completions"
    hint_key = (url, str(payload.get("model")))
    if think_off and schema is None and not _hint_rejected.get(hint_key):
        reply, status, detail = _post(url, {**payload, **_THINK_OFF_HINT}, timeout_s, via_proxy)
        if not (status in (400, 422) and "chat_template_kwargs" in detail):
            return reply
        # Сервер поле не знает (это его ответ — на процесс): дальше без подсказки.
        _hint_rejected[hint_key] = True
    if schema is None:
        return _post(url, payload, timeout_s, via_proxy)[0]
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
    via_proxy: bool | str = False,
    on_cut: str = "error",
    purpose: str | None = None,
    images=(),
    keep_session: bool = False,
) -> AgentReply:
    """Вызов модели; ошибки — в AgentReply.error, счёт токенов — в `usage`.

    `model` (имя модели Claude из общего контракта) не используется: имя
    локальной модели задаёт `local_model`. `response_schema` — JSON Schema
    ответа (см. начало модуля); `purpose` — назначение вызова (`title`,
    `tick`, `answer`, `summary`, `improve`, `analysis`): предел ответа и текст
    ошибки; `max_tokens` — свой предел ответа; `on_cut="keep"` — обрезанный
    сервером промпт не ошибка (анализ встречи сам отмечает его).

    Сеансов и изображений у локальной модели нет (`llm.supports_resume`,
    `llm.vision` — False): `images` и `keep_session` игнорируются, а `resume`
    — сразу `resume_failed` (продолжать нечего, нужна затравка)."""
    if resume:
        return resume_failure("локальная модель сеансов не держит")
    payload = {
        "model": local_model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    return await asyncio.to_thread(_complete, base_url, payload, timeout_s, response_schema,
                                   max_tokens, via_proxy, on_cut, purpose)
