"""Профили сессии ассистента (0.3.7, P1): «Рабочая встреча» и «Нейтральный».

- промпт: «Рабочая встреча» — слово в слово как до профилей (снимок
  `fixtures/participant_prompts/work_snapshot.json`), «Нейтральный» — без
  карты, правил базы знаний и рабочей рамки, со своей ролью и примерами;
- смена профиля по ходу — пометка в ходе, сеанс модели пересоздаётся с
  продолжением; профиль — в журнале встречи (`sessions.json`);
- в «Нейтральном» база знаний и библиотека не в папках модели, запасные
  запросы к базе выключены;
- настройка `assist.profile`, маршруты ребёнка и резидента.

Модель не зовётся: поддельные диалог и runner, журнал и шина — настоящие, во
временной папке."""

import asyncio
import json
import re
from pathlib import Path

import pytest

from meet import control, settings
from meet.assist import participant_prompts as pp
from meet.assist.bus import TranscriptBus
from meet.assist.chatlog import ChatLog
from meet.assist.kb_prep import KnowledgeBase
from meet.assist.participant import NEUTRAL_NO_KB, Participant, from_settings, session_profile
from meet.settings import Settings

from test_assist_participant import SILENT, FakeRunner, _make, publish, run, say
from test_chat_resident import RID, app, state  # noqa: F401  (фикстуры pytest)
from test_live_attach_resident import _recording_resident
from test_live_control import _wait_for, data_dir, make_live, resident  # noqa: F401

SNAPSHOT = Path(__file__).parent / "fixtures" / "participant_prompts" / "work_snapshot.json"

# Те же параметры, что у снимка (снят с промпта до профилей).
SNAPSHOT_CASES = {
    "tools_map": dict(frequency="чаще", tools_available=True, kb_map="Проекты/\n  План.md",
                      owner_name="Ирина", kb_exclude=("Личное/",),
                      folders={"База знаний": "C:/kb", "Библиотека встреч": "C:/lib",
                               "Эта встреча": "C:/lib/m"},
                      glossary="SLA — соглашение", task_context="Запуск Альфы"),
    "no_tools": dict(frequency="реже", tools_available=False, kb_map="", owner_name=""),
    "plain": dict(),
}

# Рабочая рамка и база знаний — в «Нейтральном» их быть не должно.
WORK_WORDS = (r"встреч", r"рабоч", r"работ", r"коллег", r"задач", r"срок", r"владел",
              r"баз\w* знаний", r"документ", r"карт\w* баз", r"\bкарт[аеуы]\b", r"Глянь",
              r"прошл\w* встреч", r'"meet:', r'"read"', r'"search"', r'"list"', r"глоссари")


def _work_hits(text: str) -> list[str]:
    return [w for w in WORK_WORDS if re.search(w, text, flags=re.I)]


# --- промпт ------------------------------------------------------------------------


def test_work_prompt_is_unchanged_against_the_snapshot():
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for name, kwargs in SNAPSHOT_CASES.items():
        assert pp.build_system(**kwargs) == snap[name], name
        assert pp.build_system(profile="work", **kwargs) == snap[name], name
        assert pp.build_system(profile="Рабочая встреча", **kwargs) == snap[name], name


