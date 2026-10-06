"""Библиотека записей: что лежит на диске и в каком оно состоянии.

Источник истины — файлы, а не индекс: папка записи самодостаточна, и её можно
скопировать с другой машины (сценарий «записал на ноутбуке — расшифровал на
десктопе» из README). Поэтому здесь нет базы, только чтение каталога; кэш, если
понадобится, всегда можно восстановить перечитыванием.

Структурный транскрипт (`transcript.json`) — источник для редактора; Markdown
остаётся человеческим артефактом и форматом экспорта.
"""

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TRANSCRIPT_JSON = "transcript.json"
# Слова сегментов с таймкодами — отдельно от transcript.json: их много, а
# транскрипт читают список записей, поиск и окно. Выравнивание — по номеру
# сегмента, у каждого слова сегмента его время и текст для сверки.
WORDS_JSON = "words.json"
META_JSON = "meta.json"
# Фаза расшифровки в transcript.json (Р4, 0.3.3): `"text"` — распознанный текст
# до диаризации, без спикеров (микрофон — владелец, собеседники — без подписи).
# Окончательная расшифровка поля не несёт вовсе. Анализ, итоги, база знаний,
# правка спикеров и «Улучшить расшифровку» по такому транскрипту не идут.
PHASE = "phase"
TEXT_PHASE = "text"
TEXT_ONLY = "Спикеры ещё не определены — дождитесь конца расшифровки или расшифруйте запись заново"
FOLDER_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})(?:_.+)?$")
# Форматы дорожек в порядке предпочтения — те же, что понимает transcribe.
TRACK_EXTS = (".opus", ".wav", ".ogg", ".flac", ".mp3", ".m4a",
              ".mp4", ".webm", ".mkv")
# Дорожки папки записи: запись пишет sys+mic, импорт кладёт один source.
TRACK_STEMS = ("sys", "mic", "source")
# Что принимаем на импорт: всё это декодирует ffmpeg.
IMPORT_EXTS = (".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".opus", ".webm", ".mkv",
               ".flac")
SOURCES = ("record", "auto", "import", "live", "merge")


@dataclass
class Recording:
    """Одна папка записи. `id` — имя папки: оно же дата и время встречи."""

    id: str
    path: Path
    started_at: str | None = None
    tracks: dict = field(default_factory=dict)
    duration_s: float | None = None
    has_transcript: bool = False
    has_voices: bool = False
    title: str | None = None
    # Откуда название (title_source): "auto" — по дате или из транскрипта,
    # "user" — задал человек, "ai" — предложила модель, "site" — заголовок
    # окна звонка в браузере.
    title_source: str = "auto"
    # Какая модель придумала название `ai` ({"provider", "model"}, meta.json
    # `title_llm`, U3). Название не от модели или модель неизвестна — None.
    title_llm: dict | None = None
    source: str = "record"
    # mtime транскрипта: окно сравнивает его с концом упавшей перерасшифровки.
    transcript_at: float | None = None
    # Пометка транскрипта о диаризации: "skipped_no_token" — расшифровано без
    # разделения на спикеров (не было токена Hugging Face). None — как обычно.
    diarization: str | None = None
    # Пометка транскрипта о распознавании (`asr_note`): "not_russian" — запись
    # не на русском, вместо GigaAM распознавал Whisper. None — как выбрано.
    asr_note: str | None = None
    # Выгрузка в базу знаний (meta.json): {"path", "at", "files"} и, если
    # последняя не удалась, "error". Не выгружалась — None.
    kb_export: dict | None = None
    # Объединённая встреча (meet.merge): {"parts", "state", "deleted",
    # "kb_left"} — из скольких записей, удалены ли исходные и какие их папки в
    # базе знаний остались нетронутыми. Не объединённая — None.
    merge: dict | None = None
    # «Переразделить на спикеров» посчитано и ждёт решения (rediarize.json).
    rediarize_ready: bool = False
    # Категория встречи (meet.categories): {"id", "source": "ai"|"user"};
    # id None — человек выбрал «Без категории». Не задана — None.
    category: dict | None = None
    # macOS: звук собеседников не записан (`system_audio` в meta.json):
    # "missing" — всю запись не было разрешения «Запись экрана», "partial" —
    # его дали посреди встречи. None — записан как обычно.
    system_audio: str | None = None
    # Почему (`system_audio_reason`): "permission" — не было разрешения,
    # "helper" — нет помощника, "unsupported" — macOS старше 13, "failed" —
    # помощник не запустился. Старая пометка без причины — None.
    system_audio_reason: str | None = None
    # Транскрипт — только текст, спикеры ещё не определены (`phase: "text"`):
    # идёт диаризация или расшифровку прервали между фазами. None — окончательный.
    transcript_phase: str | None = None
    # Микрофон звонка по голосам (`mic_split` транскрипта, meet.mic_split):
    # {"status", "room_speakers", "dropped"} — подсказка в карточке. Нет — None.
    mic_split: dict | None = None

    def to_raw(self) -> dict:
        return {
            "id": self.id,
            "path": str(self.path),
            "started_at": self.started_at,
            "tracks": {name: str(p) for name, p in self.tracks.items()},
            "duration_s": self.duration_s,
            "has_transcript": self.has_transcript,
            "has_voices": self.has_voices,
            "title": self.title,
            "title_source": self.title_source,
            "title_llm": self.title_llm,
            "source": self.source,
            "transcript_at": self.transcript_at,
            "diarization": self.diarization,
            "asr_note": self.asr_note,
            "kb_export": self.kb_export,
            "merge": self.merge,
            "rediarize_ready": self.rediarize_ready,
            "category": self.category,
            "system_audio": self.system_audio,
            "system_audio_reason": self.system_audio_reason,
            "transcript_phase": self.transcript_phase,
            "mic_split": self.mic_split,
        }


