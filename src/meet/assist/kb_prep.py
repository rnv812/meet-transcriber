"""База знаний и прошлые встречи для агента: карта и буквальные исполнители
его просьб (V4, `v4-simple.md` §2–3).

Весь интеллект — у агента: Meet ничего не подбирает, не оценивает и не
решает, когда заглянуть. Он только:

* **рисует карту** (`KnowledgeBase.kb_map`) — папки и названия документов
  базы знаний без содержимого, сжатые под бюджет символов, и прошлые встречи
  группы (дата, название). Папка группы (`kb_folder` в `.meet-groups.json`)
  — целиком, первой; остальные папки — по алфавиту: число документов, до
  MAP_TOP_TITLES самых свежих названий и «ещё K». Строится один раз на
  сессию (кэш по аргументам). Настройка `assist.kb_map` («Показывать
  ассистенту карту: базу знаний и прошлые встречи группы») выключает карту
  целиком — ни структуры базы, ни списка прошлых встреч группы;
* **называет запреты** для инструментов агента: `kb_exclude_paths` —
  абсолютные пути исключённых папок (`assist.kb_exclude`) как на диске;
  цикл агента отдаёт их провайдеру в `deny_paths`, правила своего CLI
  (у Claude Code — `Read(//d/…)`) собирает слой провайдеров. Читает и ищет
  агент сам, своими инструментами (§6);
* **запасной путь для локальной модели без инструментов** — исполняет её
  просьбы буквально: `kb_read(paths)` — разобрать файл в текст с местами
  (`meet.materials`), `kb_search(query, in_path)` — найти слова (по
  основам, как `kb_index`) и вернуть выдержки с местами, `kb_list(folder)`
  — содержимое папки.

Пути — относительные: в базе знаний — как на карте («Проекты/Альфа/План.md»),
встречи библиотеки — `meet:<папка записи>[/файл]` («meet:2026-09-30_10-00/
summary.md»; `meet:` — список встреч). Выйти за базу или библиотеку нельзя:
абсолютные пути, «..», служебные и скрытые папки, исключённые
(`assist.kb_exclude`) и ссылки (symlink), ведущие наружу, отклоняются. У
встречи читаются только текстовые файлы (.md, .txt) и расшифровка
(`transcript.md` — из transcript.json). Все ответы ограничены по размеру.

Ошибки пути не бросаются, а возвращаются в ответе (`error`): агент видит, что
не так, и цикл не падает. Модель не вызывается; в цикл агента и API не
подключено (задачи 4 и 6).
"""

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from meet import groups, library, materials
from meet.assist import kb_index

MEET_PREFIX = "meet:"
# Карта.
MAP_BUDGET_CHARS = 10_000
MAP_TOP_TITLES = 5
MAP_MEETINGS = 10
MAP_MAX_FILES = 20_000
# Папка группы целиком, но не больше этой доли бюджета карты.
GROUP_SHARE = 0.6
ROOT_LABEL = "(корень)"
# read: файлов за просьбу, символов на файл и всего.
READ_MAX_FILES = 5
READ_MAX_CHARS = 20_000
READ_TOTAL_CHARS = 40_000
# search: сколько файлов просмотреть (из них не текстовых — разбор дороже),
# сколько выдержек вернуть и какой длины.
SEARCH_MAX_FILES = 2_000
SEARCH_MAX_PARSED = 100
SEARCH_MAX_HITS = 20
SNIPPET_CHARS = 300
SEARCH_TOTAL_CHARS = 6_000
# Весь поиск — не дольше (сверх — отдаётся найденное, `timed_out`).
SEARCH_DEADLINE_S = 20.0
QUERY_MAX_CHARS = 200
# list: сколько строк папки или встреч.
LIST_MAX = 200
# Встреча: что из её папки можно читать.
MEETING_SUFFIXES = (".md", ".txt")
TRANSCRIPT = "transcript.md"

