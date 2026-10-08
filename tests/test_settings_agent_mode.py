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


def test_the_child_marks_the_notice_outside_config_json(tmp_path, monkeypatch):
    """Ребёнок `meet assist` не пишет `config.json` (его пишет резидент, замок
    у них разный — правка из окна терялась бы): «показано» — отдельная отметка
    в папке данных, создаётся атомарно и не трогает настроек."""
    import threading

    from meet import paths
    from meet.assist import participant as part

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))
    config = paths.config_path()
    settings_mod.patch({"assist": {"frequency": "less"}}, config)
    before = config.read_bytes()
    assert part.mode_noticed(Settings()) is False

    part._mark_mode_noticed()
    part._mark_mode_noticed()                                    # второй раз — не ошибка
    assert config.read_bytes() == before
    assert (tmp_path / part.MODE_NOTICED_MARK).is_file()
    assert part.mode_noticed(Settings()) is True
    # прежний флаг в config.json (сборки до этой правки) тоже считается
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "other"))
    assert part.mode_noticed(Settings.from_raw({"assist": {"agent_mode_noticed": True}})) is True
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path))

    # одновременно: правки резидента и отметка ребёнка — правки не теряются
    errors = []

    def resident(i):
        try:
            settings_mod.patch({"recording": {"speaker_name": f"Имя {i}"}}, config)
        except Exception as e:  # pragma: no cover - отчёт о сбое
            errors.append(e)

    threads = [threading.Thread(target=resident, args=(i,)) for i in range(20)]
    threads += [threading.Thread(target=part._mark_mode_noticed) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    raw = json.loads(config.read_text(encoding="utf-8"))
    assert "agent_mode_noticed" not in raw["assist"] and raw["assist"]["frequency"] == "less"
    assert raw["recording"]["speaker_name"].startswith("Имя ")


def test_from_settings_skips_the_notice_after_the_mark(tmp_path, monkeypatch):
    import asyncio

    from meet.assist import participant as part
    from meet.assist.bus import TranscriptBus

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    folder = tmp_path / "lib" / "rec"
    folder.mkdir(parents=True)

    def notices():
        p = part.from_settings(Settings(), TranscriptBus(), folder, "claude-code", None,
                               log=lambda _m: None, mode_notice=True)

        async def main():
            await p.start()
            await p.shutdown()

        asyncio.run(main())
        return [m for m in p._chatlog.messages() if m.get("notice") == "agent_mode"]

    assert len(notices()) == 1
    assert (tmp_path / "data" / part.MODE_NOTICED_MARK).is_file()
    assert not (tmp_path / "data" / "config.json").exists()      # настроек ребёнок не создаёт
    assert len(notices()) == 1                                   # новой строки нет
