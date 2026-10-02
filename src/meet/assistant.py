"""Ассистент по готовой записи: итоги и вопросы по транскрипту.

Модель вызывается через runner из `meet.llm` (async, ошибки — в
`AgentReply.error`); здесь только промпты, сборка контекста и запись файлов в
папку записи (выгрузка в базу знаний — `meet.kb_export`). Вызов модели идёт в подпроцессе задачи (`meet.job_worker`), а не
в резиденте: SDK провайдера туда не тянется.

Файлы папки записи:
    summary.md   итоги последнего прогона (атомарная замена; ошибка не трогает)
    qa.jsonl     вопросы и ответы по одной паре в строке {"q","a","at","provider"}
    meta.json    summary_at — когда сделаны итоги (epoch, как transcript_at)
    live_state.json  сводка живого режима (пишет `meet assist`): итоги берут
                 её черновиком и сверяют с полной расшифровкой
"""

import asyncio
import inspect
import json
import os
import time
from datetime import datetime
from pathlib import Path

from meet import library
from meet.output import fmt_ts

SUMMARY_MD = "summary.md"
QA_JSONL = "qa.jsonl"
# Расшифровка двухчасовой встречи — порядка 150 тыс. символов; лимит с запасом
# под длинные дни, но не бесконечный: контекст модели не резиновый.
MAX_TRANSCRIPT_CHARS = 400_000
QA_HISTORY = 6
SUMMARY_TIMEOUT_S = 600.0
ASK_TIMEOUT_S = 300.0
NO_PROVIDER = "Подключите Claude Code или Codex в настройках"
EMPTY_REPLY = "модель вернула пустой ответ"

SUMMARY_SYSTEM = """\
Ты — секретарь встречи. Тебе дают расшифровку записи встречи: реплики вида
«[мм:сс] Имя: текст». Составь итоги по-русски в Markdown строго такой структуры:

## Итоги
3–7 пунктов: о чём договорились и что важно знать тому, кто не был на встрече.

## Решения
Принятые решения списком. Нет решений — так и напиши.

## Задачи
Таблица с колонками «Кто · Что · Срок». Исполнитель или срок не названы — «—».

## Открытые вопросы
Что осталось нерешённым или требует уточнения.

## Цитаты
2–5 дословных цитат с таймкодом в формате «[мм:сс] Имя: …», подтверждающих решения.

Правила:
- Только то, что есть в транскрипте. Ничего не выдумывай.
- Не выдумывай сроки и исполнителей: не прозвучали — ставь «—».
- Спорное, неуверенно расслышанное или противоречивое помечай «(спорно)».
- Если доступна база знаний (папка с материалами), сверяй по ней термины,
  названия продуктов и имена людей; в итогах пиши их так, как в базе.
- Отвечай только итогами, без вступлений и пояснений.
"""

# Черновик из живого режима: сводка по неполной живой расшифровке. Модель
# сверяет его с полным транскриптом — подтверждённое берёт, остальное нет.
DRAFT_INTRO = (
    "Черновик итогов, собранный во время встречи живым ассистентом по неполной "
    "расшифровке. Используй его как подсказку, а не как источник: каждое утверждение "
    "проверь по транскрипту выше; чего в транскрипте нет — не включай, расхождения "
    "решай в пользу транскрипта."
)
DRAFT_MAX_CHARS = 6000

ASK_SYSTEM = """\
Ты — помощник по прошедшей встрече. Тебе дают расшифровку записи (реплики вида
«[мм:сс] Имя: текст»), итоги, если они уже есть, и предыдущие вопросы с
ответами. Ответь на новый вопрос по-русски, коротко и по делу.

Правила:
- Опирайся на транскрипт; ссылайся на таймкоды «[мм:сс]», где это помогает.
- Если в транскрипте ответа нет — так и скажи, не выдумывай.
- Если доступна база знаний (папка с материалами), сверяй по ней термины и
  имена и пользуйся ею для контекста, явно отделяя это от сказанного на встрече.
"""


# --- контекст ------------------------------------------------------------------


def transcript_text(data: dict | None) -> str:
    """Транскрипт для промпта: «[мм:сс] Спикер: текст», подряд идущие реплики
    одного спикера склеены. Длиннее MAX_TRANSCRIPT_CHARS — вырезается середина
    с пометкой (начало и конец встречи обычно важнее)."""
    data = library.with_display_names(data) or {}
    lines: list[str] = []
    last_speaker: object = object()
    for seg in data.get("segments") or []:
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if seg.get("kind") == "break":  # перерыв объединённой встречи
            lines.append(text)
            last_speaker = object()
            continue
        speaker = seg.get("speaker") or "Спикер ?"
        if lines and speaker == last_speaker:
            lines[-1] += f" {text}"
            continue
        lines.append(f"[{fmt_ts(float(seg.get('start') or 0.0))}] {speaker}: {text}")
        last_speaker = speaker
    return _cut_middle("\n".join(lines), MAX_TRANSCRIPT_CHARS)


def _cut_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    head = text[:half]
    tail = text[-half:]
    # По границам строк: обрывок реплики только сбивал бы модель.
    if "\n" in head:
        head = head[:head.rfind("\n")]
    if "\n" in tail:
        tail = tail[tail.find("\n") + 1:]
    skipped = len(text) - len(head) - len(tail)
    return f"{head}\n[… пропущено {skipped} символов из середины записи …]\n{tail}"


def _read_transcript(folder: Path) -> dict:
    data = library.read_transcript(folder)
    if data is None:
        raise RuntimeError("транскрипта нет")
    return data


