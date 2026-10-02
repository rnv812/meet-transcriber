"""Поводы для внеочередной подсказки: вопрос на встрече, обращение к владельцу.

Тикер подсказок зовёт модель по ритму речи (раз в ~20 с сказанного), но
вопрос, заданный на встрече, ждать ритма не должен: подсказка «что ответить»
нужна, пока собеседник ждёт ответа. Здесь — простые проверяемые правила
по одной реплике (русский язык), без модели:

* `question` — реплика не владельца кончается вопросительным знаком;
* `addressed` — в реплике не владельца прозвучало имя владельца (с
  падежными окончаниями: «Кузьма», «Кузьму», «Кузьме») или «вы/ты» вместе
  с вопросительным словом («Вы сможете…», «А ты когда…»).

Реплики самого владельца поводом не бывают: свои вопросы он слышит сам.
Распознавание речи неидеально, поэтому правило — повод спросить модель, а
не вывод: модель сама решает, есть ли что подсказать.
"""

import re

QUESTION = "question"
ADDRESSED = "addressed"

# Слова второго лица (обращение к собеседнику на «вы» и на «ты»).
SECOND_PERSON = frozenset(
    "вы вас вам вами ваш ваша ваше ваши вашего вашей вашим ваших вашу "
    "ты тебя тебе тобой твой твоя твоё твое твои твоего твоей твоим твоих твою".split())

# Вопросительные слова и частицы, по которым «вы/ты» — это вопрос к человеку.
QUESTION_WORDS = frozenset(
    "как что когда кто где куда откуда почему зачем сколько какой какая какое какие "
    "каким какую чей чья ли можете сможете могли могли бы готовы успеете знаете "
    "помните подскажете скажете расскажете думаете считаете согласны планируете "
    "можешь сможешь готов успеешь знаешь помнишь подскажешь скажешь думаешь "
    "считаешь согласен планируешь".split())

# Имя короче — без падежного «хвоста» (иначе «Ян» совпадёт с чем угодно).
STEM_MIN = 4

_WORD = re.compile(r"[а-яёa-z]+", re.I)


def _words(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in _WORD.findall(text or "")]


def owner_names(name: str | None) -> list[str]:
    """Имя владельца для поиска обращений: первое слово, без «Вы» (это
    подпись микрофона по умолчанию, а не имя)."""
    words = _words(name or "")
    if not words or words[0] in SECOND_PERSON:
        return []
    return [words[0]]


def _calls_name(words: list[str], names: list[str]) -> bool:
    for name in names:
        stem = name[:-1] if len(name) >= STEM_MIN + 1 else name
        for w in words:
            if w == name or (len(stem) >= STEM_MIN and w.startswith(stem)
                             and len(w) - len(stem) <= 3):
                return True
    return False


def trigger_of(entry: dict, *, owner_speaker: str, owner_name: str | None = None) -> str | None:
    """Повод внеочередной подсказки по реплике или None.

    `owner_speaker` — подпись владельца в ленте (реплики микрофона);
    `owner_name` — его имя для поиска обращений (обычно та же подпись)."""
    speaker = str(entry.get("speaker") or "")
    if speaker and speaker == owner_speaker:
        return None
    text = str(entry.get("text") or "").strip()
    if not text:
        return None
    words = _words(text)
    names = owner_names(owner_name if owner_name is not None else owner_speaker)
    if names and _calls_name(words, names):
        return ADDRESSED
    if any(w in SECOND_PERSON for w in words) and any(w in QUESTION_WORDS for w in words):
        return ADDRESSED
    if text.rstrip("»\"')").endswith("?"):
        return QUESTION
    return None
