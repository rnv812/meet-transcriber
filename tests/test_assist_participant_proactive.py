"""Ходы по репликам у провайдеров с вызовом на ход (Antigravity CLI, Codex):
серия сбоев видна в чате одной строкой, молчание модели — счётчиком в
журнале, текст ответа идёт в ленту по ходу, причина отказа ворот — заметкой в
следующий ход. Поддельный runner, модель не зовётся."""

import asyncio

import pytest
from test_assist_participant import SILENT, FakeRunner, _make, agents, publish, run, say

from meet.assist import participant as part
from meet.llm import consent
from meet.llm.base import EMPTY_ERROR, AgentReply

SECRET = "бюджет сорок миллионов"     # реплика встречи: её не должно быть в журнале и строке


def _systems(h):
    return [m for m in h.chat.messages() if m["kind"] == "system"]


async def _proactive(h, t):
    """Реплика встречи в момент `t` и ход по ней (после паузы сбоя — тоже)."""
    publish(h, t, "Олег", f"{SECRET}, реплика {t}")
    h.clock.t = max(t + 10, h.p._retry_at + 1)
    assert await h.p.tick()


@pytest.mark.parametrize("provider", ["agy", "codex"])
def test_failed_replica_turns_show_one_line_per_series(tmp_path, provider):
    error = "Antigravity прервал ход сам\nподробности stderr"
    runner = FakeRunner([AgentReply(text="", error=error)] * 4 + [say("Теперь слышу")]
                        + [AgentReply(text="", error=EMPTY_ERROR)] * 3)
    h = _make(tmp_path, provider=provider, runner=runner)

    async def main():
        t = 10
        for _ in range(3):
            await _proactive(h, t)
            t = h.clock.t + 1
        assert len(_systems(h)) == 1                  # после третьего сбоя — строка
        await _proactive(h, t)                        # четвёртый — без второй строки
        assert len(_systems(h)) == 1
        await _proactive(h, h.clock.t + 1)            # ответ — серия кончилась
        for _ in range(3):
            await _proactive(h, h.clock.t + 1)
        await h.p.shutdown()

    run(main())
    first, second = _systems(h)
    assert first["level"] == "error" and part.PROACTIVE_FAILED.split("(")[0] in first["text"]
    assert "Antigravity прервал ход сам" in first["text"] and "stderr" not in first["text"]
    assert h.p.provider_label in first["text"]
    assert EMPTY_ERROR.rstrip(".") in second["text"]
    assert [m["status"] for m in agents(h)].count("shown") == 1
    for line in (first["text"], second["text"], *h.logs):
        assert SECRET not in line
    assert "агент: ход по репликам без ответа модели, подряд: 3" in h.logs


def test_unparseable_replica_answers_count_as_failures(tmp_path):
    runner = FakeRunner([AgentReply(text='{"op": "none"}')] * 3)
    h = _make(tmp_path, provider="agy", runner=runner)

    async def main():
        for t in (10, 30, 50):
            await _proactive(h, t)
        await h.p.shutdown()

    run(main())
    (line,) = _systems(h)
    assert part.NOT_PROTOCOL in line["text"]


def test_silence_is_an_answer_and_is_only_counted_in_the_log(tmp_path):
    runner = FakeRunner([SILENT] * 3 + [AgentReply(text="молчу")])
    h = _make(tmp_path, provider="agy", runner=runner)

    async def main():
        for t in (10, 30, 50, 70):
            await _proactive(h, t)
        await h.p.shutdown()

    run(main())
    assert not _systems(h)
    assert [line for line in h.logs if "промолчала" in line] == [
        "агент: ход по репликам — модель промолчала, подряд: 1",
        "агент: ход по репликам — модель промолчала, подряд: 3"]


def test_failures_of_answers_to_the_user_do_not_count(tmp_path):
    runner = FakeRunner([AgentReply(text="", error="сеть недоступна")] * 3)
    h = _make(tmp_path, provider="agy", runner=runner)

    async def main():
        await h.p.start()
        for _ in range(3):
            await h.p.post_user_message("что там со сроками?")
            h.clock.t = h.p._retry_at + 1
            await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert not [m for m in _systems(h) if m.get("level") == "error"]
    assert all(m["status"] == "failed" for m in agents(h))


@pytest.mark.parametrize("provider", ["agy", "codex"])
def test_reply_text_streams_into_the_feed(tmp_path, provider):
    """Текст ответа по ходу (`on_text`) — «пишет…» с частичным текстом, как у
    Claude Code; новое сообщение после инструмента (None) начинает текст заново."""
    seen: list[dict] = []

    class Streaming(FakeRunner):
        async def __call__(self, prompt, **kwargs):
            on_text = kwargs["on_text"]
            for piece in ("Посмотрю план.", None, '{"say": "В плане ', 'запуск 15.11"}'):
                h.clock.t += 1
                on_text(piece)
                await asyncio.sleep(0)
            return await super().__call__(prompt, **kwargs)

    runner = Streaming([say("В плане запуск 15.11")])
    h = _make(tmp_path, provider=provider, runner=runner)
    h.p.add_listener(lambda name, data: seen.append(data) if name == "chat_partial" else None)

    async def main():
        await _proactive(h, 10)
        await h.p.shutdown()

    run(main())
    texts = [d["text"] for d in seen]
    assert texts[0] == "Посмотрю план." and texts[-1] == "В плане запуск 15.11"
    assert not any("Посмотрю" in t for t in texts[1:])
    assert agents(h)[0]["text"] == "В плане запуск 15.11"


def test_agy_gets_the_reason_of_a_denial_in_the_next_turn(tmp_path):
    """agy видит только «отклонено»: причина отказа ворот — заметкой следующему ходу."""
    outside = tmp_path / "Загрузки" / "spec.md"

    class Denying(FakeRunner):
        async def __call__(self, prompt, **kwargs):
            gate = kwargs.get("gate")
            if gate is not None and len(self.calls) == 0:
                call = {"toolCallId": "c1", "kind": "read", "title": "Read", "locations": [{"path": str(outside)}]}
                await asyncio.to_thread(consent.agy_approval, gate, call, windows=False)
            return await super().__call__(prompt, **kwargs)

    runner = Denying([SILENT, SILENT])
    h = _make(tmp_path, provider="agy", runner=runner, freedom=True)

    async def main():
        await _proactive(h, 10)
        await _proactive(h, 40)
        await h.p.shutdown()

    run(main())
    first, second = (p for p, _kw in runner.calls)
    assert "В прошлом ходе: Meet заблокировал" not in first
    assert "В прошлом ходе: Meet заблокировал" in second and "spec.md" in second


def test_short_reason_takes_the_api_message_and_one_line():
    codex_error = ('{"type":"error","status":400,"error":{"type":"invalid_request_error",'
                   '"message":"The \'gpt-x\' model is not supported when using Codex with a ChatGPT account."}}')
    assert part._short_reason(codex_error) == ("The 'gpt-x' model is not supported when using Codex "
                                               "with a ChatGPT account")
    assert part._short_reason("первая\nвторая") == "первая"
    assert part._short_reason("") == "ошибка модели"
    long = part._short_reason("я" * 500)
    assert len(long) == part.REASON_MAX and long.endswith("…")


def test_claude_code_gets_no_denial_notes(tmp_path):
    h = _make(tmp_path, script=[])
    h.p._denial_notes([consent.Decision(consent.DENY, reason="Meet заблокировал: x")])
    assert not h.p._notes
