"""Список слов распознавания (hotwords.txt): разбор и бюджет.

Отдельный модуль, а не часть transcribe: разбор нужен и резиденту (счётчик в
настройках), а transcribe тянет numpy и ASR, которых в минимальной установке
для записи нет. Один источник правил — чтобы счётчик в настройках совпадал с
тем, что реально уйдёт в распознавание.
"""

# IMPORTANT: контекст Whisper — 448 токенов, и faster-whisper НЕЗАВИСИМО усекает
# до 223 токенов и hotwords, и предыдущий текст (condition_on_previous_text);
# вместе они переполняют окно (223+223+служебные > 448) и роняют декодирование
# («maximum decoding length must be > 0»). Поэтому держим hotwords заведомо ниже.
# Бюджет в символах: при замеренной плотности лексики ~2.4 симв./токен это ~160
# токенов, и даже при пессимистичных 2.0 симв./токен ≈198 — итог с предыдущим
# текстом остаётся < 448. Список можно пополнять и дальше: лишнее отсекается.
HOTWORDS_CHAR_BUDGET = 400


def terms(text: str) -> list[str]:
    """Термины из текста hotwords.txt: по одному на строку, `#` — комментарий,
    повторы убраны с сохранением порядка."""
    found = []
    for line in text.splitlines():
        term = line.split("#", 1)[0].strip()
        if term:
            found.append(term)
    return list(dict.fromkeys(found))


def _key(term: str) -> str:
    return " ".join(term.lower().replace("ё", "е").split())


def add_term(text: str, term: str) -> tuple[str, bool]:
    """Термин — в конец списка (свежие важнее при обрезке по бюджету), если
    его там ещё нет (без учёта регистра, «ё» = «е»). → (текст, добавлен ли)."""
    term = " ".join(term.split())
    if not term or _key(term) in {_key(t) for t in terms(text)}:
        return text, False
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}{term}\n", True


def remove_term(text: str, term: str) -> str:
    """Убрать строку с этим термином (последнюю такую — её и добавили);
    комментарии и остальные строки не трогать."""
    lines = text.splitlines(keepends=True)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].split("#", 1)[0].strip() == " ".join(term.split()):
            return "".join(lines[:i] + lines[i + 1:])
    return text


def read(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write(path, text: str) -> None:
    """Атомарно: список читают расшифровка и окно настроек."""
    import os

    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def add_to_file(path, term: str) -> dict:
    """Термин — в файл списка (окно «Исправить…», `meet fix --hotword`):
    {"term", "added", "over_budget"} или {"term", "added": False, "error"}."""
    try:
        text, added = add_term(read(path), term)
        if added:
            write(path, text)
    except OSError as e:
        return {"term": term, "added": False, "error": f"не удалось сохранить термины: {e}"}
    used = len(", ".join(terms(text)))
    return {"term": term, "added": added, "over_budget": used > HOTWORDS_CHAR_BUDGET}
