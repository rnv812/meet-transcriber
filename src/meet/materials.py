"""Материалы ассистента: документы встречи и заметки базы знаний как текст с
местами для ссылок (дизайн V4, §5.2).

* **Разбор — локально, без тяжёлых зависимостей.** `.md`/`.txt` — как есть
  (UTF-8, иначе cp1251), место — заголовок; `.docx`/`.pptx`/`.xlsx` — stdlib
  `zipfile` + `xml.etree` (абзацы и заголовки, слайды с заметками докладчика,
  листы со строками); `.csv` — первые CSV_MAX_ROWS строк; `.pdf` — `pypdf`
  (ленивый импорт: нет пакета или нет текстового слоя — предупреждение, не
  ошибка). OCR не делаем.
* **Фрагменты** по CHUNK_MIN–CHUNK_MAX символов по границам абзацев (строк у
  таблиц), каждый со своим местом: «слайд 7», «лист Бюджет, строки 2–40»,
  «стр. 3», заголовок раздела.
* **Исходный файл не копируется.** Разобранный текст — в
  `<папка записи>/assistant/materials/<id>.json` (`{v, id, meta, summary,
  chunks}`), источник — путь, sha256, размер и время правки. Файл изменили
  или он исчез — `status()` говорит «изменён» / «нет файла», текст остаётся.
* **Пределы** — §5.5: документ ≤ 20 МБ и ≤ 200 000 символов (дальше обрезка с
  пометкой), материалов в сессии ≤ 30 (папка базы знаний группы — ещё ≤ 30)
  и ≤ 2 млн символов всего.
* Поиск по базе знаний и встречам по просьбе агента — `meet.assist.kb_prep`
  (буквальный поиск по словам в тексте, который разбирает этот модуль).
"""

import codecs
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from meet import library
from meet.assist import kb_index

DOC_SUFFIXES = (".md", ".txt", ".docx", ".pptx", ".xlsx", ".csv", ".pdf")
ASSISTANT_DIR = "assistant"
MATERIALS_DIR = "materials"
FILES_DIR = "files"
FORMAT = 1
# Последний выданный номер вложения (`a<N>`): id не переиспользуются.
IDS_FILE = ".ids"

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
CSV_MAX_ROWS = 2_000
SHEET_MAX_ROWS = 2_000
CHUNK_MIN = 800
CHUNK_MAX = 1_200
MAX_FOLDER_FILES = 200
MAX_FOLDER_CHARS = 1_000_000
# Защита от бомб (архив в 200 КБ, распаковывающийся в гигабайты, миллионы
# пустых элементов): распакованная часть OOXML — не больше MAX_PART_BYTES,
# весь архив — не больше MAX_ARCHIVE_BYTES (считается то, что распаковалось
# на деле, а не объявлено); служебные части деревом — не больше
# MAX_DOM_BYTES, содержимое — только потоком. На файл — TIME_BUDGET_S
# секунд. Упёрлись — разобранное остаётся, с предупреждением.
MAX_PART_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_DOM_BYTES = 8 * 1024 * 1024
TIME_BUDGET_S = 10.0
# Строк листа просматривается (и пустых тоже), общих строк xlsx — не больше.
SHEET_SCAN_ROWS = 100_000
MAX_SHARED_STRINGS = 1_000_000
# Поле CSV длиннее — разбор останавливается (по умолчанию в csv — 128 КБ).
CSV_FIELD_MAX = 1_000_000
PDF_MAX_PAGES = 2_000
# Сессия: вручную и из базы знаний — MAX_MATERIALS, папка базы знаний группы —
# ещё MAX_GROUP_MATERIALS сверх; всего текста — MAX_SESSION_CHARS.
MAX_MATERIALS = 30
MAX_GROUP_MATERIALS = 30
MAX_SESSION_CHARS = 2_000_000
# Откуда материал: вручную (файл, папка), заметка базы знаний, итоги прошлой
# встречи группы, заметка из папки базы знаний группы.
ORIGINS = ("file", "kb", "past_meeting", "kb_folder")
GROUP_ORIGINS = ("kb_folder",)

STATUS_LABELS = {"ok": "", "changed": "изменён", "missing": "нет файла"}

UNSUPPORTED = "Такой файл не поддерживается: нужен {}"
TOO_BIG = "Файл больше 20 МБ — его не разобрать"
NOT_FOUND = "Файла нет: {}"
BROKEN = "Файл повреждён или это не {}"
NO_PYPDF = "Нет модуля pypdf — PDF не разобран, модель видит только название"
NO_PDF_TEXT = "В PDF нет текста (скан) — модель видит только название"
PDF_LOCKED = "PDF защищён паролем — модель видит только название"
TRUNCATED = "Текст длиннее {} символов — дальше обрезан"
ROWS_CUT = "{}: взяты первые {} строк"
CSV_BROKEN = "CSV разобран до строки {}: {}"
PDF_PAGES_CUT = "В PDF больше {} страниц — взяты первые"
PART_TOO_BIG = "Часть документа слишком большая после распаковки — разобрано начало"
ARCHIVE_TOO_BIG = "Документ слишком большой после распаковки — разобрано начало"
TOO_SLOW = "Разбор занял слишком долго — разобрано начало"
EXCLUDED_KB = "Эта папка базы знаний закрыта для ассистента (исключения): {}"
FOLDER_CUT = "В папке больше {} файлов — взяты первые"
TOO_MANY = "В сессии уже {} материалов — уберите ненужные"
TOO_MANY_GROUP = "Из папки группы уже подключено {} заметок"
TOO_MUCH_TEXT = "Материалов в сессии больше 2 млн символов — уберите ненужные"
OUTSIDE_KB = "Заметка вне базы знаний: {}"


