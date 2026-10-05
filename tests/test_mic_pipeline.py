"""Микрофон по голосам в пайплайне расшифровки (meet.mic_split, шаг
`mic-voices`, 0.3.3).

Встреча из двух дорожек: после диаризации собеседников микрофон делится на
владельца и людей в комнате, дубли соседа и эхо убираются. В окончательную
расшифровку уходят подписи людей в комнате («Спикер N» общей нумерацией),
поле `mic_split`, записи сайдкара (`OWNER`, `SPEAKER_M<n>`) и
`mic_voices.json`. Распознавание и диаризация подменены; эмбеддер — подделка
из test_mic_split (тоны частот), моделей нет."""

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from meet import events, library, mic_split, owner_voice
from meet.asr import Segment, Word
from meet.diarize import SKIPPED_NO_TOKEN, Diarization


def _words(start: float, text: str) -> list[Word]:
    out, t = [], start
    for w in text.split():
        out.append(Word(t, t + 0.4, f" {w}"))
        t += 0.5
    return out


def _seg(start: float, text: str, **kw) -> Segment:
    words = _words(start, text)
    return Segment(start, words[-1].end, text, words=words, **kw)


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """Запись звонка с заглушками: sys — две реплики двух собеседников, mic —
    две реплики. `mic_split.run` подменяется в каждом тесте (`calls`)."""
    import meet.transcribe as tr

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    folder = tmp_path / "recordings" / "2026-10-05_10-00"
    folder.mkdir(parents=True)
    for role in ("sys", "mic"):
        (folder / f"{role}.opus").write_bytes(b"x")
    seen: dict = {"calls": [], "events": []}

    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)

    def fake_asr(path, hotwords, **kw):
        if "mic" in Path(path).name:
            return [_seg(3.0, "да согласен"), _seg(20.0, "а я бы подождал до пятницы")]
        return [_seg(0.0, "добрый день коллеги"), _seg(6.0, "тогда начнём")]

    seen["diar"] = Diarization(turns=[(0.0, 2.0, "SPEAKER_00"), (5.0, 8.0, "SPEAKER_01")],
                               embeddings={"SPEAKER_00": np.ones(4, dtype=np.float32),
                                           "SPEAKER_01": -np.ones(4, dtype=np.float32)})
    monkeypatch.setattr(tr, "transcribe_wav", fake_asr)
    monkeypatch.setattr(tr, "diarize_wav", lambda path, num_speakers=None, exclusive=False, **kw: seen["diar"])
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    bus = events.EventBus()
    bus.subscribe(seen["events"].append)
    seen.update(folder=folder, bus=bus, tr=tr)
    return seen


def _room_split(seen, *, room_label="SPEAKER_M0", drop_sys_text=None, status="ok"):
    """Подмена mic_split.run: вторая реплика микрофона — человек в комнате,
    реплика sys с текстом `drop_sys_text` — утечка владельца."""
    def fake_run(mic_segs, sys_segs, mic_wav, sys_wav, diar, **kw):
        seen["calls"].append({"mic": [s.text for s in mic_segs], "sys": [(s.text, s.speaker) for s in sys_segs],
                              "mic_wav": Path(mic_wav).name, "sys_wav": Path(sys_wav).name, "diar": diar, **kw})
        mic = [Segment(s.start, s.end, s.text, kw["owner_label"] if i == 0 else room_label, words=s.words,
                       track="mic") for i, s in enumerate(mic_segs)]
        sys = [s for s in sys_segs if s.text != drop_sys_text]
        dropped = ([{"track": "sys", "start": 6.0, "end": 6.9, "text": drop_sys_text, "words": 2,
                     "reason": "owner_leak"}] if drop_sys_text else [])
        sidecar = [{"label": "OWNER", "display": kw["owner_label"], "embedding": [0.5, 0.5, 0.5, 0.5],
                    "track": "mic", "owner": True},
                   {"label": "SPEAKER_M0", "display": room_label, "embedding": [1.0, 0.0, 0.0, 0.0],
                    "track": "mic"}]
        report = {"rule": 1, "status": status, "owner_profile": "enroll", "room_speakers": 1,
                  "dropped": {"echo": 0, "neighbour": 0, "owner_leak": len(dropped)}}
        voices = {"version": 1, "rule": 1, "model": "m", "status": status, "fast": False, "lag_s": None,
                  "clusters": [], "dropped": dropped}
        return mic_split.MicResult(mic=mic, sys=sys, dropped=dropped, sidecar=sidecar, report=report,
                                   voices=voices)
    return fake_run


def _sidecar(folder: Path) -> dict:
    (path,) = folder.glob("*_speakers.json")
    return json.loads(path.read_text(encoding="utf-8"))


# --- план шагов -------------------------------------------------------------------


def test_mic_voices_is_a_step_after_voices_and_before_render():
    import meet.transcribe as tr

    keys = [s.key for s in tr._two_track_plan(False)]
    assert keys == ["convert", "asr-sys", "asr-mic", "diarize", "voices", "mic-voices", "render"]
    step = next(s for s in tr._two_track_plan(True) if s.key == "mic-voices")
    assert step.stage == "voices" and step.label == "голоса микрофона" and step.note == "mic"
    assert step.weight > 0 and not step.measured
    # Одна дорожка (импорт) — без шага микрофона.
    assert "mic-voices" not in [s.key for s in tr._single_plan(True)]