# Результат «Переразделить на спикеров», ждущий применения (meet.rediarize).
REDIARIZE_PREVIEW = "rediarize.json"
# Голоса микрофона окончательной расшифровки звонка (meet.mic_split): кластеры
# окон и убранные дубли соседа, эхо и утечки владельца — для панели «Спикеры».
MIC_VOICES = "mic_voices.json"


def find_track(folder: Path, stem: str) -> Path | None:
    for ext in TRACK_EXTS:
        candidate = folder / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _started_at(name: str) -> str | None:
    """Время начала из имени папки (recorder именует их YYYY-MM-DD_HH-MM)."""
    m = FOLDER_RE.match(name)
    if not m:
        return None
    y, mo, d, h, mi = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:00"


def _duration_from_events(folder: Path) -> float | None:
    """Длительность из `events.jsonl` — её пишет сама запись при остановке.

    Файла нет (старая запись, убитый процесс) — не выдумываем: пусть будет None,
    а не число, которому нельзя верить."""
    path = folder / "events.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        # обрезанный хвост (meet.tail) пишется позже остановки — он и главный
        if event.get("kind") in ("record.stopped", "record.trimmed"):
            value = event.get("duration_s")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def transcript_path(folder: Path) -> Path:
    return folder / TRANSCRIPT_JSON


def words_path(folder: Path) -> Path:
    return folder / WORDS_JSON


def _words_ok(segment: dict, entry) -> bool:
    """Запись words.json ещё описывает сегмент: то же время и тот же текст."""
    if not isinstance(entry, list) or len(entry) != 3 or not isinstance(entry[2], list) or not entry[2]:
        return False
    try:
        same_time = float(entry[0]) == float(segment.get("start")) and float(entry[1]) == float(segment.get("end"))
    except (TypeError, ValueError):
        return False
    return same_time and words_match({**segment, "words": entry[2]})


def read_transcript_full(folder: Path) -> dict | None:
    """Транскрипт со словами сегментов (из words.json; у расшифровок, где слова
    ещё лежат в transcript.json, — оттуда). Слова сегмента, у которого с тех
    пор поменяли время или текст, не подставляются."""
    data = read_transcript(folder)
    segments = (data or {}).get("segments")
    if not isinstance(segments, list):
        return data
    try:
        raw = json.loads(words_path(folder).read_text(encoding="utf-8"))
        items = raw.get("items") if isinstance(raw, dict) else None
    except (OSError, ValueError):
        items = None
    if not isinstance(items, list):
        return data
    # Запасной поиск — по времени: редактор вставил или удалил сегмент, и
    # номера остальных сдвинулись (words.json он не переписывает).
    by_time: dict[tuple, list] = {}
    for entry in items:
        if isinstance(entry, list) and len(entry) == 3:
            try:
                by_time.setdefault((float(entry[0]), float(entry[1])), []).append(entry)
            except (TypeError, ValueError):
                continue
    out = []
    for i, s in enumerate(segments):
        if isinstance(s, dict) and "words" not in s:
            entry = items[i] if i < len(items) and _words_ok(s, items[i]) else None
            if entry is None:
                try:
                    near = by_time.get((float(s.get("start")), float(s.get("end"))), [])
                except (TypeError, ValueError):
                    near = []
                entry = next((e for e in near if _words_ok(s, e)), None)
            if entry is not None:
                s = {**s, "words": entry[2]}
        out.append(s)
    return {**data, "segments": out}


