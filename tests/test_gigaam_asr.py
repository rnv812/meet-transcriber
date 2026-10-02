"""GigaAM: нарезка на куски, разбор слов в реплики и распознавание с
поддельной моделью (без загрузки весов)."""

import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from meet import gigaam_asr as g
from meet.asr import Segment, Word

SR = 16000


def _speech(seconds: float, pauses: tuple[float, ...] = (), level: float = 0.3) -> np.ndarray:
    """«Речь» — громкий шум; паузы (0.2 с тишины) — в заданных секундах."""
    rng = np.random.default_rng(1)
    audio = (rng.standard_normal(int(seconds * SR)) * level).astype(np.float32)
    for p in pauses:
        audio[int(p * SR): int((p + 0.2) * SR)] = 0.0
    return audio


def test_neighbouring_regions_merge_up_to_max_length():
    audio = np.zeros(60 * SR, dtype=np.float32)
    regions = [(0.0, 5.0), (6.0, 12.0), (13.0, 20.0), (21.0, 30.0)]
    chunks = g.plan_chunks(regions, audio)
    assert [(c.start, c.end) for c in chunks] == [(0.0, 20.0), (21.0, 30.0)]
    assert all(c.length <= g.MAX_CHUNK_S for c in chunks)


def test_long_region_is_cut_at_the_pause_without_overlap():
    audio = _speech(40.0, pauses=(15.0,))
    chunks = g.plan_chunks([(0.0, 40.0)], audio)
    assert all(c.length <= g.MAX_CHUNK_S + 1e-9 for c in chunks)
    first, second = chunks[0], chunks[1]
    assert 15.0 <= first.end <= 15.2  # разрез в тишине
    assert second.start == first.end  # чистый разрез — без нахлёста
    assert first.keep_to == float("inf") and second.keep_from == float("-inf")


def test_latest_pause_wins_for_longer_chunks():
    audio = _speech(40.0, pauses=(10.0, 19.0))
    first = g.plan_chunks([(0.0, 40.0)], audio)[0]
    assert 19.0 <= first.end <= 19.2


def test_short_dip_inside_a_word_is_not_a_pause():
    """Смычка взрывного согласного (тишина 60 мс) — не место для разреза:
    режем в настоящей паузе раньше неё."""
    audio = _speech(40.0, pauses=(12.0,))
    audio[int(20.0 * SR): int(20.06 * SR)] = 0.0
    first = g.plan_chunks([(0.0, 40.0)], audio)[0]
    assert 12.0 <= first.end <= 12.2


def test_no_pause_means_hard_cut_with_overlap():
    audio = _speech(40.0)
    chunks = g.plan_chunks([(0.0, 40.0)], audio)
    assert all(c.length <= g.MAX_CHUNK_S + 1e-9 for c in chunks)
    first, second = chunks[0], chunks[1]
    assert first.end == pytest.approx(g.MAX_CHUNK_S)
    assert second.start == pytest.approx(g.MAX_CHUNK_S - g.HARD_OVERLAP_S)
    border = g.MAX_CHUNK_S - g.HARD_OVERLAP_S / 2
    assert first.keep_to == pytest.approx(border) and second.keep_from == pytest.approx(border)


def test_very_long_monologue_never_exceeds_max():
    audio = _speech(130.0, pauses=(30.0, 70.0))
    chunks = g.plan_chunks([(0.0, 130.0)], audio)
    assert all(c.length <= g.MAX_CHUNK_S + 1e-9 for c in chunks)
    assert chunks[0].start == 0.0 and chunks[-1].end == 130.0
    for a, b in zip(chunks, chunks[1:]):
        assert b.start <= a.end  # без дыр


def _w(text, start, end):
    return types.SimpleNamespace(text=text, start=start, end=end)


def test_overlap_words_are_deduplicated_by_time():
    first = g.Chunk(0.0, 22.0, keep_to=21.75)
    second = g.Chunk(21.5, 30.0, keep_from=21.75)
    # «слово» звучит на 21.55–21.70 и попало в оба куска
    a = g.words_of_chunk(first, [_w("начало", 21.0, 21.4), _w("слово", 21.55, 21.70)])
    b = g.words_of_chunk(second, [_w("слово", 0.05, 0.20), _w("дальше", 0.4, 0.8)])
    texts = [w.text for w in a + b]
    assert texts == [" начало", " слово", " дальше"]


