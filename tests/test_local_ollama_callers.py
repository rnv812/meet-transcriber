"""Всё, что зовёт локальную модель на Ollama, идёт родным `/api/chat` с окном
контекста по промпту: итоги, вопросы, названия, «Улучшить расшифровку», тики
живого ассистента (анализ — в test_llm_openai_compat). Одна общая функция
(`openai_compat._ollama_chat`), её же проверки: обрезанный промпт — ошибка,
окно меньше 6K — отказ без вызова. Модели нет — фейковый сервер Ollama."""

import asyncio
import json
from dataclasses import replace

import pytest
from fake_ollama import FakeOllama, clear_caches

from meet import assistant, improve, library, settings, titles
from meet import llm as llm_pkg
from meet.assist.digester import PerCallSession

RID = "2026-10-01_10-00"
SEGMENTS = [{"start": float(i * 10), "end": float(i * 10 + 9), "speaker": "Ольга",
             "text": f"Реплика {i}: обсуждаем выпуск экспорта отчётов и сроки."} for i in range(30)]


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def folder(tmp_path):
    folder = tmp_path / RID
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [dict(s) for s in SEGMENTS]})
    return folder


def _runner(server: FakeOllama):
    cfg = settings.Settings()
    cfg = replace(cfg, llm=replace(cfg.llm, provider="openai-compatible", base_url=server.base_url,
                                   local_model="qwen3:8b", enabled=("openai-compatible",)))
    return llm_pkg.runner_for("openai-compatible", cfg), cfg


def _native(server: FakeOllama) -> dict:
    """Последний вызов — родной /api/chat с окном контекста и пределом ответа."""
    chats = server.chats()
    assert chats, "вызова /api/chat не было"
    body = chats[-1]
    assert body["model"] == "qwen3:8b" and body["stream"] is False
    assert body["options"]["num_ctx"] in (4096, 8192, 16384, 32768)
    assert body["options"]["num_predict"] > 0
    assert not any(path.startswith("/v1") for path, _ in server.posted)
    return body


def test_summary(folder):
    with FakeOllama(reply=lambda body: "## Итоги\n- выпускаем экспорт") as server:
        runner, _cfg = _runner(server)
        path = assistant.summarize(folder, runner, None, provider="openai-compatible")
        assert "выпускаем экспорт" in path.read_text(encoding="utf-8")
        assert "format" not in _native(server)


def test_question(folder):
    with FakeOllama(reply=lambda body: "В пятницу.") as server:
        runner, _cfg = _runner(server)
        item = assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
        assert item["a"] == "В пятницу."
        _native(server)


def test_title(folder):
    with FakeOllama(reply=lambda body: "Выпуск экспорта отчётов") as server:
        runner, _cfg = _runner(server)
        assert titles.ask_title(folder, runner) == "Выпуск экспорта отчётов"
        _native(server)


def test_improve(folder):
    with FakeOllama(reply=lambda body: json.dumps({"replacements": []})) as server:
        runner, cfg = _runner(server)
        improve.run(folder, runner, cfg, provider="openai-compatible")
        _native(server)


def test_live_ticks_reuse_one_context_window_and_ask_the_model_info_once():
    # Тики живого ассистента: /api/show — раз на процесс, окно на модель не
    # уменьшается (Ollama не перезагружает модель между тиками).
    with FakeOllama(reply=lambda body: '{"ops": []}') as server:
        runner, _cfg = _runner(server)
        session = PerCallSession(runner, "Ты ведёшь сводку встречи.")
        for text in ("Реплика " * 3000, "Короткий тик.", "Ещё тик."):
            reply = asyncio.run(session.send(text))
            assert reply.error is None
        windows = [body["options"]["num_ctx"] for body in server.chats()]
        assert len(windows) == 3 and len(set(windows)) == 1
        assert server.shows() == 1 and server.gets.count("/api/version") == 1