@pytest.mark.parametrize("tools", [True, False])
@pytest.mark.parametrize("frequency", ["реже", "обычно", "чаще"])
def test_neutral_prompt_has_no_work_or_kb_wording(tools, frequency):
    text = pp.build_system(profile="neutral", frequency=frequency, tools_available=tools,
                           kb_map="Проекты/\n  План запуска.md\nВстречи/\n  Ретро.md",
                           owner_name="Ирина", kb_exclude=("Личное/",),
                           folders={"Папка этой записи": "C:/rec/s1"},
                           glossary="SLA — соглашение", task_context="Запуск Альфы")
    assert _work_hits(text) == []
    for leaked in ("План запуска", "Ретро", "Личное", "SLA", "Альф", "ДАННЫЕ\nПроекты"):
        assert leaked not in text
    assert "# База знаний" not in text and "# Карта базы знаний" not in text
    assert "# Контекст задачи" not in text and "# Глоссарий" not in text
    # Своя роль: тип контента, ответы, краткое содержание, нейтральный тон.
    assert text.startswith("Ты смотришь и слушаешь вместе с пользователем: это может быть созвон, "
                           "стрим, видео, подкаст")
    assert "Сначала пойми по репликам, что это" in text and "один раз коротко скажи" in text
    assert "Отвечай на вопросы пользователя" in text and "Краткое содержание" in text
    assert "Тон нейтральный" in text
    assert "Пользователя зовут Ирина" in text
    # Закреп — только когда к пользователю обратились по имени.
    assert 'обратились по имени' in text and 'В остальных случаях "pin" не ставь' in text
    # Частота — те же три ступени, своя фраза.
    assert f"# Как часто писать: «{frequency}»" in text
    assert pp.NEUTRAL_FREQUENCIES[frequency] in text
    # Свои примеры.
    assert "# Примеры" in text and "Похоже, это стрим" in text and "Кратко:" in text
    assert pp.H_TRANSCRIPT_NEUTRAL in text and pp.H_TRANSCRIPT not in text
    # Протокол и реакции — те же.
    assert '{"silent": true}' in text and "👎 «Не по теме»" in text and '"explains"' in text
    if tools:
        assert "C:/rec/s1" in text
    else:
        assert "C:/rec/s1" not in text


def test_neutral_examples_are_valid_json_lines():
    text = pp.build_system(profile="neutral").split("# Примеры", 1)[1]
    replies = [line for line in text.splitlines() if line.startswith("{")]
    assert len(replies) == 6
    for line in replies:
        json.loads(line)
    assert "\n- [03:12]" in json.loads(replies[2])["say"]       # список — переводами строк


def test_neutral_without_examples_and_normalize_profile():
    assert "# Примеры" not in pp.build_system(profile="neutral", examples=False)
    assert pp.normalize_profile("neutral") == "neutral"
    assert pp.normalize_profile(" Нейтральный ") == "neutral"
    assert pp.normalize_profile("Рабочая встреча") == "work"
    for bad in (None, "", "stream", 1):
        assert pp.normalize_profile(bad) == "work"


def test_profile_change_produces_the_note():
    note = pp.profile_note("neutral")
    assert note.startswith("Профиль сменён на «Нейтральный» — это заменяет прежние правила роли: ")
    assert "созвон, стрим, видео, подкаст" in note
    d = pp.delta(notes=(), profile="neutral", profile_changed=True)
    assert pp.H_NOTES in d and f"- {note}" in d and d.endswith(pp.REMINDER)
    work = pp.delta(profile="work", profile_changed=True, kb_map="Проекты/\n  План.md")
    assert "Профиль сменён на «Рабочая встреча» — это заменяет прежние правила роли: " in work
    # Обратно к работе — карта приходит вместе с пометкой (Codex не перечитывает промпт).
    assert "Карта базы знаний" in work and "План.md" in work
    # В «Нейтральный» — карты нет, даже если её передали.
    assert "План.md" not in pp.delta(profile="neutral", profile_changed=True,
                                     kb_map="Проекты/\n  План.md")
    # Без смены — ни пометки, ни карты; пустой ход — пусто.
    assert pp.delta(profile="neutral") == ""
    assert "Профиль" not in pp.delta(["x"], profile="neutral", kb_map="План.md")


def test_neutral_delta_and_seed_wording():
    lines = [{"t": 5, "speaker": "Спикер 1", "text": "всем привет, мы в эфире"}]
    d = pp.delta(lines, profile="neutral", frequency="реже")
    assert pp.H_TRANSCRIPT_NEUTRAL in d and pp.H_TRANSCRIPT not in d
    assert pp.NEUTRAL_FREQUENCIES["реже"] in d and pp.FREQUENCIES["реже"] not in d
    # «Рабочая встреча» — как раньше.
    w = pp.delta(lines, frequency="реже")
    assert pp.H_TRANSCRIPT in w and pp.FREQUENCIES["реже"] in w
    s = pp.seed(None, "Проекты/\n  План.md", "", {"profile": "neutral", "frequency": "more"},
                transcript=lines, t=65)
    assert s.startswith(pp.SEED_NEW_NEUTRAL) and "Профиль: «Нейтральный»" in s
    assert "План.md" not in s and "Карта" not in s and "встреч" not in s
    assert pp.H_EARLIER_NEUTRAL in s and "Сейчас [01:05] от начала." in s
    sw = pp.seed(None, "Проекты/\n  План.md", "", {"frequency": "more"}, transcript=lines, t=65)
    assert sw.startswith(pp.SEED_NEW) and "План.md" in sw and pp.H_EARLIER in sw