def test_mic_voices_progress_is_reported_between_voices_and_render(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    order = list(dict.fromkeys((e.data["stage"], e.data.get("label")) for e in pipeline["events"]
                               if e.kind == "progress"))
    labels = [label for _, label in order]
    assert labels.index("голоса микрофона") > labels.index(next(l for s, l in order if s == "voices"))
    assert labels.index("голоса микрофона") < labels.index(next(l for s, l in order if s == "render"))


# --- встраивание ------------------------------------------------------------------


def test_mic_split_gets_the_meeting_and_its_settings(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    (folder / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"kind": "record.started", "tracks": [{"track": "sys.opus", "device": "Динамики"},
                                              {"track": "mic.opus", "device": "Микрофон (USB)"}]},
        {"kind": "record.level", "levels": {}},
    ]), encoding="utf-8")
    owner = [owner_voice.OwnerSample(id="o1", embedding=np.ones(4, dtype=np.float32), source="enroll",
                                     date="2026-10-05", seconds=25.0)]
    monkeypatch.setattr(owner_voice, "load", lambda voices=None: owner)
    import meet.voices as voices

    monkeypatch.setattr(voices, "load_voices", lambda folder=None: {"Анна": [np.ones(4, dtype=np.float32)]})
    monkeypatch.setattr(voices, "match_speakers", lambda emb, base, threshold=None: {"SPEAKER_01": "Анна"})
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    (call,) = pipeline["calls"]
    # Настоящие сегменты (не копии черновика), wav пайплайна, имена кластеров sys.
    assert call["mic"] == ["да согласен", "а я бы подождал до пятницы"]
    assert call["sys"] == [("добрый день коллеги", "SPEAKER_00"), ("тогда начнём", "Анна")]
    assert (call["mic_wav"], call["sys_wav"]) == ("mic16.wav", "sys16.wav")
    assert call["diar"] is pipeline["diar"] and call["names"] == {"SPEAKER_01": "Анна"}
    assert call["owner"] == owner and set(call["base"]) == {"Анна"}
    assert call["owner_label"] == "Вы" and call["device"] == "Микрофон (USB)"
    assert call["speakers"] is True and call["dedupe"] is True and call["no_token"] is False
    # Слова sys не выравнивались (align=False): так и записано для калибровки лагов.
    assert call["aligned"] is False
    assert call["threshold"] == pytest.approx(tr.voice_threshold(folder))


def test_settings_switch_the_split_and_the_dedupe_off(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    state = folder.parents[1] / "state"
    state.mkdir(exist_ok=True)
    (state / "config.json").write_text(json.dumps({"asr": {"mic_speakers": False, "mic_dedupe": False}}),
                                       encoding="utf-8")
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    (call,) = pipeline["calls"]
    assert call["speakers"] is False and call["dedupe"] is False


def test_device_is_unknown_when_the_microphone_changed(tmp_path):
    import meet.transcribe as tr

    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"kind": "record.started", "tracks": [{"track": "mic.opus", "device": "Встроенный"}]},
        {"kind": "record.device", "track": "mic.opus", "device": "Гарнитура"},
    ]) + "\nне json\n", encoding="utf-8")
    assert tr._mic_device(tmp_path) is None
    (tmp_path / "events.jsonl").write_text(json.dumps(
        {"kind": "record.device", "track": "mic.opus", "device": "Гарнитура"}), encoding="utf-8")
    assert tr._mic_device(tmp_path) == "Гарнитура"
    assert tr._mic_device(tmp_path / "нет") is None


def test_final_transcript_has_room_speakers_sidecar_and_mic_voices(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline, drop_sys_text="тогда начнём"))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    final = library.read_transcript(folder)
    rows = [(s["text"], s["speaker"], s.get("track")) for s in final["segments"]]
    # Человек в комнате — «Спикер N» общей с собеседниками нумерацией; утечка
    # владельца из sys убрана.
    assert rows == [("добрый день коллеги", "Спикер 1", None), ("да согласен", "Вы", "mic"),
                    ("а я бы подождал до пятницы", "Спикер 2", "mic")]
    assert final["mic_split"] == {"rule": 1, "status": "ok", "owner_profile": "enroll", "room_speakers": 1,
                                  "dropped": {"echo": 0, "neighbour": 0, "owner_leak": 1}}
    # Текст до спикеров был — окончательная расшифровка та же (created_at).
    assert final["created_at"]
    side = {e["label"]: e for e in _sidecar(folder)["speakers"]}
    assert set(side) == {"SPEAKER_00", "SPEAKER_01", "OWNER", "SPEAKER_M0"}
    assert side["OWNER"] == {"label": "OWNER", "display": "Вы", "embedding": [0.5, 0.5, 0.5, 0.5],
                             "track": "mic", "owner": True}
    assert side["SPEAKER_M0"]["display"] == "Спикер 2" and side["SPEAKER_M0"]["track"] == "mic"
    assert "track" not in side["SPEAKER_00"]
    voices_file = json.loads((folder / library.MIC_VOICES).read_text(encoding="utf-8"))
    assert voices_file["dropped"][0]["text"] == "тогда начнём"
    md = next(folder.glob("*_transcript.md")).read_text(encoding="utf-8")
    assert "Спикер 2" in md and "тогда начнём" not in md


def test_final_keeps_the_created_at_of_the_text_phase(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    seen = {}
    real = library.write_transcript

    def spy(path, data, words="keep"):
        if data.get("phase") == "text":
            seen["created_at"] = data["created_at"]
        return real(path, data, words=words)

    monkeypatch.setattr(library, "write_transcript", spy)
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert library.read_transcript(folder)["created_at"] == seen["created_at"]


def test_room_speaker_linked_to_a_sys_cluster_shares_its_label(pipeline, monkeypatch):
    """Человек в комнате с голосом кластера sys (сосед в том же звонке)
    подписан, как этот кластер: одна и та же «Спикер N» на обеих дорожках."""
    tr, folder = pipeline["tr"], pipeline["folder"]
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline, room_label="SPEAKER_01"))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    final = library.read_transcript(folder)
    assert [s["speaker"] for s in final["segments"]] == ["Спикер 1", "Вы", "Спикер 2", "Спикер 2"]
    side = {e["label"]: e for e in _sidecar(folder)["speakers"]}
    assert side["SPEAKER_M0"]["display"] == side["SPEAKER_01"]["display"] == "Спикер 2"


def test_mic_split_failure_leaves_the_microphone_to_the_owner(pipeline, monkeypatch, capsys):
    tr, folder = pipeline["tr"], pipeline["folder"]

    def boom(*a, **kw):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(mic_split, "run", boom)
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    final = library.read_transcript(folder)
    assert [s["speaker"] for s in final["segments"]] == ["Спикер 1", "Вы", "Спикер 2", "Вы"]
    assert final["mic_split"]["status"] == "skipped_error"
    assert final["mic_split"]["dropped"] == {"echo": 0, "neighbour": 0, "owner_leak": 0}
    assert not (folder / library.MIC_VOICES).exists()
    assert "микрофон: голоса не разобраны" in capsys.readouterr().out


