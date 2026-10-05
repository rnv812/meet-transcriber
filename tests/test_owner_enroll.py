"""Разбор записи образца голоса владельца (meet.owner_enroll): речь по VAD,
клиппинг, уровень над фоном, три трети с согласованными отпечатками.

Ни микрофона, ни моделей: звук синтетический (тон над шумом), VAD и
эмбеддер — подставные."""

import wave

import numpy as np
import pytest

from meet import owner_enroll, owner_voice

RATE = 16000
DIM = 8


def _take(speech=((1.0, 21.0),), seconds=25.0, tone=0.1, noise=0.001, seed=0):
    """Тон 220 Гц на участках речи поверх слабого шума, int16 16 кГц."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    audio = rng.normal(0.0, noise, t.size)
    for a, b in speech:
        m = (t >= a) & (t < b)
        audio[m] += tone * np.sin(2 * np.pi * 220 * t[m])
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16)


def _vad(regions):
    return lambda audio, sr: list(regions)


class _Embed:
    """Отпечатки по очереди; запоминает длины кусков, которые ему дали."""

    def __init__(self, *vectors):
        self.vectors = list(vectors)
        self.lengths: list[int] = []

    def __call__(self, audio):
        assert audio.dtype == np.float32 and np.abs(audio).max() <= 1.0
        self.lengths.append(len(audio))
        return self.vectors.pop(0)


def _v(*parts):
    v = np.zeros(DIM, dtype=np.float32)
    v[: len(parts)] = parts
    return v


SAME = (_v(1, 0.1), _v(1, 0, 0.1), _v(1, 0.05, 0.05))


def test_good_take_gives_unit_centroid_of_three_thirds():
    regions = [(1.0, 8.0), (8.3, 21.0)]
    embed = _Embed(*(x * k for x, k in zip(SAME, (3.0, 0.5, 1.0))))  # нормы разные
    got = owner_enroll.analyze(_take(speech=regions), vad=_vad(regions), embed=embed)
    assert got.seconds == pytest.approx(19.7)
    assert np.linalg.norm(got.embedding) == pytest.approx(1.0, abs=1e-5)
    units = [x / np.linalg.norm(x) for x in SAME]
    expected = sum(units) / np.linalg.norm(sum(units))
    assert np.allclose(got.embedding, expected, atol=1e-5)
    assert got.quality == pytest.approx(min(
        float(units[i] @ units[j]) for i, j in ((0, 1), (0, 2), (1, 2))), abs=1e-4)
    assert got.snr_db > 30
    # Три равные трети только речи: паузы между участками в отпечаток не идут.
    assert len(embed.lengths) == 3
    assert sum(embed.lengths) == pytest.approx(19.7 * RATE, abs=3)
    assert max(embed.lengths) - min(embed.lengths) <= 1


def test_runs_shorter_than_two_seconds_still_count_as_speech():
    """Чтение с паузами: короткие участки VAD тоже речь (их склеивает пауза)."""
    regions = [(a, a + 1.5) for a in np.arange(0.5, 24.0, 1.6)]
    got = owner_enroll.analyze(_take(speech=regions), vad=_vad(regions), embed=_Embed(*SAME))
    assert got.seconds >= 15


def test_too_little_speech_is_explained():
    regions = [(1.0, 10.0)]
    with pytest.raises(owner_enroll.QualityError) as e:
        owner_enroll.analyze(_take(speech=regions), vad=_vad(regions), embed=_Embed(*SAME))
    assert "9 с" in str(e.value) and "15" in str(e.value)


def test_silence_says_the_voice_was_not_heard():
    with pytest.raises(owner_enroll.QualityError) as e:
        owner_enroll.analyze(_take(speech=()), vad=_vad([]), embed=_Embed(*SAME))
    assert "не слышен" in str(e.value) and "микрофон" in str(e.value)


def test_clipping_is_refused():
    regions = [(1.0, 21.0)]
    audio = _take(speech=regions, tone=1.6)  # пики далеко за полной шкалой
    with pytest.raises(owner_enroll.QualityError) as e:
        owner_enroll.analyze(audio, vad=_vad(regions), embed=_Embed(*SAME))
    assert "громко" in str(e.value)


def test_clipping_is_checked_on_the_original_recording():
    """После передискретизации пики сглажены: клиппинг ищем в исходном WAV."""
    regions = [(1.0, 21.0)]
    raw = np.full(48000 * 25, 32767, dtype=np.int16)
    with pytest.raises(owner_enroll.QualityError):
        owner_enroll.analyze(_take(speech=regions), raw=raw, vad=_vad(regions), embed=_Embed(*SAME))


def test_noise_close_to_voice_level_is_refused():
    regions = [(1.0, 21.0)]
    audio = _take(speech=regions, tone=0.05, noise=0.01)  # ~10 дБ над шумом
    with pytest.raises(owner_enroll.QualityError) as e:
        owner_enroll.analyze(audio, vad=_vad(regions), embed=_Embed(*SAME))
    assert "дБ" in str(e.value) and "15" in str(e.value)


def test_thirds_that_disagree_ask_to_repeat_in_silence():
    regions = [(1.0, 21.0)]
    embed = _Embed(_v(1), _v(1, 0.1), _v(0, 1))
    with pytest.raises(owner_enroll.QualityError) as e:
        owner_enroll.analyze(_take(speech=regions), vad=_vad(regions), embed=embed)
    assert "повторите в тишине" in str(e.value).lower()


def test_failed_embedding_is_a_quality_error():
    regions = [(1.0, 21.0)]
    with pytest.raises(owner_enroll.QualityError):
        owner_enroll.analyze(_take(speech=regions), vad=_vad(regions), embed=_Embed(_v(1), None, _v(1)))


def _wav(path, audio, rate=RATE):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.astype("<i2").tobytes())
    return path


def test_enroll_stores_enroll_sample_with_device(tmp_path):
    regions = [(1.0, 21.0)]
    audio = _take(speech=regions)
    wav = _wav(tmp_path / "take.wav", audio)
    voices = tmp_path / "voices"
    sample = owner_enroll.enroll(wav, device="USB-микрофон", voices=voices, vad=_vad(regions),
                                 embed=_Embed(*SAME), decode=lambda p: audio)
    (stored,) = owner_voice.load(voices)
    assert stored.id == sample.id and stored.source == "enroll"
    assert stored.device == "USB-микрофон" and stored.seconds == pytest.approx(20.0)
    assert stored.quality == sample.quality and 0.75 <= stored.quality <= 1.0
    assert wav.exists()  # удаляет файл задача (job_worker), а не разбор


def test_enroll_with_bad_take_writes_nothing(tmp_path):
    audio = _take(speech=())
    wav = _wav(tmp_path / "take.wav", audio)
    with pytest.raises(owner_enroll.QualityError):
        owner_enroll.enroll(wav, device=None, voices=tmp_path / "voices", vad=_vad([]),
                            embed=_Embed(*SAME), decode=lambda p: audio)
    assert owner_voice.load(tmp_path / "voices") == []


def test_read_wav_downmixes_and_reports_rate(tmp_path):
    path = tmp_path / "st.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.array([100, 300, -10, -30], dtype="<i2").tobytes())
    raw, rate = owner_enroll.read_wav(path)
    assert rate == 48000 and raw.tolist() == [200, -20]


def test_embedder_is_capped_at_four_threads(monkeypatch):
    import sys
    import types

    calls = []
    fake_torch = types.SimpleNamespace(set_num_threads=calls.append)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    from meet import segvoices

    monkeypatch.setattr(segvoices, "load_embedder", lambda: "эмбеддер")
    monkeypatch.setattr(owner_enroll.os, "cpu_count", lambda: 20)
    assert owner_enroll.load_embedder() == "эмбеддер"
    assert calls == [4]
