"""Временная встреча и удаление записи без следа.

**Временная встреча с ассистентом** — запись идёт как обычно (дорожки, живое
распознавание, чат с агентом-участником), но вне библиотеки записей:
`<data_dir>/tmp-meetings/<сеанс>/<YYYY-MM-DD_HH-MM>/`. Папку сеанса (случайное
имя из 12 шестнадцатеричных знаков) резидент создаёт сам до начала записи, с
правами только владельца; в ней же lock записи. Библиотека, поиск, группы,
фасеты и «Последняя запись» читают только папку записей — временной встречи
там нет. Агент временной встречи сеанс у провайдера не сохраняет вовсе
(`Participant(ephemeral=True)`): Claude Code — `--no-session-persistence`,
Codex — `--ephemeral`, OpenCode — сеанс удаляется после каждого вызова.

Конец встречи (обычный «Стоп») — ни расшифровки, ни анализа, ни названия, ни
выгрузки в базу знаний, ни пост-хука: папка сеанса удаляется целиком, а
сеансы агента у провайдера, если они всё же есть, забываются —
`llm.forget_session` по id из `assistant/sessions.json` (и прежним id из
`past`), чтобы и в истории CLI не осталось следа.

«Сохранить как обычную встречу» во время встречи ставит отметку `keep` в папку
сеанса: на «Стоп» запись переносится в библиотеку и дальше живёт как обычная
(расшифровка, анализ, хук). Переносится после остановки, а не сразу: на
Windows папку с открытыми файлами (идут дорожки и журнал чата) не переименовать.
Перенос никогда не кладёт запись внутрь существующей папки: имя выбирается в
момент переноса, на том же томе — одно переименование, на другом — копия в
скрытую `.<имя>.partial-<id>` рядом с целью, переименование на место и только
потом удаление источника (`moved` в папке сеанса: дальше — только удалить).

Сбой или убитый резидент: при следующем запуске оставшиеся сеансы удаляются
(и их сеансы агента забываются), отмеченные `keep` — переносятся в библиотеку,
`moved` — просто удаляются (`sweep`). Ссылки (symlink, junction) нигде не
проходятся: ни при поиске сеансов, ни при чтении их id, ни при удалении.

`wipe` — то же удаление для обычной записи, остановленной без сохранения
(«Остановить без сохранения»): папка записи, сеансы её агента и папка проекта
Claude Code вкладки «Агент» (`claude.forget_project`). Что не удалилось сразу
(файл держит другая программа), — удаляется повторно; осталось — папку
записи вызывающий прячет как `.<id>.deleting-…` (`hide_leftover`), и её
доудаляет восстановление при следующем запуске.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path

from meet import paths

ROOT_NAME = "tmp-meetings"
# Имя папки сеанса — как его даёт new_session (token_hex(6)).
SESSION_RE = re.compile(r"[0-9a-f]{12}")
# Отметка в папке сеанса: «Сохранить как обычную встречу».
KEEP_MARK = "keep"
# Запись уже перенесена в библиотеку — остаток сеанса только удалить (без
# забывания сеансов агента: они теперь у записи библиотеки).
MOVED_MARK = "moved"
PARTIAL_MARK = ".partial-"
SESSIONS_JSON = ("assistant", "sessions.json")
# Метка агента вкладки «Агент» в meta.json (tray_control.AGENT_SESSIONS_META).
AGENT_SESSIONS_META = "agent_sessions"
WIPE_TRIES = 6
WIPE_PAUSE_S = 0.5
MOVE_TRIES = 6
MOVE_PAUSE_S = 0.5
# Windows: ERROR_NOT_SAME_DEVICE — переименование между томами.
_WIN_NOT_SAME_DEVICE = 17


def root() -> Path:
    """Корень временных встреч — в данных приложения, не в библиотеке."""
    return paths.data_dir() / ROOT_NAME


def _private(path: Path) -> None:
    """Права только владельца. На Windows данные приложения и так закрыты
    для других пользователей (%LOCALAPPDATA%), chmod там ничего не значит."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def new_session() -> Path:
    """Папка сеанса новой временной встречи (создана, права — только владельцу)."""
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    _private(base)
    while True:
        session = base / secrets.token_hex(6)
        try:
            session.mkdir()
            break
        except FileExistsError:
            continue
    _private(session)
    return session


