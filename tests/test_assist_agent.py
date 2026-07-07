import asyncio
from pathlib import Path

from meet.assist.agent import make_permission_callback


def _decide(cb, tool, data):
    return asyncio.run(cb(tool, data, None))


def test_read_inside_allowed_dir_allowed(tmp_path):
    from claude_agent_sdk import PermissionResultAllow

    cb = make_permission_callback((tmp_path,))
    ok = _decide(cb, "Read", {"file_path": str(tmp_path / "note.md")})
    assert isinstance(ok, PermissionResultAllow)


def test_read_outside_denied(tmp_path):
    from claude_agent_sdk import PermissionResultDeny

    cb = make_permission_callback((tmp_path / "vault",))
    deny = _decide(cb, "Read", {"file_path": "C:/Windows/system.ini"})
    assert isinstance(deny, PermissionResultDeny)


def test_write_and_bash_always_denied(tmp_path):
    from claude_agent_sdk import PermissionResultDeny

    cb = make_permission_callback((tmp_path,))
    assert isinstance(_decide(cb, "Bash", {"command": "dir"}), PermissionResultDeny)
    assert isinstance(
        _decide(cb, "Write", {"file_path": str(tmp_path / "x.md")}),
        PermissionResultDeny,
    )


def test_read_without_file_path_denied(tmp_path):
    from claude_agent_sdk import PermissionResultDeny

    cb = make_permission_callback((tmp_path,))
    assert isinstance(_decide(cb, "Read", {}), PermissionResultDeny)


def test_grep_without_path_allowed(tmp_path):
    from claude_agent_sdk import PermissionResultAllow

    cb = make_permission_callback((tmp_path,))
    assert isinstance(_decide(cb, "Grep", {}), PermissionResultAllow)


def test_no_allowed_dirs_denies_read(tmp_path):
    from claude_agent_sdk import PermissionResultDeny

    cb = make_permission_callback(())
    assert isinstance(
        _decide(cb, "Read", {"file_path": str(tmp_path / "x.md")}),
        PermissionResultDeny,
    )
