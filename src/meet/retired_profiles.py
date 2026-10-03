"""Уборка после профилей людей: в 0.3.2 их убрали из Meet (Meet — про записи
встреч и историю решений, а не про анализ людей).

До 0.3.2 профили лежали в `<data_dir>/profiles/` (meet.profiles 0.3.1):
`<id>.json` — профиль, составленный моделью; `<id>.notes.md` — «Мои
заметки», их писал сам человек; `<id>.state.json` — служебное (ждущая задача
`pending`, скрытые утверждения, ошибка); `.<имя>.<hex>.tmp` — недописанные
атомарные записи; `_index/<id встречи>.json` — индекс реплик. `<id>` — 16
шестнадцатеричных знаков, ключ "id" в файле голоса `<voices>/<имя>.json`.

`run` при запуске резидента. Правило одно: удаляется только то, что писал
Meet, и ни одна заметка не теряется.

1. `profiles` — ссылка (symlink, junction): убирается только сама ссылка,
   в её цель уборка не заходит. В папке нет ни одного файла Meet (это чужая
   папка с таким же именем — например, data_dir указали на родительскую) —
   ничего не трогается.
2. Заметки (только файлы `<id>.notes.md`) переносятся одним файлом
   `<data_dir>/NOTES_NAME`: раздел на человека (имя — по id в файле голоса),
   с датой. Файл уже есть и он наш (с нашим заголовком) — дописываются только
   разделы, которых в нём нет; чужой файл с таким именем не трогается —
   рядом, с номером. Заметок нет — и файла нет.
3. Байты заметки, которую пришлось декодировать не как UTF-8, и недописанная
   правка заметки (`.<id>.notes.md.<hex>.tmp` новее самой заметки) копируются
   как есть в `<data_dir>/SOURCES_NAME/`. Заметку не удалось прочитать или
   скопировать — её файл остаётся на месте до следующего запуска.
4. Удаляются только файлы Meet (имена выше) и `_index/` с файлами индекса;
   чужие файлы остаются, папка удаляется, только если опустела. Прерванные
   задачи профилей (`pending` в state-файлах) уходят вместе с ними.
5. Из config.json убирается секция `profiles`, из `llm_stats.json` — замеры
   задач профилей.
6. Остаётся отметка NOTICE_NAME: окно один раз показывает в «Голосах» строку
   об этом; «Понятно» её снимает (`dismiss`).

Голоса не трогаются: файлы голосов только читаются. Сбой внутри `run` наружу
не выходит: строка в журнал, следующий запуск попробует снова. Папки
`profiles` нет — ничего не делается, так что повторный запуск безвреден.
"""

import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

DIR_NAME = "profiles"
NOTES_NAME = "Заметки о людях (из профилей).md"
SOURCES_NAME = "Заметки о людях (из профилей) — исходные файлы"
NOTICE_NAME = "profiles-removed.json"
NOTES_HEADER = "# Заметки о людях (из профилей)"
INDEX_DIR = "_index"

# Имена, которые писал Meet 0.3.1 (meet.profiles, meet.profile_index).
_ID = r"[0-9a-f]{16}"
_KINDS = r"(?:json|state\.json|notes\.md)"
_TMP = r"\.[0-9a-f]{32}\.tmp"
_OWN = re.compile(rf"^{_ID}\.{_KINDS}$")
_OWN_TMP = re.compile(rf"^\.{_ID}\.{_KINDS}{_TMP}$")
_NOTE = re.compile(rf"^({_ID})\.notes\.md$")
_NOTE_TMP = re.compile(rf"^\.({_ID})\.notes\.md{_TMP}$")
_INDEX_FILE = re.compile(r"^[^.][^\\/]*\.json$")
_INDEX_TMP = re.compile(rf"^\.[^\\/]+\.json{_TMP}$")

# Замеры длительности задач модели (meet.llm_progress): ключи «вид:провайдер».
_STATS_NAME = "llm_stats.json"
_STATS_KINDS = ("profile", "profile-check")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_text(path: Path, text: str) -> None:
    _write_bytes(path, text.encode("utf-8"))


