import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  setAgentFrequency: vi.fn(async () => ({ frequency: "less", label: "реже", live: true })),
  pasteChatImage: vi.fn(async () => ({ id: "a1", status: "ready", attachment: { id: "a1", kind: "attachment" } })),
  postChat: vi.fn(async () => ({ id: "m9", queued: false, attachments: [] })),
  newChatClientId: vi.fn(() => "c1"),
}));
import { postChat, setAgentFrequency } from "../lib/api";
import type { ChatSnapshot, LiveHint } from "../lib/types";
import { agentInfo, agentMsg } from "../test/chatFixtures";
import { LiveWorkspace, useLiveView } from "./LiveWorkspace";
import { EMPTY_SUMMARY } from "./liveModel";
import { type Chat, useChat } from "./useChat";
import type { Live } from "./useLive";

const ep = { base: "http://h", token: "t" };
const lines = [
  { t: 60, speaker: "Ольга", text: "начнём с миграции", id: 0 },
  { t: 125, speaker: "Игорь", text: "миграцию сделаем к пятнице", id: 1 },
];
const hint: LiveHint = {
  id: "h1", kind: "risk", text: "У миграции нет ответственного", why: "", source_t: 125, ref: null, pinned: false,
  dismissed: false, created_at: 1, updated_at: 1,
};
function makeLive(o: Partial<Live> = {}): Live {
  return {
    status: null, lines, digest: "", summary: { ...EMPTY_SUMMARY, topic: "Миграция" }, hints: [hint], hintsEnabled: true,
    quietDefault: false, catchup: null, qa: [], loaded: true, error: null, asking: false, askError: null, hintError: null,
    agent: null, ask: vi.fn(async () => {}), hint: vi.fn(async () => {}), setTask: vi.fn(async () => {}), ...o,
  };
}
let chat: Chat;
function Host({ live, wide = false }: { live: Live; wide?: boolean }) {
  chat = useChat(ep);
  const view = useLiveView(live, { open: true, wide, quiet: false });
  return <LiveWorkspace live={live} view={view} onAsk={() => {}} chat={chat} />;
}
const load = () => act(() => chat.sink.onChatSnapshot({
  messages: [agentMsg("m1", { text: "Срок другой: [02:05]" })], seq: 3, agent: agentInfo(),
} as ChatSnapshot));

/** Ширина элементов в jsdom — 0: задать её, чтобы раскладка не была компактной. */
function width(px: number) {
  return vi.spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockReturnValue({ width: px, height: 600, top: 0, left: 0, right: px, bottom: 600, x: 0, y: 0, toJSON: () => ({}) });
}
afterEach(() => vi.restoreAllMocks());

test("участник выключен — прежняя раскладка без изменений (вкладки, подсказки)", () => {
  render(<Host live={makeLive()} />);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Лента", "Сводка", "Подсказки", "Спросить"]);
  expect(screen.queryByRole("log", { name: "Чат с ассистентом" })).toBeNull();
});

test("участник включён — чат вместо «Подсказок»/«Спросить»/«Сводки»; шапка сессии", () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.queryByText("У миграции нет ответственного")).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Вопрос ассистенту" })).toBeNull();
  expect(screen.getByRole("log", { name: "Чат с ассистентом" })).toHaveTextContent("Срок другой");
  expect(screen.getByRole("group", { name: "Сессия ассистента" })).toHaveTextContent("Claude Code");
  expect(screen.getByRole("region", { name: "Расшифровка" })).toHaveTextContent("начнём с миграции");
  expect(screen.getByRole("separator", { name: "Высота расшифровки" })).toBeInTheDocument();
});

test("широкая: расшифровка колонкой сбоку с разделителем ширины", () => {
  width(900);
  render(<Host live={makeLive({ agent: agentInfo() })} wide />);
  expect(screen.getByRole("separator", { name: "Ширина расшифровки" })).toBeInTheDocument();
  expect(screen.getByRole("log", { name: "Лента встречи" })).toBeInTheDocument();
});

test("таймкод в сообщении — к моменту в расшифровке", async () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  await userEvent.click(screen.getByRole("button", { name: "02:05" }));
  const target = screen.getByText("миграцию сделаем к пятнице").closest("li")!;
  expect(target).toHaveClass("is-target");
});

