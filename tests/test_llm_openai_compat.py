import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from meet.llm import openai_compat


@pytest.fixture
def server():
    state = {"status": 200, "body": {"choices": [{"message": {"content": "ок"}}]},
             "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            state["requests"].append(
                (self.path, json.loads(self.rfile.read(length).decode("utf-8"))))
            data = json.dumps(state["body"]).encode("utf-8")
            self.send_response(state["status"])
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
    _run(server["base_url"])
    assert server["requests"][0][1]["model"] == "local-model"


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