# --- агент: папки, карта, запасной путь ------------------------------------------------


def _kb(tmp_path):
    root = tmp_path / "kb"
    (root / "Проекты").mkdir(parents=True)
    (root / "Проекты" / "План запуска.md").write_text("# План\n\nЗапуск 14.11.", encoding="utf-8")
    return KnowledgeBase(root, exclude=(), library_root=tmp_path / "lib")


def _participant(tmp_path, *, provider="claude-code", profile="work", runner=None, script=None):
    h = _make(tmp_path, provider=provider, script=script, runner=runner, kb=_kb(tmp_path),
              profile=profile)
    return h.p, h, h.chat, h.clock, h.made


def _line(h, t, text, speaker="Спикер 1"):
    publish(h, t, speaker, text)


def test_work_session_keeps_kb_and_library_in_add_dirs(tmp_path):
    p, h, _chat, clock, made = _participant(tmp_path, script=[SILENT])

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        await p.shutdown()

    run(main())
    kw = made[0].kwargs
    assert kw["add_dirs"] == [str(p._folder), str(tmp_path / "kb"), str(tmp_path / "lib")]
    assert "План запуска" in kw["system_prompt"] and "# База знаний и прошлые встречи" in kw["system_prompt"]
    assert p.view()["profile"] == "work" and p.view()["sees"]["kb"] is True
    assert str(tmp_path / "kb") not in kw["deny_paths"]


def test_neutral_session_has_no_map_no_kb_dirs_and_denies_the_kb(tmp_path):
    p, h, chat, clock, made = _participant(tmp_path, profile="neutral", script=[SILENT])

    async def main():
        await p.start()
        _line(h, 5, "всем привет, мы в эфире")
        clock.t = 40
        await p.tick()
        await p.shutdown()

    run(main())
    kw = made[0].kwargs
    assert kw["add_dirs"] == [str(p._folder)]                    # ни базы, ни библиотеки
    assert str(tmp_path / "kb") in kw["deny_paths"]              # база — ещё и запрет CLI
    assert str(tmp_path / "lib") not in kw["deny_paths"]         # библиотека держит папку записи
    assert "План запуска" not in kw["system_prompt"] and _work_hits(kw["system_prompt"]) == []
    sent = made[0].sent[0][0]
    assert sent.startswith(pp.SEED_NEW_NEUTRAL) and "План запуска" not in sent
    assert pp.H_TRANSCRIPT_NEUTRAL in sent
    view = p.view()
    assert view["profile"] == "neutral" and view["sees"]["kb"] is False
    assert view["sees"]["kb_docs"] is False
    assert chat.profile() == "neutral"                           # журнал встречи помнит профиль
    assert p._profile_blocked_roots() == [str(tmp_path / "kb"), str(tmp_path / "lib")]


def test_neutral_runner_gets_only_the_meeting_folder(tmp_path):
    runner = FakeRunner([SILENT])
    p, h, _chat, clock, _made = _participant(tmp_path, provider="codex", profile="neutral",
                                               runner=runner)

    async def main():
        await p.start()
        _line(h, 5, "привет")
        clock.t = 40
        await p.tick()
        await p.shutdown()

    run(main())
    (_prompt, kw), = runner.calls
    assert list(kw["allowed_dirs"]) == [str(p._folder)]
    assert "План запуска" not in kw["system_prompt"]


