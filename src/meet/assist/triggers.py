"""Поводы для внеочередной подсказки: вопрос на встрече, обращение к владельцу.

Тикер подсказок зовёт модель по ритму речи (раз в ~20 с сказанного), но
вопрос, заданный на встрече, ждать ритма не должен: подсказка «что ответить»
нужна, пока собеседник ждёт ответа. Здесь — простые проверяемые правила
по одной реплике (русский язык), без модели:

* `addressed` — к владельцу обратились:
  - его имя (из настроек или прежние имена в базе голосов) в позиции
    обращения: «Марина, посмотрите…», «…, Марина?» — имя отделено запятой
    или стоит перед «!»/«?». Имя в косвенном падеже («вопрос к Марине») —
    рассказ о человеке, а не обращение;
  - или короткая реплика с «?» на конце и формами второго лица: «Вы
    успеете к пятнице?», «Сможете прислать оценку?». Без «?» — нет:
    «Я думаю, что вы правы», «Спасибо вам, что пришли» — обычные фразы
    делового «вы»;
* `question` — любая другая реплика с «?» на конце.

Реплики самого владельца поводом не бывают: свои вопросы он слышит сам.
Распознавание речи неидеально, поэтому правило — повод спросить модель, а
не вывод: модель сама решает, есть ли что подсказать. Частоту таких
внеочередных тиков ограничивает тикер (минимальный промежуток и свой
бюджет в час).
"""

import re

QUESTION = "question"
ADDRESSED = "addressed"

# Местоимения второго лица (обращение на «вы» и на «ты»).
SECOND_PERSON = frozenset(
    "вы вас вам вами ваш ваша ваше ваши вашего вашей вашим ваших вашу вашем "
    "ты тебя тебе тобой твой твоя твое твои твоего твоей твоим твоих твою твоем".split())

# Глаголы второго лица, которыми спрашивают без местоимения («Успеете?»).
SECOND_PERSON_VERBS = frozenset(
    "можете сможете успеете готовы знаете помните подскажете скажете расскажете "
    "думаете считаете согласны планируете возьмете берете пришлете посмотрите "
    "можешь сможешь успеешь готов знаешь помнишь подскажешь скажешь думаешь "
    "считаешь согласен планируешь возьмешь пришлешь посмотришь".split())

# Короткая реплика: длинная фраза с «?» в конце — скорее рассуждение вслух.
SHORT_WORDS = 16

_WORD = re.compile(r"[а-яёa-z]+", re.I)
_END = "»\"') "


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    return _WORD.findall(_norm(text))


def owner_names(*names: str | None) -> list[str]:
    """Имена владельца для поиска обращений: первое слово каждого, без «Вы»
    (это подпись микрофона по умолчанию, а не имя)."""
    out: list[str] = []
    for name in names:
        words = _words(name or "")
        if words and words[0] not in SECOND_PERSON and words[0] not in out:
            out.append(words[0])
    return out


def _vocative(text: str, names: list[str]) -> bool:
    """Имя в позиции обращения: за ним запятая, «!» или «?», либо оно
    стоит в конце реплики после запятой («…, Марина?»)."""
    norm = _norm(text)
    for name in names:
        n = re.escape(name)
        if re.search(rf"(?<![а-яa-z]){n}(?![а-яa-z])\s*[,!?]", norm):
            return True
        if re.search(rf",\s*{n}\s*[.!?…]*\s*$", norm):
            return True
    return False


def trigger_of(entry: dict, *, owner_speaker: str, owner_name: str | None = None,
               names: list[str] | None = None) -> str | None:
    """Повод внеочередной подсказки по реплике или None.

    `owner_speaker` — подпись владельца в ленте (реплики микрофона);
    `owner_name`/`names` — его имена для поиска обращений (по умолчанию —
    подпись)."""
    speaker = str(entry.get("speaker") or "")
    if speaker and speaker == owner_speaker:
        return None
    text = str(entry.get("text") or "").strip()
    if not text:
        return None
    if names is None:
        names = owner_names(owner_name if owner_name is not None else owner_speaker)
    if names and _vocative(text, names):
        return ADDRESSED
    if not text.rstrip(_END).endswith("?"):
        return None
    words = _words(text)
    if len(words) <= SHORT_WORDS and any(w in SECOND_PERSON or w in SECOND_PERSON_VERBS
                                         for w in words):
        return ADDRESSED
    return QUESTION
