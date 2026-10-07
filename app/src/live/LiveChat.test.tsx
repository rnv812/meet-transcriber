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
import { EXPLAIN_WAIT_S } from "./chatModel";
import { ACK_MS, type Chat, useChat } from "./useChat";

const ep = { base: "http://h", token: "t" };
let chat: Chat;

function Host({ onTime, quiet, compact, disabled }: {
  onTime?: (t: number) => void; quiet?: boolean; compact?: boolean; disabled?: boolean;
}) {
  chat = useChat(ep);
  return <LiveChat chat={chat} onTime={onTime} quiet={quiet} compact={compact} disabled={disabled} />;
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
  const like = screen.getByRole("button", { name: "👍 Полезно" });
  expect(screen.getByRole("button", { name: "👎 Не по теме" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "❓ Поясни" })).toBeInTheDocument();
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
  await userEvent.click(screen.getByRole("button", { name: "❓ Поясни" }));
  expect(screen.getByRole("button", { name: "❓ Поясни" })).toHaveAttribute("aria-pressed", "false");
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


// --- реакции: подписи, что будет, отклик, ❓ ждёт пояснения ---------------------------------

const msgRow = (text: string | RegExp) => within(log()).getByText(text).closest("li")!;
/** Постоянная live-область ленты: она одна объявляет отклики на реакции. */
const announced = () => document.querySelector<HTMLElement>("[data-chat-announce]")!;

test("live-область отклика на реакции есть с самого начала (пустая), заметки под сообщением — без role", () => {
  render(<Host />);
  load([agentMsg("m1")]);
  expect(announced()).toHaveAttribute("role", "status");
  expect(announced()).toHaveAttribute("aria-live", "polite");
  expect(announced()).toBeEmptyDOMElement();
  fireEvent.click(screen.getByRole("button", { name: "👍 Полезно" }));
  expect(announced()).toHaveTextContent("Учту: такое полезно");
  expect(within(msgRow("Сообщение m1")).queryByRole("status")).toBeNull();
});

test("непоставленные реакции вне наведения спрятаны только глазам: в дереве доступности они есть", () => {
  render(<Host />);
  load([agentMsg("m1", { reactions: { "👍": 1 } })]);
  const row = msgRow("Сообщение m1");
  for (const name of ["👍 Полезно", "👎 Не по теме", "❓ Поясни"]) {
    expect(within(row).getByRole("button", { name })).toBeInTheDocument();
  }
});

test("реакции: формальные подписи у кнопок, в подсказке — что будет", () => {
  render(<Host />);
  load([agentMsg("m1")]);
  const row = msgRow("Сообщение m1");
  const like = within(row).getByRole("button", { name: "👍 Полезно" });
  const dislike = within(row).getByRole("button", { name: "👎 Не по теме" });
  const explain = within(row).getByRole("button", { name: "❓ Поясни" });
  expect(like).toHaveAttribute("title", "Полезно — ассистент будет писать больше такого");
  expect(dislike).toHaveAttribute("title", "Не по теме — ассистент поймёт, что промахнулся, и скорректирует, о чём писать");
  expect(explain).toHaveAttribute("title", "Поясни — ассистент объяснит, на что опирался");
  // подпись рядом с эмодзи (видна при наведении и фокусе — CSS), прежних «норм» нет
  expect(like).toHaveTextContent("👍Полезно");
  expect(dislike).toHaveTextContent("👎Не по теме");
  expect(explain).toHaveTextContent("❓Поясни");
  expect(row).not.toHaveTextContent(/норм|вопрос/);
});

test("узкая панель: у реакций только эмодзи, подпись и что будет — в подсказке и aria", () => {
  render(<Host compact />);
  load([agentMsg("m1")]);
  const like = screen.getByRole("button", { name: "👍 Полезно" });
  expect(like).toHaveTextContent(/^👍$/);
  expect(like).toHaveAttribute("title", "Полезно — ассистент будет писать больше такого");
  expect(screen.getByRole("button", { name: "👎 Не по теме" })).toHaveTextContent(/^👎$/);
});

test("👍 — отклик «Учту: такое полезно» сразу, гаснет, кнопка остаётся нажатой", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    render(<Host />);
    load([agentMsg("m1")]);
    const like = screen.getByRole("button", { name: "👍 Полезно" });
    fireEvent.click(like);
    expect(within(msgRow("Сообщение m1")).getByText("Учту: такое полезно")).toBeInTheDocument();
    expect(announced()).toHaveTextContent("Учту: такое полезно");
    await act(async () => { await vi.advanceTimersByTimeAsync(ACK_MS + 10); });
    expect(within(msgRow("Сообщение m1")).queryByText("Учту: такое полезно")).toBeNull();
    expect(like).toHaveAttribute("aria-pressed", "true");
  } finally {
    vi.useRealTimers();
  }
});

