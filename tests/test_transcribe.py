from pathlib import Path

import numpy as np
import pytest

from meet.asr import Segment
from meet.diarize import Diarization
from meet.transcribe import _apply_names, _find_track, _match_names, _maybe_align, _write_sidecar
from meet.voices import read_sidecar, sidecar_path


def test_find_track_returns_opus(tmp_path):
    (tmp_path / "sys.opus").write_bytes(b"x")
    assert _find_track(tmp_path, "sys") == tmp_path / "sys.opus"


def test_find_track_falls_back_to_legacy_wav(tmp_path):
    (tmp_path / "mic.wav").write_bytes(b"x")
    assert _find_track(tmp_path, "mic") == tmp_path / "mic.wav"


def test_find_track_prefers_opus_over_wav(tmp_path):
    (tmp_path / "sys.wav").write_bytes(b"x")
    (tmp_path / "sys.opus").write_bytes(b"x")
    assert _find_track(tmp_path, "sys") == tmp_path / "sys.opus"


def test_find_track_missing_returns_none(tmp_path):
    assert _find_track(tmp_path, "sys") is None


def test_maybe_align_disabled_returns_input_unchanged():
    segs = [Segment(0.0, 1.0, "привет")]
    assert _maybe_align(segs, "x.wav", enabled=False) is segs


def test_maybe_align_falls_back_on_error(monkeypatch):
    segs = [Segment(0.0, 1.0, "привет")]
    import meet.align

    def boom(*a, **k):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(meet.align, "align_segments", boom)
    # ошибка alignment не должна ронять транскрибацию — откат на исходные сегменты
    assert _maybe_align(segs, "x.wav", enabled=True) is segs


