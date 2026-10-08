"""Инструменты Meet для ассистента (0.5): что откроется, что нет; настройки без секретов."""

from __future__ import annotations

import pytest

from meet.assist import meet_tools as mt


def test_open_file_opens_documents_only(tmp_path):
    doc = tmp_path / "План.pdf"
    doc.write_bytes(b"%PDF")
    opened = []
    assert mt.open_file(str(doc), opener=opened.append) == "Открыт файл План.pdf"
    assert opened == [str(doc.resolve())]
    for bad in ("run.exe", "x.bat", "y.ps1", "z.lnk", "noext"):
        (tmp_path / bad).write_text("x")
        with pytest.raises(mt.ToolRefusal):
            mt.open_file(str(tmp_path / bad), opener=opened.append)
    assert len(opened) == 1


@pytest.mark.parametrize("path", ["", "relative/file.pdf", r"\\server\share\a.pdf", "//server/a.pdf",
                                  "https://example.com/a.pdf", "file:///C:/a.pdf"])
def test_open_file_refuses_network_relative_and_urls(path):
    with pytest.raises(mt.ToolRefusal):
        mt.open_file(path, opener=lambda _p: None)


def test_missing_file_and_ntfs_stream_are_refused(tmp_path):
    with pytest.raises(mt.ToolRefusal, match="файла нет"):
        mt.open_file(str(tmp_path / "нет.pdf"), opener=lambda _p: None)
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"x")
    with pytest.raises(mt.ToolRefusal):
        mt.open_file(str(doc) + ":evil.exe", opener=lambda _p: None)


def test_show_in_folder_any_local_file_or_folder(tmp_path):
    f = tmp_path / "run.exe"
    f.write_text("x")
    shown = []
    mt.show_in_folder(str(f), revealer=shown.append)       # выделить — не запустить: можно и exe
    mt.show_in_folder(str(tmp_path), revealer=shown.append)
    assert shown == [f.resolve(), tmp_path.resolve()]


def test_open_url_http_only_and_not_local():
    opened = []
    assert mt.open_url("https://example.com/x", opener=opened.append) == "Открыта ссылка example.com"
    for bad in ("http://127.0.0.1:8766/live", "http://localhost/", "file:///C:/x", "javascript:alert(1)", "ftp://a.b"):
        with pytest.raises(mt.ToolRefusal):
            mt.open_url(bad, opener=opened.append)
    assert opened == ["https://example.com/x"]


def test_launch_app_programs_only(tmp_path):
    app = tmp_path / "Telegram.exe"
    app.write_text("x")
    doc = tmp_path / "a.pdf"
    doc.write_text("x")
    run = []
    assert mt.launch_app(str(app), opener=run.append) == "Запущена программа Telegram"
    with pytest.raises(mt.ToolRefusal):
        mt.launch_app(str(doc), opener=run.append)


def test_mcp_server_lists_the_meet_tools():
    import asyncio

    from meet.assist import meet_mcp

    app = meet_mcp.build()
    names = sorted(t.name for t in asyncio.run(app.list_tools()))
    assert names == sorted(meet_mcp.TOOLS)
    cfg = meet_mcp.mcp_config("C:/py/python.exe")
    assert cfg == {"mcpServers": {"meet": {"type": "stdio", "command": "C:/py/python.exe",
                                           "args": ["-m", "meet.assist.meet_mcp"]}}}


def test_settings_view_has_no_secrets(monkeypatch):
    from meet import settings

    class S:
        def to_raw(self):
            return {"llm": {"provider": "openai-compatible", "api_key": "sk-1", "local": {"token": "t"}},
                    "integrations": {"jira": {"url": "https://j", "password": "p", "items": [{"secret": 1, "a": 2}]}},
                    "ui": {"theme": "dark"}}

    monkeypatch.setattr(settings, "load", lambda *a, **k: S())
    view = mt.settings_view()
    assert view == {"llm": {"provider": "openai-compatible", "local": {}},
                    "integrations": {"jira": {"url": "https://j", "items": [{"a": 2}]}}, "ui": {"theme": "dark"}}
    assert mt.settings_view("ui") == {"ui": {"theme": "dark"}}
    with pytest.raises(mt.ToolRefusal, match="нет секции"):
        mt.settings_view("нет")
