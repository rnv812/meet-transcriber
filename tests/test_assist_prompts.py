from pathlib import Path

from meet.assist.prompts import build_digester_system, build_qa_system, load_glossary


def test_load_glossary_missing_returns_empty(tmp_path):
    assert load_glossary(tmp_path) == ""


def test_load_glossary_reads_file(tmp_path):
    (tmp_path / "glossary.txt").write_text("джоба — задача\n", encoding="utf-8")
    assert "джоба" in load_glossary(tmp_path)


def test_digester_system_includes_protocol_and_context():
    s = build_digester_system("термин X", "# Контекст задачи demo")
    assert "ADD" in s and "НЕТ_НОВЫХ" in s
    assert "термин X" in s and "Контекст задачи demo" in s


def test_qa_system_vault_rules_only_with_vault():
    with_vault = build_qa_system("", "", Path("C:/vault/Claude"))
    without = build_qa_system("", "", None)
    assert "superseded" in with_vault and "хаб" in with_vault
    assert "superseded" not in without


def test_knowledge_block_only_when_given():
    kb = Path("C:/kb/Docs")
    assert str(kb) in build_qa_system("", "", None, knowledge=kb)
    assert str(kb) in build_digester_system("", "", knowledge=kb)
    assert build_qa_system("", "", None) == build_qa_system("", "", None, knowledge=None)
    assert "База знаний" not in build_digester_system("", "")
