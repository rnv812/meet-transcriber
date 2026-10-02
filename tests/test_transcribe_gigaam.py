"""Пайплайн расшифровки с GigaAM: без выравнивания wav2vec2 (по умолчанию),
возврат латинских терминов, пометки в transcript.json, время ступеней."""

import json

import pytest

from meet import asr, library
from meet.asr import Segment, Word
from meet.diarize import Diarization


def _gigaam_segments():
    return [Segment(0.0, 1.2, "Открой апи.", words=[Word(0.0, 0.5, " Открой"), Word(0.6, 1.2, " апи.")])]


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Пайплайн с подменой звука, распознавания и диаризации: снимает,
    с чем зовут распознавание и выравнивание."""
    import meet.transcribe as tr

    calls = {"asr": [], "align": []}
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("MEET_DATA_DIR", str(state))
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)

    def fake_asr(path, hotwords=None, *, choice=None, model_name=None, **kw):
        calls["asr"].append((path.name, choice))
        calls.setdefault("models", []).append(model_name)
        if choice is not None and choice.backend == "gigaam":
            if calls.get("gigaam_fails"):
                from meet.gigaam_asr import Unavailable

                raise Unavailable("GigaAM: не удалось скачать v3_e2e_rnnt.ckpt (нет связи)")
            return _gigaam_segments()
        return [Segment(0.0, 1.0, "whisper")]

    def fake_align(segments, wav, enabled):
        calls["align"].append(enabled)
        return segments

    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "_maybe_align", fake_align)
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False: Diarization(
        turns=[(0.0, 5.0, "SPEAKER_00")]))
    monkeypatch.setattr(tr, "_match_names", lambda diar, threshold=None: {})
    return tr, calls, state


def _choose(monkeypatch, choice):
    monkeypatch.setattr(asr, "choose", lambda path=None, **kw: choice)


def _write_config(state, asr_section):
    (state / "config.json").write_text(json.dumps({"asr": asr_section}), encoding="utf-8")


def _import_folder(tmp_path):
    folder = tmp_path / "2026-10-01_10-00_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"x")
    return folder


def test_gigaam_skips_forced_alignment_by_default(pipeline, monkeypatch, tmp_path):
    tr, calls, _ = pipeline
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    tr.transcribe(str(_import_folder(tmp_path)), align=True)
    assert calls["align"] == [False]


def test_align_after_gigaam_switch_turns_alignment_back_on(pipeline, monkeypatch, tmp_path):
    tr, calls, state = pipeline
    _write_config(state, {"align_after_gigaam": True})
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    tr.transcribe(str(_import_folder(tmp_path)), align=True)
    assert calls["align"] == [True]


def test_whisper_keeps_alignment(pipeline, monkeypatch, tmp_path):
    tr, calls, _ = pipeline
    _choose(monkeypatch, asr.Choice("faster-whisper", "cpu"))
    tr.transcribe(str(_import_folder(tmp_path)), align=True)
    assert calls["align"] == [True]
    assert calls["asr"][0][1] is None  # Whisper зовётся прежним образом, с подсказками


def test_gigaam_output_gets_latin_terms_back_and_marks_transcript(pipeline, monkeypatch, tmp_path):
    tr, _, state = pipeline
    (state / "hotwords.txt").write_text("API\n", encoding="utf-8")
    monkeypatch.setattr(tr.paths, "hotwords_path", lambda: state / "hotwords.txt")
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    folder = _import_folder(tmp_path)
    tr.transcribe(str(folder), align=True)
    data = library.read_transcript_full(folder)
    assert [s["text"] for s in data["segments"]] == ["Открой API."]
    assert [w[2] for w in data["segments"][0]["words"]] == [" Открой", " API."]
    assert data["asr"] == {"backend": "gigaam", "device": "cpu", "model": "v3_e2e_rnnt"}
    assert "asr_note" not in data
    assert library.describe(folder).to_raw()["asr_note"] is None


def test_non_russian_fallback_is_noted_in_transcript_and_card(pipeline, monkeypatch, tmp_path):
    tr, _, _ = pipeline
    _choose(monkeypatch, asr.Choice("faster-whisper", "cpu", note=asr.NOT_RUSSIAN))
    folder = _import_folder(tmp_path)
    tr.transcribe(str(folder), align=False)
    data = library.read_transcript(folder)
    assert data["asr_note"] == "not_russian"
    assert data["asr"] == {"backend": "faster-whisper", "device": "cpu"}
    assert library.describe(folder).to_raw()["asr_note"] == "not_russian"


def test_two_tracks_share_one_choice(pipeline, monkeypatch, tmp_path):
    tr, calls, _ = pipeline
    chosen = []

    def choose(path=None, **kw):
        chosen.append(path.name)
        return asr.Choice("gigaam", "cpu", "v3_e2e_rnnt")

    monkeypatch.setattr(asr, "choose", choose)
    folder = tmp_path / "2026-10-01_10-00"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    tr.transcribe(str(folder), align=True)
    assert chosen == ["sys16.wav"]  # решение — один раз, по первой дорожке
    assert [name for name, _ in calls["asr"]] == ["sys16.wav", "mic16.wav"]
    assert all(c.backend == "gigaam" for _, c in calls["asr"])


def test_stage_timing_is_printed_and_sent_to_the_bus(pipeline, monkeypatch, tmp_path, capsys):
    from meet import events

    tr, _, _ = pipeline
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    bus = events.EventBus()
    logs = []
    bus.subscribe(lambda e: logs.append(e.to_dict()) if e.kind == events.LOG else None)
    tr.transcribe(str(_import_folder(tmp_path)), align=True, bus=bus)
    out = capsys.readouterr().out
    assert "время ступеней (GigaAM, cpu): распознавание" in out
    assert "выравнивание 0.0 с" in out and "диаризация" in out
    assert logs and logs[0]["source"] == "timing" and logs[0]["text"].startswith("время ступеней")


def test_translit_failure_does_not_stop_transcription(pipeline, monkeypatch, tmp_path, capsys):
    from meet import translit

    tr, _, _ = pipeline
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))

    def boom(segments, terms):
        raise ValueError("сломалось")

    monkeypatch.setattr(translit, "apply", boom)
    folder = _import_folder(tmp_path)
    tr.transcribe(str(folder), align=False)
    assert "термины латиницей пропущены" in capsys.readouterr().out
    assert library.read_transcript(folder)["segments"][0]["text"] == "Открой апи."


def test_gigaam_failure_falls_back_to_installed_whisper(pipeline, monkeypatch, tmp_path, capsys):
    """GigaAM не скачалась (нет сети, файл битый) — встреча не падает: её
    распознаёт уже скачанная модель Whisper, в карточке — пометка, в журнале —
    причина; обе дорожки — Whisper."""
    tr, calls, _ = pipeline
    calls["gigaam_fails"] = True
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    monkeypatch.setattr(asr, "local_whisper_model", lambda device: "Systran/faster-whisper-medium")
    folder = tmp_path / "2026-10-01_10-00"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    tr.transcribe(str(folder), align=True)
    out = capsys.readouterr().out
    assert "GigaAM недоступна" in out and "не удалось скачать" in out
    data = library.read_transcript(folder)
    assert data["asr_note"] == "gigaam_failed"
    assert data["asr"]["backend"] == "faster-whisper"
    assert library.describe(folder).to_raw()["asr_note"] == "gigaam_failed"
    # sys: GigaAM упала → Whisper; mic — сразу Whisper; модель — уже скачанная
    assert [c.backend if c else None for _, c in calls["asr"]] == ["gigaam", None, None]
    assert calls["models"][1:] == ["Systran/faster-whisper-medium"] * 2
    assert calls["align"] == [True]  # для Whisper выравнивание как обычно


def _no_local_whisper(monkeypatch, reachable=True):
    monkeypatch.setattr(asr, "local_whisper_model", lambda device: None)
    monkeypatch.setattr(asr, "fallback_whisper_model", lambda device: "Systran/faster-whisper-medium")
    monkeypatch.setattr(asr, "hub_reachable", lambda timeout=10: reachable)


def test_gigaam_failure_without_local_whisper_says_it_downloads(pipeline, monkeypatch, tmp_path, capsys):
    """Ни одной модели Whisper на диске: качается выбранная — и это видно в
    ходе задачи («скачивается модель Whisper (около 1,5 ГБ)») и в журнале."""
    from meet import events

    tr, calls, _ = pipeline
    calls["gigaam_fails"] = True
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    _no_local_whisper(monkeypatch)
    bus = events.EventBus()
    notes = []
    bus.subscribe(lambda e: notes.append(e.to_dict().get("note")) if e.kind == events.PROGRESS else None)
    folder = _import_folder(tmp_path)
    tr.transcribe(str(folder), align=False, bus=bus)
    note = "GigaAM недоступна — скачивается модель Whisper (около 1,5 ГБ)"
    assert note in notes and note in capsys.readouterr().out
    assert calls["models"][1:] == ["Systran/faster-whisper-medium"]
    assert library.read_transcript(folder)["segments"][0]["text"] == "whisper"


def test_no_hub_means_one_clear_error_without_a_download(pipeline, monkeypatch, tmp_path):
    tr, calls, _ = pipeline
    calls["gigaam_fails"] = True
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    _no_local_whisper(monkeypatch, reachable=False)
    with pytest.raises(SystemExit) as error:
        tr.transcribe(str(_import_folder(tmp_path)), align=False)
    text = str(error.value)
    assert text.startswith("GigaAM недоступна (GigaAM: не удалось скачать")
    assert "нет связи с Hugging Face" in text and "Проверьте подключение или прокси" in text
    assert calls["models"][1:] == []  # Whisper даже не пытались грузить


def test_failed_whisper_download_names_both_causes(pipeline, monkeypatch, tmp_path):
    tr, calls, _ = pipeline
    calls["gigaam_fails"] = True
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    _no_local_whisper(monkeypatch)
    real = tr.transcribe_wav

    def whisper_fails(path, hotwords=None, *, choice=None, model_name=None, **kw):
        if choice is None:
            raise OSError("Connection reset by peer")
        return real(path, hotwords, choice=choice, model_name=model_name, **kw)

    monkeypatch.setattr(tr, "transcribe_wav", whisper_fails)
    with pytest.raises(SystemExit) as error:
        tr.transcribe(str(_import_folder(tmp_path)), align=False)
    text = str(error.value)
    assert "GigaAM недоступна (GigaAM: не удалось скачать" in text
    assert "модель Whisper не скачалась (OSError: Connection reset by peer)" in text
    assert text.endswith("Проверьте подключение или прокси в настройках")


def test_user_rules_win_over_latin_restoring(pipeline, monkeypatch, tmp_path):
    """Правило человека «апи» → «API-шлюз» срабатывает, хотя «API» есть в
    терминах: правила замены применяются раньше возврата латиницы."""
    tr, _, state = pipeline
    (state / "config.json").write_text(json.dumps(
        {"asr": {"replacements": [{"from": "апи", "to": "API-шлюз"}]}}), encoding="utf-8")
    (state / "hotwords.txt").write_text("API\n", encoding="utf-8")
    monkeypatch.setattr(tr.paths, "hotwords_path", lambda: state / "hotwords.txt")
    _choose(monkeypatch, asr.Choice("gigaam", "cpu", "v3_e2e_rnnt"))
    folder = _import_folder(tmp_path)
    tr.transcribe(str(folder), align=False)
    assert library.read_transcript(folder)["segments"][0]["text"] == "Открой API-шлюз."
