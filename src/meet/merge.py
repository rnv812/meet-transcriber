"""Объединение встреч: несколько записей одной встречи (вышли и зашли заново,
сеть оборвалась дольше ожидания) → одна запись с общей расшифровкой.

Как это идёт:

1. `create` — новая папка записи рядом с исходными: `meta.json` с
   `source: "merge"`, названием, списком исходных (`merged_from`) и тем, что
   делать с ними после (`merge.keep_originals`). Звука в ней ещё нет, но
   библиотека её уже показывает — окно сразу открывает её карточку.
2. `run` (задача `merge` очереди резидента или `meet merge`) — в порядке
   времени склеивает ffmpeg'ом каждую дорожку: `sys`, `mic` или `source`
   (импорт). Нет дорожки в какой-то части — на её место тишина той же длины:
   дорожки обязаны остаться выровненными, иначе расшифровка двух дорожек
   поставила бы реплики не туда. Части пишутся в `meta.parts`
   (сдвиг в общей записи, исходное начало, перерыв перед частью).
3. Обычная расшифровка общей папки — спикеры одни на всю встречу. Перерывы
   вставляются в транскрипт отметками «— перерыв N мин —» (`with_breaks`,
   зовёт `meet.transcribe`): это не реплика, а разделитель.
4. После успешной расшифровки исходные записи удаляются, если не просили
   оставить (это решает резидент или `meet merge`, см. `originals`).

Чат ассистента (`assistant/chat.jsonl`, V4) частей склеивается в журнал
объединённой записи (`merge_chats`, в `run` после звука): сообщения по
времени, `t` — со сдвигом части, id — заново, ссылки между сообщениями
переписаны, перед каждой частью — строка «— часть N —». Картинки и тексты
вложений копируются в папку объединённой записи с приставкой части.
Сеансы агента (`sessions.json`) не переносятся: у объединённой встречи —
новый сеанс с затравкой из журнала.

Импорт среди звонков: его звук — запись чужого разговора целиком, ближе всего
к «собеседникам», поэтому он идёт в `sys`, а микрофон этой части — тишина.
Одни импорты — одна дорожка `source`, как у обычного импорта.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from meet import library

SOURCE = "merge"
FOLDER_SUFFIX = "merged"
BREAK = "break"
# Склеенные дорожки — тот же формат, что пишет запись (meet.recorder).
RATE = 16000
OPUS_BITRATE = "24k"
FFMPEG_TIMEOUT_S = 3600
PROBE_TIMEOUT_S = 60
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


class MergeError(Exception):
    """Объединить нельзя или не вышло — текст для человека."""


@dataclass
class Part:
    """Одна исходная запись в составе объединённой."""

    id: str
    folder: Path
    start: datetime
    duration_s: float
    tracks: dict = field(default_factory=dict)
    title: str | None = None


# --- части ------------------------------------------------------------------------


def _events(folder: Path) -> list[dict]:
    try:
        lines = (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def part_start(folder: Path) -> datetime | None:
    """Начало записи: точное — из `events.jsonl` (запись пишет его при старте),
    иначе — по имени папки (с точностью до минуты)."""
    for event in _events(folder):
        if event.get("kind") == "record.started" and isinstance(event.get("at"), (int, float)):
            return datetime.fromtimestamp(float(event["at"]))
    started = library._started_at(folder.name)
    return datetime.fromisoformat(started) if started else None


def _events_duration(folder: Path) -> float | None:
    for event in reversed(_events(folder)):
        if event.get("kind") == "record.stopped" and isinstance(event.get("duration_s"), (int, float)):
            return float(event["duration_s"])
    return None


def probe_duration(path: Path, run=subprocess.run) -> float | None:
    """Длительность файла в секундах: ffprobe, без него — строка «Duration» из
    ffmpeg. Не узнали — None."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if shutil.which("ffprobe"):
            proc = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=PROBE_TIMEOUT_S, creationflags=flags)
            try:
                return float((proc.stdout or "").strip().splitlines()[0])
            except (ValueError, IndexError):
                pass
        if shutil.which("ffmpeg"):
            proc = run(["ffmpeg", "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=PROBE_TIMEOUT_S, creationflags=flags)
            m = _DURATION.search(proc.stderr or "")
            if m:
                h, mi, s = m.groups()
                return int(h) * 3600 + int(mi) * 60 + float(s)
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def describe_part(folder: Path, probe=probe_duration, end=None) -> Part:
    """Исходная запись как часть: дорожки, начало и длительность (самая длинная
    дорожка; не измерить — длительность записи из `events.jsonl`).

    Автозапись, хвост которой не обрезали (meet.tail), берётся до конца
    звонка + 30 с: `end(folder)` — где он (по умолчанию `tail.effective_end`).
    Иначе склейка тащила бы в середину встречи минуты ожидания, а перерыв
    считался бы от остановки записи, а не от конца звонка."""
    card = library.describe(folder)
    if card is None or not card.tracks:
        raise MergeError(f"В записи нет звука: {folder.name}")
    start = part_start(folder)
    if start is None:
        raise MergeError(f"Неизвестно время начала записи: {folder.name}")
    lengths = [d for d in (probe(Path(p)) for p in card.tracks.values()) if d]
    duration = max(lengths) if lengths else _events_duration(folder)
    if not duration or duration <= 0:
        raise MergeError(f"Не удалось узнать длительность записи: {folder.name}")
    if end is None:
        from meet import tail

        end = tail.effective_end
    try:
        cut = end(folder)
    except Exception:
        cut = None  # не измерили — берём запись целиком
    if cut and 0 < cut < duration:
        duration = cut
    return Part(id=folder.name, folder=folder, start=start, duration_s=float(duration),
                tracks={k: Path(v) for k, v in card.tracks.items()}, title=card.title)


def output_roles(parts: list[Part]) -> tuple[str, ...]:
    """Дорожки объединённой записи: одни импорты — `source`, иначе `sys`+`mic`."""
    if all(set(p.tracks) <= {"source"} for p in parts):
        return ("source",)
    return ("sys", "mic")


def role_track(part: Part, role: str) -> Path | None:
    """Файл части для дорожки `role`; импорт среди звонков идёт в `sys`."""
    if role == "sys":
        return part.tracks.get("sys") or part.tracks.get("source")
    return part.tracks.get(role)


def pieces(parts: list[Part], role: str) -> list[tuple[Path | None, float]]:
    return [(role_track(p, role), p.duration_s) for p in parts]


def concat_command(items: list[tuple[Path | None, float]], out: Path) -> list[str]:
    """ffmpeg: куски подряд, каждый ровно длиной своей части.

    Короткая дорожка части дополняется тишиной (`apad`) и обрезается до длины
    части (`atrim`); отсутствующая — генератор тишины той же длины
    (`anullsrc`). Всё приводится к формату записи — моно 16 кГц, — чтобы
    `concat` склеивал одинаковое."""
    args: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    norm = f"aformat=sample_fmts=fltp:sample_rates={RATE}:channel_layouts=mono"
    inputs = 0
    for index, (path, seconds) in enumerate(items):
        end = f"atrim=end={seconds:.3f}"
        if path is None:
            chains.append(f"anullsrc=r={RATE}:cl=mono,{end},{norm}[a{index}]")
        else:
            args += ["-i", str(path)]
            chains.append(f"[{inputs}:a:0]aresample={RATE},{norm},asetpts=PTS-STARTPTS,"
                          f"apad,{end},asetpts=PTS-STARTPTS[a{index}]")
            inputs += 1
        labels.append(f"[a{index}]")
    graph = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(items)}:v=0:a=1[out]"
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *args,
        "-filter_complex", graph, "-map", "[out]",
        "-ac", "1", "-ar", str(RATE), "-c:a", "libopus", "-b:a", OPUS_BITRATE,
        "-application", "voip",
        "-f", "ogg", str(out),
    ]


