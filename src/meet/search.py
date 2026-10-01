"""Поиск по тексту встреч: по всей библиотеке (`GET /search`) с фрагментами.

Правила — те же, что у поиска внутри встречи в окне (`app/src/lib/search.ts`),
общие случаи — `tests/fixtures/search_cases.json`, их проверяют оба набора
тестов:

* сравнение по словам (цепочки букв и цифр), без учёта регистра, «ё» = «е»;
  знаки препинания только разделяют слова, подсветка — по исходному тексту;
* "фраза в кавычках" (и «…», “…”) — слова подряд, точно;
* слова без кавычек — ключевые, нужны все; слово текста подходит, если
  начинается с основы ключевого (у слов от 5 букв отрезается одно частое
  окончание из `ENDINGS`, основа — не короче 4 букв);
* `спикер:Анна` / `спикер:"Анна П"` — только реплики этого спикера.

Реплики — как в карточке: подряд идущие сегменты одного спикера с паузой
меньше 2 с склеиваются (`mergeTurns` в окне), сырые SPEAKER_XX — «Спикер N».

Индекса нет: транскрипты читаются при поиске, разобранные — в памяти, пока
не изменился файл (mtime и размер). Кэш ограничен по объёму текста; фоновой
индексации нет.
"""

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
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
# Сколько текста держать разобранным: ~40 часовых встреч. Больше — вытесняются
# давно не нужные, и поиск по ним просто прочтёт файл заново.
CACHE_CHARS = 8_000_000

_WORD_RE = re.compile(r"[^\W_]+")
_QUERY_RE = re.compile(
    r'(спикер:)?(?:"([^"]*)"?|«([^»]*)»?|“([^”]*)”?)|спикер:(\S*)|(\S+)', re.IGNORECASE)


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
    for m in _QUERY_RE.finditer(q or ""):
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
    """Совпадение в реплике с учётом спикера; пустой запрос не находит ничего."""
    if q.empty or not speaker_matches(speaker, q):
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
    norm: str


def turns_of(segments) -> list[Turn]:
    """Реплики как в карточке: склейка подряд идущих сегментов одного спикера."""
    out: list[Turn] = []
    parts: list[str] = []
    end = 0.0
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        speaker = seg.get("speaker") or NO_SPEAKER
        start = float(seg.get("start") or 0.0)
        text = str(seg.get("text") or "")
        if out and out[-1].speaker == speaker and start - end < GAP_S:
            parts.append(text)
            end = max(end, float(seg.get("end") or start))
            out[-1].text = " ".join(parts)
        else:
            parts = [text]
            end = float(seg.get("end") or start)
            out.append(Turn(start, str(speaker), text, ""))
    for turn in out:
        turn.norm = norm_word(turn.text)
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


class _Cache:
    """Разобранные транскрипты по папке, пока файл тот же; объём ограничен."""

    def __init__(self, limit: int = CACHE_CHARS) -> None:
        self.limit = limit
        self._items: OrderedDict[str, tuple[tuple, int, list[Turn]]] = OrderedDict()
        self._chars = 0
        self._lock = threading.Lock()

    def turns(self, folder: Path) -> list[Turn]:
        try:
            st = library.transcript_path(folder).stat()
        except OSError:
            return []
        key, stamp = str(folder), (st.st_mtime_ns, st.st_size)
        with self._lock:
            got = self._items.get(key)
            if got and got[0] == stamp:
                self._items.move_to_end(key)
                return got[2]
        data = library.with_display_names(library.read_transcript(folder)) or {}
        turns = turns_of(data.get("segments") if isinstance(data.get("segments"), list) else [])
        size = sum(len(t.text) for t in turns)
        with self._lock:
            old = self._items.pop(key, None)
            if old:
                self._chars -= old[1]
            self._items[key] = (stamp, size, turns)
            self._chars += size
            while self._chars > self.limit and len(self._items) > 1:
                _, (_, dropped, _) = self._items.popitem(last=False)
                self._chars -= dropped
        return turns

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._chars = 0


_CACHE = _Cache()


def clear_cache() -> None:
    _CACHE.clear()


def search_library(root: Path, q: str, limit: int = 200) -> list[dict]:
    """Записи, где запрос нашёлся в репликах или в названии, от свежих к
    старым: карточка записи и `date`, `hits` (до MAX_HITS: время реплики,
    спикер, фрагмент, подсветка), `total` — сколько реплик подошло,
    `title_match` — нашлось в названии. Пустой запрос — пустой ответ."""
    query = parse_query(q)
    if query.empty:
        return []
    found = []
    for card in library.listing(Path(root), limit=10**9):
        hits, total = [], 0
        if card.get("has_transcript"):
            for turn in _CACHE.turns(Path(card["path"])):
                if not speaker_matches(turn.speaker, query):
                    continue
                if any(s not in turn.norm for s in query.stems) or any(
                        p[0] not in turn.norm for p in query.phrases):
                    continue
                ranges = match_tokens(tokenize(turn.text), query)
                if ranges is None:
                    continue
                total += 1
                if len(hits) < MAX_HITS:
                    text, marks = snippet(turn.text, ranges)
                    hits.append({"t": turn.start, "speaker": turn.speaker,
                                 "snippet": text, "ranges": marks})
        title = card.get("title") or ""
        title_match = bool(title) and query.has_text and not query.speakers and (
            match_tokens(tokenize(title), query) is not None)
        if total or title_match:
            found.append({**card, "date": (card.get("started_at") or "")[:10] or None,
                          "hits": hits, "total": total, "title_match": title_match})
            if len(found) >= limit:
                break
    return found
