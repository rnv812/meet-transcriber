"""Исправление распознанных слов встречи: «Исправить…» в расшифровке и
`meet fix`.

Совпадения ищутся по правилам поиска (meet.search): текст в NFC, без учёта
регистра, «ё» = «е», по целым словам; фраза — слова подряд (знаки препинания
между ними не мешают). Отметки перерыва — не реплики, в них не ищем.

Замена — шаг общей истории встречи (meet.speakers): каждый изменённый сегмент
лежит в объёмной части шага «до/после» (`replace` — тот же механизм, что у
разрезанных реплик), поэтому отмена, повтор и «Вернуть к этому состоянию»
работают вперемешку с правками спикеров, а правку, поверх которой с тех пор
что-то поменяли, откат честно отказывается трогать.

Слова (words.json) остаются выровненными: слова, задетые совпадением,
заменяются словами исправления на их же отрезке времени — одно в одно с
прежним временем, иначе время делится по длине слов (несколько слов в одно —
их отрезки сливаются).

Правила замены (`asr.replacements` в настройках, meet.replacements) — то же
исправление для новых расшифровок: пайплайн применяет их сразу после
распознавания и выравнивания слов (apply_rules), до раздачи реплик спикерам.
"""

import re
import uuid
from datetime import datetime
from pathlib import Path

from meet import library, speakers
from meet.replacements import TEXT_MAX, case_like, clean_rules, clean_text, matches, nfc

SCOPES = ("one", "all")
SAMPLES = 5
CONTEXT = 40
_PIECE = re.compile(r"\s*\S+")


class Unchanged(speakers.SpeakerError):
    """Текст уже такой — менять нечего (400; термин при этом добавить можно)."""


# --- слова сегмента -------------------------------------------------------------


def _respread(words: list[list], i: int, j: int, chunk: str) -> list[list]:
    """Слова i..j → слова текста `chunk` на том же отрезке времени."""
    pieces = _PIECE.findall(chunk)
    if not pieces:
        return []
    tail = chunk[len("".join(pieces)):]
    pieces[-1] += tail
    old = words[i:j + 1]
    if len(pieces) == len(old):
        return [[w[0], w[1], p] for w, p in zip(old, pieces)]
    t0, t1 = float(old[0][0]), float(old[-1][1])
    sizes = [max(1, len(p.strip())) for p in pieces]
    total, acc, out = sum(sizes), 0, []
    start = t0
    for k, (p, size) in enumerate(zip(pieces, sizes)):
        acc += size
        end = t1 if k == len(pieces) - 1 else round(t0 + (t1 - t0) * acc / total, 2)
        out.append([round(start, 2), end, p])
        start = end
    return out


def _rewrite_words(text: str, words: list, spans: list[tuple[int, int, str]]) -> list | None:
    """Слова сегмента после замен `spans` ([начало, конец) в `text`, замена);
    None — слова не описывают текст, выровнять нечего."""
    words = [[w[0], w[1], nfc(str(w[2]))] for w in words]
    full = "".join(w[2] for w in words)
    if full.strip() != text.strip():
        return None
    shift = (len(full) - len(full.lstrip())) - (len(text) - len(text.lstrip()))
    for a, b, repl in sorted(spans, reverse=True):
        qa, qb = a + shift, b + shift
        bounds, pos = [], 0
        for w in words:
            bounds.append((pos, pos + len(w[2])))
            pos += len(w[2])
        hit = [k for k, (cs, ce) in enumerate(bounds)
               if cs + (len(words[k][2]) - len(words[k][2].lstrip())) < qb and ce > qa]
        if not hit:
            return None
        i, j = hit[0], hit[-1]
        cs = bounds[i][0]
        chunk = full[cs:bounds[j][1]]
        chunk = chunk[:qa - cs] + repl + chunk[qb - cs:]
        new = _respread(words, i, j, chunk)
        if not new:
            return None
        words = words[:i] + new + words[j + 1:]
        full = "".join(w[2] for w in words)
    return words