def _allowed_dirs(folder: Path, knowledge_dir) -> tuple[Path, ...]:
    knowledge = Path(knowledge_dir) if knowledge_dir else None
    if knowledge is not None and knowledge.is_dir():
        return (folder, knowledge)
    return (folder,)


def _knowledge_hint(dirs: tuple[Path, ...]) -> str:
    if len(dirs) < 2:
        return ""
    return f"\n\nБаза знаний (только чтение): {dirs[1]}"


def _call(runner, prompt: str, **kwargs):
    """Runner провайдера — async; вызываем синхронно (мы в подпроцессе задачи)."""
    reply = runner(prompt, **kwargs)
    if inspect.isawaitable(reply):
        reply = asyncio.run(reply)
    text = (reply.text or "").strip()
    if reply.error or not text:
        raise RuntimeError(reply.error or EMPTY_REPLY)
    return text


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --- итоги ---------------------------------------------------------------------


def summarize(folder: Path, runner, knowledge_dir, *, provider: str | None = None,
              want_title: bool = False) -> Path:
    """Итоги встречи → `summary.md`. Ошибка модели — RuntimeError, прежний
    summary.md при этом не трогается.

    `want_title` — включено «Придумывать название встречи»: модель первой
    строкой пишет «Название: …»; строка в итоги не попадает, а название ложится
    в meta.json (`summary_title`) — применяет его резидент или CLI по правилам
    `meet.titles`."""
    from meet import titles

    folder = Path(folder)
    data = _read_transcript(folder)
    title, date = library.title_and_date(folder, data, today_if_unknown=True)
    dirs = _allowed_dirs(folder, knowledge_dir)
    prompt = (f"Встреча: {title} ({date})\n\nТранскрипт:\n{transcript_text(data)}"
              f"{_live_draft(folder)}{_knowledge_hint(dirs)}")
    system = SUMMARY_SYSTEM + (titles.SUMMARY_TITLE_RULE if want_title else "")
    text = _call(runner, prompt, system_prompt=system, allowed_dirs=dirs,
                 cwd=folder, timeout_s=SUMMARY_TIMEOUT_S)
    suggested, text = titles.split_summary_title(text) if want_title else (None, text)
    if not text.strip():
        raise RuntimeError(EMPTY_REPLY)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    path = folder / SUMMARY_MD
    _write_atomic(path, f"# Итоги — {title}\n\n{text}\n\n"
                        f"_Модель: {provider or 'модель'} · {stamp}_\n")
    now = time.time()
    library.update_meta(folder, lambda meta: {
        **{k: v for k, v in meta.items() if k != "summary_title"}, "summary_at": now,
        **({"summary_title": {"title": suggested, "at": now}} if suggested and want_title else {})})
    return path


def _live_draft(folder: Path) -> str:
    """Сводка живого режима как черновик для промпта итогов; нет — пусто."""
    from meet.assist.live_state import load_saved

    saved = load_saved(folder)
    if saved is None:
        return ""
    draft = saved["markdown"].strip()
    if not draft or draft.startswith("_Пока пусто"):
        return ""
    if len(draft) > DRAFT_MAX_CHARS:
        draft = draft[:DRAFT_MAX_CHARS].rstrip() + "…"
    return f"\n\n{DRAFT_INTRO}\n\n{draft}"


def read_summary(folder: Path) -> dict | None:
    """`{"markdown", "created_at"}` или None, если итогов нет."""
    path = Path(folder) / SUMMARY_MD
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        return None
    created = library.read_meta(Path(folder)).get("summary_at")
    if not isinstance(created, (int, float)):
        try:
            created = path.stat().st_mtime
        except OSError:
            created = None
    return {"markdown": markdown, "created_at": created}


# --- вопросы -------------------------------------------------------------------


def read_qa(folder: Path) -> list[dict]:
    """Пары вопрос-ответ по порядку; битые строки пропускаются."""
    try:
        raw = (Path(folder) / QA_JSONL).read_text(encoding="utf-8")
    except OSError:
        return []
    items = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and "q" in item:
            items.append(item)
    return items


def ask(folder: Path, question: str, runner, knowledge_dir, *,
        provider: str | None = None) -> dict:
    """Вопрос по записи. Ответ дописывается в qa.jsonl; ошибка — RuntimeError."""
    folder = Path(folder)
    data = _read_transcript(folder)
    title, date = library.title_and_date(folder, data, today_if_unknown=True)
    dirs = _allowed_dirs(folder, knowledge_dir)
    parts = [f"Встреча: {title} ({date})", "", "Транскрипт:", transcript_text(data)]
    summary = read_summary(folder)
    if summary:
        parts += ["", "Итоги встречи:", summary["markdown"].strip()]
    history = read_qa(folder)[-QA_HISTORY:]
    if history:
        parts += ["", "Предыдущие вопросы и ответы:"]
        for item in history:
            parts += [f"Вопрос: {item.get('q', '')}", f"Ответ: {item.get('a', '')}", ""]
    hint = _knowledge_hint(dirs)
    if hint:
        parts.append(hint.strip())
    parts += ["", f"Новый вопрос: {question}"]
    answer = _call(runner, "\n".join(parts), system_prompt=ASK_SYSTEM,
                   allowed_dirs=dirs, cwd=folder, timeout_s=ASK_TIMEOUT_S)
    item = {"q": question, "a": answer, "at": time.time(), "provider": provider}
    with (folder / QA_JSONL).open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item