def _norm(path) -> str:
    """Путь для сравнения — без обращения к диску (и без раскрытия ссылок)."""
    return os.path.normcase(os.path.abspath(str(path)))


def is_link(path) -> bool:
    """Ссылка (symlink) или точка соединения NTFS (junction): не проходим."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    # Не любая точка повторной обработки: облачные файлы OneDrive — тоже они,
    # а папка записей в OneDrive — обычное дело. Только junction.
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction is not None and isjunction(path))


def _real_dir(path) -> bool:
    """Настоящая папка, не ссылка на неё."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode) and not is_link(path)
    except OSError:
        return False


def _real_subdirs(path: Path) -> list[Path]:
    """Подпапки без ссылок (ссылку на папку не считаем подпапкой)."""
    out = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                child = Path(entry.path)
                if entry.is_dir(follow_symlinks=False) and not is_link(child):
                    out.append(child)
    except OSError:
        pass
    return sorted(out)


def session_of(folder) -> Path | None:
    """Папка сеанса временной встречи, к которому относится `folder` (сама
    папка сеанса или запись в ней); не временная — None."""
    base = _norm(root())
    path = Path(folder)
    for candidate in (path, path.parent):
        if _norm(candidate.parent) == base and SESSION_RE.fullmatch(candidate.name):
            return candidate
    return None


def is_temporary(folder) -> bool:
    return folder is not None and session_of(folder) is not None


def loggable(folder) -> str:
    """Что писать в журнал о папке записи: у временной встречи — без пути и
    времени (журналы живут дольше встречи)."""
    return "временная встреча" if is_temporary(folder) else str(folder)


def mark_keep(session: Path) -> None:
    """«Сохранить как обычную встречу»: отметка переживает и сбой резидента."""
    (Path(session) / KEEP_MARK).write_text(json.dumps({"at": time.time()}), encoding="utf-8")


def is_kept(session: Path) -> bool:
    return (Path(session) / KEEP_MARK).is_file()


def conflicts_with_library(library_root) -> bool:
    """Библиотека совпадает с корнем временных встреч, лежит в нём или
    содержит его: уборка такого корня могла бы задеть настоящие записи."""
    if library_root is None:
        return False
    tmp, lib = Path(_norm(root())), Path(_norm(library_root))
    return tmp == lib or tmp.is_relative_to(lib) or lib.is_relative_to(tmp)


# --- сеансы агента ---------------------------------------------------------


def _read_json(folder: Path, *parts: str):
    """JSON внутри папки записи без хождения по ссылкам: ни сама папка, ни
    одна часть пути до файла не может быть ссылкой. Нет, ссылка, битый — None."""
    path = Path(folder)
    if is_link(path):
        return None
    for part in parts:
        path = path / part
        if is_link(path):
            return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def session_ids(folder) -> list[tuple[str, str]]:
    """Сеансы агента записи: (провайдер, id) из `assistant/sessions.json`
    (действующие и прежние — `past`) и метки вкладки «Агент» в meta.json.
    Битый или нечитаемый файл, ссылка — пропускаются."""
    out: list[tuple[str, str]] = []

    def add(provider, sid) -> None:
        if isinstance(provider, str) and provider and isinstance(sid, str) and sid:
            if (provider, sid) not in out:
                out.append((provider, sid))

    doc = _read_json(Path(folder), *SESSIONS_JSON)
    if isinstance(doc, dict):
        heads = doc.get("heads")
        for providers in (heads.values() if isinstance(heads, dict) else ()):
            if not isinstance(providers, dict):
                continue
            for provider, entry in providers.items():
                if isinstance(entry, dict):
                    add(provider, entry.get("id"))
        past = doc.get("past")
        for entry in (past if isinstance(past, list) else ()):
            if isinstance(entry, dict):
                add(entry.get("provider"), entry.get("id"))
    for provider, sid in _agent_marks(folder):
        add(provider, sid)
    return out


