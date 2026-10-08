import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/** Остатки ревью live-chat, сделанные в задаче 8: M6, M7, M12–M15. */

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  getChat: vi.fn(),
  getState: vi.fn(),
  liveStop: vi.fn(),
  pasteChatImage: vi.fn(),
  cancelVoicePress: vi.fn(),
  newChatClientId: vi.fn(() => "c1"),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  invoke: vi.fn(),
  onLiveWindow: vi.fn(async () => () => {}),
  onFileDrop: vi.fn(async () => () => {}),
}));
import { cancelVoicePress, getChat, getState, pasteChatImage } from "../lib/api";
import { invoke } from "../lib/shell";
import type { ChatMessage, ChatSnapshot, LiveStatus, Snapshot } from "../lib/types";
import { agentInfo, agentMsg, attMsg, userMsg } from "../test/chatFixtures";
import { FakeEventSource } from "../test/setup";
import { LiveChat } from "./LiveChat";
import { LivePanel } from "./LivePanel";
import { type Chat, HISTORY_LIMIT, PREVIEW_MAX, SNAPSHOT_LIMIT, useChat } from "./useChat";

const ep = { base: "http://h", token: "t" };
let chat: Chat;

function Host({ compact = false, quiet = false }: { compact?: boolean; quiet?: boolean }) {
  chat = useChat(ep);
  return <LiveChat chat={chat} compact={compact} quiet={quiet} />;
}
const load = (messages: ChatMessage[], seq = 50) =>
  act(() => chat.sink.onChatSnapshot({ messages, seq, agent: agentInfo() } as ChatSnapshot));
const log = () => screen.getByRole("log", { name: "Чат с ассистентом" });
const rows = () => Array.from(log().querySelectorAll<HTMLElement>(":scope > li[data-key]"));
/** Кнопка дочитки. Лента в 200 сообщений: getByRole считал бы имя у каждого элемента (секунды на запрос). */
const more = () => document.querySelector<HTMLButtonElement>("button.chat__more");
const moreLabel = "Показать более ранние сообщения";
const clickMore = async () => {
  const b = more();
  expect(b).toHaveTextContent(moreLabel);
  await act(async () => { fireEvent.click(b!); });
};
const hasText = (s: string) => Array.from(log().querySelectorAll("li")).some((li) => li.textContent?.includes(s));

beforeEach(() => vi.clearAllMocks());

test("M12: в порядке Tab — одно сообщение и его действия; ↑ / ↓ / Home / End — между сообщениями", async () => {
  render(<Host />);
  load([
    agentMsg("m1", { text: "Первое", buttons: ["Да"] }),
    userMsg("m2", { text: "Ответ" }),
    agentMsg("m3", { text: "Третье", buttons: ["Глянь", "Не надо"] }),
  ]);
  const [first, second, third] = rows();
  expect(third).toHaveAttribute("tabindex", "0");
  expect(first).toHaveAttribute("tabindex", "-1");
  expect(within(third!).getByRole("button", { name: "Глянь" })).toHaveAttribute("tabindex", "0");
  expect(within(third!).getByRole("button", { name: "Полезно" })).toHaveAttribute("tabindex", "0");
  expect(within(first!).getByRole("button", { name: "Да" })).toHaveAttribute("tabindex", "-1");
  expect(within(first!).getByRole("button", { name: "Копировать" })).toHaveAttribute("tabindex", "-1");
  act(() => third!.focus());
  fireEvent.keyDown(third!, { key: "ArrowUp" });
  expect(second).toHaveFocus();
  fireEvent.keyDown(second!, { key: "Home" });
  expect(first).toHaveFocus();
  expect(first).toHaveAttribute("tabindex", "0");
  expect(within(first!).getByRole("button", { name: "Да" })).toHaveAttribute("tabindex", "0");
  expect(within(third!).getByRole("button", { name: "Глянь" })).toHaveAttribute("tabindex", "-1");
  // С кнопки сообщения стрелка тоже ведёт к соседнему сообщению.
  act(() => within(first!).getByRole("button", { name: "Да" }).focus());
  fireEvent.keyDown(within(first!).getByRole("button", { name: "Да" }), { key: "ArrowDown" });
  expect(second).toHaveFocus();
  fireEvent.keyDown(second!, { key: "End" });
  expect(third).toHaveFocus();
});