def parts_meta(parts: list[Part]) -> list[dict]:
    """Части для `meta.parts`: сдвиг в общей записи, исходное начало,
    длительность и перерыв перед частью (по времени, а не по звуку)."""
    out = []
    offset = 0.0
    prev_end: datetime | None = None
    for part in parts:
        gap = 0.0 if prev_end is None else max(0.0, (part.start - prev_end).total_seconds())
        out.append({
            "id": part.id,
            "start_offset_s": round(offset, 3),
            "original_start": part.start.isoformat(timespec="seconds"),
            "duration_s": round(part.duration_s, 3),
            "gap_s": round(gap, 3),
        })
        offset += part.duration_s
        prev_end = datetime.fromtimestamp(part.start.timestamp() + part.duration_s)
    return out


# --- перерывы в транскрипте --------------------------------------------------------------


def break_text(gap_s: float | None) -> str:
    gap = max(0.0, float(gap_s or 0.0))
    if gap < 60:
        return "— перерыв меньше минуты —"
    if gap >= 86400:  # части разных дней — минуты уже ни о чём не говорят
        days = int(round(gap / 86400))
        return f"— перерыв {days} {_days_word(days)} —"
    minutes = int(round(gap / 60))
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"— перерыв {minutes} мин —"
    return f"— перерыв {hours} ч —" if not rest else f"— перерыв {hours} ч {rest} мин —"


