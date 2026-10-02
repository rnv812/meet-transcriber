"""Индекс реплик людей для профилей (meet.profiles): по встрече — кто из
названных спикеров сколько говорил и какие у него реплики (номер, начало,
длина, число слов, отпечаток текста), без самого текста.

Зачем: профиль, проверка «пора ли обновить» и ссылки на реплики не должны
разбирать все transcript.json библиотеки (тысячи встреч — десятки секунд).
Индекс по встрече лежит файлом `<data_dir>/profiles/_index/<id встречи>.json`
и пересчитывается, только когда изменились transcript.json или meta.json
(время изменения и размер); в резиденте он ещё и в памяти. Первый проход по
большой библиотеке резидент делает в фоне.

Удаление (`forget`): профили выключили или удалили все — индекс с диска и из
памяти убирается так, чтобы его не воскресили: запись файла и удаление папки
идут под одним замком хранилища, идущий первый проход отменяется и больше не
пишет, а хранилище помечается удалённым — новый индекс для него не
создаётся, пока профили снова не включат (`get` перепроверяет настройку).

Реплика здесь — подряд идущие сегменты одного спикера (как в окне): номер
реплики `i` — номер её первого сегмента в transcript.json, `start` — его
начало. Отпечаток `h` — хеш первых HASH_CHARS символов нормализованного
текста: по нему ссылка профиля узнаёт «ту же» реплику после правок.
"""

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

from meet import library

VERSION = 1
DIR_NAME = "_index"
HASH_CHARS = 60
# Реплика короче (междометия, «Да.», «Угу.») не считается и не уходит модели.
WORDS_MIN = 3

# Поля реплики в индексе: [i, start, chars, words, h, before_chars].
I, START, CHARS, WORDS, HASH, BEFORE = range(6)


def norm_text(text) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def text_hash(text) -> str:
    return hashlib.sha1(norm_text(text)[:HASH_CHARS].encode("utf-8")).hexdigest()[:10]


