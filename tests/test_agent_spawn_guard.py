"""Защита тестов: ни Claude Code, ни Codex, ни OpenCode не запускаются (conftest)."""

import asyncio
import subprocess
import sys

import pytest

from conftest import _agent_program


@pytest.mark.parametrize("args,hit", [
    (["C:/Users/u/.local/bin/claude.exe", "-p"], True),
    (["claude", "--version"], True),
    (r'"C:\Program Files\codex\codex.exe" exec -', True),
    (["node", "C:/npm/node_modules/@anthropic-ai/claude-code/cli.js"], True),
    (["C:/oc/opencode.exe", "run", "--format", "json"], True),
    (["/usr/local/bin/opencode", "auth", "list"], True),
    (r'"C:\Users\u\AppData\Roaming\npm\opencode.cmd" models', True),
    (["node", "C:/npm/node_modules/opencode-ai/bin/opencode", "run"], True),
    ([sys.executable, "tests/fake_claude_stream.py"], False),
    (["ffmpeg", "-i", "x"], False),
    (["taskkill", "/PID", "1"], False),
])
def test_agent_program_detection(args, hit):
    assert (_agent_program(args) is not None) is hit


def test_popen_and_asyncio_spawns_of_agents_are_refused(_no_agent_spawn):
    with pytest.raises(OSError, match="запустил агента"):
        subprocess.Popen(["claude.exe", "-p"])

    async def scenario():
        await asyncio.create_subprocess_exec("codex", "exec", "-")

    with pytest.raises(OSError, match="запустил агента"):
        asyncio.run(scenario())
    with pytest.raises(OSError, match="запустил агента"):
        subprocess.run(["opencode", "auth", "list"], capture_output=True)
    assert len(_no_agent_spawn) == 3
    _no_agent_spawn.clear()  # своя проверка — тест не проваливаем


def test_agent_sdk_transport_is_refused(_no_agent_spawn):
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    with pytest.raises(OSError):
        asyncio.run(SubprocessCLITransport.connect(object()))
    assert _no_agent_spawn
    _no_agent_spawn.clear()


def test_python_children_still_run():
    out = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, text=True)
    assert out.stdout.strip() == "1"