def _agent_marks(folder) -> list[tuple[str, str | None]]:
    """Метки вкладки «Агент» (meta.json): (провайдер, id или None)."""
    meta = _read_json(Path(folder), "meta.json")
    marks = meta.get(AGENT_SESSIONS_META) if isinstance(meta, dict) else None
    out: list[tuple[str, str | None]] = []
    if isinstance(marks, dict):
        for provider, mark in marks.items():
            if not isinstance(provider, str) or not mark:
                continue
            sid = mark.get("id") if isinstance(mark, dict) else None
            out.append((provider, sid if isinstance(sid, str) and sid else None))
    return out


# Провайдеры, у которых сеанс вкладки «Агент» Meet не знает по id (оболочка
# задаёт его только Claude Code) — забыть его нечем.
_NAMES = {"codex": "Codex", "opencode": "OpenCode"}


def forget_gaps(folder) -> list[str]:
    """Чью историю ассистента «Остановить без сохранения» не удалит: агенты
    вкладки «Агент» без известного id (Codex, OpenCode). → имена для текста
    вопроса; пусто — удалится всё."""
    out = []
    for provider, sid in _agent_marks(folder):
        name = _NAMES.get(provider)
        if name and sid is None and name not in out:
            out.append(name)
    return out


def _default_forget(provider: str, sid: str) -> int:
    from meet import llm

    return llm.forget_session(provider, sid)


def _default_forget_project(folder: Path, log=None) -> None:
    from meet.llm import claude

    claude.forget_project(folder, log=log)


def forget_sessions(folder, *, forget=None, log=None) -> int:
    """Забыть у провайдеров сеансы агента записи. → сколько удалено.
    Сбой одного сеанса не мешает остальным (и удалению папки)."""
    forget = forget or _default_forget
    removed = 0
    for provider, sid in session_ids(folder):
        try:
            removed += int(forget(provider, sid) or 0)
        except Exception as e:  # чужой CLI, занятый файл — не повод оставить папку
            if log:
                log(f"сеанс агента ({provider}) не забыт: {type(e).__name__}")
    return removed


def _rmtree(path: Path, *, tries: int = WIPE_TRIES, pause: float = WIPE_PAUSE_S,
            sleep=time.sleep) -> bool:
    """Удалить папку целиком, с повторами. Каждый проход удаляет всё, что
    можно, и идёт дальше мимо занятых файлов (их держит только что убитый
    процесс, плеер, антивирус); следующий проход — только за оставшимся.
    Ссылку не удаляет и не проходит. → удалена ли."""
    path = Path(path)
    if is_link(path):
        return False
    for attempt in range(max(1, tries)):
        if attempt:
            sleep(pause)
        if not os.path.lexists(path):
            return True
        _rmtree_once(path)
        if not os.path.lexists(path):
            return True
    return not os.path.lexists(path)


