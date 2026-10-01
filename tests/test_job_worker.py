import shutil

from meet import job_worker, library


def _import_folder(tmp_path):
    src = tmp_path / "in" / "встреча.mp3"
    src.parent.mkdir()
    src.write_bytes(b"media")
    return library.create_import(tmp_path / "rec", src)


def test_copy_import_leaves_only_final_source(tmp_path):
    folder = _import_folder(tmp_path)
    assert job_worker._copy_import(str(folder)) == 0
    assert (folder / "source.mp3").read_bytes() == b"media"
    assert not list(folder.glob("*.part"))


def test_copy_import_failure_cleans_up_partial(tmp_path, monkeypatch):
    folder = _import_folder(tmp_path)

    def broken(src, dst):
        open(dst, "wb").write(b"me")
        raise OSError("диск полон")

    monkeypatch.setattr(shutil, "copy2", broken)
    assert job_worker._copy_import(str(folder)) != 0
    assert not (folder / "source.mp3").exists()
    assert not list(folder.glob("*.part"))


def _transcribed(tmp_path, monkeypatch):
    # Настройки — не с машины разработчика.
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    folder = tmp_path / "rec" / "2026-09-30_16-04"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Демьян", "text": "Начнём."}]})
    return folder


def _lines(capsys):
    import json

    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_summary_without_provider_says_how_to_connect(tmp_path, monkeypatch, capsys):
    import meet.llm as llm

    monkeypatch.setattr(llm, "resolve", lambda cfg: (None, None))
    folder = _transcribed(tmp_path, monkeypatch)
    assert job_worker.main(["summary", str(folder)]) == 2
    errors = [x for x in _lines(capsys) if x.get("kind") == "error"]
    assert errors[-1]["text"] == "Подключите Claude Code или Codex в настройках"
    assert not (folder / "summary.md").exists()


def test_summary_and_ask_jobs_report_their_files(tmp_path, monkeypatch, capsys):
    import meet.llm as llm
    from meet.llm.base import AgentReply

    async def runner(prompt, **kwargs):
        return AgentReply(text="ответ модели")

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("codex", runner))
    folder = _transcribed(tmp_path, monkeypatch)
    assert job_worker.main(["summary", str(folder)]) == 0
    result = [x for x in _lines(capsys) if x.get("kind") == "job.result"]
    assert result == [{"kind": "job.result", "path": str(folder / "summary.md")}]
    assert "_Модель: codex · " in (folder / "summary.md").read_text(encoding="utf-8")

    assert job_worker.main(["ask", str(folder), "--question=-что решили?"]) == 0
    result = [x for x in _lines(capsys) if x.get("kind") == "job.result"]
    assert result == [{"kind": "job.result", "path": str(folder / "qa.jsonl")}]
    assert "-что решили?" in (folder / "qa.jsonl").read_text(encoding="utf-8")


def test_model_error_becomes_job_error(tmp_path, monkeypatch, capsys):
    import meet.llm as llm
    from meet.llm.base import AgentReply

    async def runner(prompt, **kwargs):
        return AgentReply(text="", error="rate_limit")

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("claude-code", runner))
    folder = _transcribed(tmp_path, monkeypatch)
    assert job_worker.main(["summary", str(folder)]) == 1
    errors = [x for x in _lines(capsys) if x.get("kind") == "error"]
    assert errors[-1]["text"] == "rate_limit"


def _engine_install_note(monkeypatch, capsys, flavor, available):
    import json

    from meet import engine

    card = {"flavor": "cuda" if available else "cpu", "installed": True, "missing": [],
            "target": "C:/env",
            "download_gb": engine.DOWNLOAD_HINT_GB["cuda" if available else "cpu"]}
    monkeypatch.setattr(engine, "state", lambda: card)
    monkeypatch.setattr(engine, "install", lambda flavor, on_line: 0)
    assert job_worker._install_engine(flavor) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return next(e["note"] for e in events if e.get("note"))


def test_engine_install_note_shows_the_download_with_a_decimal_comma(monkeypatch, capsys):
    # 4.5 через :.0f печаталось «~4 ГБ» (банковское округление).
    assert _engine_install_note(monkeypatch, capsys, None, True) == "cuda, ~4,5 ГБ"


def test_engine_install_note_follows_the_chosen_flavor(monkeypatch, capsys):
    # Видеокарта есть, но выбран CPU — и объём скачивания у CPU.
    assert _engine_install_note(monkeypatch, capsys, "cpu", True) == "cpu, ~0,6 ГБ"