class MaterialError(ValueError):
    """Материал не добавить (текст — человеку)."""


@dataclass
class Parsed:
    """Разобранный документ: `chunks` — [{n, loc, text}], `warnings` — что
    сказать человеку (обрезка, скан без текста, нет pypdf)."""

    title: str
    kind: str
    chunks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sha256: str = ""
    size: int = 0
    mtime_ns: int | None = None

    @property
    def chars(self) -> int:
        return sum(len(c["text"]) for c in self.chunks)

    @property
    def text(self) -> str:
        return "\n\n".join(c["text"] for c in self.chunks)


# Раздел документа: место (строка или функция первой и последней строки
# таблицы → строка) и куски текста [(текст, номер строки или None)].
class _Section:
    __slots__ = ("loc", "units")

    def __init__(self, loc, units=None) -> None:
        self.loc = loc
        self.units: list[tuple[str, int | None]] = units if units is not None else []


def _rows_loc(prefix: str = ""):
    return lambda a, b: f"{prefix}строка {a}" if a == b else f"{prefix}строки {a}–{b}"


# --- фрагменты -----------------------------------------------------------------------


def _split_long(text: str, limit: int | None = None) -> list[str]:
    """Кусок длиннее предела (CHUNK_MAX) — режем по переводу строки, концу
    предложения или пробелу во второй половине окна, иначе — жёстко."""
    limit = CHUNK_MAX if limit is None else limit
    out: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = -1
        for sep in ("\n", ". ", "; ", " "):
            at = window.rfind(sep)
            if at >= limit // 2:
                cut = at + len(sep)
                break
        if cut <= 0:
            cut = limit
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        out.append(text)
    return [t for t in out if t]


def _chunks(sections: list[_Section], limit: int | None = None) -> tuple[list[dict], bool]:
    """Фрагменты по CHUNK_MIN–CHUNK_MAX символов, не через границу раздела;
    всего — не больше `limit` (MAX_TEXT_CHARS) символов (→ обрезано ли)."""
    limit = MAX_TEXT_CHARS if limit is None else limit
    chunks: list[dict] = []
    total = 0
    truncated = False

    def flush(loc, parts, rows):
        nonlocal total, truncated
        if not parts or truncated:
            return
        text = "\n".join(parts)
        if callable(loc):
            nums = [r for r in rows if r is not None]
            loc = loc(nums[0], nums[-1]) if nums else ""
        if total + len(text) > limit:
            text = text[:max(0, limit - total)].rstrip()
            truncated = True
            if not text:
                return
        total += len(text)
        chunks.append({"n": len(chunks) + 1, "loc": loc or "", "text": text})

    for section in sections:
        parts: list[str] = []
        rows: list[int | None] = []
        size = 0
        for raw, row in section.units:
            for piece in _split_long(raw.strip()):
                if parts and size + 1 + len(piece) > CHUNK_MAX:
                    flush(section.loc, parts, rows)
                    parts, rows, size = [], [], 0
                parts.append(piece)
                rows.append(row)
                size += len(piece) + (1 if size else 0)
            if truncated:
                break
        flush(section.loc, parts, rows)
        if truncated:
            break
    return chunks, truncated


def _finish(title: str, kind: str, sections: list[_Section], warnings: list[str],
            limit: int | None = None) -> Parsed:
    limit = MAX_TEXT_CHARS if limit is None else limit
    chunks, truncated = _chunks(sections, limit)
    if truncated:
        warnings.append(TRUNCATED.format(f"{limit:,}".replace(",", " ")))
    return Parsed(title=title, kind=kind, chunks=chunks, warnings=warnings)


# --- бюджет разбора одного файла ---------------------------------------------------


class _Limit(Exception):
    """Разбор упёрся в предел: дальше не читаем, разобранное остаётся,
    человеку — предупреждение (текст исключения)."""


class _Budget:
    """Пределы одного файла: время (TIME_BUDGET_S), распакованные байты
    архива (MAX_ARCHIVE_BYTES), набранный текст (MAX_TEXT_CHARS — дальше
    читать незачем)."""

    def __init__(self) -> None:
        self.deadline = time.monotonic() + TIME_BUDGET_S
        self.unpacked = 0
        self.chars = 0
        self._ticks = 0

    def tick(self, every: int = 1) -> None:
        self._ticks += 1
        if self._ticks % every == 0 and time.monotonic() > self.deadline:
            raise _Limit(TOO_SLOW)

    def unpack(self, n: int) -> None:
        self.unpacked += n
        if self.unpacked > MAX_ARCHIVE_BYTES:
            raise _Limit(ARCHIVE_TOO_BIG)

    def text(self, n: int) -> None:
        self.chars += n

    @property
    def full(self) -> bool:
        return self.chars >= MAX_TEXT_CHARS


class _Capped(io.RawIOBase):
    """Поток части архива со счётчиком: больше `cap` байт этой части или
    MAX_ARCHIVE_BYTES всего архива, или вышло время — `_Limit`. Объявленному
    в архиве размеру не верим: считаем то, что распаковалось на деле."""

    def __init__(self, raw, budget: _Budget, cap: int) -> None:
        super().__init__()
        self.raw = raw
        self.budget = budget
        self.cap = cap
        self.size = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        self.budget.tick()
        n = self.raw.readinto(buffer)
        if n:
            self.size += n
            if self.size > self.cap:
                raise _Limit(PART_TOO_BIG)
            self.budget.unpack(n)
        return n or 0

    def close(self) -> None:
        try:
            self.raw.close()
        finally:
            super().close()


# --- текст: md, txt, csv -----------------------------------------------------------