def test_apply_names_renames_matched_labels():
    turns = [(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")]
    got = _apply_names(turns, {"SPEAKER_00": "Демьян Петров"})
    assert got == [(0.0, 1.0, "Демьян Петров"), (1.0, 2.0, "SPEAKER_01")]


def test_apply_names_empty_map_returns_turns():
    turns = [(0.0, 1.0, "SPEAKER_00")]
    assert _apply_names(turns, {}) == turns


def test_match_names_no_embeddings_returns_empty():
    assert _match_names(Diarization(turns=[], embeddings=None)) == {}


def test_match_names_empty_base_returns_empty(monkeypatch):
    import meet.voices

    monkeypatch.setattr(meet.voices, "load_voices", lambda *a, **k: {})
    diar = Diarization(turns=[], embeddings={"SPEAKER_00": [1.0]})
    assert _match_names(diar) == {}


def test_match_names_error_does_not_crash(monkeypatch, capsys):
    import meet.voices

    def boom(*a, **k):
        raise RuntimeError("битая база")

    monkeypatch.setattr(meet.voices, "load_voices", boom)
    diar = Diarization(turns=[], embeddings={"SPEAKER_00": [1.0]})
    assert _match_names(diar) == {}
    assert "матчинг пропущен" in capsys.readouterr().out


def test_write_sidecar_display_matches_transcript_names(tmp_path):
    out_md = tmp_path / "2026-07-01_transcript.md"
    # в сегментах SPEAKER_01 появляется раньше SPEAKER_00 → он «Спикер 1»;
    # SPEAKER_02 совпал с базой и уже переименован в сегментах
    segments = [
        Segment(0.0, 1.0, "а", "SPEAKER_01"),
        Segment(1.0, 2.0, "б", "Демьян Петров"),
        Segment(2.0, 3.0, "в", "SPEAKER_00"),
    ]
    diar = Diarization(
        turns=[],
        embeddings={
            "SPEAKER_00": np.asarray([1.0]),
            "SPEAKER_01": np.asarray([2.0]),
            "SPEAKER_02": np.asarray([3.0]),
        },
    )
    _write_sidecar(out_md, Path("recordings/x"), "2026-07-01", segments, diar, {"SPEAKER_02": "Демьян Петров"})
    data = read_sidecar(sidecar_path(out_md))
    display = {s["label"]: s["display"] for s in data["speakers"]}
    assert display == {"SPEAKER_01": "Спикер 1", "SPEAKER_00": "Спикер 2", "SPEAKER_02": "Демьян Петров"}


def test_write_sidecar_no_embeddings_writes_nothing(tmp_path):
    out_md = tmp_path / "x.md"
    _write_sidecar(out_md, Path("r"), "2026-07-01", [], Diarization(turns=[]), {})
    assert not sidecar_path(out_md).exists()


def test_write_sidecar_warns_when_no_embeddings(tmp_path, capsys):
    out_md = tmp_path / "x.md"
    diar = Diarization(turns=[(0.0, 1.0, "SPEAKER_00")], embeddings=None)
    _write_sidecar(out_md, Path("r"), "2026-07-01", [], diar, {})
    assert not sidecar_path(out_md).exists()
    assert "не вернул эмбеддинги" in capsys.readouterr().out


def test_no_embeddings_warning_survives_cp866(tmp_path, capsys):
    # предупреждение должно печататься и в консоли cp866 (без «→» и «—»)
    diar = Diarization(turns=[(0.0, 1.0, "SPEAKER_00")], embeddings=None)
    _write_sidecar(tmp_path / "x.md", Path("r"), "2026-07-01", [], diar, {})
    capsys.readouterr().out.encode("cp866")


def _run_single_capturing(monkeypatch, tmp_path, **kwargs):
    """Запустить _transcribe_single с заглушками аудио/моделей и снять,
    что дошло до diarize_wav (exclusive) и split_by_speaker (overlaps)."""
    import meet.transcribe as tr

    calls = {}
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(
        tr, "transcribe_wav", lambda p, h, **kw: [Segment(0.0, 1.0, "привет")]
    )

    def fake_diarize(path, num_speakers=None, exclusive=False, **kw):
        calls["exclusive"] = exclusive
        return Diarization(
            turns=[(0.0, 1.0, "SPEAKER_00")],
            overlaps=None if exclusive else [(0.4, 0.8)],
        )

    def fake_split(segments, turns, overlaps=None):
        calls["overlaps"] = overlaps
        return segments

    monkeypatch.setattr(tr, "diarize_wav", fake_diarize)
    monkeypatch.setattr(tr, "split_by_speaker", fake_split)
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    tr._transcribe_single(src, None, None, align=False, **kwargs)
    return calls


def test_transcribe_single_default_is_overlap_aware(monkeypatch, tmp_path):
    calls = _run_single_capturing(monkeypatch, tmp_path)
    assert calls["exclusive"] is False
    assert calls["overlaps"] == [(0.4, 0.8)]


def test_transcribe_single_no_overlap_goes_exclusive(monkeypatch, tmp_path):
    calls = _run_single_capturing(monkeypatch, tmp_path, overlap=False)
    assert calls["exclusive"] is True
    assert calls["overlaps"] is None


# --- прогресс по ступеням пайплайна ---


def _progress_stages(monkeypatch, tmp_path, folder=False, align=False):
    """Прогнать пайплайн с заглушками и снять последовательность ступеней."""
    import meet.transcribe as tr
    from meet import events

    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [Segment(0.0, 1.0, "а")])
    monkeypatch.setattr(
        tr, "diarize_wav",
        lambda p, num_speakers=None, exclusive=False, **kw: Diarization(turns=[]),
    )
    monkeypatch.setattr(tr, "split_by_speaker", lambda s, t, o=None: s)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled: s)

    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    if folder:
        (tmp_path / "sys.opus").write_bytes(b"x")
        (tmp_path / "mic.opus").write_bytes(b"x")
        tr.transcribe(str(tmp_path), align=align, bus=bus)
    else:
        src = tmp_path / "a.wav"
        src.write_bytes(b"x")
        tr.transcribe(str(src), align=align, bus=bus)
    return [e.data["stage"] for e in seen if e.kind == "progress"], seen


def test_progress_reports_pipeline_order_for_folder(monkeypatch, tmp_path):
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True)
    # порядок ступеней, как их проходит двухдорожечный пайплайн
    assert stages[0] == "convert" and stages[-1] == "render"
    assert stages.index("diarize") < stages.index("voices")
    assert "asr" in stages


def test_progress_skips_align_when_disabled(monkeypatch, tmp_path):
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True, align=False)
    assert "align" not in stages


def test_progress_reports_align_when_enabled(monkeypatch, tmp_path):
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True, align=True)
    assert "align" in stages


