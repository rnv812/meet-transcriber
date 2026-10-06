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


@pytest.fixture(autouse=True)
def _fresh_caches():
    from fake_ollama import clear_caches

    clear_caches()
    yield
    clear_caches()


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

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            state.setdefault("posted", []).append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            self.do_GET()

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
    ("http://100.101.102.103:11434/v1", True),
    ("https://api.example.com/v1", False),
    ("http://8.8.8.8/v1", False),
])
def test_which_addresses_go_without_proxy(url, direct):
    assert openai_compat.direct(url) is direct



def test_ollama_latest_alias_is_the_same_model(server):
    # Ollama принимает «llama3.2» как «llama3.2:latest» — это не пропавшая модель.
    server["routes"]["/v1/models"] = (200, {"data": [{"id": "llama3.2:latest", "owned_by": "library"}]})
    server["routes"]["/api/tags"] = (200, {"models": [{"name": "llama3.2:latest", "size": 2019393189}]})
    got = local_models.list_models(server["root"] + "/v1", model="llama3.2")
    assert got["missing"] is False and got["models"][0]["size"] == 2019393189
    assert local_models.same_model("llama3.2:latest", "llama3.2")
    assert local_models.same_model("hf.co/org/repo", "hf.co/org/repo:latest")
    assert not local_models.same_model("qwen3:8b", "qwen3")
    assert not local_models.same_model("", "")


def test_context_from_ollama_show(server):
    server["routes"]["/api/version"] = (200, {"version": "0.12.3"})
    server["routes"]["/api/show"] = (200, {"model_info": {"general.architecture": "qwen3",
                                                          "qwen3.context_length": 8192}})
    got = local_models.context_length(server["root"] + "/v1", "qwen3:8b")
    assert got == {"tokens": 8192, "source": "ollama", "max": 8192}
    assert server["posted"][0] == ("/api/show", {"model": "qwen3:8b"})


def test_ollama_context_is_capped_at_32k(server):
    server["routes"]["/api/version"] = (200, {"version": "0.12.3"})
    server["routes"]["/api/show"] = (200, {"model_info": {"qwen3.context_length": 131072}})
    assert local_models.context_length(server["root"] + "/v1", "qwen3")["tokens"] == 32768


def test_context_from_lm_studio_prefers_the_loaded_window(server):
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    server["routes"]["/api/v0/models"] = (200, {"data": [
        {"id": "qwen2.5-7b-instruct", "type": "llm", "state": "loaded",
         "max_context_length": 32768, "loaded_context_length": 4096}]})
    got = local_models.context_length(server["root"] + "/v1", "qwen2.5-7b-instruct")
    assert got == {"tokens": 4096, "source": "lmstudio", "max": 32768}


def test_context_from_vllm(server):
    server["routes"]["/v1/models"] = (200, VLLM)
    got = local_models.context_length(server["root"] + "/v1", "Qwen/Qwen2.5-14B-Instruct")
    assert got == {"tokens": 32768, "source": "vllm", "max": None}


def test_context_from_llama_cpp_props(server):
    server["routes"]["/v1/models"] = (200, {"data": [{"id": "model.gguf"}]})
    server["routes"]["/props"] = (200, {"default_generation_settings": {"n_ctx": 8192}})
    assert local_models.context_length(server["root"] + "/v1", "model.gguf") == {
        "tokens": 8192, "source": "llamacpp", "max": None}


def test_context_unknown(server):
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    assert local_models.context_length(server["root"] + "/v1", "x") == {"tokens": None, "source": None,
                                                                          "max": None}


def test_timeout_is_marked(server):
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    server["delay"] = 1.0
    assert local_models.list_models(server["root"] + "/v1", timeout=0.2)["timeout"] is True


def test_via_proxy_sends_even_local_addresses_through_the_proxy(server, monkeypatch):
    # «Локальную модель — через прокси»: прокси (здесь мёртвый) — и для 127.0.0.1.
    # Общий opener urllib собран раньше, без прокси (как в задаче, где прокси
    # задают переменными уже после первого запроса), — он не должен решать.
    import urllib.request

    monkeypatch.setattr(urllib.request, "_opener", urllib.request.build_opener(urllib.request.ProxyHandler({})))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    assert local_models.list_models(server["root"] + "/v1", via_proxy=True)["reason"] == "unreachable"



