"""Уборка после профилей людей: в 0.3.2 их убрали из Meet (Meet — про записи
встреч и историю решений, а не про анализ людей).

До 0.3.2 профили лежали в `<data_dir>/profiles/`: `<id>.json` — профиль,
составленный моделью; `<id>.notes.md` — «Мои заметки», их писал сам человек;
`<id>.state.json` — служебное (ждущая задача `pending`, ошибка); `_index/` —
индекс реплик; прочие служебные файлы. `<id>` — ключ "id" в файле голоса
`<voices>/<имя>.json`.

`run` при запуске резидента:

1. Заметки переносятся одним файлом `<data_dir>/NOTES_NAME`: раздел на
   человека (имя — по id в файле голоса), с датой заметки. Заметок нет — и
   файла нет. Файл с таким именем уже есть и он другой — рядом, с номером.
2. Папка `profiles` удаляется целиком: профили, индекс, служебные отметки —
   прерванные задачи профилей не вернутся. Заметки не записались — их файлы
   остаются (следующий запуск попробует снова), остальное удаляется.
3. Из config.json убирается секция `profiles`, из `llm_stats.json` — замеры
   задач профилей.
4. Остаётся отметка NOTICE_NAME: окно один раз показывает в «Голосах» строку
   об этом; «Понятно» её снимает (`dismiss`).

Голоса не трогаются: файлы голосов только читаются. Папки `profiles` нет —
ничего не делается, так что повторный запуск безвреден.
"""

import json
import os
import shutil
import stat
import sys
import time
import uuid
from pathlib import Path

DIR_NAME = "profiles"
NOTES_NAME = "Заметки о людях (из профилей).md"
NOTICE_NAME = "profiles-removed.json"
_NOTES_SUFFIX = ".notes.md"
# Замеры длительности задач модели (meet.llm_progress): ключи «вид:провайдер».
_STATS_NAME = "llm_stats.json"
_STATS_KINDS = ("profile", "profile-check")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _names_by_id(voices: Path | None) -> dict[str, str]:
    """id человека → имя (файл голоса). Только чтение."""
    out: dict[str, str] = {}
    if voices is None or not Path(voices).is_dir():
        return out
    for path in sorted(Path(voices).glob("*.json")):
        data = _read_json(path)
        pid = data.get("id") if isinstance(data, dict) else None
        if isinstance(pid, str) and pid:
            out.setdefault(pid, path.stem)
    return out


def collect_notes(folder: Path, voices: Path | None) -> list[dict]:
    """Непустые заметки: [{"name", "date", "text", "path"}] по имени."""
    names = _names_by_id(voices)
    found = []
    for path in sorted(Path(folder).glob(f"*{_NOTES_SUFFIX}")):
        if path.name.startswith("."):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if not text.strip():
            continue
        pid = path.name[: -len(_NOTES_SUFFIX)]
        found.append({
            "name": names.get(pid) or f"Без имени ({pid[:6]})",
            "date": time.strftime("%d.%m.%Y", time.localtime(mtime)),
            "text": text.strip(),
            "path": path,
        })
    found.sort(key=lambda n: (n["name"].casefold(), n["name"]))
    return found


def notes_text(notes: list[dict]) -> str:
    parts = [
        "# Заметки о людях (из профилей)",
        "",
        "Профили людей убраны из Meet в версии 0.3.2: Meet сосредоточен на записи встреч "
        "и истории решений. Сгенерированные профили удалены; здесь — ваши заметки из вкладки "
        "«Профиль», как вы их написали.",
    ]
    for n in notes:
        parts += ["", f"## {n['name']}", "", f"_Заметка от {n['date']}_", "", n["text"]]
    return "\n".join(parts) + "\n"


