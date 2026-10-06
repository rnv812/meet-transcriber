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

import csv
import hashlib
import io
import json
import os
import posixpath
import re
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

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
CSV_MAX_ROWS = 2_000
SHEET_MAX_ROWS = 2_000
CHUNK_MIN = 800
CHUNK_MAX = 1_200
MAX_FOLDER_FILES = 200
MAX_FOLDER_CHARS = 1_000_000
# Распакованная часть OOXML больше этого — не читаем (zip-бомба).
MAX_PART_BYTES = 64 * 1024 * 1024
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


# --- текст: md, txt, csv -----------------------------------------------------------


def decode(data: bytes) -> str:
    """UTF-8 (с BOM или без), иначе cp1251 — так пишут старые файлы Windows."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


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
    text = decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    section = _Section(_rows_loc())
    warnings: list[str] = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect), start=1):
        if i > CSV_MAX_ROWS:
            warnings.append(ROWS_CUT.format(title, CSV_MAX_ROWS))
            break
        cells = [c.strip() for c in row if c and c.strip()]
        if cells:
            section.units.append((" | ".join(cells), i))
    return _finish(title, "csv", [section], warnings)


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


def _part(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise MaterialError(TOO_BIG)
    with z.open(info) as f:
        data = f.read(MAX_PART_BYTES + 1)
    if len(data) > MAX_PART_BYTES:
        raise MaterialError(TOO_BIG)
    return data


def _xml(z: zipfile.ZipFile, name: str):
    data = _part(z, name)
    if data is None:
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Связи части: Id → (тип, путь части внутри архива)."""
    base, name = posixpath.split(part)
    root = _xml(z, posixpath.join(base, "_rels", name + ".rels"))
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


def _main_part(z: zipfile.ZipFile, fallback: str) -> str:
    for kind, path in _rels(z, "").values():
        if kind.endswith("/officeDocument"):
            return path
    return fallback


def _by_type(rels: dict, suffix: str) -> str | None:
    return next((path for kind, path in rels.values() if kind.endswith(suffix)), None)


_HEADING_NAME = re.compile(r"^(?:heading|заголовок)\s*(\d)", re.I)


def _heading_styles(z: zipfile.ZipFile, document: str) -> dict[str, int]:
    """styleId → уровень заголовка: по имени стиля («heading 1», «Заголовок 2»,
    «Title») или по уровню структуры. Русский Word даёт стилям id вроде «1»,
    поэтому смотрим имя, а не id."""
    styles = _by_type(_rels(z, document), "/styles") or "word/styles.xml"
    root = _xml(z, styles)
    out: dict[str, int] = {}
    if root is None:
        return out
    for style in root:
        if _local(style.tag) != "style" or _attr(style, "type") not in (None, "paragraph"):
            continue
        sid = _attr(style, "styleId")
        name_el = _child(style, "name")
        name = (_attr(name_el, "val") or "") if name_el is not None else ""
        ppr = _child(style, "pPr")
        outline = _child(ppr, "outlineLvl") if ppr is not None else None
        level = None
        if outline is not None and (_attr(outline, "val") or "").isdigit():
            level = int(_attr(outline, "val")) + 1
        elif (m := _HEADING_NAME.match(name.strip())):
            level = int(m.group(1))
        elif name.strip().lower() in ("title", "заголовок", "название"):
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


