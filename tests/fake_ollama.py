"""Фейковый сервер Ollama для тестов: `/api/version`, `/api/show`, `/api/chat`
(и `/v1/…` — 404, чтобы вызов в обход родного пути был виден). Настоящей
модели нет: ответ — `reply(тело запроса)`, счёт токенов промпта — честная
оценка по символам или заданный (`prompt_tokens`, чтобы изобразить обрезку).

Рассуждающая модель (`thinking` — сколько «слов» рассуждения): в
`capabilities` — «thinking»; без `think: false` ответ начинается с
`<think>…</think>`, и всё вместе укладывается в `num_predict` (по слову на
токен) — не уложилось, ответ обрезан, `done_reason: "length"`."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOllama:
    def __init__(self, *, context: int = 40960, reply=lambda body: "ок", prompt_tokens=None, native=True,
                 thinking: int = 0, capabilities: bool = True):
        self.context = context
        self.capabilities = capabilities
        self.native = native
        self.reply = reply
        self.prompt_tokens = prompt_tokens
        self.thinking = thinking
        self.posted: list[tuple[str, dict]] = []
        self.gets: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.gets.append(self.path)
                if self.path == "/api/version":
                    return self._send(200, {"version": "0.12.3"})
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                fake.posted.append((self.path, body))
                if self.path == "/api/show":
                    show = {"model_info": {"qwen3.context_length": fake.context}}
                    if fake.capabilities:  # Ollama до ~0.6 поля не отдаёт
                        show["capabilities"] = ["completion"] + (["thinking"] if fake.thinking else [])
                    return self._send(200, show)
                if self.path == "/api/chat" and not fake.native:
                    return self._send(404, {"error": "404 page not found"})
                if self.path == "/v1/chat/completions" and not fake.native:
                    return self._send(200, {"choices": [{"message": {"content": fake.reply(body)}}]})
                if self.path == "/api/chat":
                    chars = sum(len(m.get("content") or "") for m in body.get("messages") or [])
                    seen = fake.prompt_tokens if fake.prompt_tokens is not None else int(chars / 2.8) + 20
                    content, done = fake.produce(body, (body.get("options") or {}).get("num_predict"))
                    return self._send(200, {"message": {"role": "assistant", "content": content},
                                            "prompt_eval_count": seen, "eval_count": 10, "done": True,
                                            "done_reason": done})
                return self._send(404, {"error": "not found"})

            def log_message(self, *a):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"

    def produce(self, body: dict, limit) -> tuple[str, str]:
        """Ответ модели с рассуждением (если не выключено) в пределах `limit` слов."""
        words = ([] if body.get("think") is False or not self.thinking
                 else ["<think>"] + ["мысль"] * self.thinking + ["</think>"])
        words += self.reply(body).split(" ")
        if isinstance(limit, int) and len(words) > limit:
            return " ".join(words[:limit]), "length"
        return " ".join(words), "stop"

    def chats(self) -> list[dict]:
        return [body for path, body in self.posted if path == "/api/chat"]

    def shows(self) -> int:
        return sum(1 for path, _ in self.posted if path == "/api/show")

    def __enter__(self):
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()


def clear_caches() -> None:
    """Сведения о серверах, которые клиент запоминает на процесс."""
    from meet.llm import local_models, openai_compat

    for cache in (openai_compat._schema_mode, openai_compat._num_ctx, openai_compat._context,
                  openai_compat._no_native, openai_compat._hint_rejected, local_models._ollama,
                  local_models._trained):
        cache.clear()
