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

    def fake_run_assist(out_root, window_seconds, hotwords, task, vault, port,
                        no_voices=False, **kw):
        called.update(locals())

    monkeypatch.setattr("meet.assist.app.run_assist", fake_run_assist)
    monkeypatch.setattr(
        "sys.argv",
        ["meet", "assist", "--task", "demo", "--port", "9000"],
    )
    from meet import cli

    cli.main()
    assert called["task"] == "demo" and called["port"] == 9000


def test_cli_live_no_voices_default_false(monkeypatch):
    called = {}

    def fake_run_live(out_root, window_seconds, hotwords, no_voices=False):
        called.update(locals())

    monkeypatch.setattr("meet.live.run_live", fake_run_live)
    monkeypatch.setattr("sys.argv", ["meet", "live"])
    from meet import cli

    cli.main()
    assert called["no_voices"] is False


def test_cli_live_no_voices_flag(monkeypatch):
    called = {}

    def fake_run_live(out_root, window_seconds, hotwords, no_voices=False):
        called.update(locals())

    monkeypatch.setattr("meet.live.run_live", fake_run_live)
    monkeypatch.setattr("sys.argv", ["meet", "live", "--no-voices"])
    from meet import cli

    cli.main()
    assert called["no_voices"] is True


def test_cli_assist_no_voices_default_false(monkeypatch):
    called = {}

    def fake_run_assist(out_root, window_seconds, hotwords, task, vault, port,
                        no_voices=False, **kw):
        called.update(locals())

    monkeypatch.setattr("meet.assist.app.run_assist", fake_run_assist)
    monkeypatch.setattr("sys.argv", ["meet", "assist"])
    from meet import cli

    cli.main()
    assert called["no_voices"] is False


def test_cli_assist_no_voices_flag(monkeypatch):
    called = {}

    def fake_run_assist(out_root, window_seconds, hotwords, task, vault, port,
                        no_voices=False, **kw):
        called.update(locals())

    monkeypatch.setattr("meet.assist.app.run_assist", fake_run_assist)
    monkeypatch.setattr("sys.argv", ["meet", "assist", "--no-voices"])
    from meet import cli

    cli.main()
    assert called["no_voices"] is True


def _assist_call(monkeypatch, argv):
    from meet import cli, settings

    called = {}

    def fake_run_assist(out_root, **kw):
        called.update(kw)

    monkeypatch.setattr("meet.assist.app.run_assist", fake_run_assist)
    monkeypatch.setattr(settings, "load", lambda *a, **k: settings.Settings.from_raw({}))
    monkeypatch.setattr("sys.argv", ["meet", "assist"] + argv)
    cli.main()
    return called


def test_cli_assist_without_new_flags_is_old_path(monkeypatch):
    called = _assist_call(monkeypatch, [])
    assert called["port"] == 8765
    assert called["open_browser"] is True
    assert called["endpoint_file"] is None
    assert called["provider"] is None


def test_cli_assist_child_mode_flags(monkeypatch):
    called = _assist_call(monkeypatch, [
        "--no-browser", "--port", "0", "--endpoint-file", "C:/run/a.json",
        "--provider", "codex"])
    assert called["port"] == 0  # 0 — эфемерный порт, а не «взять из настроек»
    assert called["open_browser"] is False
    assert called["endpoint_file"] == "C:/run/a.json"
    assert called["provider"] == "codex"