def test_without_diarization_the_split_knows_there_is_no_token(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    pipeline["diar"] = Diarization(turns=[], skipped=SKIPPED_NO_TOKEN)
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline, status="skipped_no_token"))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    (call,) = pipeline["calls"]
    assert call["no_token"] is True and call["names"] == {}
    final = library.read_transcript(folder)
    assert final["diarization"] == SKIPPED_NO_TOKEN and final["mic_split"]["status"] == "skipped_no_token"
    assert {s["speaker"] for s in final["segments"] if not s.get("track")} == {"Собеседник"}


def test_stale_mic_voices_go_away_with_a_retranscription(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    (folder / library.MIC_VOICES).write_text('{"dropped": [{"text": "старое"}]}', encoding="utf-8")

    def boom(*a, **kw):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(mic_split, "run", boom)
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert not (folder / library.MIC_VOICES).exists()


def test_single_track_import_has_no_mic_split(monkeypatch, tmp_path):
    import meet.transcribe as tr

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    folder = tmp_path / "recordings" / "2026-10-05_11-00"
    folder.mkdir(parents=True)
    (folder / "source.opus").write_bytes(b"x")
    monkeypatch.setattr(tr, "to_wav16k", lambda src, dst, **k: dst)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [_seg(0.0, "добрый день")])
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw:
                        Diarization(turns=[(0.0, 2.0, "SPEAKER_00")]))
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    monkeypatch.setattr(mic_split, "run", lambda *a, **kw: pytest.fail("у импорта нет микрофона"))
    tr.transcribe(str(folder), align=False)
    final = library.read_transcript(folder)
    assert "mic_split" not in final and not (folder / library.MIC_VOICES).exists()


def test_merged_meeting_splits_the_whole_microphone_with_breaks(pipeline, monkeypatch):
    """Объединённая встреча: микрофон делится целиком (лаг — на всю встречу),
    отметки перерыва встают после, как и раньше."""
    tr, folder = pipeline["tr"], pipeline["folder"]
    library.update_meta(folder, lambda m: {**m, "parts": [
        {"start_offset_s": 0.0}, {"start_offset_s": 15.0, "gap_s": 600}]})
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline))
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    (call,) = pipeline["calls"]
    assert call["mic"] == ["да согласен", "а я бы подождал до пятницы"]
    final = library.read_transcript(folder)
    kinds = [(s.get("kind"), s["speaker"]) for s in final["segments"]]
    assert kinds == [(None, "Спикер 1"), (None, "Вы"), (None, "Спикер 2"), ("break", None), (None, "Спикер 3")]


# --- настоящий mic_split с поддельным эмбеддером -------------------------------------


def _write_wav(path: Path, audio: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def test_end_to_end_owner_and_a_person_in_the_room(monkeypatch, tmp_path):
    """Настоящий mic_split по синтетическому звуку: владелец и человек рядом с
    ним. Комнатный голос — отдельный «Спикер N», владелец — «Вы»; сайдкар и
    mic_voices.json — по-настоящему."""
    import test_mic_split as ms

    import meet.transcribe as tr
    from meet import segvoices

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    folder = tmp_path / "recordings" / "2026-10-05_12-00"
    folder.mkdir(parents=True)
    for role in ("sys", "mic"):
        (folder / f"{role}.opus").write_bytes(b"x")
    mic_segs, _, mic_wav, _ = ms._meeting(tmp_path, room1=True)
    audio = {"mic16.wav": mic_wav, "sys16.wav": _write_wav(tmp_path / "silence.wav", np.zeros(16000 * 90))}

    def to_wav(src, dst, **k):
        Path(dst).write_bytes(Path(audio[Path(dst).name]).read_bytes())
        return dst

    sys_segs = [_seg(80.0, "всем спасибо до встречи")]
    monkeypatch.setattr(tr, "to_wav16k", to_wav)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [
        Segment(s.start, s.end, s.text, words=list(s.words)) for s in (mic_segs if "mic" in Path(p).name
                                                                      else sys_segs)])
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw:
                        Diarization(turns=[(79.0, 85.0, "SPEAKER_00")],
                                    embeddings={"SPEAKER_00": ms._e(5).astype(np.float32)}))
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    monkeypatch.setattr(segvoices, "load_embedder", lambda: ms.fake_embed)
    monkeypatch.setattr(owner_voice, "load", lambda voices=None: ms._owner())
    tr.transcribe(str(folder), align=False)
    final = library.read_transcript(folder)
    assert final["mic_split"]["status"] == "ok" and final["mic_split"]["room_speakers"] == 1
    by = {}
    for s in final["segments"]:
        by.setdefault(s["speaker"], []).append(s["start"])
    assert set(by) == {"Вы", "Спикер 1", "Спикер 2"}
    # Собеседник говорит последним, человек в комнате — раньше: он «Спикер 1».
    assert min(by["Спикер 1"]) >= 40.0 and max(by["Спикер 1"]) < 60.0 and by["Спикер 2"] == [80.0]
    assert max(by["Вы"]) < 40.0
    side = {e["label"]: e for e in _sidecar(folder)["speakers"]}
    assert side["OWNER"]["owner"] is True and side["SPEAKER_M0"]["display"] == "Спикер 1"
    voices_file = json.loads((folder / library.MIC_VOICES).read_text(encoding="utf-8"))
    assert {c["role"] for c in voices_file["clusters"]} >= {"owner", "room"}


# --- сайдкар микрофона: панель «Спикеры» и «Переразделить» ---------------------------

ROOM_SEGMENTS = [
    {"start": 0.0, "end": 4.0, "speaker": "Спикер 1", "text": "Склад готов к отгрузке.", "uncertain": False},
    {"start": 4.2, "end": 5.0, "speaker": "Вы", "text": "Отлично.", "uncertain": False, "track": "mic"},
    {"start": 5.2, "end": 5.9, "speaker": "Спикер 3", "text": "А машина есть?", "uncertain": False,
     "track": "mic"},
    {"start": 6.0, "end": 9.0, "speaker": "Спикер 1", "text": "Счёт оплатим до пятницы.", "uncertain": False},
    {"start": 10.0, "end": 12.0, "speaker": "Спикер 2", "text": "Договор продлим.", "uncertain": False},
    {"start": 12.5, "end": 13.5, "speaker": "Вы", "text": "Наверное, да.", "uncertain": True, "track": "mic"},
]
OWNER_EMB = [0.0, 0.0, 1.0, 0.0]


