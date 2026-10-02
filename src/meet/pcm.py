"""Раздел «Модель PCM» профиля человека (meet.profiles): гипотеза по
Process Communication Model (Тайби Кейлер) — по репликам во встречах.

Это гипотеза, а не сертифицированная оценка: окно подписывает её так прямо.
Модель упоминается только описательно (PCM — товарный знак Kahler
Communications, см. NOTICE), без логотипов и без претензии на методику.

Что спрашивается у модели (поле "pcm" ответа) и как проверяется:

* `base`, `phase` — тип (один из шести, английский ключ), уверенность 0..1 и
  ссылки на реплики; без годной ссылки — отбрасывается; без базы весь раздел
  отбрасывается (этажи без базы не нарисовать);
* `floors` — выраженность каждого типа 0..5 (целые); база — не ниже прочих;
* `perception` — одно из шести восприятий и ссылки; `channel` — один из пяти
  каналов и 1–3 готовые фразы; `needs` — потребности и «как давать
  признание»;
* `stress_signs` — наблюдаемые признаки напряжения со ссылками,
  `back_to_constructive` и `conversation` — советы.

Каждый текст проходит тот же фильтр, что утверждения профиля
(meet.profile_safety). Раздел просят только при достаточных данных (не
меньше profiles.FULL_TURNS реплик в profiles.FULL_MEETINGS встречах) и если
он включён в настройках (`profiles.pcm`).
"""

from meet import profile_safety
from meet.analysis import _flat, _number

TYPES = ("thinker", "persister", "harmonizer", "imaginer", "rebel", "promoter")
LABELS = {
    "thinker": "Логик",
    "persister": "Упорный",
    "harmonizer": "Гармонизатор",
    "imaginer": "Мечтатель",
    "rebel": "Бунтарь",
    "promoter": "Деятель",
}
ORIGINALS = {t: t.capitalize() for t in TYPES}
PERCEPTIONS = ("мысли", "мнения", "эмоции", "бездействие", "реакции", "действия")
CHANNELS = ("запрашивающий", "директивный", "заботливый", "эмоциональный", "прерывающий")

FLOOR_MAX = 5
EXAMPLES_MAX = 3
ADVICE_MAX = 5
TEXT_MAX = 240
VALUE_MAX = 120

FIELD = """
- "pcm": гипотеза по модели Process Communication Model (PCM, Тайби Кейлер) — только по поведению в репликах, как предположение, а не диагноз и не ярлык. Типы (ключи — по-английски): thinker — Логик (факты, логика, структура, время), persister — Упорный (мнения, ценности, убеждения), harmonizer — Гармонизатор (эмоции, отношения, забота), imaginer — Мечтатель (размышление, воображение, нужна ясная инструкция), rebel — Бунтарь (реакции, юмор, «нравится — не нравится»), promoter — Деятель (действие, результат, риск, коротко и прямо).
 {
  "base": {"type": "thinker", "confidence": 0.6, "refs": ["m1#12"]},
  "phase": {"type": "thinker", "confidence": 0.5, "refs": ["m2#3"]},
  "floors": {"thinker": 5, "persister": 3, "harmonizer": 2, "imaginer": 1, "rebel": 2, "promoter": 3},
  "perception": {"value": "мысли", "refs": ["m1#12"]},
  "channel": {"value": "запрашивающий", "examples": ["готовая фраза для разговора с ним"]},
  "needs": {"value": "какие психологические потребности видны", "how_to_recognize": "как давать признание"},
  "stress_signs": [{"text": "наблюдаемый признак напряжения во встрече", "refs": ["m3#7"]}],
  "back_to_constructive": ["как вернуть разговор в конструктив"],
  "conversation": ["совет"]
 }
 base — базовый тип (самый развитый «этаж»), phase — текущая фаза (чьи потребности сейчас ведут поведение; может совпадать с базой); confidence — насколько ты уверен, от 0 до 1. floors — выраженность каждого из шести типов от 0 до 5 (у базы — наибольшая). perception — одно из: мысли, мнения, эмоции, бездействие, реакции, действия. channel — один из каналов: запрашивающий, директивный, заботливый, эмоциональный, прерывающий; examples — 1–3 фразы, которыми лучше начать разговор с ним. needs: value — потребности (признание за работу и время, признание убеждений, признание как личности и чувственное удовлетворение, одиночество, контакт и игра, стимулы и риск), how_to_recognize — как давать признание. stress_signs — до 4 признаков напряжения, только видимых в репликах, каждый со ссылками. back_to_constructive — 2–4 совета, как вернуть разговор в конструктив. conversation — 3–5 советов: как начать, как аргументировать, как просить о решении, как давать обратную связь. Без ссылок на реплики base, phase и perception не пиши; данных мало — "pcm": null."""


