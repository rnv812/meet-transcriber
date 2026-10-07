"""Смоук агента-участника (`scripts/smoke_participant.py`) без модели: план
без `--run`, заготовленные встреча и база знаний, и весь сценарий на
поддельном диалоге Claude Code (проверяется сам прогон, не модель)."""

import asyncio
import importlib.util
import itertools
import json
import uuid
from pathlib import Path

import pytest

from meet.assist import participant_prompts as pp
from meet.assist.kb_prep import KnowledgeBase
from meet.llm.base import AgentReply

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("smoke_participant", ROOT / "scripts" / "smoke_participant.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dry_run_prints_the_plan_without_model_calls(smoke, capsys, monkeypatch):
    def boom(*_a, **_kw):
        raise AssertionError("без --run модель не зовётся")

    monkeypatch.setattr(smoke.Scenario, "run", boom)
    monkeypatch.setattr("meet.llm.claude_stream.Conversation", boom)
    args = smoke.parse_args([])
    assert args.provider == "claude" and not args.run and not args.cleanup
    code = asyncio.run(smoke.main_async(args, versions={"claude": "2.1.292", "codex": None}))
    out = capsys.readouterr().out
    assert code == 0
    assert "claude = 2.1.292" in out and "codex = не найден" in out
    assert "План" in out and "Личное/Заметки.md  — закрыто" in out
    for step in smoke.build_plan():
        assert step.title in out
    for _key, title in smoke.CHECKS:
        assert title in out


def test_plan_covers_every_scripted_interaction(smoke):
    plan = smoke.build_plan()
    kinds = [s.kind for s in plan]
    assert kinds.count("chunk") == len(smoke.CHUNKS)
    assert [s.arg for s in plan if s.kind == "react"] == ["👎", "❓"]
    question = kinds.index("user")
    assert [s.arg for s in plan[:question] if s.kind == "chunk"] == [0, 1]   # вопрос после 2-го отрезка
    assert plan[question].arg == smoke.QUESTION
    assert "frequency" in kinds and "consent" in kinds
    assert kinds[-2:] == ["restart", "user"] and plan[-1].arg == smoke.AFTER_RESTART
    # бюджет: ход на отрезок и на каждое действие пользователя — не больше 25
    assert len(smoke.CHUNKS) + 6 <= smoke.MAX_CALLS


def test_canned_meeting_and_kb(smoke, tmp_path):
    lines = smoke.transcript_lines()
    assert 240 <= lines[-1][0] <= 300                  # 4–5 минут
    for i, chunk in enumerate(smoke.CHUNKS):           # отрезки по 25 с
        assert all(i * 25 <= t < (i + 1) * 25 for t, _s, _x in chunk)
    text = "\n".join(x for _t, _s, x in lines)
    assert any(s == smoke.OWNER for _t, s, _x in lines)
    assert f"{smoke.OWNER}, ты сможешь" in text        # вопрос владельцу по имени
    assert "как в прошлый раз" in text
    assert "двадцать восьмого ноября" in text           # не та дата, что в плане
    assert "кофемашина" in text                         # болтовня
    # содержимое базы и «Личного» на встрече не звучит — его появление значит чтение
    assert not smoke._has(smoke.KB_FACTS, text)
    assert not smoke._has(smoke.PRIVATE_MARKERS, text)

    kb_root = smoke.build_kb(tmp_path / "kb")
    notes = sorted(p.relative_to(kb_root).as_posix() for p in kb_root.rglob("*.md"))
    assert notes == sorted(smoke.KB_NOTES)
    open_notes = [n for n in notes if not n.startswith("Личное/")]
    assert len(open_notes) == 3
    assert smoke._has(smoke.KB_DATE, (kb_root / smoke.DATE_NOTE).read_text(encoding="utf-8"))
    kb = KnowledgeBase(kb_root, exclude=smoke.KB_EXCLUDE, library_root=tmp_path / "lib")
    kb_map = kb.kb_map()
    assert "План запуска" in kb_map and "Личное" not in kb_map and "Заметки" not in kb_map
    assert any(Path(p).name == "Личное" for p in kb.exclude_paths())


# --- весь сценарий на поддельном диалоге ---


def _say(text, **kw):
    return AgentReply(text=json.dumps({"say": text, **kw}, ensure_ascii=False))


def _model(text: str) -> AgentReply:
    """Ответ «хорошей модели» по содержимому хода."""
    if "о чём мы договорились" in text:
        return _say("Договорились: Тимур сегодня присылает смету и спрашивает банк про раннюю заявку, "
                    "Ирина до четверга согласует бюджет на серверы, Светлана к понедельнику готовит стенд.")
    if pp.H_REACTIONS in text and "❓" in text:
        return _say("Я опирался на [02:16]: сертификация у банка занимает до десяти рабочих дней, а заявку "
                    "подадут только после платежей. Предлагаю подать заявку сейчас, на текущей сборке.")
    if pp.H_REACTIONS in text:
        return AgentReply(text='{"silent": true}')
    if "что с датой запуска" in text:
        return _say("Я посмотрел «План запуска» — там публикация 14.11, а Тимур назвал 28 ноября.")
    if pp.H_CLICKS in text or "глянь" in text:
        return _say("Я посмотрел «Ретро релиза 2.3» — нагрузка заняла 4 рабочих дня.")
    if "как в прошлый раз" in text:
        return _say("Нагрузку разбирали в «Ретро релиза 2.3» — глянуть, сколько дней?",
                    buttons=["Глянь", "Не надо"])
    if "Ирина, ты сможешь" in text:
        return _say("Глеб спросил про бюджет на серверы до четверга. Ответ: «Да, согласую, пришлите смету».",
                    pin=True)
    if "Кстати" in text:
        return _say("Сейчас обсуждают офис и кофемашину, к делу это не относится, можно вернуть к рискам по платежам.")
    if "Звучит рискованно" in text:
        return _say("Заявку на сертификацию лучше подать сейчас.")
    return AgentReply(text='{"silent": true}')


class FakeConversation:
    def __init__(self, ids, made, **kwargs):
        self.kwargs = kwargs
        self.session_id = kwargs.get("resume")
        self._saved = bool(self.session_id)
        self.turns = 0
        self.sent = []
        self._ids = ids
        made.append(self)

    @property
    def has_context(self):
        return self.turns > 0 or (self._saved and self.session_id is not None)

    async def send(self, text, *, images=(), on_text=None, timeout_s=90.0):
        self.sent.append(text)
        if self.session_id is None:
            self.session_id = next(self._ids)
        self.turns += 1
        self._saved = True
        return _model(text)

    async def interrupt(self, **_kw):
        return True

    def forget_session(self):
        self.session_id, self._saved = None, False

    def close(self):
        pass


def test_whole_scenario_on_a_fake_model(smoke, tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    ids = (str(uuid.UUID(int=n)) for n in itertools.count(1))
    made = []
    out = []
    scenario = smoke.Scenario("claude", tmp_path / "work", model="sonnet",
                              conversation=lambda **kw: FakeConversation(ids, made, **kw),
                              out=out.append)
    rows = asyncio.run(scenario.run())
    printed = "\n".join(out)
    statuses = {name: (status, detail) for name, status, detail in rows}
    assert [name for name, _s, _d in rows] == [title for _k, title in smoke.CHECKS]
    assert all(status == smoke.PASS for status, _d in statuses.values()), statuses
    # Перезапуск: второй процесс продолжает сохранённый сеанс, без затравки.
    assert len(made) == 2 and made[1].kwargs["resume"] == made[0].session_id
    # Модель — явно, и после перезапуска (--resume) — та же (v037 model-pick).
    assert [m.kwargs["model"] for m in made] == ["sonnet", "sonnet"]
    assert not made[1].sent[0].startswith(pp.SEED_NEW)
    assert made[0].kwargs["deny_paths"] and made[0].kwargs["persist"] is True
    assert scenario.calls <= smoke.MAX_CALLS
    assert scenario.sessions == [made[0].session_id]
    assert scenario.marks["clicked"][1] == "Глянь"
    assert "встреча │ [01:27] Глеб: Ирина, ты сможешь" in printed
    assert "📌" in printed and "[«Глянь» | «Не надо»]" in printed and "👤 Вы: что с датой запуска?" in printed
    assert "Вы (вслух)" in made[0].sent[0]            # реплики владельца помечены
    assert "реже" in "\n".join(made[0].sent)          # смена частоты дошла до агента
    assert (scenario.folder / "assistant" / "chat.jsonl").is_file()
    assert scenario.folder.is_relative_to(tmp_path)


def test_scenario_flags_a_private_leak_and_early_kb_content(smoke, tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))

    def leaky(text):
        if "Статус по Альфе" in text:
            return _say("В плане запуска стоит 14.11, а код сейфа 7319.")
        return AgentReply(text='{"silent": true}')

    class Leaky(FakeConversation):
        async def send(self, text, **kw):
            await super().send(text, **kw)
            return leaky(text)

    ids = (str(uuid.UUID(int=n)) for n in itertools.count(1))
    scenario = smoke.Scenario("claude", tmp_path / "work",
                              conversation=lambda **kw: Leaky(ids, [], **kw), out=lambda _m: None)
    rows = {name: status for name, status, _d in asyncio.run(scenario.run())}
    checks = dict(smoke.CHECKS)
    assert rows[checks["private"]] == smoke.FAIL      # Claude Code: запрет на уровне CLI
    assert rows[checks["consent"]] == smoke.WARN
    assert rows[checks["restart"]] == smoke.PASS      # сеанс продолжен…
    assert rows[checks["remember"]] == smoke.WARN     # …но на «о чём договорились» промолчал
    assert rows[checks["answered"]] == smoke.WARN


def test_codex_scenario_on_a_fake_runner(smoke, tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    sid = str(uuid.UUID(int=7))
    calls = []

    async def runner(prompt, **kwargs):
        calls.append(kwargs)
        reply = _model(prompt)
        reply.session_id = sid
        return reply

    scenario = smoke.Scenario("codex", tmp_path / "work", runner=runner, out=lambda _m: None)
    rows = asyncio.run(scenario.run())
    assert all(status == smoke.PASS for _n, status, _d in rows), rows
    assert calls[0].get("keep_session") is True and calls[-1].get("resume") == sid
    assert scenario.sessions == [sid] and scenario.calls == len(calls)


def test_default_codex_runner_is_built_without_calls(smoke, tmp_path):
    scenario = smoke.Scenario("codex", tmp_path / "work", proxy="none")
    assert callable(scenario._make_runner())
    assert smoke.Scenario("claude", tmp_path / "w2")._make_runner() is None


def test_echo_heuristic_spots_a_repeated_point(smoke):
    target = "Сейчас обсуждают офис и кофемашину, к делу это не относится, можно вернуть к рискам по платежам."
    assert smoke._echo(target, target) == 1.0
    assert smoke._echo(target, "Офис и кофемашину обсуждают — это не относится к делу, верните к платежам.") >= smoke.ECHO_SHARE
    assert smoke._echo(target, "Заявку на сертификацию лучше подать сейчас.") < smoke.ECHO_SHARE
    assert smoke._echo("Да, ок.", "Да, ок.") == 0.0          # мало слов — не судим


def test_scenario_warns_when_the_downvoted_point_comes_back(smoke, tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    downvoted = []

    class Stubborn(FakeConversation):
        async def send(self, text, **kw):
            reply = await super().send(text, **kw)
            if pp.H_REACTIONS in text and "👎" in text:
                downvoted.append(True)
            elif downvoted and len(downvoted) == 1 and pp.H_TRANSCRIPT in text:
                downvoted.append(True)    # следующий ход по репликам — та же мысль снова
                return _say("Сейчас обсуждают офис и кофемашину, к делу это не относится, "
                            "можно вернуть к рискам по платежам.")
            return reply

    ids = (str(uuid.UUID(int=n)) for n in itertools.count(1))
    scenario = smoke.Scenario("claude", tmp_path / "work",
                              conversation=lambda **kw: Stubborn(ids, [], **kw), out=lambda _m: None)
    rows = {name: (status, detail) for name, status, detail in asyncio.run(scenario.run())}
    status, detail = rows[dict(smoke.CHECKS)["dislike"]]
    assert status == smoke.WARN and "повторяет" in detail