def rewrite(segment: dict, spans: list[tuple[int, int, str]]) -> dict:
    """Сегмент (dict транскрипта) после замен: текст и выровненные слова."""
    text = nfc(str(segment.get("text") or ""))
    new_text = text
    for a, b, repl in sorted(spans, reverse=True):
        new_text = new_text[:a] + repl + new_text[b:]
    out = {**segment, "text": new_text}
    if isinstance(segment.get("words"), list) and segment["words"]:
        if library.words_match({**segment, "text": text}):
            words = _rewrite_words(text, segment["words"], spans)
            if words is None or not library.words_match({"text": new_text, "words": words}):
                out.pop("words")  # не сошлось — честнее без слов, чем с чужими
            else:
                out["words"] = words
    return out


# --- встреча --------------------------------------------------------------------


def _clean(value, what: str) -> str:
    text = clean_text(value)
    if not text:
        raise speakers.SpeakerError(f"{what}: пусто")
    if len(text) > TEXT_MAX:
        raise speakers.SpeakerError(f"{what}: слишком длинно (не больше {TEXT_MAX} символов)")
    return text


def _found(segments: list[dict], find: str, whole_word: bool) -> list[tuple[int, int, int]]:
    """(сегмент, начало, конец) всех совпадений, кроме отметок перерыва."""
    out = []
    for i, s in enumerate(segments):
        if not isinstance(s, dict) or s.get("kind") == "break":
            continue
        out += [(i, a, b) for a, b in matches(nfc(str(s.get("text") or "")), find, whole_word)]
    return out


def _time(segment: dict, a: int, b: int) -> dict:
    """Когда звучит совпадение: по словам, а без них — весь сегмент."""
    start, end = float(segment.get("start") or 0.0), float(segment.get("end") or 0.0)
    text = nfc(str(segment.get("text") or ""))
    words = segment.get("words")
    if isinstance(words, list) and words and library.words_match(segment):
        full = "".join(nfc(str(w[2])) for w in words)
        shift = (len(full) - len(full.lstrip())) - (len(text) - len(text.lstrip()))
        pos, hit = 0, []
        for w in words:
            size = len(nfc(str(w[2])))
            lead = size - len(nfc(str(w[2])).lstrip())
            if pos + lead < b + shift and pos + size > a + shift:
                hit.append(w)
            pos += size
        if hit:
            start, end = float(hit[0][0]), float(hit[-1][1])
    return {"start": round(start, 2), "end": round(end, 2)}


def _sample(segments: list[dict], i: int, a: int, b: int) -> dict:
    """Совпадение с окружением; обрезанное окружение — по границе слова, с «…»."""
    s = segments[i]
    text = nfc(str(s.get("text") or ""))
    before, after = text[max(0, a - CONTEXT):a], text[b:b + CONTEXT]
    if a > CONTEXT:
        before = "…" + (before.split(" ", 1)[1] if " " in before else before)
    if b + CONTEXT < len(text):
        after = (after.rsplit(" ", 1)[0] if " " in after.strip() else after) + "…"
    return {"segment": i, "offset": a, "speaker": s.get("speaker"), **_time(s, a, b),
            "before": before, "match": text[a:b], "after": after}


def preview(folder: Path, find: str, whole_word: bool = True, segment: int | None = None,
            offset: int | None = None, limit: int = SAMPLES) -> dict:
    """Сколько раз `find` встречается во встрече, первые совпадения с
    окружением и когда звучит выбранное (`segment`, `offset`)."""
    data = library.read_transcript_full(folder)
    segments = (data or {}).get("segments")
    if not isinstance(segments, list):
        raise speakers.SpeakerError("у записи нет расшифровки")
    find = _clean(find, "Что исправить")
    found = _found(segments, find, whole_word)
    here = next((_time(segments[i], a, b) for i, a, b in found
                 if i == segment and (offset is None or a == offset)), None)
    return {"count": len(found), "samples": [_sample(segments, *x) for x in found[:max(0, limit)]],
            "here": here}


