/**
 * Ход работы ассистента в ленте (0.4, «как в Claude CLI»): строки вызовов под сообщением
 * своего хода, состояние, раскрытие вывода, решение ворот, карточка согласия в строке;
 * ответы слэш-команд и однократная строка об автомоде.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  confirmChat: vi.fn(async () => ({ ok: true })),
  postChat: vi.fn(async () => ({ id: "m5", queued: false, attachments: [] })),
  newChatClientId: vi.fn(() => "c1"),
}));
import { confirmChat, newChatClientId, postChat } from "../lib/api";
import type { ChatMessage, ChatSnapshot } from "../lib/types";
import { agentInfo, agentMsg, userMsg } from "../test/chatFixtures";
import { LiveChat } from "./LiveChat";
import { OUTPUT_LINES, duration, headLines } from "./ToolRows";
import { type Chat, useChat } from "./useChat";

const ep = { base: "http://h", token: "t" };
let chat: Chat;

function Host() {
  chat = useChat(ep);
  return <LiveChat chat={chat} />;
}
const load = (messages: ChatMessage[], seq = 50) =>
  act(() => chat.sink.onChatSnapshot({ messages, seq, agent: agentInfo() } as ChatSnapshot));

let n = 0;
const row = (o: Partial<ChatMessage> = {}): ChatMessage => ({
  id: `t${++n}`, seq: 100 + n, at: 1_800_000_000, kind: "tool", event: "call", tool_use_id: `toolu_${n}`, name: "Bash",
  view: "shell", label: "Bash", summary: "git status --short", input_preview: "git status --short", status: "done",
  reply: "m2", duration_ms: 1200, gate: { decision: "auto", label: "разрешено автоматически" }, ...o,
});

beforeEach(() => vi.clearAllMocks());

test("строки вызовов — под ответом своего хода: вид, суть, ✓ и время, решение ворот", async () => {
  render(<Host />);
  load([userMsg("m1", { text: "что в git?" }), agentMsg("m2", { mode: "reply", text: "Изменён один файл" }),
    row({ status: "done" }),
    row({ name: "Edit", view: "edit", label: "Правка", summary: "C:/kb/План.md", added: 3, removed: 1, duration_ms: 300,
      input_preview: "{}", status: "done" })]);
  const msg = screen.getByText("Изменён один файл").closest("li")!;
  const rows = within(msg).getByRole("list", { name: "Ход работы ассистента" });
  const [bash, edit] = within(rows).getAllByRole("listitem");
  expect(bash).toHaveTextContent("готово: Bashgit status --short1,2 с");
  // «Разрешено автоматически» — не строкой под вызовом, а подсказкой и для диктора.
  expect(bash).toHaveTextContent("разрешено автоматически");
  expect(bash!.querySelector(".chat-tool__gate")).toBeNull();
  // Суть и решение — подсказкой Aurora (ui/Tip) при наведении, не системным title.
  const line = bash!.querySelector<HTMLElement>(".chat-tool__line")!;
  expect(line).not.toHaveAttribute("title");
  fireEvent.mouseEnter(line);
  expect(await screen.findByText("git status --short — разрешено автоматически")).toHaveClass("tooltip");
  expect(edit).toHaveTextContent("Правка");
  expect(within(edit!).getByText("+3")).toBeInTheDocument();
  expect(within(edit!).getByText("−1")).toBeInTheDocument();
});

test("строка без ответа в ленте (молчаливый ход) не видна; служебные запросы — тоже", () => {
  render(<Host />);
  load([row({ reply: "m9" }), { ...row(), event: "request", reply: undefined }]);
  expect(screen.queryByRole("list", { name: "Ход работы ассистента" })).toBeNull();
});

test("ответ человеку, который уже вызывает инструменты, виден сразу; выполняется — знак «пишет»", () => {
  render(<Host />);
  load([userMsg("m1"), agentMsg("m2", { mode: "reply", status: "writing", text: "" }),
    row({ status: "running", duration_ms: undefined })]);
  const rows = screen.getByRole("list", { name: "Ход работы ассистента" });
  expect(within(rows).getByRole("listitem")).toHaveTextContent("выполняется: Bash");
  expect(rows.querySelector(".agent-mark[data-state='write']")).not.toBeNull();
  expect(screen.queryByText("Пишет…")).toBeNull();
});

test("ошибка: ✕ и первая строка ошибки; запрет ворот — «запрещено: …»", () => {
  render(<Host />);
  load([agentMsg("m2", { mode: "reply" }),
    row({ status: "error", error: "Error: boom", output_preview: "Error: boom\nat x" }),
    row({ status: "denied", summary: "rm -rf build", gate: { decision: "denied", label: "запрещено: удаление" } })]);
  const [err, denied] = within(screen.getByRole("list", { name: "Ход работы ассистента" })).getAllByRole("listitem");
  expect(err).toHaveTextContent("ошибка: Bash");
  expect(within(err!).getByText("Error: boom", { selector: ".chat-tool__error" })).toBeInTheDocument();
  expect(denied).toHaveTextContent("отклонено: Bash");
  expect(denied).toHaveTextContent("запрещено: удаление");
});

test("вывод раскрывается по строке (клавиатура): первые 40 строк, «Показать всё» — целиком", async () => {
  const output = Array.from({ length: 55 }, (_, i) => `строка ${i + 1}`).join("\n");
  render(<Host />);
  load([agentMsg("m2", { mode: "reply" }), row({ output_preview: output })]);
  const line = screen.getByRole("button", { name: /Bash/ });
  expect(line).toHaveAttribute("aria-expanded", "false");
  line.focus();
  await userEvent.keyboard("{Enter}");
  expect(line).toHaveAttribute("aria-expanded", "true");
  const code = document.querySelector(".chat-tool__code:not(.chat-tool__code--in)")!;
  expect(code.textContent).toContain(`строка ${OUTPUT_LINES}`);
  expect(code.textContent).not.toContain(`строка ${OUTPUT_LINES + 1}`);
  await userEvent.click(screen.getByRole("button", { name: "Показать всё (ещё 15 строк)" }));
  expect(document.querySelector(".chat-tool__code:not(.chat-tool__code--in)")!.textContent).toContain("строка 55");
});

test("карточка согласия — в строке своего вызова, кнопки в ней; над лентой отдельной нет", async () => {
  render(<Host />);
  const card: ChatMessage = {
    id: "m3", seq: 300, at: 1_800_000_000, kind: "system", card: "confirm", tool: "Bash", title: "удалить build",
    args: "rm -rf build", tool_use_id: "toolu_77", expires_at: 4_000_000_000,
  };
  load([agentMsg("m2", { mode: "reply", status: "writing", text: "" }),
    row({ tool_use_id: "toolu_77", status: "running", summary: "rm -rf build", duration_ms: undefined }), card]);
  const rows = screen.getByRole("list", { name: "Ход работы ассистента" });
  const group = within(rows).getByRole("group", { name: /удалить build/ });
  expect(group).toHaveTextContent("Нужно ваше решение: удалить build");
  expect(screen.getAllByRole("group", { name: /удалить build/ })).toHaveLength(1);
  await userEvent.click(within(group).getByRole("button", { name: "Разрешить один раз" }));
  expect(confirmChat).toHaveBeenCalledWith(ep, "m3", true, false);
});

test("ответ /mcp: серверы, у сбойного — «Переподключить»; неизвестная — «Отправить как текст»", async () => {
  render(<Host />);
  const sys = (id: string, o: Partial<ChatMessage>): ChatMessage =>
    ({ id, seq: 400 + Number(id.slice(1)), at: 1_800_000_000, kind: "system", card: "command", ...o });
  load([
    sys("m1", { command: "mcp", text: "team-jira — ошибка: Connection closed\ngitlab — подключён",
      servers: [{ name: "team-jira", status: "failed", error: "Connection closed" }, { name: "gitlab", status: "connected" }] }),
    sys("m2", { command: "foo", text: "Нет команды /foo — /help", unknown: "/foo bar", level: "info" }),
  ]);
  await userEvent.click(screen.getByRole("button", { name: "Переподключить" }));
  expect(postChat).toHaveBeenLastCalledWith(ep, expect.objectContaining({ text: "/mcp reconnect team-jira" }));
  expect(screen.getByText("Нет команды /foo — /help")).toBeInTheDocument();
  vi.mocked(newChatClientId).mockReturnValue("c2");
  await userEvent.click(screen.getByRole("button", { name: "Отправить как текст" }));
  expect(postChat).toHaveBeenLastCalledWith(ep, expect.objectContaining({ text: "//foo bar" }));
});

test("однократная строка об автомоде и ответ хода-команды с пометкой «команда»", () => {
  render(<Host />);
  load([
    { id: "m1", seq: 1, at: 1_800_000_000, kind: "system", notice: "agent_mode",
      text: "Ассистент теперь сам выполняет обычные действия, рискованные — с вашего разрешения." },
    userMsg("m2", { text: "/context", via: "command" }),
    agentMsg("m3", { mode: "reply", via: "command", text: "## Context" }),
  ]);
  expect(screen.getByText(/Ассистент теперь сам выполняет/).closest("li")).toHaveClass("chat-sys--notice");
  expect(screen.getAllByText("команда")).toHaveLength(2);
});

test("0.5: итог под блоком действий — «Выполнено N из M · не удалось: … — причина», щелчок — к строке", async () => {
  const scrolled: string[] = [];
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) { scrolled.push(this.getAttribute("data-tool") ?? ""); });
  try {
    render(<Host />);
    load([agentMsg("m2", { mode: "reply", text: "Готово" }),
      row({ status: "done" }), row({ status: "done", summary: "npm test" }),
      row({ status: "error", summary: "npm run build", error: "Error: vite не найден\nat x", tool_use_id: "toolu_bad" }),
      row({ status: "denied", summary: "rm -rf build", gate: { decision: "denied", label: "запрещено: удаление" } })]);
    const result = screen.getByRole("button", { name: /^Выполнено 2 из 4/ });
    expect(result).toHaveTextContent("Выполнено 2 из 4 · не удалось: Bash npm run build — Error: vite не найден · ещё 1");
    await userEvent.click(result);
    expect(scrolled).toEqual(["toolu_bad"]);
  } finally {
    Element.prototype.scrollIntoView = original;
  }
});

test("0.5: итог — когда вызовов два и больше и ни один не выполняется; всё удалось — без «не удалось»", () => {
  render(<Host />);
  load([agentMsg("m2", { mode: "reply", status: "writing", text: "" }), row({ status: "done" }), row({ status: "running" })]);
  expect(screen.queryByText(/^Выполнено/)).toBeNull();
  load([agentMsg("m2", { mode: "reply", text: "Ок" }), row({ status: "done" }), row({ status: "done" })], 60);
  expect(screen.getByText("Выполнено 2 из 2")).toBeInTheDocument();
  load([agentMsg("m2", { mode: "reply", text: "Ок" }), row({ status: "done" })], 70);
  expect(screen.queryByText(/^Выполнено/)).toBeNull();
});

test("время и строки вывода", () => {
  expect(duration(400)).toBe("400 мс");
  expect(duration(1200)).toBe("1,2 с");
  expect(duration(12_400)).toBe("12 с");
  expect(duration(125_000)).toBe("2 мин 5 с");
  expect(duration(undefined)).toBe("");
  expect(headLines("a\nb\n", 5)).toEqual({ head: "a\nb", hidden: 0 });
  expect(headLines("a\nb\nc", 2)).toEqual({ head: "a\nb", hidden: 1 });
});
