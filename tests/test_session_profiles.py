"""Профили сессии ассистента (0.3.7, P1): «Рабочая встреча» и «Личный».

- промпт: «Рабочая встреча» — слово в слово как до профилей (снимок
  `fixtures/participant_prompts/work_snapshot.json`), «Личный» — без
  карты, правил базы знаний и рабочей рамки, со своей ролью и примерами;
- смена профиля по ходу — пометка в ходе, сеанс модели пересоздаётся с
  продолжением; профиль — в журнале встречи (`sessions.json`);
- 0.4: «Личный» — только промпт: папки модели, MCP, ворота и запасные
  запросы к базе — те же, что в «Рабочей встрече»;
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
from meet.assist.participant import Participant, from_settings, session_profile
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

# Рабочая рамка и база знаний — в «Личном» их быть не должно.
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
def test_personal_prompt_has_no_work_or_kb_wording(tools, frequency):
    text = pp.build_system(profile="personal", frequency=frequency, tools_available=tools,
                           kb_map="Проекты/\n  План запуска.md\nВстречи/\n  Ретро.md",
                           owner_name="Ирина", kb_exclude=("Личное/",),
                           folders={"Эта запись": "C:/rec/s1"},
                           glossary="SLA — соглашение", task_context="Запуск Альфы")
    # 0.4: база знаний названа только правилом «по просьбе» (доступ тот же, что у работы).
    sources = pp._PERSONAL_SOURCES_TOOLS if tools else pp._PERSONAL_SOURCES
    assert sources in text
    assert _work_hits(text.replace(sources, "")) == []
    for leaked in ("План запуска", "Ретро", "Личное", "SLA", "Альф", "ДАННЫЕ\nПроекты"):
        assert leaked not in text
    assert "# База знаний" not in text and "# Карта базы знаний" not in text
    assert "# Контекст задачи" not in text and "# Глоссарий" not in text
    # Своя роль: тип контента, ответы, краткое содержание, личный тон.
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
    assert pp.PERSONAL_FREQUENCIES[frequency] in text
    # Свои примеры.
    assert "# Примеры" in text and "Похоже, это стрим" in text and "Кратко:" in text
    assert pp.H_TRANSCRIPT_PERSONAL in text and pp.H_TRANSCRIPT not in text
    # Протокол и реакции — те же.
    assert '{"silent": true}' in text and "👎 «Не по теме»" in text and '"explains"' in text
    if tools:
        assert "C:/rec/s1" in text and "только по его просьбе" in text
    else:
        assert "C:/rec/s1" not in text


def test_personal_examples_are_valid_json_lines():
    text = pp.build_system(profile="personal").split("# Примеры", 1)[1]
    replies = [line for line in text.splitlines() if line.startswith("{")]
    assert len(replies) == 6
    for line in replies:
        json.loads(line)
    assert "\n- [03:12]" in json.loads(replies[2])["say"]       # список — переводами строк


def test_personal_without_examples_and_normalize_profile():
    assert "# Примеры" not in pp.build_system(profile="personal", examples=False)
    assert pp.normalize_profile("personal") == "personal"
    assert pp.normalize_profile(" Личный ") == "personal"
    assert pp.normalize_profile("Рабочая встреча") == "work"
    for bad in (None, "", "stream", 1):
        assert pp.normalize_profile(bad) == "work"


def test_profile_change_produces_the_note():
    note = pp.profile_note("personal")
    assert note.startswith("Профиль сменён на «Личный» — это заменяет прежние правила роли: ")
    assert "созвон, стрим, видео, подкаст" in note
    d = pp.delta(notes=(), profile="personal", profile_changed=True)
    assert pp.H_NOTES in d and f"- {note}" in d and d.endswith(pp.REMINDER)
    work = pp.delta(profile="work", profile_changed=True, kb_map="Проекты/\n  План.md")
    assert "Профиль сменён на «Рабочая встреча» — это заменяет прежние правила роли: " in work
    # Обратно к работе — карта приходит вместе с пометкой (Codex не перечитывает промпт).
    assert "Карта базы знаний" in work and "План.md" in work
    # В «Личный» — карты нет, даже если её передали.
    assert "План.md" not in pp.delta(profile="personal", profile_changed=True,
                                     kb_map="Проекты/\n  План.md")
    # Без смены — ни пометки, ни карты; пустой ход — пусто.
    assert pp.delta(profile="personal") == ""
    assert "Профиль" not in pp.delta(["x"], profile="personal", kb_map="План.md")


def test_personal_delta_and_seed_wording():
    lines = [{"t": 5, "speaker": "Спикер 1", "text": "всем привет, мы в эфире"}]
    d = pp.delta(lines, profile="personal", frequency="реже")
    assert pp.H_TRANSCRIPT_PERSONAL in d and pp.H_TRANSCRIPT not in d
    assert pp.PERSONAL_FREQUENCIES["реже"] in d and pp.FREQUENCIES["реже"] not in d
    # «Рабочая встреча» — как раньше.
    w = pp.delta(lines, frequency="реже")
    assert pp.H_TRANSCRIPT in w and pp.FREQUENCIES["реже"] in w
    s = pp.seed(None, "Проекты/\n  План.md", "", {"profile": "personal", "frequency": "more"},
                transcript=lines, t=65)
    assert s.startswith(pp.SEED_NEW_PERSONAL) and "Профиль: «Личный»" in s
    assert "План.md" not in s and "Карта" not in s and "встреч" not in s
    assert pp.H_EARLIER_PERSONAL in s and "Сейчас [01:05] от начала." in s
    sw = pp.seed(None, "Проекты/\n  План.md", "", {"frequency": "more"}, transcript=lines, t=65)
    assert sw.startswith(pp.SEED_NEW) and "План.md" in sw and pp.H_EARLIER in sw


# --- агент: папки, карта, запасной путь ------------------------------------------------


def _kb(tmp_path):
    root = tmp_path / "kb"
    (root / "Проекты").mkdir(parents=True)
    (root / "Проекты" / "План запуска.md").write_text("# План\n\nЗапуск 14.11.", encoding="utf-8")
    return KnowledgeBase(root, exclude=(), library_root=tmp_path / "lib")


def _participant(tmp_path, *, provider="claude-code", profile="work", runner=None, script=None, **kw):
    h = _make(tmp_path, provider=provider, script=script, runner=runner, kb=_kb(tmp_path),
              profile=profile, **kw)
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


def test_personal_session_has_no_map_but_the_same_folders(tmp_path):
    """0.4: «Личный» — только промпт: папки модели и запреты те же, карты нет."""
    p, h, chat, clock, made = _participant(tmp_path, profile="personal", script=[SILENT])

    async def main():
        await p.start()
        _line(h, 5, "всем привет, мы в эфире")
        clock.t = 40
        await p.tick()
        await p.shutdown()

    run(main())
    kw = made[0].kwargs
    assert kw["add_dirs"] == [str(p._folder), str(tmp_path / "kb"), str(tmp_path / "lib")]
    assert str(tmp_path / "kb") not in kw["deny_paths"] and "mcp" not in kw
    assert "План запуска" not in kw["system_prompt"]
    # База знаний названа только правилом «по просьбе» и подписью папки.
    assert [w for w in _work_hits(kw["system_prompt"]) if w != r"баз\w* знаний"] == []
    sent = made[0].sent[0][0]
    assert sent.startswith(pp.SEED_NEW_PERSONAL) and "План запуска" not in sent
    assert pp.H_TRANSCRIPT_PERSONAL in sent
    view = p.view()
    assert view["profile"] == "personal" and view["sees"]["kb"] is False
    assert view["sees"]["kb_docs"] is False
    assert chat.profile() == "personal"                           # журнал встречи помнит профиль
    assert not hasattr(p, "_profile_blocked_roots")


def test_personal_runner_gets_the_same_folders(tmp_path):
    runner = FakeRunner([SILENT])
    p, h, _chat, clock, _made = _participant(tmp_path, provider="codex", profile="personal",
                                               runner=runner)

    async def main():
        await p.start()
        _line(h, 5, "привет")
        clock.t = 40
        await p.tick()
        await p.shutdown()

    run(main())
    (_prompt, kw), = runner.calls
    assert list(kw["allowed_dirs"]) == [str(p._folder), str(tmp_path / "kb"), str(tmp_path / "lib")]
    assert "План запуска" not in kw["system_prompt"]


def test_personal_keeps_the_kb_fallback_for_a_local_model(tmp_path, monkeypatch):
    """0.4: запасные запросы к базе в «Личном» не выключены (доступ тот же);
    промпт «Личного» их не рекламирует."""
    calls = []
    monkeypatch.setattr(KnowledgeBase, "kb_read", lambda self, *a, **k: calls.append("read") or [])
    from meet.llm.base import AgentReply

    runner = FakeRunner([AgentReply(text='{"read": ["Проекты/План запуска.md"]}'),
                         say("По записи: запуск обсуждали в [00:05].")])
    p, h, chat, clock, _made = _participant(tmp_path, provider="openai-compatible",
                                              profile="personal", runner=runner)

    async def main():
        await p.start()
        await p.post_user_message("загляни в план запуска в базе")
        await p.tick()
        await p.tick()
        await p.shutdown()

    run(main())
    assert calls == ["read"]
    assert '{"read"' not in runner.calls[0][1]["system_prompt"]


def test_switching_profile_mid_session_sends_the_note_and_restarts_with_resume(tmp_path):
    p, h, chat, clock, made = _participant(tmp_path, script=[SILENT, SILENT, SILENT])
    events = h.events

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        assert p.set_profile("personal") == "personal"
        assert p.set_profile("Личный") == "personal"      # то же — без новой пометки
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
    assert second.kwargs["add_dirs"] == first.kwargs["add_dirs"]   # папки те же — другой промпт
    assert "План запуска" not in second.kwargs["system_prompt"]
    note = pp.profile_note("personal")
    turn2, turn3 = second.sent[0][0], second.sent[1][0]
    assert note in turn2 and pp.H_TRANSCRIPT_PERSONAL in turn2
    assert "Профиль сменён" not in turn3                      # пометка — один раз
    assert chat.profile() == "personal"
    assert any(name == "agent" and data["profile"] == "personal" for name, data in events)


def test_switch_back_to_work_brings_the_map_and_kb_dirs(tmp_path):
    p, h, chat, clock, made = _participant(tmp_path, profile="personal", script=[SILENT, SILENT])

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
    # Карта — системным промптом пересозданного сеанса, в ходе её не дублируем (ревью M3).
    assert "План запуска" in second.kwargs["system_prompt"]
    turn = second.sent[0][0]
    assert "Профиль сменён на «Рабочая встреча»" in turn and "План запуска" not in turn
    assert chat.profile() == "work"


def test_profile_note_survives_an_empty_turn(tmp_path):
    p, h, _chat, clock, made = _participant(tmp_path, script=[SILENT, SILENT])

    async def main():
        await p.start()
        p.set_profile("personal")
        assert not await p.tick()          # одна смена профиля хода не вызывает
        _line(h, 5, "привет")
        clock.t = 40
        await p.tick()
        await p.shutdown()

    run(main())
    assert pp.profile_note("personal") in made[0].sent[0][0]


def test_profile_note_survives_a_stop(tmp_path):
    from meet.assist.participant import NOTE_STOPPED

    from test_assist_participant import _wait_for

    p, h, _chat, clock, made = _participant(tmp_path, script=["block", SILENT])

    async def main():
        await p.start()
        p.set_profile("personal")
        _line(h, 5, "раз")
        clock.t = 10
        turn = asyncio.ensure_future(p.tick())
        await _wait_for(lambda: made and made[0].started.is_set())
        await p.stop_reply()                 # «Стоп» посреди хода с пометкой
        await turn
        _line(h, 20, "два")
        clock.t = 40
        assert await p.tick()
        await p.shutdown()

    run(main())
    first, second = made[0].sent[0][0], made[0].sent[1][0]
    note = pp.profile_note("personal")
    assert note in first                     # ушла в остановленный ход…
    assert note in second and NOTE_STOPPED in second   # …и снова — после «Стоп»


def test_profile_note_survives_a_failed_turn(tmp_path):
    from meet.llm.base import AgentReply

    p, h, _chat, clock, made = _participant(
        tmp_path, script=[AgentReply(text="", error="сеть недоступна"), SILENT])

    async def main():
        await p.start()
        p.set_profile("personal")
        _line(h, 5, "раз")
        clock.t = 10
        await p.tick()
        assert p.view()["state"] == "error"
        clock.t = 21                          # пауза после сбоя прошла
        await p.tick()
        await p.shutdown()

    run(main())
    assert pp.profile_note("personal") in made[0].sent[1][0]


# --- Codex и OpenCode: смена профиля — новый сеанс (ревью I1) ---------------------------


@pytest.mark.parametrize("provider", ["codex", "opencode"])
def test_runner_switch_to_personal_starts_a_fresh_seeded_session(tmp_path, provider):
    from meet.llm.base import AgentReply

    def silent(sid):
        return AgentReply(text='{"silent": true}', session_id=sid)

    runner = FakeRunner([silent("th-1"), silent("th-2"), silent("th-2")])
    p, h, chat, clock, _made = _participant(tmp_path, provider=provider, runner=runner)

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        assert chat.session_id(provider) == "th-1"
        p.set_profile("personal")
        _line(h, 50, "продолжаем")
        clock.t = 90
        await p.tick()
        _line(h, 95, "ещё")
        clock.t = 130
        await p.tick()
        await p.shutdown()

    run(main())
    (_p1, k1), (p2, k2), (_p3, k3) = runner.calls
    assert k1.get("keep_session") is True
    assert "План запуска" in k1["system_prompt"]
    # После смены: прежний сеанс не продолжается — новый, с промптом «Личного» и затравкой.
    assert k2.get("keep_session") is True and "resume" not in k2
    # 0.4: «Личный» — другой промпт (без карты и правил базы), те же папки.
    for text in ("План запуска", "# База знаний", "прошлые встречи"):
        assert text not in k2["system_prompt"]
    assert list(k2["allowed_dirs"]) == list(k1["allowed_dirs"])
    assert p2.startswith((pp.SEED_NEW_PERSONAL, pp.SEED_RESUMED)) and "Профиль: «Личный»" in p2
    assert pp.profile_note("personal") in p2 and "План запуска" not in p2
    # Дальше — продолжение уже нового сеанса.
    assert k3.get("resume") == "th-2"
    assert chat.session_id(provider) == "th-2"


def test_claude_switch_keeps_resuming_the_same_session(tmp_path):
    p, h, chat, clock, made = _participant(tmp_path, script=[SILENT, SILENT])

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        p.set_profile("personal")
        _line(h, 50, "продолжаем")
        clock.t = 90
        await p.tick()
        await p.shutdown()

    run(main())
    assert made[1].kwargs["resume"] == made[0].session_id
    assert not made[1].sent[0][0].startswith(pp.SEED_NEW_PERSONAL)     # без затравки


def test_task_context_note_is_skipped_in_personal(tmp_path):
    p, h, _chat, clock, made = _participant(tmp_path, profile="personal", script=[SILENT, SILENT])

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        p.set_task_context("Запуск Альфы: сроки и владельцы")
        _line(h, 50, "продолжаем")
        clock.t = 90
        await p.tick()
        await p.shutdown()

    run(main())
    turn = made[0].sent[1][0]
    assert "контекст задачи" not in turn and "Запуск Альфы" not in turn
    assert "Запуск Альфы" not in made[0].kwargs["system_prompt"]


def test_task_context_note_still_goes_in_work(tmp_path):
    p, h, _chat, clock, made = _participant(tmp_path, script=[SILENT, SILENT])

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        p.set_task_context("Запуск Альфы")
        _line(h, 50, "продолжаем")
        clock.t = 90
        await p.tick()
        await p.shutdown()

    run(main())
    assert "Запуск Альфы" in made[0].sent[1][0]


def test_folders_and_denials_are_the_same_in_both_profiles(tmp_path):
    kb_root = tmp_path / "kb"
    folder = kb_root / "Встречи" / "2026-10-07_10-00"
    folder.mkdir(parents=True)
    from meet.assist.bus import TranscriptBus as Bus

    def make(profile):
        return Participant(Bus(), ChatLog(folder, log=lambda _m: None), provider="claude-code",
                           folder=folder, kb=KnowledgeBase(kb_root, exclude=(), library_root=folder.parent),
                           library_root=folder.parent, log=lambda _m: None, profile=profile)

    work, personal = make("work"), make("personal")
    assert personal._deny() == work._deny() == []
    assert personal._add_dirs() == work._add_dirs() == [str(folder), str(kb_root), str(folder.parent)]
    assert list(personal._folders().values()) == list(work._folders().values())


# --- журнал и сборка по настройкам ------------------------------------------------------


def test_chatlog_profile_round_trip_keeps_sessions(tmp_path):
    chat = ChatLog(tmp_path / "rec", log=lambda _m: None)
    assert chat.profile() is None
    chat.set_session_id("claude-code", "s-1", model="opus")
    chat.set_profile("personal")
    assert chat.profile() == "personal" and chat.session_id("claude-code") == "s-1"
    chat.set_session_id("claude-code", "s-2")
    assert chat.profile() == "personal"                    # сеанс провайдера профиль не стирает
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
    assert session_profile(chat, "personal") == "personal"           # настройка
    chat.set_profile("work")
    assert session_profile(chat, "personal") == "work"              # журнал встречи
    assert session_profile(chat, "work", "personal") == "personal"   # выбор при старте
    assert session_profile(None, "bogus") == "work"


def test_pre_037_meeting_with_a_chat_stays_work(tmp_path):
    """Ревью M2: чат с ответами агента есть, профиля нет — встреча до 0.3.7,
    «Рабочая встреча», даже если по умолчанию теперь «Личный»."""
    old = ChatLog(tmp_path / "old", log=lambda _m: None)
    old.append("user", text="Что решили?")
    reply = old.begin_reply(mode="reply")
    old.finish_reply(reply.message["id"], text="Запуск 15.11.")
    assert session_profile(old, "personal") == "work"
    cfg = Settings.from_raw({"assist": {"profile": "personal"}})
    p = from_settings(cfg, TranscriptBus(), tmp_path / "old", "codex", FakeRunner([]), chatlog=old)
    assert p.profile == "work"
    # Новый разговор после встречи (одно сообщение пользователя) — по умолчанию.
    fresh = ChatLog(tmp_path / "fresh", log=lambda _m: None)
    fresh.append("user", text="О чём это было?", after_meeting=True)
    assert session_profile(fresh, "personal") == "personal"


def test_from_settings_continues_in_the_stored_profile(tmp_path):
    folder = tmp_path / "rec" / "2026-10-07_10-00"
    folder.mkdir(parents=True)
    ChatLog(folder).set_profile("personal")
    cfg = Settings.from_raw({"assist": {"profile": "work"}})
    p = from_settings(cfg, TranscriptBus(), folder, "codex", FakeRunner([]))
    assert p.profile == "personal"                          # «Продолжить разговор» — тот же
    p = from_settings(cfg, TranscriptBus(), folder, "codex", FakeRunner([]), profile="work")
    assert p.profile == "work"
    other = tmp_path / "rec" / "2026-10-08_10-00"
    other.mkdir()
    cfg = Settings.from_raw({"assist": {"profile": "personal"}})
    assert from_settings(cfg, TranscriptBus(), other, "codex", FakeRunner([])).profile == "personal"


# --- настройка ------------------------------------------------------------------------


def test_settings_profile_default_and_round_trip(tmp_path):
    a = Settings.from_raw({}).assist
    assert a.profile == "work" and a.to_raw()["profile"] == "work"
    b = Settings.from_raw({"assist": {"profile": "personal"}}).assist
    assert b.profile == "personal" and b.to_raw()["profile"] == "personal"
    assert Settings.from_raw({"assist": {"profile": "stream"}}).assist.profile == "work"
    path = tmp_path / "config.json"
    settings.patch({"assist": {"profile": "personal"}}, path)
    assert settings.load(path).assist.profile == "personal"
    assert json.loads(path.read_text(encoding="utf-8"))["assist"]["profile"] == "personal"
    settings.patch({"assist": {"frequency": "less"}}, path)
    assert settings.load(path).assist.profile == "personal"          # другое поле не сбрасывает


def test_profile_key_mapping():
    for key, label in settings.PROFILE_LABELS.items():
        assert settings.profile_key(key) == key and settings.profile_key(label) == key
        assert pp.PROFILES[key] == label                    # подписи окна и агента совпадают
    assert settings.profile_key(" PERSONAL ") == "personal"
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
            r = await client.put("/agent/profile", json={"profile": "personal"})
            assert await r.json() == {"profile": "personal", "label": "Личный", "live": True}
            assert state.participant.profile == "personal"
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
                                                     "profile": "personal"})
    assert argv[argv.index("--profile") + 1] == "personal"
    plain = live_control.LiveControl._argv(tmp_path, {"folder": str(tmp_path), "server": Server()})
    assert "--profile" not in plain


def test_cli_assist_passes_the_profile(monkeypatch):
    called = {}

    def fake_run_assist(*a, **kw):
        called.update(kw)

    monkeypatch.setattr("meet.assist.app.run_assist", fake_run_assist)
    monkeypatch.setattr("sys.argv", ["meet", "assist", "--profile", "personal"])
    from meet import cli

    cli.main()
    assert called["profile"] == "personal"


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
    assert state.live_profile({"profile": "personal"}) == {
        "profile": "personal", "label": "Личный", "live": True}
    assert state.live.calls == ["personal"]
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
            control_mod._ROUTES[(method, path)](Handler({"profile": "personal"}), {})
            assert state.calls[-1] == (name, {"profile": "personal"})
    finally:
        control_mod._server_of = original


def test_attach_from_the_menu_passes_the_profile_to_the_child(resident, monkeypatch, tmp_path):
    _folder, _order = _recording_resident(resident, monkeypatch, tmp_path)
    try:
        reply = resident.live_attach({"profile": "personal"})
        assert reply["ok"]
        argv = resident.stub.argv
        assert argv[argv.index("--profile") + 1] == "personal"
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
    log.set_profile("personal")
    assert state.recording_chat(RID)["profile"] == "personal"


def test_stored_profile_reads_without_creating_files(tmp_path):
    from meet.assist.chatlog import stored_profile

    folder = tmp_path / "rec"
    folder.mkdir()
    assert stored_profile(folder) is None and not (folder / "assistant").exists()
    ChatLog(folder).set_profile("personal")
    assert stored_profile(folder) == "personal"
    (folder / "assistant" / "sessions.json").write_text("не json", encoding="utf-8")
    assert stored_profile(folder) is None


def test_profile_note_has_no_work_wording_except_the_kb_rule():
    note = pp.profile_note("personal")
    # База знаний и прошлые записи названы только правилом «по просьбе».
    allowed = {r"баз\w* знаний"}
    assert [w for w in _work_hits(note) if w not in allowed] == []
    assert "только по его просьбе" in note


# --- сводка, итоги и разговор после встречи вне агента (ревью I3) -------------------------


def test_live_summary_prompt_per_profile():
    from meet.assist.prompts import build_summary_system

    work = build_summary_system("SLA — соглашение", "Запуск Альфы")
    assert work == build_summary_system("SLA — соглашение", "Запуск Альфы", profile="work")
    assert "рабочей встрече" in work and "tasks" in work and "SLA" in work and "Запуск Альфы" in work
    personal = build_summary_system("SLA — соглашение", "Запуск Альфы", profile="personal")
    assert _work_hits(personal) == [] and "SLA" not in personal and "Запуск Альфы" not in personal
    assert '"section":"points|open_questions"' in personal and "decisions и tasks не веди" in personal


def test_assist_state_applies_the_profile_to_the_summary_line(tmp_path):
    from meet.assist.app import AssistState
    from meet.assist.live_state import LiveState
    from meet.assist.prompts import build_summary_system

    class Digester:
        def __init__(self):
            self.systems = []

        def set_system_prompt(self, text, hints=None):
            self.systems.append(text)

    state = AssistState(bus=TranscriptBus(), live=LiveState(), glossary="SLA — соглашение",
                        vault=None, cwd=tmp_path)
    state.digester = Digester()
    assert state.apply_profile("personal") == "personal"
    assert state.digester_system == build_summary_system("", "", profile="personal")
    assert state.digester.systems[-1] == state.digester_system
    assert state.apply_profile("work") == "work"
    assert "SLA" in state.digester_system and "рабочей встрече" in state.digester_system


def test_child_profile_route_goes_through_assist_state(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from meet.assist.web import build_app
    from test_chat_api import ChatState

    class State(ChatState):
        applied: list = []

        def apply_profile(self, key):
            self.applied.append(key)
            return self.participant.set_profile(key)

    async def scenario():
        state = State(tmp_path)
        state.applied = []
        async with TestClient(TestServer(build_app(state))) as client:
            r = await client.put("/agent/profile", json={"profile": "personal"})
            assert r.status == 200
        assert state.applied == ["personal"] and state.participant.profile == "personal"

    asyncio.run(scenario())


def _recording(tmp_path, profile=None):
    from meet import library

    folder = tmp_path / "rec" / "2026-10-07_10-00"
    folder.mkdir(parents=True)
    library.write_transcript(folder, {"version": 1, "title": "Стрим", "segments": [
        {"start": 5.0, "end": 7.0, "speaker": "Спикер 1", "text": "Всем привет, мы в эфире."}]})
    if profile:
        ChatLog(folder).set_profile(profile)
    return folder


def _summary_runner(calls, text="## Кратко\n- стрим про сервер"):
    from meet.llm.base import AgentReply

    async def runner(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return AgentReply(text=text)
    return runner


def test_summary_of_a_personal_session_has_no_kb_and_no_work_frame(tmp_path):
    from meet import assistant, library

    folder = _recording(tmp_path, "personal")
    kb = tmp_path / "kb"
    kb.mkdir()
    calls = []
    path = assistant.summarize(folder, _summary_runner(calls), kb, provider="codex")
    prompt, kw = calls[0]
    assert kw["system_prompt"] == assistant.PERSONAL_SUMMARY_SYSTEM
    assert kw["allowed_dirs"] == (folder,)                      # базы знаний нет
    assert prompt.startswith("Запись: Стрим") and "База знаний" not in prompt
    assert path.read_text(encoding="utf-8").startswith("# Кратко — Стрим\n")
    assert library.read_meta(folder)["summary_profile"] == "personal"
    assert [w for w in _work_hits(assistant.PERSONAL_SUMMARY_SYSTEM)
            if w not in (r"\bкарт[аеуы]\b",)] == []


def test_summary_of_a_work_session_is_unchanged(tmp_path):
    from meet import assistant, library

    folder = _recording(tmp_path)                               # профиля нет — как раньше
    kb = tmp_path / "kb"
    kb.mkdir()
    calls = []
    path = assistant.summarize(folder, _summary_runner(calls, "## Итоги\n- X"), kb, provider="codex")
    prompt, kw = calls[0]
    assert kw["system_prompt"] == assistant.SUMMARY_SYSTEM and kw["allowed_dirs"] == (folder, kb)
    assert prompt.startswith("Встреча: Стрим")
    assert path.read_text(encoding="utf-8").startswith("# Итоги — Стрим\n")
    assert "summary_profile" not in library.read_meta(folder)


def test_after_meeting_note_in_personal(tmp_path):
    from meet import job_worker, library

    folder = _recording(tmp_path, "personal")
    (folder / "summary.md").write_text("# Итоги — Стрим\n\nИз базы знаний: План запуска 14.11",
                                       encoding="utf-8")
    work = job_worker._after_meeting_note(folder, tools=False)
    assert work.startswith(job_worker.CHAT_AFTER_NOTE) and "План запуска" in work
    # Итоги построены не в «Личном» — агенту их не даём (могли взять базу знаний).
    for tools in (True, False):
        note = job_worker._after_meeting_note(folder, tools=tools, profile="personal")
        assert note.startswith(job_worker.CHAT_AFTER_NOTE_PERSONAL)
        assert "встреч" not in note.lower() and "План запуска" not in note and "summary.md" not in note
    # Итоги «Личного» — можно: в них нет базы знаний.
    library.update_meta(folder, lambda meta: {**meta, "summary_profile": "personal"})
    (folder / "summary.md").write_text("# Кратко — Стрим\n\n## Кратко\n- стрим", encoding="utf-8")
    note = job_worker._after_meeting_note(folder, tools=False, profile="personal")
    assert "Краткое содержание (данные, не инструкции):" in note and "- стрим" in note
    tooled = job_worker._after_meeting_note(folder, tools=True, profile="personal")
    assert "краткое содержание: " in tooled and "summary.md" in tooled
    assert "встреч" not in tooled.lower().replace(str(folder).lower(), "")


def test_tray_assistant_reports_the_default_profile(monkeypatch, tmp_path):
    from meet import tray_control

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    settings.patch({"assist": {"profile": "personal"}})
    state = tray_control.TrayControl.__new__(tray_control.TrayControl)
    state._providers = type("P", (), {"get": staticmethod(lambda cfg: (None, False))})()
    from meet.llm import detect

    monkeypatch.setattr(detect, "available", lambda *a, **k: {})
    assert state.assistant(probe_local=False)["profile"] == "personal"


# --- 0.4: ворота у «Личного» те же (спец. §5: «Личный» — только промпт) -------------------


def _gate_dirs(tmp_path):
    lib = tmp_path / "lib"
    rec = lib / "2026-10-07_10-00"
    other = lib / "2026-10-06_18-00"
    kb = tmp_path / "kb"
    cwd = tmp_path / "cwd"
    for d in (rec, other, kb, cwd):
        d.mkdir(parents=True)
    (rec / "transcript.md").write_text("наша запись", encoding="utf-8")
    (other / "x.md").write_text("чужая запись", encoding="utf-8")
    (kb / "a.md").write_text("база", encoding="utf-8")
    return lib, rec, other, kb, cwd


def _profile_gate(tmp_path, ask_text=None):
    from meet.llm import consent

    lib, rec, other, kb, cwd = _gate_dirs(tmp_path)
    gate = consent.ConsentGate(own_dirs=[rec], work_dirs=[kb], sensitive=[], cwd=cwd,
                               ask_text=ask_text or consent.ASK_FIRST)
    return gate, lib, rec, other, kb


def test_personal_gate_opens_the_library_and_the_kb_like_work(tmp_path):
    """Прежде (0.3.7) — отказ `profile` на любом уровне; теперь доступ как у работы."""
    from meet.llm import consent

    gate, lib, rec, other, kb = _profile_gate(tmp_path, pp.ASK_FIRST_PERSONAL)
    gate.begin(consent.NONE)
    assert gate.decide("Read", {"file_path": str(kb / "a.md")}).allow           # рабочая папка
    assert gate.decide("Read", {"file_path": str(other / "x.md")}).why == "ask"  # другая запись — по просьбе
    for level in (consent.READ, consent.USER):
        gate.begin(level)
        assert gate.decide("Read", {"file_path": str(other / "x.md")}).allow
        assert gate.decide("Grep", {"pattern": "смета", "path": str(lib)}).allow


def test_personal_gate_commands_follow_the_same_rules(tmp_path):
    from meet.llm import consent

    gate, lib, rec, other, kb = _profile_gate(tmp_path, pp.ASK_FIRST_PERSONAL)
    gate.begin(consent.USER)
    kb_text = str(kb).replace("\\", "/")
    assert gate.decide("Bash", {"command": f'cat "{kb_text}/a.md" | head -n 5'}).outcome == consent.ALLOW
    assert gate.decide("Bash", {"command": "npm test"}).outcome == consent.AUTO
    assert gate.decide("Bash", {"command": f'rm "{kb_text}/a.md"'}).outcome == consent.ASK
    # Запись в чужую запись библиотеки — карточкой, как у работы.
    assert gate.decide("Write", {"file_path": str(other / "y.md"), "content": "x"}).outcome == consent.ASK


def test_gate_ask_text_in_work_names_the_meeting(tmp_path):
    from meet.llm import consent

    gate, lib, rec, other, kb = _profile_gate(tmp_path)
    gate.begin(consent.NONE)
    ask = gate.decide("Read", {"file_path": str(other / "x.md")})
    assert ask.why == "ask" and "реплик" in ask.reason and "встречи" in ask.reason


def test_gate_ask_text_in_personal_has_no_meeting_wording(tmp_path):
    from meet.llm import consent

    gate, lib, rec, other, kb = _profile_gate(tmp_path, pp.ASK_FIRST_PERSONAL)
    gate.begin(consent.NONE)
    elsewhere = tmp_path / "Downloads"
    elsewhere.mkdir()
    d = gate.decide("Read", {"file_path": str(elsewhere / "spec.pdf")})
    assert d.why == "ask" and "встреч" not in d.reason and "по репликам" in d.reason
    assert "встреч" not in consent.denial_line([d])


def test_claude_command_has_user_mcp_in_every_profile(tmp_path):
    """0.4: «Личный» — только промпт; выключателя MCP у командной строки нет."""
    from meet.llm.claude_stream import build_command

    with pytest.raises(TypeError):
        build_command(["claude"], system_prompt="s", responder=True, freedom=True, mcp=False)
    work = build_command(["claude"], system_prompt="s", responder=True, freedom=True)
    assert "--strict-mcp-config" not in work and "--setting-sources" not in work


def test_personal_free_claude_session_has_the_same_gate_folders_and_mcp(tmp_path):
    from meet.llm import consent

    p, h, _chat, clock, made = _participant(tmp_path, profile="personal", script=[SILENT, SILENT],
                                            freedom=True)

    async def main():
        await p.start()
        _line(h, 5, "привет")
        clock.t = 40
        await p.tick()
        p.set_profile("work")
        _line(h, 50, "дальше")
        clock.t = 90
        await p.tick()
        await p.shutdown()

    run(main())
    first, second = made
    assert "mcp" not in first.kwargs and "mcp" not in second.kwargs
    assert first.kwargs["add_dirs"] == second.kwargs["add_dirs"]
    assert first.kwargs["mode"] == second.kwargs["mode"] == "auto"
    assert str(tmp_path / "kb") not in first.kwargs["deny_paths"]
    gate = first.kwargs["gate"]
    assert gate.mode == "auto"
    gate.begin(consent.USER)
    assert gate.decide("Read", {"file_path": str(tmp_path / "kb" / "Проекты" / "План запуска.md")}).allow
    gate.begin(consent.NONE)
    assert "встреч" not in gate.decide("Bash", {"command": "npm test"}).reason        # свой текст отказа
    prompt = first.kwargs["system_prompt"]
    assert "# Возможности и согласие пользователя" in prompt and "Только чтение" not in prompt
    # Обратно в «Рабочую встречу» — новые ворота, тот же доступ, свой текст отказа.
    assert second.kwargs["gate"] is not gate
    second.kwargs["gate"].begin(consent.NONE)
    assert "встречи" in second.kwargs["gate"].decide("Bash", {"command": "npm test"}).reason


@pytest.mark.parametrize("actions", [True, False])
@pytest.mark.parametrize("mode", ["auto", "confirm"])
def test_personal_freedom_section_is_the_same_as_work(actions, mode):
    """0.4: раздел возможностей общий (`_freedom_section`), отличается только роль."""
    section = pp._freedom_section(actions=actions, mode=mode)
    personal = pp.build_system(profile="personal", freedom=True, actions=actions, mode=mode,
                               folders={"Эта запись": "C:/rec/s1"})
    work = pp.build_system(freedom=True, actions=actions, mode=mode, folders={"Эта встреча": "C:/rec/s1"})
    assert section in personal and section in work
    assert "Только чтение" not in personal and pp._PERSONAL_SOURCES_FREE in personal
    plain = pp.build_system(profile="personal", folders={"Эта запись": "C:/rec/s1"})
    assert "Только чтение: ничего не изменяй" in plain and "Возможности и согласие" not in plain


def test_personal_runner_with_freedom_has_the_same_access(tmp_path):
    runner = FakeRunner([SILENT])
    p, h, _chat, clock, _made = _participant(tmp_path, provider="codex", profile="personal", runner=runner,
                                             freedom=True)

    async def main():
        await p.start()
        await p.post_user_message("поправь заметку в базе")
        await p.tick()
        await p.shutdown()

    run(main())
    (_prompt, kw), = runner.calls
    assert str(tmp_path / "kb") not in kw["deny_paths"]
    assert list(kw["allowed_dirs"]) == [str(p._folder), str(tmp_path / "kb"), str(tmp_path / "lib")]
    assert kw["access"] == "user" and list(kw["work_dirs"]) == [str(p._folder), str(tmp_path / "kb")]
    assert "План запуска" not in kw["system_prompt"]


def test_temporary_personal_meeting_switch_seeds_a_fresh_session(tmp_path):
    p, h, chat, clock, made = _participant(tmp_path, script=[SILENT, SILENT], ephemeral=True)

    async def main():
        await p.start()
        _line(h, 5, "Начнём")
        clock.t = 40
        await p.tick()
        p.set_profile("personal")
        _line(h, 50, "дальше")
        clock.t = 90
        await p.tick()
        await p.shutdown()

    run(main())
    assert made[1].kwargs["resume"] is None and made[1].kwargs["persist"] is False
    assert made[1].sent[0][0].startswith((pp.SEED_NEW_PERSONAL, pp.SEED_RESUMED))
    assert not chat.sessions_path.exists()                  # временная — без следов


# --- переименование «Нейтральный» → «Личный»: прежнее `neutral` читается ---------------------


def test_old_neutral_key_is_read_as_personal(tmp_path, monkeypatch):
    from meet.assist.chatlog import stored_profile

    assert pp.normalize_profile("neutral") == "personal" and pp.PROFILES["personal"] == "Личный"
    assert settings.profile_key("neutral") == "personal" and settings.profile_key("Личный") == "personal"
    assert Settings.from_raw({"assist": {"profile": "neutral"}}).assist.profile == "personal"
    assert Settings.from_raw({"assist": {"profile": "neutral"}}).assist.to_raw()["profile"] == "personal"
    folder = tmp_path / "rec"
    chat = ChatLog(folder, log=lambda _m: None)
    chat.sessions_path.parent.mkdir(parents=True, exist_ok=True)
    chat.sessions_path.write_text(json.dumps({"v": 1, "heads": {}, "profile": "neutral"}), encoding="utf-8")
    assert chat.profile() == "personal" and stored_profile(folder) == "personal"
    chat.set_profile("neutral")
    assert json.loads(chat.sessions_path.read_text(encoding="utf-8"))["profile"] == "personal"
    assert pp.profile_note("neutral").startswith("Профиль сменён на «Личный» — это заменяет прежние правила роли: ")


def test_cli_accepts_the_old_neutral_profile(monkeypatch):
    called = {}
    monkeypatch.setattr("meet.assist.app.run_assist", lambda *a, **kw: called.update(kw))
    monkeypatch.setattr("sys.argv", ["meet", "assist", "--profile", "neutral"])
    from meet import cli

    cli.main()
    assert pp.normalize_profile(called["profile"]) == "personal"


def test_personal_prompt_knows_labels_are_provisional():
    from meet import job_worker

    text = pp.build_system(profile="personal")
    assert "Подписи «Собеседник», «Собеседник N» и «Спикер N» предварительные" in text
    assert "Meet уточнил говорящих" in text
    assert _work_hits(text.replace(pp._PERSONAL_SOURCES_TOOLS, "")) == []
    assert "после встречи" in job_worker.CHAT_LABELS_NOTE
    assert "встреч" not in job_worker.CHAT_LABELS_NOTE_PERSONAL
