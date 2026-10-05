"""Задачи Jira, названные во встрече: «ORION 2122», «орион двадцать один
двадцать два», «в баге 4452» → ключи ORION-2122, ORION-4452.

Человек не произносит «ORION дефис две тысячи сто двадцать два» так, как
ключ пишется: он говорит название проекта, как привык, и номер — цифрами или
словами. Здесь — детерминированный слой (без модели), единственный источник
ссылок для окна: резидент считает их, отдавая карточку записи (`for_recording`).

* **Проекты** — из настроек (`integrations.jira_projects`): ключ латиницей
  («ORION», «orion», «ORI ON»), русская запись (meet.translit: «орион», с
  падежами — «в орионе»), чтение по буквам («эс пи ар», «кей дев» для KDEV) и
  свои варианты человека («орайон»). «э» и «е», «ё» и «е» не различаются.
* **Номер** — цифры (2122, «2 122», «21-22», «21 22»), русские и английские
  числительные, в том числе парами («двадцать один двадцать два»,
  «сорок четыре пятьдесят два» → 2122, 4452). Больше шести цифр — не номер.
* **Шаблоны.** `<проект> [№|номер|-]? <номер>` — номер сразу за проектом
  (или за «номер»/«№»), дальше — перечисление («орион 2122 и 2123»); номер
  словами меньше десяти — не номер («орион номер один в рейтинге»). Номер
  после слова-признака («баг», «тикет», «задача», «issue», «джира»…) не
  дальше CONTEXT_WORDS слов, от трёх цифр или после «номер»/«№», — в проекте
  по умолчанию, если он задан; иначе такое упоминание остаётся анализу
  встречи (слой модели, meet.analysis `issues`).
* **Не номер:** за числом единица, дата, время суток, сумма, оценка или
  счёт («2 122 рубля», «15 марта», «10 утра», «300 тысяч», «13 поинтов»,
  «двадцать два бага»), порядковое («двадцать первого»), время и дроби
  («21:22», «2,5»), процент; перед свободным числом — предлог («в 2122
  году», «к 15»).
* Ключ, написанный текстом («ORION-2122»), — по шаблону из настроек
  (`Integrations.jira_literal`): источник "literal".

Ссылки считаются по реплике окна (подряд идущие сегменты одного спикера с
паузой меньше 2 с — как mergeTurns в окне): «орион» в конце одного сегмента и
номер в начале следующего — одна ссылка. Позиции — в символах UTF-16 (как
строки JavaScript) в тексте сегмента в NFC; ссылка, начатая в сегменте, может
закончиться в следующем сегменте той же реплики. Окно сверяет `spoken` с
текстом и, если текст успели поправить, ищет эти слова заново.
"""

import bisect
import itertools
import re
import unicodedata
from dataclasses import dataclass, field

from meet import translit

MAX_DIGITS = 6
# Номер после слова-признака («в баге 4452») — не дальше стольких слов.
CONTEXT_WORDS = 3
# Номер после слова-признака без проекта — от трёх цифр, если не сказано
# «номер»/«№»: «задача 18 переходит дальше» — не задача.
MIN_CONTEXT = 100
# Номер словами меньше десяти — не номер задачи даже после «номер»: «задача
# номер один — стабилизировать релиз».
MIN_WORDS = 10
# Перечисление после номера («2122 и 2123») — от двух цифр.
MIN_LIST = 10
# Реплика окна: сегменты одного спикера с паузой меньше (как GAP_S в lib/speakers.ts).
TURN_GAP_S = 2.0
# Сколько вариантов произношения строить на проект (сверх своих).
_LATIN_SPLIT_MAX = 6

_TOKEN = re.compile(r"[0-9]+|[^\W\d_]+")
# Между частями названия проекта и между словами числа — пробелы и дефис.
_NAME_GAP = re.compile(r"[\s\-‐‑_]*")
_WORD_GAP = re.compile(r"[\s\-‐‑]*")
# Между проектом и номером: пробелы, дефис, тире, двоеточие, «№», «#».
_KEY_GAP = re.compile(r"[\s\-‐‑–—:#№]*")
_HYPHEN_GAP = re.compile(r"\s*[\-‐‑–]\s*")
_SPACE_GAP = re.compile(r"[    ]+")
_SENTENCE = re.compile(r"[.!?;…\n]")
_TIME_OR_FRACTION = re.compile(r"[:.,][0-9]")
_PERCENT = re.compile(r"\s*[%‰]")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def fold(word: str) -> str:
    """Слово для сравнения: строчные, «ё» и «э» — как «е» (длина та же)."""
    return word.lower().replace("ё", "е").replace("э", "е")