def _start(seg: dict) -> float:
    try:
        return float(seg.get("start") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def turns_of(data: dict | None) -> list[dict]:
    """Реплики расшифровки: подряд идущие сегменты одного спикера — одна
    реплика {"speaker", "i", "start", "text", "before"}; `before` —
    предыдущая реплика другого участника «Имя: текст» (после отметки
    перерыва — None). Пустые сегменты пропускаются, номера остальных — как в
    transcript.json."""
    data = library.with_display_names(data) or {}
    segments = data.get("segments")
    out: list[dict] = []
    cur: dict | None = None
    before: str | None = None
    for i, seg in enumerate(segments if isinstance(segments, list) else []):
        if not isinstance(seg, dict):
            continue
        if seg.get("kind") == "break":
            cur, before = None, None
            continue
        text = " ".join(str(seg.get("text") or "").split())
        if not text:
            continue
        speaker = seg.get("speaker")
        if cur is not None and speaker == cur["speaker"]:
            cur["text"] += " " + text
            continue
        if cur is not None:
            before = f"{cur['speaker'] or 'Спикер ?'}: {cur['text']}"
        cur = {"speaker": speaker, "i": i, "start": _start(seg), "text": text, "before": before}
        out.append(cur)
    return out


def _key(folder: Path) -> list | None:
    try:
        st = library.transcript_path(folder).stat()
    except OSError:
        return None
    try:
        meta = (folder / "meta.json").stat().st_mtime_ns
    except OSError:
        meta = 0
    return [st.st_mtime_ns, st.st_size, meta]


def extract(folder: Path, key: list | None = None) -> dict | None:
    """Строка индекса встречи (None — расшифровки нет)."""
    from meet import categories

    folder = Path(folder)
    key = key or _key(folder)
    data = library.read_transcript(folder)
    if key is None or data is None:
        return None
    people: dict[str, list] = {}
    for t in turns_of(data):
        if not isinstance(t["speaker"], str) or not t["speaker"]:
            continue
        people.setdefault(t["speaker"], []).append(
            [t["i"], round(t["start"], 2), len(t["text"]), len(t["text"].split()), text_hash(t["text"]),
             len(t["before"]) if t["before"] else 0])
    meta = library.read_meta(folder)
    category = categories.of(meta)
    return {
        "v": VERSION,
        "id": folder.name,
        "key": key,
        "title": str(meta.get("title") or data.get("title") or folder.name),
        "started": library._started_at(folder.name),
        "category": category["id"] if category else None,
        "people": people,
    }


class Index:
    """Индекс библиотеки записей `recordings`; `store` — папка файлов индекса
    (None — только в памяти)."""

    def __init__(self, recordings: Path, store: Path | None = None) -> None:
        self.recordings = Path(recordings)
        self.store = Path(store) if store is not None else None
        self._mem: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._texts: dict[str, tuple[list, list[dict]]] = {}
        self._texts_lock = threading.Lock()
        self.warm = False
        self.building = False
        # Индекс забыт (forget): проход останавливается, на диск больше не пишет.
        self.cancelled = False

    def _path(self, rid: str) -> Path:
        assert self.store is not None
        return self.store / f"{rid}.json"

    def _load(self, rid: str) -> dict | None:
        if self.store is None:
            return None
        try:
            got = json.loads(self._path(rid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return got if isinstance(got, dict) and got.get("v") == VERSION else None

    def _save(self, entry: dict) -> None:
        store = self.store
        if store is None:
            return
        # Под замком хранилища и с перепроверкой: удаление (forget) не может
        # пройти посреди записи, а после него запись не воскресит папку.
        with store_lock(store):
            if self.cancelled or self.store is None or _deleted(store):
                return
            path = self._path(entry["id"])
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                store.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                pass  # индекс — кэш: не записался — пересчитаем в следующий раз
            finally:
                tmp.unlink(missing_ok=True)

    def refresh(self, *, yield_s: float = 0.0) -> dict[str, dict]:
        """Актуальный индекс: заново разбираются только изменившиеся встречи.
        `yield_s` — пауза после каждого разбора (фоновый первый проход не
        должен занимать резидент целиком). → {id встречи: строка}."""
        with self._lock:
            fresh: dict[str, dict] = {}
            try:
                items = sorted(os.scandir(self.recordings), key=lambda e: e.name)
            except OSError:
                items = []
            for item in items:
                if self.cancelled:
                    return {}  # индекс забыли посреди прохода — бросаем его
                if item.name.startswith(".") or not item.is_dir():
                    continue
                folder = Path(item.path)
                key = _key(folder)
                if key is None:
                    continue
                cur = self._mem.get(item.name)
                if cur is not None and cur["key"] == key:
                    fresh[item.name] = cur
                    continue
                disk = self._load(item.name) if cur is None else None
                if disk is not None and disk.get("key") == key:
                    fresh[item.name] = disk
                    continue
                try:
                    entry = extract(folder, key)
                except Exception:
                    entry = None
                if entry is None:
                    # Расшифровка есть, но не читается (её как раз пишут) —
                    # прежняя строка остаётся (с прежним ключом: разберём
                    # в следующий раз), а не «встречу удалили».
                    prev = cur or disk
                    if prev is not None:
                        fresh[item.name] = prev
                    continue
                self._save(entry)
                fresh[item.name] = entry
                if yield_s:
                    time.sleep(yield_s)
            if self.cancelled:
                return {}
            if self.store is not None and not self.warm and self.store.is_dir():
                for path in self.store.glob("*.json"):
                    if path.stem not in fresh:
                        path.unlink(missing_ok=True)
            elif self.store is not None:
                for gone in set(self._mem) - set(fresh):
                    self._path(gone).unlink(missing_ok=True)
            with self._texts_lock:
                for gone in [rid for rid in list(self._texts) if rid not in fresh]:
                    del self._texts[gone]
            self._mem = fresh
            self.warm = True
            return dict(fresh)

    def turns(self, rid: str) -> list[dict]:
        """Реплики встречи с текстом (для ссылок после правок) — с кэшем по
        ключу расшифровки."""
        folder = self.recordings / rid
        key = _key(folder)
        if key is None:
            return []
        with self._texts_lock:
            hit = self._texts.get(rid)
        if hit is not None and hit[0] == key:
            return hit[1]
        got = turns_of(library.read_transcript(folder))
        with self._texts_lock:
            self._texts[rid] = (key, got)
        return got


_registry: dict[tuple[str, str], Index] = {}
_registry_lock = threading.Lock()
# Замки хранилищ (запись файла индекса и удаление папки) и хранилища,
# помеченные удалёнными (до того, как профили снова включат).
_store_locks: dict[str, threading.Lock] = {}
_deleted_stores: set[str] = set()


def _norm(store) -> str:
    return os.path.normcase(str(Path(store)))


def store_lock(store) -> threading.Lock:
    with _registry_lock:
        return _store_locks.setdefault(_norm(store), threading.Lock())


def _deleted(store) -> bool:
    return _norm(store) in _deleted_stores


def _profiles_enabled() -> bool:
    try:
        from meet import settings

        return settings.load().profiles.enabled
    except Exception:
        return False


def get(recordings: Path, store: Path | None = None) -> Index:
    """Индекс процесса для этой библиотеки (резидент держит его в памяти).
    Хранилище удалено (forget), а профили всё ещё выключены (кто-то прочитал
    настройку до выключения), — отменённый пустой индекс: ничего не
    считает и не пишет. Профили включили снова — пометка снимается."""
    if store is None:
        from meet import profiles

        store = profiles.profiles_dir() / DIR_NAME
    k = (_norm(recordings), _norm(store))
    with store_lock(store):
        if _deleted(store):
            if not _profiles_enabled():
                dead = Index(recordings, None)
                dead.cancelled = True
                return dead
            _deleted_stores.discard(_norm(store))
        with _registry_lock:
            ix = _registry.get(k)
            if ix is None:
                ix = _registry[k] = Index(recordings, store)
            return ix


def forget(store: Path, *, delete: bool = True) -> None:
    """Забыть индексы с этим хранилищем (профили удалили или выключили): из
    реестра процесса, идущий проход — отменить (он больше не пишет), папку —
    удалить (`delete`), хранилище — пометить удалённым. Всё под замком
    хранилища: запись файла не проскочит ни до, ни после."""
    import shutil

    key = _norm(store)
    with store_lock(store):
        _deleted_stores.add(key)
        with _registry_lock:
            gone = [_registry.pop(k) for k in [k for k in _registry if k[1] == key]]
        for ix in gone:
            ix.cancelled = True
            ix.store = None
            ix._mem = {}
            with ix._texts_lock:
                ix._texts = {}
            ix.warm = False
        if delete:
            shutil.rmtree(store, ignore_errors=True)


def warm_in_background(ix: Index, on_done=None) -> bool:
    """Первый проход — в фоне. → запущен ли (уже тёплый или уже идёт — нет)."""
    if ix.warm or ix.building or ix.cancelled:
        return False
    ix.building = True

    def work() -> None:
        try:
            ix.refresh(yield_s=0.001)
        finally:
            ix.building = False
            if on_done is not None:
                on_done()

    threading.Thread(target=work, name="meet-profile-index", daemon=True).start()
    return True