@pytest.fixture
def call(tmp_path):
    """Окончательная расшифровка звонка с человеком в комнате («Спикер 3») и
    сайдкаром микрофона (OWNER, SPEAKER_M0)."""
    folder = tmp_path / "recordings" / "2026-10-05_14-00"
    folder.mkdir(parents=True)
    (folder / "sys.opus").write_bytes(b"x")
    (folder / "mic.opus").write_bytes(b"x")
    library.write_transcript(folder, {
        "version": 1, "created_at": "2026-10-05T15:00:00", "track_marks": "pipeline",
        "segments": json.loads(json.dumps(ROOM_SEGMENTS)),
        "mic_split": {"rule": 1, "status": "ok", "owner_profile": "enroll", "room_speakers": 1,
                      "dropped": {"echo": 1, "neighbour": 2, "owner_leak": 0}}})
    (folder / "2026-10-05_speakers.json").write_text(json.dumps({
        "model": "m", "source": str(folder), "date": "2026-10-05", "speakers": [
            {"label": "SPEAKER_00", "display": "Спикер 1", "embedding": [1.0, 0.0, 0.0, 0.0]},
            {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [0.0, 1.0, 0.0, 0.0]},
            {"label": "OWNER", "display": "Вы", "embedding": OWNER_EMB, "track": "mic", "owner": True},
            {"label": "SPEAKER_M0", "display": "Спикер 3", "embedding": [0.0, 0.0, 0.0, 1.0],
             "track": "mic"}]}, ensure_ascii=False), encoding="utf-8")
    (folder / library.MIC_VOICES).write_text(json.dumps({
        "version": 1, "rule": 1, "dropped": [
            {"track": "mic", "start": 30.0, "end": 31.0, "text": "всем привет", "words": 2, "reason": "neighbour",
             "coverage": 1.0, "lag": 0.2},
            {"track": "mic", "start": 20.0, "end": 21.5, "text": "да слышно", "words": 2, "reason": "echo"},
            {"track": "mic", "start": 40.0, "end": 41.0, "text": "до завтра", "words": 2, "reason": "neighbour"},
            {"track": "sys", "start": 50.0, "end": 51.0, "text": None, "reason": "что-то новое"},
            "мусор"]}, ensure_ascii=False), encoding="utf-8")
    voices = tmp_path / "voices"
    voices.mkdir()
    # Голос в базе, похожий на владельца: подсказкой к «Вы» он не становится.
    (voices / "Кузьма.json").write_text(json.dumps({"samples": [
        {"embedding": OWNER_EMB, "source": "C:/rec/old", "date": "2026-09-01"}]}, ensure_ascii=False),
        encoding="utf-8")
    return folder, voices


def _side_entries(folder: Path) -> list[tuple]:
    side = json.loads(next(folder.glob("*_speakers.json")).read_text(encoding="utf-8"))
    return [(e["label"], e["display"], e.get("track")) for e in side["speakers"]]


def test_owner_voice_is_never_suggested_as_someone_from_the_base(call):
    from meet import speakers

    folder, voices = call
    rows = {r["label"]: r for r in speakers.overview(folder, voices)["speakers"]}
    assert rows["Вы"]["has_voice"] is True and rows["Вы"]["suggestions"] == []
    # Чужой голос с тем же вектором подсказку получает (правило не по вектору).
    assert speakers._suggestions([{"embedding": OWNER_EMB}], {"Кузьма": [np.array(OWNER_EMB)]})


def test_overview_tells_the_track_of_each_speaker(call):
    from meet import speakers

    folder, voices = call
    data = library.read_transcript(folder)
    data["segments"].append({"start": 14.0, "end": 15.0, "speaker": "Спикер 2", "text": "И я тут.",
                             "uncertain": False, "track": "mic"})
    library.write_transcript(folder, data)
    rows = {r["label"]: r["track"] for r in speakers.overview(folder, voices)["speakers"]}
    assert rows == {"Спикер 1": "sys", "Вы": "mic", "Спикер 3": "mic", "Спикер 2": "mixed"}


def test_overview_of_an_import_has_no_tracks(tmp_path):
    from meet import speakers

    folder = tmp_path / "2026-10-05_16-00"
    folder.mkdir()
    (folder / "source.opus").write_bytes(b"x")
    library.write_transcript(folder, {"version": 1, "segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Спикер 1", "text": "а", "uncertain": False}]})
    view = speakers.overview(folder, tmp_path / "voices")
    assert view["speakers"][0]["track"] is None
    assert view["mic_split"] is None and view["mic_removed"] == []


def test_overview_lists_what_was_removed_from_the_microphone(call):
    from meet import speakers

    folder, voices = call
    view = speakers.overview(folder, voices)
    assert view["mic_split"]["status"] == "ok"
    assert view["mic_removed"] == [
        {"start": 20.0, "end": 21.5, "text": "да слышно", "reason": "echo", "track": "mic"},
        {"start": 30.0, "end": 31.0, "text": "всем привет", "reason": "neighbour", "track": "mic"},
        {"start": 40.0, "end": 41.0, "text": "до завтра", "reason": "neighbour", "track": "mic"},
    ]
    (folder / library.MIC_VOICES).write_text("не json", encoding="utf-8")
    assert speakers.overview(folder, voices)["mic_removed"] == []


def _rediarize(folder, voices, monkeypatch, turns, emb):
    from meet import rediarize
    from meet import voices as voice_base

    # Базу для узнавания — пустую: «Кузьма» из базы похож на владельца и
    # честно назвал бы новый кластер — здесь проверяется наследование подписей.
    empty = folder.parent / "пустая база"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(voice_base, "voices_dir", lambda: empty)

    def to_wav(src, dst, normalize=False):
        Path(dst).write_bytes(b"wav")
        return Path(dst)

    rediarize.run(folder, diarize=lambda wav, **kw: Diarization(turns=turns, embeddings=emb, overlaps=[]),
                  to_wav=to_wav)
    return rediarize


