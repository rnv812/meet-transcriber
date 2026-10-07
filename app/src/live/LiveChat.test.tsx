import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  clickChat: vi.fn(async () => ({ ok: true, id: "m9" })),
  reactChat: vi.fn(async () => ({ ok: true, changed: true })),
  newChatClientId: vi.fn(() => "c1"),
}));
import { clickChat, reactChat } from "../lib/api";
import type { ChatMessage, ChatSnapshot } from "../lib/types";
import { agentInfo, agentMsg, attMsg, userMsg } from "../test/chatFixtures";
import { LiveChat } from "./LiveChat";
import { type Chat, useChat } from "./useChat";

const ep = { base: "http://h", token: "t" };
let chat: Chat;

function Host({ onTime, quiet }: { onTime?: (t: number) => void; quiet?: boolean }) {
  chat = useChat(ep);
  return <LiveChat chat={chat} onTime={onTime} quiet={quiet} />;
}
const load = (messages: ChatMessage[], seq = 20) =>
  act(() => chat.sink.onChatSnapshot({ messages, seq, agent: agentInfo() } as ChatSnapshot));
const log = () => screen.getByRole("log", { name: "Чат с ассистентом" });

beforeEach(() => vi.clearAllMocks());

test("лента — role=log с вежливыми объявлениями; в «Не отвлекать» — без объявлений", () => {
  const { rerender } = render(<Host />);
  expect(log()).toHaveAttribute("aria-live", "polite");
  rerender(<Host quiet />);
  expect(log()).toHaveAttribute("aria-live", "off");
});

test("сообщение агента: Markdown, таймкод переходит к моменту в расшифровке", async () => {
  const onTime = vi.fn();
  render(<Host onTime={onTime} />);
  load([agentMsg("m1", { text: "**Срок** другой: см. [01:05]\n\n- первое\n- второе" })]);
  expect(within(log()).getByText("Срок").tagName).toBe("STRONG");
  expect(within(log()).getAllByRole("listitem").some((li) => li.textContent === "первое")).toBe(true);
  await userEvent.click(within(log()).getByRole("button", { name: "01:05" }));
  expect(onTime).toHaveBeenCalledWith(65);
});

test("кнопки агента: щелчок уходит clickChat, после — нажатая отмечена, остальные недоступны", async () => {
  render(<Host />);
  load([agentMsg("m1", { buttons: ["Глянь", "Только сроки", "Не надо"] })]);
  const group = screen.getByRole("group", { name: "Ответить ассистенту" });
  await userEvent.click(within(group).getByRole("button", { name: "Только сроки" }));
  expect(clickChat).toHaveBeenCalledWith(ep, "m1", "Только сроки", "c1");
  expect(within(group).getByRole("button", { name: /Только сроки/ })).toHaveAttribute("aria-pressed", "true");
  expect(within(group).getByRole("button", { name: "Глянь" })).toHaveAttribute("aria-disabled", "true");
  await userEvent.click(within(group).getByRole("button", { name: "Глянь" }));
  expect(clickChat).toHaveBeenCalledTimes(1);
});

test("кнопки, нажатые раньше (в журнале), показаны нажатыми", () => {
  render(<Host />);
  load([agentMsg("m1", { buttons: ["Глянь", "Не надо"] }), userMsg("m2", { via: "button", re: "m1", text: "Не надо" })]);
  const group = screen.getByRole("group", { name: "Ответить ассистенту" });
  expect(within(group).getByRole("button", { name: /Не надо/ })).toHaveAttribute("aria-pressed", "true");
});

test("реакции 👍 👎 ❓: переключатели, уходят reactChat; поставленная видна нажатой", async () => {
  render(<Host />);
  load([agentMsg("m1")]);
  const like = screen.getByRole("button", { name: "👍 норм" });
  expect(screen.getByRole("button", { name: "👎 не норм" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "❓ вопрос" })).toBeInTheDocument();
  await userEvent.click(like);
  expect(reactChat).toHaveBeenCalledWith(ep, "m1", "👍", true);
  expect(like).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(like);
  expect(reactChat).toHaveBeenLastCalledWith(ep, "m1", "👍", false);
  expect(like).toHaveAttribute("aria-pressed", "false");
});

test("реакция не дошла — откат и заметка", async () => {
  vi.mocked(reactChat).mockRejectedValueOnce(new Error("409"));
  render(<Host />);
  load([agentMsg("m1")]);
  await userEvent.click(screen.getByRole("button", { name: "❓ вопрос" }));
  expect(screen.getByRole("button", { name: "❓ вопрос" })).toHaveAttribute("aria-pressed", "false");
  expect(chat.note).toMatch(/Реакция не дошла/);
});

