import * as api from "./api";
import type { AgentInfo, ChatEvent, ChatSnapshot } from "./api";
import { FakeEventSource } from "../test/setup";

const ep = { base: "http://h", token: "t" };

function okFetch(payload: unknown = {}) {
  const f = vi.fn().mockImplementation(async () => new Response(JSON.stringify(payload), { status: 200 }));
  globalThis.fetch = f;
  return f;
}

const calls = (f: ReturnType<typeof vi.fn>) =>
  f.mock.calls.map(([url, init]) => [url, (init as RequestInit).method ?? "GET", (init as RequestInit).body]);

test("чат идущей встречи: адреса, методы и тела", async () => {
  const f = okFetch();
  await api.getChat(ep);
  await api.getChat(ep, 50);
  await api.postChat(ep, { text: "Что с бюджетом?", client_id: "c-1" });
  await api.postChat(ep, { text: "вот", client_id: "c-2", attachments: ["a1"] });
  await api.attachChatFile(ep, "C:\\Документы\\План.pptx");
  await api.clickChat(ep, "m3", "Глянь");
  await api.clickChat(ep, "m3", "Не надо", "k-1");
  await api.reactChat(ep, "m3", "👍");
  await api.reactChat(ep, "m3", "❓", false);
  await api.stopChat(ep);
  await api.stopChat(ep, "m4");
  await api.setAgentFrequency(ep, "less");
  expect(calls(f)).toEqual([
    ["http://h/live/chat", "GET", undefined],
    ["http://h/live/chat?limit=50", "GET", undefined],
    ["http://h/live/chat", "POST", JSON.stringify({ text: "Что с бюджетом?", client_id: "c-1" })],
    ["http://h/live/chat", "POST", JSON.stringify({ text: "вот", client_id: "c-2", attachments: ["a1"] })],
    ["http://h/live/chat/attach", "POST", JSON.stringify({ path: "C:\\Документы\\План.pptx" })],
    ["http://h/live/chat/m3/click", "POST", JSON.stringify({ label: "Глянь" })],
    ["http://h/live/chat/m3/click", "POST", JSON.stringify({ label: "Не надо", client_id: "k-1" })],
    ["http://h/live/chat/m3/react", "POST", JSON.stringify({ emoji: "👍" })],
    ["http://h/live/chat/m3/react", "POST", JSON.stringify({ emoji: "❓", on: false })],
    ["http://h/live/chat/stop", "POST", "{}"],
    ["http://h/live/chat/stop", "POST", JSON.stringify({ id: "m4" })],
    ["http://h/agent/frequency", "PUT", JSON.stringify({ frequency: "less" })],
  ]);
  const headers = (f.mock.calls[2]![1] as RequestInit).headers as Record<string, string>;
  expect(headers).toMatchObject({ Authorization: "Bearer t", "Content-Type": "application/json" });
});

test("профиль сессии: старт и подключение с профилем, смена по ходу — PUT /live/profile", async () => {
  const f = okFetch({ ok: true });
  await api.liveStart(ep);
  await api.liveStart(ep, { profile: "personal" });
  await api.liveAttach(ep);
  await api.liveAttach(ep, "work");
  await api.setAgentProfile(ep, "personal");
  expect(calls(f)).toEqual([
    ["http://h/live/start", "POST", undefined],
    ["http://h/live/start", "POST", JSON.stringify({ profile: "personal" })],
    ["http://h/live/attach", "POST", undefined],
    ["http://h/live/attach", "POST", JSON.stringify({ profile: "work" })],
    ["http://h/live/profile", "PUT", JSON.stringify({ profile: "personal" })],
  ]);
});

test("pasteChatImage: сырое тело с типом картинки и именем в X-File-Name", async () => {
  const f = okFetch({ id: "a2", status: "ready", attachment: { id: "a2", kind: "attachment" } });
  const blob = new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });
  const result = await api.pasteChatImage(ep, blob, "скрин 1.png");
  expect(result.id).toBe("a2");
  const [url, init] = f.mock.calls[0]! as [string, RequestInit];
  expect(url).toBe("http://h/live/chat/paste");
  expect(init.method).toBe("POST");
  expect(init.body).toBe(blob);
  expect(init.headers).toEqual({
    Authorization: "Bearer t", "Content-Type": "image/png", "X-File-Name": encodeURIComponent("скрин 1.png"),
  });
  await api.pasteChatImage({ base: "/api", token: null }, new Blob(["x"]));
  const second = f.mock.calls[1]![1] as RequestInit;
  expect(second.headers).toEqual({ "Content-Type": "image/png" });
});