def _days_word(n: int) -> str:
    d, u = n % 100, n % 10
    if 11 <= d <= 14:
        return "дней"
    return "день" if u == 1 else "дня" if 2 <= u <= 4 else "дней"


def with_breaks(segments: list, parts: list[dict] | None) -> list:
    """Сегменты расшифровки с отметками перерывов на стыках частей.

    Отметка — `Segment(kind="break")` без спикера: вывод и окно рисуют её
    разделителем, а не репликой. Встаёт перед первой репликой, начавшейся на
    стыке или позже; реплика, начатая до стыка, остаётся в своей части."""
    from meet.asr import Segment

    if not isinstance(parts, list) or len(parts) < 2:
        return segments
    marks = []
    for part in parts[1:]:
        try:
            at = float(part["start_offset_s"])
        except (KeyError, TypeError, ValueError):
            continue
        marks.append(Segment(at, at, break_text(part.get("gap_s")), None, kind=BREAK))
    marks.sort(key=lambda s: s.start)
    out = []
    i = 0
    for seg in segments:
        while i < len(marks) and marks[i].start <= seg.start:
            out.append(marks[i])
            i += 1
        out.append(seg)
    out.extend(marks[i:])
    return out


# --- новая запись -------------------------------------------------------------------------


def _new_folder(root: Path, start: datetime) -> Path:
    stem = f"{start:%Y-%m-%d_%H-%M}_{FOLDER_SUFFIX}"
    folder = root / stem
    n = 2
    while True:
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            folder = root / f"{stem}-{n}"
            n += 1