@dataclass(frozen=True)
class Tok:
    word: str  # fold(...)
    start: int
    end: int
    digit: bool


def tokens(text: str) -> list[Tok]:
    return [Tok(fold(m.group()), m.start(), m.end(), m.group()[0].isdigit())
            for m in _TOKEN.finditer(text)]


# --- числа ----------------------------------------------------------------------

_UNITS = {
    "один": 1, "одна": 1, "одно": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9,
}
_ZERO = {"ноль", "нуль", "zero"}
_TEENS = {
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_HUNDREDS = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}
_HUNDRED = {"hundred"}
_THOUSAND = {"тысяча", "тысячи", "тысяч", "тысячу", "thousand"}
NUMBER_WORDS = frozenset(_UNITS) | _ZERO | frozenset(_TEENS) | frozenset(_TENS) \
    | frozenset(_HUNDREDS) | _HUNDRED | _THOUSAND


def _cardinal(toks: list[Tok], k: int, text: str) -> tuple[int, int] | None:
    """Одно число словами с позиции k: «две тысячи сто двадцать два» → 2122.
    Следующее слово, которое не продолжает число по правилам («двадцать
    один | двадцать два»), начинает следующее число. → (значение, конец)."""
    h = t = u = None
    teen = False
    thousands = None
    n, used = k, 0
    while n < len(toks):
        tok = toks[n]
        if n > k and not _WORD_GAP.fullmatch(text, toks[n - 1].end, tok.start):
            break
        w = tok.word
        if tok.digit:
            break
        if w in _ZERO:
            if used:
                break
            return 0, n + 1
        if w in _HUNDREDS:
            if h is not None or t is not None or u is not None:
                break
            h = _HUNDREDS[w]
        elif w in _HUNDRED:
            if h is not None or t is not None or u is None or teen:
                break
            h, u = u * 100, None
        elif w in _TENS:
            if t is not None or u is not None:
                break
            t = _TENS[w]
        elif w in _TEENS:
            if t is not None or u is not None:
                break
            u, teen = _TEENS[w], True
        elif w in _UNITS:
            if u is not None:
                break
            u = _UNITS[w]
        elif w in _THOUSAND:
            if thousands is not None:
                break
            thousands = ((h or 0) + (t or 0) + (u or 0)) or 1
            h = t = u = None
            teen = False
        elif w == "and" and h is not None and t is None and u is None and n + 1 < len(toks) \
                and toks[n + 1].word in (_TENS.keys() | _TEENS.keys() | _UNITS.keys()):
            pass
        else:
            break
        used += 1
        n += 1
    if not used:
        return None
    return (thousands or 0) * 1000 + (h or 0) + (t or 0) + (u or 0), n


@dataclass(frozen=True)
class Number:
    digits: str  # "" — похоже на число, но не номер задачи (длинное, с нуля)
    end: int  # индекс токена после числа
    words: bool  # сказано словами


def number_at(toks: list[Tok], j: int, text: str) -> Number | None:
    """Номер с токена j: цифры («2122», «2 122», «21-22», «21 22») или слова
    («двадцать один двадцать два»). None — здесь не число."""
    if j >= len(toks):
        return None
    if toks[j].digit:
        groups = [text[toks[j].start:toks[j].end]]
        n, mode = j + 1, None
        while n < len(toks) and toks[n].digit:
            gap = text[toks[n - 1].end:toks[n].start]
            group = text[toks[n].start:toks[n].end]
            if _HYPHEN_GAP.fullmatch(gap):
                pass
            elif _SPACE_GAP.fullmatch(gap):
                # «2 122» — разряды; «21 22» — номер парами.
                if mode in (None, "thousands") and len(groups[0]) <= 3 and len(group) == 3 \
                        and all(len(g) == 3 for g in groups[1:]):
                    mode = "thousands"
                elif mode is None and len(groups) == 1 and len(groups[0]) == 2 and len(group) == 2:
                    mode = "pairs"
                else:
                    break
            else:
                break
            groups.append(group)
            n += 1
        digits, words = "".join(groups), False
    else:
        parts, n = [], j
        while n < len(toks):
            if n > j and not _WORD_GAP.fullmatch(text, toks[n - 1].end, toks[n].start):
                break
            got = _cardinal(toks, n, text)
            if got is None:
                break
            value, n = got
            parts.append(str(value))
        if not parts:
            return None
        digits, words = "".join(parts), True
    if len(digits) > MAX_DIGITS or digits.startswith("0"):
        return Number("", n, words)
    return Number(digits, n, words)