def _docx(z: zipfile.ZipFile, title: str) -> Parsed:
    document = _main_part(z, "word/document.xml")
    root = _xml(z, document)
    if root is None:
        raise MaterialError(BROKEN.format("документ Word"))
    styles = _heading_styles(z, document)
    body = _child(root, "body")
    sections = [_Section("")]

    def heading_level(p) -> int | None:
        ppr = _child(p, "pPr")
        if ppr is None:
            return None
        style = _child(ppr, "pStyle")
        sid = _attr(style, "val") if style is not None else None
        if sid in styles:
            return styles[sid]
        outline = _child(ppr, "outlineLvl")
        if outline is not None and (_attr(outline, "val") or "").isdigit():
            return int(_attr(outline, "val")) + 1
        if sid and (m := _HEADING_NAME.match(sid)):
            return int(m.group(1))
        return None

    def walk(el):
        for node in el:
            tag = _local(node.tag)
            if tag == "p":
                text = _w_text(node)
                if not text:
                    continue
                if heading_level(node) is not None:
                    sections.append(_Section(" ".join(text.split())))
                sections[-1].units.append((text, None))
            elif tag == "tbl":
                for tr in node.iter():
                    if _local(tr.tag) != "tr":
                        continue
                    cells = [" ".join(_w_text(p) for p in tc.iter() if _local(p.tag) == "p").strip()
                             for tc in _children(tr, "tc")]
                    cells = [c for c in cells if c]
                    if cells:
                        sections[-1].units.append((" | ".join(cells), None))
            elif tag not in ("sectPr",):
                walk(node)  # sdt, customXml и прочие обёртки абзацев

    if body is not None:
        walk(body)
    return _finish(title, "docx", sections, [])


def _drawing_paragraphs(el) -> list[str]:
    """Абзацы DrawingML (`a:p`) по порядку: текст прогонов, `a:br` — перевод строки."""
    out: list[str] = []
    for node in el.iter():
        if _local(node.tag) != "p" or "drawingml" not in _ns(node.tag):
            continue
        parts: list[str] = []
        for run in node.iter():
            tag = _local(run.tag)
            if tag == "t" and run.text:
                parts.append(run.text)
            elif tag == "br":
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            out.append(text)
    return out


# Служебные заполнители страницы заметок: миниатюра слайда, номер, колонтитулы.
_NOTES_SKIP = {"sldImg", "sldNum", "hdr", "ftr", "dt"}


def _notes_text(root) -> list[str]:
    out: list[str] = []
    for shape in root.iter():
        if _local(shape.tag) != "sp":
            continue
        ph = next((n for n in shape.iter() if _local(n.tag) == "ph"), None)
        if ph is not None and _attr(ph, "type") in _NOTES_SKIP:
            continue
        out.extend(_drawing_paragraphs(shape))
    return out


def _pptx(z: zipfile.ZipFile, title: str) -> Parsed:
    presentation = _main_part(z, "ppt/presentation.xml")
    root = _xml(z, presentation)
    if root is None:
        raise MaterialError(BROKEN.format("презентация PowerPoint"))
    rels = _rels(z, presentation)
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
    sections: list[_Section] = []
    for i, part in enumerate(slides, start=1):
        slide = _xml(z, part)
        if slide is None:
            continue
        section = _Section(f"слайд {i}", [(t, None) for t in _drawing_paragraphs(slide)])
        notes_part = _by_type(_rels(z, part), "/notesSlide")
        notes = _xml(z, notes_part) if notes_part else None
        if notes is not None:
            text = "\n".join(_notes_text(notes))
            if text:
                section.units.append((f"Заметки докладчика: {text}", None))
        sections.append(section)
    return _finish(title, "pptx", sections, [])


def _number(value: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.10g}"


def _si_text(si) -> str:
    """Текст общей строки без фонетических подсказок (`rPh`)."""
    parts: list[str] = []

    def walk(el):
        for node in el:
            tag = _local(node.tag)
            if tag == "rPh":
                continue
            if tag == "t" and node.text:
                parts.append(node.text)
            walk(node)

    walk(si)
    return "".join(parts)


def _sheet_rows(z: zipfile.ZipFile, part: str, shared: list[str], limit: int):
    """Непустые строки листа [(номер, текст)] потоком: большой лист не
    разворачивается в память целиком. → (строки, обрезан ли)."""
    try:
        info = z.getinfo(part)
    except KeyError:
        return [], False
    rows: list[tuple[int, str]] = []
    count = 0
    with z.open(info) as f:
        try:
            for _event, el in ET.iterparse(f, events=("end",)):
                if _local(el.tag) != "row":
                    continue
                count += 1
                try:
                    number = int(_attr(el, "r") or count)
                except ValueError:
                    number = count
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
                el.clear()
                if cells:
                    if len(rows) >= limit:
                        return rows, True
                    rows.append((number, " | ".join(cells)))
        except ET.ParseError:
            pass
    return rows, False


