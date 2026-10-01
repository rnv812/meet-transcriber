import { ApiError, deleteRecording, avatarUrl, saveTranscript, nameSpeakers, putHotwords } from "./api";
import * as api from "./api";
import { FakeEventSource } from "../test/setup";

test("ошибка резидента приходит текстом", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ error: "идёт расшифровка — отмените её или дождитесь" }), { status: 400 }));
  await expect(deleteRecording({ base: "http://127.0.0.1:1", token: "t" }, "x"))
    .rejects.toEqual(new ApiError(400, "идёт расшифровка — отмените её или дождитесь"));
});
test("аватар с токеном и версией", () => {
  expect(avatarUrl({ base: "http://127.0.0.1:9", token: "t k" }, "Демьян", 3))
    .toBe("http://127.0.0.1:9/voices/%D0%94%D0%B5%D0%BC%D1%8C%D1%8F%D0%BD/avatar?token=t%20k&v=3");
});
test("saveTranscript: PUT с телом-транскриптом", async () => {
  const f = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
  globalThis.fetch = f;
  const t = { version: 1, title: null, segments: [] };
  await saveTranscript({ base: "http://h", token: "t" }, "a b", t);
  expect(f.mock.calls[0]![0]).toBe("http://h/recordings/a%20b/transcript");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "PUT", body: JSON.stringify(t) });
});

const ep = { base: "http://h", token: "t" };
const okFetch = () => {
  const f = vi.fn().mockImplementation(async () => new Response("{}", { status: 200 }));
  globalThis.fetch = f;
  return f;
};

test("nameSpeakers: тело — сам словарь", async () => {
  const f = okFetch();
  await nameSpeakers(ep, "a b", { SPEAKER_00: "Демьян" });
  expect(f.mock.calls[0]![0]).toBe("http://h/recordings/a%20b/speakers");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "POST", body: JSON.stringify({ SPEAKER_00: "Демьян" }) });
});

test("putHotwords: PUT {text}", async () => {
  const f = okFetch();
  await putHotwords(ep, "Вася, Петя");
  expect(f.mock.calls[0]![0]).toBe("http://h/hotwords");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "PUT", body: JSON.stringify({ text: "Вася, Петя" }) });
});

test("ask: POST {question} на запись", async () => {
  const f = okFetch();
  await api.ask(ep, "a b", "что решили?");
  expect(f.mock.calls[0]![0]).toBe("http://h/recordings/a%20b/ask");
  expect(f.mock.calls[0]![1]).toMatchObject({ method: "POST", body: JSON.stringify({ question: "что решили?" }) });
});

test("итоги, вопросы, заметки, ассистент: адреса и методы", async () => {
  const f = okFetch();
  await api.makeSummary(ep, "r1");
  await api.getSummary(ep, "r1");
  await api.getQa(ep, "r1");
  await api.toNotes(ep, "r1");
  await api.getAssistant(ep);
  await api.checkProvider(ep, "codex");
  const calls = f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method ?? "GET"]);
  expect(calls).toEqual([
    ["http://h/recordings/r1/summary", "POST"],
    ["http://h/recordings/r1/summary", "GET"],
    ["http://h/recordings/r1/qa", "GET"],
    ["http://h/recordings/r1/notes", "POST"],
    ["http://h/assistant", "GET"],
    ["http://h/assistant/check", "POST"],
  ]);
  expect(f.mock.calls[5]![1]).toMatchObject({ body: JSON.stringify({ provider: "codex" }) });
});

test("409 без провайдера приходит текстом", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ error: "Подключите Claude Code или Codex в настройках" }), { status: 409 }));
  await expect(api.makeSummary(ep, "r1"))
    .rejects.toEqual(new ApiError(409, "Подключите Claude Code или Codex в настройках"));
});

test("живой режим: start/stop/ask/task", async () => {
  const f = okFetch();
  await api.liveStart(ep);
  await api.liveStop(ep);
  await api.liveAsk(ep, "кто за что?");
  await api.liveTask(ep, "релиз 2.0");
  expect(f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method, (init as RequestInit).body]))
    .toEqual([
      ["http://h/live/start", "POST", undefined],
      ["http://h/live/stop", "POST", undefined],
      ["http://h/live/ask", "POST", JSON.stringify({ question: "кто за что?" })],
      ["http://h/live/task", "POST", JSON.stringify({ task: "релиз 2.0" })],
    ]);
});

test("openLiveEvents: токен в query, state и line разбираются, close закрывает", () => {
  const onState = vi.fn();
  const onLine = vi.fn();
  const live = api.openLiveEvents({ base: "http://h", token: "t k" }, { onState, onLine });
  const source = FakeEventSource.instances.at(-1)!;
  expect(source.url).toBe("http://h/live/events?token=t%20k");
  source.emit("state", { digest: "# Д", transcript: ["[00:01] Демьян: привет"], status: null });
  source.emit("line", { t: 1.5, speaker: "Демьян", text: "привет" });
  expect(onState).toHaveBeenCalledWith({ digest: "# Д", transcript: ["[00:01] Демьян: привет"], status: null });
  expect(onLine).toHaveBeenCalledWith({ t: 1.5, speaker: "Демьян", text: "привет" });
  live.close();
  expect(source.closed).toBe(true);
});