# Единицы и счёт после числа: «2 122 рубля», «2122 года», «сорок четыре минуты».
_UNIT = re.compile(
    r"(?:год(?:а|у|ом|ов|ы)?$|лет$|рубл|руб$|доллар|бакс|евро$|минут|мин$|секунд|сек$|"
    r"час(?:а|ов|у|ик\w*)?$|сут(?:ки|ок|кам)$|дн(?:я|ей)$|день$|недел[ьяиюе]|месяц|мес$|квартал|"
    r"процент|штук|шт$|человек|чел$|люд(?:ей|и|ям)$|раз(?:а)?$|километр|км$|метр(?:а|ов)?$|"
    r"гигабайт|мегабайт|байт|гиг(?:а|ов)?$|мег(?:а|ов)?$|строк(?:а|и|у)?$|пользовател|юзер|"
    r"клиент(?:а|ов)$|пункт(?:а|ов)?$|страниц|файл(?:а|ов)?$|сервер(?:а|ов)?$|ядер$|ядра$|тыс$|"
    r"миллион|млн$|миллиард|млрд$|верси(?:и|й|я)$|сотрудник|участник|запис(?:ей|и|ь)$|"
    r"сообщени(?:й|я)$|коммит(?:а|ов)$|шаг(?:а|ов)$|этап(?:а|ов)$|попыт(?:ка|ки|ок)$|"
    r"раунд(?:а|ов)$|копе(?:ек|йки|йка)$|years?$|months?$|weeks?$|days?$|hours?$|minutes?$|"
    r"seconds?$|percent$|users?$|rubles?$|dollars?$|times?$|items?$|points?$|pages?$|files?$|"
    r"lines?$|people$|persons?$|gb$|mb$|kb$|ms$|мс$|гб$|мб$|кб$|тб$|г$|ч$|"
    # Даты: «15 марта», «20 мая», «15 числа», «янв».
    r"январ|феврал|март[аеу]?$|апрел|ма[йяюе]$|июн[ьяюе]$|июл[ьяюе]$|август|сентябр|октябр|ноябр|"
    r"декабр|янв$|фев$|мар$|апр$|авг$|сент?$|окт$|нояб?$|дек$|числ[оау]$|"
    r"(?:january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|"
    r"sept|sep|october|oct|november|nov|december|dec)$|"
    # Время суток: «10 утра», «7 вечера», «3 ночи».
    r"утра$|вечера$|ночи$|пополудни$|am$|pm$|"
    # Суммы и счёт: «300 тысяч», «две сотни», «десяток», «долл.».
    r"тысяч\w*$|сот(?:ни|ен|ня|ню)$|десят(?:ок|ка|ков|ки)$|долл|"
    # Оценки: «13 поинтов», «5 очков», «8 баллов».
    r"поинт\w*$|очк(?:о|а|ов)$|балл\w*$|sp$)")
# Счётные существительные: «двадцать два бага», «пять тикетов»; именительный
# («двадцать один баг») — только после числа на 1 (кроме 11).
_COUNT_GEN = re.compile(r"(?:бага|багов|задачи|задач|тикета|тикетов|таска|тасков|ишью|issues|"
                        r"tickets|bugs|tasks)$")
_COUNT_NOM = re.compile(r"(?:баг|задача|тикет|таск|issue|ticket|bug|task)$")
_ORDINAL = re.compile(
    r"(?:перв|втор|трет|четверт|пят|шест|седьм|восьм|девят|десят|\w+надцат|двадцат|тридцат|"
    r"сороков|\w+десят|девяност|\w*сот|тысячн)"
    r"(?:ый|ий|ой|ая|ое|ого|ому|ым|ом|ую|ые|ых|ыми|ья|ье|ьего|ьему|ьей|ью|ьи|ьих|ей|его|ему|ем|ем)$"
    r"|(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|"
    r"\w+teenth|\w+tieth|hundredth|thousandth)$")