def test_rediarize_leaves_the_microphone_and_its_voices_alone(call, monkeypatch):
    """«Переразделить»: микрофон не трогается (ни реплики, ни голоса владельца и
    людей в комнате в сайдкаре); новый собеседник не наследует голос владельца
    и получает «Спикер N», не занятый человеком в комнате."""
    from meet import speakers

    folder, voices = call
    turns = [(0.0, 4.0, "SPEAKER_00"), (6.0, 9.0, "SPEAKER_02"), (9.5, 13.0, "SPEAKER_01")]
    emb = {"SPEAKER_00": np.array([1.0, 0.0, 0.0, 0.0]), "SPEAKER_01": np.array([0.0, 1.0, 0.0, 0.0]),
           # Голос нового кластера — как у владельца в микрофоне.
           "SPEAKER_02": np.array(OWNER_EMB)}
    rediarize = _rediarize(folder, voices, monkeypatch, turns, emb)
    rediarize.apply(folder, voices)
    segs = library.read_transcript_full(folder)["segments"]
    assert [s for s in segs if s.get("track") == "mic"] == [s for s in ROOM_SEGMENTS if s.get("track") == "mic"]
    # Кому из двух новых кластеров «Спикер 1» достанется по времени — ничья;
    # второй — свободный номер, не «Вы» по голосу и не «Спикер 3» из комнаты.
    sys_labels = [s["speaker"] for s in segs if not s.get("track")]
    assert sys_labels[2] == "Спикер 2" and sorted(sys_labels[:2]) == ["Спикер 1", "Спикер 4"]
    entries = _side_entries(folder)
    assert ("OWNER", "Вы", "mic") in entries and ("SPEAKER_M0", "Спикер 3", "mic") in entries
    assert {d for _, d, t in entries if t is None} == {"Спикер 1", "Спикер 2", "Спикер 4"}
    speakers.undo(folder, voices)
    assert library.read_transcript_full(folder)["segments"] == ROOM_SEGMENTS
    assert len(_side_entries(folder)) == 4


def test_rediarize_with_the_same_split_changes_nothing(call, monkeypatch):
    from meet import speakers

    folder, voices = call
    turns = [(0.0, 9.0, "SPEAKER_00"), (9.5, 13.0, "SPEAKER_01")]
    emb = {"SPEAKER_00": np.array([1.0, 0.0, 0.0, 0.0]), "SPEAKER_01": np.array([0.0, 1.0, 0.0, 0.0])}
    rediarize = _rediarize(folder, voices, monkeypatch, turns, emb)
    with pytest.raises(speakers.SpeakerError, match="совпадает"):
        rediarize.apply(folder, voices)


def test_new_unnamed_speaker_skips_the_room_speaker_number(call):
    """«Новый спикер без имени» для реплики собеседника — не «Спикер 3»:
    это подпись человека в комнате (и в сайдкаре, и в репликах)."""
    from meet import speakers

    folder, voices = call
    speakers.relabel(folder, [4], None, voices)
    assert library.read_transcript(folder)["segments"][4]["speaker"] == "Спикер 4"


# --- пометка «голос под вопросом» -------------------------------------------------


def test_unsure_microphone_voice_is_not_called_an_overlap():
    """`uncertain` у микрофона — голос между «точно вы» и «точно не вы»
    (роль unsure), а не нахлёст спикеров: своя пометка, блоки не склеиваются."""
    from meet.output import to_markdown

    segs = [Segment(0.0, 1.0, "Добрый день.", "SPEAKER_00", uncertain=True),
            Segment(1.5, 2.0, "Да.", "Вы", track="mic"),
            Segment(2.5, 3.0, "Наверное.", "Вы", uncertain=True, track="mic")]
    md = to_markdown("Встреча", segs)
    assert "## 00:00 — Спикер 1 (нахлёст)" in md
    assert "## 00:01 — Вы\n" in md and "## 00:02 — Вы (голос под вопросом)" in md


def test_export_marks_the_unsure_voice_too():
    from meet import export

    data = {"segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Анна", "text": "Привет.", "uncertain": True},
        {"start": 2.0, "end": 3.0, "speaker": "Вы", "text": "Наверное.", "uncertain": True, "track": "mic"}]}
    txt = export.render(data, "txt")
    assert "Анна (нахлёст): Привет." in txt and "Вы (голос под вопросом): Наверное." in txt
    md = export.render(data, "md")
    assert "Вы (голос под вопросом)" in md


# --- окно: «в комнате», подсказки карточки, образец голоса человека -------------------


def test_window_marks_room_speakers_on_the_microphone(call, monkeypatch):
    """Реплики микрофона не владельца — «в комнате» (флаг `room` для окна);
    владелец под прежним именем из настроек — нет."""
    from meet import segvoices, tray_control

    folder, _ = call
    monkeypatch.setattr(segvoices, "owners", lambda: {"Вы", "Кузьма"})
    data = library.read_transcript(folder)
    data["segments"].append({"start": 14.0, "end": 15.0, "speaker": "Кузьма", "text": "Я тоже.",
                             "uncertain": False, "track": "mic"})
    shown = tray_control._window_transcript(data)["segments"]
    assert [s.get("room", False) for s in shown] == [False, False, True, False, False, False, False]
    # Без пометок пайплайна (старый звонок) «в комнате» не бывает.
    old = {"segments": [{"start": 0.0, "end": 1.0, "speaker": "Анна", "text": "а", "uncertain": False}]}
    assert "room" not in tray_control._window_transcript(old)["segments"][0]


def test_card_knows_the_mic_split_status(call):
    folder, _ = call
    card = library.describe(folder).to_raw()
    assert card["mic_split"] == {"status": "ok", "room_speakers": 1,
                                 "dropped": {"echo": 1, "neighbour": 2, "owner_leak": 0}}
    data = library.read_transcript(folder)
    data["mic_split"] = {"status": "no_profile", "dropped": "мусор"}
    library.write_transcript(folder, data)
    assert library.describe(folder).to_raw()["mic_split"] == {"status": "no_profile", "room_speakers": 0,
                                                             "dropped": {}}
    data.pop("mic_split")
    library.write_transcript(folder, data)
    assert library.describe(folder).to_raw()["mic_split"] is None