def _rmtree_once(path: Path) -> None:
    """Один проход: всё, что удаляется, — удалить, занятое — пропустить."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda *_: None)
    else:  # pragma: no cover — движок на 3.12, это на всякий случай
        shutil.rmtree(path, onerror=lambda *_: None)


def wipe(path, *, forget=None, forget_project=None, log=None, tries: int = WIPE_TRIES,
         pause: float = WIPE_PAUSE_S, sleep=time.sleep) -> bool:
    """Удалить запись (или папку сеанса временной встречи) без следа: сначала
    забыть сеансы агента у провайдеров и папку проекта Claude Code вкладки
    «Агент» (их id и путь — в самой папке), потом папку. Ссылку не трогает.
    → удалена ли целиком."""
    path = Path(path)
    if not os.path.lexists(path):
        return True
    if is_link(path) or not _real_dir(path):
        if log:
            log("удаление без следа отклонено: не папка записи (ссылка)")
        return False
    if forget_project is None:
        def forget_project(folder):
            _default_forget_project(folder, log)
    for folder in [path, *_real_subdirs(path)]:
        forget_sessions(folder, forget=forget, log=log)
        try:
            forget_project(folder)
        except Exception as e:
            if log:
                log(f"проект вкладки «Агент» не забыт: {type(e).__name__}")
    return _rmtree(path, tries=tries, pause=pause, sleep=sleep)


def hide_leftover(folder) -> Path | None:
    """Удалилась не вся папка записи: спрятать остаток. Сначала —
    переименовать в `.<id>.deleting-…`: из библиотеки она пропадает, а
    восстановление при следующем запуске (`library.leftover_deletions`)
    доудалит. Не переименовать (в ней открыт файл — Windows не даёт) —
    отметка `DISCARDED_MARK` внутри: библиотека такую папку не показывает,
    а уборка (`finish_later` сейчас, `sweep_discarded` при запуске) доудалит.
    → куда спрятана (новое имя или сама папка с отметкой) или None."""
    from meet import library

    folder = Path(folder)
    if not os.path.lexists(folder) or is_link(folder):
        return None
    aside = library._aside(folder)
    try:
        os.rename(folder, aside)
        return aside
    except OSError:
        pass
    try:
        (folder / library.DISCARDED_MARK).write_text("{}", encoding="utf-8")
    except OSError:
        return None
    return folder


def _still_discarded(folder: Path, active=None, log=None) -> bool:
    """Остаток всё ещё только остаток: это не папка идущей записи и в нём нет
    файлов новее отметки. Иначе в папку пишут заново (так не должно быть —
    запись не переиспользует папки, — но удалить чужую запись хуже) — отметку
    снять, ничего не удалять. → можно доудалять."""
    from meet import library

    folder = Path(folder)
    mark = folder / library.DISCARDED_MARK
    current = active() if callable(active) else active
    try:
        marked_at = os.lstat(mark).st_mtime
    except OSError:
        return False
    newer = False
    if not (current and _norm(current) == _norm(folder)):
        for dirpath, _dirs, files in os.walk(folder, followlinks=False):
            for name in files:
                path = os.path.join(dirpath, name)
                if name == library.DISCARDED_MARK and _norm(dirpath) == _norm(folder):
                    continue
                try:
                    if os.lstat(path).st_mtime > marked_at:
                        newer = True
                        break
                except OSError:
                    continue
            if newer:
                break
    if (current and _norm(current) == _norm(folder)) or newer:
        try:
            mark.unlink()
        except OSError:
            pass
        if log:
            log("остаток записи, остановленной без сохранения, не удалён: в папку пишут заново")
        return False
    return True


def _finish_marked(folder: Path) -> bool:
    """Один проход доуборки остатка с отметкой: удалить всё, кроме самой
    отметки (иначе после неудачного прохода остаток стал бы невидимой для
    уборки, но видимой в библиотеке папкой), и только когда больше ничего не
    осталось — отметку и папку. → удалён ли остаток целиком."""
    from meet import library

    try:
        entries = list(os.scandir(folder))
    except OSError:
        return not os.path.lexists(folder)
    for entry in entries:
        if entry.name == library.DISCARDED_MARK:
            continue
        path = Path(entry.path)
        try:
            if entry.is_dir(follow_symlinks=False) and not is_link(path):
                _rmtree_once(path)
            else:
                os.unlink(path)
        except OSError:
            pass
    try:
        if any(e.name != library.DISCARDED_MARK for e in os.scandir(folder)):
            return False
        (Path(folder) / library.DISCARDED_MARK).unlink(missing_ok=True)
        os.rmdir(folder)
    except OSError:
        return not os.path.lexists(folder)
    return True


def finish_later(folder, *, tries: int = 60, pause: float = 2.0, active=None, log=None,
                 sleep=time.sleep) -> None:
    """Доудалить остаток в фоне, когда его отпустят (плеер, антивирус).
    Папку идущей записи (`active` — () -> папка) и папку с файлами новее
    отметки не трогает (`_still_discarded`)."""
    import threading

    def work() -> None:
        for _ in range(max(1, tries)):
            sleep(pause)
            if not os.path.lexists(folder):
                return
            if not _still_discarded(Path(folder), active, log):
                return
            if _finish_marked(Path(folder)):
                if log:
                    log("остаток записи, остановленной без сохранения, удалён")
                return

    threading.Thread(target=work, name="meet-wipe-later", daemon=True).start()


def sweep_discarded(library_root, *, active=None, log=None) -> int:
    """Запуск резидента: остатки записей, остановленных без сохранения
    (отметка `DISCARDED_MARK`), — удалить; не удаляются — спрятать. Папку
    идущей записи и папку с файлами новее отметки не трогает."""
    from meet import library

    removed = 0
    for folder in _real_subdirs(Path(library_root)):
        if not (folder / library.DISCARDED_MARK).is_file():
            continue
        if not _still_discarded(folder, active, log):
            continue
        if _finish_marked(folder):
            removed += 1
        else:
            hide_leftover(folder)  # держат и сейчас — хотя бы переименовать
    if removed and log:
        log(f"доудалены записи, остановленные без сохранения: {removed}")
    return removed


# --- перенос в библиотеку ----------------------------------------------------


def library_target(folder, library_root) -> Path:
    """Куда ляжет запись в библиотеке: то же имя, занято — `_2`, `_3`…
    (`library.FOLDER_RE` такой хвост понимает)."""
    folder, library_root = Path(folder), Path(library_root)
    target = library_root / folder.name
    n = 2
    while os.path.lexists(target):
        target = library_root / f"{folder.name}_{n}"
        n += 1
    return target


def _cross_device(e: OSError) -> bool:
    return e.errno == errno.EXDEV or getattr(e, "winerror", None) == _WIN_NOT_SAME_DEVICE


def _move_direct(src: Path, dst: Path) -> None:
    """Переименование на том же томе. Существующую цель не заменяет: на
    Windows папку поверх папки не переименовать, на POSIX — проверка до
    (существующая пустая папка цели — гонка, в которую ничего не вкладывается)."""
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "цель уже есть", str(dst))
    os.replace(src, dst)


def _place(partial: Path, dst: Path) -> None:
    """Готовую копию — на место (то же правило: не поверх существующей)."""
    _move_direct(partial, dst)


def move_to_library(folder, library_root, *, tries: int = MOVE_TRIES,
                    pause: float = MOVE_PAUSE_S, sleep=time.sleep, log=None) -> Path:
    """Перенести запись временной встречи в библиотеку. Имя выбирается в
    момент переноса и всегда новое: запись никогда не ложится внутрь
    существующей папки. Тот же том — одно переименование; другой — копия в
    `.<имя>.partial-<id>` рядом с целью, переименование на место, отметка
    `moved` в сеансе и удаление источника (не удалился — доудалит уборка).
    Занята дольше повторов — OSError."""
    folder = Path(folder)
    library_root = Path(library_root)
    library_root.mkdir(parents=True, exist_ok=True)
    if is_link(folder) or not _real_dir(folder):
        raise OSError(errno.EINVAL, "не папка записи", str(folder))
    error: OSError | None = None
    for attempt in range(max(1, tries)):
        if attempt:
            sleep(pause)
        target = library_target(folder, library_root)
        try:
            _move_direct(folder, target)
            return target
        except FileExistsError as e:
            error = e
            continue  # имя заняли между выбором и переносом — выбрать заново
        except OSError as e:
            if not _cross_device(e):
                error = e
                continue  # занято — ещё раз
        return _copy_across(folder, library_root, log=log)
    raise error or OSError("не удалось перенести запись")


def _copy_across(folder: Path, library_root: Path, *, log=None) -> Path:
    partial = library_root / f".{folder.name}{PARTIAL_MARK}{secrets.token_hex(4)}"
    try:
        shutil.copytree(folder, partial, symlinks=True)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    for _ in range(100):
        target = library_target(folder, library_root)
        try:
            _place(partial, target)
            break
        except FileExistsError:
            continue
    else:
        shutil.rmtree(partial, ignore_errors=True)
        raise OSError(errno.EEXIST, "не нашлось свободного имени", str(library_root))
    session = session_of(folder)
    if session is not None:
        # Копия на месте: остаток сеанса — только удалить, не переносить снова.
        try:
            (session / MOVED_MARK).write_text("{}", encoding="utf-8")
            (session / KEEP_MARK).unlink(missing_ok=True)
        except OSError:
            pass
    if not _rmtree(folder) and log:
        log("источник перенесённой временной встречи удалён не до конца — доудалю при запуске")
    return target


def recordings_in(session: Path) -> list[Path]:
    """Папки записей в сеансе (обычно одна); ссылки — не записи."""
    return _real_subdirs(Path(session))


def adopt(session: Path, library_root, *, log=None) -> list[Path]:
    """Перенести записи отмеченного `keep` сеанса в библиотеку и убрать сеанс.
    → новые папки в библиотеке."""
    moved = []
    for folder in recordings_in(session):
        moved.append(move_to_library(folder, library_root, log=log))
    try:
        (Path(session) / MOVED_MARK).write_text("{}", encoding="utf-8")
    except OSError:
        pass
    _rmtree(Path(session))
    if log and moved:
        log(f"временная встреча сохранена как обычная: {len(moved)}")
    return moved


def sweep_partials(library_root, *, log=None) -> int:
    """Недокопированные переносы (`.<имя>.partial-…`) в библиотеке — удалить."""
    removed = 0
    lib = Path(library_root)
    try:
        entries = list(os.scandir(lib))
    except OSError:
        return 0
    for entry in entries:
        if entry.name.startswith(".") and PARTIAL_MARK in entry.name \
                and _real_dir(entry.path) and _rmtree(Path(entry.path)):
            removed += 1
    if removed and log:
        log(f"удалены недокопированные переносы временных встреч: {removed}")
    return removed


def sweep(*, before: float | None = None, skip=None, library_root=None, forget=None,
          forget_project=None, active=None, log=None) -> dict:
    """Запуск резидента: прошлый резидент упал или его убили посреди временной
    встречи. Сеансы без отметок — удалить (и забыть сеансы агента), с `keep`
    — перенести в библиотеку, с `moved` — только удалить. Берутся только
    настоящие папки (не ссылки) с именем сеанса. `before` — время запуска
    резидента: сеансы, тронутые позже, — его собственные (встречу могли
    начать, пока шла уборка), их не трогаем; `skip` — () -> папка сеанса,
    которая идёт сейчас; `active` — () -> папка идущей записи (остатки
    «Остановить без сохранения» в библиотеке её не трогают). Библиотека внутри корня или корень в ней — уборки
    нет вовсе. → {"wiped": [...], "kept": [...]}."""
    done: dict = {"wiped": [], "kept": []}
    base = root()
    if is_link(base) or not _real_dir(base):
        return done
    if conflicts_with_library(library_root):
        if log:
            log("временные встречи не убраны: папка записей пересекается с их папкой")
        return done
    if library_root is not None:
        sweep_partials(library_root, log=log)
        sweep_discarded(library_root, active=active, log=log)
    for session in _real_subdirs(base):
        if not SESSION_RE.fullmatch(session.name):
            continue
        current = skip() if callable(skip) else skip
        if current and _norm(current) == _norm(session):
            continue
        if before is not None:
            try:
                if os.lstat(session).st_mtime >= before:
                    continue
            except OSError:
                continue
        try:
            if (session / MOVED_MARK).is_file():
                if _rmtree(session):
                    done["wiped"].append(str(session))
            elif is_kept(session) and library_root is not None:
                done["kept"] += [str(p) for p in adopt(session, library_root, log=log)]
            elif wipe(session, forget=forget, forget_project=forget_project, log=log):
                done["wiped"].append(str(session))
        except OSError as e:
            if log:
                log(f"временная встреча прошлого запуска не убрана: {type(e).__name__}")
    return done
