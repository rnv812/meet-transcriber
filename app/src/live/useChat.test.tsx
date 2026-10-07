import { act, render, screen } from "@testing-library/react";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  postChat: vi.fn(),
  getChat: vi.fn(),
  newChatClientId: vi.fn(() => "c1"),
}));
import { getChat, postChat } from "../lib/api";
import { FakeEventSource } from "../test/setup";
import { agentInfo, agentMsg, userMsg } from "../test/chatFixtures";
import { REVEAL_MS } from "./chatModel";
import { type Chat, useChat } from "./useChat";
import { useLive } from "./useLive";

const ep = { base: "http://h", token: "t" };
const stream = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events")).at(-1)!;
let chat: Chat;

function Host() {
  chat = useChat(ep);
  useLive(ep, true, chat.sink);
  return (
    <ol>
      {chat.items.map((it) => (
        <li key={it.type === "message" ? it.message.id : it.out.client_id}>
          {it.type === "message"
            ? `${it.message.kind}:${it.message.status === "writing" ? `пишет:${chat.state.partial[it.message.id] ?? ""}` : it.message.text}`
            : `черновик:${it.out.text}:${it.out.state}`}
        </li>
      ))}
    </ol>
  );
}
const rows = () => screen.queryAllByRole("listitem").map((li) => li.textContent);

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.useRealTimers());

test("снимок, события по seq, частичный текст и состояние агента — из потока /live/events", () => {
  render(<Host />);
  act(() => stream().emit("chat_snapshot", { messages: [agentMsg("m1", { text: "Привет" })], seq: 4, agent: agentInfo() }));
  expect(rows()).toEqual(["agent:Привет"]);
  act(() => {
    stream().emit("chat", { seq: 4, op: "add", message: agentMsg("m0") }); // уже в снимке
    stream().emit("chat", { seq: 5, op: "add", message: agentMsg("m2", { status: "writing", text: "" }) });
    stream().emit("chat_partial", { id: "m2", text: "Смотрю" });
  });
  expect(rows()).toEqual(["agent:Привет", "agent:пишет:Смотрю"]);
  act(() => stream().emit("chat", { seq: 6, op: "patch", id: "m2", set: { status: "shown", text: "Смотрю план" } }));
  expect(rows()).toEqual(["agent:Привет", "agent:Смотрю план"]);
  act(() => stream().emit("agent", agentInfo({ state: "writing" })));
  expect(chat.agent?.state).toBe("writing");
});

test("молчаливый ход по расшифровке не показывает «Пишет…» ни на миг", () => {
  vi.useFakeTimers();
  render(<Host />);
  act(() => stream().emit("chat_snapshot", { messages: [], seq: 1, agent: agentInfo() }));
  act(() => stream().emit("chat", { seq: 2, op: "add", message: agentMsg("m1", { status: "writing", mode: "proactive", text: "" }) }));
  expect(rows()).toEqual([]);
  act(() => { vi.advanceTimersByTime(5000); });
  expect(rows()).toEqual([]);
  act(() => stream().emit("chat", { seq: 3, op: "patch", id: "m1", set: { status: "dropped" } }));
  expect(rows()).toEqual([]);
});

test("ответ человеку без текста — пузырь «Пишет…» через REVEAL_MS", () => {
  vi.useFakeTimers();
  render(<Host />);
  act(() => stream().emit("chat_snapshot", { messages: [], seq: 1, agent: agentInfo() }));
  act(() => stream().emit("chat", { seq: 2, op: "add", message: agentMsg("m1", { status: "writing", mode: "reply", text: "" }) }));
  expect(rows()).toEqual([]);
  act(() => { vi.advanceTimersByTime(REVEAL_MS - 100); });
  expect(rows()).toEqual([]);
  act(() => { vi.advanceTimersByTime(200); });
  expect(rows()).toEqual(["agent:пишет:"]);
  expect(chat.writing).toBe("m1");
});

test("сообщение видно сразу; не дошло — «failed», повтор с тем же client_id; запись журнала заменяет черновик", async () => {
  vi.mocked(postChat).mockRejectedValueOnce(new Error("нет связи")).mockResolvedValueOnce({ id: "m2", queued: false, attachments: [] });
  render(<Host />);
  act(() => stream().emit("chat_snapshot", { messages: [], seq: 1, agent: agentInfo() }));
  await act(async () => { await chat.send("что с бюджетом?"); });
  expect(rows()).toEqual(["черновик:что с бюджетом?:failed"]);
  await act(async () => { await chat.retry("c1"); });
  expect(vi.mocked(postChat).mock.calls.map(([, m]) => m.client_id)).toEqual(["c1", "c1"]);
  expect(rows()).toEqual(["черновик:что с бюджетом?:sent"]);
  act(() => stream().emit("chat", { seq: 2, op: "add", message: userMsg("m2", { client_id: "c1", text: "что с бюджетом?" }) }));
  expect(rows()).toEqual(["user:что с бюджетом?"]);
});

test("переподключение: новый снимок заменяет ленту, отправленное в обрыв сверяется по client_id", async () => {
  vi.mocked(postChat).mockReturnValue(new Promise(() => {})); // ответа нет — связь пропала
  render(<Host />);
  act(() => stream().emit("chat_snapshot", { messages: [agentMsg("m1", { text: "Раз" })], seq: 3, agent: agentInfo() }));
  await act(async () => { void chat.send("ещё вопрос"); });
  act(() => stream().fail(true));
  await act(async () => { await new Promise((r) => setTimeout(r, 1100)); });
  act(() => stream().emit("chat_snapshot", {
    messages: [agentMsg("m1", { text: "Раз" }), userMsg("m2", { client_id: "c1", text: "ещё вопрос" }), agentMsg("m3", { text: "Два" })],
    seq: 9, agent: agentInfo(),
  }));
  expect(rows()).toEqual(["agent:Раз", "user:ещё вопрос", "agent:Два"]);
});

test("правка неизвестной записи — лента перечитывается getChat", async () => {
  vi.mocked(getChat).mockResolvedValue({ messages: [agentMsg("m7", { text: "Вернулось" })], seq: 12 });
  render(<Host />);
  act(() => stream().emit("chat_snapshot", { messages: [], seq: 1, agent: agentInfo() }));
  await act(async () => { stream().emit("chat", { seq: 2, op: "patch", id: "m7", set: { status: "shown" } }); });
  expect(getChat).toHaveBeenCalledWith(ep);
  expect(rows()).toEqual(["agent:Вернулось"]);
});