def test_person_sample_plays_the_track_the_person_spoke_on(call, tmp_path):
    """Человек в комнате говорил в микрофон: его образец — с дорожки mic, а
    не с дорожки собеседников, где его не слышно."""
    from meet import people

    folder, voices = call
    data = library.read_transcript(folder)
    for s in data["segments"]:
        if s["speaker"] == "Спикер 3":
            s["speaker"] = "Пётр"
        elif s["speaker"] == "Спикер 2":
            s["speaker"] = "Анна"
    library.write_transcript(folder, data)
    for name in ("Пётр", "Анна"):
        (voices / f"{name}.json").write_text('{"samples": []}', encoding="utf-8")
    rec = folder.parent
    assert people.sample("Пётр", voices, rec) == {"recording": folder.name, "start": 5.2, "end": 5.9,
                                                  "track": "mic"}
    assert people.sample("Анна", voices, rec)["track"] == "sys"


# --- fix round 1: порядок записи, оценка времени, охрана поведения без образца ------------


def test_mic_voices_is_written_after_the_transcript_and_not_without_it(pipeline, monkeypatch):
    tr, folder = pipeline["tr"], pipeline["folder"]
    monkeypatch.setattr(mic_split, "run", _room_split(pipeline, drop_sys_text="тогда начнём"))
    order = []
    real = library.write_transcript

    def spy(path, data, words="keep"):
        if data.get("phase") != "text":
            order.append(("transcript", (folder / library.MIC_VOICES).exists()))
        return real(path, data, words=words)

    monkeypatch.setattr(library, "write_transcript", spy)
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert order == [("transcript", False)] and (folder / library.MIC_VOICES).exists()

    (folder / library.MIC_VOICES).unlink()

    def broken(path, data, words="keep"):
        if data.get("phase") != "text":
            raise OSError("диск занят")
        return real(path, data, words=words)

    monkeypatch.setattr(library, "write_transcript", broken)
    pipeline["calls"].clear()
    tr.transcribe(str(folder), align=False, bus=pipeline["bus"])
    assert not (folder / library.MIC_VOICES).exists()


def test_mic_voices_survives_a_busy_file_on_windows(tmp_path, monkeypatch):
    import os

    import meet.transcribe as tr

    real, calls = os.replace, []

    def busy_once(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            raise PermissionError("файл читает окно")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", busy_once)
    result = mic_split.MicResult(mic=[], sys=[], dropped=[], sidecar=[], report={}, voices={"version": 1})
    tr._write_mic_voices(tmp_path, result)
    assert len(calls) == 2 and json.loads((tmp_path / library.MIC_VOICES).read_text(encoding="utf-8")) == {
        "version": 1}


def test_mic_voices_estimate_knows_when_voices_are_not_computed(monkeypatch, tmp_path):
    """Без образца владельца (или с выключенным разделением) шаг — только
    дубли, секунды: оценка «осталось» не ждёт минуты эмбеддинга."""
    import meet.transcribe as tr

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "state"))
    voices = tmp_path / "voices"
    monkeypatch.setattr(owner_voice, "path", lambda v=None: voices / "_owner" / "owner.json")
    load, work = tr._mic_voices_time("cpu", 3600.0)
    assert load + work < 5.0
    (voices / "_owner").mkdir(parents=True)
    (voices / "_owner" / "owner.json").write_text("{}", encoding="utf-8")
    load, work = tr._mic_voices_time("cpu", 3600.0)
    assert 40.0 < load + work < 90.0


def test_step_times_plan_the_mic_voices_step(monkeypatch, tmp_path):
    import meet.transcribe as tr
    from meet import asr

    run = tr._Run()
    run.choice = asr.Choice("gigaam", "cpu", "v3_e2e_rnnt")
    run._seconds = {"sys": 600.0, "mic": 600.0}
    times = run._step_times()
    assert "mic-voices" in times and times["mic-voices"][0] > 0


def test_sidecar_is_written_for_mic_voices_without_call_embeddings(tmp_path):
    import meet.transcribe as tr

    out_md = tmp_path / "2026-10-05_transcript.md"
    segs = [Segment(0.0, 1.0, "а", "Вы", track="mic"), Segment(2.0, 3.0, "б", "SPEAKER_M0", track="mic")]
    mic = [{"label": "OWNER", "display": "Вы", "embedding": [1.0, 0.0], "track": "mic", "owner": True},
           {"label": "SPEAKER_M0", "display": "SPEAKER_M0", "embedding": [0.0, 1.0], "track": "mic"}]
    tr._write_sidecar(out_md, tmp_path, "2026-10-05", segs, Diarization(turns=[(0.0, 1.0, "SPEAKER_00")]), {},
                      mic=mic)
    side = _sidecar(tmp_path)
    assert [(e["label"], e["display"]) for e in side["speakers"]] == [("OWNER", "Вы"), ("SPEAKER_M0", "Спикер 1")]