def test_words_shift_to_meeting_time_and_carry_leading_space():
    words = g.words_of_chunk(g.Chunk(100.0, 110.0), [_w("Привет,", 0.5, 0.9), _w("", 1.0, 1.1)])
    assert words == [Word(100.5, 100.9, " Привет,")]


def test_segments_break_on_sentence_end_and_long_pause():
    words = [Word(0.0, 0.4, " Привет,"), Word(0.5, 0.9, " коллеги."), Word(1.0, 1.3, " Начнём"),
             Word(4.0, 4.3, " Итак")]
    segs = g.to_segments(words)
    assert [s.text for s in segs] == ["Привет, коллеги.", "Начнём", "Итак"]
    assert (segs[0].start, segs[0].end) == (0.0, 0.9)
    # контракт с words.json и правкой спикеров: текст реплики собирается из слов
    for s in segs:
        assert "".join(w.text for w in s.words).strip() == s.text


def _write_wav(path: Path, audio: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((audio * 32767).astype(np.int16).tobytes())
    return path


class FakeModel:
    """Модель GigaAM для тестов: на каждый кусок — два слова с таймкодами
    от начала куска; запоминает длины кусков."""

    def __init__(self):
        self.lengths = []

    def transcribe(self, wav_file, word_timestamps=False):
        with wave.open(wav_file, "rb") as wf:
            seconds = wf.getnframes() / wf.getframerate()
        self.lengths.append(seconds)
        n = len(self.lengths)
        assert word_timestamps
        return types.SimpleNamespace(
            text=f"Кусок {n}.",
            words=[_w("Кусок", 0.1, 0.5), _w(f"{n}.", 0.6, 0.9)],
        )


def test_transcribe_feeds_chunks_and_returns_segments(tmp_path):
    wav = _write_wav(tmp_path / "a.wav", _speech(50.0, pauses=(18.0, 36.0)))
    model = FakeModel()
    segs = g.transcribe(wav, regions=[(2.0, 50.0)], model=model)
    assert model.lengths and max(model.lengths) <= g.MAX_CHUNK_S + 0.01
    assert all(isinstance(s, Segment) for s in segs)
    assert segs[0].start == pytest.approx(2.1)
    assert [s.text for s in segs][:2] == ["Кусок 1.", "Кусок 2."]


def test_transcribe_loads_model_into_app_models_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    seen = {}

    def load_model(name, device=None, download_root=None, **kw):
        seen.update(name=name, device=device, root=download_root)
        return FakeModel()

    monkeypatch.setitem(sys.modules, "gigaam", types.SimpleNamespace(load_model=load_model))
    wav = _write_wav(tmp_path / "a.wav", _speech(5.0))
    segs = g.transcribe(wav, model_name="v3_e2e_ctc", device="cpu", regions=[(0.0, 5.0)])
    assert seen == {"name": "v3_e2e_ctc", "device": "cpu", "root": str(data / "models" / "gigaam")}
    assert segs and segs[0].text == "Кусок 1."


def test_model_files_downloaded_size_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    assert not g.downloaded("v3_e2e_rnnt")
    root = g.cache_dir()
    root.mkdir(parents=True)
    (root / "v3_e2e_rnnt.ckpt").write_bytes(b"x" * 10)
    assert not g.downloaded("v3_e2e_rnnt")  # без токенизатора — не скачана
    (root / "v3_e2e_rnnt_tokenizer.model").write_bytes(b"y" * 5)
    assert g.downloaded("v3_e2e_rnnt") and g.size_on_disk("v3_e2e_rnnt") == 15
    assert g.remove("v3_e2e_rnnt") == 2
    assert not g.downloaded("v3_e2e_rnnt") and g.size_on_disk("v3_e2e_rnnt") == 0


def test_module_import_stays_light():
    """Настройки импортируют этот модуль в резиденте: без numpy/torch на верхнем уровне."""
    import subprocess

    code = ("import sys; import meet.gigaam_asr; "
            "print('numpy' in sys.modules, 'torch' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(Path(g.__file__).parents[1])})
    assert out.stdout.strip() == "False False"
