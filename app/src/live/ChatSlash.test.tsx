/**
 * Подсказка слэш-команд в строке ввода (0.4): «/» открывает список (`.menu` Aurora) над полем,
 * фильтр по набранному, ↑ ↓, Tab — дописать, Enter — выбрать, Esc — убрать; команды CLI — от
 * ассистента (`agent.commands`), без них — команды Meet.
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  postChat: vi.fn(async () => ({ id: "m5", queued: false, attachments: [] })),
  newChatClientId: vi.fn(() => "c1"),
}));
import { postChat } from "../lib/api";
import type { AgentCommand, ChatSnapshot } from "../lib/types";
import { agentInfo } from "../test/chatFixtures";
import { ChatComposer } from "./ChatComposer";
import { MEET_COMMANDS, completion, matchCommands, slashQuery } from "./slash";
import { type Chat, useChat } from "./useChat";

const ep = { base: "http://h", token: "t" };
let chat: Chat;

function Host() {
  chat = useChat(ep);
  return <ChatComposer chat={chat} />;
}
const field = () => screen.getByRole("textbox", { name: "Сообщение ассистенту" });
const cli: AgentCommand[] = [
  ...MEET_COMMANDS,
  { name: "context", hint: "", description: "Заполнение контекста", source: "cli" },
  { name: "review", hint: "[PR]", description: "Ревью", source: "cli" },
];
const load = (commands?: AgentCommand[]) =>
  act(() => chat.sink.onChatSnapshot({ messages: [], seq: 1, agent: agentInfo({ commands }) } as ChatSnapshot));

beforeEach(() => vi.clearAllMocks());

test("разбор и фильтр", () => {
  expect(slashQuery("/")).toBe("");
  expect(slashQuery("/mc")).toBe("mc");
  expect(slashQuery("/mcp reconnect")).toBeNull();
  expect(slashQuery("//mcp")).toBeNull();
  expect(slashQuery("текст")).toBeNull();
  expect(matchCommands(cli, "c").map((c) => c.name)).toEqual(["clear", "compact", "context", "mcp"]);
  expect(completion(cli[1]!)).toBe("/mcp ");
  expect(completion(cli[0]!)).toBe("/help");
});

test("«/» открывает список над полем, набранное сужает его, описание и аргументы видны", async () => {
  render(<Host />);
  load(cli);
  await userEvent.type(field(), "/");
  const list = screen.getByRole("listbox", { name: "Команды" });
  expect(list).toHaveClass("menu", "open");
  // Внутри строки ввода: там её непрозрачный фон (chat.css), лента не просвечивает.
  expect(list.closest(".chat-compose")).not.toBeNull();
  expect(within(list).getAllByRole("option")).toHaveLength(cli.length);
  expect(field()).toHaveAttribute("aria-expanded", "true");
  await userEvent.type(field(), "re");
  const [review] = within(screen.getByRole("listbox")).getAllByRole("option");
  expect(review).toHaveTextContent("/review[PR]CLIРевью");
  expect(review).toHaveAttribute("aria-selected", "true");
  expect(field()).toHaveAttribute("aria-activedescendant", review!.id);
});

test("↓ и Enter: команда без аргументов уходит сразу; с аргументами — дописывается", async () => {
  render(<Host />);
  load(cli);
  await userEvent.type(field(), "/c");
  await userEvent.keyboard("{ArrowDown}");             // clear → compact
  expect(within(screen.getByRole("listbox")).getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
  await userEvent.keyboard("{Enter}");
  expect(postChat).not.toHaveBeenCalled();
  expect(field()).toHaveValue("/compact ");
  expect(screen.queryByRole("listbox")).toBeNull();
  await userEvent.clear(field());
  await userEvent.type(field(), "/con{Enter}");
  expect(postChat).toHaveBeenCalledWith(ep, expect.objectContaining({ text: "/context" }));
  expect(field()).toHaveValue("");
});

test("Tab дописывает, Esc убирает список (текст остаётся), Enter тогда отправляет как есть", async () => {
  render(<Host />);
  load(cli);
  await userEvent.type(field(), "/he");
  await userEvent.keyboard("{Tab}");
  expect(field()).toHaveValue("/help");
  expect(field()).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(field()).toHaveValue("/help");
  await userEvent.keyboard("{Enter}");
  expect(postChat).toHaveBeenCalledWith(ep, expect.objectContaining({ text: "/help" }));
});

test("без списка от ассистента — команды Meet; «//» и текст списка не открывают", async () => {
  render(<Host />);
  load(undefined);
  await userEvent.type(field(), "/");
  expect(within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent?.split(/\s|\[/)[0]))
    .toEqual(["/help", "/mcp", "/clear", "/model", "/compact"].map((n) => expect.stringContaining(n)));
  await userEvent.type(field(), "/");
  expect(screen.queryByRole("listbox")).toBeNull();
  await userEvent.clear(field());
  await userEvent.type(field(), "привет /");
  expect(screen.queryByRole("listbox")).toBeNull();
});

const servers = [{ name: "gitlab", status: "connected" }, { name: "team-jira", status: "failed" },
  { name: "tracker", status: "disabled" }];
const models = [{ value: "opus", label: "Opus" }, { value: "sonnet", label: "Sonnet" }];
const loadFull = () => act(() => chat.sink.onChatSnapshot({
  messages: [], seq: 1,
  agent: agentInfo({ commands: [...cli, { name: "summeet", hint: "[файл]", description: "Итоги встречи", source: "skill" }],
    mcp_servers: servers, models }),
} as ChatSnapshot));
const options = () => within(screen.getByRole("listbox")).getAllByRole("option");

test("навык — в списке с пометкой «навык»; у команды с аргументами — призрак argumentHint", async () => {
  render(<Host />);
  loadFull();
  await userEvent.type(field(), "/sum");
  expect(options()[0]).toHaveTextContent("/summeet[файл]навыкИтоги встречи");
  await userEvent.keyboard("{Tab}");
  expect(field()).toHaveValue("/summeet ");
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(document.querySelector(".chat-slash__ghost")).toHaveTextContent("/summeet [файл]");
});

test("/mcp: действие, затем сервер с состоянием (сбойный первым); Enter отправляет команду целиком", async () => {
  render(<Host />);
  loadFull();
  await userEvent.type(field(), "/mcp ");
  expect(options().map((o) => o.textContent)).toEqual([
    expect.stringContaining("reconnect"), expect.stringContaining("enable"), expect.stringContaining("disable")]);
  await userEvent.keyboard("{Enter}");
  expect(field()).toHaveValue("/mcp reconnect ");
  expect(options().map((o) => o.textContent)).toEqual(["team-jiraошибка", "gitlabподключён", "trackerвыключен"]);
  expect(options()[0]!.querySelector(".is-warn")).not.toBeNull();
  await userEvent.type(field(), "g");
  expect(options()).toHaveLength(1);
  await userEvent.keyboard("{Enter}");
  expect(postChat).toHaveBeenCalledWith(ep, expect.objectContaining({ text: "/mcp reconnect gitlab" }));
  expect(field()).toHaveValue("");
});

test("/mcp enable — только выключенные; /model — модели CLI, Tab дописывает, Esc убирает", async () => {
  render(<Host />);
  loadFull();
  await userEvent.type(field(), "/mcp enable ");
  expect(options().map((o) => o.textContent)).toEqual(["trackerвыключен"]);
  await userEvent.clear(field());
  await userEvent.type(field(), "/model s");
  expect(options().map((o) => o.textContent)).toEqual(["sonnetSonnet"]);
  await userEvent.keyboard("{Tab}");
  expect(field()).toHaveValue("/model sonnet");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("listbox")).toBeNull();
  await userEvent.keyboard("{Enter}");
  expect(postChat).toHaveBeenCalledWith(ep, expect.objectContaining({ text: "/model sonnet" }));
});

test("подсказки обновляются вместе с агентом (после /mcp reconnect — новое состояние)", async () => {
  render(<Host />);
  loadFull();
  await userEvent.type(field(), "/mcp reconnect v");
  expect(options()[0]).toHaveTextContent("team-jiraошибка");
  act(() => chat.sink.onAgent(agentInfo({ commands: cli, mcp_servers: [{ name: "team-jira", status: "connected" }], models })));
  expect(options()[0]).toHaveTextContent("team-jiraподключён");
});

test("щелчок по команде — как Enter", async () => {
  render(<Host />);
  load(cli);
  await userEvent.type(field(), "/mo");
  await userEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: /\/model/ }));
  expect(field()).toHaveValue("/model ");
});