_ORDINAL_SUFFIX = {"го", "й", "я", "е", "х", "ти", "ми", "ый", "ой", "ая", "ое", "ого", "му", "м",
                   "ю", "th", "st", "nd", "rd"}
# Предлог перед свободным числом: «в 2122 году», «к 15», «через 20».
_PREPS = {
    "с", "со", "до", "к", "ко", "по", "в", "во", "на", "за", "через", "от", "из", "около", "после",
    "перед", "при", "про", "о", "об", "примерно", "почти", "всего", "минус", "плюс", "чем", "уже",
    "at", "in", "on", "by", "for", "from", "to", "until", "about", "after", "before", "since", "of",
    "around", "than",
}
# Перечисление номеров: «2122 и 2123», «2122, 2123».
_LIST_WORDS = {"и", "или", "and", "or"}
_LIST_GAP = re.compile(r"[\s,]*")
# Слова, после которых номер — явно номер: «номер», «№» (знак — не слово).
_EXPLICIT = {"номер", "номером", "номеру", "номера", "number", "num", "no", "n"}
# Слова-признаки задачи без проекта: «в баге 4452», «тикет 4452», «issue 4452».
_TRIGGER = re.compile(
    r"(?:баг|бага|багу|баге|багом|тикет|тикета|тикету|тикете|тикетом|задача|задачи|задаче|"
    r"задачу|задачей|задачка|задачке|задачку|таск|таска|таске|таску|таском|ишью|issue|ticket|bug|"
    r"task|джира|джире|джиру|джиры|jira|епик|епике|епика|стори|сторя|story|инцидент|инциденте|"
    r"инцидента)$")
# Между словом-признаком и номером — только такие слова: «в баге с номером 4452».
_FILLERS = {"номер", "номером", "номеру", "под", "с", "со", "n", "no", "number", "num", "вот",
            "ето", "ну"}


def _after_rejects(toks: list[Tok], num: Number, text: str) -> bool:
    """За числом — единица, счёт, порядковое, время, дробь или процент."""
    end = toks[num.end - 1].end
    if _TIME_OR_FRACTION.match(text, end) or _PERCENT.match(text, end):
        return True
    if num.end >= len(toks):
        return False
    gap = text[end:toks[num.end].start]
    if not gap:
        return True  # «12a», «2122й»: число — часть слова

    if _SENTENCE.search(gap) or "," in gap:
        return False
    w = toks[num.end].word
    if any(c in gap for c in "-‐‑") and not gap.strip(" -‐‑") and w in _ORDINAL_SUFFIX:
        return True  # «21-го»
    if _UNIT.match(w) or _ORDINAL.match(w) or _COUNT_GEN.match(w):
        return True
    if _COUNT_NOM.match(w):
        last = num.digits[-2:]
        return last.endswith("1") and last != "11"
    return False


def _prep_before(toks: list[Tok], p: int, text: str) -> bool:
    return p > 0 and toks[p - 1].word in _PREPS and not _SENTENCE.search(text, toks[p - 1].end, toks[p].start)


def _explicit_before(toks: list[Tok], p: int, text: str) -> bool:
    if p > 0 and toks[p - 1].word in _EXPLICIT:
        return True
    start = toks[p - 1].end if p > 0 else 0
    return any(c in text[start:toks[p].start] for c in "№#")


# --- проекты --------------------------------------------------------------------

# Как читают латинские буквы по-русски (после fold: «э» → «е»).
_LETTER_RU = {
    "a": ("ей", "а"), "b": ("би", "бе"), "c": ("си", "це"), "d": ("ди", "де"), "e": ("и", "е"),
    "f": ("еф",), "g": ("джи", "ге", "же"), "h": ("ейч", "аш", "ейдж"), "i": ("ай", "и"),
    "j": ("джей", "жи"), "k": ("кей", "ка"), "l": ("ел", "ель"), "m": ("ем",), "n": ("ен",),
    "o": ("оу", "о"), "p": ("пи", "пе"), "q": ("кью",), "r": ("ар", "ер"), "s": ("ес",),
    "t": ("ти", "те"), "u": ("ю", "у"), "v": ("ви", "ве"), "w": ("дабл ю", "дубль ве", "даблью"),
    "x": ("икс",), "y": ("уай", "игрек"), "z": ("зед", "зет"),
}
# Падежные окончания русской записи проекта: «в орионе», «по ориону».
_CASE_ENDINGS = ("а", "у", "е", "ом", "ой", "ы", "и", "ов", "ам", "ами", "ах", "ем", "ю", "я")


