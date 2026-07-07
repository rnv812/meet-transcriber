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
