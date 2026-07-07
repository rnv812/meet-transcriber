import asyncio

from meet.assist.agent import AgentReply
from meet.assist.bus import TranscriptBus
from meet.assist.digest import Digest
from meet.assist.digester import Digester


def _make(replies):
    bus, digest = TranscriptBus(), Digest()

    async def runner(prompt, **kw):
        return replies.pop(0)

    return bus, digest, Digester(bus, digest, system_prompt="s", runner=runner)


def test_should_tick_rules():
    d = Digester(TranscriptBus(), Digest(), system_prompt="s", runner=None)
    assert not d.should_tick(0, 999)              # тишина — не тикать
    assert not d.should_tick(100, 30)             # рано и мало
    assert d.should_tick(2000, 61)                # много текста после min
    assert d.should_tick(100, 91)                 # хоть что-то после max
    assert not d.should_tick(2000, 30)            # даже burst не раньше min


def test_tick_once_applies_delta_and_advances_cursor():
    bus, digest, d = _make([AgentReply(text="ADD Тема :: Решили X")])
    bus.publish("[00:00:05] Вы: решили X")
    assert asyncio.run(d.tick_once()) is True
    assert "Решили X" in digest.render() and digest.version == 1
    assert asyncio.run(d.tick_once()) is False  # новых строк нет — без вызова


def test_error_reply_sets_status_and_keeps_lines_for_retry():
    bus, digest, d = _make([
        AgentReply(text="", error="rate_limit"),
        AgentReply(text="ADD Тема :: Решили X"),
    ])
    bus.publish("[00:00:05] Вы: решили X")
    assert asyncio.run(d.tick_once()) is False
    assert "rate_limit" in (d.status or "")
    assert asyncio.run(d.tick_once()) is True     # строки не потеряны
    assert digest.version == 1


def test_garbage_delta_discarded_status_set():
    bus, digest, d = _make([AgentReply(text="Вот дайджест: всё хорошо")])
    bus.publish("[00:00:05] Вы: привет")
    assert asyncio.run(d.tick_once()) is False
    assert digest.version == 0 and d.status


def test_runner_exception_sets_status_and_keeps_lines_for_retry():
    bus, digest = TranscriptBus(), Digest()

    async def boom(prompt, **kw):
        raise RuntimeError("sdk упал")

    d = Digester(bus, digest, system_prompt="s", runner=boom)
    bus.publish("[00:00:05] Вы: решили X")
    assert asyncio.run(d.tick_once()) is False  # исключение не вылетает наружу
    assert "RuntimeError" in (d.status or "")
    assert digest.version == 0

    async def ok(prompt, **kw):
        return AgentReply(text="ADD Тема :: Решили X")

    d._runner = ok  # ретрай с рабочим runner'ом: курсор не сдвинулся
    assert asyncio.run(d.tick_once()) is True
    assert digest.version == 1 and "Решили X" in digest.render()