@dataclass(frozen=True)
class Variant:
    """Вариант названия проекта: слоты подряд, у слота — альтернативы (кортеж
    слов). `declines` — у последнего слова бывают падежные окончания."""

    key: str
    slots: tuple[tuple[tuple[str, ...], ...], ...]
    declines: bool


def _alts(*words: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(fold(w).split()) for w in words)


def _cyrillic(word: str) -> bool:
    return any("а" <= c <= "я" or c == "ё" for c in word.lower())


def project_variants(key: str, aliases=()) -> list[Variant]:
    """Как могут сказать (и распознать) ключ проекта: латиница целиком и
    по частям, русская запись, по буквам, с буквами в начале и чтением
    остального («кей дев»), слитно («кейдев») и свои варианты."""
    out: list[Variant] = []
    low = key.lower()
    # Ключ как пишется — по словам токенизатора: «OPS2» → «ops», «2».
    out.append(Variant(key, tuple(_alts(m.group()) for m in _TOKEN.finditer(low)), False))
    if re.fullmatch(r"[a-z]+", low):
        if len(low) <= _LATIN_SPLIT_MAX:
            for cuts in itertools.product((False, True), repeat=len(low) - 1):
                chunks, cur = [], low[0]
                for ch, cut in zip(low[1:], cuts):
                    if cut:
                        chunks.append(cur)
                        cur = ch
                    else:
                        cur += ch
                chunks.append(cur)
                if len(chunks) > 1:
                    out.append(Variant(key, tuple(_alts(c) for c in chunks), False))
        for k in range(len(low) + 1):
            head, rest = low[:k], low[k:]
            if len(rest) == 1:
                continue
            # Буква одним звуком («а», «и») — только в ключе от трёх букв:
            # у «AI» по буквам вышло бы «а и».
            spelled = tuple(_alts(*(n for n in _LETTER_RU[c] if len(low) >= 3 or len(n) > 1))
                            for c in head)
            reads = [fold(r) for r in translit.word_variants(rest)] if rest else []
            reads = [r for r in dict.fromkeys(reads) if r]
            if not head:
                # Одно слово: не короче трёх букв и не обычное русское слово.
                reads = [r for r in reads if len(r) >= 3 and not translit._common(r)]
                if reads:
                    out.append(Variant(key, (_alts(*reads),), True))
                continue
            if rest:
                if reads:
                    out.append(Variant(key, (*spelled, _alts(*reads)), True))
                    joined = "".join(_LETTER_RU[c][0].replace(" ", "") for c in head)
                    out.append(Variant(key, (_alts(*(joined + r for r in reads)),), True))
            else:
                out.append(Variant(key, spelled, False))
                out.append(Variant(key, (_alts("".join(_LETTER_RU[c][0].replace(" ", "") for c in head)),),
                                   False))
    for alias in aliases:
        words = [fold(m.group()) for m in _TOKEN.finditer(nfc(str(alias)))]
        if words:
            out.append(Variant(key, tuple(_alts(w) for w in words), _cyrillic(words[-1])))
    seen, unique = set(), []
    for v in out:
        if (v.slots, v.declines) not in seen:
            seen.add((v.slots, v.declines))
            unique.append(v)
    return unique