NOT_CONFIGURED = "База знаний не настроена"
NO_LIBRARY = "Библиотека встреч недоступна"
BAD_PATH = "Путь должен быть относительным, внутри базы знаний или встречи (meet:…), без «..»"
EXCLUDED = "Эта папка закрыта для ассистента (исключения базы знаний)"
NOT_FOUND = "Нет такого файла или папки: {}"
NOT_A_FILE = "Это папка, а не файл: {}"
NOT_A_FOLDER = "Это файл, а не папка: {}"
NOT_READABLE = "Этот файл встречи не читается: {}"
NO_QUERY = "Пустой запрос"
TOO_MANY_FILES = "За раз — не больше {} файлов"
READ_SPENT = "Лимит чтения за одну просьбу исчерпан — попросите этот файл отдельно"
UNREADABLE = "Файл не разобрать: {} ({})"
CUT = "[… обрезано: показаны первые {} символов]"


class AccessError(ValueError):
    """Путь не годится (текст — агенту и человеку)."""


@dataclass(frozen=True)
class Place:
    """Разобранный путь: `kind` — "kb" | "meet" | "meetings" (список встреч);
    `path` — файл или папка на диске (у meetings — папка библиотеки),
    `rel` — путь так, как его показывать агенту."""

    kind: str
    path: Path
    rel: str


def _docs_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "документ"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "документа"
    return "документов"


_BAD_CHARS = re.compile(r'[\x00-\x1f<>"|?*]')


def _parts(text: str) -> list[str]:
    """Части относительного пути; абсолютный, с диском, «..», «.» или
    недопустимыми символами — AccessError."""
    if not isinstance(text, str):
        raise AccessError(BAD_PATH)
    text = text.strip().strip("«»").replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or _BAD_CHARS.search(text) or ":" in text:
        raise AccessError(BAD_PATH)
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if any(p in (".", "..") for p in parts):
        raise AccessError(BAD_PATH)
    return parts


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


