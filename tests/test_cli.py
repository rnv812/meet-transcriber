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
    assert called["knowledge_dir"] is None
    assert called["parent_pid"] is None


def test_cli_assist_knowledge_dir_from_settings(monkeypatch, tmp_path):
    from meet import cli, settings

    called = {}
    monkeypatch.setattr("meet.assist.app.run_assist",
                        lambda out_root, **kw: called.update(kw))
    monkeypatch.setattr(settings, "load", lambda *a, **k: settings.Settings.from_raw(
        {"assistant": {"knowledge_dir": str(tmp_path)}}))
    monkeypatch.setattr("sys.argv", ["meet", "assist", "--no-browser"])
    cli.main()
    assert called["knowledge_dir"] == str(tmp_path)


def test_cli_assist_child_mode_flags(monkeypatch):
    called = _assist_call(monkeypatch, [
        "--no-browser", "--port", "0", "--endpoint-file", "C:/run/a.json",
        "--provider", "codex", "--parent-pid", "4321"])
    assert called["parent_pid"] == 4321
    assert called["port"] == 0  # 0 — эфемерный порт, а не «взять из настроек»
    assert called["open_browser"] is False
    assert called["endpoint_file"] == "C:/run/a.json"
    assert called["provider"] == "codex"


# --- паритет CLI: всё без окна ------------------------------------------------
#
# Новые команды зовутся как `cli.main([...])` и возвращают код выхода. Ни одна
# не смеет спрашивать (input) или открывать браузер: обе подменены на падение.

import json  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from meet import library  # noqa: E402
from meet.llm.base import AgentReply  # noqa: E402

RID = "2026-09-29_15-30"


