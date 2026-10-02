from pathlib import Path

from meet.assist.prompts import (
    GLOSSARY_IN_TICK,
    build_hints_delta,
    build_hints_seed,
    build_hints_system,
    build_qa_system,
    build_repair_lines,
    build_repair_once,
    build_summary_prompt,
    build_summary_system,
    load_glossary,
)


def test_load_glossary_missing_returns_empty(tmp_path):
    assert load_glossary(tmp_path) == ""


def test_load_glossary_reads_file(tmp_path):
    (tmp_path / "glossary.txt").write_text("джоба — задача\n", encoding="utf-8")
    assert "джоба" in load_glossary(tmp_path)


def test_hints_system_has_line_schema_kinds_quality_rules_and_owner():
    s = build_hints_system("термин X", "# Контекст задачи demo", max_hints=5, owner="Кузьма")
    assert '"op":"add"' in s and '"op":"none"' in s and "ask_you" in s and '"reply"' in s
    assert "не больше 5" in s and "«Кузьма»" in s
    assert "не больше 1–2 новых" in s and "общих советов" in s
    assert "весь разговор" in s                      # контекст прежнего диалога
    assert "термин X" in s and "Контекст задачи demo" in s
    assert "{{" not in s and "{max_hints}" not in s


def test_summary_system_has_no_hint_schema():
    s = build_summary_system("", "")
    assert '"op":"topic"' in s and '"section":"tasks"' in s and '"op":"none"' in s
    assert "ask_you" not in s and '"kind"' not in s


def test_systems_trim_glossary_and_task_context():
    for build in (lambda g, t: build_summary_system(g, t), lambda g, t: build_hints_system(g, t)):
        base = build("", "")
        s = build("г" * 10_000, "к" * 10_000)
        assert s.count("г") - base.count("г") <= GLOSSARY_IN_TICK
        assert s.count("к") - base.count("к") <= 1000


def test_hints_delta_only_new_lines_ids_excerpts_and_trigger():
    p = build_hints_delta(["[00:01:00] Ольга: новое"], "Активные подсказки: h1",
                          [{"term": "Шлюз", "ref": "Шлюз.md", "text": "Сервис платежей"}],
                          ("question", "00:01:00"))
    assert p.index("новое") < p.index("h1") < p.index("Шлюз.md") < p.index("Повод")
    assert "Сводка" not in p
    assert "База знаний" not in build_hints_delta(["x"], "Активные подсказки: нет", [])


def test_hints_seed_order():
    p = build_hints_seed(summary="Тема: Запуск", hints="[h1] (risk) Нет владельца",
                         earlier=["[00:00:10] Ольга: давнее"], recent=["[00:05:00] Ольга: недавнее"],
                         new_lines=["[00:06:00] Ольга: новое"], excerpts=[])
    assert p.index("Запуск") < p.index("Нет владельца") < p.index("давнее") < p.index("недавнее") < p.index("новое")


def test_summary_prompt_sections():
    p = build_summary_prompt("Тема: Запуск", ["[00:01:00] Вы: новое"], ["[00:00:30] Вы: старое"])
    assert p.index("Запуск") < p.index("старое") < p.index("новое")


def test_repair_prompts_carry_errors_lines_and_original():
    lines = build_repair_lines([("плохо" * 1000, "нет поля 'text'")])
    assert "нет поля 'text'" in lines and len(lines) < 2000 and '{"op":"none"}' in lines
    once = build_repair_once("ИСХОДНЫЙ", [("{", "строка JSON оборвана")])
    assert "ИСХОДНЫЙ" in once and "оборвана" in once


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


def test_qa_system_names_the_recording_folder():
    folder = Path("D:/Записи/2026-10-02_10-00")
    assert str(folder) in build_qa_system("", "", None, folder=folder)
    assert "Папка этой записи" not in build_qa_system("", "", None)
