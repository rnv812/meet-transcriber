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


def _runner(server, model: str = "qwen3:8b"):
    cfg = settings.Settings()
    cfg = replace(cfg, llm=replace(cfg.llm, provider="openai-compatible", base_url=server.base_url,
                                   local_model=model, enabled=("openai-compatible",)))
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


def test_long_meeting_summary_goes_by_parts_instead_of_a_refusal(folder):
    # Встреча (~45 тыс. символов) не влезает в окно 8K: итоги по частям, затем
    # итоги из пересказов частей — а не отказ.
    long = [dict(s, text=s["text"] + " подробности" * 120) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    with FakeOllama(context=8192, reply=lambda body: "- выпуск в пятницу") as server:
        runner, _cfg = _runner(server)
        path = assistant.summarize(folder, runner, None, provider="openai-compatible", context=8192)
        systems = [body["messages"][0]["content"] for body in server.chats()]
    assert "выпуск в пятницу" in path.read_text(encoding="utf-8")
    parts = [x for x in systems if x == assistant.PART_SYSTEM]
    assert len(parts) >= 3 and systems[-1].startswith(assistant.SUMMARY_SYSTEM[:40])
    assert len(systems) == len(parts) + 1
    assert all(body["options"]["num_ctx"] <= 8192 for body in server.chats())


def test_question_on_a_meeting_longer_than_the_model_window_has_ollama_advice(folder):
    # Окно Ollama ставит сам Meet — «увеличьте контекст» тут не совет.
    long = [dict(s, text=s["text"] + " подробности" * 120) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    with FakeOllama(context=8192, reply=lambda body: "ок") as server:
        runner, _cfg = _runner(server)
        with pytest.raises(RuntimeError) as e:
            assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
        assert server.chats() == []
    assert str(e.value) == ("текст не помещается в окно контекста модели (8192 токенов): ответ не получить — "
                            "окно этой модели меньше нужного — возьмите модель с бо́льшим окном контекста")


def test_meeting_longer_than_meets_ollama_cap_says_so(folder):
    # Модель умеет 128K, а Meet просит у Ollama не больше 32K — совет про это.
    long = [dict(s, text=s["text"] + " подробности" * 300) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    with FakeOllama(context=131072) as server:
        runner, _cfg = _runner(server)
        with pytest.raises(RuntimeError, match=r"встреча длиннее окна, которое Meet запрашивает у Ollama \(32K\)"):
            assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")


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
    `chars_per_token`, по умолчанию ~3 символа на токен; 2,5 — худший случай)
    + max_tokens больше окна — 400, как у vLLM. Рассуждающая модель
    (`thinking` слов) пишет `<think>…</think>` перед ответом, если шаблону не
    сказали `enable_thinking: false` (или `honours_hint=False`); не уложилась в
    max_tokens (по слову на токен) — ответ обрезан, finish_reason "length"."""

    def __init__(self, max_model_len: int, reply="ок", chars_per_token: float = 3.0, thinking: int = 0,
                 honours_hint: bool = True, rejects_hint: bool = False):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        fake = self
        self.reply = reply
        self.rejected = 0
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
                if rejects_hint and "chat_template_kwargs" in body:
                    return self._send(400, {"object": "error", "message": (
                        "1 validation error: chat_template_kwargs — extra inputs are not permitted")})
                prompt = int(sum(len(m["content"]) for m in body["messages"]) / chars_per_token)
                if prompt + body["max_tokens"] > max_model_len:
                    fake.rejected += 1
                    return self._send(400, {"object": "error", "message": (
                        f"This model's maximum context length is {max_model_len} tokens. However, you "
                        f"requested {prompt + body['max_tokens']} tokens ({prompt} in the messages, "
                        f"{body['max_tokens']} in the completion).")})
                hint_off = (body.get("chat_template_kwargs") or {}).get("enable_thinking") is False
                words = [] if not thinking or (hint_off and honours_hint) else (
                    ["<think>"] + ["мысль"] * thinking + ["</think>"])
                words += fake.reply.split(" ")
                finish = "stop"
                if len(words) > body["max_tokens"]:
                    words, finish = words[:body["max_tokens"]], "length"
                return self._send(200, {"choices": [{"message": {"content": " ".join(words)},
                                                     "finish_reason": finish}],
                                        "usage": {"prompt_tokens": prompt, "completion_tokens": len(words)}})

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

    def make(max_model_len: int, reply="ок", **kw):
        fake = FakeVLLM(max_model_len, reply, **kw)
        servers.append(fake)
        return fake

    yield make
    for fake in servers:
        fake.close()


def test_vllm_every_caller_fits_its_reply_into_8k(folder, vllm):
    # До раунда 3 всем шло max_tokens=8192 — и при max_model_len 8192 vLLM
    # отвергал каждое название, вопрос, итоги и тик.
    server = vllm(8192, reply="Выпуск экспорта")
    runner, cfg = _runner(server, "mistral-7b")
    assert titles.ask_title(folder, runner) == "Выпуск экспорта"
    assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
    assistant.summarize(folder, runner, None, provider="openai-compatible")
    asyncio.run(PerCallSession(runner, "Сводка.").send("Новые реплики."))
    budgets = [body["max_tokens"] for body in server.requests]
    assert budgets == [256, 1500, 3000, 600]


def test_vllm_improve_budget_follows_its_input(folder, vllm):
    server = vllm(16384, reply=json.dumps({"replacements": []}))
    runner, cfg = _runner(server, "mistral-7b")
    improve.run(folder, runner, cfg, provider="openai-compatible")
    prompt_chars = sum(len(m["content"]) for m in server.requests[0]["messages"])
    assert server.requests[0]["max_tokens"] == improve.reply_budget("x" * (prompt_chars - len(improve._SYSTEM)))


def test_vllm_long_summary_gets_the_rest_of_the_window(folder, vllm):
    # Итоги длинной встречи: на ответ — сколько осталось в окне, и vLLM это принимает.
    long = [dict(s, text=s["text"] + " подробности" * 42) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    server = vllm(8192, reply="## Итоги")
    runner, _cfg = _runner(server, "mistral-7b")
    assistant.summarize(folder, runner, None, provider="openai-compatible")
    sent = server.requests[-1]["max_tokens"]
    assert 800 <= sent < 3000


def test_vllm_meeting_that_cannot_fit_is_refused_before_the_call(folder, vllm):
    long = [dict(s, text=s["text"] + " подробности" * 200) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    server = vllm(8192)
    runner, _cfg = _runner(server, "mistral-7b")
    with pytest.raises(RuntimeError, match="ответ не получить — увеличьте контекст модели до 16K"):
        assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
    assert server.requests == []


def test_vllm_worst_case_tokenizer_still_fits(folder, vllm):
    # Худший случай: модель со словарём 32K (~2,5 символа на токен). Предел
    # ответа считается осторожно и с запасом — vLLM запрос принимает.
    long = [dict(s, text=s["text"] + " подробности" * 30) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    server = vllm(8192, reply="## Итоги", chars_per_token=2.5)
    runner, _cfg = _runner(server, "mistral-7b")
    path = assistant.summarize(folder, runner, None, provider="openai-compatible")
    assert "## Итоги" in path.read_text(encoding="utf-8") and len(server.requests) == 1


def test_fit_is_judged_optimistically_and_the_reply_clamped_carefully(folder, vllm):
    # ~21 тыс. символов: по 2,5 на токен (8400) — больше окна 8192, по 3,0
    # (7000) — влезает с ответом не меньше минимума. Вопрос не отвергается.
    long = [dict(s, text=s["text"] + " подробности" * 52) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    server = vllm(8192, reply="В пятницу.")
    runner, _cfg = _runner(server, "mistral-7b")
    item = assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
    assert item["a"] == "В пятницу." and server.requests[0]["max_tokens"] == 400  # минимум ответа


def test_improve_pieces_fit_a_small_context(folder, vllm):
    # Улучшение на модели 8K: куски — по её окну, каждый vLLM принимает.
    long = [dict(s, text=s["text"] + " подробности" * 120) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    server = vllm(8192, reply=json.dumps({"replacements": []}))
    runner, cfg = _runner(server, "mistral-7b")
    improve.run(folder, runner, cfg, provider="openai-compatible")
    assert len(server.requests) > 3 and server.rejected == 0


# --- рассуждающие модели (Qwen3, DeepSeek-R1, gpt-oss) ------------------------------


def test_ollama_thinking_model_is_told_not_to_think_outside_analysis(folder):
    # Рассуждение (800 «слов») не влезло бы в предел названия (256) — без
    # think: false название было бы обрывком <think>.
    with FakeOllama(thinking=800, reply=lambda body: "Выпуск экспорта отчётов") as server:
        runner, _cfg = _runner(server)
        assert titles.ask_title(folder, runner) == "Выпуск экспорта отчётов"
        reply = asyncio.run(PerCallSession(runner, "Сводка.").send("Новые реплики."))
        assert reply.error is None
        bodies = server.chats()
    assert all(body["think"] is False for body in bodies)
    assert bodies[0]["options"]["num_predict"] == 256  # запаса на рассуждение не надо


def test_ollama_analysis_may_think_with_an_allowance():
    from meet.llm import openai_compat

    with FakeOllama(thinking=300, reply=lambda body: '{"title": "Т"}') as server:
        reply = asyncio.run(openai_compat.run("Реплика.", system_prompt="С.", base_url=server.base_url,
                                              local_model="qwen3:8b", purpose="analysis", max_tokens=2048,
                                              response_schema={"type": "object"}, timeout_s=10))
        body = server.chats()[-1]
    assert "think" not in body
    assert body["options"]["num_predict"] == 2048 + openai_compat.REASONING_ALLOWANCE
    assert reply.text == '{"title": "Т"}'  # рассуждение снято


def test_v1_reasoning_model_gets_an_allowance_and_the_hint(folder, vllm):
    # LM Studio и т. п. подсказку шаблону могли не понять: рассуждение 500
    # слов пришло — запас его вместил, а <think> из названия снят.
    server = vllm(32768, reply="Выпуск экспорта", thinking=500, honours_hint=False)
    runner, _cfg = _runner(server, "qwen3-8b")
    assert titles.ask_title(folder, runner) == "Выпуск экспорта"
    body = server.requests[0]
    assert body["max_tokens"] == 256 + 2048
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_v1_non_reasoning_model_gets_no_hint(folder, vllm):
    server = vllm(32768, reply="Выпуск экспорта")
    runner, _cfg = _runner(server, "mistral-7b")
    titles.ask_title(folder, runner)
    assert "chat_template_kwargs" not in server.requests[0] and server.requests[0]["max_tokens"] == 256


# --- ответ упёрся в предел -------------------------------------------------------


def test_summary_cut_at_the_limit_is_saved_with_a_note(folder, vllm):
    server = vllm(32768, reply=" ".join(["пункт"] * 4000))
    runner, _cfg = _runner(server, "mistral-7b")
    text = assistant.summarize(folder, runner, None, provider="openai-compatible").read_text(encoding="utf-8")
    assert "Итоги обрезаны: модели не хватило места для ответа" in text


def test_answer_cut_at_the_limit_gets_the_note(folder, vllm):
    server = vllm(32768, reply=" ".join(["слово"] * 2000))
    runner, _cfg = _runner(server, "mistral-7b")
    item = assistant.ask(folder, "Что решили?", runner, None, provider="openai-compatible")
    assert item["a"].endswith("(Ответ обрезан: модели не хватило места для ответа.)")


def test_title_and_tick_cut_at_the_limit_are_discarded(folder, vllm):
    server = vllm(32768, reply=" ".join(["слово"] * 1000))
    runner, _cfg = _runner(server, "mistral-7b")
    with pytest.raises(RuntimeError, match="не хватило места"):
        titles.ask_title(folder, runner)
    reply = asyncio.run(PerCallSession(runner, "Сводка.").send("Новые реплики."))
    assert reply.text == "" and "не хватило места" in reply.error



def test_old_ollama_without_capabilities_falls_back_to_the_name(folder):
    # Ollama до ~0.6 не отдаёт capabilities: Qwen3 узнаём по имени.
    with FakeOllama(thinking=800, capabilities=False, reply=lambda body: "Выпуск экспорта") as server:
        runner, _cfg = _runner(server)
        assert titles.ask_title(folder, runner) == "Выпуск экспорта"
        assert server.chats()[-1]["think"] is False


def test_gpt_oss_keeps_the_allowance_even_with_think_off(folder):
    # gpt-oss на Ollama булево think не слушает — запас на рассуждение остаётся.
    from meet.llm import openai_compat

    with FakeOllama(thinking=500, reply=lambda body: "Выпуск экспорта") as server:
        runner, _cfg = _runner(server, "gpt-oss:20b")
        titles.ask_title(folder, runner)
        options = server.chats()[-1]["options"]
    assert options["num_predict"] == 256 + openai_compat.REASONING_ALLOWANCE


def test_rejected_thinking_hint_is_remembered(folder, vllm):
    server = vllm(32768, reply="Выпуск экспорта", rejects_hint=True)
    runner, _cfg = _runner(server, "qwen3-8b")
    assert titles.ask_title(folder, runner) == "Выпуск экспорта"
    assert titles.ask_title(folder, runner) == "Выпуск экспорта"
    with_hint = [body for body in server.requests if "chat_template_kwargs" in body]
    assert len(with_hint) == 1 and len(server.requests) == 3  # отказ — один раз на процесс


def test_unknown_model_window_does_not_blame_meets_cap(folder):
    # Обученное окно не узнать — виноват не предел Meet (32K): совет — про модель.
    long = [dict(s, text=s["text"] + " подробности" * 300) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    with FakeOllama(context=None) as server:
        runner, _cfg = _runner(server)
        with pytest.raises(RuntimeError) as e:
            assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
    assert "Meet запрашивает" not in str(e.value)
    assert "возьмите модель с бо́льшим окном контекста" in str(e.value)
