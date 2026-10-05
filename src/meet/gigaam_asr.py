"""Распознавание GigaAM (salute-developers/GigaAM, MIT) — русский движок для CPU.

Почему он: на 6-минутном фрагменте встречи (i9-13900H, 14 потоков) GigaAM
`v3_e2e_rnnt` распознаёт за ~27 с против ~4,7 мин распознавания у
faster-whisper medium, с пунктуацией, заглавными и пословными таймкодами, и
ближе к эталону CUDA.
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

Веса качаем сами (а не загрузчиком GigaAM): во временный файл, с проверкой
контрольной суммы и атомарным переименованием — оборванная загрузка не
оставляет «скачанную» битую модель. Токенизатор sentencepiece читается из
байтов (`model_proto`), а не по пути: sentencepiece на Windows не открывает
путь с кириллицей (папка данных у пользователя «Кузьма»).

Модуль лёгкий на импорт (без numpy и torch на верхнем уровне): его константы
читают настройки и каталог моделей в резиденте.
"""

import errno
import hashlib
import json
import os
import time
from contextlib import contextmanager
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
# нахлёста делятся по середине нахлёста (по началу слова).
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

    `keep_from`/`keep_to` — какие слова куска остаются (по началу слова): у
    чистых разрезов — все, у жёстких — до середины нахлёста. По началу, а не
    по середине: слово на разрезе каждый кусок слышит по-своему (первый —
    обрезанным, второй — хвостом), а начало у обоих общее, если слово целиком
    в нахлёсте, и раньше границы у первого, если оно её пересекает."""

    start: float
    end: float
    keep_from: float = float("-inf")
    keep_to: float = float("inf")

    @property
    def length(self) -> float:
        return self.end - self.start


# Откуда GigaAM (зафиксированный коммит) берёт веса.
URL = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM"
# Файлы моделей: имя, размер, алгоритм и контрольная сумма. Веса — MD5 из
# самого GigaAM (`_MODEL_HASHES`, он сверяет их и при загрузке модели);
# токенизаторы GigaAM не проверяет — их SHA-256 записаны здесь по загрузке
# 02.10.2026.
FILES = {
    "v3_e2e_rnnt": (
        ("v3_e2e_rnnt.ckpt", 448929252, "md5", "2730de7545ac43ad256485a462b0a27a"),
        ("v3_e2e_rnnt_tokenizer.model", 255336, "sha256",
         "828c12c991019eef952a960661f25a92d6ad279591e2ea466b4aeddf1d20a18a"),
    ),
    "v3_e2e_ctc": (
        ("v3_e2e_ctc.ckpt", 442404646, "md5", "367074d6498f426d960b25f49531cf68"),
        ("v3_e2e_ctc_tokenizer.model", 240941, "sha256",
         "0b9a1960898fbfdf5424ab852ea17445eb3da960fba23e977ff100eb0054fbc8"),
    ),
}
_PART = ".part"
_VERIFIED = ".verified.json"
# Сеть: подключение — не дольше CONNECT_TIMEOUT_S, ни байта за
# READ_TIMEOUT_S — обрыв; меньше мегабайта за STALL_S — тоже (сервер или
# прокси «капает» по байту): очередь задач последовательная, и зависшая
# загрузка держала бы все следующие встречи.
CONNECT_TIMEOUT_S = 20
READ_TIMEOUT_S = 60
STALL_S = 120
_PROGRESS_BYTES = 1 << 20
# Замок модели занят другим процессом — проверять снова через столько секунд.
LOCK_POLL_S = 0.5


class Unavailable(RuntimeError):
    """GigaAM не скачать или не загрузить (нет сети, сервер недоступен, файл
    битый). Текст — для человека; расшифровка в этом случае идёт Whisper.
    `network` — причина в сети (а не в файле или пакете)."""

    def __init__(self, message: str, network: bool = False) -> None:
        super().__init__(message)
        self.network = network


def _open(url: str):
    """urlopen с таймаутом подключения; после подключения — таймаут чтения."""
    import urllib.request

    response = urllib.request.urlopen(url, timeout=CONNECT_TIMEOUT_S)
    try:
        response.fp.raw._sock.settimeout(READ_TIMEOUT_S)
    except AttributeError:
        pass  # другой транспорт — остаётся таймаут подключения
    return response


def cache_dir() -> Path:
    """Веса GigaAM — в папке моделей приложения, а не в общем ~/.cache:
    удалить приложение — значит удалить и их; работает без сети после первой
    загрузки."""
    from meet import paths

    return paths.models_dir() / "gigaam"


def model_files(name: str) -> list[Path]:
    """Файлы модели в кэше: веса и токенизатор (у e2e-моделей он свой)."""
    root = cache_dir()
    if name in FILES:
        return [root / f for f, _, _, _ in FILES[name]]
    files = [root / f"{name}.ckpt"]
    if "e2e" in name:
        files.append(root / f"{name}_tokenizer.model")
    return files


def _all_files(name: str) -> list[Path]:
    """Файлы модели с недокачанными (.part) и отметкой проверки."""
    out = []
    for p in model_files(name):
        out += [p, p.with_name(p.name + _PART)]
    out.append(cache_dir() / f"{name}{_VERIFIED}")
    return out


def _expected_size(path: Path) -> int | None:
    for files in FILES.values():
        for fname, size, _, _ in files:
            if fname == path.name:
                return size
    return None


def downloaded(name: str) -> bool:
    """Модель скачана целиком: все файлы на месте и нужного размера (дёшево;
    контрольные суммы сверяются перед загрузкой модели, см. ensure)."""
    for p in model_files(name):
        try:
            size = p.stat().st_size
        except OSError:
            return False
        expected = _expected_size(p)
        if expected is not None and size != expected:
            return False
    return True


def present(name: str) -> bool:
    """Есть хоть какой-то файл модели — в том числе недокачанный или битый:
    такую модель должно быть можно удалить из окна."""
    return any(p.exists() for p in _all_files(name))


def size_on_disk(name: str) -> int:
    total = 0
    for p in _all_files(name):
        try:
            total += p.stat().st_size
        except OSError:
            continue
    return total


class Busy(OSError):
    """Файлы модели сейчас качает или сверяет другой процесс."""


def remove(name: str) -> int:
    """Удалить файлы модели (и недокачанные). → сколько файлов удалено.
    Модель качает или сверяет другой процесс (загрузка, расшифровка,
    ассистент) — Busy: удалить из-под него нельзя."""
    removed = 0
    with _files_lock(name, wait=False) as busy:
        if busy:
            raise Busy("модель сейчас скачивается или проверяется — удалить её можно после")
        for p in _all_files(name):
            try:
                p.unlink()
                if not p.name.endswith(_VERIFIED):
                    removed += 1
            except FileNotFoundError:
                continue
    return removed


def _digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _stamp(path: Path) -> list:
    st = path.stat()
    return [st.st_size, st.st_mtime_ns]


def _verified(name: str) -> dict:
    try:
        return json.loads((cache_dir() / f"{name}{_VERIFIED}").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _file_ok(path: Path, size: int, algo: str, digest: str, seen: dict) -> bool:
    """Файл цел: размер и контрольная сумма. Однажды проверенный и с тех пор
    не менявшийся (размер, время изменения) повторно не хешируется."""
    try:
        if path.stat().st_size != size:
            return False
        if seen.get(path.name) == _stamp(path):
            return True
        return _digest(path, algo) == digest
    except OSError:
        return False


def _fetch(url: str, target: Path, size: int, algo: str, digest: str, on_line=None,
           opener=None) -> None:
    """Скачать во временный файл, сверить и только тогда переименовать.
    Прокси — из переменных среды (urllib), их задаёт задача (meet.netproxy).
    Таймауты — CONNECT_TIMEOUT_S / READ_TIMEOUT_S / STALL_S."""
    part = target.with_name(target.name + _PART)
    part.unlink(missing_ok=True)
    open_url = opener or _open
    h = hashlib.new(algo)
    done, step = 0, max(size // 10, 1)
    try:
        with open_url(url) as src, open(part, "wb") as out:
            read = getattr(src, "read1", None) or src.read
            mark, mark_at = 0, time.monotonic()
            while block := read(1 << 20):
                out.write(block)
                h.update(block)
                if on_line and (done + len(block)) // step > done // step:
                    on_line(f"{target.name}: {min(100, (done + len(block)) * 100 // size)}%")
                done += len(block)
                now = time.monotonic()
                if done - mark >= _PROGRESS_BYTES:
                    mark, mark_at = done, now
                elif now - mark_at > STALL_S:
                    raise TimeoutError(f"загрузка почти остановилась ({done} байт)")
    except Exception as e:
        part.unlink(missing_ok=True)
        raise Unavailable(f"GigaAM: не удалось скачать {target.name} ({type(e).__name__}: {e})",
                          network=isinstance(e, OSError)) from e
    if done != size or h.hexdigest() != digest:
        part.unlink(missing_ok=True)
        raise Unavailable(f"GigaAM: {target.name} скачался повреждённым (контрольная сумма не совпала)")
    os.replace(part, target)


def _lock_path(name: str) -> Path:
    """Файл замка модели — во временной папке системы (её делят резидент,
    задачи и ассистент), а не рядом с весами: там он остался бы после
    удаления модели. В имени — папка моделей: у другой папки данных свой замок."""
    from meet import tempdirs

    key = os.path.normcase(str(cache_dir().resolve() / name))
    digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    return tempdirs.system_temp() / "meet-model-locks" / f"{digest}.lock"


# errno «замок держит другой»: EACCES и EDEADLK (EDEADLOCK) — у msvcrt.locking,
# EWOULDBLOCK (= EAGAIN) — у flock. Прочие ошибки — ФС замков не умеет.
_LOCK_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}

if os.name == "nt":
    import msvcrt

    def _try_lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _files_lock(name: str, on_line=None, wait: bool = True):
    """Замок файлов модели между процессами: загрузка из окна, расшифровка и
    ассистент могут прийти за одной моделью одновременно, а `.part` у них
    один. Второй ждёт, пока первый докачает, и находит файлы готовыми.

    Отдаёт «занят»: True — только при `wait=False`, когда замок держит
    другой (тогда замка у нас нет). Замок не открылся или ФС его не
    поддерживает — работаем без него, как раньше (False).

    Срока ожидания нет: держатель качает с таймаутами обрыва и «капания»
    (`_fetch`), отмена задачи его убивает, а умерший процесс замок отпускает."""
    path = _lock_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")
    except OSError:
        yield False
        return
    locked = busy = False
    try:
        told = False
        while True:
            try:
                _try_lock(handle)
                locked = True
                break
            except OSError as e:
                if e.errno not in _LOCK_BUSY:
                    break  # не «занят», а «не умею» (ENOLCK и т. п.) — без замка
                if not wait:
                    busy = True
                    break
                if on_line and not told:
                    on_line(f"GigaAM {name}: модель уже скачивается — жду окончания")
                    told = True
                time.sleep(LOCK_POLL_S)
        try:
            yield busy
        finally:
            if locked:
                try:
                    _unlock(handle)
                except OSError:
                    pass
    finally:
        handle.close()


def ensure(name: str, on_line=None, opener=None) -> None:
    """Модель целиком на диске: недостающие и битые файлы удаляются и
    скачиваются заново (один раз). Не вышло — Unavailable с понятным текстом.
    Под замком модели (`_files_lock`): одну модель качает один процесс."""
    if name not in FILES:
        raise Unavailable(f"GigaAM: неизвестная модель {name}")
    root = cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    with _files_lock(name, on_line):
        _ensure_files(name, root, on_line, opener)


def _ensure_files(name: str, root: Path, on_line, opener) -> None:
    seen = _verified(name)
    stamps = {}
    for fname, size, algo, digest in FILES[name]:
        path = root / fname
        if not _file_ok(path, size, algo, digest, seen):
            if path.exists():
                if on_line:
                    on_line(f"{fname}: файл повреждён или недокачан — скачиваю заново")
                path.unlink()
            _fetch(f"{URL}/{fname}", path, size, algo, digest, on_line, opener)
        stamps[fname] = _stamp(path)
    try:
        (root / f"{name}{_VERIFIED}").write_text(json.dumps(stamps), encoding="utf-8")
    except OSError:
        pass  # отметка — только ускорение


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


def _quiet_level(energy):
    import numpy as np

    median = float(np.median(energy)) if len(energy) else 0.0
    return QUIET_RATIO * median if median > 0 else float("inf")


def _latest_run(window, quiet: float) -> float | None:
    """Середина последней паузы (не короче PAUSE_S тихих кадров) или None."""
    need = max(1, int(round(PAUSE_S / FRAME_S)))
    best, run_start = None, None
    for i, q in enumerate(list(window <= quiet) + [False]):
        if q and run_start is None:
            run_start = i
        elif not q and run_start is not None:
            if i - run_start >= need:
                best = (run_start + i) / 2
            run_start = None
    return best


def find_pause(audio, sr: int, lo_s: float, hi_s: float) -> float | None:
    """Последняя пауза между словами между lo_s и hi_s секундами (от начала
    `audio`) или None — только настоящая пауза, не просто тихий кадр."""
    hi_s = min(hi_s, len(audio) / sr)
    lo_s = min(max(0.0, lo_s), hi_s)
    energy = _frame_energy(audio[: int(hi_s * sr)], sr)
    lo, hi = int(lo_s / FRAME_S), int(hi_s / FRAME_S)
    if hi <= lo or not len(energy):
        return None
    at = _latest_run(energy[lo:hi], _quiet_level(energy))
    return None if at is None else min(hi_s, (lo + at) * FRAME_S)


def quiet_cut(audio, sr: int, lo_s: float, hi_s: float) -> float:
    """Где резать звук между lo_s и hi_s секундами (от начала `audio`): в
    последней паузе между словами (find_pause), а нет паузы — в самом тихом
    кадре. Для окон живого режима: окно кончается там, где человек замолчал,
    а не посреди слова. → секунды от начала."""
    import numpy as np

    at = find_pause(audio, sr, lo_s, hi_s)
    if at is not None:
        return at
    hi_s = min(hi_s, len(audio) / sr)
    lo_s = min(max(0.0, lo_s), hi_s)
    energy = _frame_energy(audio[: int(hi_s * sr)], sr)
    lo, hi = int(lo_s / FRAME_S), int(hi_s / FRAME_S)
    if hi <= lo or not len(energy):
        return hi_s
    return min(hi_s, max(lo_s, (lo + int(np.argmin(energy[lo:hi])) + 0.5) * FRAME_S))


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
        if not (chunk.keep_from <= start < chunk.keep_to):
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


def _proto_tokenizer() -> None:
    """Токенизатор GigaAM — из байтов файла, а не по пути.

    sentencepiece на Windows не открывает путь с не-ASCII символами («Кузьма»
    в папке пользователя), а GigaAM отдаёт ему путь из `download_root`. Самая
    узкая точка — класс SentencePieceProcessor в модуле декодирования GigaAM:
    подменяем его наследником, у которого `Load(model_file)` читает файл
    сам и передаёт `model_proto`. Повторный вызов ничего не меняет."""
    import gigaam.decoding as decoding

    base = getattr(decoding, "SentencePieceProcessor", None)
    if base is None or getattr(base, "_meet_from_bytes", False):
        return

    class FromBytes(base):
        _meet_from_bytes = True

        def Load(self, model_file=None, model_proto=None):  # noqa: N802 — имя из sentencepiece
            if model_file is not None and model_proto is None:
                model_proto, model_file = Path(model_file).read_bytes(), None
            return super().Load(model_proto=model_proto)

        load = Load

    decoding.SentencePieceProcessor = FromBytes


def in_process_wav() -> bool:
    """Свои wav (16 кГц, моно, PCM16) GigaAM читает в процессе, без ffmpeg.

    `model.transcribe(path)` грузит файл через `gigaam.load_audio` — это
    новый процесс ffmpeg на каждый вызов: для окна живого режима (~5 с) это
    ~0,06 с из ~0,5 с (замер M10). Редкие многосекундные задержки окон на CPU
    это не убрало — их убрало ограничение потоков torch (`live_asr`).
    Подменяем имя `load_audio` в модуле модели GigaAM: файл
    нужного формата читается модулем `wave`, любой другой — прежним путём.
    Точки подмены нет — ничего не меняем (ffmpeg, как раньше). → True —
    подмена стоит."""
    try:
        import gigaam.model as model_mod
    except Exception:
        return False
    base = getattr(model_mod, "load_audio", None)
    if base is None:
        return False
    if getattr(base, "_meet_wav", False):
        return True

    def load_audio(audio_path, sample_rate: int = SAMPLE_RATE):
        import wave

        import numpy as np
        import torch

        try:
            with wave.open(str(audio_path), "rb") as wf:
                if (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (sample_rate, 1, 2):
                    pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                    return torch.from_numpy(pcm.astype(np.float32) / 32768.0)
        except (OSError, EOFError, wave.Error):
            pass
        return base(audio_path, sample_rate)

    load_audio._meet_wav = True
    model_mod.load_audio = load_audio
    return True


def load(name: str = MODEL_NAME, device: str = "cpu", on_line=None):
    """Модель GigaAM; веса при необходимости скачиваются (ensure) в cache_dir().
    Любой сбой — Unavailable: тогда распознаёт Whisper. Модель не загрузилась
    — отметка проверки файлов снимается: в следующий раз они сверятся
    заново и битый скачается снова, а не будет «проверенным» навсегда."""
    try:
        ensure(name, on_line)
    except Unavailable:
        raise
    except Exception as e:
        raise Unavailable(f"GigaAM: модель не подготовлена ({type(e).__name__}: {e})") from e
    try:
        import gigaam

        _proto_tokenizer()
        return gigaam.load_model(name, device=device, download_root=str(cache_dir()))
    except Exception as e:
        (cache_dir() / f"{name}{_VERIFIED}").unlink(missing_ok=True)
        raise Unavailable(f"GigaAM не загрузилась ({type(e).__name__}: {e})") from e


def transcribe(path: Path, *, model_name: str = MODEL_NAME, device: str = "cpu",
               regions=None, model=None, on_chunk=None) -> list:
    """Распознать mono 16 кГц wav (выход to_wav16k) → list[asr.Segment].

    `regions` и `model` подменяются в тестах; `on_chunk(done, total)` — ход
    в секундах звука кусков (длинный кусок весит больше короткого): первое
    `on_chunk(0, total)` — куски нарезаны, распознавание началось.

    Куски пишутся во временную папку с pid в имени (meet.tempdirs: процесс
    убили — её удалит резидент), и каждый удаляется сразу после распознавания:
    звук встречи на диске не копится."""
    import numpy as np

    from meet import tempdirs

    samples, sr = _read_wav(path)
    audio = samples.astype(np.float32) / 32768.0
    if regions is None:
        regions = speech_regions(audio, sr)
    chunks = plan_chunks(regions, audio, sr)
    own = model is None
    if own:
        # Строки загрузки и «файл повреждён — скачиваю заново» — в журнал задачи.
        model = load(model_name, device, on_line=print)
        print(f"Распознавание GigaAM ({model_name}, {device}), кусков: {len(chunks)}...")
    words: list = []
    total_s = sum(c.length for c in chunks)
    done_s = 0.0
    if on_chunk:
        on_chunk(0.0, total_s)
    try:
        with tempdirs.temp_dir("gigaam-") as td:
            for i, chunk in enumerate(chunks):
                piece = samples[int(chunk.start * sr): int(chunk.end * sr)]
                done_s += chunk.length
                if len(piece) < int(0.1 * sr):
                    continue
                wav = Path(td) / f"c{i:05d}.wav"
                _write_wav(wav, piece, sr)
                try:
                    result = model.transcribe(str(wav), word_timestamps=True)
                finally:
                    wav.unlink(missing_ok=True)
                words.extend(words_of_chunk(chunk, getattr(result, "words", None)))
                if on_chunk:
                    on_chunk(done_s, total_s)
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