test("M12: щелчок по действию другого сообщения переносит туда остановку Tab", async () => {
  render(<Host />);
  load([agentMsg("m1", { text: "Первое" }), agentMsg("m2", { text: "Второе" })]);
  const [first, second] = rows();
  await userEvent.click(within(first!).getByRole("button", { name: "Полезно" }));
  expect(first).toHaveAttribute("tabindex", "0");
  expect(second).toHaveAttribute("tabindex", "-1");
});

test("M7: узкая панель — время в подсказке, вложения счётчиком «скрепка N»", () => {
  render(<Host compact />);
  load([
    attMsg("a1", { name: "Скриншот.png" }), attMsg("a2", { type: "doc", name: "План.pptx" }),
    agentMsg("m1", { text: "Вижу", t: 65 }),
    userMsg("m2", { text: "Смотри", attachments: ["a1", "a2"], t: 70 }),
  ]);
  const [agent, user] = rows();
  // Время — подсказкой Aurora (ui/Tip) и описанием для диктора, не системным title.
  expect(agent).toHaveAttribute("aria-description", "01:05");
  expect(agent).not.toHaveAttribute("title");
  expect(within(agent!).queryByText("01:05")).toBeNull();
  expect(user).toHaveAttribute("aria-description", "01:10");
  // Счётчик — значок скрепки Lucide и число (без эмодзи).
  const count = within(user!).getByLabelText("Вложения: Скриншот.png, План.pptx");
  expect(count).toHaveTextContent(/^2$/);
  expect(count.querySelector("svg")).not.toBeNull();
  expect(within(user!).queryByText("План.pptx")).toBeNull();
});

test("M13: снимок обрезан — «Показать более ранние сообщения» дочитывает ленту", async () => {
  const many = Array.from({ length: SNAPSHOT_LIMIT }, (_, k) => agentMsg(`m${k + 10}`, { text: `Сообщение ${k + 10}` }));
  const older = [agentMsg("m1", { text: "Самое первое" }), ...many];
  vi.mocked(getChat).mockResolvedValue({ messages: older, seq: 300 });
  render(<Host />);
  load(many, 300);
  await clickMore();
  expect(getChat).toHaveBeenCalledWith(ep, HISTORY_LIMIT);
  await vi.waitFor(() => expect(hasText("Самое первое")).toBe(true));
  expect(more()).toBeNull();
});

test("M13: короткая лента — кнопки нет", () => {
  render(<Host />);
  load([agentMsg("m1")]);
  expect(more()).toBeNull();
});

test("M14: миниатюр вставленных картинок не больше PREVIEW_MAX — старые отзываются", async () => {
  const created: string[] = [];
  const revoked: string[] = [];
  const real = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
  URL.createObjectURL = (() => {
    created.push(`blob:${created.length}`);
    return created.at(-1)!;
  }) as never;
  URL.revokeObjectURL = ((u: string) => { revoked.push(u); }) as never;
  let n = 0;
  vi.mocked(pasteChatImage).mockImplementation(async () => ({ id: `a${++n}`, status: "ready", attachment: attMsg(`a${n}`) }) as never);
  render(<Host />);
  load([]);
  for (let k = 0; k < PREVIEW_MAX + 2; k++) await act(async () => { await chat.paste(new Blob(["x"], { type: "image/png" })); });
  expect(created).toHaveLength(PREVIEW_MAX + 2);
  expect(revoked).toEqual(["blob:0", "blob:1"]);
  expect(chat.preview("a1")).toBeUndefined();
  expect(chat.preview(`a${PREVIEW_MAX + 2}`)).toBe(`blob:${PREVIEW_MAX + 1}`);
  URL.createObjectURL = real.create;
  URL.revokeObjectURL = real.revoke;
});