def test_progress_counts_both_tracks(monkeypatch, tmp_path):
    """Две дорожки — два шага распознавания: собеседники и микрофон."""
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=True)
    asr_labels = [e.data["label"] for e in seen if e.kind == "progress" and e.data["stage"] == "asr"]
    assert "распознавание собеседников" in asr_labels and "распознавание микрофона" in asr_labels
    convert = [e for e in seen if e.data.get("stage") == "convert"]
    assert convert[0].data["step"] == 1 and convert[-1].data["done"] == 1


def _progress(seen):
    return [e.data for e in seen if e.kind == "progress"]


def test_progress_is_one_monotonic_scale_with_step_numbers(monkeypatch, tmp_path):
    """Общая доля не убывает от первого события до последнего и кончается
    единицей; номер шага растёт, а их число не меняется, если ничего не
    пропущено (выравнивание включено, диаризация есть)."""
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=True, align=True)
    events_ = _progress(seen)
    fractions = [e["fraction"] for e in events_]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert {e["steps"] for e in events_} == {7}
    steps = [e["step"] for e in events_]
    assert steps == sorted(steps) and steps[0] == 1 and steps[-1] == 7
    order = list(dict.fromkeys((e["stage"], e["label"]) for e in events_))
    assert [s for s, _ in order] == ["convert", "asr", "align", "diarize", "voices", "asr", "render"]


def test_progress_without_align_has_one_step_less(monkeypatch, tmp_path):
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=False, align=False)
    events_ = _progress(seen)
    assert {e["steps"] for e in events_} == {5}
    assert "align" not in {e["stage"] for e in events_}


def test_progress_drops_voices_when_diarization_is_skipped(monkeypatch, tmp_path):
    """Без токена голоса не сопоставляются: шаг убирается, а доля не откатывается."""
    import meet.transcribe as tr
    from meet import events

    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [Segment(0.0, 1.0, "а")])
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw:
                        Diarization(turns=[], skipped=tr.SKIPPED_NO_TOKEN))
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    tr.transcribe(str(src), align=False, bus=bus)
    events_ = _progress(seen)
    assert "voices" not in {e["stage"] for e in events_}
    assert events_[0]["steps"] == 5 and events_[-1]["steps"] == 4
    fractions = [e["fraction"] for e in events_]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert any(e["note"] == "пропущено: нет токена Hugging Face" for e in events_)


def test_asr_and_diarization_report_progress_inside_their_steps(monkeypatch, tmp_path):
    """Распознавание и диаризация двигают шкалу внутри своего шага."""
    import meet.transcribe as tr
    from meet import events
    from meet.progress import Stages

    monkeypatch.setattr(Stages, "MIN_GAP_S", 0.0)
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)

    def fake_asr(p, h, on_progress=None, **kw):
        for part in (0.25, 0.5, 0.75):
            on_progress(part)
        return [Segment(0.0, 1.0, "а")]

    def fake_diarize(p, num_speakers=None, exclusive=False, on_progress=None, **kw):
        on_progress(0.5)
        return Diarization(turns=[])

    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "diarize_wav", fake_diarize)
    monkeypatch.setattr(tr, "split_by_speaker", lambda s, t, o=None: s)
    bus = events.EventBus()
    seen = []
    bus.subscribe(seen.append)
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    tr.transcribe(str(src), align=False, bus=bus)
    asr_done = [e["done"] for e in _progress(seen) if e["stage"] == "asr"]
    assert asr_done[:4] == [0.0, 0.25, 0.5, 0.75]
    assert [e["done"] for e in _progress(seen) if e["stage"] == "diarize"][:2] == [0.0, 0.5]


def test_progress_final_event_names_result(monkeypatch, tmp_path):
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=True)
    last = [e for e in seen if e.data.get("stage") == "render"][-1]
    assert last.data["note"].endswith("_transcript.md")


def test_single_file_progress_has_one_track(monkeypatch, tmp_path):
    _, seen = _progress_stages(monkeypatch, tmp_path, folder=False)
    assert [e["label"] for e in _progress(seen) if e["stage"] == "asr"][0] == "распознавание"


def test_transcribe_works_without_bus(monkeypatch, tmp_path):
    """Шина необязательна: CLI зовёт transcribe как раньше."""
    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True)
    assert stages  # заглушки те же, но вызов без bus проверяем отдельно
    import meet.transcribe as tr

    src = tmp_path / "b.wav"
    src.write_bytes(b"x")
    assert tr.transcribe(str(src), align=False).suffix == ".md"