def _clean(value, limit: int, dropped: list) -> str:
    text = _flat(value, limit) if isinstance(value, str) else ""
    if text and profile_safety.unsafe(text):
        dropped.append(text)
        return ""
    return text


def _type_claim(raw, refs_of) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip().lower()
    refs = refs_of(raw.get("refs"))
    if kind not in TYPES or not refs:
        return None
    confidence = _number(raw.get("confidence"))
    return {"type": kind, "confidence": 0.5 if confidence is None else confidence, "refs": refs}


def _floors(raw, base: str) -> dict:
    out = {t: 0 for t in TYPES}
    if isinstance(raw, dict):
        for key, value in raw.items():
            kind = str(key).strip().lower()
            if kind not in TYPES or isinstance(value, bool):
                continue
            try:
                out[kind] = max(0, min(FLOOR_MAX, round(float(value))))
            except (TypeError, ValueError):
                continue
    # База — самый развитый «этаж»: не ниже остальных.
    out[base] = max(out.values()) or FLOOR_MAX
    return out


def _texts(raw, limit: int, dropped: list) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        text = _clean(item, TEXT_MAX, dropped)
        if text and text not in out:
            out.append(text)
    return out[:limit]


def parse(raw, refs_of, statements_of, dropped: list) -> dict | None:
    """Поле "pcm" ответа → раздел профиля или None (нет базы со ссылками).
    `refs_of(raw)` — годные ссылки, `statements_of(raw)` — утверждения со
    ссылками (как разделы профиля)."""
    if not isinstance(raw, dict):
        return None
    base = _type_claim(raw.get("base"), refs_of)
    if base is None:
        return None
    out: dict = {"base": base, "floors": _floors(raw.get("floors"), base["type"])}
    phase = _type_claim(raw.get("phase"), refs_of)
    if phase is not None:
        out["phase"] = phase
    perception = raw.get("perception")
    if isinstance(perception, dict):
        value = str(perception.get("value") or "").strip().lower()
        refs = refs_of(perception.get("refs"))
        if value in PERCEPTIONS and refs:
            out["perception"] = {"value": value, "refs": refs}
    channel = raw.get("channel")
    if isinstance(channel, dict):
        value = str(channel.get("value") or "").strip().lower()
        if value in CHANNELS:
            out["channel"] = {"value": value,
                              "examples": _texts(channel.get("examples"), EXAMPLES_MAX, dropped)}
    needs = raw.get("needs")
    if isinstance(needs, dict):
        value = _clean(needs.get("value"), VALUE_MAX, dropped)
        how = _clean(needs.get("how_to_recognize"), TEXT_MAX, dropped)
        if value or how:
            out["needs"] = {"value": value, "how_to_recognize": how}
    try:
        stress = statements_of(raw.get("stress_signs") if isinstance(raw.get("stress_signs"), list) else [])
    except ValueError:
        stress = []
    if stress:
        out["stress_signs"] = stress[:4]
    for key in ("back_to_constructive", "conversation"):
        items = _texts(raw.get(key), ADVICE_MAX, dropped)
        if items:
            out[key] = items
    return out


def label(kind: str) -> str:
    """«Логик (Thinker)»."""
    return f"{LABELS[kind]} ({ORIGINALS[kind]})"


def text_lines(pcm: dict | None) -> list[str]:
    """Раздел текстом (CLI)."""
    if not pcm:
        return []
    lines = ["", "Модель PCM — гипотеза по репликам во встречах, не сертифицированная оценка"]
    base = pcm["base"]
    lines.append(f"  База: {label(base['type'])}, уверенность {round(base['confidence'] * 100)} %")
    if pcm.get("phase"):
        phase = pcm["phase"]
        lines.append(f"  Фаза: {label(phase['type'])}, уверенность {round(phase['confidence'] * 100)} %")
    floors = pcm.get("floors") or {}
    order = sorted(TYPES, key=lambda t: (t != base["type"], -floors.get(t, 0), TYPES.index(t)))
    lines.append("  Этажи (снизу вверх): " + ", ".join(f"{LABELS[t]} {floors.get(t, 0)}" for t in order))
    if pcm.get("perception"):
        lines.append(f"  Восприятие: {pcm['perception']['value']}")
    if pcm.get("channel"):
        lines.append(f"  Канал: {pcm['channel']['value']}")
        lines += [f"    «{e}»" for e in pcm["channel"].get("examples") or []]
    if pcm.get("needs"):
        lines.append(f"  Как давать признание: {pcm['needs'].get('value')}. {pcm['needs'].get('how_to_recognize')}".rstrip(". "))
    for s in pcm.get("stress_signs") or []:
        lines.append(f"  Признак напряжения: {s['text']}")
    for a in pcm.get("back_to_constructive") or []:
        lines.append(f"  Как вернуть в конструктив: {a}")
    for a in pcm.get("conversation") or []:
        lines.append(f"  Как строить разговор: {a}")
    return lines