def apply(folder: Path, find: str, replace: str, scope: str, voices_dir: Path, *,
          segment: int | None = None, offset: int | None = None, whole_word: bool = True,
          count: int | None = None, now: datetime | None = None) -> dict:
    """Заменить `find` на `replace` одним шагом истории встречи: одно
    совпадение (`scope="one"`: сегмент `segment`, начало `offset`; без
    `offset` — первое в сегменте) или все (`"all"`, регистр — как у каждого
    исправляемого). `count` — сколько сегментов видит окно: разошлось — Stale."""
    if scope not in SCOPES:
        raise speakers.SpeakerError(f"неизвестная область замены «{scope}»")
    find = _clean(find, "Что исправить")
    replace = _clean(replace, "Как правильно")
    data = speakers.editable(folder)
    segments = data["segments"]
    if count is not None and count != len(segments):
        raise speakers.Stale(speakers.STALE_VIEW)
    found = _found(segments, find, whole_word)
    if scope == "one":
        found = [x for x in found if x[0] == segment and (offset is None or x[1] == offset)][:1]
        if not found:
            raise speakers.Stale(speakers.STALE_VIEW)
    elif not found:
        raise speakers.SpeakerError(f"Во встрече нет «{find}»")

    by_segment: dict[int, list[tuple[int, int, str]]] = {}
    for i, a, b in found:
        text = nfc(str(segments[i].get("text") or ""))
        repl = replace if scope == "one" else case_like(text[a:b], replace)
        if text[a:b] != repl:
            by_segment.setdefault(i, []).append((a, b, repl))
    if not by_segment:
        raise Unchanged("нечего менять — текст уже такой")

    items = []
    for i, spans in sorted(by_segment.items()):
        before = segments[i]
        after = rewrite(before, spans)
        items.append({"at": i, "before": before, "after": [after]})
        segments[i] = after
    changed = sum(len(x) for x in by_segment.values())
    op = {"type": "text", "from": find, "to": replace, "count": changed, "scope": scope}
    step = {
        "id": uuid.uuid4().hex[:12],
        "at": (now or datetime.now()).isoformat(timespec="seconds"),
        "ops": [op], "enrolled": [], "created_people": [], "segments": [],
        "count": len(segments), "count_after": len(segments), "names": {}, "payload": True,
    }
    meta, _ = speakers._commit(folder, data, speakers._recorder(data, step), voices_dir, lambda m: None,
                               payload=(step["id"], {"replace": items}))
    steps, pos = speakers._history_of(meta, data)
    return {"step": speakers._public([step])[0], "history": speakers._public(steps), "pos": pos,
            "trimmed": speakers._trimmed(meta, data), "voices_error": None, "changed": changed}


# --- правила для новых расшифровок ------------------------------------------------


def apply_rules(segments: list, rules) -> int:
    """Правила замены — к сегментам распознавания (`asr.Segment`, на месте):
    целые слова, регистр — как у исправляемого, слова выровнены. → сколько
    замен сделано."""
    from meet.asr import Word

    rules = clean_rules(rules)
    if not rules:
        return 0
    total = 0
    for seg in segments:
        if getattr(seg, "kind", None) == "break":
            continue
        for rule in rules:
            text = nfc(seg.text or "")
            spans = _rule_spans(text, rule)
            if seg.words:
                # Разбиение по спикерам собирает текст заново из слов: слова
                # исправляются по своему тексту, даже если он чуть разошёлся с
                # текстом сегмента.
                words = [[w.start, w.end, w.text] for w in seg.words]
                wtext = nfc("".join(str(w[2]) for w in words)).strip()
                wspans = _rule_spans(wtext, rule)
                if wspans:
                    got = rewrite({"text": wtext, "words": words}, wspans)
                    if "words" in got:
                        seg.words = [Word(float(w[0]), float(w[1]), str(w[2])) for w in got["words"]]
                spans = spans or []
                total += len(spans) or len(wspans)
            else:
                total += len(spans)
            if spans:
                seg.text = rewrite({"text": text}, spans)["text"]
    return total


def _rule_spans(text: str, rule: dict) -> list[tuple[int, int, str]]:
    spans = [(a, b, case_like(text[a:b], rule["to"])) for a, b in matches(text, rule["from"])]
    return [x for x in spans if text[x[0]:x[1]] != x[2]]
