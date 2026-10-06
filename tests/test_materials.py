"""Материалы ассистента (`meet.materials`): разбор документов с местами,
фрагменты, пределы, хранение в папке записи, «изменён» / «нет файла». Файлы OOXML собираются здесь же через zipfile; данные выдуманы."""

import importlib.util
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from meet import materials

FIXTURES = Path(__file__).parent / "fixtures"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _rels(*items) -> str:
    body = "".join(f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{target}"/>'
                   for rid, kind, target in items)
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG}">{body}</Relationships>'


def _zip(path: Path, parts: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        for name, text in parts.items():
            z.writestr(name, text)
    return path


# --- docx ----------------------------------------------------------------------------


def _p(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _docx(path: Path, body: str) -> Path:
    # Русский Word: id стиля заголовка — «1», имя — «heading 1».
    styles = (f'<w:styles xmlns:w="{W}">'
              f'<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/></w:style>'
              f'<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="Normal"/></w:style>'
              f"</w:styles>")
    return _zip(path, {
        "_rels/.rels": _rels(("rId1", "officeDocument", "word/document.xml")),
        "word/_rels/document.xml.rels": _rels(("rId1", "styles", "styles.xml")),
        "word/styles.xml": styles,
        "word/document.xml": f'<w:document xmlns:w="{W}"><w:body>{body}<w:sectPr/></w:body></w:document>',
    })


def test_docx_paragraphs_headings_and_tables(tmp_path):
    body = (_p("Вступление без раздела.")
            + _p("Сроки", "1") + _p("Запуск назначен на 15 ноября.", "a3")
            + "<w:tbl><w:tr><w:tc>" + _p("Этап") + "</w:tc><w:tc>" + _p("Дата") + "</w:tc></w:tr>"
            + "<w:tr><w:tc>" + _p("Пилот") + "</w:tc><w:tc>" + _p("01.11") + "</w:tc></w:tr></w:tbl>"
            + _p("Бюджет", "Heading2") + _p("Триста тысяч."))
    parsed = materials.parse(_docx(tmp_path / "План.docx", body))
    assert parsed.kind == "docx" and parsed.title == "План.docx" and parsed.warnings == []
    by_loc = {c["loc"]: c["text"] for c in parsed.chunks}
    assert by_loc[""] == "Вступление без раздела."
    assert "Запуск назначен на 15 ноября." in by_loc["Сроки"]
    assert "Этап | Дата" in by_loc["Сроки"] and "Пилот | 01.11" in by_loc["Сроки"]
    assert by_loc["Бюджет"].endswith("Триста тысяч.")
    assert [c["n"] for c in parsed.chunks] == list(range(1, len(parsed.chunks) + 1))
    assert len(parsed.sha256) == 64 and parsed.size == (tmp_path / "План.docx").stat().st_size


def test_broken_office_file_is_an_error_not_a_crash(tmp_path):
    bad = tmp_path / "битый.docx"
    bad.write_bytes(b"not a zip at all")
    with pytest.raises(materials.MaterialError):
        materials.parse(bad)
    empty = _zip(tmp_path / "пустой.xlsx", {})
    with pytest.raises(materials.MaterialError):
        materials.parse(empty)


# --- pptx ----------------------------------------------------------------------------


def _slide(*paragraphs: str) -> str:
    paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in paragraphs)
    return (f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody>{paras}'
            f"</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")


def _notes(text: str) -> str:
    return (f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
            f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr></p:sp>'
            f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
            f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody><a:p><a:r><a:t>99</a:t></a:r></a:p></p:txBody></p:sp>"
            f"</p:spTree></p:cSld></p:notes>")


def _pptx(path: Path) -> Path:
    # Порядок показа — по списку слайдов, а не по именам файлов: slide2.xml первый.
    presentation = (f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
                    f'<p:sldId id="256" r:id="rId3"/><p:sldId id="257" r:id="rId2"/>'
                    f"</p:sldIdLst></p:presentation>")
    return _zip(path, {
        "_rels/.rels": _rels(("rId1", "officeDocument", "ppt/presentation.xml")),
        "ppt/presentation.xml": presentation,
        "ppt/_rels/presentation.xml.rels": _rels(("rId2", "slide", "slides/slide1.xml"),
                                                 ("rId3", "slide", "slides/slide2.xml")),
        "ppt/slides/slide1.xml": _slide("Сроки", "Релиз 15.11"),
        "ppt/slides/slide2.xml": _slide("План запуска"),
        "ppt/slides/_rels/slide1.xml.rels": _rels(("rId1", "notesSlide", "../notesSlides/notesSlide1.xml")),
        "ppt/notesSlides/notesSlide1.xml": _notes("Сказать про перенос"),
    })


def test_pptx_slides_in_show_order_with_speaker_notes(tmp_path):
    parsed = materials.parse(_pptx(tmp_path / "Запуск.pptx"))
    assert [(c["loc"], c["text"]) for c in parsed.chunks] == [
        ("слайд 1", "План запуска"),
        ("слайд 2", "Сроки\nРелиз 15.11\nЗаметки докладчика: Сказать про перенос"),
    ]


# --- xlsx ----------------------------------------------------------------------------


def _xlsx(path: Path) -> Path:
    workbook = (f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                f'<sheet name="Бюджет" sheetId="1" r:id="rId1"/><sheet name="Пусто" sheetId="2" r:id="rId2"/>'
                f"</sheets></workbook>")
    shared = f'<sst xmlns="{S}"><si><t>Статья</t></si><si><t>Сумма</t></si><si><r><t>Мат</t></r><r><t>веры</t></r></si></sst>'
    sheet = (f'<worksheet xmlns="{S}"><sheetData>'
             f'<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
             f'<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>300000</v></c></row>'
             f'<row r="4"><c r="A4" t="inlineStr"><is><t>Итого</t></is></c><c r="B4"><v>0.30000000000000004</v></c>'
             f'<c r="C4" t="b"><v>1</v></c></row>'
             f"</sheetData></worksheet>")
    return _zip(path, {
        "_rels/.rels": _rels(("rId1", "officeDocument", "xl/workbook.xml")),
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": _rels(("rId1", "worksheet", "worksheets/sheet1.xml"),
                                            ("rId2", "worksheet", "worksheets/sheet2.xml"),
                                            ("rId3", "sharedStrings", "sharedStrings.xml")),
        "xl/sharedStrings.xml": shared,
        "xl/worksheets/sheet1.xml": sheet,
        "xl/worksheets/sheet2.xml": f'<worksheet xmlns="{S}"><sheetData/></worksheet>',
    })