@dataclass
class Spec:
    """Что искать: проекты (их варианты), проект по умолчанию, ключ текстом."""

    projects: tuple[str, ...] = ()
    default: str = ""
    literal: re.Pattern | None = None
    _index: dict = field(default_factory=dict)

    @classmethod
    def build(cls, projects=(), default: str = "", literal: str | None = None) -> "Spec":
        """`projects` — [(ключ, [варианты])] или объекты с .key/.aliases."""
        pairs = []
        for p in projects:
            key, aliases = (p.key, p.aliases) if hasattr(p, "key") else (p[0], p[1] if len(p) > 1 else ())
            pairs.append((key, tuple(aliases)))
        spec = cls(projects=tuple(k for k, _ in pairs),
                   default=default if default in {k for k, _ in pairs} else "")
        if literal:
            try:
                spec.literal = re.compile(rf"(?<![\w-])(?:{literal})(?!\w)")
            except re.error:
                spec.literal = None
        for key, aliases in pairs:
            for v in project_variants(key, aliases):
                for alt in v.slots[0]:
                    spec._index.setdefault(alt[0], []).append(v)
        return spec

    def candidates(self, word: str) -> list[Variant]:
        found = list(self._index.get(word, ()))
        for end in _CASE_ENDINGS:
            if word.endswith(end) and len(word) > len(end) + 2:
                found += [v for v in self._index.get(word[:-len(end)], ())
                          if v.declines and len(v.slots) == 1 and len(v.slots[0][0]) == 1]
        return found


def _match(v: Variant, toks: list[Tok], i: int, text: str) -> int | None:
    """Вариант с токена i → индекс токена после него или None."""
    n = i
    for si, slot in enumerate(v.slots):
        last = si == len(v.slots) - 1
        for alt in slot:
            if n + len(alt) > len(toks):
                continue
            ok = True
            for ai, word in enumerate(alt):
                tok = toks[n + ai]
                if n + ai > i and not _NAME_GAP.fullmatch(text, toks[n + ai - 1].end, tok.start):
                    ok = False
                    break
                if tok.word == word:
                    continue
                if last and ai == len(alt) - 1 and v.declines and tok.word.startswith(word) \
                        and tok.word[len(word):] in _CASE_ENDINGS:
                    continue
                ok = False
                break
            if ok:
                n += len(alt)
                break
        else:
            return None
    return n


@dataclass(frozen=True)
class Ref:
    start: int
    end: int
    key: str
    source: str  # literal | spoken | context | agent
    # Начало фразы для итогов и наблюдений: у номера по слову-признаку — само
    # слово («баге 4452»), голое число там ссылкой не становится.
    lead: int | None = None


def _mentions(toks: list[Tok], text: str, spec: Spec) -> list[tuple[int, int, str]]:
    """Упоминания проектов: (первый токен, токен после, ключ) — самые длинные, без наложений."""
    out, i = [], 0
    while i < len(toks):
        best = None
        if not toks[i].digit:
            for v in spec.candidates(toks[i].word):
                end = _match(v, toks, i, text)
                if end is not None and (best is None or end > best[0]):
                    best = (end, v.key)
        if best:
            out.append((i, best[0], best[1]))
            i = best[0]
        else:
            i += 1
    return out


