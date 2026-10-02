import asyncio

from meet.assist.agent import AgentReply
from meet.assist.bus import TranscriptBus
from meet.assist.live_state import LiveState
from meet.assist.qa import QAService


def _service(replies, calls, **kw):
    bus, digest = TranscriptBus(), LiveState()
    digest.apply({"ops": [{"op": "add", "section": "decisions", "text": "Решили X"}]})

    async def runner(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return replies.pop(0)

    return bus, QAService(bus, digest, system_prompt="s", allowed_dirs=(),
                          cwd=".", runner=runner, **kw)


def test_ask_includes_digest_and_fresh_lines_and_resumes():
    calls = []
    bus, qa = _service(
        [AgentReply(text="ответ 1", session_id="sid-1"),
         AgentReply(text="ответ 2", session_id="sid-1")],
        calls,
    )
    bus.publish("[00:01:00] Собеседник: срок пятница")
    assert asyncio.run(qa.ask("какой срок?")) == "ответ 1"
    prompt, kwargs = calls[0]
    assert "Решили X" in prompt and "срок пятница" in prompt
    assert kwargs["resume"] is None

    asyncio.run(qa.ask("а точно?"))
    prompt2, kwargs2 = calls[1]
    assert kwargs2["resume"] == "sid-1"
    assert "срок пятница" not in prompt2  # хвост не дублируется


def test_ask_triggers_fresh_audio():
    flushed = []
    calls = []
    _, qa = _service([AgentReply(text="ок")], calls,
                     on_fresh_audio=lambda: flushed.append(1))
    asyncio.run(qa.ask("вопрос"))
    assert flushed == [1]


def test_error_returned_as_message_session_kept():
    calls = []
    _, qa = _service([AgentReply(text="", error="rate_limit")], calls)
    answer = asyncio.run(qa.ask("вопрос"))
    assert "rate_limit" in answer


def test_without_resume_history_goes_into_prompt():
    """Провайдер без сессий (Codex, локальная модель) не возвращает session_id —
    память диалога (последние 6 пар) кладётся в сам промпт."""
    calls = []
    replies = [AgentReply(text=f"ответ {i}") for i in range(8)]
    _, qa = _service(replies, calls)
    for i in range(8):
        asyncio.run(qa.ask(f"вопрос {i}"))
    first_prompt = calls[0][0]
    assert "Предыдущие вопросы" not in first_prompt
    assert "вопрос 0" in calls[1][0] and "ответ 0" in calls[1][0]
    last = calls[7][0]
    # В последнем промпте — пары 1..6 (шесть последних), пары 0 уже нет.
    assert "вопрос 0" not in last and "ответ 0" not in last
    for i in range(1, 7):
        assert f"вопрос {i}" in last and f"ответ {i}" in last
    assert all(kw["resume"] is None for _, kw in calls)
    # «Вопрос:» — только у текущего вопроса; история помечена иначе.
    assert last.count("Вопрос:") == 1 and "Вопрос: вопрос 7" in last
    assert "Ранее спросили: вопрос 6" in last and "Ты ответил: ответ 6" in last


def test_with_resume_history_not_duplicated():
    calls = []
    _, qa = _service([AgentReply(text="ответ 1", session_id="s"),
                      AgentReply(text="ответ 2", session_id="s")], calls)
    asyncio.run(qa.ask("вопрос 1"))
    asyncio.run(qa.ask("вопрос 2"))
    assert "ответ 1" not in calls[1][0]
    assert calls[1][1]["resume"] == "s"