def test_xlsx_rows_with_sheet_and_row_range(tmp_path):
    parsed = materials.parse(_xlsx(tmp_path / "Бюджет.xlsx"))
    assert [(c["loc"], c["text"]) for c in parsed.chunks] == [
        ("лист Бюджет, строки 1–4", "Статья | Сумма\nСерверы | 300000\nИтого | 0.3 | TRUE"),
    ]


def test_xlsx_and_csv_take_only_the_first_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(materials, "SHEET_MAX_ROWS", 2)
    parsed = materials.parse(_xlsx(tmp_path / "Бюджет.xlsx"))
    assert parsed.chunks[0]["loc"] == "лист Бюджет, строки 1–2"
    assert any("первые 2 строк" in w for w in parsed.warnings)

    monkeypatch.setattr(materials, "CSV_MAX_ROWS", 3)
    data = "\n".join(f"строка{i};{i}" for i in range(1, 11)).encode("cp1251")
    got = materials.parse_bytes(data, "выгрузка.csv")
    assert got.chunks == [{"n": 1, "loc": "строки 1–3", "text": "строка1 | 1\nстрока2 | 2\nстрока3 | 3"}]
    assert got.warnings == [materials.ROWS_CUT.format("выгрузка.csv", 3)]


# --- md, txt ------------------------------------------------------------------------