def read_transcript(folder: Path) -> dict | None:
    """Структурный транскрипт папки (без слов — они в words.json, см.
    read_transcript_full); нет или битый — None."""
    try:
        data = json.loads(transcript_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_text_phase(data) -> bool:
    """Транскрипт — текст до спикеров (`phase: "text"`), а не окончательный."""
    return isinstance(data, dict) and data.get(PHASE) == TEXT_PHASE


def final_transcript(folder: Path) -> dict | None:
    """Окончательный транскрипт папки: текст до спикеров — как его нет. Для
    всего, что опирается на спикеров или отдаёт расшифровку наружу."""
    data = read_transcript(folder)
    return None if is_text_phase(data) else data


def turn_mark(segment: dict) -> tuple[bool, bool]:
    """Чем подряд идущие сегменты одного спикера не склеиваются в одну реплику
    (окно, поиск, правка спикеров — одинаково): микрофон и звонок — разные
    реплики (человек в комнате — та же подпись, что у его кластера в звонке),
    голос микрофона под вопросом — отдельная."""
    mic = isinstance(segment, dict) and segment.get("track") == "mic"
    return mic, mic and bool(segment.get("uncertain"))


def display_names(segments: list[dict]) -> dict[str, str]:
    """Сырая метка SPEAKER_XX → «Спикер N» в порядке первого появления.

    Нумерация та же, что в output.speaker_names; прочие метки (имена, «Вы») не трогаем."""
    names: dict[str, str] = {}
    for seg in segments:
        label = seg.get("speaker")
        if isinstance(label, str) and label.startswith("SPEAKER_") and label not in names:
            names[label] = f"Спикер {len(names) + 1}"
    return names


def with_display_names(data: dict | None) -> dict | None:
    """Копия транскрипта, где сырые метки заменены отображаемыми именами."""
    if not data or not isinstance(data.get("segments"), list):
        return data
    names = display_names(data["segments"])
    if not names:
        return data
    segments = [{**seg, "speaker": names.get(seg.get("speaker"), seg.get("speaker"))}
                for seg in data["segments"]]
    return {**data, "segments": segments}


def _atomic_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_transcript(folder: Path, data: dict, words: str = "keep") -> Path:
    """Атомарная запись: редактор читает файл в любой момент.

    Слова сегментов (`words`, если они есть в данных) уходят в words.json —
    целиком, по номерам сегментов; transcript.json остаётся без них. Данные
    без слов (окно, переименования) words.json не трогают: запись, которая
    уже не сходится с сегментом, при чтении просто не подставляется.
    `words="replace"` — данные несут все слова расшифровки (пайплайн, правка
    спикеров по read_transcript_full): нет ни одного — words.json удаляется,
    чтобы от прежней расшифровки не осталось чужих слов."""
    path = transcript_path(folder)
    segments = data.get("segments")
    has_words = isinstance(segments, list) and any(isinstance(s, dict) and "words" in s for s in segments)
    if not has_words and words == "replace":
        words_path(folder).unlink(missing_ok=True)
    if has_words:
        items = [[s.get("start"), s.get("end"), s["words"]]
                 if isinstance(s, dict) and isinstance(s.get("words"), list) and s["words"] else None
                 for s in segments]
        _atomic_text(words_path(folder), json.dumps({"version": 1, "items": items}, ensure_ascii=False,
                                                    separators=(",", ":")))
        data = {**data, "segments": [{k: v for k, v in s.items() if k != "words"} if isinstance(s, dict) else s
                                     for s in segments]}
    _atomic_text(path, json.dumps(data, ensure_ascii=False, indent=1))
    return path


# Заголовок транскрипта для списка записей: (path, mtime_ns, size) → (есть ли
# транскрипт, название, пометка диаризации, пометка распознавания, фаза,
# итог разделения микрофона).
# Список не разбирает каждый раз все транскрипты целиком.
_heads: dict[str, tuple] = {}
_heads_lock = threading.Lock()


def _mic_split_head(raw) -> dict | None:
    """Итог разделения микрофона (`mic_split` транскрипта, meet.mic_split) для
    карточки: статус (подсказка), сколько людей в комнате и убрано копий."""
    if not isinstance(raw, dict) or not isinstance(raw.get("status"), str):
        return None
    dropped = raw.get("dropped") if isinstance(raw.get("dropped"), dict) else {}
    rooms = raw.get("room_speakers")
    return {"status": raw["status"],
            "room_speakers": rooms if isinstance(rooms, int) and not isinstance(rooms, bool) else 0,
            "dropped": {k: v for k, v in dropped.items()
                        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)}}


