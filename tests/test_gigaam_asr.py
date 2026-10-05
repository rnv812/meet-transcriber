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

# Маленькие «модели» вместо 450 МБ: содержимое, размер и контрольные суммы.
BLOBS = {
    "v3_e2e_rnnt.ckpt": b"w" * 10, "v3_e2e_rnnt_tokenizer.model": b"t" * 5,
    "v3_e2e_ctc.ckpt": b"c" * 8, "v3_e2e_ctc_tokenizer.model": b"k" * 4,
}


def small_files():
    import hashlib

    def entry(name, algo):
        return (name, len(BLOBS[name]), algo, hashlib.new(algo, BLOBS[name]).hexdigest())

    return {m: (entry(f"{m}.ckpt", "md5"), entry(f"{m}_tokenizer.model", "sha256"))
            for m in ("v3_e2e_rnnt", "v3_e2e_ctc")}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Сети в тестах нет: загрузка — только через поддельный opener."""
    import urllib.request

    def refuse(*a, **kw):
        raise AssertionError("тест полез в сеть")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(g, "FILES", small_files())


class _Response:
    def __init__(self, data: bytes):
        self._data, self._pos = data, 0

    def read(self, n=-1):
        chunk = self._data[self._pos:self._pos + n] if n >= 0 else self._data[self._pos:]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener(blobs=None, calls=None):
    blobs = BLOBS if blobs is None else blobs

    def open_url(url):
        name = url.rsplit("/", 1)[-1]
        if calls is not None:
            calls.append(name)
        if name not in blobs:
            raise OSError("нет связи")
        return _Response(blobs[name])

    return open_url


def fake_gigaam(monkeypatch, load_model):
    """Модуль gigaam с decoding.SentencePieceProcessor, как у настоящего."""
    class Processor:
        def Load(self, model_file=None, model_proto=None):  # noqa: N802
            self.args = (model_file, model_proto)
            return True

        load = Load

    decoding = types.SimpleNamespace(SentencePieceProcessor=Processor)
    module = types.SimpleNamespace(load_model=load_model, decoding=decoding)
    monkeypatch.setitem(sys.modules, "gigaam", module)
    monkeypatch.setitem(sys.modules, "gigaam.decoding", decoding)
    return decoding


def put(name: str, data: bytes | None = None) -> Path:
    root = g.cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(BLOBS[name] if data is None else data)
    return root / name


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


def test_each_chunk_is_deleted_right_after_recognition(tmp_path):
    """Звук встречи не копится во временной папке: на диске — только кусок,
    который распознаётся сейчас; папка — с pid в имени (её найдёт уборка)."""
    from meet import tempdirs

    wav = _write_wav(tmp_path / "a.wav", _speech(50.0, pauses=(18.0, 36.0)))
    seen = []

    class Watching(FakeModel):
        def transcribe(self, wav_file, word_timestamps=False):
            here = Path(wav_file).parent
            seen.append((here, sorted(p.name for p in here.iterdir())))
            return super().transcribe(wav_file, word_timestamps)

    g.transcribe(wav, regions=[(2.0, 50.0)], model=Watching())
    assert len(seen) >= 2
    assert all(len(files) == 1 for _name, files in seen)
    assert seen[0][0].name.startswith(tempdirs.prefix("gigaam-"))
    assert not seen[0][0].exists()


def test_transcribe_loads_model_into_app_models_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    seen = {}

    def load_model(name, device=None, download_root=None, **kw):
        seen.update(name=name, device=device, root=download_root)
        return FakeModel()

    fake_gigaam(monkeypatch, load_model)
    put("v3_e2e_ctc.ckpt")
    put("v3_e2e_ctc_tokenizer.model")
    wav = _write_wav(tmp_path / "a.wav", _speech(5.0))
    segs = g.transcribe(wav, model_name="v3_e2e_ctc", device="cpu", regions=[(0.0, 5.0)])
    assert seen == {"name": "v3_e2e_ctc", "device": "cpu", "root": str(data / "models" / "gigaam")}
    assert segs and segs[0].text == "Кусок 1."


def test_model_files_downloaded_size_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    assert not g.downloaded("v3_e2e_rnnt")
    put("v3_e2e_rnnt.ckpt")
    assert not g.downloaded("v3_e2e_rnnt")  # без токенизатора — не скачана
    assert g.present("v3_e2e_rnnt")  # но удалить её можно
    put("v3_e2e_rnnt_tokenizer.model", b"y" * 3)
    assert not g.downloaded("v3_e2e_rnnt")  # токенизатор не того размера
    put("v3_e2e_rnnt_tokenizer.model")
    assert g.downloaded("v3_e2e_rnnt") and g.size_on_disk("v3_e2e_rnnt") == 15
    (g.cache_dir() / "v3_e2e_rnnt.ckpt.part").write_bytes(b"p")
    assert g.remove("v3_e2e_rnnt") == 3  # и недокачанный .part
    assert not g.present("v3_e2e_rnnt") and g.size_on_disk("v3_e2e_rnnt") == 0


def test_module_import_stays_light():
    """Настройки импортируют этот модуль в резиденте: без numpy/torch на верхнем уровне."""
    import subprocess

    code = ("import sys; import meet.gigaam_asr; "
            "print('numpy' in sys.modules, 'torch' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(Path(g.__file__).parents[1])})
    assert out.stdout.strip() == "False False"


# --- нахлёст жёсткого разреза: слово на границе ---------------------------------


def test_straddling_word_is_kept_once_from_the_first_chunk():
    """«интеграция» звучит 21.40–22.10 и разрезана на 22.0: первый кусок
    слышит её до разреза, второй (с 21.5) — хвост «грация». Остаётся одно
    слово, из первого куска (не «интеграция грация»)."""
    first = g.Chunk(0.0, 22.0, keep_to=21.75)
    second = g.Chunk(21.5, 30.0, keep_from=21.75)
    a = g.words_of_chunk(first, [_w("интеграция", 21.40, 22.00)])
    b = g.words_of_chunk(second, [_w("грация", 0.0, 0.60), _w("дальше", 0.7, 1.0)])
    assert [w.text for w in a + b] == [" интеграция", " дальше"]


def test_word_starting_after_the_border_comes_from_the_second_chunk():
    first = g.Chunk(0.0, 22.0, keep_to=21.75)
    second = g.Chunk(21.5, 30.0, keep_from=21.75)
    a = g.words_of_chunk(first, [_w("было", 21.0, 21.6), _w("инте", 21.85, 22.0)])
    b = g.words_of_chunk(second, [_w("было", 0.0, 0.1), _w("интеграция", 0.35, 0.95)])
    assert [w.text for w in a + b] == [" было", " интеграция"]


# --- загрузка весов --------------------------------------------------------------


def test_ensure_downloads_through_a_part_file_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    calls, lines = [], []
    g.ensure("v3_e2e_rnnt", lines.append, opener=opener(calls=calls))
    assert calls == ["v3_e2e_rnnt.ckpt", "v3_e2e_rnnt_tokenizer.model"]
    assert g.downloaded("v3_e2e_rnnt")
    assert not list(g.cache_dir().glob("*.part"))
    # второй раз — ничего не качает и не хеширует заново (отметка проверки)
    calls.clear()
    monkeypatch.setattr(g, "_digest", lambda *a: pytest.fail("повторное хеширование"))
    g.ensure("v3_e2e_rnnt", opener=opener(calls=calls))
    assert calls == []


def test_ensure_waits_while_another_process_downloads_the_model(tmp_path, monkeypatch):
    """Загрузка из окна и расшифровка пришли за одной моделью: второй ждёт
    замка модели, а потом находит файлы готовыми и не качает их снова."""
    import threading

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(g, "LOCK_POLL_S", 0.01)
    g.cache_dir().mkdir(parents=True)
    holding, release = threading.Event(), threading.Event()

    def first():  # «другой процесс»: держит замок и докачивает
        with g._files_lock("v3_e2e_rnnt"):
            holding.set()
            release.wait(5)
            for name in ("v3_e2e_rnnt.ckpt", "v3_e2e_rnnt_tokenizer.model"):
                put(name, BLOBS[name])

    owner = threading.Thread(target=first)
    owner.start()
    assert holding.wait(5)
    calls, lines = [], []
    second = threading.Thread(
        target=lambda: g.ensure("v3_e2e_rnnt", lines.append, opener=opener(calls=calls)))
    second.start()
    second.join(0.3)
    assert second.is_alive(), "ensure не ждал замка модели"
    release.set()
    owner.join(5)
    second.join(5)
    assert not second.is_alive()
    assert calls == []  # докачал первый — второй не качает
    assert any("уже скачивается" in line for line in lines)
    # Замок модели своей папки моделей не держит чужую модель.
    assert g._lock_path("v3_e2e_rnnt") != g._lock_path("v3_e2e_ctc")


def test_corrupt_or_partial_file_is_replaced_once(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    put("v3_e2e_rnnt.ckpt", b"x" * 10)  # тот же размер, другое содержимое
    put("v3_e2e_rnnt_tokenizer.model", b"t")  # недокачан
    calls, lines = [], []
    g.ensure("v3_e2e_rnnt", lines.append, opener=opener(calls=calls))
    assert calls == ["v3_e2e_rnnt.ckpt", "v3_e2e_rnnt_tokenizer.model"]
    assert (g.cache_dir() / "v3_e2e_rnnt.ckpt").read_bytes() == BLOBS["v3_e2e_rnnt.ckpt"]
    assert any("повреждён" in line for line in lines)


def test_bad_download_leaves_nothing_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    broken = {**BLOBS, "v3_e2e_rnnt.ckpt": b"q" * 10}
    with pytest.raises(g.Unavailable, match="повреждённым"):
        g.ensure("v3_e2e_rnnt", opener=opener(broken))
    assert not (g.cache_dir() / "v3_e2e_rnnt.ckpt").exists()
    assert not list(g.cache_dir().glob("*.part"))
    with pytest.raises(g.Unavailable, match="не удалось скачать"):
        g.ensure("v3_e2e_rnnt", opener=opener({}))  # нет сети


def test_interrupted_download_is_not_a_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    class Dies(_Response):
        def read(self, n=-1):
            raise ConnectionResetError("обрыв")

    with pytest.raises(g.Unavailable):
        g.ensure("v3_e2e_rnnt", opener=lambda url: Dies(b""))
    assert not g.downloaded("v3_e2e_rnnt") and not list(g.cache_dir().glob("*.part"))


def test_load_reads_tokenizer_from_bytes_for_any_path(tmp_path, monkeypatch):
    """sentencepiece не открывает путь с кириллицей: GigaAM получает класс,
    который читает файл сам и передаёт model_proto."""
    data = tmp_path / "Кузьма тест"
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    seen = {}

    def load_model(name, device=None, download_root=None, **kw):
        processor = decoding.SentencePieceProcessor()
        processor.load(str(Path(download_root) / f"{name}_tokenizer.model"))
        seen.update(args=processor.args, root=download_root)
        return FakeModel()

    decoding = fake_gigaam(monkeypatch, load_model)
    g.ensure("v3_e2e_rnnt", opener=opener())
    g.load("v3_e2e_rnnt")
    assert seen["args"] == (None, BLOBS["v3_e2e_rnnt_tokenizer.model"])
    assert "Кузьма тест" in seen["root"]
    patched = decoding.SentencePieceProcessor
    g.load("v3_e2e_rnnt")  # повторная подмена не наслаивается
    assert decoding.SentencePieceProcessor is patched


def test_load_failure_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    def load_model(*a, **kw):
        raise AssertionError("Model checksum failed")

    fake_gigaam(monkeypatch, load_model)
    g.ensure("v3_e2e_rnnt", opener=opener())
    with pytest.raises(g.Unavailable, match="не загрузилась"):
        g.load("v3_e2e_rnnt")


def test_real_sentencepiece_loads_from_cyrillic_folder(tmp_path, monkeypatch):
    """С настоящим sentencepiece (если он есть в окружении): модель в папке
    «Кузьма тест» грузится через подмену GigaAM."""
    spm = pytest.importorskip("sentencepiece")
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("\n".join(["привет мир", "встреча началась", "обсудим задачи"] * 50),
                      encoding="utf-8")
    spm.SentencePieceTrainer.train(input=str(corpus), model_prefix=str(tmp_path / "tok"),
                                   vocab_size=24, character_coverage=1.0, hard_vocab_limit=False)
    folder = tmp_path / "Кузьма тест"
    folder.mkdir()
    target = folder / "tok.model"
    target.write_bytes((tmp_path / "tok.model").read_bytes())
    decoding = types.SimpleNamespace(SentencePieceProcessor=spm.SentencePieceProcessor)
    monkeypatch.setitem(sys.modules, "gigaam", types.SimpleNamespace(decoding=decoding))
    monkeypatch.setitem(sys.modules, "gigaam.decoding", decoding)
    g._proto_tokenizer()
    processor = decoding.SentencePieceProcessor()
    processor.load(str(target))
    assert len(processor) > 0 and processor.encode("привет мир")


# --- таймауты, отметка проверки, ошибки подготовки -------------------------------


def test_default_download_uses_connect_timeout(tmp_path, monkeypatch):
    import urllib.request

    seen = {}

    def urlopen(url, timeout=None):
        seen["timeout"] = timeout
        return _Response(BLOBS[url.rsplit("/", 1)[-1]])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    g.ensure("v3_e2e_ctc")
    assert seen["timeout"] == g.CONNECT_TIMEOUT_S == 20
    assert g.READ_TIMEOUT_S == 60


def test_read_timeout_becomes_unavailable(tmp_path, monkeypatch):
    import socket

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    class Stalls(_Response):
        def read(self, n=-1):
            raise socket.timeout("timed out")

    with pytest.raises(g.Unavailable) as error:
        g.ensure("v3_e2e_rnnt", opener=lambda url: Stalls(b""))
    assert error.value.network and "не удалось скачать" in str(error.value)
    assert not list(g.cache_dir().glob("*.part"))


def test_trickling_download_is_cut(tmp_path, monkeypatch):
    """Сервер «капает» по байту (каждое чтение укладывается в таймаут) —
    загрузка всё равно обрывается по STALL_S, а не держит очередь вечно."""
    import time

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    clock = iter(range(0, 10_000, 50))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))

    class Trickle(_Response):
        def read1(self, n=-1):
            return b"w"

    with pytest.raises(g.Unavailable, match="почти остановилась"):
        g.ensure("v3_e2e_rnnt", opener=lambda url: Trickle(b""))


def test_failed_load_drops_the_verification_stamp(tmp_path, monkeypatch):
    """Модель не загрузилась — отметка снимается: в следующий раз файлы
    сверятся заново, а не будут «проверенными» навсегда."""
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    def load_model(*a, **kw):
        raise RuntimeError("tokenizer parse error")

    fake_gigaam(monkeypatch, load_model)
    g.ensure("v3_e2e_rnnt", opener=opener())
    stamp = g.cache_dir() / "v3_e2e_rnnt.verified.json"
    assert stamp.exists()
    with pytest.raises(g.Unavailable):
        g.load("v3_e2e_rnnt")
    assert not stamp.exists()


def test_any_preparation_error_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    def boom(*a, **kw):
        raise PermissionError("файл занят другой программой")

    monkeypatch.setattr(g, "ensure", boom)
    with pytest.raises(g.Unavailable, match="модель не подготовлена"):
        g.load("v3_e2e_rnnt")