def test_without_owner_sample_the_final_transcript_is_as_with_the_split_off(monkeypatch, tmp_path):
    """Главное свойство: без образца владельца и без дублей, при включённых
    настройках, расшифровка и сайдкар — те же, что с выключенным разделением
    (кроме поля mic_split). Настоящий mic_split, поддельный эмбеддер."""
    import test_mic_split as ms

    import meet.transcribe as tr
    from meet import segvoices

    mic_segs, _, mic_wav, _ = ms._meeting(tmp_path, room1=True)
    audio = {"mic16.wav": mic_wav, "sys16.wav": _write_wav(tmp_path / "silence.wav", np.zeros(16000 * 90))}

    def to_wav(src, dst, **k):
        Path(dst).write_bytes(Path(audio[Path(dst).name]).read_bytes())
        return dst

    sys_segs = [_seg(80.0, "всем спасибо до встречи")]
    monkeypatch.setattr(tr, "to_wav16k", to_wav)
    monkeypatch.setattr(tr, "transcribe_wav", lambda p, h, **kw: [
        Segment(s.start, s.end, s.text, words=list(s.words)) for s in (mic_segs if "mic" in Path(p).name
                                                                      else sys_segs)])
    monkeypatch.setattr(tr, "diarize_wav", lambda p, num_speakers=None, exclusive=False, **kw:
                        Diarization(turns=[(79.0, 85.0, "SPEAKER_00")],
                                    embeddings={"SPEAKER_00": ms._e(5).astype(np.float32)}))
    monkeypatch.setattr(tr, "_maybe_align", lambda s, w, enabled, **kw: s)
    monkeypatch.setattr(segvoices, "load_embedder", lambda: pytest.fail("без образца эмбеддер не нужен"))
    monkeypatch.setattr(owner_voice, "load", lambda voices=None: [])
    out = {}
    for flags in (True, False):
        state = tmp_path / f"state-{flags}"
        state.mkdir()
        (state / "config.json").write_text(json.dumps({"asr": {"mic_speakers": flags, "mic_dedupe": flags}}),
                                           encoding="utf-8")
        monkeypatch.setenv("MEET_DATA_DIR", str(state))
        folder = tmp_path / f"rec-{flags}" / "2026-10-05_12-00"
        folder.mkdir(parents=True)
        for role in ("sys", "mic"):
            (folder / f"{role}.opus").write_bytes(b"x")
        tr.transcribe(str(folder), align=False)
        final = library.read_transcript(folder)
        out[flags] = (final.pop("mic_split"), [{k: v for k, v in s.items()} for s in final["segments"]],
                      _sidecar(folder)["speakers"])
    assert out[True][0]["status"] == "no_profile" and out[False][0]["status"] == "off"
    assert out[True][1] == out[False][1] and out[True][2] == out[False][2]
    assert {s["speaker"] for s in out[True][1] if s.get("track") == "mic"} == {"Вы"}


# --- fix round 1: кто владелец (I1), голос владельца не в базу людей (I2) ------------------


def _config(tmp_path, monkeypatch, **recording):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "config.json").write_text(json.dumps({"recording": recording}, ensure_ascii=False),
                                       encoding="utf-8")
    monkeypatch.setenv("MEET_DATA_DIR", str(state))


def test_owner_under_a_former_name_is_not_in_the_room(call, tmp_path, monkeypatch):
    """Человек сменил подпись микрофона на «Н. Р.»: в старой записи его реплики
    под прежним «Кузьма» — не «в комнате» ни в расшифровке, ни в панели."""
    from meet import speakers, tray_control

    folder, voices = call
    _config(tmp_path, monkeypatch, speaker_name="Н. Р.", former_speaker_names=["Кузьма"])
    data = library.read_transcript(folder)
    for seg in data["segments"]:
        if seg["speaker"] == "Вы":
            seg["speaker"] = "Кузьма"
    library.write_transcript(folder, data)
    side_path = next(folder.glob("*_speakers.json"))
    side = json.loads(side_path.read_text(encoding="utf-8"))
    side["speakers"] = [e for e in side["speakers"] if not e.get("owner")]  # старая запись: OWNER нет
    side_path.write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    rows = {r["label"]: r for r in speakers.overview(folder, voices, owner="Н. Р.")["speakers"]}
    assert rows["Кузьма"]["room"] is False and rows["Спикер 3"]["room"] is True
    assert rows["Спикер 1"]["room"] is False
    shown = tray_control._window_transcript(library.read_transcript(folder), folder)["segments"]
    assert [s["speaker"] for s in shown if s.get("room")] == ["Спикер 3"]


def test_renamed_owner_row_stays_the_owner(call):
    """Строку «Вы» переименовали в «Кузьма» (панель «Спикеры»): владелец
    узнаётся по голосу OWNER сайдкара — его реплики не «в комнате»."""
    from meet import speakers, tray_control

    folder, voices = call
    speakers.apply(folder, [{"type": "rename", "label": "Вы", "to": "Кузьма"}], {}, voices)
    rows = {r["label"]: r for r in speakers.overview(folder, voices)["speakers"]}
    assert rows["Кузьма"]["room"] is False and rows["Спикер 3"]["room"] is True
    shown = tray_control._window_transcript(library.read_transcript(folder), folder)["segments"]
    assert [s["speaker"] for s in shown if s.get("room")] == ["Спикер 3"]


def _samples(voices: Path, name: str) -> int:
    path = voices / f"{name}.json"
    return len(json.loads(path.read_text(encoding="utf-8"))["samples"]) if path.exists() else 0


def test_owner_voice_never_goes_to_the_people_base(call):
    """«Вы» → «Кузьма» с «Запомнить голос»: голос владельца в базу людей не
    пишется (для него — «Запомнить мой голос»); строка о причине."""
    from meet import speakers

    folder, voices = call
    view = {r["label"]: r for r in speakers.overview(folder, voices)["speakers"]}
    assert view["Вы"]["owner_voice_only"] is True and view["Спикер 3"]["owner_voice_only"] is False
    before = _samples(voices, "Кузьма")
    got = speakers.apply(folder, [{"type": "rename", "label": "Вы", "to": "Кузьма"}], {"Вы": True}, voices)
    assert _samples(voices, "Кузьма") == before and got["step"]["enrolled"] == []
    assert got["voices_error"] == speakers.OWNER_NOT_ENROLLED


def test_owner_merged_into_a_person_enrolls_only_the_room_voice(call):
    from meet import speakers

    folder, voices = call
    got = speakers.apply(folder, [{"type": "rename", "label": "Спикер 3", "to": "Пётр"},
                                  {"type": "merge", "label": "Вы", "to": "Спикер 3"}],
                         {"Спикер 3": True, "Вы": True}, voices)
    assert [e["label"] for e in got["step"]["enrolled"]] == ["SPEAKER_M0"]
    assert _samples(voices, "Пётр") == 1


# --- fix round 1: реплики не склеиваются через дорожку (M8) ---------------------------------


