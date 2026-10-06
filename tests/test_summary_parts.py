"""Итоги длинной встречи на локальной модели по частям (map → reduce):
настоящие раунды сведения, честный отказ до первого вызова, предел вызовов,
стоп, если сведение не сокращает, ход частей для окна. Модели нет — фейковая
Ollama, ответ которой занимает весь отпущенный предел (худший случай)."""

import pytest
from fake_ollama import FakeOllama, clear_caches

from meet import assistant, events, library, llm_progress, settings
from meet import llm as llm_pkg

RID = "2026-10-01_10-00"


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _meeting(tmp_path, chars_per_turn=4000, turns=30):
    folder = tmp_path / RID
    folder.mkdir()
    word = "подробности "
    segments = [{"start": float(i * 60), "end": float(i * 60 + 59), "speaker": "Ольга" if i % 2 else "Демьян",
                 "text": f"Реплика {i}: " + word * (chars_per_turn // len(word))} for i in range(turns)]
    library.write_transcript(folder, {"version": 1, "segments": segments})
    return folder


def _runner(server):
    from dataclasses import replace

    cfg = settings.Settings()
    cfg = replace(cfg, llm=replace(cfg.llm, provider="openai-compatible", base_url=server.base_url,
                                   local_model="mistral-7b", enabled=("openai-compatible",)))
    return llm_pkg.runner_for("openai-compatible", cfg)


def full(word: str):
    """Ответ во весь предел `num_predict` (по «слову» на токен)."""
    return lambda body: " ".join([word] * (body["options"]["num_predict"] - 1))


def _systems(server):
    return [body["messages"][0]["content"] for body in server.chats()]


def test_8k_runs_a_merge_round_and_leaves_a_sane_final_budget(tmp_path):
    # ~120 тыс. символов: 10 частей, их пересказы (по 1200 токенов) вместе не
    # влезают — раунд сведения, затем итоги с нормальным пределом ответа.
    folder = _meeting(tmp_path)
    with FakeOllama(context=8192, reply=full("аб")) as server:
        assistant.summarize(folder, _runner(server), None, provider="openai-compatible", context=8192)
        systems = _systems(server)
        final = server.chats()[-1]
    assert systems.count(assistant.MERGE_SYSTEM) >= 1
    assert systems.count(assistant.PART_SYSTEM) >= 8
    assert systems[-1].startswith(assistant.SUMMARY_SYSTEM[:40])
    assert final["options"]["num_predict"] >= 1500
    assert len(systems) <= assistant.MAX_DIGEST_CALLS + 1
    assert (folder / assistant.SUMMARY_MD).exists()


def test_6k_with_a_live_draft_is_refused_before_any_call(tmp_path, monkeypatch):
    folder = _meeting(tmp_path)
    monkeypatch.setattr(assistant, "_live_draft", lambda f: "\n\nЧерновик живого режима:\n" + "пункт " * 700)
    with FakeOllama(context=6144, reply=full("аб")) as server:
        with pytest.raises(RuntimeError, match="итогам по частям нужно окно от 8K"):
            assistant.summarize(folder, _runner(server), None, provider="openai-compatible", context=6144)
        assert server.chats() == []


def test_4k_is_refused_with_zero_calls(tmp_path):
    folder = _meeting(tmp_path)
    with FakeOllama(context=4096, reply=full("аб")) as server:
        with pytest.raises(RuntimeError, match="увеличьте контекст модели до 16K"):
            assistant.summarize(folder, _runner(server), None, provider="openai-compatible", context=4096)
        assert server.chats() == []
    assert not (folder / assistant.SUMMARY_MD).exists()


def test_too_many_calls_are_refused_up_front(tmp_path):
    # ~360 тыс. символов при окне 8K: частей и сведений больше предела вызовов.
    folder = _meeting(tmp_path, chars_per_turn=12000)
    with FakeOllama(context=8192, reply=full("аб")) as server:
        with pytest.raises(RuntimeError, match=f"больше {assistant.MAX_DIGEST_CALLS} вызовов"):
            assistant.summarize(folder, _runner(server), None, provider="openai-compatible", context=8192)
        assert server.chats() == []


def test_merge_that_does_not_shrink_stops_at_once(tmp_path):
    # «Слова» по 9 символов: сведение во весь предел длиннее своего входа —
    # стоп после первого раунда, а не четыре раунда и отказ.
    folder = _meeting(tmp_path)
    with FakeOllama(context=8192, reply=full("пересказ")) as server:
        with pytest.raises(RuntimeError, match="не сокращаются"):
            assistant.summarize(folder, _runner(server), None, provider="openai-compatible", context=8192)
        systems = _systems(server)
    parts = systems.count(assistant.PART_SYSTEM)
    merges = systems.count(assistant.MERGE_SYSTEM)
    assert merges >= 1 and len(systems) == parts + merges  # один раунд, итогового вызова нет
    assert not (folder / assistant.SUMMARY_MD).exists()


def test_part_reply_is_sized_to_its_part(tmp_path):
    folder = _meeting(tmp_path, chars_per_turn=1500)  # ~45 тыс.: части и короткая последняя
    with FakeOllama(context=8192, reply=full("аб")) as server:
        assistant.summarize(folder, _runner(server), None, provider="openai-compatible", context=8192)
        parts = [body for body in server.chats() if body["messages"][0]["content"] == assistant.PART_SYSTEM]
    for body in parts:
        chars = len(body["messages"][1]["content"])
        assert body["options"]["num_predict"] <= min(assistant.PART_REPLY_TOKENS, int(chars / 2.5) // 3 + 1)


def test_one_retry_per_part(tmp_path):
    folder = _meeting(tmp_path, chars_per_turn=1500)
    calls = []

    async def flaky(prompt, **kwargs):
        from meet.llm.base import AgentReply

        calls.append(kwargs.get("purpose"))
        if len(calls) == 2:
            return AgentReply(text="", error="таймаут вызова модели")
        return AgentReply(text="пункт")

    path = assistant.summarize(folder, flaky, None, provider="openai-compatible", context=8192)
    assert path.exists() and calls.count("summary_part") >= 3

    calls.clear()

    async def broken(prompt, **kwargs):
        from meet.llm.base import AgentReply

        calls.append(1)
        return AgentReply(text="", error="таймаут вызова модели")

    with pytest.raises(RuntimeError, match="таймаут"):
        assistant.summarize(folder, broken, None, provider="openai-compatible", context=8192)
    assert len(calls) == 2  # часть 1 и один её повтор — дальше не тратим


def test_progress_names_parts_merge_and_final(tmp_path):
    folder = _meeting(tmp_path)
    bus = events.EventBus()
    seen = []
    bus.subscribe(lambda event: seen.append(str(event.to_dict())))
    tracker = llm_progress.Tracker(bus, "summary", "итоги встречи", stage="llm", provider="openai-compatible")
    llm_progress.attach(bus, tracker)
    with FakeOllama(context=8192, reply=full("аб")) as server:
        assistant.summarize(folder, tracker.wrap(_runner(server)), None, provider="openai-compatible",
                            context=8192, bus=bus)
    text = "\n".join(seen)
    assert "Итоги: часть 1 из 10" in text and "Итоги: часть 10 из 10" in text
    assert "объединение" in text and "итог" in text
    assert "repair" not in text  # части — не «исправление ответа»


def test_continuation_piece_keeps_its_speaker():
    line = "[12:30] Ольга: " + "слово " * 400
    pieces = assistant._split_long(line, 500)
    assert len(pieces) > 3 and pieces[0].startswith("[12:30] Ольга: ")
    assert all(p.startswith("[12:30] Ольга (продолжение): ") for p in pieces[1:])
    assert all(len(p) <= 500 for p in pieces)


def test_unknown_window_sizes_summaries_for_8k(monkeypatch):
    from meet import analysis, job_worker

    monkeypatch.setattr(analysis, "local_context", lambda cfg: None)
    assert job_worker._local_context("openai-compatible", settings.Settings()) == 8192
    assert job_worker._local_context("claude-code", settings.Settings()) is None



@pytest.mark.parametrize("chars_per_turn", [6000, 7000, 8000, 9000, 10000, 11000, 12000])
def test_plan_refuses_before_any_call_rather_than_at_the_cap(tmp_path, chars_per_turn):
    # Пересказы по ~4 символа на токен во весь предел: встреча либо проходит
    # в 40 вызовов, либо получает отказ до первого — не на сороковом.
    folder = _meeting(tmp_path, chars_per_turn=chars_per_turn)
    with FakeOllama(context=8192, reply=full("абв")) as server:
        try:
            assistant.summarize(folder, _runner(server), None, provider="openai-compatible", context=8192)
        except RuntimeError as e:
            assert server.chats() == [], f"отказ после {len(server.chats())} вызовов: {e}"
        else:
            assert len(server.chats()) <= assistant.MAX_DIGEST_CALLS


def test_draft_that_eats_the_room_is_named_in_the_refusal():
    room = assistant._final_room(8192)
    with pytest.raises(assistant.DigestError, match="черновик живого режима") as e:
        assistant.digest_plan(100_000, 8192, overhead=room - 1000, draft=9000)
    assert "9000 символов" in str(e.value)
    with pytest.raises(assistant.DigestError) as plain:
        assistant.digest_plan(100_000, 8192, overhead=room - 1000)
    assert "черновик" not in str(plain.value) and "не остаётся места" in str(plain.value)
