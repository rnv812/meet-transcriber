"""Распознавание GigaAM (salute-developers/GigaAM, MIT) — русский движок для CPU.

Почему он: на 6-минутном фрагменте встречи (i9-13900H, 14 потоков) GigaAM
`v3_e2e_rnnt` распознаёт за ~20 с против ~7,5 мин у faster-whisper medium, с
пунктуацией, заглавными и пословными таймкодами, и ближе к эталону CUDA.
Ограничения: только русский, латиница транслитерируется («api» → «апи»), нет
подсказок (hotwords), и на вход — не длиннее ~25 с звука за вызов.

Поэтому здесь своя нарезка: речь находит тот же Silero VAD, что фильтрует
тишину у Whisper (`vad_filter`), соседние участки речи собираются в куски не
длиннее `MAX_CHUNK_S`, а длинный монолог режется в самой тихой точке (пауза
между словами). Тихой точки нет — жёсткий разрез с нахлёстом `HARD_OVERLAP_S`,
а повторившиеся в нахлёсте слова отбрасываются по времени.

Только публичный API GigaAM: `load_model(...)` и `model.transcribe(wav,
word_timestamps=True)` на каждый кусок. Пакетный режим (в 7 раз быстрее на
GPU) — внутренности библиотеки, и обновление их сломает; на CPU выигрыша нет.

Модуль лёгкий на импорт (без numpy и torch на верхнем уровне): его константы
читают настройки и каталог моделей в резиденте.
"""

from dataclasses import dataclass
from pathlib import Path

MODEL_NAME = "v3_e2e_rnnt"
MODELS = ("v3_e2e_rnnt", "v3_e2e_ctc")
SAMPLE_RATE = 16000

# GigaAM принимает до 25 с; держим запас, чтобы не упереться в его порог.
MAX_CHUNK_S = 22.0
# Раньше этого длинный участок не режем: куски по 2 с дороже (вызов на кусок)
# и беднее контекстом.
MIN_CHUNK_S = 8.0
# Жёсткий разрез: следующий кусок начинается на столько раньше, слова из
# нахлёста делятся по середине нахлёста.
HARD_OVERLAP_S = 0.5
# Кадр оценки громкости при поиске паузы.
FRAME_S = 0.03
# Кадр тише этой доли медианной громкости участка — тихий.
QUIET_RATIO = 0.35
# Пауза между словами — тихих кадров подряд хотя бы на столько.
PAUSE_S = 0.12
# Новая реплика — после конца предложения или паузы между словами длиннее этой.
SEGMENT_GAP_S = 1.5
_SENTENCE_END = (".", "?", "!", "…")


@dataclass(frozen=True)
class Chunk:
    """Кусок звука для одного вызова: [start, end) в секундах записи.

    `keep_from`/`keep_to` — какие слова куска остаются (по середине слова):
    у чистых разрезов — все, у жёстких — до середины нахлёста."""

    start: float
    end: float
    keep_from: float = float("-inf")
    keep_to: float = float("inf")

    @property
    def length(self) -> float:
        return self.end - self.start


def cache_dir() -> Path:
    """Веса GigaAM — в папке моделей приложения, а не в общем ~/.cache:
    удалить приложение — значит удалить и их; работает без сети после первой
    загрузки."""
    from meet import paths

    return paths.models_dir() / "gigaam"


def model_files(name: str) -> list[Path]:
    """Файлы модели в кэше: веса и токенизатор (у e2e-моделей он свой)."""
    root = cache_dir()
    files = [root / f"{name}.ckpt"]
    if "e2e" in name:
        files.append(root / f"{name}_tokenizer.model")
    return files


def downloaded(name: str) -> bool:
    return all(p.is_file() for p in model_files(name))


def size_on_disk(name: str) -> int:
    total = 0
    for p in model_files(name):
        try:
            total += p.stat().st_size
        except OSError:
            continue
    return total


def remove(name: str) -> int:
    """Удалить файлы модели. → сколько файлов удалено."""
    removed = 0
    for p in model_files(name):
        try:
            p.unlink()
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def installed() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("gigaam") is not None
    except (ImportError, ValueError):
        return False


