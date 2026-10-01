"""Описание выпуска: release.yml публикует только тег, для которого есть
docs/release-notes/<тег>.md. Первый публичный выпуск — v0.2.0 (0.1.0 был
закрытым кандидатом, 0.1.1 — тестовой сборкой)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs" / "release-notes"


def test_release_workflow_takes_notes_by_tag():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert '$notes = "docs/release-notes/$tag.md"' in workflow
    assert "body_path: ${{ steps.version.outputs.notes }}" in workflow


def test_first_public_release_has_notes_for_new_and_test_users():
    text = (NOTES / "v0.2.0.md").read_text(encoding="utf-8")
    for part in ("SmartScreen", "4,5 ГБ", "Hugging Face", "## Приватность",
                 "## Известные ограничения", "## Лицензия", "Если у вас была тестовая версия 0.1.x",
                 "Все изменения с 0.1.0"):
        assert part in text
    assert not (NOTES / "unreleased.md").exists()  # слито в v0.2.0.md