test("копирование: текст сообщения без разметки", async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  render(<Host />);
  load([agentMsg("m1", { text: "Там **15.11**, а не 01.12" })]);
  await userEvent.click(screen.getByRole("button", { name: "Копировать" }));
  expect(writeText).toHaveBeenCalledWith("Там 15.11, а не 01.12");
  expect(await screen.findByRole("button", { name: "Скопировано" })).toBeInTheDocument();
});

test("закреплённый вопрос — над лентой, с кнопками; ушёл после вашего ответа", () => {
  render(<Host />);
  load([agentMsg("m1", { text: "Сказать Анне про срок?", pin: true, buttons: ["Да", "Нет"] })]);
  const pin = screen.getByRole("region", { name: "Вопрос вам" });
  expect(pin).toHaveTextContent("Сказать Анне про срок?");
  expect(within(pin).getByRole("button", { name: "Да" })).toBeInTheDocument();
  act(() => chat.sink.onChat({ seq: 21, op: "add", message: userMsg("m2", { text: "да" }) }));
  expect(screen.queryByRole("region", { name: "Вопрос вам" })).toBeNull();
});

test("закреплённый вопрос можно убрать «×»", async () => {
  render(<Host />);
  load([agentMsg("m1", { pin: true })]);
  await userEvent.click(screen.getByRole("button", { name: "Убрать из закреплённых" }));
  expect(screen.queryByRole("region", { name: "Вопрос вам" })).toBeNull();
});

test("ваше сообщение с вложением: картинка и документ с пометками", () => {
  render(<Host />);
  load([
    attMsg("a1", { name: "Скрин.png", note: "Модель не видит изображения — ушёл только текст сообщения" }),
    attMsg("a2", { type: "doc", name: "План.pptx" }),
    attMsg("a3", { type: "doc", name: "Сломанный.pdf", status: "failed", error: "битый файл" }),
    userMsg("m1", { text: "что тут?", attachments: ["a1", "a2"] }),
  ]);
  const msg = within(log()).getByText("что тут?").closest("li")!;
  expect(msg).toHaveTextContent("Скрин.png");
  expect(msg).toHaveTextContent("Модель не видит изображения");
  expect(msg).toHaveTextContent("План.pptx");
  expect(log()).not.toHaveTextContent("Сломанный.pdf"); // ни на что не сослались — в ленте нет
});

test("системные строки и строки встречи", () => {
  render(<Host />);
  load([{ ...agentMsg("s1"), kind: "system", text: "Ассистенту нечего добавить" }, { ...agentMsg("e1"), kind: "meeting", text: "— часть 2 —" }]);
  expect(log()).toHaveTextContent("Ассистенту нечего добавить");
  expect(log()).toHaveTextContent("— часть 2 —");
});

/** Прокрутка ленты: jsdom не считает размеры — задаём их сами. */
function scrollTo(el: HTMLElement, { top, height = 1000, client = 200 }: { top: number; height?: number; client?: number }) {
  Object.defineProperty(el, "scrollHeight", { value: height, configurable: true });
  Object.defineProperty(el, "clientHeight", { value: client, configurable: true });
  el.scrollTop = top;
  fireEvent.scroll(el);
}

test("прокручено вверх — «↓ N новых»; щелчок — к низу и счётчик уходит", async () => {
  render(<Host />);
  load([agentMsg("m1"), agentMsg("m2")]);
  const box = log().parentElement!;
  scrollTo(box, { top: 100 });
  expect(screen.queryByRole("button", { name: /новых/ })).toBeNull();
  act(() => {
    chat.sink.onChat({ seq: 21, op: "add", message: agentMsg("m3") });
    chat.sink.onChat({ seq: 22, op: "add", message: agentMsg("m4") });
  });
  const pill = screen.getByRole("button", { name: "↓ 2 новых" });
  await userEvent.click(pill);
  expect(screen.queryByRole("button", { name: /новых/ })).toBeNull();
  expect(box.scrollTop).toBe(1000);
});

test("внизу — следит за низом, плашки нет", () => {
  render(<Host />);
  load([agentMsg("m1")]);
  const box = log().parentElement!;
  scrollTo(box, { top: 800 });
  act(() => chat.sink.onChat({ seq: 21, op: "add", message: agentMsg("m2") }));
  expect(screen.queryByRole("button", { name: /нов/ })).toBeNull();
});