def _xlsx(z: zipfile.ZipFile, title: str) -> Parsed:
    workbook = _main_part(z, "xl/workbook.xml")
    root = _xml(z, workbook)
    if root is None:
        raise MaterialError(BROKEN.format("таблица Excel"))
    rels = _rels(z, workbook)
    shared: list[str] = []
    strings = _xml(z, _by_type(rels, "/sharedStrings") or "xl/sharedStrings.xml")
    if strings is not None:
        shared = [_si_text(si) for si in strings if _local(si.tag) == "si"]
    sheets_el = _child(root, "sheets")
    sections: list[_Section] = []
    warnings: list[str] = []
    for sheet in sheets_el if sheets_el is not None else ():
        name = _attr(sheet, "name") or "?"
        rid = next((v for k, v in sheet.attrib.items() if k.endswith("}id")), None)
        if rid not in rels:
            continue
        rows, cut = _sheet_rows(z, rels[rid][1], shared, SHEET_MAX_ROWS)
        if cut:
            warnings.append(ROWS_CUT.format(f"лист {name}", SHEET_MAX_ROWS))
        sections.append(_Section(_rows_loc(f"лист {name}, "), [(t, n) for n, t in rows]))
    return _finish(title, "xlsx", sections, warnings)


_OOXML = {".docx": (_docx, "документ Word"), ".pptx": (_pptx, "презентация PowerPoint"),
          ".xlsx": (_xlsx, "таблица Excel")}


def _ooxml(data: bytes, title: str, suffix: str) -> Parsed:
    parse, what = _OOXML[suffix]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return parse(z, title)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, KeyError, NotImplementedError) as e:
        raise MaterialError(BROKEN.format(what)) from e


# --- PDF --------------------------------------------------------------------------


def _pdf(data: bytes, title: str) -> Parsed:
    try:
        import pypdf  # ленивый импорт: модуль нужен только для PDF
    except ImportError:
        return Parsed(title=title, kind="pdf", warnings=[NO_PYPDF])
    import logging

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # шум о кривых PDF — не в журнал процесса
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    return Parsed(title=title, kind="pdf", warnings=[PDF_LOCKED])
            except Exception:
                return Parsed(title=title, kind="pdf", warnings=[PDF_LOCKED])
        sections: list[_Section] = []
        total = 0
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            units = [(p, None) for p in re.split(r"\n\s*\n", text) if p.strip()]
            if units:
                sections.append(_Section(f"стр. {i}", units))
                total += len(text)
            if total > MAX_TEXT_CHARS:
                break
    except Exception as e:
        raise MaterialError(BROKEN.format("PDF")) from e
    warnings = [] if sections else [NO_PDF_TEXT]
    return _finish(title, "pdf", sections, warnings)


# --- разбор ------------------------------------------------------------------------


def parse_bytes(data: bytes, name: str) -> Parsed:
    """Содержимое файла с именем `name` → Parsed (с хэшем и размером)."""
    suffix = Path(name).suffix.lower()
    if suffix not in DOC_SUFFIXES:
        raise MaterialError(UNSUPPORTED.format(", ".join(DOC_SUFFIXES)))
    if len(data) > MAX_INPUT_BYTES:
        raise MaterialError(TOO_BIG)
    title = Path(name).name
    if suffix in (".md", ".txt"):
        parsed = parse_text(decode(data), title, suffix[1:])
    elif suffix == ".csv":
        parsed = _csv(data, title)
    elif suffix == ".pdf":
        parsed = _pdf(data, title)
    else:
        parsed = _ooxml(data, title, suffix)
    parsed.sha256 = hashlib.sha256(data).hexdigest()
    parsed.size = len(data)
    return parsed


