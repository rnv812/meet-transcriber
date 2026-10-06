"""Список моделей локального сервера (meet.llm.local_models): LM Studio,
Ollama, vLLM и ошибки. Настоящих серверов нет — фейковый HTTP на 127.0.0.1."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from meet.llm import local_models, openai_compat

LMSTUDIO = {"object": "list", "data": [
    {"id": "qwen2.5-7b-instruct", "object": "model", "owned_by": "organization_owner"},
    {"id": "text-embedding-nomic-embed-text-v1.5", "object": "model", "owned_by": "organization_owner"},
]}
OLLAMA_V1 = {"object": "list", "data": [
    {"id": "qwen3:8b", "object": "model", "created": 1759000000, "owned_by": "library"},
    {"id": "gemma3:4b", "object": "model", "created": 1759000000, "owned_by": "library"},
]}
OLLAMA_TAGS = {"models": [
    {"name": "qwen3:8b", "model": "qwen3:8b", "size": 5225388164,
     "details": {"parameter_size": "8.2B", "quantization_level": "Q4_K_M"}},
    {"name": "gemma3:4b", "model": "gemma3:4b", "size": 3338801804,
     "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"}},
]}
VLLM = {"object": "list", "data": [
    {"id": "Qwen/Qwen2.5-14B-Instruct", "object": "model", "owned_by": "vllm", "max_model_len": 32768},
]}


@pytest.fixture
def server():
    """Сервер с заготовленными ответами по пути: {путь: (код, тело)}; нет пути — 404."""
    state = {"routes": {}, "hits": [], "delay": 0.0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"].append(self.path)
            if state["delay"]:
                time.sleep(state["delay"])
            status, body = state["routes"].get(self.path, (404, {"error": "not found"}))
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "text/html" if isinstance(body, str) else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                pass  # клиент ушёл по таймауту

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["root"] = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield state
    httpd.shutdown()
    httpd.server_close()


def _ids(result):
    return [m["id"] for m in result["models"]]


def test_lm_studio_list(server):
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    got = local_models.list_models(server["root"] + "/v1")
    assert got["ok"] is True and got["error"] is None
    # Модели эмбеддингов в чат не годятся — их в списке нет.
    assert _ids(got) == ["qwen2.5-7b-instruct"]
    assert server["hits"] == ["/v1/models"]


def test_base_url_without_v1_asks_v1_models(server):
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    got = local_models.list_models(server["root"] + "/")
    assert got["ok"] and server["hits"][0] == "/v1/models"


def test_ollama_list_gets_sizes_from_its_native_api(server):
    server["routes"]["/v1/models"] = (200, OLLAMA_V1)
    server["routes"]["/api/tags"] = (200, OLLAMA_TAGS)
    got = local_models.list_models(server["root"] + "/v1")
    assert got["ok"] and _ids(got) == ["qwen3:8b", "gemma3:4b"]
    qwen = got["models"][0]
    assert qwen["size"] == 5225388164 and qwen["params"] == "8.2B"


def test_ollama_without_v1_models_falls_back_to_api_tags(server):
    server["routes"]["/api/tags"] = (200, OLLAMA_TAGS)
    got = local_models.list_models(server["root"] + "/v1")
    assert got["ok"] and got["source"] == "ollama"
    assert _ids(got) == ["qwen3:8b", "gemma3:4b"]
    assert got["models"][1]["size"] == 3338801804


def test_vllm_list_keeps_the_context_length(server):
    server["routes"]["/v1/models"] = (200, VLLM)
    got = local_models.list_models(server["root"] + "/v1")
    assert got["ok"] and got["models"] == [
        {"id": "Qwen/Qwen2.5-14B-Instruct", "size": None, "params": None, "context": 32768}]


def test_unreachable_server():
    got = local_models.list_models("http://127.0.0.1:9/v1")
    assert got["ok"] is False and got["reason"] == "unreachable"
    assert "не отвечает" in got["error"] and "127.0.0.1:9" in got["error"]


def test_slow_server_times_out(server):
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    server["delay"] = 1.0
    got = local_models.list_models(server["root"] + "/v1", timeout=0.2)
    assert got["ok"] is False and got["reason"] == "unreachable"
    assert "не ответил" in got["error"]


def test_not_openai_compatible(server):
    server["routes"]["/v1/models"] = (200, "<html>Router login</html>")
    got = local_models.list_models(server["root"] + "/v1")
    assert got["ok"] is False and got["reason"] == "not_openai"
    assert "не OpenAI-совместимый" in got["error"]


def test_nothing_at_known_paths_is_not_openai(server):
    got = local_models.list_models(server["root"] + "/v1")
    assert got["reason"] == "not_openai"


def test_only_embedding_models(server):
    server["routes"]["/v1/models"] = (200, {"data": [{"id": "nomic-embed-text:latest"}]})
    got = local_models.list_models(server["root"] + "/v1")
    assert got["reason"] == "empty" and "эмбеддинг" in got["error"]


def test_empty_list(server):
    server["routes"]["/v1/models"] = (200, {"object": "list", "data": []})
    got = local_models.list_models(server["root"] + "/v1")
    assert got["ok"] is False and got["reason"] == "empty" and got["models"] == []
    assert "моделей нет" in got["error"]


def test_auth_required(server):
    server["routes"]["/v1/models"] = (401, {"error": "Unauthorized"})
    got = local_models.list_models(server["root"] + "/v1")
    assert got["ok"] is False and got["reason"] == "auth"
    assert "401" in got["error"] and "ключ" in got["error"]


def test_chosen_model_no_longer_listed(server):
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    got = local_models.list_models(server["root"] + "/v1", model="llama-3-8b")
    assert got["ok"] is True and got["missing"] is True
    assert "llama-3-8b" in got["warning"] and "нет" in got["warning"]
    listed = local_models.list_models(server["root"] + "/v1", model="qwen2.5-7b-instruct")
    assert listed["missing"] is False and listed["warning"] is None


def test_bad_address():
    got = local_models.list_models("localhost:1234")
    assert got["ok"] is False and got["reason"] == "bad_url"


def test_local_addresses_skip_the_proxy(server, monkeypatch):
    # Прокси из окружения (мёртвый) не должен касаться локального сервера.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    assert local_models.list_models(server["root"] + "/v1")["ok"]


@pytest.mark.parametrize("url, direct", [
    ("http://localhost:1234/v1", True),
    ("http://127.0.0.1:11434", True),
    ("http://[::1]:8000/v1", True),
    ("http://192.168.1.20:1234/v1", True),
    ("http://10.0.0.5:8000/v1", True),
    ("http://172.20.0.2:8000/v1", True),
    ("http://169.254.10.1/v1", True),
    ("http://gpu-box:1234/v1", True),
    ("http://studio.local:1234/v1", True),
    ("https://api.example.com/v1", False),
    ("http://8.8.8.8/v1", False),
])
def test_which_addresses_go_without_proxy(url, direct):
    assert openai_compat.direct(url) is direct