def decode(data: bytes) -> str:
    """UTF-16 с BOM (Блокнот, «Юникод»), UTF-8 (с BOM или без), иначе
    cp1251 — так пишут старые файлы Windows. Нулевые символы убираются."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1251", errors="replace")
    return text.replace("\x00", "")


_FENCE = re.compile(r"^\s*(```|~~~)")


def _paragraphs(lines: list[str]) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line.strip():
            buf.append(line.rstrip())
        elif buf:
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


def _md_sections(text: str, headings: bool = True) -> list[_Section]:
    """Разделы по заголовкам `#`–`####` (внутри блоков кода — не заголовки);
    место — текст заголовка, у текста до первого заголовка — пусто."""
    lines = kb_index._strip_frontmatter(text.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
    sections: list[_Section] = []
    loc, body = "", []
    fence = False

    def close():
        units = [(p, None) for p in _paragraphs(body)]
        if units:
            sections.append(_Section(loc, units))

    for line in lines:
        if _FENCE.match(line):
            fence = not fence
        m = None if fence or not headings else kb_index._HEADING.match(line)
        if m:
            close()
            loc, body = kb_index._clean(m.group(1)), [line.strip()]
            continue
        body.append(line)
    close()
    return sections


def parse_text(text: str, title: str, kind: str = "md") -> Parsed:
    """Текст заметки (`md`) или простой текст (`txt`) → фрагменты."""
    return _finish(title, kind, _md_sections(text, headings=kind == "md"), [])


def _csv(data: bytes, title: str) -> Parsed:
    """Первые CSV_MAX_ROWS строк; поле длиннее CSV_FIELD_MAX или битая
    строка — разбор останавливается с предупреждением, прочитанное остаётся."""
    text = decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    section = _Section(_rows_loc())
    warnings: list[str] = []
    with _CSV_LOCK:
        # Предел длины поля — общий для процесса: ставим свой и возвращаем.
        saved = csv.field_size_limit(CSV_FIELD_MAX)
        try:
            reader = csv.reader(io.StringIO(text), dialect)
            i = 0
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error as e:
                    warnings.append(CSV_BROKEN.format(i + 1, e))
                    break
                i += 1
                if i > CSV_MAX_ROWS:
                    warnings.append(ROWS_CUT.format(title, CSV_MAX_ROWS))
                    break
                cells = [c.strip() for c in row if c and c.strip()]
                if cells:
                    section.units.append((" | ".join(cells), i))
        finally:
            csv.field_size_limit(saved)
    return _finish(title, "csv", [section], warnings)


_CSV_LOCK = threading.Lock()


# --- OOXML: docx, pptx, xlsx ------------------------------------------------------


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ns(tag) -> str:
    return tag[1:].split("}", 1)[0] if isinstance(tag, str) and tag.startswith("{") else ""


def _attr(el, name: str, default=None):
    """Атрибут по локальному имени: строгий OOXML и переходный пишут разные
    пространства имён, разбор их не различает."""
    for key, value in el.attrib.items():
        if key == name or key.endswith("}" + name):
            return value
    return default


def _child(el, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _children(el, name: str):
    return [c for c in el if _local(c.tag) == name]


def _open(z: zipfile.ZipFile, name: str, budget: _Budget, cap: int | None = None) -> _Capped | None:
    """Поток части архива под счётчиком или None, если части нет."""
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    return _Capped(z.open(info), budget, MAX_PART_BYTES if cap is None else cap)


def _xml(z: zipfile.ZipFile, name: str, budget: _Budget):
    """Небольшая служебная часть (связи, стили, список слайдов и листов)
    деревом — не больше MAX_DOM_BYTES; битая — None."""
    f = _open(z, name, budget, MAX_DOM_BYTES)
    if f is None:
        return None
    with f:
        data = f.read()
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def _stream(f: _Capped, budget: _Budget, want):
    """Элементы части потоком: `want(elem)` — нужен ли элемент; отдаётся
    самый внешний нужный по закрытии, и после обработки он, как и всё
    прочее закрытое, отцепляется от родителя — в памяти только открытые
    предки и текущий элемент. Битый XML — конец разбора части."""
    stack: list = []
    inside = 0
    try:
        with f:
            for event, el in ET.iterparse(f, events=("start", "end")):
                if event == "start":
                    stack.append(el)
                    if want(el):
                        inside += 1
                    continue
                stack.pop()
                budget.tick(4096)
                if inside and want(el):
                    inside -= 1
                    if inside == 0:
                        yield el
                if inside == 0 and stack:
                    stack[-1].remove(el)
    except ET.ParseError:
        return


def _rels(z: zipfile.ZipFile, part: str, budget: _Budget) -> dict[str, tuple[str, str]]:
    """Связи части: Id → (тип, путь части внутри архива)."""
    base, name = posixpath.split(part)
    root = _xml(z, posixpath.join(base, "_rels", name + ".rels"), budget)
    out: dict[str, tuple[str, str]] = {}
    if root is None:
        return out
    for rel in root:
        if _local(rel.tag) != "Relationship" or _attr(rel, "TargetMode") == "External":
            continue
        target = _attr(rel, "Target") or ""
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
        out[_attr(rel, "Id") or ""] = (_attr(rel, "Type") or "", path)
    return out


def _main_part(z: zipfile.ZipFile, fallback: str, budget: _Budget) -> str:
    for kind, path in _rels(z, "", budget).values():
        if kind.endswith("/officeDocument"):
            return path
    return fallback


def _by_type(rels: dict, suffix: str) -> str | None:
    return next((path for kind, path in rels.values() if kind.endswith(suffix)), None)


_HEADING_NAME = re.compile(r"^(?:heading|заголовок)\s*(\d)", re.I)


def _outline_level(ppr) -> int | None:
    """Уровень структуры из `w:outlineLvl`: 0–8 — заголовок 1–9; 9 — «основной
    текст», не заголовок."""
    outline = _child(ppr, "outlineLvl") if ppr is not None else None
    value = _attr(outline, "val") if outline is not None else None
    if value is not None and value.isdigit() and int(value) < 9:
        return int(value) + 1
    return None


def _heading_styles(z: zipfile.ZipFile, document: str, budget: _Budget) -> dict[str, int]:
    """styleId → уровень заголовка: по имени стиля («heading 1», «Заголовок 2»,
    «Title») или по уровню структуры. Русский Word даёт стилям id вроде «1»,
    поэтому смотрим имя, а не id."""
    styles = _by_type(_rels(z, document, budget), "/styles") or "word/styles.xml"
    f = _open(z, styles, budget)
    out: dict[str, int] = {}
    if f is None:
        return out
    for style in _stream(f, budget, lambda el: _local(el.tag) == "style"):
        if _attr(style, "type") not in (None, "paragraph"):
            continue
        sid = _attr(style, "styleId")
        name_el = _child(style, "name")
        name = (_attr(name_el, "val") or "") if name_el is not None else ""
        level = _outline_level(_child(style, "pPr"))
        if level is None and (m := _HEADING_NAME.match(name.strip())):
            level = int(m.group(1))
        elif level is None and name.strip().lower() in ("title", "заголовок", "название"):
            level = 1
        if sid and level is not None and level <= 9:
            out[sid] = level
    return out


def _w_text(el) -> str:
    parts: list[str] = []
    for node in el.iter():
        tag = _local(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _add(section: _Section, text: str, row: int | None, budget: _Budget) -> None:
    section.units.append((text, row))
    budget.text(len(text))


def _docx(z: zipfile.ZipFile, title: str, sections: list[_Section], warnings: list[str],
          budget: _Budget) -> None:
    document = _main_part(z, "word/document.xml", budget)
    f = _open(z, document, budget)
    if f is None:
        raise MaterialError(BROKEN.format("документ Word"))
    styles = _heading_styles(z, document, budget)
    sections.append(_Section(""))

    def heading_level(p) -> int | None:
        ppr = _child(p, "pPr")
        if ppr is None:
            return None
        style = _child(ppr, "pStyle")
        sid = _attr(style, "val") if style is not None else None
        if sid in styles:
            return styles[sid]
        level = _outline_level(ppr)
        if level is not None:
            return level
        if sid and (m := _HEADING_NAME.match(sid)):
            return int(m.group(1))
        return None

    # Абзацы и таблицы тела потоком, в порядке документа; обёртки (sdt,
    # customXml) — не помеха: нужен самый внешний абзац или таблица.
    for node in _stream(f, budget, lambda el: _local(el.tag) in ("p", "tbl") and "wordprocessingml" in _ns(el.tag)):
        if _local(node.tag) == "p":
            text = _w_text(node)
            if not text:
                continue
            if heading_level(node) is not None:
                sections.append(_Section(" ".join(text.split())))
            _add(sections[-1], text, None, budget)
        else:
            for tr in node.iter():
                if _local(tr.tag) != "tr":
                    continue
                cells = [" ".join(_w_text(p) for p in tc.iter() if _local(p.tag) == "p").strip()
                         for tc in _children(tr, "tc")]
                cells = [c for c in cells if c]
                if cells:
                    _add(sections[-1], " | ".join(cells), None, budget)
        if budget.full:
            break


def _paragraph_text(p) -> str:
    parts: list[str] = []
    for run in p.iter():
        tag = _local(run.tag)
        if tag == "t" and run.text:
            parts.append(run.text)
        elif tag == "br":
            parts.append("\n")
    return "".join(parts).strip()


def _is_drawing_p(el) -> bool:
    return _local(el.tag) == "p" and "drawingml" in _ns(el.tag)


# Служебные заполнители страницы заметок: миниатюра слайда, номер, колонтитулы.
_NOTES_SKIP = {"sldImg", "sldNum", "hdr", "ftr", "dt"}


def _notes_text(f: _Capped, budget: _Budget) -> list[str]:
    out: list[str] = []
    for shape in _stream(f, budget, lambda el: _local(el.tag) == "sp"):
        ph = next((n for n in shape.iter() if _local(n.tag) == "ph"), None)
        if ph is not None and _attr(ph, "type") in _NOTES_SKIP:
            continue
        out.extend(t for p in shape.iter() if _is_drawing_p(p) and (t := _paragraph_text(p)))
    return out


def _pptx(z: zipfile.ZipFile, title: str, sections: list[_Section], warnings: list[str],
          budget: _Budget) -> None:
    presentation = _main_part(z, "ppt/presentation.xml", budget)
    root = _xml(z, presentation, budget)
    if root is None:
        raise MaterialError(BROKEN.format("презентация PowerPoint"))
    rels = _rels(z, presentation, budget)
    slides: list[str] = []
    order = _child(root, "sldIdLst")
    for sld in order if order is not None else ():
        # r:id — атрибут в пространстве связей; у p:sldId есть ещё числовой id.
        rid = next((v for k, v in sld.attrib.items() if k.endswith("}id")), None)
        if rid in rels:
            slides.append(rels[rid][1])
    if not slides:  # нет списка — по номерам файлов
        names = [n for n in z.namelist() if re.match(r"^ppt/slides/slide\d+\.xml$", n)]
        slides = sorted(names, key=lambda n: int(re.findall(r"\d+", n)[-1]))
    for i, part in enumerate(slides, start=1):
        if budget.full:
            break
        f = _open(z, part, budget)
        if f is None:
            continue
        section = _Section(f"слайд {i}")
        sections.append(section)
        for p in _stream(f, budget, _is_drawing_p):
            text = _paragraph_text(p)
            if text:
                _add(section, text, None, budget)
        notes_part = _by_type(_rels(z, part, budget), "/notesSlide")
        notes = _open(z, notes_part, budget) if notes_part else None
        if notes is not None:
            text = "\n".join(_notes_text(notes, budget))
            if text:
                _add(section, f"Заметки докладчика: {text}", None, budget)


def _number(value: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.10g}"


def _si_text(si) -> str:
    """Текст общей строки без фонетических подсказок (`rPh`) — обходом без
    рекурсии: глубокая вложенность не роняет разбор."""
    skip: set[int] = set()
    for node in si.iter():
        if _local(node.tag) == "rPh":
            skip.update(id(x) for x in node.iter())
    return "".join(node.text for node in si.iter()
                   if _local(node.tag) == "t" and node.text and id(node) not in skip)


def _shared_strings(z: zipfile.ZipFile, part: str, budget: _Budget) -> list[str]:
    f = _open(z, part, budget)
    out: list[str] = []
    if f is None:
        return out
    for si in _stream(f, budget, lambda el: _local(el.tag) == "si"):
        out.append(_si_text(si))
        if len(out) >= MAX_SHARED_STRINGS:
            break
    return out


def _sheet_rows(f: _Capped, shared: list[str], budget: _Budget, name: str,
                warnings: list[str]) -> list[tuple[int, str]]:
    """Непустые строки листа [(номер, текст)] потоком. Каждая `<row>`, и
    пустая тоже, считается к SHEET_SCAN_ROWS; непустых — не больше
    SHEET_MAX_ROWS."""
    rows: list[tuple[int, str]] = []
    seen = 0
    for el in _stream(f, budget, lambda e: _local(e.tag) == "row"):
        seen += 1
        if seen > SHEET_SCAN_ROWS:
            warnings.append(ROWS_CUT.format(f"лист {name}", SHEET_SCAN_ROWS))
            break
        try:
            number = int(_attr(el, "r") or seen)
        except ValueError:
            number = seen
        cells: list[str] = []
        for c in _children(el, "c"):
            kind = _attr(c, "t")
            v = _child(c, "v")
            raw = v.text if v is not None and v.text is not None else ""
            if kind == "s":
                text = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
            elif kind == "inlineStr":
                inline = _child(c, "is")
                text = _si_text(inline) if inline is not None else ""
            elif kind == "b":
                text = "TRUE" if raw == "1" else "FALSE" if raw == "0" else raw
            elif kind in ("str", "e"):
                text = raw
            else:
                text = _number(raw) if raw else ""
            text = " ".join(text.split())
            if text:
                cells.append(text)
        if not cells:
            continue
        if len(rows) >= SHEET_MAX_ROWS:
            warnings.append(ROWS_CUT.format(f"лист {name}", SHEET_MAX_ROWS))
            break
        line = " | ".join(cells)
        rows.append((number, line))
        budget.text(len(line))
        if budget.full:
            break
    return rows


def _xlsx(z: zipfile.ZipFile, title: str, sections: list[_Section], warnings: list[str],
          budget: _Budget) -> None:
    workbook = _main_part(z, "xl/workbook.xml", budget)
    root = _xml(z, workbook, budget)
    if root is None:
        raise MaterialError(BROKEN.format("таблица Excel"))
    rels = _rels(z, workbook, budget)
    shared = _shared_strings(z, _by_type(rels, "/sharedStrings") or "xl/sharedStrings.xml", budget)
    sheets_el = _child(root, "sheets")
    for sheet in sheets_el if sheets_el is not None else ():
        if budget.full:
            break
        name = _attr(sheet, "name") or "?"
        rid = next((v for k, v in sheet.attrib.items() if k.endswith("}id")), None)
        if rid not in rels:
            continue
        f = _open(z, rels[rid][1], budget)
        if f is None:
            continue
        section = _Section(_rows_loc(f"лист {name}, "))
        sections.append(section)
        section.units.extend((t, n) for n, t in _sheet_rows(f, shared, budget, name, warnings))


_OOXML = {".docx": (_docx, "документ Word"), ".pptx": (_pptx, "презентация PowerPoint"),
          ".xlsx": (_xlsx, "таблица Excel")}


def _ooxml(data: bytes, title: str, suffix: str, budget: _Budget) -> Parsed:
    """Архив OOXML: упёрлись в предел (байты, время) — разобранное до него
    остаётся, с предупреждением."""
    parse, what = _OOXML[suffix]
    sections: list[_Section] = []
    warnings: list[str] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError) as e:
        raise MaterialError(BROKEN.format(what)) from e
    with z:
        try:
            parse(z, title, sections, warnings, budget)
        except _Limit as e:
            warnings.append(str(e))
    return _finish(title, suffix[1:], sections, warnings)


# --- PDF --------------------------------------------------------------------------


def _pdf(data: bytes, title: str, budget: _Budget) -> Parsed:
    try:
        import pypdf  # ленивый импорт: модуль нужен только для PDF
    except ImportError:
        return Parsed(title=title, kind="pdf", warnings=[NO_PYPDF])
    import logging

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # шум о кривых PDF — не в журнал процесса
    sections: list[_Section] = []
    warnings: list[str] = []
    reader = pypdf.PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                return Parsed(title=title, kind="pdf", warnings=[PDF_LOCKED])
        except Exception:
            return Parsed(title=title, kind="pdf", warnings=[PDF_LOCKED])
    try:
        for i, page in enumerate(reader.pages, start=1):
            if i > PDF_MAX_PAGES:
                warnings.append(PDF_PAGES_CUT.format(PDF_MAX_PAGES))
                break
            budget.tick()
            try:
                text = page.extract_text() or ""
            except _Limit:
                raise
            except Exception:
                text = ""
            units = [(p, None) for p in re.split(r"\n\s*\n", text) if p.strip()]
            if units:
                sections.append(_Section(f"стр. {i}", units))
                budget.text(len(text))
            if budget.full:
                break
    except _Limit as e:
        warnings.append(str(e))
    if not sections and not warnings:
        warnings.append(NO_PDF_TEXT)
    return _finish(title, "pdf", sections, warnings)


# --- разбор ------------------------------------------------------------------------


_WHAT = {".md": "текст", ".txt": "текст", ".csv": "таблица CSV", ".pdf": "PDF",
         **{k: v[1] for k, v in _OOXML.items()}}


def parse_bytes(data: bytes, name: str) -> Parsed:
    """Содержимое файла с именем `name` → Parsed (с хэшем и размером).
    Любой сбой разборщика (битый архив, глубокая вложенность, нехватка
    памяти) — MaterialError: один плохой файл не роняет вызывающего."""
    suffix = Path(name).suffix.lower()
    if suffix not in DOC_SUFFIXES:
        raise MaterialError(UNSUPPORTED.format(", ".join(DOC_SUFFIXES)))
    if len(data) > MAX_INPUT_BYTES:
        raise MaterialError(TOO_BIG)
    title = Path(name).name
    budget = _Budget()
    try:
        if suffix in (".md", ".txt"):
            parsed = parse_text(decode(data), title, suffix[1:])
        elif suffix == ".csv":
            parsed = _csv(data, title)
        elif suffix == ".pdf":
            parsed = _pdf(data, title, budget)
        else:
            parsed = _ooxml(data, title, suffix, budget)
    except MaterialError:
        raise
    except Exception as e:  # RecursionError, MemoryError, csv.Error, zlib.error, RuntimeError…
        raise MaterialError(BROKEN.format(_WHAT[suffix])) from e
    parsed.sha256 = hashlib.sha256(data).hexdigest()
    parsed.size = len(data)
    return parsed


def _read_file(path: Path) -> tuple[bytes, os.stat_result]:
    """Байты файла не больше MAX_INPUT_BYTES (+1, чтобы заметить больший) и
    его размер и время правки — с того же открытого файла, до чтения."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            if st.st_size > MAX_INPUT_BYTES:
                raise MaterialError(TOO_BIG)
            data = f.read(MAX_INPUT_BYTES + 1)
    except FileNotFoundError:
        raise MaterialError(NOT_FOUND.format(path.name)) from None
    except IsADirectoryError:
        raise MaterialError(NOT_FOUND.format(path.name)) from None
    except PermissionError as e:
        raise MaterialError(f"Файл не прочитать: {path.name} ({e.strerror or e})") from None
    except OSError as e:
        raise MaterialError(f"Файл не прочитать: {path.name} ({e.strerror or e})") from None
    if len(data) > MAX_INPUT_BYTES:
        raise MaterialError(TOO_BIG)
    return data, st


