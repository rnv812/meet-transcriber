from pathlib import Path

from meet.assist.prompts import (
    GLOSSARY_IN_TICK,
    build_digester_system,
    build_qa_system,
    build_repair_prompt,
    build_tick_prompt,
    load_glossary,
)


def test_load_glossary_missing_returns_empty(tmp_path):
    assert load_glossary(tmp_path) == ""


def test_load_glossary_reads_file(tmp_path):
    (tmp_path / "glossary.txt").write_text("джоба — задача\n", encoding="utf-8")
    assert "джоба" in load_glossary(tmp_path)


def test_digester_system_has_json_schema_hints_and_context():
    s = build_digester_system("термин X", "# Контекст задачи demo", max_hints=5)
    assert '"ops"' in s and '"section":"hints"' in s and "unanswered" in s
    assert "не больше 5" in s
    assert "термин X" in s and "Контекст задачи demo" in s
    assert "{" in s and "{{" not in s  # фигурные скобки схемы не удвоены


def test_summary_only_system_has_no_hint_schema():
    s = build_digester_system("", "", hints=False)
    assert '"section":"hints"' not in s and "Подсказки не нужны" in s


def test_digester_system_trims_glossary_and_task_context():
    base = build_digester_system("", "")
    s = build_digester_system("г" * 10_000, "к" * 10_000)
    assert s.count("г") - base.count("г") <= GLOSSARY_IN_TICK
    assert s.count("к") - base.count("к") <= 1000


def test_tick_prompt_sections():
    p = build_tick_prompt("Тема: Запуск", ["[00:01:00] Вы: новое"], ["[00:00:30] Вы: старое"],
                          [{"term": "Шлюз", "ref": "Шлюз.md", "text": "Сервис платежей"}])
    assert p.index("старое") < p.index("Шлюз.md") < p.index("новое")
    assert build_tick_prompt("(пока пусто)", ["x"], [], []).count("База знаний") == 0


def test_repair_prompt_carries_error_bad_reply_and_original():
    p = build_repair_prompt("ИСХОДНЫЙ", "плохо" * 1000, "нет поля 'text'")
    assert "нет поля 'text'" in p and "ИСХОДНЫЙ" in p and len(p) < 2000


def test_qa_system_vault_rules_only_with_vault():
    with_vault = build_qa_system("", "", Path("C:/vault/Claude"))
    without = build_qa_system("", "", None)
    assert "superseded" in with_vault and "хаб" in with_vault
    assert "superseded" not in without
    assert "[00:12:34]" in without  # просит ссылаться на таймкоды


def test_knowledge_block_only_when_given():
    kb = Path("C:/kb/Docs")
    assert str(kb) in build_qa_system("", "", None, knowledge=kb)
    assert build_qa_system("", "", None) == build_qa_system("", "", None, knowledge=None)
