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

Индекса нет: транскрипты читаются при поиске и держатся в памяти, пока не
изменился файл (mtime и размер); карточки записей — пока не изменились папка,
meta.json, транскрипт и events.jsonl. Фоновой индексации нет. Запрос короче
MIN_QUERY символов ничего не ищет.

Кэш в два уровня, оба ограничены по объёму:

1. встреча целиком (`_Doc`): нормализованный текст всех реплик одной строкой
   (`norm`, реплики через перевод строки), начала реплик в нём (`array('I')`),
   время и спикер реплики, а исходный регистр — только там, где он отличается
   (заглавные, «ё»). Встреча без слов запроса отсеивается поиском подстроки по
   всей строке; кандидаты — реплики, где нашлось самое длинное слово запроса,
   проверка остальных — регулярками по той же строке (начало слова для основ,
   слова подряд для фраз — то же правило, что у `match_tokens`);
2. слова реплик (`tokenize`) — лениво, только у реплик, попавших во
   фрагменты, в своём LRU.
"""

import itertools
import os
import re
import sys
import threading
import unicodedata
from array import array
from bisect import bisect_right
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
# Сколько держать разобранным (оценка в байтах): уровень 1 — текст встреч
# (~2 байта на символ, часовая встреча — около 120 КБ: пять сотен таких
# помещаются), уровень 2 — слова реплик из фрагментов. Больше — вытесняются
# давно не нужные, и поиск по ним просто прочтёт файл заново.
CACHE_BYTES = 96 * 1024 * 1024
TOKEN_CACHE_BYTES = 16 * 1024 * 1024
WORD_BYTES = 120  # кортеж (слово, начало, конец) с самим словом
TURN_BYTES = 24  # начало реплики, её время и ссылка на спикера
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
    # library.turn_mark первого сегмента: реплики с разной пометкой не склеиваются.
    mark: tuple = (False, False)


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
        mark = library.turn_mark(seg)
        if out and out[-1].speaker == speaker and out[-1].speaker and out[-1].mark == mark and start - end < GAP_S:
            parts.append(text)
            end = max(end, float(seg.get("end") or start))
            out[-1].text = " ".join(parts)
        else:
            parts = [text]
            end = float(seg.get("end") or start)
            out.append(Turn(start, str(speaker), text, mark=mark))
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


# Символы, у которых нормализация может поменять вид (заглавные, «ё», прочие
# алфавиты): только их и сверяем с нормализованным текстом.
_MAYBE_CASED = re.compile(r"[^a-zа-я0-9\s.,!?;:\-—–«»\"'()…%/]")


_SERIALS = itertools.count(1)


class _Doc:
    """Встреча в кэше (уровень 1): см. описание модуля."""

    __slots__ = ("norm", "offs", "starts", "speakers", "case_at", "case_chars", "texts", "size", "serial")

    def __init__(self, turns: list[Turn]) -> None:
        # Номер разбора: по нему — слова реплик в кэше второго уровня. Не время
        # файла: две записи подряд его не меняют (см. forget).
        self.serial = next(_SERIALS)
        self.norm = "\n".join(t.norm for t in turns)
        self.offs = array("I")
        pos = 0
        for t in turns:
            self.offs.append(pos)
            pos += len(t.norm) + 1
        self.offs.append(pos)  # конец последней реплики + 1: реплика i — offs[i]..offs[i+1]-1
        self.starts = array("d", (t.start for t in turns))
        self.speakers = [sys.intern(t.speaker) for t in turns]
        self.case_at = array("I")
        self.case_chars = ""
        self.texts: list[str] | None = None
        if all(len(t.text) == len(t.norm) for t in turns):
            text = "\n".join(t.text for t in turns)
            chars = []
            for m in _MAYBE_CASED.finditer(text):
                at = m.start()
                if text[at] != self.norm[at]:
                    self.case_at.append(at)
                    chars.append(text[at])
            self.case_chars = "".join(chars)
            extra = 6 * len(self.case_at)
        else:
            # Нормализация поменяла длину (редкие алфавиты): исходный текст —
            # целиком, проверка — по словам, как в окне.
            self.texts = [t.text for t in turns]
            extra = sum(2 * len(t) + 60 for t in self.texts)
        self.size = 2 * len(self.norm) + TURN_BYTES * len(turns) + extra

    def __len__(self) -> int:
        return len(self.speakers)

    def span(self, i: int) -> tuple[int, int]:
        return self.offs[i], self.offs[i + 1] - 1

    def text(self, i: int) -> str:
        """Исходный текст реплики (регистр, «ё»)."""
        if self.texts is not None:
            return self.texts[i]
        a, b = self.span(i)
        out = self.norm[a:b]
        lo, hi = bisect_right(self.case_at, a - 1), bisect_right(self.case_at, b - 1)
        if lo == hi:
            return out
        chars = list(out)
        for k in range(lo, hi):
            chars[self.case_at[k] - a] = self.case_chars[k]
        return "".join(chars)


class _Term:
    """Слово запроса в нормализованном тексте: основа — с начала слова, фраза —
    её слова подряд целиком (то же, что `match_tokens`). Начало слова
    проверяется руками: просмотр назад в регулярке отключает быстрый поиск
    подстроки, и она идёт по тексту в десятки раз медленнее."""

    __slots__ = ("lit", "rx", "weight")

    def __init__(self, words: list[str], phrase: bool) -> None:
        self.lit = words[0]
        self.rx = re.compile(r"[\W_]+".join(map(re.escape, words)) + r"(?![^\W_])") if phrase else None
        self.weight = sum(map(len, words))

    def find(self, text: str, pos: int, end: int) -> int:
        """Первое вхождение с начала слова в [pos, end) или -1."""
        while True:
            if self.rx is None:
                at = text.find(self.lit, pos, end)
                if at < 0:
                    return -1
            else:
                m = self.rx.search(text, pos, end)
                if m is None:
                    return -1
                at = m.start()
            if at == 0 or not text[at - 1].isalnum():  # [^\W_] — это isalnum
                return at
            pos = at + 1


class _Plan:
    """Запрос, разобранный для поиска по `_Doc.norm`: подстроки для отсева
    встречи целиком, слова запроса (`_Term`) и ведущее — самое длинное, по
    нему ищутся реплики-кандидаты."""

    def __init__(self, q: Query) -> None:
        self.q = q
        self.needles = list(q.stems) + [p[0] for p in q.phrases]
        terms = [_Term([s], False) for s in q.stems] + [_Term(p, True) for p in q.phrases]
        terms.sort(key=lambda t: -t.weight)
        self.lead = terms[0] if terms else None
        # Что проверить в реплике-кандидате. Ведущую основу — нет: она нашлась в
        # самой реплике (перевода строки в основе нет); фразу — да: кандидата
        # могли дать слова на стыке двух реплик.
        self.terms = terms[1:] if terms and terms[0].rx is None else terms

    def candidates(self, doc: _Doc):
        """Номера реплик, где есть ведущее слово (по порядку); без слов — все."""
        if self.lead is None:
            yield from range(len(doc))
            return
        pos, norm, offs, end = 0, doc.norm, doc.offs, len(doc.norm)
        while True:
            at = self.lead.find(norm, pos, end)
            if at < 0:
                return
            i = bisect_right(offs, at) - 1
            yield i
            pos = offs[i + 1]  # дальше — со следующей реплики

    def matches(self, doc: _Doc, i: int) -> bool:
        a, b = doc.span(i)
        return all(t.find(doc.norm, a, b) >= 0 for t in self.terms)


def _stamp(path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _Cache:
    """Разобранные транскрипты (`_Doc`, уровень 1), слова реплик из фрагментов
    (уровень 2) и карточки записей по папке, пока файлы те же. Оба уровня
    ограничены по объёму (`limit`, `token_limit`, байты по оценке), карточки малы."""

    def __init__(self, limit: int = CACHE_BYTES, token_limit: int = TOKEN_CACHE_BYTES) -> None:
        self.limit = limit
        self.token_limit = token_limit
        self._items: OrderedDict[str, tuple[tuple, _Doc]] = OrderedDict()
        self._size = 0
        self._tokens: OrderedDict[tuple, list] = OrderedDict()
        self._token_size = 0
        self._cards: dict[str, tuple[tuple, dict | None]] = {}
        self._lock = threading.Lock()

    def card(self, folder) -> dict | None:
        return self.card_stamped(folder)[0]

    def card_stamped(self, folder) -> tuple[dict | None, tuple | None]:
        """Карточка записи (`library.describe`), None — не запись. Перечитывается,
        когда меняется сама папка (файлы добавлены, удалены), meta.json,
        транскрипт или events.jsonl."""
        key = str(folder)
        join = os.path.join
        stamp = (_stamp(key), _stamp(join(key, library.META_JSON)),
                 _stamp(join(key, library.TRANSCRIPT_JSON)), _stamp(join(key, "events.jsonl")))
        with self._lock:
            got = self._cards.get(key)
            if got and got[0] == stamp:
                return got[1], stamp[2]
        card = library.describe(Path(folder))
        raw = card.to_raw() if card is not None else None
        with self._lock:
            self._cards[key] = (stamp, raw)
        return raw, stamp[2]

    def forget_except(self, keep: set[str]) -> None:
        """Убрать карточки папок, которых больше нет."""
        with self._lock:
            for key in [k for k in self._cards if k not in keep]:
                del self._cards[key]

    def doc(self, folder, stamp: tuple | None = None) -> tuple[tuple, _Doc] | None:
        """(ключ слов реплик, встреча) — None, если транскрипта нет. `stamp` —
        отпечаток transcript.json, только что снятый проверкой карточки."""
        key = str(folder)
        stamp = stamp or _stamp(os.path.join(key, library.TRANSCRIPT_JSON))
        if stamp is None:
            return None
        folder = Path(folder)
        with self._lock:
            got = self._items.get(key)
            if got and got[0] == stamp:
                self._items.move_to_end(key)
                return (key, got[1].serial), got[1]
        data = library.with_display_names(library.read_transcript(folder)) or {}
        turns = turns_of(data.get("segments") if isinstance(data.get("segments"), list) else [])
        if library.is_text_phase(data):
            # Текст до спикеров: у собеседников подписи нет (окно не пишет и
            # «Неизвестный»), и запрос `спикер:` их не находит.
            for turn in turns:
                if turn.speaker == NO_SPEAKER:
                    turn.speaker = ""
        doc = _Doc(turns)
        with self._lock:
            old = self._items.pop(key, None)
            if old:
                self._size -= old[1].size
            self._items[key] = (stamp, doc)
            self._size += doc.size
            while self._size > self.limit and len(self._items) > 1:
                _, (_, dropped) = self._items.popitem(last=False)
                self._size -= dropped.size
        return (key, doc.serial), doc

    def turns(self, folder: Path) -> list[Turn]:
        """Реплики записи из кэша (время, спикер, исходный текст)."""
        got = self.doc(folder)
        if got is None:
            return []
        doc = got[1]
        return [Turn(doc.starts[i], doc.speakers[i], doc.text(i), doc.norm[slice(*doc.span(i))])
                for i in range(len(doc))]

    def tokens(self, ref: tuple, i: int, text: str) -> list:
        """Слова реплики `i` встречи `ref` (уровень 2): разбираются один раз."""
        key = (*ref, i)
        with self._lock:
            got = self._tokens.get(key)
            if got is not None:
                self._tokens.move_to_end(key)
                return got
        tokens = tokenize(text)
        size = WORD_BYTES * len(tokens) + 64
        with self._lock:
            if key not in self._tokens:
                self._tokens[key] = tokens
                self._token_size += size
                while self._token_size > self.token_limit and len(self._tokens) > 1:
                    _, dropped = self._tokens.popitem(last=False)
                    self._token_size -= WORD_BYTES * len(dropped) + 64
        return tokens

    def forget(self, folder: Path) -> None:
        """Забыть одну запись: её транскрипт только что переписали. По имени
        папки: путь в кэше мог быть записан иначе (без resolve, другой регистр)."""
        name = Path(folder).name.lower()
        with self._lock:
            for key in [k for k in self._items if Path(k).name.lower() == name]:
                self._size -= self._items.pop(key)[1].size
            for key in [k for k in self._cards if Path(k).name.lower() == name]:
                del self._cards[key]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._tokens.clear()
            self._cards.clear()
            self._size = 0
            self._token_size = 0


_CACHE = _Cache()


def clear_cache() -> None:
    _CACHE.clear()


def forget(folder: Path) -> None:
    """Перечитать запись при следующем поиске (правка спикеров из окна): не
    полагаемся на время изменения файла — две записи подряд его не меняют."""
    _CACHE.forget(folder)


def _cards(root: Path):
    """Карточки записей библиотеки от свежих к старым (как `library.listing`).
    Время папки — отдельным stat, а не из обхода каталога: на Windows запись
    каталога о вложенной папке обновляется с запаздыванием."""
    try:
        with os.scandir(root) as it:
            folders = sorted(((e.name, e.path) for e in it if e.is_dir()), reverse=True)
    except OSError:
        return
    _CACHE.forget_except({path for _, path in folders})
    library._forget_heads(root, {name for name, _ in folders})
    for _, path in folders:
        card, stamp = _CACHE.card_stamped(path)
        if card is not None:
            yield card, stamp


def cards(root: Path) -> list[dict]:
    """Все карточки библиотеки от свежих к старым — из кеша (meta.json и
    транскрипт перечитываются, только когда меняются)."""
    return [card for card, _ in _cards(Path(root))]


def searchable(q: str | None) -> bool:
    """Ищет ли поиск по такому запросу (не короче MIN_QUERY, есть что искать)."""
    return len(nfc(q or "").strip()) >= MIN_QUERY and not parse_query(q).empty


def title_ranges(title: str, q: Query) -> list[list[int]]:
    """Что подсветить в названии (`nfc(title)`), в единицах UTF-16: слова по
    основам ключевых и фразы — каждое найденное, даже если всё название
    запросу не отвечает (встреча нашлась по тексту)."""
    tokens = tokenize(title)
    ranges: list[list[int]] = []
    for phrase in q.phrases:
        n = len(phrase)
        for i in range(len(tokens) - n + 1):
            if all(tokens[i + j][0] == phrase[j] for j in range(n)):
                ranges.append([tokens[i][1], tokens[i + n - 1][2]])
    ranges += [[t[1], t[2]] for t in tokens if any(t[0].startswith(s) for s in q.stems)]
    return [[_utf16(title, a), _utf16(title, b)] for a, b in _merge(ranges)]


def _search_doc(ref: tuple, doc: _Doc, plan: _Plan) -> tuple[int, list[dict]]:
    """Сколько реплик встречи подходит и фрагменты первых MAX_HITS."""
    q = plan.q
    if any(s not in doc.norm for s in plan.needles):
        return 0, []  # отсев встречи целиком: одна проверка подстроки
    allowed = None
    if q.speakers:
        allowed = {s for s in set(doc.speakers) if s and speaker_matches(s, q)}
        if not allowed:
            return 0, []
    total, hits = 0, []
    for i in plan.candidates(doc):
        speaker = doc.speakers[i]
        if allowed is not None and speaker not in allowed:
            continue
        if doc.texts is None:
            if not plan.matches(doc, i):
                continue
            if len(hits) >= MAX_HITS:
                total += 1
                continue
        text = doc.text(i)
        ranges = match_tokens(_CACHE.tokens(ref, i, text), q)
        if ranges is None:
            continue
        total += 1
        if len(hits) < MAX_HITS:
            snip, marks = snippet(text, ranges)
            hits.append({"t": doc.starts[i], "speaker": speaker, "snippet": snip, "ranges": marks})
    return total, hits


def search_library(root: Path, q: str, limit: int = 200, keep=None,
                   title_only: bool = False) -> list[dict]:
    """Записи, где запрос нашёлся в репликах или в названии, от свежих к
    старым: карточка записи и `date`, `hits` (до MAX_HITS: время реплики,
    спикер, фрагмент, подсветка), `total` — сколько реплик подошло,
    `title_match` — нашлось в названии, `title_ranges` — что в нём подсветить
    (UTF-16). Пустой запрос или короче MIN_QUERY символов — пустой ответ.
    `keep(card)` — фильтр по карточке (meet.library_filter) до поиска и до
    `limit`: старые записи нужной категории не теряются за свежими.
    `title_only` — только в названиях (`in=title`), транскрипты не читаются."""
    if not searchable(q):
        return []
    query = parse_query(q)
    plan = _Plan(query)
    found = []
    for card, stamp in _cards(Path(root)):
        if keep is not None and not keep(card):
            continue
        hits, total = [], 0
        if card.get("has_transcript") and not title_only:
            got = _CACHE.doc(card["path"], stamp)
            if got is not None:
                total, hits = _search_doc(*got, plan)
        title = nfc(card.get("title") or "")
        title_match = bool(title) and query.has_text and not query.speakers and (
            match_tokens(tokenize(title), query) is not None)
        if total or title_match:
            found.append({**card, "date": (card.get("started_at") or "")[:10] or None,
                          "hits": hits, "total": total, "title_match": title_match,
                          "title_ranges": title_ranges(title, query) if title else []})
            if len(found) >= limit:
                break
    return found