def _transcript_head(folder: Path) -> tuple[bool, str | None, str | None, str | None, str | None, dict | None]:
    path = transcript_path(folder)
    try:
        st = path.stat()
    except OSError:
        return False, None, None, None, None, None
    key = (st.st_mtime_ns, st.st_size)
    with _heads_lock:
        hit = _heads.get(str(path))
    if hit is not None and hit[0] == key:
        return hit[1]
    transcript = read_transcript(folder)
    title = diarization = asr_note = phase = mic_split = None
    if isinstance(transcript, dict):
        raw_title = transcript.get("title")
        title = str(raw_title) if raw_title else None
        flag = transcript.get("diarization")
        diarization = str(flag) if isinstance(flag, str) and flag else None
        note = transcript.get("asr_note")
        asr_note = str(note) if isinstance(note, str) and note else None
        phase = TEXT_PHASE if is_text_phase(transcript) else None
        mic_split = _mic_split_head(transcript.get("mic_split"))
    head = (transcript is not None, title, diarization, asr_note, phase, mic_split)
    with _heads_lock:
        _heads[str(path)] = (key, head)
    return head


def transcript_phase(folder: Path) -> str | None:
    """Фаза транскрипта папки ("text" — текст до спикеров, None —
    окончательный или его нет) — из кэша заголовков: не разбирать весь
    transcript.json на каждый запрос окна."""
    return _transcript_head(Path(folder))[4]


def _forget_heads(root: Path, names: set[str]) -> None:
    """Заголовки записей этой библиотеки, папок которых больше нет (удалены,
    объединены, переименованы), — из кэша: он не должен расти без конца."""
    base = str(root)
    with _heads_lock:
        for key in [k for k in _heads if str(Path(k).parent.parent) == base
                    and Path(k).parent.name not in names]:
            del _heads[key]


