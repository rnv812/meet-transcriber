"""Ворота согласия: находки финального ревью 0.4 (C1, I1, I2) и
состязательный проход по `read_plan` / `shell_risk` / `_path_ok`.

Каждый случай — настоящие ворота `ConsentGate(own_dirs=[запись])`: команда
чтения (`read_plan`) в ходах READ и USER проходит БЕЗ классификатора CLI
(хук отвечает `allow`), поэтому любая команда, выдающая себя за чтение, —
дыра; `shell_risk` — карточка Meet поверх автомода."""

import os

import pytest

from meet.llm import consent
from meet.llm.consent import ALLOW, ASK, DENY, NONE, READ, USER, ConsentGate


@pytest.fixture
def env(tmp_path):
    rec = tmp_path / "rec"
    home = tmp_path / "home"
    for d in (rec, home / ".ssh", tmp_path / "cwd", tmp_path / "outside"):
        d.mkdir(parents=True)
    (rec / "a.txt").write_text("a", encoding="utf-8")
    sensitive = consent.sensitive_paths(data_dir=tmp_path / "data", library_root=tmp_path / "lib", home=home)
    gate = ConsentGate(own_dirs=[rec], sensitive=sensitive, cwd=tmp_path / "cwd")
    p = lambda x: str(x).replace("\\", "/")  # noqa: E731
    return {"gate": gate, "rec": p(rec), "home": p(home), "out": p(tmp_path / "outside"), "root": tmp_path}


def _cmd(env, command):
    return command.format(rec=env["rec"], home=env["home"], out=env["out"])


def _decide(env, level, tool, command):
    gate = env["gate"]
    gate.begin(level)
    return gate.decide(tool, {"command": _cmd(env, command)})


# --- C1: «#» не режет команду ----------------------------------------------------------------

_HASH = [
    ("Bash", "cat {rec}/a.txt#; rm -rf {rec}/b"),                 # посреди слова — не комментарий
    ("Bash", "cat {rec}/a.txt#x; rm -rf {rec}/b"),
    ("Bash", "cat {rec}/a.txt #; rm -rf {rec}/b"),                # в начале слова — комментарий bash
    ("Bash", "# заметка\nrm -rf {rec}/b"),                        # строка-комментарий, потом команда
    ("Bash", "# >\nrm -rf {rec}/b"),
    ("PowerShell", "Get-Content {rec}/a.txt#; Remove-Item {rec}/b"),
    ("PowerShell", "Get-Content {rec}/a.txt #; Remove-Item {rec}/b"),
    ("PowerShell", "Get-Content {rec}/a.txt <# x #>; Remove-Item {rec}/b"),
    ("PowerShell", "<# x #>Remove-Item {rec}/b"),
    ("PowerShell", "# заметка\nRemove-Item {rec}/b"),
]


@pytest.mark.parametrize("tool,command", _HASH)
def test_hash_never_hides_a_second_command(env, tool, command):
    cmd = _cmd(env, command)
    assert consent.read_plan(tool, cmd) is None, cmd
    assert consent.shell_risk(tool, cmd) == consent.DELETE, cmd
    for level in (READ, USER):
        d = _decide(env, level, tool, command)
        assert d.outcome != ALLOW and d.kind != "shell-read", (level, cmd, d)
    assert _decide(env, USER, tool, command).outcome == ASK


@pytest.mark.parametrize("tool,command", [
    ("Bash", "ls x#; rm -rf {rec}/b"), ("Bash", "echo a#b; rm -rf {out}"),
    ("PowerShell", "ls x#; Remove-Item {out}"),
])
def test_hash_never_hides_a_delete_from_the_risk_check(env, tool, command):
    assert consent.shell_risk(tool, _cmd(env, command)) == consent.DELETE
    d = _decide(env, USER, tool, command)
    assert d.outcome == ASK and d.why == consent.DELETE, d


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cat {rec}/a.txt # прочитать"), ("Bash", "grep -n '#' {rec}/a.txt"),
    ("PowerShell", "Get-Content {rec}/a.txt # прочитать"), ("PowerShell", "Get-Content {rec}/a#b.txt"),
])
def test_any_hash_makes_a_command_not_a_plain_read(env, tool, command):
    """Чтение с «#» — не «простая команда чтения» (решает автомод или карточка),
    но и не удаление."""
    cmd = _cmd(env, command)
    assert consent.read_plan(tool, cmd) is None
    assert consent.shell_risk(tool, cmd) != consent.DELETE
    assert _decide(env, READ, tool, command).outcome == DENY


@pytest.mark.parametrize("tool,command", [
    ("Bash", "echo 'без пары"), ("PowerShell", "Write-Output <# без конца"),
])
def test_a_command_that_cannot_be_parsed_is_a_card_without_a_grant(env, tool, command):
    assert consent.shell_risk(tool, command) == consent.UNPARSED
    d = _decide(env, USER, tool, command)
    assert d.outcome == ASK and d.why == consent.UNPARSED and d.card["grant"] is None, d
    assert d.label == "спрашиваю вас: непонятная команда"
    assert env["gate"].grant_for(tool, {"command": command}) is None
