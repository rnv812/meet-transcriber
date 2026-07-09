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


def _run_assist_capturing_matcher(tmp_path, monkeypatch, **kwargs):
    """Гоняет run_assist с замоканными тяжёлыми частями и возвращает
    voice_matcher, с которым был создан LiveEngine."""
    captured = {}

    class FakeEngine:
        def __init__(self, out_dir, transcriber, **kw):
            captured["voice_matcher"] = kw.get("voice_matcher", "MISSING")

        def start(self):
            pass

        def stop(self):
            pass

        def process_window(self):
            pass

    async def fake_check_auth():
        return None

    async def fake_main(state, port):
        pass

    monkeypatch.setattr("meet.assist.app.check_auth", fake_check_auth)
    monkeypatch.setattr("meet.assist.app._main", fake_main)
    monkeypatch.setattr("meet.asr.Transcriber", lambda: object())
    monkeypatch.setattr("meet.live.LiveEngine", FakeEngine)

    from meet.assist.app import run_assist

    run_assist(out_root=str(tmp_path), **kwargs)
    return captured["voice_matcher"]


def test_run_assist_creates_voice_matcher_by_default(tmp_path, monkeypatch):
    from meet.voice_id import VoiceMatcher

    matcher = _run_assist_capturing_matcher(tmp_path, monkeypatch)
    assert isinstance(matcher, VoiceMatcher)


def test_run_assist_no_voices_disables_matcher(tmp_path, monkeypatch):
    matcher = _run_assist_capturing_matcher(tmp_path, monkeypatch, no_voices=True)
    assert matcher is None