def test_markdown_sections_by_headings_skip_frontmatter_and_code(tmp_path):
    text = ("---\ntags: [x]\n---\nВводный абзац.\n\n# Регламент\n\nПункт первый.\n\n"
            "```\n# не заголовок\n```\n\n## Раздел 3\n\nВебхуки подписываются секретом.\n")
    (tmp_path / "Регламент.md").write_text(text, encoding="utf-8")
    parsed = materials.parse(tmp_path / "Регламент.md")
    assert [c["loc"] for c in parsed.chunks] == ["", "Регламент", "Раздел 3"]
    assert "tags" not in parsed.text and "# не заголовок" in parsed.chunks[1]["text"]
    assert materials.outline(parsed).startswith("Вводный абзац.\nРазделы: Регламент; Раздел 3")


def test_txt_falls_back_to_cp1251(tmp_path):
    (tmp_path / "заметка.txt").write_bytes("# не заголовок в txt\n\nПривет, мир".encode("cp1251"))
    parsed = materials.parse(tmp_path / "заметка.txt")
    assert parsed.kind == "txt"
    assert [c["loc"] for c in parsed.chunks] == [""]
    assert "Привет, мир" in parsed.text


def test_chunks_follow_paragraphs_within_size_bounds():
    paragraphs = [f"Абзац {i}. " + "слово " * 40 for i in range(40)]  # ~250 символов
    paragraphs.append("Длинный " + "текст без абзацев. " * 200)  # ~3 800 символов
    parsed = materials.parse_text("\n\n".join(paragraphs), "длинный.md")
    sizes = [len(c["text"]) for c in parsed.chunks]
    assert max(sizes) <= materials.CHUNK_MAX
    # кроме хвостов — не меньше нижней границы
    assert all(s >= materials.CHUNK_MIN for s in sizes[:5])
    # абзац не режется посередине, если помещается целиком
    assert all(c["text"].split("\n")[0].startswith(("Абзац", "Длинный", "текст"))
               for c in parsed.chunks)


def test_text_beyond_the_limit_is_cut_with_a_note():
    text = "\n\n".join("строка " * 150 for _ in range(250))  # ~260 000 символов
    parsed = materials.parse_text(text, "огромный.md")
    assert parsed.chars == materials.MAX_TEXT_CHARS
    assert parsed.warnings == [materials.TRUNCATED.format("200 000")]


def test_input_size_and_kind_limits(tmp_path, monkeypatch):
    with pytest.raises(materials.MaterialError, match="не поддерживается"):
        materials.parse_bytes(b"x", "картинка.png")
    (tmp_path / "a.md").write_text("x" * 50, encoding="utf-8")
    monkeypatch.setattr(materials, "MAX_INPUT_BYTES", 10)
    with pytest.raises(materials.MaterialError, match="20 МБ"):
        materials.parse(tmp_path / "a.md")
    with pytest.raises(materials.MaterialError, match="20 МБ"):
        materials.parse_bytes(b"x" * 11, "a.md")
    with pytest.raises(materials.MaterialError, match="Файла нет"):
        materials.parse(tmp_path / "нет.md")


# --- pdf ----------------------------------------------------------------------------


@pytest.mark.skipif(importlib.util.find_spec("pypdf") is None, reason="нет pypdf")
def test_pdf_pages_become_locators():
    parsed = materials.parse(FIXTURES / "materials_text.pdf")
    assert parsed.kind == "pdf" and parsed.warnings == []
    assert [(c["loc"], c["text"]) for c in parsed.chunks] == [
        ("стр. 1", "Launch plan: release date is November 15."),
        ("стр. 2", "Budget review: payment gateway webhooks cost 300."),
    ]


@pytest.mark.skipif(importlib.util.find_spec("pypdf") is None, reason="нет pypdf")
def test_pdf_without_text_layer_warns():
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    parsed = materials.parse_bytes(out.getvalue(), "скан.pdf")
    assert parsed.chunks == [] and parsed.warnings == [materials.NO_PDF_TEXT]