def find(text: str, spec: Spec) -> list[Ref]:
    """Ссылки на задачи в тексте (NFC): [Ref] по порядку, без наложений.
    Позиции — в символах Python."""
    text = nfc(text or "")
    if not text:
        return []
    toks = tokens(text)
    literal: list[Ref] = []
    if spec.literal is not None:
        literal = [Ref(m.start(), m.end(), m.group(), "literal")
                   for m in spec.literal.finditer(text) if m.group()]
    spoken: list[Ref] = []
    mentions = _mentions(toks, text, spec)

    def ref(a: int, num: Number, key: str, source: str, lead: int | None = None) -> Ref:
        return Ref(toks[a].start, toks[num.end - 1].end, f"{key}-{int(num.digits)}", source, lead)

    def listed(p: int, key: str) -> None:
        """Перечисление сразу за номером: «2122 и 2123», «2122, 2123»."""
        while p < len(toks):
            q = p + 1 if toks[p].word in _LIST_WORDS else p
            if q >= len(toks) or _SENTENCE.search(text, toks[p - 1].end, toks[q].start) \
                    or not _LIST_GAP.fullmatch(text, toks[p - 1].end, toks[p].start):
                return
            num = number_at(toks, q, text)
            if num is None or not num.digits or int(num.digits) < MIN_LIST \
                    or _after_rejects(toks, num, text):
                return
            spoken.append(ref(q, num, key, "spoken"))
            p = num.end

    for first, after, key in mentions:
        # Номер — сразу за проектом или за «номер»/«№»: «орион 2122», «орион номер 2122».
        j = after
        while j < len(toks) and toks[j].word in _EXPLICIT \
                and _KEY_GAP.fullmatch(text, toks[j - 1].end, toks[j].start):
            j += 1
        if j >= len(toks) or not _KEY_GAP.fullmatch(text, toks[j - 1].end, toks[j].start):
            continue
        num = number_at(toks, j, text)
        if num is None or not num.digits or _after_rejects(toks, num, text):
            continue
        # «орион один из тикетов», «орион номер один в рейтинге»: номер словами
        # меньше десяти — не задача.
        if num.words and int(num.digits) < MIN_WORDS:
            continue
        spoken.append(ref(first, num, key, "spoken"))
        listed(num.end, key)

    context: list[Ref] = []
    if spec.default:
        for t, tok in enumerate(toks):
            if not _TRIGGER.match(tok.word):
                continue
            p = t + 1
            while p < len(toks) and p - t <= CONTEXT_WORDS:
                if _SENTENCE.search(text, toks[p - 1].end, toks[p].start):
                    break
                num = number_at(toks, p, text)
                if num is not None:
                    value = int(num.digits) if num.digits else 0
                    explicit = _explicit_before(toks, p, text) and not (num.words and value < MIN_WORDS)
                    if num.digits and not _prep_before(toks, p, text) \
                            and not _after_rejects(toks, num, text) \
                            and (value >= MIN_CONTEXT or explicit):
                        context.append(ref(p, num, spec.default, "context", lead=tok.start))
                    break
                if toks[p].word not in _FILLERS:
                    break
                p += 1

    # На наложении побеждает раньше найденное; принятые — по порядку начала.
    starts: list[int] = []
    out: list[Ref] = []
    for r in literal + spoken + context:
        i = bisect.bisect_right(starts, r.start)
        if (i > 0 and out[i - 1].end > r.start) or (i < len(starts) and starts[i] < r.end):
            continue
        starts.insert(i, r.start)
        out.insert(i, r)
    return out


# --- реплики, анализ, окно ----------------------------------------------------------


def _u16(text: str, i: int) -> int:
    """Позиция в символах Python → в единицах UTF-16 (строки JavaScript)."""
    return len(text[:i].encode("utf-16-le")) // 2


def turns_of(segments: list) -> list[list[int]]:
    """Реплики окна — номера сегментов (как mergeTurns в lib/speakers.ts):
    подряд один спикер, пауза меньше TURN_GAP_S, та же пометка
    `library.turn_mark` (микрофон и звонок, голос под вопросом — разные
    реплики); отметки перерыва — отдельно."""
    from meet import library

    out: list[list[int]] = []
    cur, speaker, end, mark = None, None, 0.0, None
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        if seg.get("kind") == "break":
            out.append([i])
            cur = None
            continue
        who = seg.get("speaker") or ""
        try:
            start, stop = float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)
        except (TypeError, ValueError):
            start = stop = 0.0
        here = library.turn_mark(seg)
        if cur is not None and who == speaker and here == mark and start - end < TURN_GAP_S:
            cur.append(i)
            end = max(end, stop)
        else:
            cur, speaker, end, mark = [i], who, stop, here
            out.append(cur)
    return out


def find_phrase(text: str, phrase: str) -> list[tuple[int, int]]:
    """Где в тексте (NFC) звучат слова `phrase` подряд — [начало, конец) без
    наложений. Регистр, «ё/э» и знаки между словами не важны: модель
    переписывает реплику с другой пунктуацией."""
    want = [t.word for t in tokens(nfc(phrase or ""))]
    if not want:
        return []
    toks = tokens(text)
    out, i = [], 0
    while i + len(want) <= len(toks):
        if [t.word for t in toks[i:i + len(want)]] == want:
            out.append((toks[i].start, toks[i + len(want) - 1].end))
            i += len(want)
        else:
            i += 1
    return out


def _seg_text(seg) -> str:
    return nfc(str(seg.get("text") or "")) if isinstance(seg, dict) else ""


