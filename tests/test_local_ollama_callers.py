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


def test_cut_prompt_is_an_error_not_a_summary_of_the_tail(folder):
    # Сервер насчитал промпту 200 токенов, а в нём тысячи: итогов по хвосту не пишем.
    long = [dict(s, text=s["text"] + " подробности" * 40) for s in SEGMENTS]
    library.write_transcript(folder, {"version": 1, "segments": long})
    with FakeOllama(reply=lambda body: "## Итоги", prompt_tokens=200) as server:
        runner, _cfg = _runner(server)
        with pytest.raises(RuntimeError, match="модель видела только часть текста"):
            assistant.summarize(folder, runner, None, provider="openai-compatible")
    assert not (folder / assistant.SUMMARY_MD).exists()


def test_too_small_context_is_refused_without_a_call(folder):
    with FakeOllama(context=4096, reply=lambda body: "ок") as server:
        runner, _cfg = _runner(server)
        with pytest.raises(RuntimeError, match="слишком маленькое окно контекста: 4096"):
            assistant.ask(folder, "Когда выпуск?", runner, None, provider="openai-compatible")
        assert server.chats() == []