def test_pdf_without_pypdf_degrades_to_a_warning(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)  # import pypdf → ImportError
    parsed = materials.parse(FIXTURES / "materials_text.pdf")
    assert parsed.chunks == [] and parsed.warnings == [materials.NO_PYPDF]
    assert len(parsed.sha256) == 64


# --- хранение в папке записи ----------------------------------------------------------


def _rec(tmp_path) -> Path:
    folder = tmp_path / "lib" / "2026-10-06_10-00"
    folder.mkdir(parents=True)
    return folder


def test_add_stores_parsed_text_not_the_file(tmp_path):
    rec = _rec(tmp_path)
    src = tmp_path / "docs" / "План.docx"
    src.parent.mkdir()
    _docx(src, _p("Сроки", "1") + _p("Запуск 15.11"))
    got = materials.add(rec, src)
    assert got["id"] == "a1" and got["status"] == "ok" and got["status_label"] == ""
    assert got["title"] == "План.docx" and got["origin"] == "file" and got["chunks"] == 1
    stored = sorted(p.relative_to(rec).as_posix() for p in rec.rglob("*") if p.is_file())
    assert stored == ["assistant/materials/a1.json"]  # исходник не скопирован
    data = json.loads((rec / "assistant/materials/a1.json").read_text(encoding="utf-8"))
    assert data["v"] == 1 and data["id"] == "a1" and set(data) == {"v", "id", "meta", "summary", "chunks"}
    assert data["meta"]["source"]["path"] == str(src)
    assert data["meta"]["source"]["sha256"] == materials.parse(src).sha256
    assert data["chunks"] == [{"n": 1, "loc": "Сроки", "text": "Сроки\nЗапуск 15.11"}]
    # тот же файл без изменений — не дублируется
    again = materials.add(rec, src)
    assert again["id"] == "a1" and again["duplicate"] is True
    assert len(materials.records(rec)) == 1


def test_changed_and_missing_source_are_marked_text_stays(tmp_path):
    rec = _rec(tmp_path)
    src = tmp_path / "заметка.md"
    src.write_text("# Тема\n\nПервая версия.\n", encoding="utf-8")
    aid = materials.add(rec, src)["id"]
    record = materials.read(rec, aid)
    assert materials.status(record) == "ok"
    # время правки сменилось, содержимое — нет: не «изменён»
    os.utime(src, (1, 1))
    assert materials.status(record) == "ok"
    src.write_text("# Тема\n\nВторая версия, длиннее.\n", encoding="utf-8")
    assert materials.status(record) == "changed"
    assert materials.attachment(record)["status_label"] == "изменён"
    src.unlink()
    assert materials.attachment(record)["status_label"] == "нет файла"
    assert "Первая версия." in materials.read(rec, aid)["chunks"][0]["text"]
    # изменённый файл можно добавить заново — это новый материал
    src.write_text("Третья версия", encoding="utf-8")
    assert materials.add(rec, src)["id"] == "a2"


def test_folder_is_one_material_with_file_locators(tmp_path):
    rec = _rec(tmp_path)
    proj = tmp_path / "Проект"
    (proj / "docs").mkdir(parents=True)
    (proj / ".git").mkdir()
    (proj / "README.md").write_text("# Обзор\n\nПро проект.\n", encoding="utf-8")
    (proj / "docs" / "сроки.txt").write_text("Срок 15.11", encoding="utf-8")
    (proj / ".git" / "config.md").write_text("скрыто", encoding="utf-8")
    (proj / "logo.png").write_bytes(b"\x89PNG")
    got = materials.add(rec, proj)
    assert got["kind"] == "folder" and got["title"] == "Проект"
    record = materials.read(rec, got["id"])
    assert [c["loc"] for c in record["chunks"]] == ["README.md, Обзор", "docs/сроки.txt"]
    assert materials.status(record) == "ok"
    (proj / "docs" / "новое.md").write_text("ещё", encoding="utf-8")
    assert materials.status(record) == "changed"