# --- нарезка -----------------------------------------------------------------


def _frame_energy(audio, sr: int):
    """Громкость (RMS) по кадрам FRAME_S."""
    import numpy as np

    hop = max(1, int(FRAME_S * sr))
    n = len(audio) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = np.asarray(audio[: n * hop], dtype=np.float32).reshape(n, hop)
    return np.sqrt((frames ** 2).mean(axis=1))


def _pause_in(window, quiet: float) -> float | None:
    """Где резать в окне громкостей (номер кадра, дробный) или None.

    Пауза — не меньше PAUSE_S тихих кадров подряд: смычка взрывного
    согласного («п», «к») тоже тихая, но короче, и резать по ней — резать
    слово. Из пауз берётся последняя (куски длиннее — меньше вызовов и больше
    контекста), режем в её середине. Пауз нет — самый тихий кадр, если он
    тихий; иначе None (жёсткий разрез)."""
    import numpy as np

    if not len(window):
        return None
    need = max(1, int(round(PAUSE_S / FRAME_S)))
    is_quiet = window <= quiet
    best, run_start = None, None
    for i, q in enumerate(list(is_quiet) + [False]):
        if q and run_start is None:
            run_start = i
        elif not q and run_start is not None:
            if i - run_start >= need:
                best = (run_start + i) / 2
            run_start = None
    if best is not None:
        return best
    i = int(np.argmin(window))
    return i + 0.5 if bool(is_quiet[i]) else None


def _split_long(start: float, end: float, audio, sr: int) -> list[Chunk]:
    """Участок речи длиннее MAX_CHUNK_S → куски не длиннее него.

    Разрез — в паузе между MIN_CHUNK_S и MAX_CHUNK_S от начала куска
    (_pause_in). Если там нет и тихого кадра (сплошная речь или шум), разрез
    жёсткий на MAX_CHUNK_S, а следующий кусок начинается на HARD_OVERLAP_S
    раньше."""
    import numpy as np

    out: list[Chunk] = []
    cur, keep_from = start, float("-inf")
    region = audio[int(start * sr): int(end * sr)]
    energy = _frame_energy(region, sr)
    median = float(np.median(energy)) if len(energy) else 0.0
    quiet = QUIET_RATIO * median if median > 0 else float("inf")
    while end - cur > MAX_CHUNK_S:
        lo = int((cur + MIN_CHUNK_S - start) / FRAME_S)
        hi = int((cur + MAX_CHUNK_S - start) / FRAME_S)
        at = _pause_in(energy[lo:hi], quiet)
        cut = start + (lo + at) * FRAME_S if at is not None else None
        if cut is not None and cut - cur <= MAX_CHUNK_S:
            out.append(Chunk(cur, cut, keep_from))
            cur, keep_from = cut, float("-inf")
            continue
        hard = cur + MAX_CHUNK_S
        border = hard - HARD_OVERLAP_S / 2
        out.append(Chunk(cur, hard, keep_from, border))
        cur, keep_from = hard - HARD_OVERLAP_S, border
    out.append(Chunk(cur, end, keep_from))
    return out


def plan_chunks(regions: list[tuple[float, float]], audio, sr: int = SAMPLE_RATE) -> list[Chunk]:
    """Участки речи (секунды) → куски для распознавания.

    Соседние участки собираются в один кусок, пока он не длиннее MAX_CHUNK_S;
    участок длиннее — режется (_split_long). Ни один кусок не длиннее
    MAX_CHUNK_S."""
    chunks: list[Chunk] = []
    group: list[float] | None = None
    for start, end in sorted(regions):
        if end <= start:
            continue
        if group is not None and end - group[0] <= MAX_CHUNK_S:
            group[1] = max(group[1], end)
            continue
        if group is not None:
            chunks.append(Chunk(group[0], group[1]))
            group = None
        if end - start > MAX_CHUNK_S:
            chunks.extend(_split_long(start, end, audio, sr))
        else:
            group = [start, end]
    if group is not None:
        chunks.append(Chunk(group[0], group[1]))
    return chunks


