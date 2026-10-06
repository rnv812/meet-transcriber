import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meet.llm import openai_compat


@pytest.fixture
def server():
    state = {"status": 200, "body": {"choices": [{"message": {"content": "ок"}}]},
             "requests": [], "reject": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            got = json.loads(self.rfile.read(length).decode("utf-8"))
            state["requests"].append((self.path, got))
            kind = (got.get("response_format") or {}).get("type")
            status, body = state["status"], state["body"]
            if kind in state["reject"]:
                # Сервер не понимает этот response_format: (код, текст ошибки).
                status, body = state["reject"][kind]
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    state["base_url"] = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    yield state
    httpd.shutdown()
    httpd.server_close()


def _run(base_url, **kw):
    return asyncio.run(openai_compat.run(
        "Вопрос?", system_prompt="Система.", base_url=base_url, timeout_s=10, **kw))


def test_chat_completion(server):
    reply = _run(server["base_url"], local_model="qwen", allowed_dirs=("C:/kb",))
    assert reply.text == "ок" and reply.error is None
    path, body = server["requests"][0]
    assert path == "/v1/chat/completions"
    assert body["model"] == "qwen"
    assert body["messages"] == [
        {"role": "system", "content": "Система."},
        {"role": "user", "content": "Вопрос?"},
    ]


def test_default_model_name(server):
    # `session_id` от вопросов живого ассистента принимается и не используется.
    reply = _run(server["base_url"], session_id="0b6f8a52-3c1d-4e2f-9a7b-1c2d3e4f5a6b")
    assert server["requests"][0][1]["model"] == "local-model"
    assert reply.session_id is None


def test_trailing_slash_base_url(server):
    _run(server["base_url"] + "/")
    assert server["requests"][0][0] == "/v1/chat/completions"


def test_http_error(server):
    server["status"] = 500
    server["body"] = {"error": "boom"}
    reply = _run(server["base_url"])
    assert reply.text == "" and "500" in reply.error


def test_empty_choice_is_error(server):
    server["body"] = {"choices": [{"message": {"content": ""}}]}
    reply = _run(server["base_url"])
    assert reply.text == "" and reply.error


def test_unreachable():
    reply = _run("http://127.0.0.1:9/v1")
    assert reply.text == "" and reply.error


SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


@pytest.fixture(autouse=True)
def _fresh_modes():
    from meet.llm import local_models

    for cache in (openai_compat._schema_mode, local_models._ollama, local_models._trained):
        cache.clear()
    yield
    for cache in (openai_compat._schema_mode, local_models._ollama, local_models._trained):
        cache.clear()


def _formats(server):
    return [(body.get("response_format") or {}).get("type") for _, body in server["requests"]]


def test_schema_asks_for_json_schema_first(server):
    # LM Studio, vLLM, llama.cpp: строгий ответ по схеме.
    reply = _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert reply.error is None
    fmt = server["requests"][0][1]["response_format"]
    assert fmt == {"type": "json_schema", "json_schema": {"name": "answer", "schema": SCHEMA}}


def test_json_schema_rejected_falls_back_to_json_object(server):
    server["reject"] = {"json_schema": (400, {"error": "response_format json_schema is not supported"})}
    reply = _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert reply.text == "ок" and _formats(server) == ["json_schema", "json_object"]
    # Сработавший режим запомнен: следующий вызов — сразу им.
    _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert _formats(server)[-1] == "json_object" and len(server["requests"]) == 3


def test_both_formats_rejected_falls_back_to_prompt_only(server):
    # LM Studio: "'response_format.type' must be 'json_schema' or 'text'" — и пусть даже схему не берёт.
    server["reject"] = {"json_schema": (422, {"error": "bad schema"}),
                        "json_object": (400, {"error": "'response_format.type' must be 'json_schema' or 'text'"})}
    reply = _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert reply.text == "ок" and _formats(server) == ["json_schema", "json_object", None]


def test_grammar_error_500_counts_as_unsupported_format(server):
    server["reject"] = {"json_schema": (500, {"error": "JSON schema conversion failed: grammar"})}
    reply = _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert reply.text == "ок" and _formats(server) == ["json_schema", "json_object"]


def test_other_server_error_is_not_retried_without_format(server):
    server["status"] = 500
    server["body"] = {"error": "model crashed"}
    reply = _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert "500" in reply.error and len(server["requests"]) == 1


def test_without_schema_no_response_format(server):
    _run(server["base_url"], local_model="qwen")
    assert "response_format" not in server["requests"][0][1]


def test_max_tokens_is_always_sent(server):
    # Без предела зациклившаяся модель пишет до таймаута вызова.
    _run(server["base_url"], local_model="qwen")
    assert server["requests"][0][1]["max_tokens"] == openai_compat.DEFAULT_MAX_TOKENS
    _run(server["base_url"], local_model="qwen", response_schema=SCHEMA, max_tokens=3000)
    assert server["requests"][1][1]["max_tokens"] == 3000


@pytest.mark.parametrize("detail", [
    # vLLM
    "This model's maximum context length is 4096 tokens. However, you requested 9000 tokens.",
    # llama.cpp
    "the request exceeds the available context size, try increasing it",
    # LM Studio
    "Trying to keep the first 9000 tokens when context the overflows. However, the model is loaded "
    "with context length of only 4096 tokens",
])
def test_context_overflow_is_not_a_format_problem(server, detail):
    server["reject"] = {"json_schema": (400, {"error": {"message": detail}})}
    reply = _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert reply.error.startswith(openai_compat.CONTEXT_ERROR)
    assert len(server["requests"]) == 1  # ни json_object, ни без формата — сразу причина


def test_plain_bad_request_is_not_retried_in_other_formats(server):
    server["reject"] = {"json_schema": (400, {"error": "messages[1].content must be a string"})}
    reply = _run(server["base_url"], local_model="qwen", response_schema=SCHEMA)
    assert "400" in reply.error and len(server["requests"]) == 1


def test_usage_is_passed_back(server):
    server["body"] = {"choices": [{"message": {"content": "ок"}}],
                      "usage": {"prompt_tokens": 4096, "completion_tokens": 12}}
    reply = _run(server["base_url"], local_model="qwen")
    assert reply.usage == {"prompt_tokens": 4096, "completion_tokens": 12}


@pytest.fixture
def ollama():
    """Ollama: /api/version, /api/show и родной /api/chat."""
    state = {"posted": [], "chat": {"message": {"role": "assistant", "content": '{"title": "Т"}'},
                                    "prompt_eval_count": 5000, "eval_count": 40, "done": True}}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/version":
                return self._send(200, {"version": "0.12.3"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            state["posted"].append((self.path, body))
            if self.path == "/api/show":
                return self._send(200, {"model_info": {"qwen3.context_length": 40960}})
            if self.path == "/api/chat":
                return self._send(200, state["chat"])
            return self._send(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["base_url"] = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    yield state
    httpd.shutdown()
    httpd.server_close()


def test_ollama_analysis_goes_to_native_chat_with_a_context_window(ollama):
    prompt = "Реплика. " * 6000  # ≈ 54 тыс. символов ≈ 18 тыс. токенов
    reply = asyncio.run(openai_compat.run(prompt, system_prompt="Система.", base_url=ollama["base_url"],
                                          local_model="qwen3:8b", response_schema=SCHEMA, max_tokens=4000,
                                          timeout_s=10))
    assert reply.text == '{"title": "Т"}' and reply.usage["prompt_tokens"] == 5000
    path, body = ollama["posted"][-1]
    assert path == "/api/chat"
    assert body["format"] == SCHEMA and body["stream"] is False
    assert body["options"]["num_predict"] == 4000
    # Окно — по промпту и ответу с запасом, не больше обученного и 32K.
    assert 18000 + 4000 < body["options"]["num_ctx"] <= 32768
    assert "ollama" in openai_compat.accepted_modes()


def test_ollama_plain_calls_stay_on_v1(ollama):
    _ = asyncio.run(openai_compat.run("Вопрос?", system_prompt="С.", base_url=ollama["base_url"],
                                      local_model="qwen3:8b", timeout_s=10))
    assert all(path != "/api/chat" for path, _body in ollama["posted"])


def test_ollama_num_ctx_bounds():
    assert openai_compat.ollama_num_ctx(300, 1000, 40960) == 2048
    assert openai_compat.ollama_num_ctx(60000, 6000, 8192) == 8192
    assert openai_compat.ollama_num_ctx(300000, 6000, None) == 32768
