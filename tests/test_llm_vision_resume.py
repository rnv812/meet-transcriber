"""Общий контракт провайдеров V4 (0.3.6): кто видит изображения
(`llm.vision`), кто продолжает сеанс нативно (`llm.supports_resume`,
`llm.session_kwargs`), `images=` / `keep_session=` у всех runner, у Claude
через Agent SDK — блоки изображений и «сеанс не продолжить». Модель не зовём."""

import asyncio
import base64
import inspect
import uuid

import claude_agent_sdk
import pytest

from meet import llm
from meet.llm import claude, codex, openai_compat, opencode
from meet.llm.base import RESUME_ERROR, image_media_type, is_uuid


def test_vision_flags():
    assert llm.vision("claude-code") is True
    assert llm.vision("codex") is True
    assert llm.vision("opencode") is False
    assert llm.vision("openai-compatible") is False
    assert llm.vision(None) is False
    assert "не видит изображения" in llm.NO_VISION_NOTE.lower()


def test_resume_support_and_session_kwargs():
    assert [p for p in llm.PROVIDERS if llm.supports_resume(p)] == ["claude-code", "codex", "opencode"]
    assert llm.session_kwargs("codex", None) == {"keep_session": True}
    assert llm.session_kwargs("claude-code", "abc") == {"resume": "abc"}
    assert llm.session_kwargs("opencode", "ses_1") == {"resume": "ses_1"}
    assert llm.session_kwargs("openai-compatible", "x") == {}
    assert llm.session_kwargs(None, None) == {}


@pytest.mark.parametrize("module", [claude, codex, opencode, openai_compat])
def test_every_runner_takes_images_and_keep_session(module):
    params = inspect.signature(module.run).parameters
    for name in ("images", "keep_session", "resume", "session_id"):
        assert name in params, (module.__name__, name)


def test_helpers():
    assert image_media_type("A.JPG") == "image/jpeg" and image_media_type("x.bmp") is None
    assert is_uuid(str(uuid.uuid4())) and not is_uuid("--resume") and not is_uuid(None)


# --- локальная модель: изображений и сеансов нет ------------------------------------


def _local(monkeypatch, **kw):
    seen = {}

    def fake_complete(base_url, payload, *args):
        seen["payload"] = payload
        return llm.AgentReply(text="ок")

    monkeypatch.setattr(openai_compat, "_complete", fake_complete)
    reply = asyncio.run(openai_compat.run("вопрос", system_prompt="s", **kw))
    return reply, seen


def test_local_model_ignores_images(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    reply, seen = _local(monkeypatch, images=[img], keep_session=True)
    assert reply.text == "ок" and reply.session_id is None
    assert seen["payload"]["messages"][1] == {"role": "user", "content": "вопрос"}


def test_local_model_cannot_resume(monkeypatch):
    reply, seen = _local(monkeypatch, resume="x")
    assert reply.resume_failed and reply.error.startswith(RESUME_ERROR)
    assert seen == {}


def test_runner_for_passes_images_only_to_the_provider(monkeypatch):
    """`llm._call` пробрасывает `images` CLI-провайдеру как есть."""
    seen = {}

    async def fake_run(prompt, **kw):
        seen.update(kw)
        return llm.AgentReply(text="ок")

    monkeypatch.setattr(codex, "run", fake_run)
    from meet.settings import Settings

    runner = llm.runner_for("codex", Settings())
    asyncio.run(runner("p", system_prompt="s", images=["a.png"], keep_session=True, purpose="answer"))
    assert seen["images"] == ["a.png"] and seen["keep_session"] is True and "purpose" not in seen


# --- Claude через Agent SDK -------------------------------------------------------


def _sdk(monkeypatch, messages, **kw):
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    seen = {"sent": []}

    def fake_query(*, prompt, options):
        seen["cmd"] = SubprocessCLITransport(prompt=prompt, options=options)._build_command()

        async def gen():
            async for msg in prompt:
                seen["sent"].append(msg)
            for m in messages:
                if isinstance(m, Exception):
                    raise m
                yield m
        return gen()

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setattr(claude, "find_cli", lambda: "C:/claude.exe")
    reply = asyncio.run(claude.run("что на картинке?", system_prompt="s", **kw))
    return reply, seen


def _result(is_error=False, sid="x", result="ответ", subtype="success"):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
                         num_turns=1, session_id=sid, result=result)


def _init():
    from claude_agent_sdk import SystemMessage

    return SystemMessage(subtype="init", data={"session_id": "x"})


def test_sdk_images_become_base64_blocks(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG-bytes")
    reply, seen = _sdk(monkeypatch, [_init(), _result()], images=[img])
    assert reply.text == "ответ"
    (msg,) = seen["sent"]
    image, text = msg["message"]["content"]
    assert image["source"] == {"type": "base64", "media_type": "image/png",
                               "data": base64.b64encode(b"\x89PNG-bytes").decode()}
    assert text == {"type": "text", "text": "что на картинке?"}


def test_sdk_keep_session_starts_with_our_uuid(monkeypatch):
    reply, seen = _sdk(monkeypatch, [_init(), _result(sid=None)], keep_session=True)
    flag = next(a for a in seen["cmd"] if a.startswith("--session-id="))
    sid = flag.split("=", 1)[1]
    assert is_uuid(sid) and reply.session_id == sid
    assert "--no-session-persistence" not in seen["cmd"]


def test_sdk_resume_failure_before_init_is_distinct(monkeypatch):
    sid = str(uuid.uuid4())
    reply, _ = _sdk(monkeypatch, [_result(is_error=True, sid=sid, result=None,
                                          subtype="error_during_execution"),
                                  RuntimeError(f"No conversation found with session ID: {sid}")],
                    resume=sid)
    assert reply.resume_failed and reply.error.startswith(RESUME_ERROR)


def test_sdk_error_after_init_while_resuming_is_an_ordinary_error(monkeypatch):
    sid = str(uuid.uuid4())
    reply, _ = _sdk(monkeypatch, [_init(), _result(is_error=True, sid=sid, result=None,
                                                   subtype="error_max_turns")], resume=sid)
    assert reply.error and not reply.resume_failed


def test_sdk_refuses_a_non_uuid_resume(monkeypatch):
    called = []
    monkeypatch.setattr(claude_agent_sdk, "query", lambda **kw: called.append(kw))
    reply = asyncio.run(claude.run("x", system_prompt="s", resume="--dangerously-skip-permissions"))
    assert reply.resume_failed and called == []