def _is_link(path: Path) -> bool:
    """Symlink или junction (любая точка повторной обработки Windows): в её
    цель уборка не заходит."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(st, "st_file_attributes", 0) & reparse)


def _unlink_link(path: Path) -> None:
    """Убрать ссылку, не трогая цель (junction на Windows — как папку)."""
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


def _unlink_file(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)  # только наш обычный файл, не ссылка
        path.unlink()


def decode(raw: bytes) -> tuple[str, bool]:
    """Текст заметки и был ли это UTF-8. Не UTF-8 — cp1251, иначе с заменой
    непонятных байтов: текст не теряется никогда."""
    try:
        return raw.decode("utf-8-sig"), True
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1251"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), False


def _names_by_id(voices: Path | None) -> dict[str, str]:
    """id человека → имя (файл голоса). Только чтение."""
    out: dict[str, str] = {}
    try:
        if voices is None or not Path(voices).is_dir():
            return out
        paths = sorted(Path(voices).glob("*.json"))
    except OSError:
        return out
    for path in paths:
        data = _read_json(path)
        pid = data.get("id") if isinstance(data, dict) else None
        if isinstance(pid, str) and pid:
            out.setdefault(pid, path.stem)
    return out


def _entries(folder: Path) -> list[Path]:
    try:
        return sorted(folder.iterdir())
    except OSError:
        return []


def _own_entry(path: Path) -> bool:
    name = path.name
    return bool(_OWN.match(name) or _OWN_TMP.match(name)) or (name == INDEX_DIR)


class _Collected:
    """Что нашлось среди заметок: тексты для общего файла, байты для папки
    исходных файлов и файлы, которые нельзя удалять."""

    def __init__(self) -> None:
        self.notes: list[dict] = []
        self.raw: list[tuple[str, bytes, Path]] = []  # (имя копии, байты, исходный файл)
        self.keep: set[Path] = set()
        self.problems: list[str] = []


def collect_notes(folder: Path, voices: Path | None) -> _Collected:
    """Заметки Meet в папке профилей (только `<id>.notes.md` и их недописанные
    правки). Ничего не удаляет и не пишет."""
    names = _names_by_id(voices)
    got = _Collected()
    entries = _entries(Path(folder))
    saved: dict[str, tuple[bytes, float]] = {}
    for path in entries:
        m = _NOTE.match(path.name)
        if not m or _is_link(path) or not path.is_file():
            continue
        pid = m.group(1)
        name = names.get(pid) or f"Без имени ({pid[:6]})"
        try:
            raw = path.read_bytes()
            mtime = path.stat().st_mtime
        except OSError as e:
            got.keep.add(path)
            got.problems.append(f"{path.name}: {type(e).__name__}: {e}")
            continue
        saved[pid] = (raw, mtime)
        text, utf8 = decode(raw)
        if not text.strip():
            continue
        if not utf8:
            got.raw.append((f"{name} — {path.name}", raw, path))
        got.notes.append({
            "name": name,
            "date": time.strftime("%d.%m.%Y", time.localtime(mtime)),
            "text": text.replace("\r\n", "\n").strip(),
            "path": path,
        })
    for path in entries:
        m = _NOTE_TMP.match(path.name)
        if not m or _is_link(path) or not path.is_file():
            continue
        pid = m.group(1)
        try:
            raw = path.read_bytes()
            mtime = path.stat().st_mtime
        except OSError as e:
            got.keep.add(path)
            got.problems.append(f"{path.name}: {type(e).__name__}: {e}")
            continue
        base = saved.get(pid)
        if not raw.strip() or (base is not None and (raw == base[0] or mtime <= base[1])):
            continue  # пустая, та же или старше сохранённой — нечего беречь
        name = names.get(pid) or f"Без имени ({pid[:6]})"
        got.raw.append((f"{name} — несохранённая правка {pid}.notes.md", raw, path))
    got.notes.sort(key=lambda n: (n["name"].casefold(), n["name"]))
    return got


def _block(n: dict) -> str:
    return f"## {n['name']}\n\n_Заметка от {n['date']}_\n\n{n['text']}"


def notes_text(notes: list[dict]) -> str:
    parts = [
        NOTES_HEADER,
        "",
        "Профили людей убраны из Meet в версии 0.3.2: Meet сосредоточен на записи встреч "
        "и истории решений. Сгенерированные профили удалены; здесь — ваши заметки из вкладки "
        "«Профиль», как вы их написали.",
    ]
    for n in notes:
        parts += ["", _block(n)]
    return "\n".join(parts) + "\n"


def _save_notes(data_dir: Path, notes: list[dict]) -> Path:
    """Записать заметки. Наш файл уже есть (прошлый запуск, оборванный на
    полпути) — дописать только недостающие разделы; чужой — не трогать."""
    stem = NOTES_NAME.removesuffix(".md")
    for k in range(1, 100):
        path = data_dir / (NOTES_NAME if k == 1 else f"{stem} ({k}).md")
        try:
            existing = path.read_bytes().decode("utf-8-sig")
        except FileNotFoundError:
            _write_text(path, notes_text(notes))
            return path
        except (OSError, UnicodeDecodeError):
            continue
        if not existing.startswith(NOTES_HEADER):
            continue
        flat = existing.replace("\r\n", "\n")  # файл могли пересохранить в редакторе
        missing = [n for n in notes if _block(n) not in flat]
        if missing:
            _write_text(path, existing.rstrip("\n") + "\n\n" + "\n\n".join(_block(n) for n in missing) + "\n")
        return path
    raise OSError("не нашлось свободного имени для файла заметок")


def _save_raw(data_dir: Path, got: _Collected, say) -> None:
    """Байты заметок — в папку исходных файлов. Не вышло — исходный файл
    остаётся на месте."""
    if not got.raw:
        return
    target = data_dir / SOURCES_NAME
    for name, raw, source in got.raw:
        try:
            target.mkdir(exist_ok=True)
            stem, dot, ext = name.rpartition(".")
            for k in range(1, 100):
                path = target / (name if k == 1 else f"{stem} ({k}){dot}{ext}")
                try:
                    if path.read_bytes() == raw:
                        break
                except FileNotFoundError:
                    _write_bytes(path, raw)
                    break
            else:
                raise OSError("не нашлось свободного имени")
        except OSError as e:
            got.keep.add(source)
            say(f"профили: {source.name} не скопирован ({e}) — файл оставлен до следующего запуска")


def _remove_index(index: Path) -> bool:
    """Файлы индекса реплик; чужое остаётся. → опустела ли папка (и удалена)."""
    if _is_link(index):
        _unlink_link(index)
        return True
    for path in _entries(index):
        if _is_link(path):
            if _INDEX_FILE.match(path.name) or _INDEX_TMP.match(path.name):
                _unlink_link(path)
            continue
        if path.is_file() and (_INDEX_FILE.match(path.name) or _INDEX_TMP.match(path.name)):
            _unlink_file(path)
    try:
        index.rmdir()
        return True
    except OSError:
        return False


def _remove_own(folder: Path, keep: set[Path], say) -> list[str]:
    """Удалить файлы Meet, кроме `keep`; пустую папку — тоже. → что осталось."""
    for path in _entries(folder):
        if path in keep or not _own_entry(path):
            continue
        try:
            if _is_link(path):
                _unlink_link(path)
            elif path.name == INDEX_DIR:
                if path.is_dir():
                    _remove_index(path)
            elif path.is_file():
                _unlink_file(path)
        except OSError as e:
            say(f"профили: {path.name} не удалён: {e}")
    left = [p.name for p in _entries(folder)]
    if not left:
        try:
            folder.rmdir()
        except OSError as e:
            say(f"профили: папка не удалена: {e}")
    return left


def _drop_stats(data_dir: Path) -> None:
    path = data_dir / _STATS_NAME
    data = _read_json(path)
    if not isinstance(data, dict):
        return
    kept = {k: v for k, v in data.items() if str(k).split(":", 1)[0] not in _STATS_KINDS}
    if len(kept) != len(data):
        _write_text(path, json.dumps(kept, ensure_ascii=False))


def _own_notes_file(data_dir: Path) -> Path | None:
    path = data_dir / NOTES_NAME
    try:
        return path if path.read_bytes().decode("utf-8-sig").startswith(NOTES_HEADER) else None
    except (OSError, UnicodeDecodeError):
        return None


def notice(data_dir: Path) -> dict | None:
    """Отметка для окна: {"notes": путь файла заметок или None, "folder"} или None."""
    data = _read_json(Path(data_dir) / NOTICE_NAME)
    if not isinstance(data, dict):
        return None
    notes = data.get("notes")
    return {"notes": notes if isinstance(notes, str) and notes else None, "folder": str(data_dir)}


def dismiss(data_dir: Path) -> None:
    (Path(data_dir) / NOTICE_NAME).unlink(missing_ok=True)


def _drop_settings(data_dir: Path, config: Path | None, say) -> None:
    try:
        from meet import settings

        settings.drop_retired(config)
    except Exception as e:
        say(f"профили: секция настроек не убрана: {type(e).__name__}: {e}")
    try:
        _drop_stats(data_dir)
    except OSError:
        pass


def run(data_dir: Path, voices: Path | None, *, config: Path | None = None, log=None) -> dict | None:
    """Уборка (см. модуль). → отметка для окна, если что-то убиралось, иначе
    None. `config` — файл настроек (по умолчанию config.json приложения).
    Не бросает: сбой — строка в журнал, следующий запуск попробует снова."""
    say = log or (lambda _text: None)
    try:
        return _run(Path(data_dir), voices, config, say)
    except Exception as e:
        say(f"профили: уборка не закончена ({type(e).__name__}: {e}) — повторю при следующем запуске")
        return None


def _run(data_dir: Path, voices: Path | None, config: Path | None, say) -> dict | None:
    folder = data_dir / DIR_NAME
    if _is_link(folder):
        _unlink_link(folder)
        say("профили: папка profiles была ссылкой — убрана только ссылка, её цель не тронута")
        _drop_settings(data_dir, config, say)
        return None
    if not folder.is_dir():
        return None
    if not any(_own_entry(p) for p in _entries(folder)):
        say("профили: в папке profiles нет файлов Meet — не трогаю")
        return None

    got = collect_notes(folder, voices)
    for problem in got.problems:
        say(f"профили: заметка не прочитана ({problem}) — файл оставлен до следующего запуска")
    _save_raw(data_dir, got, say)
    notes_path: Path | None = None
    if got.notes:
        try:
            notes_path = _save_notes(data_dir, got.notes)
        except OSError as e:
            got.keep |= {n["path"] for n in got.notes}
            say(f"профили: заметки не перенесены ({e}) — их файлы оставлены до следующего запуска")
    left = _remove_own(folder, got.keep, say)
    if left:
        foreign = [name for name in left if not _own_entry(folder / name)]
        if foreign:
            say(f"профили: в папке profiles остались чужие файлы ({len(foreign)}) — не трогаю")
    _drop_settings(data_dir, config, say)

    previous = _read_json(data_dir / NOTICE_NAME)
    if notes_path is None and isinstance(previous, dict) and isinstance(previous.get("notes"), str):
        notes_path = Path(previous["notes"])
    if notes_path is None:
        notes_path = _own_notes_file(data_dir)  # другой процесс успел раньше
    marker = {"notes": str(notes_path) if notes_path else None, "at": time.time()}
    try:
        _write_text(data_dir / NOTICE_NAME, json.dumps(marker, ensure_ascii=False))
    except OSError as e:
        say(f"профили: отметка для окна не записана: {e}")
    say("профили людей убраны: сгенерированные профили удалены"
        + (f", заметки — в {notes_path.name}" if notes_path else ""))
    return notice(data_dir)
