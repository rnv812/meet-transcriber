import sys

import meet.transcribe


def _run_cli(monkeypatch, argv):
    import meet.cli as cli

    called = {}

    # overlap без default: пока cli его не передаёт, тест падает TypeError
    def fake_transcribe(path, speakers=None, hotwords=None, align=True, *, overlap):
        called.update(path=path, align=align, overlap=overlap)

    monkeypatch.setattr(meet.transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(sys, "argv", ["meet"] + argv)
    cli.main()
    return called


def test_cli_transcribe_defaults_overlap_on(monkeypatch):
    called = _run_cli(monkeypatch, ["transcribe", "x"])
    assert called["path"] == "x"
    assert called["align"] is True
    assert called["overlap"] is True


def test_cli_no_overlap_flag(monkeypatch):
    called = _run_cli(monkeypatch, ["transcribe", "x", "--no-overlap"])
    assert called["overlap"] is False


def test_cli_assist_parses_flags(monkeypatch):
    called = {}

    def fake_run_assist(out_root, window_seconds, hotwords, task, vault, port):
        called.update(locals())

    monkeypatch.setattr("meet.assist.app.run_assist", fake_run_assist)
    monkeypatch.setattr(
        "sys.argv",
        ["meet", "assist", "--task", "demo", "--port", "9000"],
    )
    from meet import cli

    cli.main()
    assert called["task"] == "demo" and called["port"] == 9000
