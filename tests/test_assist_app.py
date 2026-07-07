import asyncio
from pathlib import Path

from meet.assist.app import AssistState
from meet.assist.bus import TranscriptBus
from meet.assist.digest import Digest


def test_state_set_task_rebuilds_prompts(tmp_path, monkeypatch):
    vault = tmp_path / "Claude"
    (vault / "demo").mkdir(parents=True)
    (vault / "demo" / "_demo.md").write_text(
        "---\ntype: hub\n---\n# Demo\n\n## Сейчас\n\n- **Состояние:** пилот\n",
        encoding="utf-8",
    )
    state = AssistState(bus=TranscriptBus(), digest=Digest(),
                        glossary="джоба", vault=vault, cwd=tmp_path)
    assert "джоба" in state.digester_system
    asyncio.run(state.set_task("demo"))
    assert "пилот" in state.digester_system and "пилот" in state.qa_system


def test_state_without_vault_has_no_vault_rules(tmp_path):
    state = AssistState(bus=TranscriptBus(), digest=Digest(),
                        glossary="", vault=None, cwd=tmp_path)
    assert "superseded" not in state.qa_system
    assert state.qa_allowed_dirs == (tmp_path,)
