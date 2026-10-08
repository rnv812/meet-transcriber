"""`assist.agent_mode` (0.4, спец. §7): `auto` — по умолчанию и у нового, и у
обновившегося пользователя (решение 1: автомод сразу), в файл не пишется,
пока равен умолчанию; `confirm` — «Спрашивать перед каждым действием».
`assist.agent_mode_noticed` — однократная строка об автомоде в первой
сессии после обновления (показана — больше нет)."""

import json

from meet import settings as settings_mod
from meet.settings import Settings


def test_default_is_auto_for_new_and_updated_users():
    assert Settings().assist.agent_mode == "auto"
    old = Settings.from_raw({"version": 3, "assist": {"agent_freedom": True, "frequency": "less"}})
    assert old.assist.agent_mode == "auto" and old.assist.agent_mode_noticed is False
    assert Settings.from_raw({"assist": {"agent_mode": "confirm"}}).assist.agent_mode == "confirm"
    assert Settings.from_raw({"assist": {"agent_mode": "мусор"}}).assist.agent_mode == "auto"


def test_defaults_are_not_written_and_a_choice_is(tmp_path):
    path = tmp_path / "config.json"
    settings_mod.patch({"assist": {"frequency": "less"}}, path)
    raw = json.loads(path.read_text(encoding="utf-8"))["assist"]
    assert "agent_mode" not in raw and "agent_mode_noticed" not in raw
    settings_mod.patch({"assist": {"agent_mode": "confirm"}}, path)
    settings_mod.patch({"assist": {"frequency": "more"}}, path)
    assert settings_mod.load(path).assist.agent_mode == "confirm"
    settings_mod.patch({"assist": {"agent_mode_noticed": True}}, path)
    assert settings_mod.load(path).assist.agent_mode_noticed is True


def test_the_live_assistant_shows_the_notice_once(tmp_path, monkeypatch):
    import asyncio

    from meet.assist.bus import TranscriptBus
    from meet.assist.participant import MODE_NOTICE, from_settings

    folder = tmp_path / "lib" / "rec"
    folder.mkdir(parents=True)
    marked = []
    monkeypatch.setattr("meet.assist.participant._mark_mode_noticed", lambda: marked.append(True))

    def notices(cfg, **kw):
        p = from_settings(cfg, TranscriptBus(), folder, "claude-code", None, log=lambda _m: None, **kw)

        async def main():
            await p.start()
            await p.shutdown()

        asyncio.run(main())
        return [m for m in p._chatlog.messages() if m.get("notice") == "agent_mode"]

    assert notices(Settings(), mode_notice=False) == []          # «Продолжить разговор» — без строки
    shown = notices(Settings(), mode_notice=True)
    assert [m["text"] for m in shown] == [MODE_NOTICE] and marked == [True]
    noticed = Settings.from_raw({"assist": {"agent_mode_noticed": True}})
    assert len(notices(noticed, mode_notice=True)) == 1          # та же строка, новой нет
    confirm = Settings.from_raw({"assist": {"agent_mode": "confirm"}})
    assert len(notices(confirm, mode_notice=True)) == 1          # спрашивать каждое действие — строки нет
    assert marked == [True]