def _notes_target(data_dir: Path, text: str) -> tuple[Path, bool]:
    """Куда писать заметки и нужно ли писать: файл с тем же текстом уже есть
    (прошлый запуск не успел удалить папку) — он и есть итог."""
    stem, suffix = NOTES_NAME.removesuffix(".md"), ".md"
    for k in range(1, 100):
        path = data_dir / (NOTES_NAME if k == 1 else f"{stem} ({k}){suffix}")
        try:
            existing = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return path, True
        except OSError:
            continue
        if existing == text:
            return path, False
    raise OSError("не нашлось свободного имени для файла заметок")


def _force_remove(func, path, _exc) -> None:
    """Файл только для чтения — снять атрибут и повторить (rmtree на Windows)."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


def _remove_tree(folder: Path, keep: set[Path]) -> None:
    """Удалить всё в папке, кроме `keep`; пустую — и саму папку."""
    for entry in list(folder.iterdir()):
        if entry in keep:
            continue
        if entry.is_dir() and not entry.is_symlink():
            _rmtree(entry)
        else:
            try:
                entry.unlink()
            except OSError:
                _force_remove(os.unlink, entry, None)
    if not keep:
        try:
            folder.rmdir()
        except OSError:
            _rmtree(folder)


def _drop_stats(data_dir: Path) -> None:
    path = data_dir / _STATS_NAME
    data = _read_json(path)
    if not isinstance(data, dict):
        return
    kept = {k: v for k, v in data.items() if str(k).split(":", 1)[0] not in _STATS_KINDS}
    if len(kept) != len(data):
        _write_text(path, json.dumps(kept, ensure_ascii=False))


def notice(data_dir: Path) -> dict | None:
    """Отметка для окна: {"notes": путь файла заметок или None, "folder"} или None."""
    data = _read_json(Path(data_dir) / NOTICE_NAME)
    if not isinstance(data, dict):
        return None
    notes = data.get("notes")
    return {"notes": notes if isinstance(notes, str) and notes else None, "folder": str(data_dir)}


def dismiss(data_dir: Path) -> None:
    (Path(data_dir) / NOTICE_NAME).unlink(missing_ok=True)


def run(data_dir: Path, voices: Path | None, *, config: Path | None = None, log=None) -> dict | None:
    """Уборка (см. модуль). → отметка для окна, если что-то убиралось, иначе None.
    `config` — файл настроек (по умолчанию config.json приложения)."""
    say = log or (lambda _text: None)
    data_dir = Path(data_dir)
    folder = data_dir / DIR_NAME
    if not folder.is_dir():
        return None
    notes = collect_notes(folder, voices)
    notes_path: Path | None = None
    keep: set[Path] = set()
    if notes:
        text = notes_text(notes)
        try:
            notes_path, write = _notes_target(data_dir, text)
            if write:
                _write_text(notes_path, text)
        except OSError as e:
            notes_path = None
            keep = {n["path"] for n in notes}
            say(f"профили: заметки не перенесены ({e}) — их файлы оставлены до следующего запуска")
    try:
        _remove_tree(folder, keep)
    except OSError as e:
        say(f"профили: папка удалена не полностью: {e}")
    try:
        from meet import settings

        settings.drop_retired(config)
    except Exception as e:
        say(f"профили: секция настроек не убрана: {type(e).__name__}: {e}")
    try:
        _drop_stats(data_dir)
    except OSError:
        pass
    previous = _read_json(data_dir / NOTICE_NAME)
    if notes_path is None and isinstance(previous, dict) and isinstance(previous.get("notes"), str):
        notes_path = Path(previous["notes"])
    marker = {"notes": str(notes_path) if notes_path else None, "at": time.time()}
    try:
        _write_text(data_dir / NOTICE_NAME, json.dumps(marker, ensure_ascii=False))
    except OSError as e:
        say(f"профили: отметка для окна не записана: {e}")
    say("профили людей убраны: сгенерированные профили удалены"
        + (f", заметки — в {notes_path.name}" if notes_path else ""))
    return notice(data_dir)
