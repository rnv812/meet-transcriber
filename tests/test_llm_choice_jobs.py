"""Модель, выбранная для одного действия, — в задаче (U3): `--provider` у
подпроцесса, проверка без перехода на другую модель и происхождение каждого
результата (какая модель его сделала)."""

import json

import pytest

import meet.llm as llm
from meet import assistant, improve, job_worker, jobs, library, settings
from meet.llm.base import AgentReply


def _transcribed(tmp_path, monkeypatch, llm_raw=None):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    if llm_raw is not None:
        settings.save(settings.Settings.from_raw({"version": 2, "llm": llm_raw}))
    folder = tmp_path / "rec" / "2026-10-06_10-00"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Демьян", "text": "Начнём."}]})
    return folder


def _lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


@pytest.mark.parametrize("kind", [jobs.SUMMARY, jobs.ANALYZE, jobs.IMPROVE])
def test_worker_argv_carries_the_chosen_provider(tmp_path, kind):
    job = jobs.Job(id="j", kind=kind, folder=str(tmp_path), options={"provider": "claude-code"})
    assert jobs.worker_argv(job)[-1] == "--provider=claude-code"
    plain = jobs.Job(id="j", kind=kind, folder=str(tmp_path))
    assert not any(a.startswith("--provider") for a in jobs.worker_argv(plain))


def test_worker_argv_of_ask_keeps_question_and_provider(tmp_path):
    job = jobs.Job(id="a", kind=jobs.ASK, folder=str(tmp_path),
                   options={"question": "-что решили?", "provider": "codex"})
    assert jobs.worker_argv(job)[-2:] == ["--question=-что решили?", "--provider=codex"]


def test_job_reports_its_chosen_provider(tmp_path):
    job = jobs.Job(id="j", kind=jobs.ANALYZE, folder=str(tmp_path), options={"provider": "codex"})
    assert job.to_raw()["provider"] == "codex"
    assert "provider" not in jobs.Job(id="j", kind=jobs.ANALYZE, folder=str(tmp_path)).to_raw()


def _runner(text):
    async def runner(prompt, **kwargs):
        return AgentReply(text=text)
    return runner


def test_analyze_with_chosen_provider_uses_it_and_records_it(tmp_path, monkeypatch, capsys):
    folder = _transcribed(tmp_path, monkeypatch, {
        "provider": "openai-compatible", "model": "opus", "enabled": ["claude-code", "openai-compatible"]})
    reply = {"phrase_types": {}, "importance": {}, "chapters": [], "insights": [], "category": None,
             "title": "Старт"}
    calls = []

    def fake_resolve(cfg, provider=None):
        calls.append(provider)
        return provider, _runner(json.dumps(reply, ensure_ascii=False))

    monkeypatch.setattr(llm, "resolve", fake_resolve)
    monkeypatch.setattr(llm, "choice_error", lambda cfg, name: None)
    assert job_worker.main(["analyze", str(folder), "--provider=claude-code"]) == 0
    assert calls == ["claude-code"]
    doc = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    assert doc["llm"] == {"provider": "claude-code", "model": "opus"}
    assert doc["model"] == "claude-code:opus"


def test_unavailable_choice_fails_the_job_without_falling_back(tmp_path, monkeypatch, capsys):
    """Выбранной модели нет — задача падает с её ошибкой; модель по умолчанию
    не зовётся."""
    folder = _transcribed(tmp_path, monkeypatch, {
        "provider": "openai-compatible", "enabled": ["codex", "openai-compatible"]})
    called = []
    monkeypatch.setattr(llm, "resolve", lambda cfg, provider=None: called.append(provider) or (None, None))
    monkeypatch.setattr(llm, "choice_error", lambda cfg, name: "не найден Codex CLI (codex)")
    for kind in ("analyze", "improve", "summary"):
        assert job_worker.main([kind, str(folder), "--provider=codex"]) == 2
        assert _lines(capsys)[-1] == {"kind": "error", "text": "не найден Codex CLI (codex)"}
    assert called == []
    assert library.read_meta(folder)["analysis_error"]["error"] == "не найден Codex CLI (codex)"
    assert library.read_meta(folder)["improve_error"]["error"] == "не найден Codex CLI (codex)"
    assert not (folder / "analysis.json").exists()


def test_disabled_choice_is_refused_by_the_worker(tmp_path, monkeypatch, capsys):
    """Настоящая проверка: модель выключили, пока задача ждала, — задача падает."""
    folder = _transcribed(tmp_path, monkeypatch, {"provider": "openai-compatible"})
    assert job_worker.main(["analyze", str(folder), "--provider=claude-code"]) == 2
    assert "не включена" in _lines(capsys)[-1]["text"]


def test_failing_chosen_model_is_the_job_error(tmp_path, monkeypatch, capsys):
    folder = _transcribed(tmp_path, monkeypatch, {
        "provider": "openai-compatible", "enabled": ["claude-code", "openai-compatible"]})
    seen = []

    async def broken(prompt, **kwargs):
        return AgentReply(text="", error="rate_limit")

    def fake_resolve(cfg, provider=None):
        seen.append(provider)
        return provider, broken

    monkeypatch.setattr(llm, "resolve", fake_resolve)
    monkeypatch.setattr(llm, "choice_error", lambda cfg, name: None)
    assert job_worker.main(["summary", str(folder), "--provider=claude-code"]) == 1
    assert _lines(capsys)[-1]["text"] == "rate_limit"
    assert seen == ["claude-code"]


