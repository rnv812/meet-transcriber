import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  clickChat: vi.fn(async () => ({ ok: true, id: "m9" })),
  reactChat: vi.fn(async () => ({ ok: true, changed: true })),
  confirmChat: vi.fn(async () => ({ ok: true })),
  postChat: vi.fn(async () => ({ id: "m5", queued: false, attachments: [] })),
  newChatClientId: vi.fn(() => "c1"),
}));
import { clickChat, confirmChat, postChat, reactChat } from "../lib/api";
import type { ChatMessage, ChatSnapshot } from "../lib/types";
import { agentInfo, agentMsg, attMsg, userMsg } from "../test/chatFixtures";
import { GATE_TITLE, LiveChat } from "./LiveChat";
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
  const like = screen.getByRole("button", { name: "Полезно" });
  expect(screen.getByRole("button", { name: "Не по теме" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Поясни" })).toBeInTheDocument();
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
  await userEvent.click(screen.getByRole("button", { name: "Поясни" }));
  expect(screen.getByRole("button", { name: "Поясни" })).toHaveAttribute("aria-pressed", "false");
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

test("закреплённый вопрос — одной строкой (MeetLive): бейдж, текст с многоточием, «Показать в ленте», «×»; щелчок — полностью", async () => {
  render(<Host />);
  load([agentMsg("m1", { text: "Сказать **Анне** про срок? Она ждёт ответа до вечера", pin: true })]);
  const pin = screen.getByRole("region", { name: "Вопрос вам" });
  expect(pin.querySelector(".badge")).toHaveClass("badge", "badge--info");
  const line = within(pin).getByRole("button", { name: /Сказать Анне про срок/ });
  expect(line).toHaveTextContent("Сказать Анне про срок? Она ждёт ответа до вечера");   // без разметки
  expect(line).toHaveAttribute("aria-expanded", "false");
  expect(line).toHaveClass("chat-pin__line");
  expect(pin.querySelector(".chat-pin__row")!.children).toHaveLength(4);
  await userEvent.click(line);
  expect(line).toHaveAttribute("aria-expanded", "true");
  expect(pin).toHaveClass("is-open");
  await userEvent.click(line);
  expect(pin).not.toHaveClass("is-open");
});

test("«Показать в ленте» у закреплённого — значком с тем же именем (место — тексту вопроса)", () => {
  render(<Host />);
  load([agentMsg("m1", { text: "Сказать Анне про срок?", pin: true })]);
  const show = within(screen.getByRole("region", { name: "Вопрос вам" })).getByRole("button", { name: "Показать в ленте" });
  expect(show).toHaveClass("btn--icon");
  expect(show.textContent).toBe("");
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
  for (const chip of msg.querySelectorAll(".chat-att")) expect(chip).toHaveClass("badge", "badge--plain");
});

test("системные строки и строки встречи", () => {
  render(<Host />);
  load([{ ...agentMsg("s1"), kind: "system", text: "Ассистенту нечего добавить" }, { ...agentMsg("e1"), kind: "meeting", text: "— часть 2 —" }]);
  expect(log()).toHaveTextContent("Ассистенту нечего добавить");
  expect(log()).toHaveTextContent("— часть 2 —");
});

test("строка ворот согласия — та же тихая системная строка, с пояснением (0.3.7)", () => {
  render(<Host />);
  const text = "Ассистент хотел без согласия: открыть C:/Users/u/Downloads/spec.pdf — запрос заблокирован";
  load([{ ...agentMsg("s2"), kind: "system", text, gate: true }]);
  const line = within(log()).getByText(text);
  expect(line).toHaveClass("chat-sys", "chat-sys--gate");
  expect(line).toHaveAttribute("aria-description", GATE_TITLE);
  expect(line).not.toHaveAttribute("title");
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
  fireEvent.click(screen.getByRole("button", { name: "Полезно" }));
  expect(announced()).toHaveTextContent("Учту: такое полезно");
  expect(within(msgRow("Сообщение m1")).queryByRole("status")).toBeNull();
});

test("непоставленные реакции вне наведения спрятаны только глазам: в дереве доступности они есть", () => {
  render(<Host />);
  load([agentMsg("m1", { reactions: { "👍": 1 } })]);
  const row = msgRow("Сообщение m1");
  for (const name of ["Полезно", "Не по теме", "Поясни"]) {
    expect(within(row).getByRole("button", { name })).toBeInTheDocument();
  }
});

test("реакции: формальные подписи у кнопок, в подсказке — что будет", () => {
  render(<Host />);
  load([agentMsg("m1")]);
  const row = msgRow("Сообщение m1");
  const like = within(row).getByRole("button", { name: "Полезно" });
  const dislike = within(row).getByRole("button", { name: "Не по теме" });
  const explain = within(row).getByRole("button", { name: "Поясни" });
  expect(like).toHaveAccessibleDescription("Полезно — ассистент будет писать больше такого");
  expect(dislike).toHaveAccessibleDescription("Не по теме — ассистент поймёт, что промахнулся, и скорректирует, о чём писать");
  expect(explain).toHaveAccessibleDescription("Поясни — ассистент объяснит, на что опирался");
  // подпись рядом со значком Lucide (видна при наведении и фокусе — CSS), без эмодзи; прежних «норм» нет
  for (const [btn, label] of [[like, "Полезно"], [dislike, "Не по теме"], [explain, "Поясни"]] as const) {
    expect(btn).toHaveTextContent(new RegExp(`^${label}$`));
    expect(btn.querySelector("svg.lucide")).not.toBeNull();
  }
  expect(row.querySelector(".chat-msg__tools")).not.toHaveTextContent(/[👍👎❓]/u);
  expect(row).not.toHaveTextContent(/норм|вопрос/);
});

test("узкая панель: у реакций только значок, подпись и что будет — в подсказке и aria", () => {
  render(<Host compact />);
  load([agentMsg("m1")]);
  const like = screen.getByRole("button", { name: "Полезно" });
  expect(like).toHaveTextContent(/^$/);
  expect(like.querySelector("svg.lucide-thumbs-up")).not.toBeNull();
  expect(like).toHaveAccessibleDescription("Полезно — ассистент будет писать больше такого");
  const dislike = screen.getByRole("button", { name: "Не по теме" });
  expect(dislike).toHaveTextContent(/^$/);
  expect(dislike.querySelector("svg.lucide-thumbs-down")).not.toBeNull();
});

test("👍 — отклик «Учту: такое полезно» сразу, гаснет, кнопка остаётся нажатой", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    render(<Host />);
    load([agentMsg("m1")]);
    const like = screen.getByRole("button", { name: "Полезно" });
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
  await userEvent.click(screen.getByRole("button", { name: "Не по теме" }));
  expect(reactChat).toHaveBeenCalledWith(ep, "m1", "👎", true);
  expect(within(msgRow("Сообщение m1")).getByText("Учту: скорректирую, о чём пишу")).toBeInTheDocument();
  expect(announced()).toHaveTextContent("Учту: скорректирую, о чём пишу");
  expect(chat.agent?.frequency).toBe("чаще");
});

test("снять реакцию — без отклика; реакция не дошла — отклик уходит", async () => {
  render(<Host />);
  load([agentMsg("m1", { reactions: { "👍": 1 } })]);
  await userEvent.click(screen.getByRole("button", { name: "Полезно" }));
  expect(reactChat).toHaveBeenLastCalledWith(ep, "m1", "👍", false);
  expect(within(msgRow("Сообщение m1")).queryByText(/Учту/)).toBeNull();
  vi.mocked(reactChat).mockRejectedValueOnce(new Error("409"));
  await userEvent.click(screen.getByRole("button", { name: "Не по теме" }));
  expect(within(msgRow("Сообщение m1")).queryByText(/Учту/)).toBeNull();
  expect(chat.note).toMatch(/Реакция не дошла/);
});

test("❓ — «Ассистент поясняет…», пока не придёт пояснение; оно ссылается на сообщение", async () => {
  const now = Date.now() / 1000;
  render(<Host />);
  load([agentMsg("m1", { text: "Риск: интеграция **без владельца**, а от неё зависит запуск 15.11", at: now - 60 })]);
  await userEvent.click(screen.getByRole("button", { name: "Поясни" }));
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

const card = (id: string, o: Partial<ChatMessage> = {}): ChatMessage => ({
  ...agentMsg(id), kind: "system", status: undefined, mode: undefined, card: "confirm", tool: "Bash", title: "команду",
  text: "Ассистент хочет выполнить: команду", args: "echo hi > out.txt", expires_at: Date.now() / 1000 + 120, ...o,
});
/**
 * Карточки подтверждения решают прямо в ленте, кнопками самой карточки: отдельной области
 * над лентой нет (после встречи, во вкладке «Ассистент», её было не видно — ход висел).
 */
const cards = () => log();

test("карточка подтверждения Meet: в ленте, точный вызов, кнопки в самой карточке; «Разрешить один раз» — confirmChat", async () => {
  render(<Host />);
  load([agentMsg("m1"), card("m2")]);
  expect(screen.queryByRole("region", { name: "Подтверждение действия" })).toBeNull();
  const box = within(log()).getByRole("group", { name: "Ассистент хочет выполнить: команду" });
  expect(box).toHaveTextContent("echo hi > out.txt");
  expect(log()).not.toHaveTextContent("над лентой");
  expect(within(log()).getAllByRole("button", { name: "Разрешить один раз" })).toHaveLength(1);
  await userEvent.click(within(box).getByRole("button", { name: "Разрешить один раз" }));
  expect(confirmChat).toHaveBeenCalledWith(ep, "m2", true, false);
});

test("ждущая решения карточка — в порядке Tab ленты, даже если после неё пришло сообщение", async () => {
  render(<Host />);
  load([card("m2"), agentMsg("m3", { text: "Жду вашего решения" })]);
  const box = within(log()).getByRole("group", { name: "Ассистент хочет выполнить: команду" });
  const allow = within(box).getByRole("button", { name: "Разрешить один раз" });
  expect(allow).not.toHaveAttribute("tabindex", "-1");
});

test("карточка: длинный вызов — начало и конец, «Разрешить» доступна сразу, «Показать полностью» — по желанию; Esc", async () => {
  render(<Host />);
  const full = `ls${"x".repeat(700)}; rm -rf ~`;
  const preview = "lsxxxx\n…⟨скрыто: 0 строк, 700 симв.⟩…\nxx; rm -rf ~";
  load([card("m2", { args: full, preview, size: "1 строка, 712 симв." })]);
  const region = cards()!;
  expect(region).toHaveTextContent("1 строка, 712 симв.");
  expect(region).toHaveTextContent("rm -rf ~");                        // хвост виден всегда
  expect(region).toHaveTextContent("скрыто: 0 строк, 700 симв.");
  expect(within(region).getByRole("button", { name: "Разрешить один раз" })).toBeEnabled();
  await userEvent.click(within(region).getByRole("button", { name: "Показать полностью" }));
  expect(region).toHaveTextContent(full);
  within(region).getByRole("button", { name: "Отклонить" }).focus();
  await userEvent.keyboard("{Escape}");
  expect(confirmChat).toHaveBeenCalledWith(ep, "m2", false, false);
});

test("карточка: «Разрешать такое до конца встречи» — только если Meet её предлагает; предупреждения крупно", async () => {
  render(<Host />);
  load([card("m2", { grant: { key: "shell:Bash:npm", label: "Bash npm" },
    warnings: ["⚠ Без песочницы Claude Code (dangerouslyDisableSandbox)"] }), card("m3", { args: "make" })]);
  const region = cards()!;
  expect(region).toHaveTextContent("⚠ Без песочницы Claude Code");
  const grants = within(region).getAllByRole("button", { name: "Разрешать такое до конца встречи" });
  expect(grants).toHaveLength(1);
  expect(grants[0]).toHaveAccessibleDescription(expect.stringContaining("Bash npm"));
  await userEvent.click(grants[0]!);
  expect(confirmChat).toHaveBeenCalledWith(ep, "m2", true, true);
});

// Карточки как их собирает резидент (`consent.card_for`, grant-polish): текст по инструменту, без JSON-экранирования.
const WRITE_CARD: Partial<ChatMessage> = {
  tool: "Write", title: "запись в файл", size: "17 строк, 263 симв.",
  args: "Записать файл: C:\\Users\\demo\\Встречи\\2026-10-07_11-00\\код мерчанта.txt\n│ # Код мерчанта\n│ строка 1\n│ строка 2\n│ строка 3\n│ строка 4\n│ строка 5\n│ строка 6\n│ строка 7\n│ строка 8\n│ строка 9\n│ строка 10\n│ строка 11\n│ строка 12\n│ строка 13\n│ строка 14\n│ КРЫЖОВНИК-7741",
  preview: "Записать файл: C:\\Users\\demo\\Встречи\\2026-10-07_11-00\\код мерчанта.txt\n│ # Код мерчанта\n│ строка 1\n│ строка 2\n│ строка 3\n│ строка 4\n…⟨скрыто: 7 строк, 80 симв.⟩…\n│ строка 12\n│ строка 13\n│ строка 14\n│ КРЫЖОВНИК-7741",
  grant: { key: "write:files:x", label: "изменение файлов в C:\\Users\\demo\\Встречи\\2026-10-07_11-00" },
};
const EDIT_CARD: Partial<ChatMessage> = {
  tool: "Edit", title: "правку файла", size: "5 строк, 159 симв.",
  args: "Изменить файл: C:\\Users\\demo\\Встречи\\2026-10-07_11-00\\код мерчанта.txt\nЗаменить ВСЕ вхождения (replace_all: true)\n− КРЫЖОВНИК-7741\n+ КРЫЖОВНИК-7741\n+ проверено",
};
const MCP_CARD: Partial<ChatMessage> = {
  tool: "mcp__team-jira__jira_create_issue", title: "MCP team-jira → jira_create_issue", size: "6 строк, 125 симв.",
  args: "{\n  \"project\": \"ABC\",\n  \"summary\": \"Запуск 28.11\",\n  \"attachment\": \"C:\\Users\\demo\\spec.md\",\n  \"labels\": [\"release\", \"risk\"]\n}",
};
const WEB_CARD: Partial<ChatMessage> = {
  tool: "WebFetch", title: "открыть адрес", size: "5 строк, 135 симв.",
  args: "хост: evil.com\n⚠ в адресе есть часть до @ — запрос уйдёт на evil.com\nадрес: https://good.com@evil.com/x\nчто найти:\n│ x) (хост: good.com",
  warnings: ["⚠ в адресе есть часть до @ — запрос уйдёт на evil.com"],
};
const cardBox = (region: HTMLElement, title: string) =>
  within(region).getByRole("group", { name: `Ассистент хочет выполнить: ${title}` });

test("карточки по инструменту: запись, правка, MCP — снимки (grant-polish)", () => {
  render(<Host />);
  load([card("m2", WRITE_CARD), card("m3", EDIT_CARD), card("m4", MCP_CARD)]);
  const region = cards()!;
  const write = cardBox(region, "запись в файл");
  const edit = cardBox(region, "правку файла");
  const mcp = cardBox(region, "MCP team-jira → jira_create_issue");
  expect(write).toMatchSnapshot("Write");
  expect(edit).toMatchSnapshot("Edit");
  expect(mcp).toMatchSnapshot("MCP");
  // Пути — с одной «\», без JSON-экранирования; правка — обе стороны и replace_all явно.
  expect(write.querySelector("pre")!.textContent).toContain("C:\\Users\\demo\\Встречи");
  expect(write.querySelector("pre")!.textContent).not.toContain("\\\\");
  expect(edit.querySelector("pre")!.textContent).toBe(EDIT_CARD.args);
  expect(mcp.querySelector("pre")!.textContent).toContain("\"attachment\": \"C:\\Users\\demo\\spec.md\"");
  expect(within(write).getByRole("button", { name: "Разрешать такое до конца встречи" }))
    .toHaveAccessibleDescription("Дальше до конца встречи без вопросов: изменение файлов в C:\\Users\\demo\\Встречи\\2026-10-07_11-00");
});

test("карточка WebFetch: настоящий хост первой строкой, часть до @ — предупреждением (ревью GP1)", () => {
  render(<Host />);
  load([card("m2", WEB_CARD)]);
  const web = cardBox(cards()!, "открыть адрес");
  expect(within(web).getByRole("note")).toHaveTextContent("запрос уйдёт на evil.com");
  expect(web.querySelector("pre")!.textContent!.split("\n")[0]).toBe("хост: evil.com");
  expect(web).toMatchSnapshot("WebFetch");
});

test("карточка записи: начало и конец с пометкой, «Показать полностью» — всё содержимое", async () => {
  render(<Host />);
  load([card("m2", WRITE_CARD)]);
  const write = cardBox(cards()!, "запись в файл");
  const pre = () => write.querySelector("pre")!.textContent;
  expect(pre()).toBe(WRITE_CARD.preview);
  expect(pre()).toContain("…⟨скрыто: 7 строк, 80 симв.⟩…");
  expect(pre()).not.toContain("строка 8");
  await userEvent.click(within(write).getByRole("button", { name: "Показать полностью" }));
  expect(pre()).toBe(WRITE_CARD.args);
  for (let i = 1; i <= 14; i++) expect(pre()).toContain(`│ строка ${i}\n`);
  expect(write).toMatchSnapshot("Write, полностью");
});

test("решённая или просроченная карточка — без кнопок, в ленте — итог", () => {
  render(<Host />);
  load([card("m2", { decision: "allow" }), card("m3", { expires_at: Date.now() / 1000 - 5 }),
    card("m4", { decision: "timeout" })]);
  expect(within(log()).queryByRole("button", { name: "Разрешить один раз" })).toBeNull();
  expect(within(log()).queryByRole("button", { name: "Отклонить" })).toBeNull();
  expect(log()).toHaveTextContent("Разрешено один раз");
  expect(log()).toHaveTextContent("Время вышло — не выполнено");
});

// --- Atlas Aurora (0.4, этап 5, задача 2): вид ленты; роли, подписи и поведение — прежние ---------

describe("Atlas Aurora", () => {
  test("сообщение агента — плоская карточка (0.5) со знаком агента; пишет — знак «пишет»", () => {
    render(<Host />);
    load([agentMsg("m1"), userMsg("m3", { text: "мой вопрос" }), { ...agentMsg("s1"), kind: "system", text: "Сбой" },
      agentMsg("m2", { status: "writing", text: "" })]);
    const done = msgRow("Сообщение m1");
    expect(done).toHaveClass("chat-msg--agent");
    expect(done).not.toHaveClass("aurora-wash");
    expect(done.querySelector(".chat-msg__head .agent-mark")).toHaveAttribute("data-state", "rest");
    act(() => chat.sink.onChatPartial({ id: "m2", text: "Сроки по встрече" }));
    const writing = within(log()).getByText("Сроки по встрече").closest("li")!;
    expect(writing).not.toHaveClass("aurora-wash");
    expect(writing.querySelector(".chat-msg__head .agent-mark")).toHaveAttribute("data-state", "write");
    // Ваши сообщения и системные строки — тоже без сияния.
    expect(msgRow("мой вопрос")).not.toHaveClass("aurora-wash");
    expect(within(log()).getByText("Сбой")).not.toHaveClass("aurora-wash");
  });

  test("кнопки агента — .filter с aria-pressed; у нажатой — без символа «✓» в подписи", async () => {
    render(<Host />);
    load([agentMsg("m1", { buttons: ["Глянь", "Только сроки"] })]);
    const group = screen.getByRole("group", { name: "Ответить ассистенту" });
    for (const b of within(group).getAllByRole("button")) {
      expect(b).toHaveClass("filter");
      expect(b).toHaveAttribute("aria-pressed", "false");
    }
    await userEvent.click(within(group).getByRole("button", { name: "Только сроки" }));
    const used = within(group).getByRole("button", { name: "Только сроки" });
    expect(used).toHaveAttribute("aria-pressed", "true");
    expect(used).toHaveTextContent(/^Только сроки$/);
  });

  test("реакции и копирование — кнопки Aurora; поставленная — нажатая (aria-pressed)", () => {
    render(<Host />);
    load([agentMsg("m1", { reactions: { "👎": 1 } })]);
    const row = msgRow("Сообщение m1");
    const group = within(row).getByRole("group", { name: "Реакция" });
    for (const b of within(group).getAllByRole("button")) expect(b).toHaveClass("btn", "btn--ghost");
    expect(within(group).getByRole("button", { name: "Не по теме" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "Поясни" }).querySelector("svg")).not.toBeNull();
    expect(within(row).getByRole("button", { name: "Копировать" })).toHaveClass("btn");
  });

  test("источник под сообщением — кнопка Aurora со значком и именем", () => {
    render(<Host />);
    load([attMsg("a1", { type: "doc", name: "Регламент API.pdf", path: "C:/r/x/assistant/files/a1.pdf" }),
      userMsg("m0", { text: "вот", attachments: ["a1"] }),
      agentMsg("m1", { text: "См. Регламент API.pdf" })]);
    const src = within(msgRow("См. Регламент API.pdf")).getByRole("button", { name: "Источник: Регламент API.pdf" });
    expect(src).toHaveClass("btn", "btn--outline");
    expect(src).toHaveTextContent("Регламент API.pdf");
    expect(src.querySelector("svg")).not.toBeNull();
  });

  test("низ карточки (макет MeetLive): источники, промежуток, реакции и копирование — одной строкой; кнопки ответа — под ней", () => {
    render(<Host />);
    load([attMsg("a1", { type: "doc", name: "Регламент API.pdf", path: "C:/r/x/assistant/files/a1.pdf" }),
      userMsg("m0", { text: "вот", attachments: ["a1"] }),
      agentMsg("m1", { text: "См. Регламент API.pdf", buttons: ["Только сроки"] })]);
    const row = msgRow("См. Регламент API.pdf");
    const foot = row.querySelector<HTMLElement>(".chat-msg__foot")!;
    expect(foot).not.toBeNull();
    const src = within(foot).getByRole("button", { name: "Источник: Регламент API.pdf" });
    const like = within(foot).getByRole("button", { name: "Полезно" });
    within(foot).getByRole("button", { name: "Копировать" });
    expect(foot.querySelector(".chat-msg__gap")).not.toBeNull();
    // Порядок: источник → промежуток → реакции; кнопки ответа агента — после строки.
    expect(src.compareDocumentPosition(like) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const reply = within(row).getByRole("button", { name: "Только сроки" });
    expect(foot.compareDocumentPosition(reply) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Размер — sm (32 px), как у кнопок ответа `.filter` в той же карточке: не плотные xs.
    for (const b of [src, like, within(foot).getByRole("button", { name: "Копировать" })]) {
      expect(b).toHaveClass("btn--sm");
      expect(b).not.toHaveAttribute("data-density");
    }
  });

  test("низ карточки без источников и текста-ответа не рисуется пустым", () => {
    render(<Host />);
    load([agentMsg("m1", { text: "", status: "failed", error: "сбой" })]);
    expect(within(log()).getByText("сбой").closest("li")!.querySelector(".chat-msg__foot")).toBeNull();
  });

  test("закреплённый вопрос: метка «Вопрос вам», кнопки — .filter, «Показать в ленте» ведёт к сообщению", async () => {
    render(<Host />);
    load([agentMsg("m1", { text: "Сказать Анне про срок?", pin: true, buttons: ["Да", "Нет"] })]);
    const pin = screen.getByRole("region", { name: "Вопрос вам" });
    expect(pin.querySelector(".badge")).toHaveTextContent("Вопрос вам");
    for (const b of within(pin).getAllByRole("button", { name: /^(Да|Нет)$/ })) expect(b).toHaveClass("filter");
    const row = within(log()).getByText("Сказать Анне про срок?").closest("li")!;
    const scrolled = vi.fn();
    row.scrollIntoView = scrolled;
    await userEvent.click(within(pin).getByRole("button", { name: "Показать в ленте" }));
    expect(scrolled).toHaveBeenCalled();
    expect(row).toHaveFocus();
    expect(row).toHaveClass("is-flash");
    expect(within(pin).getByRole("button", { name: "Убрать из закреплённых" })).toHaveClass("btn");
  });

  test("не отправлено — «Повторить» ссылкой Aurora, сообщение с рамкой ошибки", async () => {
    vi.mocked(postChat).mockRejectedValueOnce(new Error("нет связи"));
    render(<Host />);
    load([agentMsg("m1")]);
    act(() => void chat.send("что с бюджетом?"));
    const failed = await within(log()).findByText(/^Не отправлено/);
    expect(failed.closest("li")).toHaveClass("chat-msg--user", "is-failed");
    expect(within(failed).getByRole("button", { name: "Повторить" })).toHaveClass("btn", "btn--link");
  });

  test("«↓ N новых» — кнопка Aurora с прежней подписью", () => {
    render(<Host />);
    load([agentMsg("m1"), agentMsg("m2")]);
    scrollTo(log().parentElement!, { top: 100 });
    act(() => chat.sink.onChat({ seq: 21, op: "add", message: agentMsg("m3") }));
    expect(screen.getByRole("button", { name: "↓ 1 новое" })).toHaveClass("btn", "chat__new");
  });

  test("карточка подтверждения: кнопки Aurora, «Показать полностью» — ссылкой; подписи прежние", () => {
    render(<Host />);
    load([card("m2", { grant: { key: "k", label: "Bash npm" }, preview: "ls…", args: "ls -la" })]);
    const region = cards()!;
    for (const name of ["Разрешить один раз", "Разрешать такое до конца встречи", "Отклонить"]) {
      expect(within(region).getByRole("button", { name })).toHaveClass("btn");
    }
    expect(within(region).getByRole("button", { name: "Показать полностью" })).toHaveClass("btn", "btn--link");
  });
});