# --- имя владельца микрофона ---------------------------------------------


def test_mic_track_uses_speaker_name_from_settings(monkeypatch, tmp_path):
    """«Вы» — дефолт, а не константа: кому-то удобнее собственное имя."""
    import json

    import meet.transcribe as tr
    from meet.asr import Segment as Seg

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "config.json").write_text(
        json.dumps({"recording": {"speaker_name": "Алексей"}}), encoding="utf-8"
    )
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [Seg(0.0, 1.0, "а")])
    monkeypatch.setattr(
        tr, "diarize_wav",
        lambda p, num_speakers=None, exclusive=False, **kw: Diarization(turns=[]),
    )
    monkeypatch.setattr(tr, "split_by_speaker", lambda s, t, o=None: s)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled: s)
    (tmp_path / "sys.opus").write_bytes(b"x")
    (tmp_path / "mic.opus").write_bytes(b"x")
    segments, _, _ = tr._transcribe_two_track(tmp_path, None, None, align=False)
    assert any(seg.speaker == "Алексей" for seg in segments)


def test_import_folder_uses_single_track_and_writes_into_folder(tmp_path, monkeypatch):
    import meet.transcribe as tr
    from meet import library

    folder = tmp_path / "2026-09-28_16-04_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"media")
    seen = {}

    def fake_single(src, speakers, hotwords, align, overlap, bus, run=None):
        seen["src"] = src
        return [Segment(0.0, 1.0, "привет", speaker="SPEAKER_00")], None, {}

    monkeypatch.setattr(tr, "_transcribe_single", fake_single)
    out = tr.transcribe(str(folder))
    assert seen["src"] == folder / "source.mp4"
    assert out == folder / "2026-09-28_transcript.md"
    assert library.read_transcript(folder)["segments"][0]["text"] == "привет"


def test_structured_transcript_uses_display_speaker_names(tmp_path):
    """transcript.json хранит «Спикер N», как Markdown и сайдкар: ключи
    переименования из окна — именно они, сырой SPEAKER_XX их не находит."""
    import json

    from meet.asr import Segment
    from meet.transcribe import _write_structured

    segs = [Segment(0, 1, "a", "SPEAKER_03"), Segment(1, 2, "b", "SPEAKER_00"),
            Segment(2, 3, "c", "SPEAKER_03"), Segment(3, 4, "d", "Демьян")]
    _write_structured(tmp_path, segs, "t", {})
    data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert [s["speaker"] for s in data["segments"]] == [
        "Спикер 1", "Спикер 2", "Спикер 1", "Демьян"]


