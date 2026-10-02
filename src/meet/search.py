"""Поиск по тексту встреч: по всей библиотеке (`GET /search`) с фрагментами.

Правила — те же, что у поиска внутри встречи в окне (`app/src/lib/search.ts`),
общие случаи — `tests/fixtures/search_cases.json`, их проверяют оба набора
тестов:

* текст и запрос сначала приводятся к NFC, подсветка — по приведённому
  тексту (его и отдаёт фрагмент);
* сравнение по словам (цепочки букв и цифр), без учёта регистра, «ё» = «е»;
  знаки препинания только разделяют слова, подсветка — по исходному тексту;
* "фраза в кавычках" (и «…», “…”) — слова подряд, точно;
* слова без кавычек — ключевые, нужны все; слово текста подходит, если
  начинается с основы ключевого (у слов от 5 букв отрезается одно частое
  окончание из `ENDINGS`, основа — не короче 4 букв);
* `спикер:Анна` / `спикер:"Анна П"` — только реплики этого спикера.

Реплики — как в карточке: подряд идущие сегменты одного спикера с паузой
меньше 2 с склеиваются (`mergeTurns` в окне), сырые SPEAKER_XX — «Спикер N».

Индекса нет: транскрипты читаются при поиске, разобранные (реплики и их
слова) — в памяти, пока не изменился файл (mtime и размер); карточки записей
— пока не изменились папка, meta.json, транскрипт и events.jsonl. Кэш
ограничен по объёму; фоновой индексации нет. Запрос короче MIN_QUERY символов
ничего не ищет.
"""

import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from meet import library

ENDINGS = (
    "ться",
    "ами", "ями",
    "ой", "ей", "ий", "ый", "ая", "яя", "ое", "ее", "ом", "ем", "ам", "ям", "ах", "ях",
    "ов", "ев", "ия", "ие", "ию", "ии", "ть", "ет", "ут", "ют", "ит", "ат", "ят",
    "а", "я", "ы", "и", "у", "ю", "е", "о",
)
MIN_STEMMED = 5
MIN_STEM = 4
GAP_S = 2.0  # склейка реплик — как mergeTurns в окне
NO_SPEAKER = "Неизвестный"
MAX_HITS = 3  # фрагментов на запись в списке
SNIPPET_LEN = 180
SNIPPET_BEFORE = 30  # список показывает две строки фрагмента: совпадение — в первой
# Сколько держать разобранным (оценка в байтах: текст и слова реплик) —
# порядка двух десятков часовых встреч. Больше — вытесняются давно не нужные,
# и поиск по ним просто прочтёт файл заново.
CACHE_BYTES = 64 * 1024 * 1024
WORD_BYTES = 120  # кортеж (слово, начало, конец) с самим словом
MIN_QUERY = 2  # короче — не ищем: одна буква находит почти всё

_WORD_RE = re.compile(r"[^\W_]+")
_QUERY_RE = re.compile(
    r'(спикер:)?(?:"([^"]*)"?|«([^»]*)»?|“([^”]*)”?)|спикер:(\S*)|(\S+)', re.IGNORECASE)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def norm_word(word: str) -> str:
    return word.lower().replace("ё", "е")


def tokenize(text: str) -> list[tuple[str, int, int]]:
    """Слова текста: (нормализованное, начало, конец) в исходной строке."""
    return [(norm_word(m.group()), m.start(), m.end()) for m in _WORD_RE.finditer(text)]


def _words(text: str) -> list[str]:
    return [t[0] for t in tokenize(text)]


def stem(word: str) -> str:
    if len(word) < MIN_STEMMED:
        return word
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            return word[:-len(ending)]
    return word


@dataclass
class Query:
    phrases: list[list[str]]
    keywords: list[str]
    stems: list[str]
    speakers: list[list[str]]

    @property
    def empty(self) -> bool:
        return not (self.phrases or self.keywords or self.speakers)

    @property
    def has_text(self) -> bool:
        return bool(self.phrases or self.keywords)


def parse_query(q: str) -> Query:
    phrases: list[list[str]] = []
    keywords: list[str] = []
    speakers: list[list[str]] = []
    for m in _QUERY_RE.finditer(nfc(q or "")):
        quoted = next((g for g in m.group(2, 3, 4) if g is not None), None)
        if quoted is not None:
            words = _words(quoted)
            if words:
                (speakers if m.group(1) else phrases).append(words)
        elif m.group(5) is not None:
            words = _words(m.group(5))
            if words:
                speakers.append(words)
        else:
            for w in _words(m.group(6) or ""):
                if w not in keywords:
                    keywords.append(w)
    return Query(phrases, keywords, [stem(k) for k in keywords], speakers)


def speaker_matches(speaker: str, q: Query) -> bool:
    if not q.speakers:
        return True
    names = _words(speaker)
    return any(all(any(n.startswith(w) for n in names) for w in f) for f in q.speakers)


