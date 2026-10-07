"""Агент-участник встречи (V4, задача 4): цикл агента.

Модель — поддельный диалог Claude Code (`FakeConversation`) и поддельный
runner (Codex, OpenCode, локальная модель); часы — поддельные; шина и журнал —
настоящие, во временной папке. Реплики выдуманы; модель не зовётся."""

import asyncio
import io
import itertools
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from meet.assist import participant_prompts as pp
from meet.assist.bus import TranscriptBus
from meet.assist.chatlog import ChatLog, visible_in_feed
from meet.assist.kb_prep import KnowledgeBase
from meet.assist.participant import (
    NOTE_INTERRUPTED,
    NOTE_STOPPED,
    TOOLS_SPENT,
    Participant,
    partial_text,
)
from meet.llm.base import CANCELLED_ERROR, NO_VISION_NOTE, AgentReply, resume_failure
from meet.settings import Settings


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def say(text, **kw):
    return AgentReply(text=json.dumps({"say": text, **kw}, ensure_ascii=False))


SILENT = AgentReply(text='{"silent": true}')


class FakeConversation:
    """Диалог Claude Code: сеанс, продолжение, остановка. `script` — общий
    для всех экземпляров список ответов: AgentReply, "block" (ход ждёт
    остановки) или функция (conv, text) -> AgentReply."""

    def __init__(self, script, ids, **kwargs):
        self.kwargs = kwargs
        self.script = script
        self.ids = ids
        self.sent = []
        self.session_id = kwargs.get("resume")
        self._saved = bool(self.session_id)
        self.turns = 0
        self.interrupts = 0
        self.closed = False
        self.started = asyncio.Event()
        self._cancel = None

    @property
    def has_context(self):
        return self.turns > 0 or (self._saved and self.session_id is not None)

    async def send(self, text, *, images=(), on_text=None, timeout_s=90.0):
        self.sent.append((text, list(images)))
        if self.session_id is None:
            self.session_id = next(self.ids)
        item = self.script.pop(0)
        if callable(item):
            item = item(self, text)
        if item == "block":
            self._cancel = asyncio.Event()
            self.started.set()
            if on_text is not None:
                on_text('{"say": "Начинаю')
            await self._cancel.wait()
            return AgentReply(text='{"say": "Начинаю', error=CANCELLED_ERROR, cancelled=True)
        if item.resume_failed:
            self.session_id, self._saved = None, False
            return item
        self._saved = True
        self.turns += 1
        if on_text is not None and item.text:
            half = len(item.text) // 2
            on_text(item.text[:half])
            on_text(item.text[half:])
        return item

    async def interrupt(self, **_kw):
        self.interrupts += 1
        if self._cancel is not None:
            self._cancel.set()
        return True

    def forget_session(self):
        self.session_id, self._saved = None, False

    def close(self):
        self.closed = True