test("после встречи: чат записи и «Продолжить разговор»", async () => {
  const f = okFetch({ messages: [], seq: 0, legacy: null, live: false, job: null });
  const chat = await api.getRecordingChat(ep, "2026-10-07 10-00");
  expect(chat.legacy).toBeNull();
  await api.continueChat(ep, "r1", { text: "А сроки?", client_id: "c-3" });
  await api.continueChat(ep, "r1", { text: "и это", client_id: "c-4", attachments: ["a1"], provider: "codex" });
  expect(calls(f)).toEqual([
    ["http://h/recordings/2026-10-07%2010-00/chat", "GET", undefined],
    ["http://h/recordings/r1/chat", "POST", JSON.stringify({ text: "А сроки?", client_id: "c-3" })],
    ["http://h/recordings/r1/chat", "POST",
      JSON.stringify({ text: "и это", client_id: "c-4", attachments: ["a1"], provider: "codex" })],
  ]);
});

test("ошибки чата приходят текстом: 409 без агента, 400 на негодное", async () => {
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ error: "Ассистент не запущен" }), { status: 409 }));
  await expect(api.postChat(ep, { text: "a", client_id: "c" }))
    .rejects.toEqual(new api.ApiError(409, "Ассистент не запущен"));
});

test("newChatClientId: разные id", () => {
  const a = api.newChatClientId();
  const b = api.newChatClientId();
  expect(a).not.toBe(b);
  expect(a.length).toBeGreaterThan(8);
});

const agent: AgentInfo = {
  state: "writing", error: null, provider: "codex", label: "Codex", vision: true, tools: true,
  deny_enforced: false, frequency: "чаще", session: "new", writing: "m5",
  sees: { conversation: true, kb: false, materials: 0, images: 0 },
};

test("openLiveEvents: chat_snapshot, chat, chat_partial, agent; мусор отбрасывается", () => {
  const onChatSnapshot = vi.fn();
  const onChat = vi.fn();
  const onChatPartial = vi.fn();
  const onAgent = vi.fn();
  api.openLiveEvents(ep, { onChatSnapshot, onChat, onChatPartial, onAgent });
  const source = FakeEventSource.instances.at(-1)!;
  const snap: ChatSnapshot = {
    messages: [{ id: "m1", seq: 1, at: 1, kind: "user", text: "привет" }], seq: 1, agent, partial: null,
  };
  source.emit("chat_snapshot", snap);
  source.emit("chat_snapshot", { messages: "нет" });
  expect(onChatSnapshot).toHaveBeenCalledTimes(1);
  expect(onChatSnapshot).toHaveBeenCalledWith(snap);
  const add: ChatEvent = { seq: 2, op: "add", message: { id: "m2", seq: 2, at: 2, kind: "agent", status: "writing" } };
  const patch: ChatEvent = { seq: 3, op: "patch", id: "m2", set: { status: "shown", text: "ок" } };
  source.emit("chat", add);
  source.emit("chat", patch);
  source.emit("chat", { op: "add" });
  source.emit("chat", { seq: 4, op: "patch", set: {} });
  expect(onChat.mock.calls).toEqual([[add], [patch]]);
  source.emit("chat_partial", { id: "m2", text: "Пиш" });
  source.emit("chat_partial", { id: 5, text: "x" });
  expect(onChatPartial.mock.calls).toEqual([[{ id: "m2", text: "Пиш" }]]);
  source.emit("agent", agent);
  source.emit("agent", { nope: true });
  expect(onAgent.mock.calls).toEqual([[agent]]);
});

test("openEvents: chat.updated приходит событием резидента", () => {
  const onEvent = vi.fn();
  api.openEvents(ep, { onEvent });
  const source = FakeEventSource.instances.at(-1)!;
  source.emit("chat.updated", { kind: "chat.updated", at: 1, id: "r1", partial: { id: "m2", text: "При" } });
  expect(onEvent).toHaveBeenCalledWith({ kind: "chat.updated", at: 1, id: "r1", partial: { id: "m2", text: "При" } });
});

test("вложение убрали до отправки: POST /live/chat/attachments/{id}/remove", async () => {
  const f = okFetch({ ok: true, changed: true });
  expect(await api.removeChatAttachment(ep, "a3")).toEqual({ ok: true, changed: true });
  expect(calls(f)).toEqual([["http://h/live/chat/attachments/a3/remove", "POST", "{}"]]);
});
