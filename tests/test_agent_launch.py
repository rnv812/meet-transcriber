"""Параметры запуска агента (`agent.launch`): разбор строки параметров,
проверка переменных окружения, настройки и PATCH. Те же примеры — в тестах
оболочки (pty.rs) и окна (agentLaunch.test.ts): правила должны совпадать."""

import pytest

from meet import agent_launch, settings
from meet.settings import Settings

# (строка, аргументы) — общие примеры трёх реализаций.
CASES = [
    ("", []),
    ("   ", []),
    ("--model opus", ["--model", "opus"]),
    ("--permission-mode  acceptEdits\t--verbose", ["--permission-mode", "acceptEdits", "--verbose"]),
    (r"--add-dir D:\Docs", ["--add-dir", r"D:\Docs"]),
    (r'--add-dir "D:\Мои документы\База"', ["--add-dir", r"D:\Мои документы\База"]),
    (r"--add-dir \\server\share\kb", ["--add-dir", r"\\server\share\kb"]),
    (r'"\\server\share\kb"', [r"\\server\share\kb"]),
    ('--x="a b" c', ["--x=a b", "c"]),
    ("'single quoted' \"\"", ["single quoted", ""]),
    (r'"say \"hi\""', ['say "hi"']),
    ("-m gpt-5 -c model_reasoning_effort=high", ["-m", "gpt-5", "-c", "model_reasoning_effort=high"]),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_args_parse_like_the_shell_and_the_window(text, expected):
    assert agent_launch.parse_args(text) == expected


@pytest.mark.parametrize("text,error", [
    ('--add-dir "D:\\Docs', agent_launch.ARGS_QUOTE),
    ('"D:\\Docs\\"', agent_launch.ARGS_QUOTE),  # \" — кавычка, строка не закрыта
    ("'abc", agent_launch.ARGS_QUOTE),
    ("--model opus\n--verbose", agent_launch.ARGS_CONTROL),
    ("a\x00b", agent_launch.ARGS_CONTROL),
    ("'a\tb'", agent_launch.ARGS_CONTROL),
])
def test_bad_args_are_explained(text, error):
    with pytest.raises(ValueError) as e:
        agent_launch.parse_args(text)
    assert str(e.value) == error


def test_env_entries_are_checked():
    ok = [{"key": "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "value": "1"},
          {"key": "EMPTY_ONE", "value": ""}, {"key": "_x1", "value": "a=b c"}]
    assert agent_launch.env_error(ok) is None
    assert agent_launch.env_error([{"key": "1A", "value": ""}]) == agent_launch.ENV_NAME.format(key="1A")
    assert agent_launch.env_error([{"key": "A-B", "value": ""}]) is not None
    assert agent_launch.env_error([{"key": "A", "value": "x\ny"}]) == agent_launch.ENV_CONTROL.format(key="A")
    assert agent_launch.env_error([{"key": "Path", "value": "1"}, {"key": "PATH", "value": "2"}]) \
        == agent_launch.ENV_TWICE.format(key="PATH")
    assert agent_launch.env_error("A=1") == agent_launch.ENV_SHAPE


def test_settings_default_empty_and_round_trip():
    cfg = Settings.from_raw({})
    assert cfg.agent.to_raw() == {"launch": {
        "claude-code": {"args": "", "env": []}, "codex": {"args": "", "env": []},
        "opencode": {"args": "", "env": []}}}
    raw = {"agent": {"launch": {"claude-code": {
        "args": "--model opus", "env": [{"key": "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "value": "1"}]}}}}
    again = Settings.from_raw(Settings.from_raw(raw).to_raw())
    assert again.agent.claude.args == "--model opus"
    assert again.agent.claude.env == (("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "1"),)
    assert again.agent.codex.args == ""


def test_broken_values_from_the_file_fall_back_to_defaults():
    cfg = Settings.from_raw({"agent": {"launch": {
        "claude-code": {"args": '"unclosed', "env": [{"key": "1BAD", "value": ""}]},
        "codex": "мусор"}}})
    assert cfg.agent.claude.args == "" and cfg.agent.claude.env == ()
    assert cfg.agent.codex.args == ""


def test_patch_validates_and_saves(tmp_path):
    path = tmp_path / "config.json"
    launch = {"claude-code": {"args": "--permission-mode acceptEdits", "env": []},
              "codex": {"args": "-m gpt-5", "env": [{"key": "CODEX_HOME", "value": r"D:\codex"}]}}
    updated = settings.patch({"agent": {"launch": launch}}, path)
    assert updated.agent.codex.args == "-m gpt-5"
    assert settings.load(path).agent.codex.env == (("CODEX_HOME", r"D:\codex"),)
    for bad in ({"claude-code": {"args": '"x', "env": []}},
                {"claude-code": {"args": "", "env": [{"key": "A B", "value": ""}]}},
                {"other": {"args": "", "env": []}}):
        with pytest.raises(ValueError):
            settings.patch({"agent": {"launch": bad}}, path)
    assert settings.load(path).agent.claude.args == "--permission-mode acceptEdits"


def test_opencode_launch_is_its_own_section(tmp_path):
    path = tmp_path / "config.json"
    cfg = Settings.from_raw({})
    assert cfg.agent.to_raw()["launch"]["opencode"] == {"args": "", "env": []}
    launch = {"opencode": {"args": "--model anthropic/claude-sonnet-4-5",
                           "env": [{"key": "OPENCODE_CONFIG", "value": r"D:\oc.json"}]}}
    updated = settings.patch({"agent": {"launch": launch}}, path)
    assert updated.agent.opencode.args == "--model anthropic/claude-sonnet-4-5"
    assert settings.load(path).agent.opencode.env == (("OPENCODE_CONFIG", r"D:\oc.json"),)
    with pytest.raises(ValueError):
        settings.patch({"agent": {"launch": {"opencode": {"args": '"x', "env": []}}}}, path)