def test_write_sidecar_stores_absolute_source(tmp_path, monkeypatch):
    """source сайдкара — абсолютный путь: образец голоса помнит, откуда взят,
    независимо от рабочей папки, из которой запускали `meet transcribe`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "recordings" / "x").mkdir(parents=True)
    out_md = tmp_path / "recordings" / "x" / "x_transcript.md"
    diar = Diarization(turns=[], embeddings={"SPEAKER_00": np.asarray([1.0])})
    _write_sidecar(out_md, Path("recordings/x"), "2026-07-01",
                   [Segment(0.0, 1.0, "а", "SPEAKER_00")], diar, {})
    source = read_sidecar(sidecar_path(out_md))["source"]
    assert Path(source).is_absolute()
    assert Path(source) == (tmp_path / "recordings" / "x").resolve()


# --- расшифровка без токена Hugging Face ------------------------------------


def test_diarize_wav_without_token_is_skipped(tmp_path):
    """Нет токена — диаризации нет, но и SystemExit нет: расшифровка идёт дальше."""
    from meet.diarize import NO_TOKEN_NOTE, diarize_wav

    diar = diarize_wav(tmp_path / "x.wav")
    assert diar.skipped == "skipped_no_token" and diar.turns == []
    NO_TOKEN_NOTE.encode("cp866")  # печатается в консоль: без тире и ёлочек


def _no_token_pipeline(monkeypatch):
    """Настоящий diarize_wav (токена нет — conftest), фейковое распознавание."""
    import meet.transcribe as tr

    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled: s)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: (
        [Segment(0.0, 1.0, "привет")] if "sys" in str(p) or "audio" in str(p)
        else [Segment(2.0, 3.0, "здравствуйте")]))
    return tr


def test_two_track_without_token_labels_tracks_and_flags_transcript(monkeypatch, tmp_path):
    from meet import library

    tr = _no_token_pipeline(monkeypatch)
    folder = tmp_path / "2026-10-01_10-00"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    out = tr.transcribe(str(folder), align=False)
    data = library.read_transcript(folder)
    assert data["diarization"] == "skipped_no_token"
    assert [(s["speaker"], s["text"]) for s in data["segments"]] == [
        ("Собеседник", "привет"), ("Вы", "здравствуйте")]
    assert "Собеседник" in out.read_text(encoding="utf-8")
    assert not list(folder.glob("*_speakers.json"))  # без диаризации нет и голосов
    assert library.describe(folder).to_raw()["diarization"] == "skipped_no_token"


def test_import_without_token_is_one_interlocutor(monkeypatch, tmp_path):
    from meet import library

    tr = _no_token_pipeline(monkeypatch)
    folder = tmp_path / "2026-10-01_10-00_import"
    folder.mkdir()
    (folder / "source.mp4").write_bytes(b"x")
    tr.transcribe(str(folder), align=False)
    data = library.read_transcript(folder)
    assert data["diarization"] == "skipped_no_token"
    assert {s["speaker"] for s in data["segments"]} == {"Собеседник"}


def test_no_token_skips_voice_matching(monkeypatch, tmp_path):
    import meet.voices as voices

    tr = _no_token_pipeline(monkeypatch)
    monkeypatch.setattr(voices, "load_voices",
                        lambda *a, **k: pytest.fail("матчинг голосов без диаризации"))
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    segments, diar, names = tr._transcribe_single(src, None, None, align=False)
    assert diar.skipped == "skipped_no_token" and names == {}
    assert [s.speaker for s in segments] == ["Собеседник"]


def test_with_token_transcript_has_no_skip_flag(monkeypatch, tmp_path):
    """С токеном поведение прежнее: поля-пометки нет."""
    from meet import library

    stages, _ = _progress_stages(monkeypatch, tmp_path, folder=True)
    data = library.read_transcript(tmp_path)
    assert "diarization" not in data
    assert library.describe(tmp_path).to_raw()["diarization"] is None


# --- токен есть, а доступа к модели нет ---------------------------------------


def _fake_pyannote(monkeypatch, from_pretrained):
    """Подменить pyannote.audio.Pipeline: настоящий не грузим (секунды и GPU)."""
    import sys
    import types

    fake = types.ModuleType("pyannote.audio")

    class Pipeline:
        @staticmethod
        def from_pretrained(checkpoint, token=None, **kw):
            return from_pretrained(checkpoint, token)

    fake.Pipeline = Pipeline
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake)


def test_no_access_pipeline_none_skips_diarization(monkeypatch, tmp_path, capsys):
    """pyannote 4.x на 401/403 от hub возвращает None (а не бросает): раньше
    `pipe.to` падал AttributeError уже после распознавания — задача терялась."""
    from meet import credentials, library

    credentials.set_hf_token("hf_REVOKED_secret")
    _fake_pyannote(monkeypatch, lambda checkpoint, token: None)
    tr = _no_token_pipeline(monkeypatch)
    folder = tmp_path / "2026-10-01_10-00"
    folder.mkdir()
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    tr.transcribe(str(folder), align=False)
    data = library.read_transcript(folder)
    assert data["diarization"] == "skipped_no_access"
    assert [s["speaker"] for s in data["segments"]] == ["Собеседник", "Вы"]
    assert library.describe(folder).to_raw()["diarization"] == "skipped_no_access"
    out = capsys.readouterr().out
    assert "Нет доступа к модели диаризации" in out
    assert "hf_REVOKED_secret" not in out


def test_no_access_hub_error_skips_diarization(monkeypatch, tmp_path, capsys):
    """Ошибка hub наружу (нет в кэше и не пускают) — тоже «нет доступа»."""
    # huggingface_hub приходит с extras движка; без него тест не о чем гонять.
    pytest.importorskip("huggingface_hub")
    from huggingface_hub.errors import LocalEntryNotFoundError

    from meet import credentials
    from meet.diarize import NO_ACCESS_NOTE, diarize_wav

    credentials.set_hf_token("hf_REVOKED_secret")

    def refuse(checkpoint, token):
        raise LocalEntryNotFoundError("нет доступа")

    _fake_pyannote(monkeypatch, refuse)
    assert diarize_wav(tmp_path / "x.wav").skipped == "skipped_no_access"
    out = capsys.readouterr().out
    assert "LocalEntryNotFoundError" in out and "hf_REVOKED_secret" not in out
    NO_ACCESS_NOTE.format(reason="x").encode("cp866")


def test_access_reason_names_class_and_status_only():
    from types import SimpleNamespace

    from meet.diarize import _access_reason

    class GatedRepoError(Exception):
        pass

    error = GatedRepoError("Authorization: Bearer hf_secret ...")
    error.response = SimpleNamespace(status_code=403)
    assert _access_reason(error) == "GatedRepoError, HTTP 403"


def test_merged_folder_gets_break_marks_in_markdown_and_json(monkeypatch, tmp_path):
    """Объединённая встреча: на стыках частей — отметки перерыва, и в
    Markdown, и в transcript.json (не реплика: без спикера, kind=break)."""
    import json

    import meet.transcribe as tr
    from meet import library

    folder = tmp_path / "2026-09-30_10-00_merged"
    folder.mkdir()
    for role in ("sys", "mic"):
        (folder / f"{role}.opus").write_bytes(b"x")
    library.write_meta(folder, {"source": "merge", "parts": [
        {"id": "a", "start_offset_s": 0.0, "gap_s": 0.0},
        {"id": "b", "start_offset_s": 10.0, "gap_s": 900.0},
    ]})

    def fake_two(path, speakers, hotwords, align, overlap, bus, run=None):
        return [Segment(1.0, 2.0, "до перерыва", "Вы"), Segment(12.0, 13.0, "после", "Вы")], None, {}

    monkeypatch.setattr(tr, "_transcribe_two_track", fake_two)
    out = tr.transcribe(str(folder))
    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert [(s["text"], s.get("kind")) for s in data["segments"]] == [
        ("до перерыва", None), ("— перерыв 15 мин —", "break"), ("после", None)]
    assert data["segments"][1]["speaker"] is None
    md = out.read_text(encoding="utf-8")
    assert "*— перерыв 15 мин —*" in md
    assert "Спикер ?" not in md


def test_replacement_rules_fix_both_tracks_before_speaker_split(monkeypatch, tmp_path):
    """Правила замены из настроек (asr.replacements) — сразу после
    распознавания: и дорожка собеседников (до раздачи спикерам), и микрофон."""
    import json

    import meet.transcribe as tr
    from meet.asr import Segment as Seg
    from meet.asr import Word

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "config.json").write_text(json.dumps({"asr": {"replacements": [
        {"from": "кубер нетис", "to": "kubernetes"}]}}), encoding="utf-8")
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [Seg(
        0.0, 1.0, "Кубер нетис готов.",
        words=[Word(0.0, 0.3, " Кубер"), Word(0.3, 0.6, " нетис"), Word(0.6, 1.0, " готов.")])])
    seen = {}

    def fake_split(segments, turns, overlaps=None):
        seen["text"] = [s.text for s in segments]
        seen["words"] = [w.text for w in segments[0].words]
        return segments

    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw: Diarization(turns=[]))
    monkeypatch.setattr(tr, "split_by_speaker", fake_split)
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled: s)
    (tmp_path / "sys.opus").write_bytes(b"x")
    (tmp_path / "mic.opus").write_bytes(b"x")
    segments, _, _ = tr._transcribe_two_track(tmp_path, None, None, align=False)
    assert seen == {"text": ["Kubernetes готов."], "words": [" Kubernetes", " готов."]}
    assert [s.text for s in segments] == ["Kubernetes готов.", "Kubernetes готов."]


def test_replacement_rules_failure_does_not_stop_transcription(monkeypatch, capsys):
    import meet.transcribe as tr
    from meet import textfix

    def boom(segments, rules):
        raise ValueError("сломалось")

    monkeypatch.setattr(textfix, "apply_rules", boom)
    monkeypatch.setattr(tr, "_replacement_rules", lambda: [{"from": "а", "to": "б"}])
    segs = [Segment(0.0, 1.0, "а")]
    assert tr._fix_terms(segs) is segs
    assert "правила замены" in capsys.readouterr().out
