"""Калибровка порогов голосов (scripts/speakers_calib.py, T0): только на
синтетике — поддельный эмбеддер и выдуманные записи. Отчёт — одни числа:
ни имён, ни текста реплик, ни имён папок."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
DIM = 8


def _calib():
    spec = importlib.util.spec_from_file_location("speakers_calib", ROOT / "scripts" / "speakers_calib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _e(i):
    return np.eye(DIM)[i]


# Голос — уровень сигнала: эмбеддер-подделка читает «чей голос» по
# постоянной составляющей клипа (номер голоса × 0.01) плюс шум окна.
def fake_embed(audio):
    who = int(round(float(np.median(audio)) * 100))
    if not 0 <= who < 4:
        return None
    rng = np.random.default_rng(int(len(audio)) + who)
    v = _e(who) + rng.normal(0, 0.1, DIM)
    return (v / np.linalg.norm(v)).astype(np.float32)


def _track(spans, total=60.0):
    audio = np.full(int(total * RATE), -0.5, dtype=np.float32)  # тишина: «не голос»
    for start, end, who in spans:
        audio[int(start * RATE):int(end * RATE)] = who * 0.01
    return (audio * 32767).astype(np.int16)


def _words(text, start, step=0.5):
    return [{"start": start + i * step, "end": start + i * step + 0.45, "text": " " + w}
            for i, w in enumerate(text.split())]


def _seg(text, start, speaker, track, step=0.5):
    words = _words(text, start, step)
    return {"start": words[0]["start"], "end": words[-1]["end"], "speaker": speaker, "text": text,
            "uncertain": False, "track": track, "words": words}


SECRET_TEXT = "секретный проект Альфа"
PERSON = "Демьян Секретов"


@pytest.fixture
def recording(tmp_path):
    folder = tmp_path / "2026-10-05_13-32"
    folder.mkdir()
    sys_segs = [_seg(f"{SECRET_TEXT} обсуждаем сроки и бюджет", 1.0 + 8 * i, PERSON if i % 2 == 0 else "Спикер 2",
                     "sys") for i in range(6)]
    mic_segs = [_seg("я думаю что это реально сделать к пятнице", 50.0, "Вы", "mic")]
    mic_segs += [_seg("да согласен полностью давайте так и сделаем", 30.0, "Вы", "mic")]
    # Копия реплики собеседника в микрофоне — на 0,4 с раньше (сосед).
    mic_segs += [_seg(f"{SECRET_TEXT} обсуждаем сроки и бюджет", 0.6, "Вы", "mic")]
    data = {"segments": sorted(sys_segs + mic_segs, key=lambda s: s["start"]), "track_marks": "pipeline"}
    (folder / "transcript.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    side = {"speakers": [{"label": "SPEAKER_00", "display": PERSON, "embedding": _e(1).tolist()},
                         {"label": "SPEAKER_01", "display": "Спикер 2", "embedding": _e(2).tolist()}]}
    (folder / "2026-10-05_13-32_speakers.json").write_text(json.dumps(side, ensure_ascii=False), encoding="utf-8")
    for stem in ("sys", "mic"):
        (folder / f"{stem}.opus").write_bytes(b"")
    spans_sys = [(s["start"], s["end"], 1 if s["speaker"] == PERSON else 2) for s in sys_segs]
    spans_mic = [(s["start"], s["end"], 0) for s in mic_segs[:2]] + [(0.6, mic_segs[2]["end"], 1)]
    audio = {"sys": _track(spans_sys), "mic": _track(spans_mic)}
    voices = tmp_path / "voices"
    voices.mkdir()
    (voices / f"{PERSON}.json").write_text(json.dumps({"samples": [
        {"embedding": _e(1).tolist(), "source": "другая встреча", "date": "2026-10-01", "id": "a"}]},
        ensure_ascii=False), encoding="utf-8")
    (voices / "Анна.json").write_text(json.dumps({"samples": [
        {"embedding": _e(3).tolist(), "source": "x", "date": "2026-10-01", "id": "b"}]}), encoding="utf-8")
    return folder, voices, (lambda path: audio[Path(path).stem])


def test_report_has_all_distributions(recording, tmp_path):
    folder, voices, load = recording
    out = tmp_path / "отчёт" / "calib.md"
    calib = _calib()
    report = calib.main([str(folder), "--voices", str(voices), "--out", str(out)], embed=fake_embed, load=load)
    assert out.exists() and out.with_suffix(".json").exists()
    raw = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert raw == json.loads(json.dumps(report))
    assert raw["recordings"] == 1
    seg = raw["segments_vs_centroids"]
    assert seg["own"]["n"] == 6 and seg["own"]["p50"] > 0.9 and seg["other"]["p50"] < 0.3
    live = raw["live_vs_base"]
    assert live["checkpoints"]["4"]["own"]["n"] == 1 and live["checkpoints"]["4"]["own"]["p50"] > 0.9
    assert live["checkpoints"]["4"]["margin"]["p50"] > 0.5
    mic = raw["mic_windows_vs_owner"]
    assert mic["reference"] == "self" and mic["cos"]["n"] >= 3
    assert raw["dedupe_lags"]["neighbour"]["n"] == 1
    assert raw["dedupe_lags"]["neighbour"]["p50"] == pytest.approx(0.4, abs=0.01)
    emb = raw["embed_ms"]
    assert emb["n"] > 0 and emb["p50"] >= 0 and raw["device"] == "injected"
    assert raw["constants"]["T_OWN"] == 0.72


def test_report_contains_no_personal_data(recording, tmp_path):
    folder, voices, load = recording
    out = tmp_path / "calib.md"
    _calib().main([str(folder), "--voices", str(voices), "--out", str(out)], embed=fake_embed, load=load)
    for path in (out, out.with_suffix(".json")):
        text = path.read_text(encoding="utf-8")
        for secret in (PERSON, "Секретов", "Анна", "секретный", "Альфа", folder.name, str(tmp_path)):
            assert secret not in text, (path.name, secret)


def test_owner_sample_is_used_when_present(recording, tmp_path):
    from meet import owner_voice

    folder, voices, load = recording
    owner_voice.add(_e(0), source="enroll", seconds=25, device="USB", voices=voices)
    out = tmp_path / "calib.md"
    report = _calib().main([str(folder), "--voices", str(voices), "--out", str(out)], embed=fake_embed, load=load)
    mic = report["mic_windows_vs_owner"]
    assert mic["reference"] == "owner_sample"
    assert mic["cos"]["p90"] > 0.9 and 0 < mic["fast_share"]["p50"] < 1


def test_recording_without_tracks_or_transcript_is_skipped(tmp_path, recording):
    folder, voices, load = recording
    empty = tmp_path / "2026-10-06_10-00"
    empty.mkdir()
    out = tmp_path / "calib.md"
    report = _calib().main([str(empty), str(folder), "--voices", str(voices), "--out", str(out)],
                           embed=fake_embed, load=load)
    assert report["recordings"] == 1 and report["skipped"] == 1


def test_no_folders_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        _calib().main(["--out", str(tmp_path / "x.md")], embed=fake_embed, load=lambda p: None)