def _refuse(*a, **k):
    raise AssertionError("CLI не должен ничего спрашивать и открывать")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Свой data dir с config.json: записи и голоса — во временной папке."""
    data = tmp_path / "data"
    data.mkdir()
    rec, voices, notes = tmp_path / "rec", tmp_path / "voices", tmp_path / "notes"
    rec.mkdir()
    voices.mkdir()
    notes.mkdir()
    (data / "config.json").write_text(json.dumps({
        "version": 4,
        "recording": {"out_dir": str(rec), "voices_dir": str(voices)},
        "llm": {"provider": "auto"},
        "assistant": {"notes_dir": str(notes), "notes_subdir": "Встречи"},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    monkeypatch.setattr("builtins.input", _refuse)
    monkeypatch.setattr("webbrowser.open", _refuse)
    monkeypatch.setattr("webbrowser.open_new_tab", _refuse)
    return {"rec": rec, "voices": voices, "notes": notes, "tmp": tmp_path}


def _meeting(env, rid=RID, title="Планёрка"):
    folder = env["rec"] / rid
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "title": title, "segments": [
        {"start": 5.0, "end": 7.25, "speaker": "Демьян", "text": "Начнём."},
        {"start": 65.0, "end": 66.0, "speaker": "SPEAKER_01", "text": "Согласен."},
    ]})
    return folder


def _voice(env, name):
    path = env["voices"] / f"{name}.json"
    path.write_text(json.dumps({"samples": [{"embedding": [0.1]}]}), encoding="utf-8")
    return path


def _main(argv):
    from meet import cli

    return cli.main(argv)


def _json_out(capsys):
    out = capsys.readouterr().out
    return json.loads(out)  # ровно один JSON-документ, иначе ValueError


# --- export --------------------------------------------------------------------


def test_export_srt_by_id_to_stdout(env, capsysbinary):
    _meeting(env)
    assert _main(["export", RID, "--format", "srt"]) == 0
    text = capsysbinary.readouterr().out.decode("utf-8")
    assert text.startswith("1\n00:00:05,000 --> 00:00:07,250\nДемьян: Начнём.")
    assert "Спикер 1: Согласен." in text  # сырые метки — как в окне


def test_export_md_by_path_to_file(env, capsys):
    folder = _meeting(env)
    out = env["tmp"] / "out.md"
    assert _main(["export", str(folder), "--format", "md", "--out", str(out)]) == 0
    assert "# Планёрка" in out.read_text(encoding="utf-8")
    assert capsys.readouterr().out.strip() == str(out)


def test_export_unknown_recording_is_exit_1_without_traceback(env, capsys):
    assert _main(["export", "нет-такой", "--format", "txt"]) == 1
    err = capsys.readouterr().err
    assert "Нет такой записи" in err and "Traceback" not in err


def test_export_without_transcript_is_exit_1(env, capsys):
    folder = env["rec"] / RID
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    assert _main(["export", RID, "--format", "txt"]) == 1
    assert "Транскрипта нет" in capsys.readouterr().err


def test_export_bad_format_is_argparse_error(env):
    _meeting(env)
    with pytest.raises(SystemExit) as e:
        _main(["export", RID, "--format", "docx"])
    assert e.value.code == 2


# --- voices --------------------------------------------------------------------


def test_voices_list_json_is_a_list(env, capsys):
    _meeting(env)
    _voice(env, "Демьян")
    _voice(env, "Пётр")
    assert _main(["voices", "list", "--json"]) == 0
    items = _json_out(capsys)
    assert isinstance(items, list)
    by_name = {p["name"]: p for p in items}
    assert by_name["Демьян"]["meetings"] == 1 and by_name["Демьян"]["seconds"] == 2
    assert by_name["Пётр"]["meetings"] == 0


def test_voices_list_table(env, capsys):
    _meeting(env)
    _voice(env, "Демьян")
    (env["voices"] / "Демьян.png").write_bytes(b"png")
    assert _main(["voices", "list"]) == 0
    out = capsys.readouterr().out
    assert "имя" in out and "встреч" in out and "мин речи" in out and "фото" in out
    row = next(line for line in out.splitlines() if line.startswith("Демьян"))
    assert row.split() == ["Демьян", "1", "0.0", "да"]


def test_voices_list_empty_base(env, capsys):
    assert _main(["voices", "list"]) == 0
    assert "пуста" in capsys.readouterr().out


def test_voices_delete_without_yes_refuses_and_keeps_file(env, capsys):
    path = _voice(env, "Демьян")
    assert _main(["voices", "delete", "Демьян"]) == 1
    assert "Удаление необратимо: добавьте --yes" in capsys.readouterr().err
    assert path.exists()


def test_voices_delete_with_yes(env, capsys):
    path = _voice(env, "Демьян")
    assert _main(["voices", "delete", "Демьян", "--yes"]) == 0
    assert not path.exists()


def test_voices_delete_unknown_is_exit_1(env, capsys):
    assert _main(["voices", "delete", "Никто", "--yes"]) == 1
    assert "Нет такого человека" in capsys.readouterr().err


def test_voices_rename_rewrites_transcripts(env, capsys):
    folder = _meeting(env)
    _voice(env, "Демьян")
    assert _main(["voices", "rename", "Демьян", "Демьян Петров"]) == 0
    assert (env["voices"] / "Демьян Петров.json").exists()
    speakers = [s["speaker"] for s in library.read_transcript(folder)["segments"]]
    assert "Демьян Петров" in speakers


def test_voices_rename_onto_existing_is_exit_1(env, capsys):
    _voice(env, "Демьян")
    _voice(env, "Пётр")
    assert _main(["voices", "rename", "Демьян", "Пётр"]) == 1
    assert "уже есть" in capsys.readouterr().err
    assert (env["voices"] / "Демьян.json").exists()


def test_voices_merge(env, capsys):
    _voice(env, "Даня")
    _voice(env, "Демьян")
    assert _main(["voices", "merge", "Даня", "Демьян", "--json"]) == 0
    assert _json_out(capsys) == {"ok": True, "name": "Демьян"}
    assert not (env["voices"] / "Даня.json").exists()
    samples = json.loads((env["voices"] / "Демьян.json").read_text(encoding="utf-8"))
    assert len(samples["samples"]) == 2


def test_voices_avatar_set_and_clear(env, capsys):
    import io

    from PIL import Image

    _voice(env, "Демьян")
    picture = env["tmp"] / "photo.png"
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, "PNG")
    picture.write_bytes(buf.getvalue())
    assert _main(["voices", "avatar", "Демьян", str(picture)]) == 0
    assert (env["voices"] / "Демьян.png").exists()
    assert _main(["voices", "avatar", "Демьян", "--clear"]) == 0
    assert not (env["voices"] / "Демьян.png").exists()


def test_voices_avatar_not_an_image_is_exit_1(env, capsys):
    _voice(env, "Демьян")
    junk = env["tmp"] / "junk.png"
    junk.write_bytes(b"not a picture")
    assert _main(["voices", "avatar", "Демьян", str(junk)]) == 1
    assert "не изображение" in capsys.readouterr().err


def test_voices_avatar_needs_picture_or_clear(env, capsys):
    _voice(env, "Демьян")
    assert _main(["voices", "avatar", "Демьян"]) == 1
    assert "--clear" in capsys.readouterr().err


# --- summary / ask / notes -------------------------------------------------------


def _fake_llm(monkeypatch, replies, seen=None):
    """llm.resolve → фейковый runner; настоящая модель не вызывается."""
    from meet import llm

    def resolve(cfg):
        if seen is not None:
            seen.append(cfg.llm.provider)

        async def runner(prompt, **kwargs):
            return AgentReply(text=replies.pop(0))
        return "fake", runner

    monkeypatch.setattr(llm, "resolve", resolve)


def test_summary_with_fake_runner_writes_summary_md(env, capsys, monkeypatch):
    folder = _meeting(env)
    _fake_llm(monkeypatch, ["## Итоги\n- решили X"])
    assert _main(["summary", RID]) == 0
    text = (folder / "summary.md").read_text(encoding="utf-8")
    assert "решили X" in text and "fake" in text
    assert "решили X" in capsys.readouterr().out


def test_summary_json(env, capsys, monkeypatch):
    folder = _meeting(env)
    _fake_llm(monkeypatch, ["## Итоги\n- решили X"])
    assert _main(["summary", str(folder), "--json"]) == 0
    got = _json_out(capsys)
    assert got["path"] == str(folder / "summary.md")
    assert got["provider"] == "fake" and "решили X" in got["markdown"]


def test_summary_provider_flag_overrides_settings(env, capsys, monkeypatch):
    _meeting(env)
    seen = []
    _fake_llm(monkeypatch, ["ок"], seen)
    assert _main(["summary", RID, "--provider", "codex"]) == 0
    assert seen == ["codex"]


def test_summary_without_provider_is_exit_1_with_hint(env, capsys, monkeypatch):
    from meet import llm

    folder = _meeting(env)
    monkeypatch.setattr(llm, "resolve", lambda cfg: (None, None))
    assert _main(["summary", RID]) == 1
    err = capsys.readouterr().err
    assert "Подключите Claude Code, Codex или OpenCode" in err
    assert "--provider codex" in err and "llm.provider" in err
    assert not (folder / "summary.md").exists()


def test_summary_model_error_is_exit_1(env, capsys, monkeypatch):
    from meet import llm

    _meeting(env)

    async def runner(prompt, **kwargs):
        return AgentReply(text="", error="rate_limit")

    monkeypatch.setattr(llm, "resolve", lambda cfg: ("fake", runner))
    assert _main(["summary", RID]) == 1
    assert "rate_limit" in capsys.readouterr().err


def test_ask_with_fake_runner_appends_qa(env, capsys, monkeypatch):
    folder = _meeting(env)
    _fake_llm(monkeypatch, ["В пятницу."])
    assert _main(["ask", RID, "Когда срок?"]) == 0
    assert capsys.readouterr().out.strip() == "В пятницу."
    line = json.loads((folder / "qa.jsonl").read_text(encoding="utf-8"))
    assert line["q"] == "Когда срок?" and line["a"] == "В пятницу."


def test_ask_empty_question_is_exit_1(env, capsys, monkeypatch):
    _meeting(env)
    _fake_llm(monkeypatch, ["не должно дойти"])
    assert _main(["ask", RID, "   "]) == 1
    assert "пустой вопрос" in capsys.readouterr().err


def test_notes_is_the_kb_export_into_migrated_notes_folder(env, capsys):
    """`meet notes` оставлен как синоним `meet kb-export`; прежняя папка
    заметок (notes_dir/notes_subdir) стала папкой для встреч."""
    folder = _meeting(env)
    library.write_meta(folder, {"title": "Планирование спринта"})
    assert _main(["notes", RID, "--json"]) == 0
    got = _json_out(capsys)
    assert Path(got["path"]) == env["notes"] / "Встречи" / "2026-09-29 - Планирование спринта"
    assert got["files"] == ["Транскрипт.md"]


def test_kb_export_prints_path(env, capsys):
    folder = _meeting(env)
    library.write_meta(folder, {"title": "Планирование спринта"})
    (folder / "summary.md").write_text("# Итоги\n", encoding="utf-8")
    assert _main(["kb-export", RID]) == 0
    out = capsys.readouterr().out
    target = env["notes"] / "Встречи" / "2026-09-29 - Планирование спринта"
    assert out == f"{target}\n"
    assert (target / "Итоги.md").exists()


# --- import --------------------------------------------------------------------


def _media(env, name="звонок.mp3"):
    src = env["tmp"] / name
    src.write_bytes(b"ID3 fake audio")
    return src


def test_import_no_transcribe_creates_folder_with_source(env, capsys, monkeypatch):
    import meet.transcribe

    monkeypatch.setattr(meet.transcribe, "transcribe", _refuse)
    src = _media(env)
    assert _main(["import", str(src), "--no-transcribe"]) == 0
    folder = Path(capsys.readouterr().out.strip())
    assert folder.parent == env["rec"]
    assert (folder / "source.mp3").read_bytes() == b"ID3 fake audio"
    assert library.read_meta(folder)["title"] == "звонок"
    assert src.exists()  # оригинал не трогаем


def test_import_transcribes_and_keeps_stdout_clean(env, capsys, monkeypatch):
    import meet.transcribe

    called = {}

    def fake_transcribe(path, speakers=None, hotwords=None, align=True,
                        overlap=True, bus=None):
        called.update(path=path, speakers=speakers)
        print("Готово: шум пайплайна")  # transcribe печатает в stdout
        bus.progress("asr", done=1, total=1)
        return Path(path) / "2026-09-29_transcript.md"

    monkeypatch.setattr(meet.transcribe, "transcribe", fake_transcribe)
    src = _media(env)
    assert _main(["import", str(src), "--speakers", "3", "--json"]) == 0
    captured = capsys.readouterr()
    got = json.loads(captured.out)
    assert called == {"path": got["folder"], "speakers": 3}
    assert got["transcribed"] is True
    assert "распознавание" in captured.err and "шум пайплайна" in captured.err


def test_import_transcription_failure_is_exit_1(env, capsys, monkeypatch):
    import meet.transcribe

    def broken(path, **kw):
        raise SystemExit("Нет дорожек")

    monkeypatch.setattr(meet.transcribe, "transcribe", broken)
    assert _main(["import", str(_media(env))]) == 1
    err = capsys.readouterr().err
    assert "Нет дорожек" in err and "meet transcribe" in err


def test_import_missing_file_is_exit_1(env, capsys):
    assert _main(["import", str(env["tmp"] / "нет.mp3")]) == 1
    assert "Файла нет" in capsys.readouterr().err


def test_import_unsupported_format_is_exit_1(env, capsys):
    assert _main(["import", str(_media(env, "doc.txt")), "--no-transcribe"]) == 1
    assert "не поддерживается" in capsys.readouterr().err
    assert list(env["rec"].iterdir()) == []


def test_summary_explicit_provider_unavailable_names_it(env, capsys, monkeypatch):
    from meet import llm

    _meeting(env)
    monkeypatch.setattr(llm, "resolve", lambda cfg: (None, None))
    assert _main(["summary", RID, "--provider", "openai-compatible"]) == 1
    err = capsys.readouterr().err
    assert "openai-compatible недоступен" in err and "Подключите" in err


def test_module_run_writes_utf8_even_when_stdout_is_redirected(env):
    """`python -m meet.cli` (так резидент зовёт assist) работает, а stdout,
    перенаправленный в файл/трубу, — UTF-8 даже для символов вне cp1251."""
    import os
    import subprocess

    _voice(env, "Łukasz")
    child_env = {k: v for k, v in os.environ.items()
                 if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    child_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    done = subprocess.run([sys.executable, "-m", "meet.cli", "voices", "list", "--json"],
                          capture_output=True, env=child_env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.decode("utf-8"))[0]["name"] == "Łukasz"


def test_import_copy_failure_is_exit_1_and_leaves_no_empty_card(env, capsys, monkeypatch):
    from meet import job_worker

    def broken_copy(src, dst, *a, **k):
        raise OSError("диск полон")

    monkeypatch.setattr(job_worker, "_copy_with_progress", broken_copy)
    assert _main(["import", str(_media(env)), "--no-transcribe"]) == 1
    assert "диск полон" in capsys.readouterr().err
    assert list(env["rec"].iterdir()) == []


# --- мелочи CLI: обрыв пайпа, заметки без папки, общий заголовок ----------------


class _ClosedPipe:
    """stdout, который читатель уже закрыл (`meet export … | head -1`)."""

    class _Buffer:
        def write(self, data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

    buffer = _Buffer()

    def write(self, text):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        pass


def test_closed_pipe_exits_quietly(env, capsys, monkeypatch):
    from meet import cli_library

    _meeting(env)
    args = type("A", (), {"command": "export", "folder": RID, "format": "txt",
                          "out_file": None, "json": False})()
    from meet import settings

    monkeypatch.setattr("sys.stdout", _ClosedPipe())
    assert cli_library.run(args, settings.load()) == 0
    assert capsys.readouterr().err == ""  # без трейсбека и без ругани


def test_kb_export_without_meetings_dir_says_which_setting(env, capsys, tmp_path):
    _meeting(env)
    cfg = tmp_path / "data" / "config.json"
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    raw["assistant"] = {"notes_dir": None}
    cfg.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert _main(["kb-export", RID]) == 1
    err = capsys.readouterr().err
    assert "Папка для встреч не задана" in err and "export.meetings_dir" in err
    assert "Traceback" not in err


def test_export_and_notes_share_title_and_date(env, capsys, monkeypatch):
    """Один помощник заголовка и даты: экспорт и заметка не расходятся."""
    from meet import assistant

    folder = _meeting(env)
    title, date = library.title_and_date(folder, library.read_transcript(folder))
    assert (title, date) == ("Планёрка", "2026-09-29")
    assert not hasattr(assistant, "_title_and_date")


def test_import_json_reports_skipped_diarization(env, capsys, monkeypatch):
    """Скрипту нужно знать, что спикеры не разделены (нет токена HF), не
    открывая transcript.json: пометка — в JSON результата `meet import`."""
    import meet.transcribe

    def fake_transcribe(path, speakers=None, hotwords=None, align=True,
                        overlap=True, bus=None):
        library.transcript_path(Path(path)).write_text(
            json.dumps({"version": 1, "segments": [], "diarization": "skipped_no_token"}),
            encoding="utf-8")
        return Path(path) / "2026-09-29_transcript.md"

    monkeypatch.setattr(meet.transcribe, "transcribe", fake_transcribe)
    assert _main(["import", str(_media(env)), "--json"]) == 0
    assert _json_out(capsys)["diarization"] == "skipped_no_token"


def test_import_json_has_no_diarization_mark_without_transcript(env, capsys, monkeypatch):
    import meet.transcribe

    monkeypatch.setattr(meet.transcribe, "transcribe", _refuse)
    assert _main(["import", str(_media(env)), "--no-transcribe", "--json"]) == 0
    got = _json_out(capsys)
    assert got["transcribed"] is False and got["diarization"] is None


# --- merge ---------------------------------------------------------------------


def _part(env, rid, title=None):
    folder = env["rec"] / rid
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    if title:
        library.write_meta(folder, {"title": title})
    return folder


def _fake_merge_pipeline(monkeypatch, fail_transcribe=False):
    import meet.transcribe
    from meet import merge

    def fake_run(folder, bus=None, **kw):
        (folder / "sys.opus").write_bytes(b"m")
        (folder / "mic.opus").write_bytes(b"m")
        library.update_meta(folder, lambda m: {**m, "merge": {**m["merge"], "state": "merged"}})
        return folder

    def fake_transcribe(path, speakers=None, hotwords=None, align=True, overlap=True, bus=None):
        if fail_transcribe:
            raise SystemExit("движок не установлен")
        library.write_transcript(Path(path), {"version": 1, "segments": []})
        return Path(path) / "t.md"

    monkeypatch.setattr(merge, "run", fake_run)
    monkeypatch.setattr(meet.transcribe, "transcribe", fake_transcribe)


def test_merge_joins_transcribes_and_deletes_originals(env, capsys, monkeypatch):
    _fake_merge_pipeline(monkeypatch)
    a = _part(env, "2026-09-29_15-30", title="Планёрка")
    b = _part(env, "2026-09-29_16-10")
    assert _main(["merge", b.name, str(a), "--json"]) == 0
    got = _json_out(capsys)
    folder = Path(got["folder"])
    assert folder.name == "2026-09-29_15-30_merged"
    assert got["merged_from"] == [a.name, b.name]
    assert got["deleted"] == [a.name, b.name]
    assert not a.exists() and not b.exists()
    meta = library.read_meta(folder)
    assert meta["title"] == "Планёрка" and meta["merge"]["state"] == "done"


def test_merge_keep_leaves_originals(env, capsys, monkeypatch):
    _fake_merge_pipeline(monkeypatch)
    a = _part(env, "2026-09-29_15-30")
    b = _part(env, "2026-09-29_16-10")
    assert _main(["merge", a.name, b.name, "--keep"]) == 0
    assert a.exists() and b.exists()
    assert capsys.readouterr().out.strip().endswith("2026-09-29_15-30_merged")


def test_merge_failed_transcription_keeps_originals(env, capsys, monkeypatch):
    _fake_merge_pipeline(monkeypatch, fail_transcribe=True)
    a = _part(env, "2026-09-29_15-30")
    b = _part(env, "2026-09-29_16-10")
    assert _main(["merge", a.name, b.name]) == 1
    err = capsys.readouterr().err
    assert "движок не установлен" in err and "Исходные записи сохранены" in err
    assert a.exists() and b.exists()


def test_merge_needs_two_recordings(env, capsys):
    a = _part(env, "2026-09-29_15-30")
    assert _main(["merge", a.name, a.name]) == 1
    assert "минимум две" in capsys.readouterr().err


def test_merge_refuses_a_recording_in_progress(env, capsys, monkeypatch):
    from meet.recorder import LOCK_NAME

    _fake_merge_pipeline(monkeypatch)
    a = _part(env, "2026-09-29_15-30")
    b = _part(env, "2026-09-29_16-10")
    (env["rec"] / LOCK_NAME).write_text(json.dumps({"pid": __import__("os").getpid(), "folder": str(b)}), encoding="utf-8")
    assert _main(["merge", a.name, b.name]) == 1
    assert "запись ещё идёт" in capsys.readouterr().err
    assert not list(env["rec"].glob("*_merged*"))


def test_merge_reports_originals_that_could_not_be_deleted(env, capsys, monkeypatch):
    """Исходную держит другая программа: не удаляется ни одна, объединение всё
    равно завершено (исходные снова обычные записи), причина — в meta.json."""
    from meet import merge

    _fake_merge_pipeline(monkeypatch)
    a = _part(env, "2026-09-29_15-30")
    b = _part(env, "2026-09-29_16-10")

    def held(folders, *args, **kw):
        raise library.FolderBusy(library.FOLDER_BUSY)

    monkeypatch.setattr(library, "remove_folders", held)
    assert _main(["merge", a.name, b.name, "--json"]) == 1
    captured = capsys.readouterr()
    got = json.loads(captured.out)
    assert got["deleted"] == [] and len(got["not_deleted"]) == 2
    assert "не все исходные удалены" in captured.err
    info = library.read_meta(Path(got["folder"]))["merge"]
    assert info["state"] == "done" and "агент в терминале" in info["kept_reason"]
    assert a.exists() and b.exists()
    assert merge.unfinished_owner(a) is None and merge.unfinished_owner(b) is None


def test_merge_keeps_originals_the_resident_is_working_on(env, capsys, monkeypatch):
    from meet import control

    _fake_merge_pipeline(monkeypatch)
    a = _part(env, "2026-09-29_15-30")
    b = _part(env, "2026-09-29_16-10")
    jobs_now = []
    monkeypatch.setattr(control, "request", lambda path, **kw: {"items": list(jobs_now)})
    # при старте свободны; к удалению резидент взялся за итоги второй записи
    import meet.transcribe

    real = meet.transcribe.transcribe

    def transcribe_and_busy(path, **kw):
        jobs_now.append({"kind": "summary", "folder": str(b), "state": "running"})
        return real(path, **kw)

    monkeypatch.setattr(meet.transcribe, "transcribe", transcribe_and_busy)
    assert _main(["merge", a.name, b.name]) == 1
    assert a.exists() and b.exists()
    assert "работает приложение" in capsys.readouterr().err


def test_merge_refuses_what_the_resident_is_transcribing(env, capsys, monkeypatch):
    from meet import control

    a = _part(env, "2026-09-29_15-30")
    b = _part(env, "2026-09-29_16-10")
    monkeypatch.setattr(control, "request", lambda path, **kw: {"items": [
        {"kind": "transcribe", "folder": str(a), "state": "queued"}]})
    assert _main(["merge", a.name, b.name]) == 1
    assert "работает приложение" in capsys.readouterr().err
    assert not list(env["rec"].glob("*_merged*"))


# --- fix -----------------------------------------------------------------------


def _fix_meeting(env):
    folder = env["rec"] / RID
    folder.mkdir()
    library.write_transcript(folder, {"version": 1, "created_at": "2026-09-01T10:00:00", "segments": [
        {"start": 0.0, "end": 2.0, "speaker": "Демьян", "text": "Поднимем кубер нетис."},
        {"start": 2.0, "end": 4.0, "speaker": "Анна", "text": "Кубер нетис готов."},
    ]})
    return folder


def _texts(folder):
    return [s["text"] for s in library.read_transcript(folder)["segments"]]


def test_fix_first_occurrence_by_default(env, capsys, monkeypatch):
    folder = _fix_meeting(env)
    assert _main(["fix", RID, "кубер нетис", "Kubernetes"]) == 0
    assert _texts(folder) == ["Поднимем Kubernetes.", "Кубер нетис готов."]
    out = capsys.readouterr().out
    assert "Исправлено: 1 из 2" in out and "--all" in out


def test_fix_all_with_hotword_json(env, capsys, monkeypatch):
    from meet import paths

    target = env["tmp"] / "hotwords.txt"
    monkeypatch.setattr(paths, "hotwords_path", lambda: target)
    folder = _fix_meeting(env)
    assert _main(["fix", str(folder), "кубер нетис", "kubernetes", "--all", "--hotword", "--json"]) == 0
    got = _json_out(capsys)
    assert got["changed"] == 2 and got["found"] == 2
    assert got["hotword"] == {"term": "kubernetes", "added": True, "over_budget": False}
    assert _texts(folder) == ["Поднимем kubernetes.", "Kubernetes готов."]
    assert target.read_text(encoding="utf-8") == "kubernetes\n"


def test_fix_not_found_is_exit_1(env, capsys):
    folder = _fix_meeting(env)
    assert _main(["fix", RID, "Docker", "Докер"]) == 1
    assert "нет «Docker»" in capsys.readouterr().err
    assert _texts(folder) == ["Поднимем кубер нетис.", "Кубер нетис готов."]


def test_fix_rule_for_future_transcriptions(env, capsys):
    from meet import settings

    folder = _fix_meeting(env)
    assert _main(["fix", RID, "кубер нетис", "Kubernetes", "--all", "--rule"]) == 0
    assert "Правило для будущих расшифровок: кубер нетис → Kubernetes" in capsys.readouterr().out
    assert list(settings.load().asr.replacements) == [{"from": "кубер нетис", "to": "Kubernetes"}]
    assert _texts(folder) == ["Поднимем Kubernetes.", "Kubernetes готов."]


def test_fix_refuses_a_recording_in_progress_without_the_app(env, capsys, monkeypatch):
    from meet import cli_library

    folder = _fix_meeting(env)
    monkeypatch.setattr(cli_library, "_recording_now", lambda root: folder)
    assert _main(["fix", RID, "кубер нетис", "Kubernetes"]) == 1
    assert "запись ещё идёт" in capsys.readouterr().err
    assert _texts(folder) == ["Поднимем кубер нетис.", "Кубер нетис готов."]


def test_fix_goes_through_the_running_app(env, capsys, monkeypatch):
    from meet import control

    folder = _fix_meeting(env)
    calls = []

    def fake_request(path, method="GET", payload=None, timeout=5.0):
        calls.append((path, payload))
        if path.endswith("/text/preview"):
            return {"count": 2, "samples": [{"segment": 0, "offset": 9}], "here": None}
        return {"changed": 1, "step": {"id": "s1"}, "hotword": {"term": "Kubernetes", "added": True},
                "rule": None}

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", fake_request)
    assert _main(["fix", RID, "кубер нетис", "Kubernetes", "--hotword", "--json"]) == 0
    got = _json_out(capsys)
    assert got["changed"] == 1 and got["step"] == "s1" and got["via_app"] is True
    assert [c[0] for c in calls] == [f"/recordings/{RID}/text/preview", f"/recordings/{RID}/text/apply"]
    assert calls[1][1]["scope"] == "one" and calls[1][1]["add_hotword"] is True
    assert (calls[1][1]["segment"], calls[1][1]["offset"]) == (0, 9)
    assert _texts(folder) == ["Поднимем кубер нетис.", "Кубер нетис готов."]  # сам файл не трогали


def test_fix_reports_the_apps_refusal(env, capsys, monkeypatch):
    from meet import control

    _fix_meeting(env)

    def busy(path, method="GET", payload=None, timeout=5.0):
        raise RuntimeError("резидент ответил 409: Идёт расшифровка — отмените её или дождитесь")

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", busy)
    assert _main(["fix", RID, "кубер нетис", "Kubernetes"]) == 1
    assert "Идёт расшифровка — отмените её или дождитесь" in capsys.readouterr().err


def test_fix_help_mentions_the_knowledge_base(capsys):
    with pytest.raises(SystemExit):
        _main(["fix", "--help"])
    assert "базу знаний" in capsys.readouterr().out


# --- анализ встречи и название (M2) --------------------------------------------

ANALYSIS_REPLY = json.dumps({
    "phrase_types": {"0": "question"}, "importance": {"0": 0.9},
    "chapters": [{"start_i": 0, "end_i": 1, "title": "Старт", "short": "Старт"}],
    "insights": [], "category": {"id": "daily", "confidence": 0.9}, "title": "Утренний старт"},
    ensure_ascii=False)


def _auto_title(env):
    path = Path(os.environ["MEET_DATA_DIR"]) / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["assistant"]["auto_title"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_analyze_without_the_app_writes_analysis_json(env, capsys, monkeypatch):
    folder = _meeting(env)
    _fake_llm(monkeypatch, [ANALYSIS_REPLY])
    assert _main(["analyze", RID, "--json"]) == 0
    got = _json_out(capsys)
    assert got["via_app"] is False and got["analysis"]["title"] == "Утренний старт"
    assert json.loads((folder / "analysis.json").read_text(encoding="utf-8"))["chapters"][0]["title"] == "Старт"
    # «Придумывать название» выключено — название записи не трогаем
    assert "title" not in library.read_meta(folder)


def test_analyze_applies_ai_title_when_enabled(env, capsys, monkeypatch):
    folder = _meeting(env)
    _auto_title(env)
    _fake_llm(monkeypatch, [ANALYSIS_REPLY])
    assert _main(["analyze", RID]) == 0
    assert "Главы: 1" in capsys.readouterr().out
    meta = library.read_meta(folder)
    assert meta["title"] == "Утренний старт" and meta["title_source"] == "ai"


def test_analyze_model_failure_is_exit_1(env, capsys, monkeypatch):
    folder = _meeting(env)
    _fake_llm(monkeypatch, ["не JSON", "опять не JSON"])
    assert _main(["analyze", RID]) == 1
    assert "Анализ не получился" in capsys.readouterr().err
    assert "analysis_error" in library.read_meta(folder)


def test_analyze_goes_through_the_running_app(env, capsys, monkeypatch):
    from meet import control

    _meeting(env)
    calls, states = [], [{"state": "queued"}, {"state": "running"},
                         {"state": "ready", "analysis": {"title": "Утренний старт", "chapters": []}}]

    def fake_request(path, method="GET", payload=None, timeout=5.0):
        calls.append((method, path))
        return {"id": "a1", "kind": "analyze"} if method == "POST" else states.pop(0)

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", fake_request)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert _main(["analyze", RID, "--json"]) == 0
    got = _json_out(capsys)
    assert got["via_app"] is True and got["analysis"]["title"] == "Утренний старт"
    assert calls[0] == ("POST", f"/recordings/{RID}/analysis")
    assert calls[-1] == ("GET", f"/recordings/{RID}/analysis")


def test_analyze_reports_the_apps_failure(env, capsys, monkeypatch):
    from meet import control

    _meeting(env)

    def fake_request(path, method="GET", payload=None, timeout=5.0):
        return {"id": "a1"} if method == "POST" else {"state": "failed", "error": "таймаут вызова модели"}

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", fake_request)
    assert _main(["analyze", RID]) == 1
    assert "таймаут вызова модели" in capsys.readouterr().err


def test_title_suggests_and_applies_without_the_app(env, capsys, monkeypatch):
    folder = _meeting(env)
    _fake_llm(monkeypatch, ["«Утренний старт»", "Утренний старт"])
    assert _main(["title", RID]) == 0
    assert capsys.readouterr().out == "Утренний старт\n"
    assert "title" not in library.read_meta(folder)
    assert _main(["title", RID, "--apply", "--json"]) == 0
    got = _json_out(capsys)
    assert got["applied"] is True and got["from"] == "model"
    meta = library.read_meta(folder)
    assert meta["title"] == "Утренний старт" and meta["title_source"] == "ai"


def test_title_through_the_running_app(env, capsys, monkeypatch):
    from meet import control

    _meeting(env)
    calls = []

    def fake_request(path, method="GET", payload=None, timeout=5.0):
        calls.append((method, path, payload))
        if path.endswith("/title/suggest"):
            return {"title": "Утренний старт", "from": "analysis"}
        return {"id": RID, "title": payload["title"], "title_source": "ai"}

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", fake_request)
    assert _main(["title", RID, "--apply"]) == 0
    assert calls == [("POST", f"/recordings/{RID}/title/suggest", {}),
                     ("PATCH", f"/recordings/{RID}", {"title": "Утренний старт", "title_source": "ai"})]


def test_summary_applies_the_title_line_when_enabled(env, capsys, monkeypatch):
    folder = _meeting(env)
    _auto_title(env)
    _fake_llm(monkeypatch, ["Название: Утренний старт\n## Итоги\n- решили X"])
    assert _main(["summary", RID]) == 0
    assert "Название:" not in (folder / "summary.md").read_text(encoding="utf-8")
    assert library.read_meta(folder)["title"] == "Утренний старт"


# --- «Улучшить расшифровку» -------------------------------------------------------

IMPROVE_REPLY = json.dumps({"replacements": [
    {"find": "апи", "replace": "API", "kind": "term", "segments": [0], "confidence": 0.9},
    {"find": "согласен", "replace": "согласна", "kind": "fix", "segments": [1], "confidence": 0.7},
]}, ensure_ascii=False)


def _improve_meeting(env):
    folder = env["rec"] / RID
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 3.0, "speaker": "Демьян", "text": "Апи готов, апи шлюза тоже."},
        {"start": 3.0, "end": 5.0, "speaker": "SPEAKER_01", "text": "Согласен."},
    ]})
    return folder


def test_improve_lists_groups_and_applies_only_terms_without_all(env, capsys, monkeypatch):
    folder = _improve_meeting(env)
    _fake_llm(monkeypatch, [IMPROVE_REPLY])
    assert _main(["improve", RID]) == 0
    assert capsys.readouterr().out == "апи → API · 2\n"
    assert library.read_transcript(folder)["segments"][0]["text"].startswith("Апи")
    _fake_llm(monkeypatch, [IMPROVE_REPLY])
    assert _main(["improve", RID, "--apply", "--json"]) == 0
    got = _json_out(capsys)
    assert got["applied"]["changed"] == 2 and [g["find"] for g in got["groups"]] == ["апи"]
    texts = [s["text"] for s in library.read_transcript(folder)["segments"]]
    assert texts == ["API готов, API шлюза тоже.", "Согласен."]


def test_improve_all_includes_recognition_fixes(env, capsys, monkeypatch):
    folder = _improve_meeting(env)
    _fake_llm(monkeypatch, [IMPROVE_REPLY])
    assert _main(["improve", RID, "--all", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "согласен → согласна · 1 (исправление)" in out and "Применено: 3 замены;" in out
    assert library.read_transcript(folder)["segments"][1]["text"] == "Согласна."


def test_improve_model_failure_is_exit_1(env, capsys, monkeypatch):
    folder = _improve_meeting(env)
    _fake_llm(monkeypatch, ["не JSON", "опять не JSON"])
    assert _main(["improve", RID]) == 1
    assert "Улучшение не получилось" in capsys.readouterr().err
    assert "improve_error" in library.read_meta(folder)


def test_improve_goes_through_the_running_app(env, capsys, monkeypatch):
    from meet import control

    _improve_meeting(env)
    calls = []
    states = [{"state": "running"}, {"state": "ready", "proposal": {"groups": [
        {"id": "g1", "find": "апи", "replace": "API", "kind": "term", "count": 2},
        {"id": "g2", "find": "согласен", "replace": "согласна", "kind": "fix", "count": 1}]}}]

    def fake_request(path, method="GET", payload=None, timeout=5.0):
        calls.append((method, path, payload))
        if path.endswith("/improve/apply"):
            return {"changed": 2, "step": {"id": "s1"}}
        return {"id": "i1", "kind": "improve"} if method == "POST" else states.pop(0)

    monkeypatch.setattr(control, "alive", lambda *a, **k: True)
    monkeypatch.setattr(control, "request", fake_request)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert _main(["improve", RID, "--apply", "--json"]) == 0
    got = _json_out(capsys)
    assert got["via_app"] is True and got["applied"] == {"changed": 2, "step": "s1"}
    assert calls[0][:2] == ("POST", f"/recordings/{RID}/improve")
    assert calls[-1] == ("POST", f"/recordings/{RID}/improve/apply", {"groups": ["g1"]})


# --- текст до спикеров (Р4): CLI его не принимает за расшифровку ------------------


def _text_phase_meeting(env):
    folder = env["rec"] / RID
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "title": "Планёрка", "phase": "text",
                                      "created_at": "2026-09-29T15:40:00", "segments": [
        {"start": 5.0, "end": 7.25, "speaker": None, "text": "Поднимем кубер нетис."}]})
    return folder


@pytest.mark.parametrize("argv", [
    ["fix", RID, "кубер нетис", "Kubernetes"],
    ["export", RID, "--format", "md"],
    ["analyze", RID],
    ["title", RID],
], ids=["fix", "export", "analyze", "title"])
def test_cli_refuses_text_phase(env, capsys, argv):
    folder = _text_phase_meeting(env)
    before = (folder / library.TRANSCRIPT_JSON).read_bytes()
    assert _main(argv) == 1
    assert "Спикеры ещё не определены" in capsys.readouterr().err
    assert (folder / library.TRANSCRIPT_JSON).read_bytes() == before
    meta = library.read_meta(folder)
    assert "analysis_error" not in meta and "speaker_history" not in meta