def read_meta(folder: Path) -> dict:
    """Метаданные папки записи. Нет файла или он битый — пустой словарь: у
    записей, сделанных до v1, meta.json нет, и это норма."""
    try:
        data = json.loads((folder / META_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# meta.json пишут несколько потоков резидента (выгрузка в базу знаний в фоне,
# переименование из окна, пометка источника записи): чтение-правка-запись —
# под замком папки, иначе одна правка теряет другую.
_META_LOCKS: dict[str, threading.Lock] = {}
_META_LOCKS_GUARD = threading.Lock()
REPLACE_TRIES = 5


def _meta_lock(folder: Path) -> threading.Lock:
    try:
        key = os.path.normcase(str(Path(folder).resolve()))
    except OSError:
        key = os.path.normcase(str(folder))
    with _META_LOCKS_GUARD:
        return _META_LOCKS.setdefault(key, threading.Lock())


def _replace(tmp: Path, target: Path) -> None:
    """os.replace с повтором: на Windows файл, который как раз читает другой
    процесс (окно, задача), заменить нельзя — PermissionError на миг."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


META_FILE_LOCK_WAIT_S = 5.0


def _meta_lock_path(folder: Path) -> Path:
    import hashlib

    from meet import tempdirs

    try:
        key = os.path.normcase(str(Path(folder).resolve()))
    except OSError:
        key = os.path.normcase(str(folder))
    digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    # Общая временная папка, а не своя у задачи: замок делят процессы (meet.tempdirs).
    return tempdirs.system_temp() / "meet-meta-locks" / f"{digest}.lock"


@contextmanager
def _meta_file_lock(folder: Path):
    """Замок meta.json между процессами: задачи-подпроцессы (итоги, объединение)
    правят его одновременно с резидентом (переименование из окна), и замок
    потоков одного процесса их не разводит. Файл замка — во временной папке,
    а не в папке записи: там он мешал бы удалению и был бы виден агенту.
    Не взяли за META_FILE_LOCK_WAIT_S (или замок недоступен) — правим без
    него: потерять запись хуже, чем гонку."""
    path = _meta_lock_path(folder)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+b")
    except OSError:
        yield
        return
    locked = False
    try:
        deadline = time.monotonic() + META_FILE_LOCK_WAIT_S
        while True:
            try:
                _lock_file(handle)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
        yield
    finally:
        if locked:
            try:
                _unlock_file(handle)
            except OSError:
                pass
        handle.close()


if os.name == "nt":
    import msvcrt

    def _lock_file(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update_meta(folder: Path, change) -> dict:
    """Атомарно поправить meta.json: `change(текущее) -> новое` под замком папки
    (и потоков резидента, и других процессов — см. _meta_file_lock): чтение,
    правка и замена идут подряд, правка другого процесса между ними не
    теряется. Временный файл свой у каждой записи — параллельные записи не
    делят его."""
    folder = Path(folder)
    with _meta_lock(folder), _meta_file_lock(folder):
        data = change(read_meta(folder))
        tmp = folder / f"{META_JSON}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            _replace(tmp, folder / META_JSON)
        finally:
            tmp.unlink(missing_ok=True)
        return data


def write_meta(folder: Path, updates: dict) -> dict:
    """Дописать поля в meta.json атомарно (UI читает его в любой момент)."""
    return update_meta(folder, lambda data: {**data, **updates})


# --- удаление папок записей ------------------------------------------------------

# Сколько ждать, пока папку отпустят (плеер, агент во вкладке «Агент»).
REMOVE_WAIT_S = 2.0
REMOVE_POLL_S = 0.25
FOLDER_BUSY = ("Папку занимает другая программа (например, агент в терминале) — "
               "закройте её и повторите")
PROBE_STUCK = ("Папку {name} не удалось вернуть после проверки — она вернётся "
               "на место при следующем запуске приложения")
# Папка, отложенная к удалению: с точки — библиотека её не показывает.
DELETING_MARK = ".deleting-"
# Папка, отложенная на время проверки «можно ли удалить» (wait_removable):
# своя метка, чтобы восстановление после сбоя вернуло её, а не удалило.
PROBING_MARK = ".probing-"
# Обратное переименование: короткая помеха (антивирус, индексатор заглянул в
# папку) не должна оставлять запись спрятанной — пробуем несколько раз.
RENAME_BACK_TRIES = 20
RENAME_BACK_PAUSE_S = 0.1


class FolderBusy(RuntimeError):
    """Папку записи держит другая программа — удалять нельзя (текст — человеку)."""


def _aside(folder: Path, mark: str = DELETING_MARK) -> Path:
    import uuid

    return folder.with_name(f".{folder.name}{mark}{uuid.uuid4().hex[:8]}")


def _rename_back(aside: Path, folder: Path, *, rename=os.rename, sleep=time.sleep) -> bool:
    """Вернуть отложенную папку под прежнее имя, с повторами. False — так и
    не вышло (папка осталась отложенной)."""
    for attempt in range(RENAME_BACK_TRIES):
        if attempt:
            sleep(RENAME_BACK_PAUSE_S)
        try:
            rename(aside, folder)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            continue
    return False


def remove_folders(folders, wait_s: float = REMOVE_WAIT_S, *, sleep=time.sleep,
                   clock=time.monotonic, rename=os.rename) -> None:
    """Удалить папки записей — все или ни одной.

    На Windows папку, которая служит рабочей папкой процесса (агент во вкладке
    «Агент») или в которой открыт файл, удалить нельзя, а `rmtree` успевает
    снести всё, что не занято, и падает на остальном — запись остаётся
    наполовину. Поэтому сначала каждая папка переименовывается в скрытую
    (переименование занятой папки не проходит и ничего не трогает); занята —
    ждём до `wait_s` и, не дождавшись, возвращаем уже отложенные на место и
    бросаем FolderBusy. Удаляем, только когда отложены все."""
    import shutil

    deadline = clock() + wait_s
    moved: list[tuple[Path, Path]] = []
    try:
        for folder in (Path(f) for f in folders):
            aside: Path | None = _aside(folder)
            while True:
                try:
                    rename(folder, aside)
                    break
                except FileNotFoundError:
                    aside = None  # уже нет — удалять нечего
                    break
                except OSError:
                    if clock() >= deadline:
                        raise FolderBusy(FOLDER_BUSY) from None
                    sleep(REMOVE_POLL_S)
            if aside is not None:
                moved.append((folder, aside))
    except BaseException:
        # Удаление отменено: вернуть всё. Не вернулась — останется `.deleting-`,
        # и восстановление её удалит, поэтому с повторами.
        for folder, aside in reversed(moved):
            _rename_back(aside, folder, rename=rename, sleep=sleep)
        raise
    for folder, aside in moved:
        try:
            shutil.rmtree(aside)
        except OSError as e:
            # что осталось — под прежним именем
            _rename_back(aside, folder, rename=rename, sleep=sleep)
            raise RuntimeError(f"не удалось удалить запись {folder.name}: {e}") from e


def wait_removable(folders, wait_s: float = REMOVE_WAIT_S, *, sleep=time.sleep,
                   clock=time.monotonic, rename=os.rename) -> None:
    """Проверить заранее, что папки можно будет удалить: переименовать туда и
    обратно (см. remove_folders). Занята дольше `wait_s` — FolderBusy.

    Отложенная папка помечена `.probing-`: если вернуть её не вышло даже с
    повторами (или процесс упал посередине), восстановление при следующем
    запуске вернёт её на место (restore_probes), а не удалит."""
    deadline = clock() + wait_s
    for folder in (Path(f) for f in folders):
        while True:
            aside = _aside(folder, PROBING_MARK)
            try:
                rename(folder, aside)
            except FileNotFoundError:
                break
            except OSError:
                if clock() >= deadline:
                    raise FolderBusy(FOLDER_BUSY) from None
                sleep(REMOVE_POLL_S)
                continue
            if not _rename_back(aside, folder, rename=rename, sleep=sleep):
                raise FolderBusy(PROBE_STUCK.format(name=folder.name))
            break


def _marked(root: Path, mark: str) -> list[Path]:
    try:
        return [p for p in Path(root).iterdir()
                if p.is_dir() and p.name.startswith(".") and mark in p.name]
    except OSError:
        return []


def leftover_deletions(root: Path) -> list[Path]:
    """Отложенные к удалению папки, оставшиеся от сбоя посреди удаления."""
    return _marked(root, DELETING_MARK)


def leftover_probes(root: Path) -> list[Path]:
    """Папки, отложенные проверкой wait_removable и не вернувшиеся на место."""
    return _marked(root, PROBING_MARK)


def restore_probes(root: Path) -> list[str]:
    """Вернуть под прежние имена папки, застрявшие посреди проверки
    wait_removable (`.<id>.probing-…` → `<id>`). Имя уже занято — не трогаем
    ни ту, ни другую. Возвращает имена возвращённых записей."""
    restored = []
    for probe in leftover_probes(root):
        name = probe.name[1:probe.name.rfind(PROBING_MARK)]
        if not name:
            continue
        target = probe.with_name(name)
        if target.exists():
            continue
        try:
            os.rename(probe, target)
        except OSError:
            continue
        restored.append(name)
    return restored


def create_import(root: Path, src: Path) -> Path:
    """Папка записи под импортируемый файл. Сам файл копирует задача `import`
    (это может быть гигабайт видео — не в потоке HTTP), здесь только папка и
    meta.json. Время в имени — время изменения файла: обычно это конец встречи,
    ближе к правде, чем «сейчас»."""
    if src.suffix.lower() not in IMPORT_EXTS:
        raise ValueError(f"формат {src.suffix or 'без расширения'} не поддерживается")
    stamp = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d_%H-%M")
    root.mkdir(parents=True, exist_ok=True)
    # mkdir как проверка: exists()+mkdir() — гонка при двух импортах сразу, и
    # проигравший получал бы FileExistsError вместо своей папки.
    folder = root / f"{stamp}_import"
    n = 2
    while True:
        try:
            folder.mkdir()
            break
        except FileExistsError:
            folder = root / f"{stamp}_import-{n}"
            n += 1
    write_meta(folder, {
        "source": "import",
        "original_path": str(src),
        "original_name": src.name,
        "title": src.stem,
        "title_source": "auto",
    })
    return folder


def _tracks(folder: Path) -> dict:
    tracks = {}
    for stem in TRACK_STEMS:
        track = find_track(folder, stem)
        if track is not None:
            tracks[stem] = track
    return tracks


def _recognised(tracks: dict, meta: dict) -> bool:
    # без дорожек это не запись (импорт — запись ещё до копии, объединение —
    # пока собирается звук)
    return bool(tracks) or meta.get("source") in ("import", "merge")


def is_recording(folder: Path) -> bool:
    """Та же проверка «это папка записи», что у describe, но без чтения
    транскрипта: обходу всей библиотеки (статистика людей) хватает одного
    разбора transcript.json на папку."""
    return folder.is_dir() and _recognised(_tracks(folder), read_meta(folder))


def recording_folders(root: Path) -> list[Path]:
    """Папки записей библиотеки по имени (= по дате), от старых к новым."""
    if not root.is_dir():
        return []
    # С точки — служебные (отложенная к удалению папка, см. remove_folders).
    return [f for f in sorted(root.iterdir(), key=lambda p: p.name)
            if not f.name.startswith(".") and is_recording(f)]


def title_and_date(folder: Path, data: dict | None, *,
                   today_if_unknown: bool = False) -> tuple[str, str]:
    """Название и дата (ГГГГ-ММ-ДД) встречи — одни для экспорта, итогов,
    вопросов и заметки: название из карточки, иначе из транскрипта, иначе имя
    папки; дата — начало записи. Неизвестна — "" (экспорт не выдумывает), а
    с `today_if_unknown` — сегодня (заметке нужна дата в имени файла)."""
    folder = Path(folder)
    card = describe(folder)
    title = (card.title if card else None) or (data or {}).get("title") or folder.name
    date = ((card.started_at if card else None) or "")[:10]
    if not date and today_if_unknown:
        date = datetime.now().strftime("%Y-%m-%d")
    return str(title), date


# Откуда название записи (meta.json `title_source`), см. title_source().
TITLE_SOURCES = ("auto", "user", "ai", "site")


def title_source(meta: dict) -> str:
    """Откуда название записи. Явная пометка — она. Без пометки (записи до
    0.3.0) — по самому названию: его нет — "auto"; имя импортированного файла
    или «Объединённая встреча …» — тоже "auto"; любое другое название в
    meta.json задал человек — "user" (его модель не трогает никогда)."""
    source = meta.get("title_source")
    if source in TITLE_SOURCES:
        return source
    title = str(meta.get("title") or "").strip()
    if not title:
        return "auto"
    original = meta.get("original_name")
    if (meta.get("source") == "import" and isinstance(original, str)
            and title == Path(original).stem):
        return "auto"
    if meta.get("source") == "merge" and title.startswith("Объединённая встреча"):
        return "auto"
    if meta.get("source") == "auto":
        # Автозапись до 0.3.0 писала заголовок окна звонка без пометки: общий
        # («Google Meet», код встречи) — тоже из окна, а не от человека.
        from meet.settings import DEFAULT_CALL_SITES
        from meet.titles import is_generic_site_title

        if is_generic_site_title(title, DEFAULT_CALL_SITES):
            return "site"
    return "user"


def describe(folder: Path) -> Recording | None:
    """Папка записи → карточка для библиотеки. Не папка записи — None."""
    if not folder.is_dir():
        return None
    tracks = _tracks(folder)
    meta = read_meta(folder)
    if not _recognised(tracks, meta):
        return None
    has_json, title, diarization, asr_note, phase, mic_split = _transcript_head(folder)
    if meta.get("title"):
        title = str(meta["title"])
    source = meta.get("source") if meta.get("source") in SOURCES else "record"
    try:
        transcript_at = transcript_path(folder).stat().st_mtime
    except OSError:
        transcript_at = None
    return Recording(
        id=folder.name,
        path=folder,
        started_at=_started_at(folder.name),
        tracks=tracks,
        duration_s=_duration_from_events(folder),
        has_transcript=has_json or bool(list(folder.glob("*_transcript.md"))),
        has_voices=bool(list(folder.glob("*_speakers.json"))),
        title=title,
        title_source=title_source(meta),
        title_llm=meta["title_llm"] if title_source(meta) == "ai" and isinstance(meta.get("title_llm"), dict)
        else None,
        source=source,
        transcript_at=transcript_at,
        diarization=diarization,
        asr_note=asr_note,
        kb_export=meta["kb_export"] if isinstance(meta.get("kb_export"), dict) else None,
        merge=_merge_summary(meta),
        rediarize_ready=(folder / REDIARIZE_PREVIEW).is_file(),
        category=_category(meta),
        system_audio=meta["system_audio"] if meta.get("system_audio") in ("missing", "partial")
        else None,
        system_audio_reason=meta["system_audio_reason"]
        if meta.get("system_audio_reason") in SYSTEM_AUDIO_REASONS else None,
        transcript_phase=phase,
        mic_split=mic_split,
    )


SYSTEM_AUDIO_REASONS = ("permission", "helper", "unsupported", "failed")


def _category(meta: dict) -> dict | None:
    from meet.categories import of

    return of(meta)


def _merge_summary(meta: dict) -> dict | None:
    info = meta.get("merge")
    if meta.get("source") != "merge" or not isinstance(info, dict):
        return None
    sources = meta.get("merged_from")
    kb_left = info.get("kb_exported")
    return {
        "parts": len(sources) if isinstance(sources, list) else 0,
        "state": str(info.get("state") or "pending"),
        "deleted": bool(info.get("deleted")),
        "kb_left": [str(p) for p in kb_left] if isinstance(kb_left, list) else [],
    }


def listing(root: Path, limit: int = 200, keep=None) -> list[dict]:
    """Записи от свежих к старым. Имена папок сортируются как даты. `keep(card)`
    — фильтр (категории) до `limit`."""
    if not root.is_dir():
        return []
    found = []
    children = sorted(root.iterdir(), key=lambda p: p.name, reverse=True)
    _forget_heads(root, {f.name for f in children})
    for folder in children:
        card = describe(folder)
        if card is not None:
            raw = card.to_raw()
            if keep is None or keep(raw):
                found.append(raw)
        if len(found) >= limit:
            break
    return found


def search(root: Path, q: str, limit: int = 200, keep=None) -> list[dict]:
    """Записи, где запрос (без учёта регистра) есть в названии или в тексте
    любой реплики. Пустой запрос — вся библиотека, как у `listing`. Полный
    обход с чтением transcript.json: библиотека — сотни папок, индекс был бы
    вторым источником истины."""
    if not (q or "").strip():
        return listing(root, limit, keep)
    return search_cards(listing(root, limit=10**9, keep=keep), q, limit)


def search_cards(cards, q: str, limit: int = 200) -> list[dict]:
    """Карточки (по порядку), у которых запрос (без учёта регистра) есть в
    названии или в тексте любой реплики; пустой запрос — первые `limit`."""
    q = (q or "").strip().lower()
    if not q:
        return list(cards)[:max(0, limit)]
    found = []
    for card in cards:
        if len(found) >= limit:
            break
        transcript = read_transcript(Path(card["path"])) or {}
        haystack = [card.get("title") or "", str(transcript.get("title") or "")]
        segments = transcript.get("segments")
        if isinstance(segments, list):
            haystack += [str(s.get("text") or "") for s in segments
                         if isinstance(s, dict) and s.get("kind") != "break"]
        if any(q in part.lower() for part in haystack):
            found.append(card)
    return found


def latest(root: Path) -> Recording | None:
    for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
        card = describe(folder)
        if card is not None:
            return card
    return None


def segments_to_raw(segments, speakers: dict | None = None,
                    title: str | None = None, source: str | None = None) -> dict:
    """Сегменты пайплайна → структурный транскрипт для редактора.

    Хранит и текст, и таймкоды, и метки нахлёста: редактору нужно ровно то же,
    что видит человек в Markdown, но в виде данных."""
    return {
        "version": 1,
        "title": title,
        "source": source,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "speakers": speakers or {},
        "segments": [
            {
                "start": round(float(s.start), 2),
                "end": round(float(s.end), 2),
                "speaker": s.speaker,
                "text": s.text,
                "uncertain": bool(getattr(s, "uncertain", False)),
                # отметка перерыва объединённой встречи (meet.merge)
                **({"kind": s.kind} if getattr(s, "kind", None) else {}),
                # микрофон владельца в записи звонка (без поля — собеседники)
                **({"track": s.track} if getattr(s, "track", None) else {}),
                # слова с таймкодами: по ним правка спикеров режет реплику
                **({"words": words_to_raw(s.words)} if getattr(s, "words", None) else {}),
            }
            for s in segments
        ],
    }


def words_to_raw(words) -> list[list]:
    """Слова сегмента компактно: [начало, конец, текст]. Текст — как у
    распознавания, с ведущим пробелом: "".join(текстов).strip() == текст
    сегмента."""
    return [[round(float(w.start), 2), round(float(w.end), 2), str(w.text)] for w in words]


def words_match(segment: dict) -> bool:
    """Слова сегмента ещё описывают его текст (текст не правили руками)."""
    words = segment.get("words")
    if not isinstance(words, list) or not words:
        return False
    try:
        return "".join(str(w[2]) for w in words).strip() == str(segment.get("text") or "").strip()
    except (IndexError, TypeError, KeyError):
        return False