test("👎 — отклик «Учту: скорректирую, о чём пишу»; частота ассистента не меняется", async () => {
  render(<Host />);
  load([agentMsg("m1")]);
  await userEvent.click(screen.getByRole("button", { name: "👎 Не по теме" }));
  expect(reactChat).toHaveBeenCalledWith(ep, "m1", "👎", true);
  expect(within(msgRow("Сообщение m1")).getByText("Учту: скорректирую, о чём пишу")).toBeInTheDocument();
  expect(announced()).toHaveTextContent("Учту: скорректирую, о чём пишу");
  expect(chat.agent?.frequency).toBe("чаще");
});

test("снять реакцию — без отклика; реакция не дошла — отклик уходит", async () => {
  render(<Host />);
  load([agentMsg("m1", { reactions: { "👍": 1 } })]);
  await userEvent.click(screen.getByRole("button", { name: "👍 Полезно" }));
  expect(reactChat).toHaveBeenLastCalledWith(ep, "m1", "👍", false);
  expect(within(msgRow("Сообщение m1")).queryByText(/Учту/)).toBeNull();
  vi.mocked(reactChat).mockRejectedValueOnce(new Error("409"));
  await userEvent.click(screen.getByRole("button", { name: "👎 Не по теме" }));
  expect(within(msgRow("Сообщение m1")).queryByText(/Учту/)).toBeNull();
  expect(chat.note).toMatch(/Реакция не дошла/);
});

test("❓ — «Ассистент поясняет…», пока не придёт пояснение; оно ссылается на сообщение", async () => {
  const now = Date.now() / 1000;
  render(<Host />);
  load([agentMsg("m1", { text: "Риск: интеграция **без владельца**, а от неё зависит запуск 15.11", at: now - 60 })]);
  await userEvent.click(screen.getByRole("button", { name: "❓ Поясни" }));
  const row = () => msgRow("без владельца");
  expect(within(row()).getByText("Ассистент поясняет…")).toBeInTheDocument();
  expect(within(row()).queryByText(/Учту/)).toBeNull();
  expect(announced()).toHaveTextContent("Ассистент поясняет…");
  // ответ с explains пишется — у его пузыря уже метка «пояснение», второй индикатор не нужен
  act(() => chat.sink.onChat({ seq: 21, op: "patch", id: "m1", set: { reactions: { "❓": now } } }));
  act(() => chat.sink.onChat({ seq: 22, op: "add", message: agentMsg("m2", {
    status: "writing", mode: "reply", text: "", explains: "m1", at: now + 1 }) }));
  expect(within(row()).queryByText("Ассистент поясняет…")).toBeNull();
  act(() => chat.sink.onChat({ seq: 23, op: "patch", id: "m2", set: { status: "shown", text: "Олег в [00:05] не назвал владельца." } }));
  expect(within(row()).queryByText("Ассистент поясняет…")).toBeNull();
  const reply = msgRow(/не назвал владельца/);
  expect(within(reply).getByText("пояснение")).toBeInTheDocument();
  // Ссылка — с короткой цитатой без разметки.
  const ref = within(reply).getByRole("button", { name: /^к сообщению «Риск: интеграция без владельца, а от/ });
  expect(ref.textContent).toMatch(/…»$/);
  const scrolled = vi.fn();
  row().scrollIntoView = scrolled;
  await userEvent.click(ref);
  expect(scrolled).toHaveBeenCalled();
  expect(row()).toHaveFocus();
  expect(row()).toHaveClass("is-flash");
});

test("❓ — промолчал: строка «нечего добавить» с re на сообщение снимает ожидание", () => {
  const now = Date.now() / 1000;
  render(<Host />);
  load([agentMsg("m1", { reactions: { "❓": now }, at: now - 60 })]);
  expect(screen.getByText("Ассистент поясняет…")).toBeInTheDocument();
  act(() => chat.sink.onChat({ seq: 21, op: "add", message: {
    id: "m2", seq: 21, at: now + 2, kind: "system", text: "Ассистенту нечего добавить", re: "m1" } }));
  expect(screen.queryByText("Ассистент поясняет…")).toBeNull();
});

test("❓ — старое (дольше EXPLAIN_WAIT_S) или агент выключен: «поясняет…» не показывается", () => {
  const now = Date.now() / 1000;
  const { rerender } = render(<Host />);
  load([agentMsg("m1", { reactions: { "❓": now - EXPLAIN_WAIT_S - 5 } }), agentMsg("m2", { reactions: { "❓": now } })]);
  expect(within(msgRow("Сообщение m1")).queryByText("Ассистент поясняет…")).toBeNull();
  expect(within(msgRow("Сообщение m2")).getByText("Ассистент поясняет…")).toBeInTheDocument();
  rerender(<Host disabled />);
  expect(screen.queryByText("Ассистент поясняет…")).toBeNull();
});