def speech_regions(audio, sr: int = SAMPLE_RATE) -> list[tuple[float, float]]:
    """Участки речи — Silero VAD из faster-whisper с теми же настройками, что
    у `vad_filter=True` в распознавании Whisper."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    stamps = get_speech_timestamps(audio, VadOptions(), sampling_rate=sr)
    return [(s["start"] / sr, s["end"] / sr) for s in stamps]


# --- распознавание и разбор --------------------------------------------------


def words_of_chunk(chunk: Chunk, words) -> list:
    """Слова GigaAM куска (время от начала куска) → asr.Word во времени
    записи, только те, что принадлежат куску (дедупликация нахлёста).

    Текст слова — с ведущим пробелом, как у Whisper: "".join(слов).strip() —
    текст реплики (на этом держатся words.json и правка спикеров)."""
    from meet.asr import Word

    out = []
    for w in words or []:
        text = str(getattr(w, "text", "") or "").strip()
        if not text:
            continue
        start = chunk.start + float(w.start)
        end = max(start, chunk.start + float(w.end))
        middle = (start + end) / 2
        if not (chunk.keep_from <= middle < chunk.keep_to):
            continue
        out.append(Word(round(start, 3), round(end, 3), " " + text))
    return out


def to_segments(words: list) -> list:
    """Слова всей дорожки → реплики: новая после конца предложения или паузы
    длиннее SEGMENT_GAP_S (у Whisper реплики примерно такие же)."""
    from meet.asr import Segment

    segments = []
    run: list = []

    def flush() -> None:
        if run:
            text = "".join(w.text for w in run).strip()
            segments.append(Segment(run[0].start, run[-1].end, text, words=list(run)))
            run.clear()

    for w in words:
        if run and w.start - run[-1].end > SEGMENT_GAP_S:
            flush()
        run.append(w)
        if w.text.rstrip().endswith(_SENTENCE_END):
            flush()
    flush()
    return segments


def _read_wav(path: Path):
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return data, sr


def _write_wav(path: Path, samples, sr: int) -> None:
    import wave

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(samples.tobytes())


def load(name: str = MODEL_NAME, device: str = "cpu"):
    """Модель GigaAM; веса скачиваются при первом вызове в cache_dir()."""
    import gigaam

    root = cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    return gigaam.load_model(name, device=device, download_root=str(root))


def transcribe(path: Path, *, model_name: str = MODEL_NAME, device: str = "cpu",
               regions=None, model=None, on_chunk=None) -> list:
    """Распознать mono 16 кГц wav (выход to_wav16k) → list[asr.Segment].

    `regions` и `model` подменяются в тестах; `on_chunk(done, total)` — ход."""
    import tempfile

    import numpy as np

    samples, sr = _read_wav(path)
    audio = samples.astype(np.float32) / 32768.0
    if regions is None:
        regions = speech_regions(audio, sr)
    chunks = plan_chunks(regions, audio, sr)
    own = model is None
    if own:
        model = load(model_name, device)
        print(f"Распознавание GigaAM ({model_name}, {device}), кусков: {len(chunks)}...")
    words: list = []
    try:
        with tempfile.TemporaryDirectory(prefix="meet-gigaam-") as td:
            for i, chunk in enumerate(chunks):
                piece = samples[int(chunk.start * sr): int(chunk.end * sr)]
                if len(piece) < int(0.1 * sr):
                    continue
                wav = Path(td) / f"c{i:05d}.wav"
                _write_wav(wav, piece, sr)
                result = model.transcribe(str(wav), word_timestamps=True)
                words.extend(words_of_chunk(chunk, getattr(result, "words", None)))
                if on_chunk:
                    on_chunk(i + 1, len(chunks))
    finally:
        if own:
            del model
            if device == "cuda":
                try:
                    import torch

                    torch.cuda.empty_cache()
                except Exception:
                    pass
    return to_segments(words)