def segment_refs(segments: list, spec: Spec, issues=()) -> list[dict]:
    """Ссылки по сегментам для окна: [{segment, start, end, key, source,
    spoken}]. `issues` — из анализа (meet.analysis): добавляются там, где
    детерминированный слой ничего не нашёл (он побеждает на наложении)."""
    out: list[dict] = []
    turn_of: dict[int, tuple[list[int], list[int], str, bool]] = {}
    found: dict[int, list[tuple[int, int]]] = {}
    for idx in turns_of(segments):
        if any(isinstance(segments[i], dict) and segments[i].get("kind") == "break" for i in idx):
            continue
        texts = [_seg_text(segments[i]) for i in idx]
        offsets, at = [], 0
        for t in texts:
            offsets.append(at)
            at += len(t) + 1
        text = " ".join(texts)
        # Без символов вне BMP позиции UTF-16 те же, что в Python: не перекодируем.
        info = (idx, offsets, text, max(text, default=" ") <= "\uffff")
        for i in idx:
            turn_of[i] = info
        spans = found.setdefault(idx[0], [])
        for r in find(text, spec):
            spans.append((r.start, r.end))
            out.append(_span(info, r))
    for issue in issues or ():
        key = issue.get("key")
        for i in issue.get("segments") or ():
            if i not in turn_of:
                continue
            idx, offsets = turn_of[i][0], turn_of[i][1]
            k = idx.index(i)
            for a, b in find_phrase(_seg_text(segments[i]), issue.get("spoken") or ""):
                a, b = a + offsets[k], b + offsets[k]
                spans = found.setdefault(idx[0], [])
                if any(a < y and x < b for x, y in spans):
                    continue
                spans.append((a, b))
                out.append(_span(turn_of[i], Ref(a, b, key, "agent")))
    return sorted(out, key=lambda r: (r["segment"], r["start"]))


def _span(info, r: Ref) -> dict:
    idx, offsets, text, bmp = info
    k = bisect.bisect_right(offsets, r.start) - 1
    u16 = (lambda i: i) if bmp else (lambda i: _u16(text, i))
    base = u16(offsets[k])
    return {"segment": idx[k], "start": u16(r.start) - base, "end": u16(r.end) - base,
            "key": r.key, "source": r.source, "spoken": text[r.start:r.end]}


def phrases(texts, spec: Spec) -> list[dict]:
    """Ссылки в итогах и наблюдениях — для окна фразами: [{text, key,
    source}] без повторов. Окно делает ссылкой каждое вхождение фразы."""
    out: dict[tuple[str, str], dict] = {}
    for text in texts:
        for line in str(text or "").splitlines():
            line = nfc(line)
            for r in find(line, spec):
                said = line[r.start if r.lead is None else r.lead:r.end]
                out.setdefault((said, r.key), {"text": said, "key": r.key, "source": r.source})
    return list(out.values())


def spec_of(cfg) -> Spec | None:
    """Что искать — по настройкам; None — ссылки выключены или адреса нет."""
    if not getattr(cfg.transcript_view, "jira", True):
        return None
    integrations = cfg.integrations
    if not integrations.jira_base_url:
        return None
    return Spec.build(integrations.jira_projects, integrations.jira_default_project,
                      integrations.jira_literal())


def analysis_issues(doc: dict | None, segments: list, cfg) -> list[dict]:
    """Ссылки модели из analysis.json — если анализ про эту расшифровку (то же
    число сегментов), часть включена и ключ — из проектов в настройках."""
    if not doc or not cfg.analysis.issues or len(segments) != doc.get("segments", len(segments)):
        return []
    keys = {p.key for p in cfg.integrations.jira_projects}
    out = []
    for item in doc.get("issues") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key.rpartition("-")[0] in keys and isinstance(item.get("spoken"), str):
            out.append(item)
    return out


def for_recording(data: dict | None, cfg, *, analysis_doc: dict | None = None,
                  summary: str | None = None) -> dict | None:
    """Ссылки для карточки записи: {"refs": по сегментам, "phrases": в итогах и
    наблюдениях}. None — ссылки выключены."""
    spec = spec_of(cfg)
    if spec is None:
        return None
    segments = (data or {}).get("segments") or []
    refs = segment_refs(segments, spec, analysis_issues(analysis_doc, segments, cfg))
    texts = [summary or ""]
    for item in (analysis_doc or {}).get("insights") or []:
        if isinstance(item, dict):
            texts += [str(item.get("text") or ""), str(item.get("why") or "")]
    return {"refs": refs, "phrases": phrases(texts, spec)}