def test_meeting_longer_than_the_model_window_is_refused_with_ollama_advice(folder):
    # Окно Ollama ставит сам Meet — «увеличьте контекст» тут не совет: нужна
    # модель с бо́льшим окном. Итогов по хвосту не пишем.
    long = [dict(s, text=s["text"] + " подробности" * 120) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    with FakeOllama(context=8192, reply=lambda body: "## Итоги") as server:
        runner, _cfg = _runner(server)
        with pytest.raises(RuntimeError) as e:
            assistant.summarize(folder, runner, None, provider="openai-compatible")
        assert server.chats() == []
    assert str(e.value) == ("текст не помещается в окно контекста модели (8192 токенов): итоги не получить — "
                            "окно этой модели меньше нужного — возьмите модель с бо́льшим окном контекста")
    assert not (folder / assistant.SUMMARY_MD).exists()


def test_small_window_model_still_answers_short_calls(folder):
    # Окно 4096: отказ «до 6K» — только у анализа; вопрос по короткой встрече идёт.
    with FakeOllama(context=4096, reply=lambda body: "В пятницу.") as server:
        runner, _cfg = _runner(server)
        assert assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")["a"] == "В пятницу."
        assert server.chats()[-1]["options"]["num_ctx"] == 4096


def test_title_asks_ollama_for_a_small_window(folder):
    with FakeOllama(reply=lambda body: "Выпуск экспорта") as server:
        runner, _cfg = _runner(server)
        titles.ask_title(folder, runner)
        options = server.chats()[-1]["options"]
        assert options["num_predict"] == 256 and options["num_ctx"] <= 8192



# --- vLLM: промпт + max_tokens должны влезть в max_model_len ------------------------


class FakeVLLM:
    """vLLM: окно `max_model_len` в /v1/models; промпт (настоящий счёт —
    ~3 символа на токен) + max_tokens больше окна — 400, как у vLLM."""

    def __init__(self, max_model_len: int, reply="ок"):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        fake = self
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/v1/models":
                    return self._send(200, {"data": [{"id": "qwen", "max_model_len": max_model_len}]})
                return self._send(404, {"detail": "Not Found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                fake.requests.append(body)
                prompt = sum(len(m["content"]) for m in body["messages"]) // 3
                if prompt + body["max_tokens"] > max_model_len:
                    return self._send(400, {"object": "error", "message": (
                        f"This model's maximum context length is {max_model_len} tokens. However, you "
                        f"requested {prompt + body['max_tokens']} tokens ({prompt} in the messages, "
                        f"{body['max_tokens']} in the completion).")})
                return self._send(200, {"choices": [{"message": {"content": reply}}],
                                        "usage": {"prompt_tokens": prompt, "completion_tokens": 5}})

            def log_message(self, *a):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def vllm():
    servers = []

    def make(max_model_len: int, reply="ок"):
        fake = FakeVLLM(max_model_len, reply)
        servers.append(fake)
        return fake

    yield make
    for fake in servers:
        fake.close()


def test_vllm_every_caller_fits_its_reply_into_8k(folder, vllm):
    # До раунда 3 всем шло max_tokens=8192 — и при max_model_len 8192 vLLM
    # отвергал каждое название, вопрос, итоги и тик.
    server = vllm(8192, reply="Выпуск экспорта")
    runner, cfg = _runner(server)
    assert titles.ask_title(folder, runner) == "Выпуск экспорта"
    assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
    assistant.summarize(folder, runner, None, provider="openai-compatible")
    asyncio.run(PerCallSession(runner, "Сводка.").send("Новые реплики."))
    budgets = [body["max_tokens"] for body in server.requests]
    assert budgets == [256, 1500, 3000, 600]


def test_vllm_improve_budget_follows_its_input(folder, vllm):
    server = vllm(16384, reply=json.dumps({"replacements": []}))
    runner, cfg = _runner(server)
    improve.run(folder, runner, cfg, provider="openai-compatible")
    prompt_chars = sum(len(m["content"]) for m in server.requests[0]["messages"])
    assert server.requests[0]["max_tokens"] == improve.reply_budget("x" * (prompt_chars - len(improve._SYSTEM)))


def test_vllm_long_summary_gets_the_rest_of_the_window(folder, vllm):
    # Итоги длинной встречи: на ответ — сколько осталось в окне, и vLLM это принимает.
    long = [dict(s, text=s["text"] + " подробности" * 42) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    server = vllm(8192, reply="## Итоги")
    runner, _cfg = _runner(server)
    assistant.summarize(folder, runner, None, provider="openai-compatible")
    sent = server.requests[-1]["max_tokens"]
    assert 128 <= sent < 3000


def test_vllm_meeting_that_cannot_fit_is_refused_before_the_call(folder, vllm):
    long = [dict(s, text=s["text"] + " подробности" * 200) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    server = vllm(8192)
    runner, _cfg = _runner(server)
    with pytest.raises(RuntimeError, match="ответ не получить — увеличьте контекст модели до 16K"):
        assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
    assert server.requests == []