def parse(path) -> Parsed:
    """Файл → Parsed{title, kind, chunks[{n, loc, text}], warnings}; размер и
    время правки — того, что прочитано и захэшировано."""
    path = Path(path)
    if path.suffix.lower() not in DOC_SUFFIXES:
        raise MaterialError(UNSUPPORTED.format(", ".join(DOC_SUFFIXES)))
    data, st = _read_file(path)
    parsed = parse_bytes(data, path.name)
    parsed.mtime_ns = st.st_mtime_ns
    return parsed


def folder_files(root, *, limit: int | None = None, suffixes=DOC_SUFFIXES, exclude=(),
                 base=None) -> tuple[list[Path], bool]:
    """Поддерживаемые файлы папки рекурсивно, по порядку имён, без служебных
    и скрытых папок (как у `kb_index`) → (файлы, есть ли ещё сверх `limit`).
    `exclude` — пути относительно `base` (корня базы знаний; по умолчанию —
    самой папки); исключённое не берётся, в том числе через ссылки."""
    root = Path(root)
    limit = MAX_FOLDER_FILES if limit is None else limit
    out: list[Path] = []
    prefixes = kb_index.exclude_prefixes(exclude)
    base_real = _real(base) if base is not None else None
    for path in kb_index.walk_kb(root, suffixes=suffixes, exclude=() if base is not None else exclude):
        if base_real is not None and _excluded_in(path, base_real, prefixes):
            continue
        if len(out) >= limit:
            return out, True
        out.append(path)
    return out, False