test("0.5: превью вставленной картинки — кнопка: раскрыть поверх окна, Esc — закрыть", async () => {
  const real = URL.createObjectURL;
  URL.createObjectURL = (() => "blob:shot") as never;
  vi.mocked(pasteChatImage).mockResolvedValue(
    { id: "a1", status: "ready", attachment: attMsg("a1", { path: "D:/rec/r1/assistant/files/a1.png" }) } as never);
  render(<Host />);
  load([]);
  await act(async () => { await chat.paste(new Blob(["x"], { type: "image/png" })); });
  load([attMsg("a1", { path: "D:/rec/r1/assistant/files/a1.png" }), userMsg("u1", { text: "вот", attachments: ["a1"] })]);
  await userEvent.click(screen.getByRole("button", { name: "Открыть изображение Скриншот.png" }));
  const view = screen.getByRole("dialog", { name: "Скриншот.png" });
  expect(within(view).getByRole("img", { name: "Скриншот.png" })).toHaveAttribute("src", "blob:shot");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  URL.createObjectURL = real;
});

test("0.5 (пропадающее сообщение): ход, что допишется к прошлому, пишется под ним — своей карточки нет и пропадать нечему", () => {
  render(<Host />);
  load([agentMsg("m1", { text: "Первое" }), agentMsg("m2", { status: "writing", text: "", merge_into: "m1" })]);
  act(() => chat.sink.onChatPartial({ id: "m2", text: "Второе" }));
  expect(rows().map((r) => r.dataset.key)).toEqual(["m1"]);
  const first = rows()[0]!;
  expect(first).toHaveTextContent("дописывает…");
  expect(first.querySelector(".chat-msg__more")).toHaveTextContent("Второе");
  // Склейка: тот же ряд, текст — уже в нём.
  act(() => chat.sink.onChat({ seq: 60, op: "patch", id: "m2", set: { status: "superseded", merged_into: "m1" } }));
  act(() => chat.sink.onChat({ seq: 61, op: "patch", id: "m1", set: { text: "Первое\n\nВторое" } }));
  expect(rows().map((r) => r.dataset.key)).toEqual(["m1"]);
  expect(rows()[0]).toBe(first);
  expect(first.querySelector(".chat-msg__more")).toBeNull();
  expect(first).toHaveTextContent("Второе");
});

test("0.5: «Засчитано голосом» — с «Отменить», пока ждёт; после — нажато или отменено", async () => {
  const cancel = vi.mocked(cancelVoicePress).mockResolvedValue({ ok: true });
  render(<Host />);
  const voice = (o: Partial<ChatMessage>) => ({ ...agentMsg("v1"), kind: "system" as const, text: "Засчитано голосом: «Да, проверь»",
    voice: { re: "m1", label: "Да, проверь" }, state: "pending", undo_s: 10, at: Date.now() / 1000, ...o });
  load([agentMsg("m1", { buttons: ["Да, проверь", "Не надо"] }), voice({})]);
  const row = rows().find((r) => r.dataset.key === "v1")!;
  expect(row).toHaveTextContent("Засчитано голосом: «Да, проверь»");
  await userEvent.click(within(row).getByRole("button", { name: "Отменить" }));
  expect(cancel).toHaveBeenCalledWith(ep, "v1");
  load([agentMsg("m1", { buttons: ["Да, проверь", "Не надо"] }), voice({ state: "cancelled" })], 60);
  expect(rows().find((r) => r.dataset.key === "v1")).toHaveTextContent("Голосовое нажатие отменено");
  expect(screen.queryByRole("button", { name: "Отменить" })).toBeNull();
  load([agentMsg("m1", { buttons: ["Да, проверь", "Не надо"] }), voice({ state: "pressed" })], 61);
  expect(rows().find((r) => r.dataset.key === "v1")).toHaveTextContent("Нажато голосом: «Да, проверь»");
});

test("M15: «Не отвлекать» — лента молчит, но вопрос к вам объявляется", () => {
  render(<Host quiet />);
  load([agentMsg("m1", { text: "Сказать про **срок**?", pin: true })]);
  expect(log()).toHaveAttribute("aria-live", "off");
  expect(screen.getByText("Вопрос вам: Сказать про срок?")).toHaveAttribute("role", "status");
});