def test_call_and_microphone_turns_of_one_person_stay_apart():
    """Человек в комнате с голосом кластера звонка: его реплика в звонке и
    сразу за ней в микрофоне — две реплики (окно, поиск, правка спикеров —
    одинаково), как и голос микрофона под вопросом."""
    from meet import search, speakers

    segs = [{"start": 0.0, "end": 1.0, "speaker": "Анна", "text": "в звонке"},
            {"start": 1.5, "end": 2.0, "speaker": "Анна", "text": "в комнате", "track": "mic"},
            {"start": 2.5, "end": 3.0, "speaker": "Вы", "text": "да", "track": "mic"},
            {"start": 3.5, "end": 4.0, "speaker": "Вы", "text": "наверное", "track": "mic", "uncertain": True},
            {"start": 4.5, "end": 5.0, "speaker": "Вы", "text": "точно", "track": "mic", "uncertain": True}]
    assert [t.text for t in search.turns_of(segs)] == ["в звонке", "в комнате", "да", "наверное точно"]
    shown = speakers._shown(segs)
    assert [t["texts"] for t in speakers._turns(segs, shown)] == [["в звонке"], ["в комнате"], ["да"],
                                                                 ["наверное", "точно"]]
    assert speakers._turn_count(segs, [0, 1]) == 2 and speakers._turn_count(segs, [3, 4]) == 1
    from meet import jira_refs

    assert jira_refs.turns_of(segs) == [[0], [1], [2], [3, 4]]


def test_meet_enroll_prefers_the_call_cluster_and_refuses_the_owner(tmp_path, monkeypatch):
    from meet import voices

    rec = tmp_path / "2026-10-05_10-00"
    rec.mkdir()
    (rec / "2026-10-05_speakers.json").write_text(json.dumps({"source": str(rec), "date": "2026-10-05",
                                                              "speakers": [
        {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": [1.0, 0.0]},
        {"label": "OWNER", "display": "Вы", "embedding": [0.0, 1.0], "track": "mic", "owner": True},
        {"label": "SPEAKER_M0", "display": "Спикер 2", "embedding": [0.5, 0.5], "track": "mic"}]},
        ensure_ascii=False), encoding="utf-8")
    base = tmp_path / "voices"
    voices.enroll(str(rec), ["Спикер 2=Анна"], folder=base)
    saved = json.loads((base / "Анна.json").read_text(encoding="utf-8"))["samples"]
    assert [s["embedding"] for s in saved] == [[1.0, 0.0]]
    with pytest.raises(SystemExit, match="ваш голос"):
        voices.enroll(str(rec), ["Вы=Кузьма"], folder=base)
    assert not (base / "Кузьма.json").exists()


# --- fix round 2: порог не трогает владельца (N1), голос-кандидат (N2) ----------------------


def _candidate(folder: Path) -> None:
    """Сайдкар встречи owner_not_found: OWNER — кандидат, не проверенный образцом."""
    side_path = next(folder.glob("*_speakers.json"))
    side = json.loads(side_path.read_text(encoding="utf-8"))
    for e in side["speakers"]:
        if e.get("owner"):
            e["candidate"] = True
    side_path.write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("candidate", [False, True])
def test_threshold_never_renames_the_owner_row(call, candidate):
    """«Порог узнавания»: строка «Вы» (голос OWNER, подтверждённый или
    кандидат) не переименовывается ни в «Спикер N», ни в человека из базы,
    даже если его голос похож на кого-то в базе («Кузьма» в базе = OWNER)."""
    from meet import speakers

    folder, voices = call
    if candidate:
        _candidate(folder)
    plan = speakers.threshold_plan(folder, 0.5, voices)
    assert "Вы" not in {r["label"] for r in plan["rows"]}
    speakers.threshold_apply(folder, 0.5, voices)
    shown = [s["speaker"] for s in library.read_transcript(folder)["segments"] if s.get("track") == "mic"]
    assert shown.count("Вы") == 2 and "Кузьма" not in shown


def test_overview_tells_a_candidate_owner_voice(call):
    from meet import speakers

    folder, voices = call
    assert speakers.overview(folder, voices)["owner_voice_candidate"] is False
    _candidate(folder)
    view = speakers.overview(folder, voices)
    assert view["owner_voice"] is True and view["owner_voice_candidate"] is True


def test_candidate_owner_voice_is_never_saved_without_confirmation(call):
    """«Это я» в встрече owner_not_found без явного «это точно я»: голос-
    кандидат образцом не становится."""
    from meet import speakers

    folder, voices = call
    _candidate(folder)
    got = speakers.apply(folder, [{"type": "rename", "label": "Спикер 3", "to": "Вы"}], {}, voices,
                         remember_owner=True, owner="Вы")
    assert owner_voice.load(voices) == [] and speakers.NO_OWNER_VOICE in got["voices_error"]


def test_confirmed_candidate_on_the_owner_row_itself_is_saved(call):
    """Строка «Вы» сама: «Голос не совпал с образцом — это точно вы?» →
    «Запомнить мой голос» — образец встречи из голоса-кандидата."""
    from meet import speakers

    folder, voices = call
    _candidate(folder)
    got = speakers.apply(folder, [], {}, voices, remember_owner=True, owner="Вы", confirm_candidate=True)
    (sample,) = owner_voice.load(voices)
    assert sample.source == "meeting" and sample.recording == folder.name
    assert got["step"]["owner_voice"] is True and got["voices_error"] is None


def test_confirmed_candidate_with_this_is_me_merge(call):
    """«Это я» на строке человека в комнате + подтверждение: образец из
    кандидата и той строки (кандидат взят до фильтра голоса владельца)."""
    from meet import speakers

    folder, voices = call
    _candidate(folder)
    got = speakers.apply(folder, [{"type": "rename", "label": "Спикер 3", "to": "Вы"}], {"Спикер 3": True},
                         voices, remember_owner=True, owner="Вы", confirm_candidate=True)
    assert [s.source for s in owner_voice.load(voices)] == ["meeting"]
    assert got["step"]["enrolled"] == [] and got["voices_error"] is None


def test_resident_passes_candidate_confirmation(call, monkeypatch, tmp_path):
    from meet import settings, tray, tray_control

    folder, voices = call
    _candidate(folder)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MEET_DATA_DIR", raising=False)
    settings.patch({"recording": {"out_dir": str(folder.parent), "voices_dir": str(voices)}})
    state = tray_control.TrayControl(tray.TrayApp())
    with pytest.raises(Exception, match="не запомнен"):
        state.speakers_apply(folder.name, {"ops": [], "remember": {}, "remember_owner": True})
    assert owner_voice.load(voices) == []
    got = state.speakers_apply(folder.name, {"ops": [], "remember": {}, "remember_owner": True,
                                             "owner_candidate": True})
    assert got["step"]["owner_voice"] is True and [s.source for s in owner_voice.load(voices)] == ["meeting"]