def test_neutral_disables_the_kb_fallback_for_a_local_model(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(KnowledgeBase, "kb_read", lambda self, *a, **k: calls.append("read"))
    monkeypatch.setattr(KnowledgeBase, "kb_search", lambda self, *a, **k: calls.append("search"))
    monkeypatch.setattr(KnowledgeBase, "kb_list", lambda self, *a, **k: calls.append("list"))
    from meet.llm.base import AgentReply

    runner = FakeRunner([AgentReply(text='{"read": ["Проекты/План запуска.md"]}'),
                         say("По записи: запуск обсуждали в [00:05].")])
    p, h, chat, clock, _made = _participant(tmp_path, provider="openai-compatible",
                                              profile="neutral", runner=runner)

    async def main():
        await p.start()
        await p.post_user_message("что там с запуском?")
        await p.tick()
        await p.tick()
        await p.shutdown()

    run(main())
    assert calls == []                                    # база не тронута
    results = [m for m in chat.messages() if m.get("kind") == "tool" and m.get("event") == "result"]
    assert results and results[0]["error"] == NEUTRAL_NO_KB
    first, second = runner.calls[0][0], runner.calls[1][0]
    assert '{"read"' not in runner.calls[0][1]["system_prompt"]   # протокола запросов нет
    assert NEUTRAL_NO_KB in second and "Запуск 14.11" not in first + second


def test_switching_profile_mid_session_sends_the_note_and_restarts_with_resume(tmp_path):
    p, h, chat, clock, made = _participant(tmp_path, script=[SILENT, SILENT, SILENT])
    events = h.events

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        assert p.set_profile("neutral") == "neutral"
        assert p.set_profile("Нейтральный") == "neutral"      # то же — без новой пометки
        await asyncio.sleep(0.05)                              # запись профиля в журнал
        _line(h, 50, "продолжаем")
        clock.t = 90
        await p.tick()
        _line(h, 95, "ещё")
        clock.t = 130
        await p.tick()
        await p.shutdown()

    run(main())
    assert len(made) == 2 and made[0].closed
    first, second = made
    # Новый процесс — тот же сеанс провайдера, другие папки и промпт.
    assert second.kwargs["resume"] == first.session_id
    assert second.kwargs["add_dirs"] == [str(p._folder)]
    assert "План запуска" not in second.kwargs["system_prompt"]
    note = pp.profile_note("neutral")
    turn2, turn3 = second.sent[0][0], second.sent[1][0]
    assert note in turn2 and pp.H_TRANSCRIPT_NEUTRAL in turn2
    assert "Профиль сменён" not in turn3                      # пометка — один раз
    assert chat.profile() == "neutral"
    assert any(name == "agent" and data["profile"] == "neutral" for name, data in events)


def test_switch_back_to_work_brings_the_map_and_kb_dirs(tmp_path):
    p, h, chat, clock, made = _participant(tmp_path, profile="neutral", script=[SILENT, SILENT])

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        p.set_profile("work")
        _line(h, 50, "продолжаем")
        clock.t = 90
        await p.tick()
        await p.shutdown()

    run(main())
    second = made[1]
    assert str(tmp_path / "kb") in second.kwargs["add_dirs"]
    turn = second.sent[0][0]
    assert "Профиль сменён на «Рабочая встреча»" in turn and "План запуска" in turn
    assert chat.profile() == "work"


def test_profile_note_survives_an_empty_turn_and_a_stop(tmp_path):
    p, h, _chat, clock, made = _participant(tmp_path, script=[SILENT, SILENT])

    async def main():
        await p.start()
        p.set_profile("neutral")
        assert not await p.tick()          # одна смена профиля хода не вызывает
        _line(h, 5, "привет")
        clock.t = 40
        await p.tick()
        await p.shutdown()

    run(main())
    assert pp.profile_note("neutral") in made[0].sent[0][0]


# --- журнал и сборка по настройкам ------------------------------------------------------


def test_chatlog_profile_round_trip_keeps_sessions(tmp_path):
    chat = ChatLog(tmp_path / "rec", log=lambda _m: None)
    assert chat.profile() is None
    chat.set_session_id("claude-code", "s-1", model="opus")
    chat.set_profile("neutral")
    assert chat.profile() == "neutral" and chat.session_id("claude-code") == "s-1"
    chat.set_session_id("claude-code", "s-2")
    assert chat.profile() == "neutral"                    # сеанс провайдера профиль не стирает
    chat.set_profile("work")
    assert chat.profile() == "work"
    with pytest.raises(ValueError):
        chat.set_profile("stream")
    raw = json.loads(chat.sessions_path.read_text(encoding="utf-8"))
    assert raw["profile"] == "work" and raw["heads"]["agent"]["claude-code"]["id"] == "s-2"
    chat.sessions_path.write_text(json.dumps({"heads": {}, "profile": "кино"}), encoding="utf-8")
    assert chat.profile() is None                          # мусор — как нет


def test_session_profile_precedence(tmp_path):
    chat = ChatLog(tmp_path / "rec", log=lambda _m: None)
    assert session_profile(chat, "neutral") == "neutral"           # настройка
    chat.set_profile("work")
    assert session_profile(chat, "neutral") == "work"              # журнал встречи
    assert session_profile(chat, "work", "neutral") == "neutral"   # выбор при старте
    assert session_profile(None, "bogus") == "work"


def test_from_settings_continues_in_the_stored_profile(tmp_path):
    folder = tmp_path / "rec" / "2026-10-07_10-00"
    folder.mkdir(parents=True)
    ChatLog(folder).set_profile("neutral")
    cfg = Settings.from_raw({"assist": {"profile": "work"}})
    p = from_settings(cfg, TranscriptBus(), folder, "codex", FakeRunner([]))
    assert p.profile == "neutral"                          # «Продолжить разговор» — тот же
    p = from_settings(cfg, TranscriptBus(), folder, "codex", FakeRunner([]), profile="work")
    assert p.profile == "work"
    other = tmp_path / "rec" / "2026-10-08_10-00"
    other.mkdir()
    cfg = Settings.from_raw({"assist": {"profile": "neutral"}})
    assert from_settings(cfg, TranscriptBus(), other, "codex", FakeRunner([])).profile == "neutral"


# --- настройка ------------------------------------------------------------------------


def test_settings_profile_default_and_round_trip(tmp_path):
    a = Settings.from_raw({}).assist
    assert a.profile == "work" and a.to_raw()["profile"] == "work"
    b = Settings.from_raw({"assist": {"profile": "neutral"}}).assist
    assert b.profile == "neutral" and b.to_raw()["profile"] == "neutral"
    assert Settings.from_raw({"assist": {"profile": "stream"}}).assist.profile == "work"
    path = tmp_path / "config.json"
    settings.patch({"assist": {"profile": "neutral"}}, path)
    assert settings.load(path).assist.profile == "neutral"
    assert json.loads(path.read_text(encoding="utf-8"))["assist"]["profile"] == "neutral"
    settings.patch({"assist": {"frequency": "less"}}, path)
    assert settings.load(path).assist.profile == "neutral"          # другое поле не сбрасывает


def test_profile_key_mapping():
    for key, label in settings.PROFILE_LABELS.items():
        assert settings.profile_key(key) == key and settings.profile_key(label) == key
        assert pp.PROFILES[key] == label                    # подписи окна и агента совпадают
    assert settings.profile_key(" NEUTRAL ") == "neutral"
    assert settings.profile_key("кино") is None and settings.profile_key(None) is None
    assert settings.ASSIST_PROFILES == tuple(pp.PROFILES)


# --- маршруты: ребёнок и резидент --------------------------------------------------------


def test_child_put_agent_profile(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from meet.assist.web import build_app
    from test_chat_api import ChatState

    async def scenario():
        state = ChatState(tmp_path)
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.put("/agent/profile", json={"profile": "neutral"})
            assert await r.json() == {"profile": "neutral", "label": "Нейтральный", "live": True}
            assert state.participant.profile == "neutral"
            assert state.saved == []                         # настройка не тронута
            r = await client.put("/agent/profile", json={"profile": "Рабочая встреча"})
            assert (await r.json())["profile"] == "work"
            for bad in ({"profile": "stream"}, {}):
                assert (await client.put("/agent/profile", json=bad)).status == 400
            state.participant = None
            assert (await client.put("/agent/profile", json={"profile": "work"})).status == 409

    asyncio.run(scenario())


def test_live_control_argv_carries_the_profile(tmp_path):
    from meet import live_control

    class Server:
        port = 1234

    argv = live_control.LiveControl._argv(tmp_path, {"folder": str(tmp_path), "server": Server(),
                                                     "profile": "neutral"})
    assert argv[argv.index("--profile") + 1] == "neutral"
    plain = live_control.LiveControl._argv(tmp_path, {"folder": str(tmp_path), "server": Server()})
    assert "--profile" not in plain


def test_cli_assist_passes_the_profile(monkeypatch):
    called = {}

    def fake_run_assist(*a, **kw):
        called.update(kw)

    monkeypatch.setattr("meet.assist.app.run_assist", fake_run_assist)
    monkeypatch.setattr("sys.argv", ["meet", "assist", "--profile", "neutral"])
    from meet import cli

    cli.main()
    assert called["profile"] == "neutral"


class _ProfileLive:
    def __init__(self, running=True):
        self.running = running
        self.calls = []

    def agent_profile(self, key):
        from meet import live_control

        if not self.running:
            raise live_control.LiveNotRunning("Ассистент не запущен")
        self.calls.append(key)
        return {"profile": key, "live": True}


def test_resident_live_profile_reaches_the_agent_but_not_the_settings(monkeypatch, tmp_path):
    from meet import tray_control

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    state = tray_control.TrayControl.__new__(tray_control.TrayControl)
    state.live = _ProfileLive()
    assert state.live_profile({"profile": "neutral"}) == {
        "profile": "neutral", "label": "Нейтральный", "live": True}
    assert state.live.calls == ["neutral"]
    assert settings.load().assist.profile == "work"        # только эта сессия
    with pytest.raises(control.BadRequest):
        state.live_profile({"profile": "кино"})
    with pytest.raises(control.BadRequest):
        state.live_profile({})
    state.live.running = False
    with pytest.raises(control.Conflict):
        state.live_profile({"profile": "work"})


def test_resident_start_validates_the_profile_first(monkeypatch, tmp_path):
    from meet import tray_control

    state = tray_control.TrayControl.__new__(tray_control.TrayControl)
    with pytest.raises(control.BadRequest, match="profile"):
        state.live_start({"profile": "кино"})
    with pytest.raises(control.BadRequest, match="profile"):
        state.live_attach({"profile": "кино"})


def test_control_routes_pass_the_body():
    class Handler:
        def __init__(self, body):
            self.body = body

        def _body(self):
            return self.body

    class State:
        def __init__(self):
            self.calls = []

        def live_start(self, body=None):
            self.calls.append(("start", body))

        def live_attach(self, body=None):
            self.calls.append(("attach", body))

        def live_profile(self, body):
            self.calls.append(("profile", body))

    state = State()
    server = type("S", (), {"state": state})()
    import meet.control as control_mod

    original = control_mod._server_of
    control_mod._server_of = lambda _h: server
    try:
        for method, path, name in (("POST", "/live/start", "start"), ("POST", "/live/attach", "attach"),
                                   ("PUT", "/live/profile", "profile")):
            control_mod._ROUTES[(method, path)](Handler({"profile": "neutral"}), {})
            assert state.calls[-1] == (name, {"profile": "neutral"})
    finally:
        control_mod._server_of = original


def test_attach_from_the_menu_passes_the_profile_to_the_child(resident, monkeypatch, tmp_path):
    _folder, _order = _recording_resident(resident, monkeypatch, tmp_path)
    try:
        reply = resident.live_attach({"profile": "neutral"})
        assert reply["ok"]
        argv = resident.stub.argv
        assert argv[argv.index("--profile") + 1] == "neutral"
    finally:
        resident.tray.stop_recording()
    _wait_for(lambda: not resident.live.busy())


def test_recording_chat_reports_the_session_profile(state, tmp_path):
    folder = tmp_path / "recordings" / RID
    assert state.recording_chat(RID)["profile"] is None
    assert not (folder / "assistant").exists()          # чтение ничего не создаёт
    log = ChatLog(folder)
    log.append("user", text="Привет")
    assert state.recording_chat(RID)["profile"] is None  # встреча до 0.3.7
    log.set_profile("neutral")
    assert state.recording_chat(RID)["profile"] == "neutral"