def test_lm_studio_without_a_known_field_is_unknown_and_logged(server, caplog):
    import logging

    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    server["routes"]["/api/v0/models"] = (200, {"data": [
        {"id": "qwen2.5-7b-instruct", "type": "llm", "state": "loaded", "ctx": 4096}]})
    with caplog.at_level(logging.DEBUG, logger="meet.llm.local_models"):
        got = local_models.context_length(server["root"] + "/v1", "qwen2.5-7b-instruct")
    assert got == {"tokens": None, "source": None, "max": None}
    assert "окна загруженной модели нет" in caplog.text
    assert local_models.context_text(None) == "окно контекста не удалось определить"


def test_lm_studio_maximum_is_not_the_loaded_window(server, caplog):
    # max_context_length — сколько модель умеет, а загружена она часто с 4096:
    # окном это не считается (иначе анализ резал бы встречу под 32K).
    import logging

    server["routes"]["/v1/models"] = (200, LMSTUDIO)
    server["routes"]["/api/v0/models"] = (200, {"data": [
        {"id": "qwen2.5-7b-instruct", "type": "llm", "state": "not-loaded", "max_context_length": 32768}]})
    with caplog.at_level(logging.DEBUG, logger="meet.llm.local_models"):
        got = local_models.context_length(server["root"] + "/v1", "qwen2.5-7b-instruct")
    assert got == {"tokens": None, "source": None, "max": 32768}
    assert "окна загруженной модели нет" in caplog.text
    assert local_models.context_text(None, 32768) == (
        "окно контекста не удалось определить (модель поддерживает до 32768)")


def test_llama_cpp_prefers_the_per_slot_window(server):
    # --parallel 4: общее n_ctx 32768, на слот — 8192.
    server["routes"]["/v1/models"] = (200, {"data": [{"id": "model.gguf"}]})
    server["routes"]["/props"] = (200, {"n_ctx": 32768, "default_generation_settings": {"n_ctx": 8192}})
    assert local_models.context_length(server["root"] + "/v1", "model.gguf")["tokens"] == 8192


def test_failed_lookups_are_retried_after_a_while(server, monkeypatch):
    # Сбой связи не запоминается на весь процесс (живой ассистент идёт часами).
    import urllib.error

    calls = []
    real = local_models._get

    def flaky(url, *a, **k):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return real(url, *a, **k)

    monkeypatch.setattr(local_models, "_get", flaky)
    server["routes"]["/api/version"] = (200, {"version": "0.12.3"})
    assert local_models.is_ollama(server["root"]) is False
    assert local_models.is_ollama(server["root"]) is False  # пока не истёк срок — не спрашиваем
    now = local_models.time.monotonic()
    monkeypatch.setattr(local_models.time, "monotonic", lambda: now + 31)
    assert local_models.is_ollama(server["root"]) is True and len(calls) == 2
    # Ответ сервера — на процесс.
    assert local_models.is_ollama(server["root"]) is True and len(calls) == 2


def test_proxy_failure_answers_mean_unreachable(server):
    from meet.llm import detect

    server["routes"]["/v1/models"] = (502, "<html>Bad Gateway</html>")
    assert detect.local_reachable(server["root"] + "/v1", via_proxy=True) is False
    got = local_models.list_models(server["root"] + "/v1", via_proxy=True)
    assert got["reason"] == "unreachable" and "Прокси не достучался" in got["error"]


def test_reachability_via_proxy_is_an_http_get(server, monkeypatch):
    from meet.llm import detect

    server["routes"]["/v1/models"] = (401, {"error": "key"})  # любой ответ HTTP — сервер есть
    assert detect.local_reachable(server["root"] + "/v1", via_proxy=True) is True
    assert server["hits"] == ["/v1/models"]
    # Через прокси (мёртвый) — недоступен, хотя напрямую TCP соединился бы.
    import urllib.request

    monkeypatch.setattr(urllib.request, "_opener", None)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert detect.local_reachable(server["root"] + "/v1", via_proxy=True) is False
    assert detect.local_reachable(server["root"] + "/v1") is True
