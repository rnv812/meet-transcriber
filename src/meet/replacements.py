"""Правила замены распознанного (`asr.replacements`) и сопоставление слов.

Лёгкий модуль (без numpy): его читают настройки при каждой загрузке, а
пайплайн расшифровки и «Исправить…» (meet.textfix) — те же правила
сопоставления: по правилам поиска (meet.search) — текст в NFC, без учёта
регистра, «ё» = «е», по целым словам; фраза — слова подряд.
"""

import unicodedata

from meet import search

TEXT_MAX = 200
RULES_MAX = 300


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def clean_text(value) -> str:
    """Слово или фраза из поля ввода: NFC, пробелы схлопнуты."""
    return " ".join(nfc(str(value or "")).split())


def _fold(text: str) -> str:
    """Регистр и «ё» без смены длины: позиции в сложенном тексте — те же."""
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text).replace("ё", "е")


def matches(text: str, find: str, whole_word: bool = True) -> list[tuple[int, int]]:
    """Совпадения `find` в `text` (уже NFC) — [начало, конец) без перекрытий."""
    if whole_word:
        want = [w for w, _, _ in search.tokenize(nfc(find))]
        if not want:
            return []
        tokens = search.tokenize(text)
        out, i, n = [], 0, len(want)
        while i + n <= len(tokens):
            if [t[0] for t in tokens[i:i + n]] == want:
                out.append((tokens[i][1], tokens[i + n - 1][2]))
                i += n
            else:
                i += 1
        return out
    needle = _fold(nfc(find).strip())
    if not needle or not search.tokenize(needle):
        return []
    hay, out, pos = _fold(text), [], 0
    while (at := hay.find(needle, pos)) >= 0:
        out.append((at, at + len(needle)))
        pos = at + len(needle)
    return out


def case_like(original: str, replacement: str) -> str:
    """Исправление в регистре исправляемого: в начале предложения — с
    заглавной, капсом — капсом. Заглавные в самом исправлении — как написано."""
    if any(c.isupper() for c in replacement):
        return replacement
    letters = [c for c in original if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return replacement.upper()
    if letters and letters[0].isupper():
        i = next((k for k, c in enumerate(replacement) if c.isalpha()), None)
        if i is not None:
            return replacement[:i] + replacement[i].upper() + replacement[i + 1:]
    return replacement


def _rule_key(text: str) -> tuple[str, ...]:
    return tuple(w for w, _, _ in search.tokenize(nfc(text)))


def clean_rules(raw) -> list[dict]:
    """Правила из настроек: {"from", "to"} с непустыми строками и словами в
    «from»; одинаковое «from» (по правилам поиска) — побеждает последнее."""
    out: dict[tuple, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        src = clean_text(item.get("from"))[:TEXT_MAX]
        dst = clean_text(item.get("to"))[:TEXT_MAX]
        key = _rule_key(src)
        if not key or not dst:
            continue
        out.pop(key, None)
        out[key] = {"from": src, "to": dst}
    return list(out.values())[-RULES_MAX:]


def with_rule(rules, src: str, dst: str) -> list[dict]:
    """Правила с ещё одним (то же «from» — заменяется)."""
    return clean_rules([*(rules or []), {"from": src, "to": dst}])