def create(root: Path, folders: list[Path], keep_originals: bool) -> Path:
    """Папка объединённой записи с `meta.json`; звук собирает `run`.

    Название — у самой ранней записи, иначе «Объединённая встреча <дата>».
    Запоминаем, какие исходные уже выгружались в базу знаний: после их
    удаления узнать это будет не у кого."""
    unique: list[Path] = []
    for folder in folders:
        folder = Path(folder)
        if all(os.path.normcase(str(folder.resolve())) != os.path.normcase(str(u.resolve()))
               for u in unique):
            unique.append(folder)
    if len(unique) < 2:
        raise MergeError("Для объединения нужно выбрать минимум две записи")
    found = []
    for folder in unique:
        card = library.describe(folder)
        if card is None:
            raise MergeError(f"Записи нет: {folder.name}")
        if not card.tracks:
            raise MergeError(f"В записи нет звука: {card.title or folder.name}")
        start = part_start(folder)
        if start is None:
            raise MergeError(f"Неизвестно время начала записи: {folder.name}")
        found.append((start, folder, card))
    found.sort(key=lambda item: item[0])
    first_start, _, first = found[0]
    title = first.title or f"Объединённая встреча {first_start:%d.%m.%Y}"
    # Название первой части переходит вместе с его происхождением: заданное
    # человеком остаётся «его», автоматическое — автоматическим.
    title_source = first.title_source if first.title else "auto"
    exported = []
    for _, folder, _card in found:
        record = library.read_meta(folder).get("kb_export")
        if isinstance(record, dict) and record.get("path"):
            exported.append(str(record["path"]))
    # Категорию, выбранную человеком у одной из частей (первую по времени),
    # объединённая встреча наследует; категорию от модели даст её новый анализ.
    category = next((card.category for _, _, card in found
                     if card.category and card.category["source"] == "user" and card.category["id"]), None)
    # Группа — первой по времени части, у которой она есть (как категория).
    group = next((card.group for _, _, card in found if card.group), None)
    target = _new_folder(Path(root), first_start)
    library.write_meta(target, {
        "source": SOURCE,
        "title": title,
        "title_source": title_source,
        **({"category": category} if category else {}),
        **({"group": group} if group else {}),
        # Следы ассистента остаются в частях: объединённая помнит, что он был.
        **({"had_assistant": True} if any(card.has_assistant for _, _, card in found) else {}),
        "merged_from": [folder.name for _, folder, _ in found],
        "merge": {"keep_originals": bool(keep_originals), "state": "pending",
                  "kb_exported": exported},
    })
    return target


def originals(folder: Path) -> list[Path]:
    """Исходные записи объединённой (те, что ещё на месте)."""
    names = library.read_meta(folder).get("merged_from")
    root = Path(folder).parent
    return [root / n for n in names if isinstance(n, str) and (root / n).is_dir()] \
        if isinstance(names, list) else []


def unfinished_owner(folder: Path, ignore: Path | None = None) -> str | None:
    """Название незавершённого объединения (кроме `ignore`), в которое входит
    запись. Пока оно не дошло до конца, исходные читаются и нужны целыми: ни
    удалять их, ни отдавать во второе объединение нельзя."""
    folder = Path(folder)
    try:
        siblings = [p for p in folder.parent.iterdir()
                    if p.is_dir() and f"_{FOLDER_SUFFIX}" in p.name]
    except OSError:
        return None
    skip = os.path.normcase(str(Path(ignore).resolve())) if ignore is not None else None
    for other in siblings:
        if skip is not None and os.path.normcase(str(other.resolve())) == skip:
            continue
        meta = library.read_meta(other)
        info = meta.get("merge")
        names = meta.get("merged_from")
        if (meta.get("source") == SOURCE and isinstance(info, dict)
                and info.get("state") != "done" and isinstance(names, list)
                and folder.name in names):
            return str(meta.get("title") or other.name)
    return None


def _tail_end(folder: Path) -> float | None:
    from meet import tail

    return tail.effective_end(folder)


def state(folder: Path) -> str | None:
    """Шаг объединения: "pending" (звук не собран), "merged" (собран),
    "done" (исходные обработаны); не объединённая запись — None."""
    meta = library.read_meta(folder)
    info = meta.get("merge")
    if meta.get("source") != SOURCE or not isinstance(info, dict):
        return None
    return str(info.get("state") or "pending")


def mark_done(folder: Path, deleted: list[str], kept_reason: str | None = None) -> None:
    """Объединение завершено: что из исходных удалено (и почему не всё)."""
    library.update_meta(folder, lambda meta: {**meta, "merge": {
        **(meta.get("merge") or {}), "state": "done", "deleted": list(deleted),
        **({"kept_reason": kept_reason} if kept_reason else {}),
    }})


def _write_events(folder: Path, start: datetime, duration: float) -> None:
    lines = [
        {"kind": "record.started", "at": start.timestamp(), "folder": str(folder), "source": SOURCE},
        {"kind": "record.stopped", "at": start.timestamp() + duration, "folder": str(folder),
         "duration_s": round(duration, 3)},
    ]
    (folder / "events.jsonl").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n", encoding="utf-8")