// --- панель: M6 (одно состояние) и M15 (строка свёрнутой панели в «Не отвлекать») ---

const status: LiveStatus = { active: true, starting: false, stopping: false, folder: "C:/r/x", error: null, started_at: null };
const snap = { status: "idle", folder: null, levels: {}, live: status } as Partial<Snapshot> as Snapshot;
const liveStream = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events")).at(-1)!;
const liveState = { digest: "", summary: null, hints: [], hints_enabled: false, agent: agentInfo() };

function panel(expanded: boolean) {
  let view = { expanded, maximized: false, pinned: true };
  vi.mocked(getState).mockResolvedValue(snap);
  vi.mocked(invoke).mockImplementation(async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === "live_window_state") return view;
    if (cmd === "live_set_expanded") { view = { ...view, expanded: !!args?.expanded }; return view; }
    return undefined;
  });
  return render(<LivePanel endpoint={ep} />);
}

test("M6: шапка панели говорит, что с агентом; у шапки сессии своей точки нет", async () => {
  const { container } = panel(true);
  act(() => {
    liveStream().emit("state", liveState);
    liveStream().emit("chat_snapshot", { messages: [], seq: 1, agent: agentInfo() });
  });
  const head = container.querySelector(".live-head__title")!;
  expect(head).toHaveTextContent("Слушает");
  act(() => liveStream().emit("agent", agentInfo({ state: "writing" })));
  expect(head).toHaveTextContent("Думает…");
  expect(container.querySelector(".session-bar__dot")).toBeNull();
  // Состояние агента в шапке — один знак (AgentMark): «думает» — «ищет».
  expect(container.querySelectorAll(".agent-mark")).toHaveLength(1);
  expect(head.querySelector(".agent-mark")).toHaveAttribute("data-state", "search");
});

test("M15: свёрнутая в «Не отвлекать» держит показанное сообщение; вопрос к вам встаёт и тогда", () => {
  panel(false);
  act(() => {
    liveStream().emit("state", { ...liveState, prefs: { quiet_default: true } });
    liveStream().emit("chat_snapshot", { messages: [agentMsg("m1", { text: "Первое" })], seq: 2, agent: agentInfo() });
  });
  expect(screen.getByRole("button", { name: /Ассистент: Первое/ })).toBeInTheDocument();
  act(() => liveStream().emit("chat", { seq: 3, op: "add", message: agentMsg("m2", { text: "Второе" }) }));
  expect(screen.getByRole("button", { name: /Ассистент: Первое/ })).toBeInTheDocument();
  act(() => liveStream().emit("chat", { seq: 4, op: "add", message: agentMsg("m3", { text: "Ответить Анне?", pin: true }) }));
  expect(screen.getByRole("button", { name: /Вопрос вам: Ответить Анне\?/ })).toBeInTheDocument();
});

test("M13 (ревью after-chat M6): после переподключения лента снова обрезана — кнопка возвращается", async () => {
  const many = Array.from({ length: SNAPSHOT_LIMIT }, (_, k) => agentMsg(`m${k + 10}`, { text: `Сообщение ${k + 10}` }));
  vi.mocked(getChat).mockResolvedValue({ messages: [agentMsg("m1", { text: "Самое первое" }), ...many], seq: 300 });
  render(<Host />);
  load(many, 300);
  await clickMore();
  await vi.waitFor(() => expect(hasText("Самое первое")).toBe(true));
  expect(more()).toBeNull();
  load(many, 301);          // переподключение: снова последние 200
  expect(more()).toHaveTextContent(moreLabel);
});

test("M13: дочитанный снимок старее учтённого — не применяется, кнопка остаётся", async () => {
  const many = Array.from({ length: SNAPSHOT_LIMIT }, (_, k) => agentMsg(`m${k + 10}`, { text: `Сообщение ${k + 10}` }));
  vi.mocked(getChat).mockResolvedValue({ messages: [agentMsg("m1", { text: "Самое первое" }), ...many], seq: 250 });
  render(<Host />);
  load(many, 300);
  await clickMore();
  expect(hasText("Самое первое")).toBe(false);
  expect(more()).toHaveTextContent(moreLabel);
});
