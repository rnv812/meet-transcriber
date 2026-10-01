from pathlib import Path

from meet.assist.context import collect_task_context


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "Claude"
    task = vault / "demo-task"
    (task / "docs").mkdir(parents=True)
    (vault / "Claude Docs.md").write_text(
        "# Индекс\n- [[_demo-task|Demo Task — пример задачи]]\n", encoding="utf-8"
    )
    (task / "_demo-task.md").write_text(
        "---\ntype: hub\ntask: demo-task\n---\n# Demo Task\n\n"
        "## Сейчас\n\n- **Состояние:** пилим прототип\n- **Следующий шаг:** демо\n\n"
        "## Главный документ\n\n[[2026-07-01_концепция-демо]]\n",
        encoding="utf-8",
    )
    (task / "docs" / "2026-07-01_концепция-демо.md").write_text(
        "---\ntype: concept\ntask: demo-task\n---\n# Концепция\n\nСуть решения.\n\n"
        "## Детали\n\nМного текста.\n\n## Открытые вопросы\n\n- Как деплоим?\n",
        encoding="utf-8",
    )
    return vault


def test_collects_now_main_intro_and_questions(tmp_path):
    ctx = collect_task_context(_make_vault(tmp_path), "demo-task")
    assert "пилим прототип" in ctx
    assert "Суть решения." in ctx
    assert "Как деплоим?" in ctx
    assert "Много текста." not in ctx  # середину главного не тащим


def test_finds_task_via_index_by_substring(tmp_path):
    ctx = collect_task_context(_make_vault(tmp_path), "Demo Task")
    assert "пилим прототип" in ctx


def test_unknown_task_returns_empty(tmp_path):
    assert collect_task_context(_make_vault(tmp_path), "no-such") == ""


def test_index_and_hub_names_are_configurable(tmp_path):
    vault = tmp_path / "notes"
    (vault / "proj").mkdir(parents=True)
    (vault / "Задачи.md").write_text("- [[hub-alpha|Альфа — пилот]]\n", encoding="utf-8")
    (vault / "proj" / "hub-alpha.md").write_text(
        "# Альфа\n\n## Сейчас\n\n- готовим пилот\n", encoding="utf-8")
    assert collect_task_context(vault, "Альфа") == ""   # умолчания этой конвенции не знают
    ctx = collect_task_context(vault, "Альфа", index="Задачи.md", hub_prefix="hub-")
    assert "готовим пилот" in ctx
    ctx = collect_task_context(vault, "alpha", index="Задачи.md", hub_prefix="hub-")
    assert ctx.startswith("Задача: alpha") and "готовим пилот" in ctx


def test_assist_settings_carry_the_vault_names():
    from meet import settings

    cfg = settings.Settings.from_raw({"version": 2})
    assert (cfg.assist.vault_index, cfg.assist.hub_prefix) == ("Claude Docs.md", "_")
    cfg = settings.Settings.from_raw({"version": 2, "assist": {"vault_index": "Index.md", "hub_prefix": ""}})
    assert (cfg.assist.vault_index, cfg.assist.hub_prefix) == ("Index.md", "")
    assert cfg.assist.to_raw()["vault_index"] == "Index.md"