def _real(path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path).absolute()


def _excluded_in(path: Path, kb_real: Path, prefixes) -> bool:
    """Путь (после ссылок) — в исключённой папке базы знаний `kb_real`."""
    try:
        rel = _real(path).relative_to(kb_real).as_posix()
    except ValueError:
        return False  # вне базы — материал вручную, исключения базы не про него
    return kb_index.is_excluded("" if rel == "." else rel, prefixes)


def folder_fingerprint(files: list[Path], root: Path) -> str:
    """Отпечаток папки: относительные пути, размеры и время правки файлов."""
    h = hashlib.sha256()
    for path in files:
        try:
            st = path.stat()
        except OSError:
            continue
        h.update(f"{path.relative_to(root).as_posix()}\t{st.st_size}\t{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def parse_folder(root, *, exclude=(), base=None) -> Parsed:
    """Папка целиком — один материал: до MAX_FOLDER_FILES файлов, место
    фрагмента — «файл, место в файле». `exclude`/`base` — как у folder_files."""
    root = Path(root)
    if not root.is_dir():
        raise MaterialError(NOT_FOUND.format(root.name))
    files, more = folder_files(root, exclude=exclude, base=base)
    chunks: list[dict] = []
    warnings: list[str] = [FOLDER_CUT.format(MAX_FOLDER_FILES)] if more else []
    total = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            got = parse(path)
        except MaterialError as e:
            warnings.append(f"{rel}: {e}")
            continue
        warnings.extend(f"{rel}: {w}" for w in got.warnings)
        for c in got.chunks:
            if total + len(c["text"]) > MAX_FOLDER_CHARS:
                warnings.append(TRUNCATED.format(f"{MAX_FOLDER_CHARS:,}".replace(",", " ")))
                return Parsed(root.name, "folder", chunks, warnings, folder_fingerprint(files, root), 0)
            total += len(c["text"])
            loc = f"{rel}, {c['loc']}" if c["loc"] else rel
            chunks.append({"n": len(chunks) + 1, "loc": loc, "text": c["text"]})
    return Parsed(root.name, "folder", chunks, warnings, folder_fingerprint(files, root), 0)


def outline(parsed: Parsed, limit: int = 600) -> str:
    """Сводка без вызова модели: первый абзац и заголовки (места) — так
    описываются заметки базы знаний (§5.2)."""
    if not parsed.chunks:
        return ""
    first = next((p for c in parsed.chunks for p in c["text"].split("\n")
                  if p.strip() and not kb_index._HEADING.match(p)), "")
    first = kb_index._clean(first)
    locs = list(dict.fromkeys(c["loc"] for c in parsed.chunks if c["loc"]))
    text = first
    if locs:
        text = f"{first}\nРазделы: {'; '.join(locs)}" if first else f"Разделы: {'; '.join(locs)}"
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# --- хранение в папке записи ----------------------------------------------------------


def assistant_dir(folder) -> Path:
    return Path(folder) / ASSISTANT_DIR


def materials_dir(folder) -> Path:
    return assistant_dir(folder) / MATERIALS_DIR


_ID = re.compile(r"^a(\d{1,9})$")


def _taken_ids(folder) -> set[int]:
    taken: set[int] = set()
    for sub in (MATERIALS_DIR, FILES_DIR):
        try:
            names = [p.stem for p in (assistant_dir(folder) / sub).iterdir()]
        except OSError:
            continue
        for stem in names:
            m = _ID.match(stem)
            if m:
                taken.add(int(m.group(1)))
    return taken


def claim_id(folder, ext: str) -> tuple[str, Path]:
    """Новый id вложения `a<N>` — общий для материалов и изображений — и
    занятый под него пустой файл (`materials/<id>.json` или `files/<id>.<ext>`).
    Под замком папки ассистента: два процесса (ребёнок и задача резидента)
    не получат один id."""
    sub = MATERIALS_DIR if ext == ".json" else FILES_DIR
    target_dir = assistant_dir(folder) / sub
    target_dir.mkdir(parents=True, exist_ok=True)
    counter = assistant_dir(folder) / IDS_FILE
    with library.file_lock(counter):
        try:
            last = int(counter.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            last = 0
        # Счётчик только растёт: id убранного вложения не выдаётся снова, и
        # старые ссылки журнала не укажут на новое.
        n = max(last, max(_taken_ids(folder), default=0)) + 1
        while True:
            path = target_dir / f"a{n}{ext}"
            try:
                with open(path, "x", encoding="utf-8"):
                    pass
                break
            except FileExistsError:
                n += 1
        try:
            counter.write_text(str(n), encoding="utf-8")
        except OSError:
            pass  # без счётчика — по наибольшему занятому, как раньше
        return f"a{n}", path


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        library._replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read(folder, aid: str) -> dict | None:
    """Запись материала (`{v, id, meta, summary, chunks}`) или None."""
    if not isinstance(aid, str) or not _ID.match(aid):
        return None
    try:
        data = json.loads((materials_dir(folder) / f"{aid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("meta"), dict):
        return None
    if not isinstance(data.get("chunks"), list):
        data["chunks"] = []
    return data


def records(folder) -> list[dict]:
    """Все материалы записи по порядку id (битые и недописанные пропускаются)."""
    try:
        stems = [p.stem for p in materials_dir(folder).glob("a*.json")]
    except OSError:
        return []
    out = []
    for stem in sorted((s for s in stems if _ID.match(s)), key=lambda s: int(s[1:])):
        data = read(folder, stem)
        if data is not None:
            out.append(data)
    return out


def _file_sha(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def status(record: dict) -> str:
    """"ok" | "changed" (изменён) | "missing" (нет файла). Размер и время
    правки те же — файл не перечитывается; иначе сравнивается хэш (папка —
    отпечаток списка файлов)."""
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    source = meta.get("source") if isinstance(meta.get("source"), dict) else {}
    raw = source.get("path")
    if not raw:
        return "ok"
    path = Path(raw)
    if meta.get("kind") == "folder":
        if not path.is_dir():
            return "missing"
        files, _ = folder_files(path, exclude=meta.get("exclude") or (), base=meta.get("kb_root"))
        return "ok" if folder_fingerprint(files, path) == source.get("sha256") else "changed"
    try:
        st = path.stat()
    except OSError:
        return "missing"
    if not path.is_file():
        return "missing"
    if st.st_size == source.get("size") and st.st_mtime_ns == source.get("mtime_ns"):
        return "ok"
    return "ok" if _file_sha(path) == source.get("sha256") else "changed"


def attachment(record: dict, *, check: bool = True) -> dict:
    """Описание материала для журнала и окна (без текста фрагментов)."""
    meta = record.get("meta") or {}
    state = status(record) if check else "ok"
    return {"id": record.get("id"), "type": "mat", "title": meta.get("title", ""),
            "kind": meta.get("kind", ""), "origin": meta.get("origin", "file"),
            "kb_ref": meta.get("kb_ref"), "path": (meta.get("source") or {}).get("path"),
            "warnings": list(meta.get("warnings") or []), "chars": meta.get("chars", 0),
            "chunks": len(record.get("chunks") or []), "summary": record.get("summary", ""),
            "status": state, "status_label": STATUS_LABELS[state]}


def _same_path(a, b) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except (OSError, ValueError):
        return False


def _kb_path(path, kb_root) -> tuple[Path, str | None]:
    """Путь материала и его место в базе знаний (`Папка/Заметка.md`) или None.
    Относительный путь — от корня базы; выйти за неё нельзя."""
    path = Path(path)
    if kb_root is None:
        return path, None
    root = Path(kb_root)
    full = path if path.is_absolute() else root / path
    try:
        rel = full.resolve().relative_to(root.resolve())
    except ValueError:
        if not path.is_absolute():
            raise MaterialError(OUTSIDE_KB.format(path)) from None
        return path, None
    except OSError:
        return full, None
    return full, rel.as_posix()


def add(folder, path, *, origin: str = "file", kb_root=None, exclude=None, summary: str | None = None,
        kind: str | None = None) -> dict:
    """Разобрать файл или папку и положить в материалы записи → описание
    (`attachment`). Тот же файл с тем же содержимым уже есть — его описание с
    `"duplicate": True`. `kb_root` — корень базы знаний: путь может быть
    относительным от него, в описании — `kb_ref`; тогда обязателен и
    `exclude` (`assist.kb_exclude`): исключённая заметка — отказ, из папки
    исключённое не берётся. `summary` — готовая сводка; у заметок базы знаний
    без неё — первый абзац и заголовки. `kind` — вид для окна (`kb_note`,
    `past_meeting`); по умолчанию — вид файла."""
    if origin not in ORIGINS:
        raise MaterialError(f"неизвестный источник материала: {origin}")
    if kb_root is not None and exclude is None:
        raise TypeError("materials.add: с kb_root нужен exclude (assist.kb_exclude)")
    exclude = tuple(exclude or ())
    full, kb_ref = _kb_path(path, kb_root)
    if kb_root is not None and _excluded_in(full, _real(kb_root), kb_index.exclude_prefixes(exclude)):
        raise MaterialError(EXCLUDED_KB.format(kb_ref or Path(path).name))
    existing = records(folder)
    is_dir = full.is_dir()
    for record in existing:
        source = (record.get("meta") or {}).get("source") or {}
        if source.get("path") and _same_path(source["path"], full) and status(record) == "ok":
            return {**attachment(record, check=False), "duplicate": True}
    group = origin in GROUP_ORIGINS
    count = sum(1 for r in existing if ((r.get("meta") or {}).get("origin") in GROUP_ORIGINS) == group)
    if count >= (MAX_GROUP_MATERIALS if group else MAX_MATERIALS):
        raise MaterialError((TOO_MANY_GROUP if group else TOO_MANY).format(count))
    parsed = parse_folder(full, exclude=exclude, base=kb_root) if is_dir else parse(full)
    used = sum(int((r.get("meta") or {}).get("chars") or 0) for r in existing)
    if used + parsed.chars > MAX_SESSION_CHARS:
        raise MaterialError(TOO_MUCH_TEXT)
    if summary is None:
        summary = outline(parsed) if origin in ("kb", "kb_folder", "past_meeting") else ""
    if is_dir:
        size, mtime_ns = 0, None
    else:  # то, что прочитано и захэшировано, — не stat после разбора
        size, mtime_ns = parsed.size, parsed.mtime_ns
    aid, target = claim_id(folder, ".json")
    meta = {"title": parsed.title, "kind": parsed.kind, "origin": origin, "added_at": time.time(),
            "source": {"path": str(full), "sha256": parsed.sha256, "size": size, "mtime_ns": mtime_ns},
            "warnings": parsed.warnings, "chars": parsed.chars}
    if kind:
        meta["view"] = kind
    if kb_ref:
        meta["kb_ref"] = kb_ref
    if is_dir and kb_root is not None:
        meta.update(kb_root=str(kb_root), exclude=list(exclude))
    record = {"v": FORMAT, "id": aid, "meta": meta, "summary": summary or "", "chunks": parsed.chunks}
    try:
        _write_json(target, record)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return attachment(record, check=False)


# --- слова для поиска --------------------------------------------------------------

# Служебные слова не ищутся: «что», «это», «давайте» есть в любой реплике.
STOPWORDS = frozenset("""
и в во не что он на я с со как а то все всё она так его но да ты к у же вы за бы по только ее её
мне было вот от меня еще ещё нет о об из ему теперь когда даже ну ли если уже или ни быть был
него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней
для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того
потому этого какой совсем ним здесь этом один почти мой тем чтобы нее неё сейчас были куда
зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про
всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том
нельзя такой им более всегда конечно всю между это вообще давайте короче типа просто значит
так ага угу окей ладно понятно который которые которая которое также либо пока очень надо
нужно нужен будем будут есть этих этим эта эти
the a an and or of to in is are for on with that this it be as at by from was were not but
""".split())


def terms(text: str) -> list[str]:
    """Значимые слова текста (нормализованные, без служебных)."""
    return [w for w in kb_index._WORD.findall(kb_index._norm(text))
            if w.strip("-_") and w not in STOPWORDS and (len(w) > 1 or w.isdigit())]