def _merge(ranges: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def match_tokens(tokens, q: Query, norm: str | None = None) -> list[list[int]] | None:
    """Совпадение без учёта спикера: None — не подходит, иначе что подсветить
    (позиции в символах Python). `norm` — нормализованный текст для отсева."""
    if norm is not None:
        if any(s not in norm for s in q.stems) or any(p[0] not in norm for p in q.phrases):
            return None
    for s in q.stems:
        if not any(t[0].startswith(s) for t in tokens):
            return None
    ranges: list[list[int]] = []
    for phrase in q.phrases:
        n, found = len(phrase), False
        for i in range(len(tokens) - n + 1):
            if all(tokens[i + j][0] == phrase[j] for j in range(n)):
                found = True
                ranges.append([tokens[i][1], tokens[i + n - 1][2]])
        if not found:
            return None
    if q.stems:
        ranges += [[t[1], t[2]] for t in tokens if any(t[0].startswith(s) for s in q.stems)]
    return _merge(ranges)


def match_text(text: str, q: Query, speaker: str = "") -> list[list[int]] | None:
    """Совпадение в реплике с учётом спикера; пустой запрос не находит ничего.
    Подсветка — по тексту после NFC (`nfc(text)`)."""
    text = nfc(text)
    if q.empty or not speaker_matches(nfc(speaker), q):
        return None
    norm = norm_word(text)
    if any(s not in norm for s in q.stems) or any(p[0] not in norm for p in q.phrases):
        return None
    return match_tokens(tokenize(text), q)


# --- реплики и фрагменты -------------------------------------------------------------


@dataclass
class Turn:
    start: float
    speaker: str
    text: str
    norm: str = ""
    # Слова реплики (tokenize): разбираются один раз и живут в кэше с репликой.
    tokens: list = field(default_factory=list)


def turns_of(segments) -> list[Turn]:
    """Реплики как в карточке: склейка подряд идущих сегментов одного спикера."""
    out: list[Turn] = []
    parts: list[str] = []
    end = 0.0
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        start = float(seg.get("start") or 0.0)
        if seg.get("kind") == "break":
            # Отметка перерыва объединённой встречи: в окне — разделитель, не
            # реплика. Своё место в списке (номера реплик — как в окне), но без
            # текста: искать в ней нечего.
            out.append(Turn(start, "", ""))
            parts, end = [], start
            continue
        speaker = nfc(str(seg.get("speaker") or "")) or NO_SPEAKER  # пустой — как null, как в окне
        text = nfc(str(seg.get("text") or ""))
        if out and out[-1].speaker == speaker and out[-1].speaker and start - end < GAP_S:
            parts.append(text)
            end = max(end, float(seg.get("end") or start))
            out[-1].text = " ".join(parts)
        else:
            parts = [text]
            end = float(seg.get("end") or start)
            out.append(Turn(start, str(speaker), text))
    for turn in out:
        turn.norm = norm_word(turn.text)
        turn.tokens = tokenize(turn.text)
    return out


def _utf16(text: str, index: int) -> int:
    return len(text[:index].encode("utf-16-le")) // 2


def snippet(text: str, ranges) -> tuple[str, list[list[int]]]:
    """Кусок реплики вокруг первого совпадения (по границам слов, «…» там, где
    обрезано) и подсветка в нём — в единицах UTF-16, как считает строки окно."""
    first_start, first_end = ranges[0] if ranges else (0, 0)
    start = max(0, first_start - SNIPPET_BEFORE)
    if start > 0:
        space = text.find(" ", start, first_start)
        if space != -1:
            start = space + 1
    end = min(len(text), start + SNIPPET_LEN)
    if end < len(text):
        space = text.rfind(" ", first_end, end)
        if space > first_end:
            end = space
    end = max(end, min(len(text), first_end))
    prefix = "…" if start > 0 else ""
    body = prefix + text[start:end] + ("…" if end < len(text) else "")
    shift = len(prefix) - start
    out = []
    for a, b in ranges:
        a, b = max(a, start), min(b, end)
        if a < b:
            out.append([_utf16(body, a + shift), _utf16(body, b + shift)])
    return body, out


# --- библиотека ----------------------------------------------------------------------


def _stamp(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _Cache:
    """Разобранные транскрипты и карточки записей по папке, пока файлы те же.
    Реплики ограничены по объёму (`limit`, байты по оценке), карточки малы."""

    def __init__(self, limit: int = CACHE_BYTES) -> None:
        self.limit = limit
        self._items: OrderedDict[str, tuple[tuple, int, list[Turn]]] = OrderedDict()
        self._size = 0
        self._cards: dict[str, tuple[tuple, dict | None]] = {}
        self._lock = threading.Lock()

    def card(self, folder: Path) -> dict | None:
        """Карточка записи (`library.describe`), None — не запись. Перечитывается,
        когда меняется сама папка (файлы добавлены, удалены), meta.json,
        транскрипт или events.jsonl."""
        stamp = (_stamp(folder), _stamp(folder / library.META_JSON),
                 _stamp(library.transcript_path(folder)), _stamp(folder / "events.jsonl"))
        key = str(folder)
        with self._lock:
            got = self._cards.get(key)
            if got and got[0] == stamp:
                return got[1]
        card = library.describe(folder)
        raw = card.to_raw() if card is not None else None
        with self._lock:
            self._cards[key] = (stamp, raw)
        return raw

    def forget_except(self, keep: set[str]) -> None:
        """Убрать карточки папок, которых больше нет."""
        with self._lock:
            for key in [k for k in self._cards if k not in keep]:
                del self._cards[key]

    def turns(self, folder: Path) -> list[Turn]:
        stamp = _stamp(library.transcript_path(folder))
        if stamp is None:
            return []
        key = str(folder)
        with self._lock:
            got = self._items.get(key)
            if got and got[0] == stamp:
                self._items.move_to_end(key)
                return got[2]
        data = library.with_display_names(library.read_transcript(folder)) or {}
        turns = turns_of(data.get("segments") if isinstance(data.get("segments"), list) else [])
        size = sum(2 * len(t.text) + WORD_BYTES * len(t.tokens) for t in turns)
        with self._lock:
            old = self._items.pop(key, None)
            if old:
                self._size -= old[1]
            self._items[key] = (stamp, size, turns)
            self._size += size
            while self._size > self.limit and len(self._items) > 1:
                _, (_, dropped, _) = self._items.popitem(last=False)
                self._size -= dropped
        return turns

    def forget(self, folder: Path) -> None:
        """Забыть одну запись: её транскрипт только что переписали. По имени
        папки: путь в кэше мог быть записан иначе (без resolve, другой регистр)."""
        name = Path(folder).name.lower()
        with self._lock:
            for key in [k for k in self._items if Path(k).name.lower() == name]:
                self._size -= self._items.pop(key)[1]
            for key in [k for k in self._cards if Path(k).name.lower() == name]:
                del self._cards[key]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._cards.clear()
            self._size = 0


_CACHE = _Cache()


def clear_cache() -> None:
    _CACHE.clear()


def forget(folder: Path) -> None:
    """Перечитать запись при следующем поиске (правка спикеров из окна): не
    полагаемся на время изменения файла — две записи подряд его не меняют."""
    _CACHE.forget(folder)


def _cards(root: Path):
    """Карточки записей библиотеки от свежих к старым (как `library.listing`)."""
    try:
        folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    except OSError:
        return
    _CACHE.forget_except({str(f) for f in folders})
    for folder in folders:
        card = _CACHE.card(folder)
        if card is not None:
            yield card


def cards(root: Path) -> list[dict]:
    """Все карточки библиотеки от свежих к старым — из кеша (meta.json и
    транскрипт перечитываются, только когда меняются)."""
    return list(_cards(Path(root)))


def searchable(q: str | None) -> bool:
    """Ищет ли поиск по такому запросу (не короче MIN_QUERY, есть что искать)."""
    return len(nfc(q or "").strip()) >= MIN_QUERY and not parse_query(q).empty


def search_library(root: Path, q: str, limit: int = 200, keep=None) -> list[dict]:
    """Записи, где запрос нашёлся в репликах или в названии, от свежих к
    старым: карточка записи и `date`, `hits` (до MAX_HITS: время реплики,
    спикер, фрагмент, подсветка), `total` — сколько реплик подошло,
    `title_match` — нашлось в названии. Пустой запрос или короче MIN_QUERY
    символов — пустой ответ. `keep(card)` — фильтр (категории) до поиска и до
    `limit`: старые записи нужной категории не теряются за свежими."""
    if not searchable(q):
        return []
    query = parse_query(q)
    found = []
    for card in _cards(Path(root)):
        if keep is not None and not keep(card):
            continue
        hits, total = [], 0
        if card.get("has_transcript"):
            for turn in _CACHE.turns(Path(card["path"])):
                if not speaker_matches(turn.speaker, query):
                    continue
                if any(s not in turn.norm for s in query.stems) or any(
                        p[0] not in turn.norm for p in query.phrases):
                    continue
                ranges = match_tokens(turn.tokens, query)
                if ranges is None:
                    continue
                total += 1
                if len(hits) < MAX_HITS:
                    text, marks = snippet(turn.text, ranges)
                    hits.append({"t": turn.start, "speaker": turn.speaker,
                                 "snippet": text, "ranges": marks})
        title = nfc(card.get("title") or "")
        title_match = bool(title) and query.has_text and not query.speakers and (
            match_tokens(tokenize(title), query) is not None)
        if total or title_match:
            found.append({**card, "date": (card.get("started_at") or "")[:10] or None,
                          "hits": hits, "total": total, "title_match": title_match})
            if len(found) >= limit:
                break
    return found