def test_folder_takes_at_most_the_file_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(materials, "MAX_FOLDER_FILES", 2)
    for i in range(3):
        (tmp_path / f"f{i}.md").write_text(f"файл {i}", encoding="utf-8")
    parsed = materials.parse_folder(tmp_path)
    assert [c["loc"] for c in parsed.chunks] == ["f0.md", "f1.md"]
    assert parsed.warnings == [materials.FOLDER_CUT.format(2)]


def test_session_limits(tmp_path, monkeypatch):
    rec = _rec(tmp_path)
    for i in range(4):
        (tmp_path / f"n{i}.md").write_text(f"заметка {i} " * 10, encoding="utf-8")
    monkeypatch.setattr(materials, "MAX_MATERIALS", 2)
    monkeypatch.setattr(materials, "MAX_GROUP_MATERIALS", 1)
    materials.add(rec, tmp_path / "n0.md")
    materials.add(rec, tmp_path / "n1.md", origin="kb")
    with pytest.raises(materials.MaterialError, match="уже 2 материалов"):
        materials.add(rec, tmp_path / "n2.md")
    # у папки базы знаний группы — своя квота
    assert materials.add(rec, tmp_path / "n2.md", origin="kb_folder")["id"] == "a3"
    with pytest.raises(materials.MaterialError, match="папки группы"):
        materials.add(rec, tmp_path / "n3.md", origin="kb_folder")
    monkeypatch.setattr(materials, "MAX_GROUP_MATERIALS", 5)
    monkeypatch.setattr(materials, "MAX_SESSION_CHARS", 300)
    with pytest.raises(materials.MaterialError, match="2 млн"):
        materials.add(rec, tmp_path / "n3.md", origin="kb_folder")


def test_kb_ref_and_paths_outside_the_kb(tmp_path):
    rec = _rec(tmp_path)
    kb = tmp_path / "kb"
    (kb / "Проекты").mkdir(parents=True)
    (kb / "Проекты" / "Альфа.md").write_text("# Альфа\n\nПро проект.\n", encoding="utf-8")
    got = materials.add(rec, "Проекты/Альфа.md", origin="kb", kb_root=kb, kind="kb_note")
    assert got["kb_ref"] == "Проекты/Альфа.md"
    assert got["summary"] == "Про проект.\nРазделы: Альфа"  # сводка заметки — без модели
    assert materials.read(rec, got["id"])["meta"]["view"] == "kb_note"
    with pytest.raises(materials.MaterialError, match="вне базы"):
        materials.add(rec, "../секрет.md", origin="kb", kb_root=kb)


def test_ids_are_shared_with_images_and_never_reused(tmp_path):
    rec = _rec(tmp_path)
    files = rec / "assistant" / "files"
    files.mkdir(parents=True)
    (files / "a1.png").write_bytes(b"x")
    aid, path = materials.claim_id(rec, ".json")
    assert aid == "a2" and path == rec / "assistant" / "materials" / "a2.json"
    aid, path = materials.claim_id(rec, ".jpg")
    assert aid == "a3" and path.parent == files


def test_broken_records_are_skipped(tmp_path):
    rec = _rec(tmp_path)
    folder = rec / "assistant" / "materials"
    folder.mkdir(parents=True)
    (folder / "a1.json").write_text("{не json", encoding="utf-8")
    (folder / "a2.json").write_text("", encoding="utf-8")  # занятый, ещё не дописанный
    (folder / "заметка.json").write_text("{}", encoding="utf-8")
    assert materials.records(rec) == []
    assert materials.read(rec, "../a1") is None


# --- слова для поиска ----------------------------------------------------------------


def test_terms_drop_function_words():
    assert materials.terms("Ну давайте это вот так: SLA и вебхуки, 15 ноября!") == ["sla", "вебхуки", "15", "ноября"]
