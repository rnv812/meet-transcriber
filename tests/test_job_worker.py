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

    def broken(src, dst, report, clock=None):
        open(dst, "wb").write(b"me")
        raise OSError("диск полон")

    monkeypatch.setattr(job_worker, "_copy_with_progress", broken)
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
    assert errors[-1]["text"] == "Подключите Claude Code, Codex или OpenCode в настройках"
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


def test_merge_job_runs_merge_and_reports_folder(tmp_path, monkeypatch, capsys):
    import json

    from meet import merge

    calls = []
    monkeypatch.setattr(merge, "run", lambda folder, bus=None: calls.append(folder) or folder)
    assert job_worker.main(["merge", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[-1] == {"kind": "job.result", "path": str(tmp_path)}


def test_merge_job_failure_is_text_for_people(tmp_path, monkeypatch, capsys):
    import json

    from meet import merge

    def broken(folder, bus=None):
        raise merge.MergeError("Исходная запись пропала: 2026-09-30_10-30")

    monkeypatch.setattr(merge, "run", broken)
    assert job_worker.main(["merge", str(tmp_path)]) == 3
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[-1] == {"kind": "error", "text": "Исходная запись пропала: 2026-09-30_10-30"}


def test_speaker_split_job_computes_voices_of_the_speaker(tmp_path, monkeypatch, capsys):
    import json

    from meet import segvoices

    library.write_transcript(tmp_path, {"segments": [
        {"start": 0, "end": 3, "speaker": "Спикер 2", "text": "а"},
        {"start": 3, "end": 5, "speaker": "Вы", "text": "б"},
        {"start": 5, "end": 9, "speaker": "Спикер 2", "text": "в"}]})
    calls = []
    monkeypatch.setattr(segvoices, "compute", lambda folder, idx, bus=None: calls.append((folder, idx)))
    assert job_worker.main(["speaker_split", str(tmp_path), "--label=Спикер 2"]) == 0
    assert calls == [(tmp_path, [0, 2])]
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[-1] == {"kind": "job.result", "path": str(tmp_path / segvoices.CACHE_NAME)}
    assert job_worker.main(["speaker_split", str(tmp_path), "--label=Нет такого"]) == 3
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[-1] == {"kind": "error", "text": "в записи нет спикера «Нет такого»"}


# --- анализ встречи (M2) -------------------------------------------------------


def test_analyze_job_writes_analysis_and_reports_progress(tmp_path, monkeypatch, capsys):
    import json

    import meet.llm as llm
    from meet.llm.base import AgentReply

    reply = {"phrase_types": {"0": "statement"}, "importance": {"0": 0.4},
             "chapters": [{"start_i": 0, "end_i": 0, "title": "Старт", "short": "Старт"}],
             "insights": [], "category": None, "title": "Начало работы"}

    async def runner(prompt, **kwargs):
        return AgentReply(text=json.dumps(reply, ensure_ascii=False))

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("codex", runner))
    folder = _transcribed(tmp_path, monkeypatch)
    library.write_meta(folder, {"analysis_error": {"error": "прошлый сбой", "at": 1.0}})
    assert job_worker.main(["analyze", str(folder)]) == 0
    lines = _lines(capsys)
    assert {"kind": "job.result", "path": str(folder / "analysis.json")} in lines
    assert any(x.get("kind") == "progress" and x.get("stage") == "analyze" for x in lines)
    assert json.loads((folder / "analysis.json").read_text(encoding="utf-8"))["model"] == "codex"
    assert "analysis_error" not in library.read_meta(folder)


def test_analyze_job_failure_is_remembered_for_the_window(tmp_path, monkeypatch, capsys):
    import meet.llm as llm

    monkeypatch.setattr(llm, "resolve", lambda cfg: (None, None))
    folder = _transcribed(tmp_path, monkeypatch)
    assert job_worker.main(["analyze", str(folder)]) == 2
    assert library.read_meta(folder)["analysis_error"]["error"] == "Подключите Claude Code, Codex или OpenCode в настройках"
    assert not (folder / "analysis.json").exists()


def test_analyze_job_argv_and_kind(tmp_path):
    from meet import jobs

    job = jobs.Job(id="a", kind=jobs.ANALYZE, folder=str(tmp_path))
    assert jobs.worker_argv(job)[-2:] == ["analyze", str(tmp_path)]
    assert jobs.ANALYZE in jobs.KINDS and jobs.ANALYZE in jobs.MODEL_KINDS


# --- «Улучшить расшифровку» (M8) ------------------------------------------------


def test_improve_job_writes_the_proposal(tmp_path, monkeypatch, capsys):
    import json

    import meet.llm as llm
    from meet import improve
    from meet.llm.base import AgentReply

    async def runner(prompt, **kwargs):
        return AgentReply(text='{"replacements": []}')

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("codex", runner))
    folder = _transcribed(tmp_path, monkeypatch)
    library.write_meta(folder, {"improve_error": {"error": "прошлый сбой", "at": 1.0}})
    assert job_worker.main(["improve", str(folder)]) == 0
    lines = _lines(capsys)
    assert {"kind": "job.result", "path": str(folder / improve.IMPROVE_JSON)} in lines
    assert any(x.get("kind") == "progress" and x.get("stage") == "improve" for x in lines)
    assert json.loads((folder / improve.IMPROVE_JSON).read_text(encoding="utf-8"))["groups"] == []
    assert "improve_error" not in library.read_meta(folder)


def test_improve_job_failure_is_remembered_for_the_window(tmp_path, monkeypatch, capsys):
    import meet.llm as llm

    monkeypatch.setattr(llm, "resolve", lambda cfg: (None, None))
    folder = _transcribed(tmp_path, monkeypatch)
    assert job_worker.main(["improve", str(folder)]) == 2
    assert library.read_meta(folder)["improve_error"]["error"] == "Подключите Claude Code, Codex или OpenCode в настройках"


def test_improve_job_argv_and_kind(tmp_path):
    from meet import jobs

    job = jobs.Job(id="i", kind=jobs.IMPROVE, folder=str(tmp_path))
    assert jobs.worker_argv(job)[-2:] == ["improve", str(tmp_path)]
    assert jobs.IMPROVE in jobs.KINDS and jobs.IMPROVE in jobs.MODEL_KINDS