test("компактная (узкая панель): чат и строка расшифровки; щелчок разворачивает полосу", async () => {
  width(360);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  expect(screen.queryByRole("log", { name: "Лента встречи" })).toBeNull();
  const ticker = screen.getByRole("button", { name: "Развернуть расшифровку" });
  expect(ticker).toHaveTextContent("Игорьмиграцию сделаем к пятнице");
  await userEvent.click(ticker);
  expect(screen.getByRole("log", { name: "Лента встречи" })).toBeInTheDocument();
  expect(screen.queryByText(/видит:/)).toBeNull();
});

test("компактная: переход по таймкоду сам разворачивает полосу", async () => {
  width(360);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  await userEvent.click(screen.getByRole("button", { name: "02:05" }));
  expect(screen.getByRole("log", { name: "Лента встречи" })).toBeInTheDocument();
});

test("«Как часто писать» уходит setAgentFrequency и сразу видно выбранное", async () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  const group = screen.getByRole("radiogroup", { name: "Как часто писать" });
  await userEvent.click(within(group).getByRole("radio", { name: "реже" }));
  expect(setAgentFrequency).toHaveBeenCalledWith(ep, "реже");
  expect(within(group).getByRole("radio", { name: "реже" })).toHaveAttribute("aria-checked", "true");
});

test("нет связи — писать нельзя, причина видна", () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo(), error: "Нет связи с ассистентом — переподключаюсь…" })} />);
  expect(screen.getByRole("textbox", { name: "Сообщение ассистенту" })).toBeDisabled();
  expect(screen.getByText("Нет связи с ассистентом — переподключаюсь…")).toBeInTheDocument();
});

test("смена раскладки (узкая → широкая → компактная) не теряет текст и вложения строки ввода", async () => {
  let px = 600;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(() => ({ width: px, height: 600, top: 0, left: 0, right: px, bottom: 600, x: 0, y: 0, toJSON: () => ({}) }));
  const live = makeLive({ agent: agentInfo() });
  const { rerender } = render(<Host live={live} />);
  load();
  const field = screen.getByRole("textbox", { name: "Сообщение ассистенту" });
  await userEvent.type(field, "черновик");
  const png = new File([new Uint8Array([1])], "shot.png", { type: "image/png" });
  await act(async () => { fireEvent.paste(field, { clipboardData: { files: [png], getData: () => "" } }); });
  rerender(<Host live={live} wide />);
  expect(screen.getByRole("separator", { name: "Ширина расшифровки" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Сообщение ассистенту" })).toBe(field); // тот же элемент — не пересоздан
  px = 360;
  rerender(<Host live={{ ...live }} />);
  await act(async () => { window.dispatchEvent(new Event("resize")); });
  expect(screen.getByRole("textbox", { name: "Сообщение ассистенту" })).toHaveValue("черновик");
  expect(screen.getByRole("list", { name: "Вложения" })).toHaveTextContent("shot.png");
});

test("быстрые вопросы над пустой строкой: щелчок отправляет; при наборе — скрыты", async () => {
  width(600);
  render(<Host live={makeLive({ agent: agentInfo() })} />);
  load();
  const group = screen.getByRole("group", { name: "Быстрые вопросы" });
  expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Что я пропустил?", "Что ответить?", "Кратко итоги"]);
  await userEvent.click(within(group).getByRole("button", { name: "Что я пропустил?" }));
  expect(postChat).toHaveBeenCalledWith(ep, { text: "Что я пропустил?", client_id: "c1", attachments: [] });
  await userEvent.type(screen.getByRole("textbox", { name: "Сообщение ассистенту" }), "с");
  expect(screen.queryByRole("group", { name: "Быстрые вопросы" })).toBeNull();
});

test("до первого состояния — «Подключаюсь к ассистенту…», не прежняя раскладка", () => {
  render(<Host live={makeLive({ loaded: false })} />);
  expect(screen.getByText("Подключаюсь к ассистенту…")).toBeInTheDocument();
  expect(screen.queryByRole("tablist")).toBeNull();
  expect(screen.queryByRole("log")).toBeNull();
});
