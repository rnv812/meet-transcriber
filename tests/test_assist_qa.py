import asyncio

from meet.assist.agent import AgentReply
from meet.assist.bus import TranscriptBus
from meet.assist.digest import Digest
from meet.assist.qa import QAService


def _service(replies, calls, **kw):
    bus, digest = TranscriptBus(), Digest()
    digest.apply_delta("ADD Тема :: Решили X")

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