class KnowledgeBase:
    """База знаний и встречи на сессию агента: `root` —
    `assistant.knowledge_dir` (None — не настроена), `exclude` —
    `assist.kb_exclude`, `library_root` — папка записей, `show_map` —
    `assist.kb_map`."""

    def __init__(self, root, *, exclude=(), library_root=None, show_map: bool = True) -> None:
        # Корень — как на деле (ссылки, короткие имена 8.3): пути внутри
        # сравниваются и открываются от него.
        self.root = _resolved(Path(root)) if root else None
        self.exclude = tuple(exclude or ())
        self.prefixes = kb_index.exclude_prefixes(self.exclude)
        self.library_root = Path(library_root) if library_root else None
        self.show_map = show_map
        self._maps: dict[tuple, str] = {}
        # разобранные файлы: путь → (размер, время правки, Parsed)
        self._parsed: dict[str, tuple[int, int, materials.Parsed]] = {}

    @property
    def configured(self) -> bool:
        return self.root is not None and self.root.is_dir()

    def exclude_paths(self) -> list[str]:
        """Запреты для инструментов агента (см. kb_exclude_paths)."""
        return kb_exclude_paths(self.root, self.exclude)

    # --- пути

    def _hidden(self, parts: list[str]) -> bool:
        return any(p.startswith(".") or p in kb_index.SKIP_DIRS for p in parts)

    def _allowed(self, path: Path) -> bool:
        """Куда на деле ведёт `path` (с учётом ссылок и junction) — внутри
        базы, не в скрытой и не в исключённой папке."""
        try:
            real = path.resolve().relative_to(self.root.resolve()).as_posix()
        except (ValueError, OSError):
            return False
        parts = [p for p in real.split("/") if p and p != "."]
        return not self._hidden(parts) and not kb_index.is_excluded("/".join(parts), self.prefixes)

    def place(self, path: str | None) -> Place:
        """Путь из просьбы агента → Place или AccessError. Пусто — корень базы."""
        raw = (path or "").strip() if isinstance(path, str) or path is None else path
        if isinstance(raw, str) and raw.lower().startswith(MEET_PREFIX):
            return self._meeting_place(raw[len(MEET_PREFIX):])
        parts = _parts(raw)
        if not self.configured:
            raise AccessError(NOT_CONFIGURED)
        rel = "/".join(parts)
        if self._hidden(parts) or kb_index.is_excluded(rel, self.prefixes):
            raise AccessError(EXCLUDED if kb_index.is_excluded(rel, self.prefixes) else BAD_PATH)
        full = self.root.joinpath(*parts)
        if not full.exists():
            raise AccessError(NOT_FOUND.format(rel or ROOT_LABEL))
        if not _inside(full, self.root):  # ссылка наружу
            raise AccessError(BAD_PATH)
        if not self._allowed(full):
            raise AccessError(EXCLUDED)  # ссылка внутрь исключённой или скрытой папки
        # Открывается тот путь, что проверен: подмена ссылки после проверки
        # не уведёт за базу.
        return Place("kb", _resolved(full), rel)

    def _meeting_place(self, rest: str) -> Place:
        if self.library_root is None or not self.library_root.is_dir():
            raise AccessError(NO_LIBRARY)
        parts = _parts(rest)
        if not parts:
            return Place("meetings", self.library_root, MEET_PREFIX)
        if len(parts) > 2 or parts[0].startswith("."):
            raise AccessError(BAD_PATH)
        folder = self.library_root / parts[0]
        if not library.is_recording(folder) or not _inside(folder, self.library_root) \
                or folder.resolve().parent != self.library_root.resolve():
            raise AccessError(NOT_FOUND.format(MEET_PREFIX + parts[0]))
        if len(parts) == 1:
            return Place("meet", folder, MEET_PREFIX + parts[0])
        name = parts[1]
        rel = f"{MEET_PREFIX}{parts[0]}/{name}"
        file = folder / name
        if name == TRANSCRIPT and not os.path.lexists(file):
            return Place("meet", file, rel)  # из transcript.json
        if name.startswith(".") or Path(name).suffix.lower() not in MEETING_SUFFIXES:
            raise AccessError(NOT_READABLE.format(rel))
        if not file.is_file():
            raise AccessError(NOT_FOUND.format(rel))
        if not _inside(file, folder):
            raise AccessError(BAD_PATH)
        return Place("meet", file, rel)

    # --- файлы

    def _meeting_files(self, folder: Path) -> list[str]:
        names = []
        if library.read_transcript(folder) is not None:
            names.append(TRANSCRIPT)
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []
        for p in entries:
            if (p.is_file() and not p.name.startswith(".") and p.suffix.lower() in MEETING_SUFFIXES
                    and p.name != TRANSCRIPT and _inside(p, folder)):
                names.append(p.name)
        return names

    def _parse(self, place: Place) -> materials.Parsed:
        """Текст файла с местами (кэш на сессию, пока файл не менялся)."""
        rendered = place.kind == "meet" and place.path.name == TRANSCRIPT and not os.path.lexists(place.path)
        source = library.transcript_path(place.path.parent) if rendered else place.path
        try:
            st = source.stat()
        except OSError:
            raise AccessError(NOT_FOUND.format(place.rel)) from None
        key = str(source)
        hit = self._parsed.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        try:
            if rendered:  # расшифровка — из transcript.json, кэш по нему
                from meet import export

                data = library.with_display_names(library.read_transcript(place.path.parent))
                if data is None:
                    raise AccessError(NOT_FOUND.format(place.rel))
                parsed = materials.parse_text(export.render(data, "md"), TRANSCRIPT)
            else:
                parsed = materials.parse(place.path)
        except AccessError:
            raise
        except materials.MaterialError as e:
            raise AccessError(str(e)) from None
        except Exception as e:  # любой сбой разбора — ошибка этого файла, не всей просьбы
            raise AccessError(UNREADABLE.format(place.rel, type(e).__name__)) from None
        self._parsed[key] = (st.st_size, st.st_mtime_ns, parsed)
        return parsed

    def doc_list(self, limit: int = MAP_MAX_FILES) -> list[str]:
        """Документы базы — пути относительно неё через «/» (без исключённых,
        скрытых и ссылок наружу), не больше `limit`. Базы нет — []. Окну —
        чтобы узнавать их в сообщениях агента (чипы-источники)."""
        if not self.configured:
            return []
        out = []
        for path in self._kb_files(self.root, limit):
            try:
                out.append(path.relative_to(self.root).as_posix())
            except ValueError:
                continue
        return out

    def _kb_files(self, folder: Path, limit: int) -> list[Path]:
        """Документы папки базы рекурсивно, без исключённых, скрытых и ссылок наружу."""
        out = []
        dirs: dict[Path, bool] = {}
        for path in kb_index.walk_kb(folder, suffixes=materials.DOC_SUFFIXES):
            rel = path.relative_to(self.root).as_posix()
            if kb_index.is_excluded(rel, self.prefixes):
                continue
            # Куда ведёт папка — один раз на папку; файл — только если он сам ссылка.
            parent = path.parent
            if parent not in dirs:
                dirs[parent] = self._allowed(parent)
            if not dirs[parent] or (os.path.islink(path) and not self._allowed(path)):
                continue
            out.append(path)
            if len(out) >= limit:
                break
        return out

    # --- read

    def kb_read(self, paths) -> list[dict]:
        """Прочитать файлы → [{"path", "text", "chars", "truncated",
        "warnings"} или {"path", "error"}]. Текст — с местами в квадратных
        скобках («[слайд 7]»), не длиннее READ_MAX_CHARS на файл и
        READ_TOTAL_CHARS всего."""
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, (list, tuple)):
            return [{"path": str(paths), "error": BAD_PATH}]
        out: list[dict] = []
        left = READ_TOTAL_CHARS
        for i, path in enumerate(dict.fromkeys(p if isinstance(p, str) else str(p) for p in paths)):
            if i >= READ_MAX_FILES:
                out.append({"path": path, "error": TOO_MANY_FILES.format(READ_MAX_FILES)})
                continue
            try:
                place = self.place(path)
                if place.kind == "meetings" or place.path.is_dir():
                    raise AccessError(NOT_A_FILE.format(place.rel))
                parsed = self._parse(place)
            except AccessError as e:
                out.append({"path": path, "error": str(e)})
                continue
            if left <= 0:
                out.append({"path": place.rel, "error": READ_SPENT})
                continue
            text = "\n\n".join(f"[{c['loc']}]\n{c['text']}" if c["loc"] else c["text"] for c in parsed.chunks)
            limit = max(0, min(READ_MAX_CHARS, left))
            truncated = len(text) > limit
            if truncated:
                text = text[:limit].rstrip() + "\n" + CUT.format(limit)
            left -= min(len(text), limit)
            out.append({"path": place.rel, "text": text, "chars": len(text), "truncated": truncated,
                        "warnings": list(parsed.warnings)})
        return out

    # --- search

    def kb_search(self, query: str, in_path: str | None = None, *,
                  deadline_s: float | None = None) -> dict:
        """Найти слова запроса (по основам, без служебных) в файлах `in_path`
        (файл, папка базы, `meet:<встреча>` или `meet:` — все встречи; пусто —
        вся база) → {"query", "in", "hits": [{"path", "loc", "snippet",
        "matched"}], "files", "more"} или {"error"}. Сначала выдержки со всеми
        словами, потом — с частью; по порядку файлов. Весь поиск — не дольше
        `deadline_s` (SEARCH_DEADLINE_S): дальше файлы не смотрятся, в ответе
        `"timed_out": True` и найденное к этому времени. Отдельный файл
        ограничен своим бюджетом разбора; звать — не из цикла событий."""
        deadline = time.monotonic() + (SEARCH_DEADLINE_S if deadline_s is None else deadline_s)
        words = list(dict.fromkeys(materials.terms((query or "")[:QUERY_MAX_CHARS])))
        if not words:
            return {"error": NO_QUERY}
        try:
            place = self.place(in_path)
        except AccessError as e:
            return {"error": str(e)}
        targets = self._search_targets(place)
        hits: list[tuple[int, int, dict]] = []
        parsed_count = 0
        timed_out = False
        for order, target in enumerate(targets[:SEARCH_MAX_FILES]):
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if target.path.suffix.lower() not in (".md", ".txt", ".csv") and target.path.name != TRANSCRIPT:
                parsed_count += 1
                if parsed_count > SEARCH_MAX_PARSED:
                    continue
            try:
                parsed = self._parse(target)
            except AccessError:
                continue
            for c in parsed.chunks:
                found = _find(c["text"], words)
                if found:
                    hits.append((-len(found), order * 10_000 + c["n"],
                                 {"path": target.rel, "loc": c["loc"], "matched": len(found),
                                  "snippet": _snippet(c["text"], found)}))
        hits.sort(key=lambda h: (h[0], h[1]))
        out, total = [], 0
        for _neg, _order, hit in hits:
            if len(out) >= SEARCH_MAX_HITS or total + len(hit["snippet"]) > SEARCH_TOTAL_CHARS:
                break
            out.append(hit)
            total += len(hit["snippet"])
        result = {"query": query, "in": place.rel or ROOT_LABEL, "words": len(words), "hits": out,
                  "files": min(len(targets), SEARCH_MAX_FILES), "more": len(hits) - len(out)}
        if timed_out:
            result["timed_out"] = True
        return result

    def _search_targets(self, place: Place) -> list[Place]:
        if place.kind == "meetings":
            out = []
            for folder in reversed(library.recording_folders(self.library_root)):
                for name in self._meeting_files(folder):
                    out.append(Place("meet", folder / name, f"{MEET_PREFIX}{folder.name}/{name}"))
            return out
        if place.kind == "meet":
            if place.path.is_dir():
                return [Place("meet", place.path / n, f"{place.rel}/{n}") for n in self._meeting_files(place.path)]
            return [place]
        if place.path.is_file():
            return [place]
        return [Place("kb", p, p.relative_to(self.root).as_posix())
                for p in self._kb_files(place.path, SEARCH_MAX_FILES)]

    # --- list

    def kb_list(self, folder: str | None = None) -> dict:
        """Содержимое папки базы (`folders`: [{"name", "docs"}], `files`:
        [имена]) или встречи (`files`) или список встреч (`meetings`:
        [{"id", "path", "date", "title", "group"}], новые первыми);
        не больше LIST_MAX строк, остальное — `more`. Ошибка — {"error"}."""
        try:
            place = self.place(folder)
        except AccessError as e:
            return {"error": str(e)}
        if place.kind == "meetings":
            folders = list(reversed(library.recording_folders(self.library_root)))
            try:
                names = {g["id"]: g["name"] for g in groups.load(self.library_root)}
            except groups.Busy:
                names = {}
            items = []
            for f in folders[:LIST_MAX]:
                title, date = library.title_and_date(f, None)
                gid = groups.of(library.read_meta(f))
                items.append({"id": f.name, "path": MEET_PREFIX + f.name, "date": date, "title": title,
                              "group": names.get(gid) if gid else None})
            return {"path": MEET_PREFIX, "meetings": items, "more": max(0, len(folders) - LIST_MAX)}
        if place.kind == "meet":
            if not place.path.is_dir():
                return {"error": NOT_A_FOLDER.format(place.rel)}
            title, date = library.title_and_date(place.path, None)
            return {"path": place.rel, "title": title, "date": date, "files": self._meeting_files(place.path)}
        if not place.path.is_dir():
            return {"error": NOT_A_FOLDER.format(place.rel)}
        subfolders, files = [], []
        try:
            entries = sorted(place.path.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            entries = []
        for entry in entries:
            rel = f"{place.rel}/{entry.name}" if place.rel else entry.name
            if self._hidden([entry.name]) or kb_index.is_excluded(rel, self.prefixes) or not self._allowed(entry):
                continue
            if entry.is_dir():
                subfolders.append({"name": entry.name, "docs": len(self._kb_files(entry, MAP_MAX_FILES))})
            elif entry.suffix.lower() in materials.DOC_SUFFIXES:
                files.append(entry.name)
        rows = len(subfolders) + len(files)
        keep_dirs = subfolders[:LIST_MAX]
        keep_files = files[:max(0, LIST_MAX - len(keep_dirs))]
        return {"path": place.rel or ROOT_LABEL, "folders": keep_dirs, "files": keep_files,
                "more": rows - len(keep_dirs) - len(keep_files)}

    # --- карта

    def kb_map(self, *, group=None, current=None, budget_chars: int = MAP_BUDGET_CHARS) -> str:
        """Карта для промпта агента: папки и названия документов базы (без
        содержимого) и прошлые встречи группы — не длиннее `budget_chars`.
        Пустая строка — карты нет (выключена, нет ни базы, ни встреч группы).
        Выключена (`show_map`, `assist.kb_map`) — модели не уходит ни
        структура базы, ни список прошлых встреч группы."""
        if not self.show_map:
            return ""
        info = self._group(group)
        key = (info["id"] if info else None, groups.kb_folder(info) if info else None,
               Path(current).name if current else None, budget_chars)
        if key not in self._maps:
            self._maps[key] = self._build_map(info, current, budget_chars)
        return self._maps[key]

    def _group(self, group) -> dict | None:
        if isinstance(group, dict):
            return group
        if not group or self.library_root is None:
            return None
        try:
            return next((g for g in groups.load(self.library_root) if g["id"] == group), None)
        except groups.Busy:
            return None

    def group_folder(self, group) -> str | None:
        """Папка базы знаний группы, если задана, есть, внутри базы и не в исключённых."""
        rel = groups.kb_folder(self._group(group))
        if rel is None or not self.configured:
            return None
        rel = kb_index.ondisk(self.root, rel)  # «проекты/альфа» → «Проекты/Альфа»
        if not rel:
            return None
        try:
            place = self.place(rel)
        except AccessError:
            return None
        return rel if place.path.is_dir() else None

    def _meetings(self, info: dict | None, current) -> list[dict]:
        if not info or self.library_root is None or not self.library_root.is_dir():
            return []
        skip = Path(current).name if current else None
        out = []
        for f in reversed(library.recording_folders(self.library_root)):
            if f.name == skip or groups.of(library.read_meta(f)) != info["id"]:
                continue
            title, date = library.title_and_date(f, None)
            out.append({"path": MEET_PREFIX + f.name, "date": date, "title": title})
            if len(out) >= MAP_MEETINGS:
                break
        return out

    def _build_map(self, info: dict | None, current, budget: int) -> str:
        lines: list[str] = []
        used = 0

        def fits(line: str, reserve: int = 0) -> bool:
            return used + len(line) + 1 + reserve <= budget

        def put(line: str, reserve: int = 0) -> bool:
            nonlocal used
            if not fits(line, reserve):
                return False
            lines.append(line)
            used += len(line) + 1
            return True

        meetings = self._meetings(info, current)
        meeting_lines = []
        if meetings:
            meeting_lines = [f"Прошлые встречи группы «{info['name']}» (путь · дата · название):"]
            meeting_lines += [f"- {m['path']} · {m['date'] or '—'} · {m['title']}" for m in meetings]
        meeting_cost = min(sum(len(x) + 1 for x in meeting_lines), budget // 4)

        docs: list[tuple[str, float]] = []
        if self.configured:
            for path in self._kb_files(self.root, MAP_MAX_FILES):
                try:
                    docs.append((path.relative_to(self.root).as_posix(), path.stat().st_mtime))
                except OSError:
                    continue
        if docs:
            put("База знаний — папки и названия документов (без содержимого; путь = папка/название):")
            gfolder = self.group_folder(info) if info else None
            if gfolder is not None:
                inner = sorted(rel for rel, _m in docs if rel.startswith(gfolder + "/"))
                cap = min(int(budget * GROUP_SHARE), budget - meeting_cost)
                if put(f"[папка группы] {gfolder}/ — {len(inner)} {_docs_word(len(inner))}:"):
                    for i, rel in enumerate(inner):
                        line = f"  - {rel[len(gfolder) + 1:]}"
                        left = len(inner) - i - 1
                        tail = len(f"  … ещё {left} {_docs_word(left)}") + 1 if left else 0
                        if used + len(line) + 1 + tail > cap or not put(line):
                            rest = len(inner) - i
                            put(f"  … ещё {rest} {_docs_word(rest)}")
                            break
            folders: dict[str, list[tuple[str, float]]] = {}
            for rel, mtime in docs:
                if gfolder is not None and rel.startswith(gfolder + "/"):
                    continue
                folders.setdefault(rel.rsplit("/", 1)[0] if "/" in rel else "", []).append((rel, mtime))
            tail_reserve = len("… ещё 99999 папок (999999 документов)") + 1 + meeting_cost
            skipped = skipped_docs = 0
            for name in sorted(folders, key=lambda f: f.casefold()):
                items = sorted(folders[name], key=lambda x: (-x[1], x[0]))  # свежие первыми
                head = f"{name + '/' if name else ROOT_LABEL} — {len(items)} {_docs_word(len(items))}"
                for n in range(min(MAP_TOP_TITLES, len(items)), -1, -1):
                    titles = [rel.rsplit("/", 1)[-1] for rel, _m in items[:n]]
                    line = head + (": " + " · ".join(titles) if titles else "")
                    if titles and len(items) > n:
                        more = len(items) - n
                        line += f" · … ещё {more} {_docs_word(more)}"
                    if put(line, reserve=tail_reserve):
                        break
                else:
                    skipped += 1
                    skipped_docs += len(items)
            if skipped:
                word = "папка" if skipped % 10 == 1 and skipped % 100 != 11 else "папок"
                put(f"… ещё {skipped} {word} ({skipped_docs} {_docs_word(skipped_docs)})")
        if meeting_lines and fits(meeting_lines[0], reserve=len(meeting_lines[1]) + 1):
            for line in meeting_lines:
                if not put(line):
                    break
        return "\n".join(lines)


def _stems(text: str) -> set[str]:
    return set(kb_index._WORD.findall(kb_index._norm(text)))


def _matches(word: str, vocab: set[str]) -> bool:
    """Слово запроса есть среди слов текста — с учётом окончаний: одно
    начинается с основы другого (как в `kb_index`)."""
    if len(word) <= 3:
        return word in vocab
    stem = kb_index._stem(word)
    return any(w.startswith(stem) or (len(w) > 3 and word.startswith(kb_index._stem(w))) for w in vocab
               if w[:3] == word[:3])


def _find(text: str, words: list[str]) -> list[str]:
    vocab = _stems(text)
    return [w for w in words if _matches(w, vocab)]


def _snippet(text: str, found: list[str]) -> str:
    """Выдержка не длиннее SNIPPET_CHARS вокруг первого найденного слова."""
    flat = " ".join(text.split())
    if len(flat) <= SNIPPET_CHARS:
        return flat
    low = kb_index._norm(flat)
    starts = [low.find(kb_index._stem(w)) for w in found]
    starts = [s for s in starts if s >= 0]
    at = max(0, min(starts) - SNIPPET_CHARS // 4) if starts else 0
    if at:
        space = flat.find(" ", at)
        at = space + 1 if 0 <= space < at + 30 else at
    end = min(len(flat), at + SNIPPET_CHARS - 2)
    return ("…" if at else "") + flat[at:end].strip() + ("…" if end < len(flat) else "")


def kb_exclude_paths(kb_root, exclude=()) -> list[str]:
    """Абсолютные пути исключённых папок (и файлов) базы, которые есть на
    диске, — так, как они записаны на диске, после раскрытия ссылок; если
    исключённое — ссылка, то и сам путь ссылки. Сравнение — как у
    исполнителей (`is_excluded`): без регистра и «ё», так что «учеба» даёт и
    «Учеба», и «Учёба», если есть обе. Без синтаксиса шаблонов: имена с «[»,
    «]», «*» — как есть. Вызывающий отдаёт их провайдеру в `deny_paths`
    (правила своего CLI собирает слой провайдеров). База не задана — пусто.

    Список — на сессию: считается при её старте. Папку, созданную позже,
    исполнители Meet всё равно не покажут (они сверяют путь при каждой
    просьбе), а правила CLI её не закроют до следующей сессии."""
    if not kb_root:
        return []
    root = _resolved(Path(kb_root))
    out: list[str] = []
    for item in exclude or ():
        parts = kb_index.exclude_parts(item)
        for rel in kb_index.ondisk_all(root, "/".join(parts)) if parts else ():
            link = root.joinpath(*rel.split("/"))
            for path in (_resolved(link), link):
                text = str(path)
                if text not in out:
                    out.append(text)
    return out


def for_settings(cfg, library_root) -> KnowledgeBase:
    """База знаний на сессию по настройкам: `assistant.knowledge_dir`,
    `assist.kb_exclude`, `assist.kb_map`."""
    return KnowledgeBase(cfg.assistant.knowledge_dir, exclude=cfg.assist.kb_exclude,
                         library_root=library_root, show_map=cfg.assist.kb_map)


def kb_map(kb: KnowledgeBase, *, group=None, current=None, budget_chars: int = MAP_BUDGET_CHARS) -> str:
    return kb.kb_map(group=group, current=current, budget_chars=budget_chars)


def kb_read(kb: KnowledgeBase, paths) -> list[dict]:
    return kb.kb_read(paths)


def kb_search(kb: KnowledgeBase, query: str, in_path: str | None = None, *,
              deadline_s: float | None = None) -> dict:
    return kb.kb_search(query, in_path, deadline_s=deadline_s)


def kb_list(kb: KnowledgeBase, folder: str | None = None) -> dict:
    return kb.kb_list(folder)