def run(folder: Path, run=subprocess.run, probe=probe_duration, bus=None,
        end=None) -> Path:
    """Собрать звук объединённой записи из исходных (см. docstring модуля).

    Все дорожки пишутся во временные `.part` и подменяются только когда
    готова каждая: сорванная сборка (сбой, отмена, таймаут) не оставит
    `sys.opus` без `mic.opus`. Любое исключение — `.part` удаляются. Если
    процесс убит, `.part` останутся, но дорожками не считаются, а состояние
    `merge.state` остаётся "pending" до самого конца — по нему резидент
    повторит сборку, а не расшифрует половину и не удалит исходные."""
    folder = Path(folder)
    if shutil.which("ffmpeg") is None:
        raise MergeError("ffmpeg не найден — записи не объединить")
    meta = library.read_meta(folder)
    names = meta.get("merged_from")
    if not isinstance(names, list) or len(names) < 2:
        raise MergeError("Не сказано, какие записи объединять")
    parts = []
    for name in names:
        source = folder.parent / str(name)
        if not source.is_dir():
            raise MergeError(f"Исходная запись пропала: {name}")
        parts.append(describe_part(source, probe=probe,
                                   end=end or _tail_end))
    parts.sort(key=lambda p: p.start)
    roles = output_roles(parts)
    for stale in [*folder.glob("*.opus"), *folder.glob("*.part")]:
        stale.unlink(missing_ok=True)  # повтор после сбоя — начисто
    part_files = {role: folder / f"{role}.opus.part" for role in roles}
    stages = _stages(bus, roles, sum(p.duration_s for p in parts))
    try:
        for role in roles:
            if stages is not None:
                stages.begin(f"merge-{role}")
            try:
                proc = run(concat_command(pieces(parts, role), part_files[role]),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=FFMPEG_TIMEOUT_S,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as e:
                raise MergeError("ffmpeg не успел объединить записи") from e
            except OSError as e:
                raise MergeError(f"ffmpeg не запустился: {e}") from e
            if proc.returncode != 0:
                raise MergeError(f"ffmpeg не объединил записи: {(proc.stderr or '').strip()[-300:]}")
        for role in roles:  # все готовы — подменяем разом
            os.replace(part_files[role], folder / f"{role}.opus")
    except BaseException:
        for part_file in part_files.values():
            part_file.unlink(missing_ok=True)
        for role in roles:  # подмену сорвало посередине — пусть не будет ни одной
            (folder / f"{role}.opus").unlink(missing_ok=True)
        raise
    finally:
        if stages is not None:
            stages.stop_ticking()
    total = sum(p.duration_s for p in parts)
    try:
        merge_chats(folder, parts, parts_meta(parts))
    except Exception as e:  # чат — не повод терять собранный звук
        print(f"объединение: чат ассистента не склеен ({type(e).__name__}: {e})", flush=True)
    _write_events(folder, parts[0].start, total)
    library.update_meta(folder, lambda m: {
        **m, "parts": parts_meta(parts),
        "merge": {**(m.get("merge") or {}), "state": "merged"},
    })
    if stages is not None:
        stages.finish()
    return folder


# --- чат ассистента ----------------------------------------------------------------


def merge_chats(folder: Path, parts: list[Part], meta: list[dict]) -> int:
    """Журналы чата частей → журнал объединённой записи (см. docstring
    модуля). `meta` — `parts_meta(parts)` (сдвиги). Чата нет ни у одной части
    или у объединённой он уже есть (повтор сборки) — ничего. → сколько
    сообщений записано."""
    from meet.assist import chatlog

    folder = Path(folder)
    if chatlog.has_chat(folder):
        return 0
    blocks = []
    for n, (part, info) in enumerate(zip(parts, meta), start=1):
        if chatlog.has_chat(part.folder):
            msgs = chatlog.ChatLog(part.folder).load()
            if msgs:
                blocks.append((n, part, float(info.get("start_offset_s") or 0.0), msgs))
    if not blocks:
        return 0
    entries = []   # (at, порядок, номер части, сообщение)
    order = 0
    for n, part, offset, msgs in blocks:
        ats = [m["at"] for m in msgs if isinstance(m.get("at"), (int, float))]
        if len(blocks) > 1:
            first = min(ats) if ats else 0.0
            entries.append((first - 0.001, order, n, {
                "kind": "meeting", "event": "session", "text": f"— часть {n} —",
                "t": round(offset, 3), "at": first - 0.001, "_part": n}))
            order += 1
        for m in msgs:
            msg = dict(m)
            if isinstance(msg.get("t"), (int, float)) and not isinstance(msg.get("t"), bool):
                msg["t"] = round(float(msg["t"]) + offset, 3)
            if msg.get("status") == chatlog.WRITING:   # ответ не дописан — уже не будет
                msg["status"] = "cancelled"
                msg.setdefault("error", "ответ прерван")
            msg["_old"] = msg.get("id")
            msg["_part"] = n
            at = msg["at"] if isinstance(msg.get("at"), (int, float)) else (max(ats) if ats else 0.0)
            entries.append((at, order, n, msg))
            order += 1
    entries.sort(key=lambda e: (e[0], e[1]))
    ids: dict[tuple[int, str], str] = {}
    counters = {"a": 0, "m": 0}
    for _, _, n, msg in entries:
        prefix = "a" if msg.get("kind") == "attachment" else "m"
        counters[prefix] += 1
        new = f"{prefix}{counters[prefix]}"
        if msg.get("_old"):
            ids[(n, msg["_old"])] = new
        msg["id"] = new
    out = []
    for _, _, n, msg in entries:
        msg.pop("_old", None)
        part_n = msg.pop("_part")
        for key in ("re", "merged_into"):
            if isinstance(msg.get(key), str):
                msg[key] = ids.get((part_n, msg[key]), msg[key])
        if isinstance(msg.get("attachments"), list):
            msg["attachments"] = [ids.get((part_n, a), a) if isinstance(a, str) else a
                                  for a in msg["attachments"]]
        if msg.get("kind") == "attachment":
            source = next(p.folder for k, p, _, _ in blocks if k == part_n)
            _copy_attachment(msg, source, folder, part_n)
        out.append(msg)
    return chatlog.ChatLog(folder).write_journal(out)


def _copy_attachment(msg: dict, source: Path, target: Path, n: int) -> None:
    """Файл вложения части (`assistant/files/…`, `assistant/materials/….txt`)
    — в папку объединённой записи с приставкой `p<N>-`: исходные части
    удаляются после объединения. Путь вне папки ассистента части — как есть."""
    from meet.assist.chatlog import CHAT_DIR

    raw = msg.get("path")
    if isinstance(msg.get("ref"), str):
        msg["ref"] = f"p{n}-{msg['ref']}"
    if not isinstance(raw, str) or not raw:
        return
    try:
        path = Path(raw).resolve()
        rel = path.relative_to((Path(source) / CHAT_DIR).resolve())
    except (OSError, ValueError):
        return
    dest = Path(target) / CHAT_DIR / rel.parent / f"p{n}-{rel.name}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    except OSError:
        return  # нет файла — запись остаётся с прежним путём
    msg["path"] = str(dest)


# Перекодирование склейки в Opus, «время / длительность звука» одной дорожки:
# для хода по времени (ffmpeg своего хода здесь не отдаёт).
ENCODE_FACTOR = 0.02


def _stages(bus, roles: list[str], seconds: float):
    """Ход объединения одной шкалой (meet.progress): шаг на дорожку, внутри
    шага — по времени (ожидаемое — длительность встречи × ENCODE_FACTOR)."""
    if bus is None:
        return None
    from meet.progress import Stages, Step

    stages = Stages(bus, [Step(f"merge-{role}", "merge", 1.0, label="объединение", note=role)
                          for role in roles])
    stages.estimate(max(1.0, seconds * ENCODE_FACTOR * len(roles)))
    stages.start_ticking()
    return stages