class FakeRunner:
    """Codex / OpenCode / локальная модель: вызов на ход."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.started = asyncio.Event()

    async def __call__(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        item = self.replies.pop(0)
        if item == "block":
            self.started.set()
            await asyncio.Event().wait()   # до отмены задачи
        return item


def _make(tmp_path, *, provider="claude-code", script=None, runner=None, kb=None, folder=None,
          **kw):
    folder = folder or tmp_path / "lib" / "2026-10-07_10-00"
    folder.mkdir(parents=True, exist_ok=True)
    bus = TranscriptBus()
    chat = ChatLog(folder, log=lambda _m: None)
    made = []
    ids = (f"sess-{n}" for n in itertools.count(1))
    script = script if script is not None else []

    def factory(**kwargs):
        conv = FakeConversation(script, ids, **kwargs)
        made.append(conv)
        return conv

    clock = kw.pop("clock", None) or Clock()
    logs = []
    p = Participant(bus, chat, provider=provider, folder=folder, runner=runner,
                    conversation=factory, kb=kb, library_root=folder.parent,
                    owner_name="Марина", owner_speaker="Марина", clock=clock,
                    log=logs.append, **kw)
    events = []
    p.add_listener(lambda name, data: events.append((name, data)))
    return SimpleNamespace(p=p, bus=bus, chat=chat, made=made, clock=clock, logs=logs,
                           events=events, folder=folder, script=script)


def publish(h, t, speaker, text, *, at=None):
    """Реплика в шину в момент `at` (часы ассистента) со временем записи `t`."""
    h.clock.t = t if at is None else at
    m, s = int(t) // 60, int(t) % 60
    entry = {"t": float(t), "end": float(t) + 2, "speaker": speaker, "text": text}
    index = h.bus.publish(f"[00:{m:02d}:{s:02d}] {speaker}: {text}", entry)
    h.p._scan()
    return index


def agents(h):
    return [m for m in h.chat.messages() if m["kind"] == "agent"]


def run(coro):
    return asyncio.run(coro)


async def _wait_for(cond, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "не дождались"
        await asyncio.sleep(0.005)


def _kb(tmp_path):
    root = tmp_path / "kb"
    (root / "Проекты").mkdir(parents=True)
    (root / "Проекты" / "План запуска.md").write_text(
        "# План запуска\n\nЗапуск назначен на 15.11, владелец этапа 2 не назначен.\n",
        encoding="utf-8")
    (root / "Личное").mkdir()
    (root / "Личное" / "секрет.md").write_text("пароль", encoding="utf-8")
    return KnowledgeBase(root, exclude=("Личное/",), library_root=tmp_path / "lib")


def _png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 16), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


# --- сессия: старт, продолжение, затравка ---


def test_start_without_session_seeds_and_stores_the_new_id(tmp_path):
    kb = _kb(tmp_path)
    h = _make(tmp_path, script=[say("В плане запуск 15.11, а не 01.12")], kb=kb, model="sonnet")

    async def main():
        publish(h, 5, "Олег", "запускаемся первого декабря")
        h.clock.t = 10
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    conv = h.made[0]
    kw = conv.kwargs
    assert kw["responder"] is True and kw["persist"] is True and kw["resume"] is None
    assert kw["model"] == "sonnet"
    assert {str(h.folder), str(kb.root), str(h.folder.parent)} <= set(kw["add_dirs"])
    assert any(Path(p).name == "Личное" for p in kw["deny_paths"])
    system = kw["system_prompt"]
    assert "План запуска.md" in system and "Марина" in system and "Личное/" in system
    assert '{"read":' not in system   # инструменты свои — запросов к Meet нет
    text = conv.sent[0][0]
    assert text.startswith(pp.SEED_NEW)
    assert "[00:05] Олег: запускаемся первого декабря" in text
    reply = agents(h)[-1]
    assert reply["status"] == "shown" and reply["text"] == "В плане запуск 15.11, а не 01.12"
    assert reply["mode"] == "proactive"
    assert h.chat.session_id("claude-code") == "sess-1"
    assert h.p.view()["session"] == "new" and h.p.view()["sees"]["kb"] is True
    assert conv.closed  # штатная остановка закрывает процесс


def test_resume_success_sends_only_the_delta(tmp_path):
    h = _make(tmp_path, script=[SILENT])
    h.chat.set_session_id("claude-code", "sess-old")

    async def main():
        publish(h, 5, "Олег", "обсудим бюджет")
        h.clock.t = 10
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    conv = h.made[0]
    assert conv.kwargs["resume"] == "sess-old"
    text = conv.sent[0][0]
    assert text.startswith(pp.H_TRANSCRIPT) and pp.SEED_NEW not in text and pp.SEED_RESUMED not in text
    assert h.chat.session_id("claude-code") == "sess-old"
    assert h.p.view()["session"] == "resumed"


def test_resume_failed_forgets_the_session_and_seeds_from_the_journal(tmp_path):
    h = _make(tmp_path, script=[resume_failure("No conversation found"), say("Я тут")])
    h.chat.set_session_id("claude-code", "sess-old")
    h.chat.append("agent", text="Раньше я писал про SLA", status="shown")

    async def main():
        publish(h, 5, "Олег", "по SLA договорились")
        h.clock.t = 10
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    conv = h.made[0]
    assert len(conv.sent) == 2
    first, second = conv.sent[0][0], conv.sent[1][0]
    assert pp.SEED_RESUMED not in first
    assert second.startswith(pp.SEED_RESUMED) and "Раньше я писал про SLA" in second
    assert "по SLA договорились" in second
    assert h.chat.session_id("claude-code") == "sess-1"  # новый сеанс, старый забыт
    assert agents(h)[-1]["text"] == "Я тут"
    assert any("не продолжить" in line for line in h.logs)


def test_codex_keeps_then_resumes_its_session(tmp_path):
    runner = FakeRunner([AgentReply(text='{"silent": true}', session_id="0000-codex"),
                         AgentReply(text='{"silent": true}', session_id="0000-codex")])
    h = _make(tmp_path, provider="codex", runner=runner)

    async def main():
        publish(h, 5, "Олег", "первое")
        h.clock.t = 10
        assert await h.p.tick()
        publish(h, 12, "Анна", "второе", at=12)
        h.clock.t = 30
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    (p1, k1), (p2, k2) = runner.calls
    assert k1["keep_session"] is True and "resume" not in k1
    assert k2["resume"] == "0000-codex" and "keep_session" not in k2
    assert p1.startswith(pp.SEED_NEW) and p2.startswith(pp.H_TRANSCRIPT)
    assert h.chat.session_id("codex") == "0000-codex"
    assert k1["max_turns"] == 12 and str(h.folder) in k1["allowed_dirs"]


def test_codex_resume_failed_starts_a_new_session_with_a_seed(tmp_path):
    runner = FakeRunner([resume_failure("no rollout found"),
                         AgentReply(text='{"silent": true}', session_id="new-codex")])
    h = _make(tmp_path, provider="codex", runner=runner)
    h.chat.set_session_id("codex", "old-codex")

    async def main():
        publish(h, 5, "Олег", "продолжаем")
        h.clock.t = 10
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    (p1, k1), (p2, k2) = runner.calls
    assert k1["resume"] == "old-codex" and k2["keep_session"] is True
    assert p2.startswith(pp.SEED_NEW) and "продолжаем" in p2
    assert h.chat.session_id("codex") == "new-codex"


def test_local_model_is_seeded_every_turn_and_has_no_session(tmp_path):
    runner = FakeRunner([SILENT, SILENT])
    h = _make(tmp_path, provider="openai-compatible", runner=runner)

    async def main():
        publish(h, 5, "Олег", "первое")
        h.clock.t = 10
        assert await h.p.tick()
        publish(h, 12, "Анна", "второе", at=12)
        h.clock.t = 30
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert runner.calls[0][0].startswith(pp.SEED_NEW)
    assert runner.calls[1][0].startswith(pp.SEED_RESUMED)   # журнал уже есть
    for prompt, kw in runner.calls:
        assert "resume" not in kw and "keep_session" not in kw
        assert '{"read":' in kw["system_prompt"]   # без инструментов — запросы к Meet
    assert "[00:05] Олег: первое" in runner.calls[1][0]   # прошлое — в затравке
    assert h.chat.sessions() == {}
    assert h.p.view()["tools"] is False


# --- подача: паузы, интервал, ничего нового ---


def test_delta_goes_at_a_pause_and_at_least_every_max_interval(tmp_path):
    h = _make(tmp_path, script=[SILENT, SILENT])

    async def main():
        publish(h, 0, "Олег", "начнём")
        assert not h.p.turn_due(3.9)
        assert h.p.turn_due(4.0)        # лента молчит — пауза
        h.clock.t = 4.0
        assert await h.p.tick()
        # Речь идёт без пауз: реплики каждые 2 с.
        t = 10.0
        publish(h, t, "Анна", "говорю без остановки")
        first = t
        while True:
            t += 2.0
            publish(h, t, "Анна", f"ещё реплика {t:.0f}")
            if t - first >= 25.0:
                assert h.p.turn_due(t)
                break
            assert not h.p.turn_due(t), t
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    second = h.made[0].sent[1][0]
    assert "говорю без остановки" in second and "ещё реплика 34" in second


def test_nothing_new_means_no_call(tmp_path):
    h = _make(tmp_path, script=[SILENT])

    async def main():
        h.clock.t = 100
        assert not await h.p.tick()            # пусто — модели нет
        publish(h, 5, "Олег", "одна реплика")
        h.clock.t = 110
        assert await h.p.tick()
        for t in (130, 200, 400):
            h.clock.t = t
            assert not await h.p.tick()
        hidden = publish(h, 50, "Олег", "дубль", at=401)
        h.bus.hide([hidden])                  # спрятанный дубль — не новое
        h.clock.t = 500
        assert not await h.p.tick()
        assert h.p._wake_in(500) is None
        await h.p.shutdown()

    run(main())
    assert len(h.made[0].sent) == 1


def test_owner_lines_are_labelled_through_delta(tmp_path):
    h = _make(tmp_path, script=[SILENT])

    async def main():
        publish(h, 5, "Марина", "Олег, а кто владелец второго этапа?")
        h.clock.t = 10
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert f"[00:05] {pp.OWNER_LABEL}: Олег, а кто владелец" in h.made[0].sent[0][0]


# --- сообщение пользователя вне очереди ---


def test_user_message_interrupts_a_transcript_turn(tmp_path):
    h = _make(tmp_path, script=["block", say("Бюджет — 2 млн, Олег сказал в [00:05]")])

    async def main():
        publish(h, 5, "Олег", "бюджет у нас два миллиона")
        h.clock.t = 10
        turn = asyncio.ensure_future(h.p.tick())
        await _wait_for(lambda: h.made and h.made[0].started.is_set())
        assert h.p.view()["state"] == "writing"
        posted = await h.p.post_user_message("Что с бюджетом?", client_id="c1")
        assert await turn
        assert h.made[0].interrupts == 1
        assert h.p.turn_due()
        assert await h.p.tick()
        await h.p.shutdown()
        return posted

    posted = run(main())
    held, answer = agents(h)
    assert held["status"] == "dropped" and held["text"] == "Начинаю"
    assert held["note"] == "прервано сообщением пользователя"
    assert not visible_in_feed(held)
    assert answer["status"] == "shown" and answer["mode"] == "reply" and answer["re"] == posted["id"]
    second = h.made[0].sent[1][0]
    assert pp.H_USER in second and "Что с бюджетом?" in second
    assert "бюджет у нас два миллиона" in second       # недоставленные реплики — вместе
    assert NOTE_INTERRUPTED in second
    partials = [d for name, d in h.events if name == "chat_partial"]
    assert partials and partials[0] == {"id": held["id"], "text": "Начинаю"}


def test_user_message_cancels_a_runner_call(tmp_path):
    runner = FakeRunner(["block", say("Отвечаю")])
    h = _make(tmp_path, provider="codex", runner=runner)

    async def main():
        publish(h, 5, "Олег", "идёт обсуждение")
        h.clock.t = 10
        turn = asyncio.ensure_future(h.p.tick())
        await _wait_for(runner.started.is_set)
        await h.p.post_user_message("Вопрос")
        assert await turn
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    held, answer = agents(h)
    assert held["status"] == "dropped" and answer["text"] == "Отвечаю"
    assert "Вопрос" in runner.calls[1][0] and "идёт обсуждение" in runner.calls[1][0]


def test_user_message_during_a_reply_to_the_user_waits_its_turn(tmp_path):
    h = _make(tmp_path, script=["block", SILENT])

    async def main():
        await h.p.post_user_message("Первый вопрос")
        turn = asyncio.ensure_future(h.p.tick())
        await _wait_for(lambda: h.made and h.made[0].started.is_set())
        posted = await h.p.post_user_message("Второй вопрос")
        assert posted["queued"] is True and h.made[0].interrupts == 0
        h.made[0]._cancel.set()
        await turn
        assert h.p.turn_due()
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert "Второй вопрос" in h.made[0].sent[1][0]


def test_stop_reply_cancels_and_keeps_the_visible_text(tmp_path):
    h = _make(tmp_path, script=["block", SILENT])

    async def main():
        publish(h, 5, "Олег", "реплика")
        h.clock.t = 10
        turn = asyncio.ensure_future(h.p.tick())
        await _wait_for(lambda: h.made and h.made[0].started.is_set())
        assert not await h.p.stop_reply("m999")
        assert await h.p.stop_reply()
        await turn
        publish(h, 20, "Анна", "новая", at=20)
        h.clock.t = 40
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    stopped = agents(h)[0]
    assert stopped["status"] == "cancelled" and stopped["text"] == "Начинаю"
    assert visible_in_feed(stopped)
    assert NOTE_STOPPED in h.made[0].sent[1][0]


# --- кнопки, реакции, вложения ---


def test_button_click_and_reactions_reach_the_next_delta(tmp_path):
    h = _make(tmp_path, script=[say("Глянуть план запуска?", buttons=["Глянь", "Не надо"]), SILENT])

    async def main():
        publish(h, 5, "Анна", "как в прошлый раз")
        h.clock.t = 10
        await h.p.tick()
        mid = agents(h)[0]["id"]
        await h.p.click(mid, "Глянь")
        events = await h.p.react(mid, "❓")
        assert events and h.p.turn_due()
        assert await h.p.tick()
        await h.p.shutdown()
        return mid

    mid = run(main())
    text = h.made[0].sent[1][0]
    assert pp.H_CLICKS in text and "«Глянь»" in text
    assert pp.H_REACTIONS in text and "❓" in text and "Глянуть план запуска?" in text
    reply = agents(h)[-1]
    assert reply["mode"] == "reply"
    assert h.chat.get(mid)["reactions"].keys() == {"❓"}
    assert [n for n, _ in h.events].count("chat") >= 6


def test_image_goes_to_a_model_with_vision(tmp_path):
    h = _make(tmp_path, script=[say("На графике рост в марте")])

    async def main():
        posted = await h.p.post_user_message("что на скрине?", [{"data": _png(), "name": "скрин.png"}])
        assert await h.p.tick()
        await h.p.shutdown()
        return posted

    posted = run(main())
    text, images = h.made[0].sent[0]
    assert len(images) == 1 and Path(images[0]).is_file()
    assert Path(images[0]).is_relative_to(h.folder)
    assert "скрин.png" in text and "изображение" in text and NO_VISION_NOTE not in text
    att = h.chat.get(posted["attachments"][0])
    assert att["kind"] == "attachment" and att["type"] == "image" and att["vision"] is True
    assert h.chat.get(posted["id"])["attachments"] == [att["id"]]
    assert h.p.view()["sees"]["images"] == 1


def test_image_without_vision_is_a_note(tmp_path):
    runner = FakeRunner([say("Не вижу картинку")])
    h = _make(tmp_path, provider="opencode", runner=runner)

    async def main():
        await h.p.post_user_message("что тут?", [{"data": _png(), "name": "схема.png"}])
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    prompt, kw = runner.calls[0]
    assert kw["images"] == []
    assert NO_VISION_NOTE in prompt and "схема.png" in prompt
    att = [m for m in h.chat.messages() if m["kind"] == "attachment"][0]
    assert att["vision"] is False and att["note"] == NO_VISION_NOTE


def test_image_the_provider_dropped_is_marked(tmp_path):
    def dropped(conv, _text):
        path = conv.sent[-1][1][0]
        return AgentReply(text='{"silent": true}', dropped_images=[path],
                          notes=["Изображение не отправлено: модель его не приняла"])

    h = _make(tmp_path, script=[dropped])

    async def main():
        posted = await h.p.post_user_message("глянь", [{"data": _png(), "name": "a.png"}])
        await h.p.tick()
        await h.p.shutdown()
        return posted

    posted = run(main())
    att = h.chat.get(posted["attachments"][0])
    assert att["delivered"] is False and "не приняла" in att["note"]


def test_broken_image_is_a_failed_attachment(tmp_path):
    h = _make(tmp_path, script=[SILENT])

    async def main():
        await h.p.post_user_message("глянь", [{"data": b"not an image", "name": "битый.png"}])
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    att = [m for m in h.chat.messages() if m["kind"] == "attachment"][0]
    assert att["status"] == "failed" and att["error"]
    text, images = h.made[0].sent[0]
    assert images == [] and "не разобрано" in text


def test_document_attachment_goes_through_materials(tmp_path):
    doc = tmp_path / "План.md"
    doc.write_text("# План запуска\n\nЗапуск назначен на 15 ноября.\n\n## Риски\n\nНет владельца.\n",
                   encoding="utf-8")
    h = _make(tmp_path, script=[say("В плане — 15 ноября")])

    async def main():
        posted = await h.p.post_user_message("глянь план", [str(doc)])
        assert await h.p.tick()
        await h.p.shutdown()
        return posted

    posted = run(main())
    att = h.chat.get(posted["attachments"][0])
    assert att["type"] == "doc" and att["status"] == "ready" and att["ref"].startswith("a")
    dump = Path(att["path"])
    assert dump.is_file() and dump.is_relative_to(h.folder)
    assert "15 ноября" in dump.read_text(encoding="utf-8")
    text = h.made[0].sent[0][0]
    assert "Кратко о вложениях" in text and "Запуск назначен на 15 ноября" in text
    assert pp.DATA_OPEN in text
    assert h.p.view()["sees"]["materials"] == 1


def test_resent_message_is_not_duplicated(tmp_path):
    h = _make(tmp_path, script=[SILENT])

    async def main():
        a = await h.p.post_user_message("привет", client_id="same")
        b = await h.p.post_user_message("привет", client_id="same")
        await h.p.shutdown()
        return a, b

    a, b = run(main())
    assert a["id"] == b["id"] and b["duplicate"] is True
    assert len(h.p._user) == 1


# --- ответ: say, silent, несколько, склейка ---


def test_silent_reply_is_hidden(tmp_path):
    h = _make(tmp_path, script=[SILENT])

    async def main():
        publish(h, 5, "Олег", "перерыв пять минут")
        h.clock.t = 10
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    reply = agents(h)[0]
    assert reply["status"] == "dropped" and reply["silent"] is True
    assert not visible_in_feed(reply)


def test_several_says_become_messages_when_the_window_allows(tmp_path):
    two = AgentReply(text='{"say": "Первое"}\n{"say": "Второе", "buttons": ["Да"]}')
    h = _make(tmp_path, script=[two], merge_window_s=0)

    async def main():
        publish(h, 5, "Олег", "реплика")
        h.clock.t = 10
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    shown = [m for m in agents(h) if m["status"] == "shown"]
    assert [m["text"] for m in shown] == ["Первое", "Второе"]
    assert shown[1]["buttons"] == ["Да"]


def test_several_says_in_one_reply_are_merged_within_the_window(tmp_path):
    two = AgentReply(text='{"say": "Первое", "buttons": ["А"]}\n{"say": "Второе", "buttons": ["Б"]}')
    h = _make(tmp_path, script=[two])

    async def main():
        publish(h, 5, "Олег", "реплика")
        h.clock.t = 10
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    (msg,) = agents(h)
    assert msg["status"] == "shown" and msg["text"] == "Первое\n\nВторое"
    assert msg["buttons"] == ["А", "Б"]


def test_one_new_agent_message_per_15_seconds(tmp_path):
    h = _make(tmp_path, script=[say("Первое"), say("Второе"), say("Третье"), say("Ответ")])

    async def main():
        publish(h, 1, "Олег", "раз")
        h.clock.t = 5
        await h.p.tick()                      # сообщение в 5 с
        publish(h, 6, "Олег", "два", at=6)
        h.clock.t = 13
        await h.p.tick()                      # 13 − 5 < 15 — склейка
        publish(h, 14, "Олег", "три", at=14)
        h.clock.t = 25
        await h.p.tick()                      # 25 − 5 ≥ 15 — новое
        h.clock.t = 26
        await h.p.post_user_message("а что скажешь?")
        await h.p.tick()                      # ответ пользователю — всегда своё
        await h.p.shutdown()

    run(main())
    first, merged, third, answer = agents(h)
    assert first["text"] == "Первое\n\nВторое" and first["status"] == "shown"
    assert merged["status"] == "superseded" and merged["merged_into"] == first["id"]
    assert third["status"] == "shown" and third["text"] == "Третье"
    assert answer["status"] == "shown" and answer["text"] == "Ответ"


# --- запасной путь: Meet исполняет read / search / list ---


def test_toolless_model_reads_through_meet(tmp_path):
    kb = _kb(tmp_path)
    runner = FakeRunner([AgentReply(text='{"read": ["Проекты/План запуска.md"]}'),
                         say("В плане запуск 15.11")])
    h = _make(tmp_path, provider="openai-compatible", runner=runner, kb=kb)

    async def main():
        await h.p.post_user_message("глянь план запуска")
        assert await h.p.tick()
        assert h.p.turn_due()                 # ответ Meet — сразу, без паузы
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    tools = [m for m in h.chat.messages() if m["kind"] == "tool"]
    assert [t["event"] for t in tools] == ["request", "result"]
    assert tools[0]["call"] == "read" and tools[0]["args"] == ["Проекты/План запуска.md"]
    assert tools[1]["re"] == tools[0]["id"] and "15.11" in tools[1]["text"]
    second = runner.calls[1][0]
    assert pp.H_TOOLS in second and "15.11" in second
    first_reply, answer = agents(h)
    assert first_reply["status"] == "dropped" and first_reply["note"] == "запрос к Meet"
    assert answer["text"] == "В плане запуск 15.11"


def test_toolless_model_searches_and_lists(tmp_path):
    kb = _kb(tmp_path)
    runner = FakeRunner([
        AgentReply(text='{"search": {"query": "владелец этапа"}}\n{"list": ""}\n{"read": ["Личное/секрет.md"]}'),
        SILENT])
    h = _make(tmp_path, provider="openai-compatible", runner=runner, kb=kb)

    async def main():
        await h.p.post_user_message("кто владелец?")
        await h.p.tick()
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    second = runner.calls[1][0]
    assert "План запуска.md" in second and "владелец" in second   # search
    assert "Проекты/ — документов: 1" in second                    # list
    assert "пароль" not in second and "Личное" in second             # исключение — отказ
    results = [m for m in h.chat.messages() if m["kind"] == "tool" and m["event"] == "result"]
    assert len(results) == 3 and results[2]["error"]


def test_tool_loop_is_bounded_when_the_model_never_stops_asking(tmp_path):
    read = AgentReply(text='{"read": ["Проекты/План запуска.md"]}')
    runner = FakeRunner([read] * 50)
    h = _make(tmp_path, provider="openai-compatible", runner=runner, kb=_kb(tmp_path))

    async def main():
        await h.p.post_user_message("читай")
        h.clock.t = 100
        for _ in range(20):                    # время стоит: только «сразу»
            await h.p.tick()
        assert len(runner.calls) == 4          # ход + 3 запроса подряд, дальше — тишина
        assert not h.p.turn_due()
        publish(h, 50, "Олег", "новая реплика", at=101)
        assert not h.p.turn_due(105)           # пауза после лимита (10 с)
        assert h.p.turn_due(111)
        h.clock.t = 111
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    results = [m for m in h.chat.messages() if m["kind"] == "tool" and m["event"] == "result"]
    assert [r.get("error") for r in results][:4] == [None, None, None, TOOLS_SPENT]
    assert TOOLS_SPENT in runner.calls[4][0]   # заметкой в следующий нужный ход
    assert pp.H_TOOLS not in runner.calls[4][0]  # не ответ на запрос, а заметка


def test_requests_are_ignored_when_the_model_has_tools(tmp_path):
    h = _make(tmp_path, script=[AgentReply(text='{"read": ["Проекты/План запуска.md"]}')])

    async def main():
        publish(h, 5, "Олег", "реплика")
        h.clock.t = 10
        await h.p.tick()
        assert not h.p.turn_due()
        await h.p.shutdown()

    run(main())
    assert not [m for m in h.chat.messages() if m["kind"] == "tool"]
    assert any("пропущены" in line for line in h.logs)
    assert agents(h)[0]["status"] == "dropped"


# --- id сеанса, частота, восстановление ---


def test_session_id_is_reread_after_every_send(tmp_path):
    def forked(conv, _text):
        conv.session_id = "sess-fork"   # ветка после отвергнутой картинки
        return SILENT

    h = _make(tmp_path, script=[SILENT, forked])

    async def main():
        publish(h, 5, "Олег", "раз")
        h.clock.t = 10
        await h.p.tick()
        assert h.chat.session_id("claude-code") == "sess-1"
        publish(h, 12, "Олег", "два", at=12)
        h.clock.t = 30
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert h.chat.session_id("claude-code") == "sess-fork"


def test_frequency_change_reaches_the_agent(tmp_path):
    runner = FakeRunner([SILENT, SILENT])
    h = _make(tmp_path, provider="openai-compatible", runner=runner)

    async def main():
        assert h.p.set_frequency("less") == "реже"
        assert h.p.set_frequency("реже") == "реже"   # то же — без новой пометки
        publish(h, 5, "Олег", "раз")
        h.clock.t = 10
        await h.p.tick()
        publish(h, 12, "Олег", "два", at=12)
        h.clock.t = 30
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    (p1, k1), (p2, _k2) = runner.calls
    assert "сменил «Как часто писать» на «реже»" in p1
    assert "сменил «Как часто писать»" not in p2
    assert "# Как часто писать: «реже»" in k1["system_prompt"]
    assert h.p.view()["frequency"] == "реже"
    assert any(name == "agent" and data["frequency"] == "реже" for name, data in h.events)


def test_crash_recovery_closes_interrupted_replies(tmp_path):
    folder = tmp_path / "lib" / "2026-10-07_10-00"
    folder.mkdir(parents=True)
    ChatLog(folder, log=lambda _m: None).begin_reply(mode="proactive")
    h = _make(tmp_path, folder=folder)

    async def main():
        await h.p.start()
        await h.p.shutdown()

    run(main())
    (reply,) = agents(h)
    assert reply["status"] == "cancelled" and reply["error"]
    assert any(name == "chat" and d.get("op") == "patch" for name, d in h.events)


def test_provider_error_backs_off_and_keeps_the_lines(tmp_path):
    h = _make(tmp_path, script=[AgentReply(text="", error="сеть недоступна"), say("Теперь ок")])

    async def main():
        publish(h, 5, "Олег", "важная реплика")
        h.clock.t = 10
        await h.p.tick()
        assert h.p.view()["state"] == "error"
        assert not h.p.turn_due(15)           # пауза после сбоя
        assert h.p.turn_due(21)
        h.clock.t = 21
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    failed, ok = agents(h)
    assert failed["status"] == "dropped" and failed["error"] == "сеть недоступна"
    assert ok["text"] == "Теперь ок" and "важная реплика" in h.made[0].sent[1][0]
    assert h.p.view()["state"] == "listening"


def test_run_loop_feeds_the_agent_and_stops_cleanly(tmp_path):
    import time

    h = _make(tmp_path, script=[say("Слышу")], clock=time.monotonic, pause_s=0.05, min_gap_s=0.0)

    async def main():
        stop = asyncio.Event()
        task = asyncio.ensure_future(h.p.run(stop))
        await asyncio.sleep(0.05)
        h.bus.publish("[00:00:05] Олег: начинаем", {"t": 5.0, "speaker": "Олег", "text": "начинаем"})
        await _wait_for(lambda: any(m["status"] == "shown" for m in agents(h)))
        stop.set()
        await asyncio.wait_for(task, 5)

    run(main())
    assert agents(h)[0]["text"] == "Слышу" and h.made[0].closed


def test_snapshot_has_the_feed_and_the_agent(tmp_path):
    h = _make(tmp_path, script=[SILENT, say("Видно")])

    async def main():
        publish(h, 5, "Олег", "раз")
        h.clock.t = 10
        await h.p.tick()
        await h.p.post_user_message("привет")
        await h.p.tick()
        snap = await h.p.snapshot()
        await h.p.shutdown()
        return snap

    snap = run(main())
    texts = [m.get("text") for m in snap["chat"]["messages"]]
    assert texts == ["привет", "Видно"]            # скрытая реплика — не в ленте
    assert snap["agent"]["provider"] == "claude-code" and snap["partial"] is None


def test_partial_text_shows_only_what_is_said():
    assert partial_text('{"say": "Привет, \\"мир') == 'Привет, "мир'
    assert partial_text('{"say": "Раз"}\n{"say": "Два') == "Раз\n\nДва"
    assert partial_text('{"silent": true}') == ""
    assert partial_text('{"re') == ""
    assert partial_text("Просто текст") == "Просто текст"
    assert partial_text('<think>думаю</think>{"say": "Да"}') == "Да"


# --- подключение в run_assist ---


def _wired(tmp_path, monkeypatch, cfg):
    from test_assist_app import _Heavy, _run

    from meet.assist import app as app_mod

    async def done(stop):
        return None

    async def codex_runner(prompt, **kw):
        return AgentReply(text="ok")

    heavy = _Heavy(monkeypatch, resolved=("codex", codex_runner), digester_run=done)
    seen = {}
    real_state = app_mod.AssistState

    def capture(**kw):
        seen["state"] = real_state(**kw)
        return seen["state"]

    monkeypatch.setattr(app_mod, "AssistState", capture)
    _run(tmp_path, open_browser=False, port=0, cfg=cfg)
    return heavy, seen["state"]


def test_participant_is_on_by_default_now_that_the_window_has_the_chat(tmp_path, monkeypatch):
    heavy, state = _wired(tmp_path, monkeypatch, Settings.from_raw({}))
    assert state.participant is not None and state.qa is None and heavy.qa_kwargs is None


def test_participant_off_brings_back_the_old_assistant(tmp_path, monkeypatch):
    heavy, state = _wired(tmp_path, monkeypatch, Settings.from_raw({"assist": {"participant": False}}))
    assert state.participant is None and heavy.qa_kwargs is not None


def test_participant_on_keeps_the_summary_lane(tmp_path, monkeypatch):
    heavy, state = _wired(tmp_path, monkeypatch, Settings.from_raw({"assist": {"participant": True}}))
    assert state.participant is not None and state.qa is None and heavy.qa_kwargs is None
    cadence = heavy.digester_kwargs["cadence"]
    assert cadence.hints is False and heavy.live.hints_enabled is False
    assert heavy.digester_kwargs["system_prompt"] == state.digester_system   # сводка — та же
    agent = state.view()["agent"]
    assert agent["provider"] == "codex" and agent["frequency"] == "чаще"
    assert agent["vision"] is True and agent["deny_enforced"] is False and agent["tools"] is True


def test_participant_off_keeps_the_old_assistant(tmp_path, monkeypatch):
    cfg = Settings.from_raw({"assist": {"participant": False}})
    heavy, state = _wired(tmp_path, monkeypatch, cfg)
    assert state.participant is None and heavy.qa_kwargs is not None
    assert heavy.digester_kwargs["cadence"].hints is True and heavy.live.hints_enabled is True
    assert "agent" not in state.view()


def test_summary_lane_still_ticks_with_hints_off():
    from meet.assist.digester import Digester, cadence_for
    from meet.assist.live_state import LiveState

    bus, live = TranscriptBus(), LiveState(hints_enabled=False)
    calls = []

    async def runner(prompt, **kw):
        calls.append(prompt)
        return AgentReply(text='{"op": "topic", "text": "Запуск"}')

    d = Digester(bus, live, system_prompt="сводка", runner=runner,
                 cadence=replace(cadence_for("calm"), hints=False), clock=Clock(), log=lambda _m: None)
    for i in range(30):
        bus.publish(f"[00:00:{i:02d}] Олег: запуск переносим на среду, все согласны {i}",
                    {"t": float(i * 3), "end": float(i * 3 + 3), "speaker": "Олег",
                     "text": f"запуск переносим на среду, все согласны {i}"})
    assert not asyncio.run(d.hints_once())       # подсказок нет
    assert d.summary_due() and asyncio.run(d.summary_once())
    assert len(calls) == 1 and live.topic == "Запуск"


def test_settings_have_participant_and_frequency():
    a = Settings.from_raw({}).assist
    assert a.participant is True and a.frequency == "more"
    assert Settings.from_raw({"assist": {"participant": True}}).assist.participant is True
    b = Settings.from_raw({"assist": {"participant": False, "frequency": "less"}}).assist
    assert b.participant is False and b.frequency == "less"
    assert b.to_raw()["participant"] is False and b.to_raw()["frequency"] == "less"
    assert Settings.from_raw({"assist": {"frequency": "always"}}).assist.frequency == "more"


def test_configured_model_reaches_the_participant(tmp_path):
    from meet.assist import app as app_mod

    cfg = Settings.from_raw({"llm": {"model": "opus"}, "assist": {"frequency": "normal"}})
    folder = tmp_path / "rec" / "2026-10-07_10-00"
    p = app_mod._make_participant(cfg, TranscriptBus(), folder, "claude-code", None)
    assert p._model == "opus" and p.label == "Claude Code (opus)"
    assert p.frequency == "обычно" and p._library_root == folder.parent


# --- раунд исправлений 1 ---


def test_shutdown_closes_a_reply_left_in_writing(tmp_path):
    h = _make(tmp_path)

    async def main():
        await h.p.start()
        # Ход отменили, пока begin_reply писался: записан, а id у хода нет.
        h.chat.begin_reply(mode="proactive")
        await h.p.shutdown()

    run(main())
    (reply,) = agents(h)
    assert reply["status"] == "cancelled" and reply["error"] == "ассистент остановлен"


def test_shutdown_mid_turn_cancels_and_closes_the_process(tmp_path):
    h = _make(tmp_path, script=["block"])

    async def main():
        publish(h, 5, "Олег", "реплика")
        h.clock.t = 10
        turn = asyncio.ensure_future(h.p.tick())
        await _wait_for(lambda: h.made and h.made[0].started.is_set())
        await h.p.shutdown()
        await asyncio.gather(turn, return_exceptions=True)

    run(main())
    (reply,) = agents(h)
    assert reply["status"] == "cancelled" and reply["error"] == "ассистент остановлен"
    assert reply["text"] == "Начинаю" and h.made[0].closed


def test_stop_keeps_a_pending_frequency_note(tmp_path):
    h = _make(tmp_path, script=["block", SILENT])

    async def main():
        h.p.set_frequency("less")
        publish(h, 5, "Олег", "раз")
        h.clock.t = 10
        turn = asyncio.ensure_future(h.p.tick())
        await _wait_for(lambda: h.made and h.made[0].started.is_set())
        await h.p.stop_reply()
        await turn
        publish(h, 20, "Олег", "два", at=20)
        h.clock.t = 40
        assert await h.p.tick()
        await h.p.shutdown()

    run(main())
    second = h.made[0].sent[1][0]
    assert "сменил «Как часто писать» на «реже»" in second and NOTE_STOPPED in second


def test_tool_chain_keeps_answering_the_user(tmp_path):
    runner = FakeRunner([AgentReply(text='{"read": ["Проекты/План запуска.md"]}'), say("15.11")])
    h = _make(tmp_path, provider="openai-compatible", runner=runner, kb=_kb(tmp_path))

    async def main():
        posted = await h.p.post_user_message("какой срок?")
        await h.p.tick()
        await h.p.tick()
        await h.p.shutdown()
        return posted

    posted = run(main())
    request, answer = agents(h)
    assert request["mode"] == "reply" and answer["mode"] == "reply"
    assert answer["re"] == posted["id"]


def test_hidden_duplicate_leaves_no_stale_pause_timer(tmp_path):
    h = _make(tmp_path, script=[SILENT])

    async def main():
        hidden = publish(h, 0, "Олег", "дубль", at=0)
        h.bus.hide([hidden])
        assert not h.p.turn_due(1)
        publish(h, 100, "Олег", "настоящая реплика", at=100)
        assert not h.p.turn_due(100)           # без паузы — нет
        assert h.p.turn_due(104)
        await h.p.shutdown()

    run(main())


def test_seed_shows_journal_attachment_ids_only(tmp_path):
    doc = tmp_path / "План.md"
    doc.write_text("# План\n\nСрок 15.11.\n", encoding="utf-8")
    h = _make(tmp_path, script=[SILENT])
    # Чужой материал раньше занял id: у журнала и материалов счётчики разные.
    h.chat.append("attachment", type="image", name="старое.png", status="ready")

    async def main():
        posted = await h.p.post_user_message("глянь", [str(doc)])
        await h.p.tick()
        await h.p.shutdown()
        return posted

    posted = run(main())
    jid = posted["attachments"][0]
    att = h.chat.get(jid)
    assert att["ref"] != jid
    seed = h.made[0].sent[0][0].split(pp.H_USER)[0]
    assert f"- {jid} «План.md»" in seed and f"{att['ref']} «" not in seed


def test_stop_before_the_reply_starts_drops_the_stopped_message(tmp_path):
    h = _make(tmp_path, script=[SILENT])

    async def main():
        await h.p.start()
        await h.p.post_user_message("не надо")
        publish(h, 5, "Олег", "реплика", at=1)
        turn = h.p._start_turn(1)
        turn.stop = "stop"                      # «Стоп» до begin_reply
        await turn.task
        assert not h.made or not h.made[0].sent
        assert h.p._user == [] and h.p._cursor == 0   # сообщение — нет, реплики — снова в очереди
        await h.p.shutdown()

    run(main())
    assert agents(h) == []


def test_fresh_user_message_bypasses_the_backoff(tmp_path):
    h = _make(tmp_path, script=[AgentReply(text="", error="сеть"), say("Ответ")])

    async def main():
        publish(h, 5, "Олег", "реплика")
        h.clock.t = 10
        await h.p.tick()
        assert not h.p.turn_due(11)
        await h.p.post_user_message("ты тут?")
        assert h.p.turn_due(11)
        h.clock.t = 11
        await h.p.tick()
        await h.p.shutdown()

    run(main())
    assert agents(h)[-1]["text"] == "Ответ"


def test_resume_failed_twice_backs_off_without_a_loop(tmp_path):
    h = _make(tmp_path, script=[resume_failure("нет"), resume_failure("снова нет")])
    h.chat.set_session_id("claude-code", "sess-old")

    async def main():
        publish(h, 5, "Олег", "реплика")
        h.clock.t = 10
        await h.p.tick()
        assert not h.p.turn_due(12)
        await h.p.shutdown()

    run(main())
    assert len(h.made[0].sent) == 2 and h.p.view()["state"] == "error"


def test_run_loop_wakes_on_a_user_message(tmp_path):
    import time

    h = _make(tmp_path, script=[say("На связи")], clock=time.monotonic)

    async def main():
        stop = asyncio.Event()
        task = asyncio.ensure_future(h.p.run(stop))
        await asyncio.sleep(0.05)
        await h.p.post_user_message("ты тут?")
        await _wait_for(lambda: any(m["status"] == "shown" for m in agents(h)))
        stop.set()
        await asyncio.wait_for(task, 5)

    run(main())
    assert agents(h)[0]["text"] == "На связи"