def test_summary_records_its_model(tmp_path, monkeypatch, capsys):
    folder = _transcribed(tmp_path, monkeypatch, {"provider": "claude-code", "model": "opus"})
    monkeypatch.setattr(llm, "resolve", lambda cfg, provider=None: ("claude-code", _runner("## Итоги\n- да")))
    assert job_worker.main(["summary", str(folder)]) == 0
    text = (folder / "summary.md").read_text(encoding="utf-8")
    assert "_Модель: Claude Code (opus) · " in text
    assert library.read_meta(folder)["summary_llm"] == {"provider": "claude-code", "model": "opus"}
    assert assistant.read_summary(folder)["llm"] == {"provider": "claude-code", "model": "opus"}


def test_ask_records_its_model(tmp_path, monkeypatch, capsys):
    folder = _transcribed(tmp_path, monkeypatch, {"provider": "claude-code", "model": "opus"})
    monkeypatch.setattr(llm, "resolve", lambda cfg, provider=None: ("claude-code", _runner("ответ")))
    assert job_worker.main(["ask", str(folder), "--question=что решили?"]) == 0
    item = assistant.read_qa(folder)[-1]
    assert item["provider"] == "claude-code" and item["model"] == "opus"


def test_improve_records_its_model(tmp_path, monkeypatch, capsys):
    folder = _transcribed(tmp_path, monkeypatch, {
        "provider": "openai-compatible", "local_model": "qwen3"})
    monkeypatch.setattr(llm, "resolve", lambda cfg, provider=None: (
        "openai-compatible", _runner('{"replacements": []}')))
    assert job_worker.main(["improve", str(folder)]) == 0
    doc = json.loads((folder / improve.IMPROVE_JSON).read_text(encoding="utf-8"))
    assert doc["llm"] == {"provider": "openai-compatible", "model": "qwen3"}
    assert improve.public(doc)["llm"] == doc["llm"]


def test_summary_without_choice_uses_default_resolve(tmp_path, monkeypatch, capsys):
    folder = _transcribed(tmp_path, monkeypatch, {"provider": "codex"})
    seen = []

    def fake_resolve(cfg, provider=None):
        seen.append(provider)
        return "codex", _runner("## Итоги")

    monkeypatch.setattr(llm, "resolve", fake_resolve)
    assert job_worker.main(["summary", str(folder)]) == 0
    assert seen == [None]
    assert library.read_meta(folder)["summary_llm"] == {"provider": "codex", "model": None}


def test_failure_remembers_the_chosen_model_for_retry(tmp_path, monkeypatch, capsys):
    """«Повторить» после сбоя выбранной модели — ею же (fix round 1, I2)."""
    from meet import analysis

    folder = _transcribed(tmp_path, monkeypatch, {
        "provider": "openai-compatible", "enabled": ["claude-code", "openai-compatible"]})
    monkeypatch.setattr(llm, "choice_error", lambda cfg, name: "не найден Claude Code")
    assert job_worker.main(["analyze", str(folder), "--provider=claude-code"]) == 2
    assert job_worker.main(["improve", str(folder), "--provider=claude-code"]) == 2
    assert analysis.state(folder)["provider"] == "claude-code"
    assert improve.state(folder)["provider"] == "claude-code"
    # Без выбора — ключа нет: повтор моделью по умолчанию.
    monkeypatch.setattr(llm, "resolve", lambda cfg, provider=None: (None, None))
    assert job_worker.main(["analyze", str(folder)]) == 2
    assert "provider" not in analysis.state(folder)


def test_cancel_queued_never_kills_a_started_job(tmp_path):
    import threading

    release = threading.Event()
    queue = jobs.JobQueue(spawn=lambda job, on_line: release.wait(5) and 0)
    try:
        first = queue.submit(jobs.ANALYZE, str(tmp_path / "a"))
        second = queue.submit(jobs.ANALYZE, str(tmp_path / "b"))
        import time

        deadline = time.monotonic() + 5
        while queue.get(first.id).state != jobs.RUNNING and time.monotonic() < deadline:
            time.sleep(0.01)
        assert queue.cancel_queued(first.id) is False
        assert queue.get(first.id).state == jobs.RUNNING
        assert queue.cancel_queued(second.id) is True
        assert queue.get(second.id).state == jobs.CANCELLED
    finally:
        release.set()
        queue.stop()


def test_analyze_outcome_goes_to_the_resident_log(tmp_path, monkeypatch, capsys):
    # Чего модель не дала и что отброшено — строкой в resident.log: без
    # analysis.json пользователя по журналу видно, что случилось.
    folder = _transcribed(tmp_path, monkeypatch, {"provider": "claude-code"})
    reply = {"phrase_types": {"0": "task", "9": "risk"}, "importance": {}, "insights": [],
             "category": None, "title": "Старт"}
    monkeypatch.setattr(llm, "resolve", lambda cfg, provider=None: ("claude-code", _runner(json.dumps(reply))))
    assert job_worker.main(["analyze", str(folder)]) == 0
    logs = [line for line in _lines(capsys) if line.get("kind") == "log" and line.get("source") == "analysis"]
    assert len(logs) == 1
    text = logs[0]["text"]
    assert "claude-code:sonnet" in text and "не дала: главы" in text and "отброшено: types 1" in text
    assert "analysis" in jobs.RESIDENT_LOG_SOURCES