def parse(path) -> Parsed:
    """Файл → Parsed{title, kind, chunks[{n, loc, text}], warnings}."""
    path = Path(path)
    if path.suffix.lower() not in DOC_SUFFIXES:
        raise MaterialError(UNSUPPORTED.format(", ".join(DOC_SUFFIXES)))
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise MaterialError(TOO_BIG)
        data = path.read_bytes()
    except FileNotFoundError:
        raise MaterialError(NOT_FOUND.format(path.name)) from None
    except OSError as e:
        raise MaterialError(f"Файл не прочитать: {path.name} ({e.strerror or e})") from None
    return parse_bytes(data, path.name)


def folder_files(root, *, limit: int | None = None, suffixes=DOC_SUFFIXES,
                 exclude=()) -> tuple[list[Path], bool]:
    """Поддерживаемые файлы папки рекурсивно, по порядку имён, без служебных
    и скрытых папок (как у `kb_index`) → (файлы, есть ли ещё сверх `limit`)."""
    root = Path(root)
    limit = MAX_FOLDER_FILES if limit is None else limit
    out: list[Path] = []
    for path in kb_index.walk_kb(root, suffixes=suffixes, exclude=exclude):
        if len(out) >= limit:
            return out, True
        out.append(path)
    return out, False


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


def parse_folder(root) -> Parsed:
    """Папка целиком — один материал: до MAX_FOLDER_FILES файлов, место
    фрагмента — «файл, место в файле»."""
    root = Path(root)
    if not root.is_dir():
        raise MaterialError(NOT_FOUND.format(root.name))
    files, more = folder_files(root)
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
    with library.file_lock(assistant_dir(folder) / ".ids"):
        n = max(_taken_ids(folder), default=0) + 1
        while True:
            path = target_dir / f"a{n}{ext}"
            try:
                with open(path, "x", encoding="utf-8"):
                    pass
                return f"a{n}", path
            except FileExistsError:
                n += 1


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
        files, _ = folder_files(path)
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


def add(folder, path, *, origin: str = "file", kb_root=None, summary: str | None = None,
        kind: str | None = None) -> dict:
    """Разобрать файл или папку и положить в материалы записи → описание
    (`attachment`). Тот же файл с тем же содержимым уже есть — его описание с
    `"duplicate": True`. `kb_root` — корень базы знаний: путь может быть
    относительным от него, в описании — `kb_ref`. `summary` — готовая сводка;
    у заметок базы знаний без неё — первый абзац и заголовки. `kind` —
    вид для окна (`kb_note`, `past_meeting`); по умолчанию — вид файла."""
    if origin not in ORIGINS:
        raise MaterialError(f"неизвестный источник материала: {origin}")
    full, kb_ref = _kb_path(path, kb_root)
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
    parsed = parse_folder(full) if is_dir else parse(full)
    used = sum(int((r.get("meta") or {}).get("chars") or 0) for r in existing)
    if used + parsed.chars > MAX_SESSION_CHARS:
        raise MaterialError(TOO_MUCH_TEXT)
    if summary is None:
        summary = outline(parsed) if origin in ("kb", "kb_folder", "past_meeting") else ""
    try:
        st = full.stat()
        size, mtime_ns = (parsed.size if not is_dir else 0), st.st_mtime_ns
    except OSError:
        size, mtime_ns = parsed.size, None
    aid, target = claim_id(folder, ".json")
    meta = {"title": parsed.title, "kind": parsed.kind, "origin": origin, "added_at": time.time(),
            "source": {"path": str(full), "sha256": parsed.sha256, "size": size, "mtime_ns": mtime_ns},
            "warnings": parsed.warnings, "chars": parsed.chars}
    if kind:
        meta["view"] = kind
    if kb_ref:
        meta["kb_ref"] = kb_ref
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
