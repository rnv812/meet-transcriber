import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const h = vi.hoisted(() => ({
  drop: null as null | ((e: unknown) => void),
  overChat: vi.fn(() => true),
  pick: vi.fn(async () => [] as string[]),
}));
vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  postChat: vi.fn(async () => ({ id: "m5", queued: false, attachments: [] })),
  pasteChatImage: vi.fn(),
  attachChatFile: vi.fn(),
  stopChat: vi.fn(async () => ({ ok: true })),
  removeChatAttachment: vi.fn(async () => ({ ok: true, changed: true })),
  newChatClientId: vi.fn(() => "c1"),
}));
vi.mock("../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../lib/shell")>()),
  inTauri: () => true,
  onFileDrop: vi.fn(async (cb: (e: unknown) => void) => { h.drop = cb; return () => { h.drop = null; }; }),
  overChatDrop: h.overChat,
  pickChatFiles: h.pick,
}));
import { attachChatFile, pasteChatImage, postChat, removeChatAttachment, stopChat } from "../lib/api";
import type { ChatMessage, ChatSnapshot } from "../lib/types";
import { agentInfo, agentMsg, attMsg } from "../test/chatFixtures";
import { ChatComposer } from "./ChatComposer";
import { LiveChat } from "./LiveChat";
import { type Chat, useChat } from "./useChat";

const ep = { base: "http://h", token: "t" };
let chat: Chat;

function Host({ reason = null, vision = true }: { reason?: string | null; vision?: boolean }) {
  chat = useChat(ep);
  return (
    <>
      <LiveChat chat={chat} />
      <ChatComposer chat={chat} disabledReason={reason} vision={vision} />
    </>
  );
}
const field = () => screen.getByRole("combobox", { name: "Сообщение ассистенту" });
const load = (messages: ChatMessage[] = [], seq = 1) =>
  act(() => chat.sink.onChatSnapshot({ messages, seq, agent: agentInfo() } as ChatSnapshot));
const log = () => screen.getByRole("log");

beforeEach(() => {
  vi.clearAllMocks();
  h.overChat.mockReturnValue(true);
});

test("Enter отправляет, поле очищается и фокус остаётся в нём; сообщение сразу в ленте", async () => {
  render(<Host />);
  load();
  await userEvent.click(field());
  await userEvent.type(field(), "что с бюджетом?{Enter}");
  expect(postChat).toHaveBeenCalledWith(ep, { text: "что с бюджетом?", client_id: "c1", attachments: [] });
  expect(field()).toHaveValue("");
  expect(field()).toHaveFocus();
  expect(log()).toHaveTextContent("что с бюджетом?");
});

test("Shift+Enter — новая строка, не отправка", async () => {
  render(<Host />);
  load();
  await userEvent.type(field(), "раз{Shift>}{Enter}{/Shift}два");
  expect(field()).toHaveValue("раз\nдва");
  expect(postChat).not.toHaveBeenCalled();
});

test("Enter во время IME-набора не отправляет", () => {
  render(<Host />);
  load();
  fireEvent.change(field(), { target: { value: "привет" } });
  fireEvent.keyDown(field(), { key: "Enter", isComposing: true });
  fireEvent.keyDown(field(), { key: "Enter", keyCode: 229 });
  expect(postChat).not.toHaveBeenCalled();
  fireEvent.keyDown(field(), { key: "Enter" });
  expect(postChat).toHaveBeenCalledTimes(1);
});

test("Ctrl+V с картинкой — pasteChatImage, превью с «×»; в ленте вложения нет до отправки", async () => {
  let done!: (v: unknown) => void;
  vi.mocked(pasteChatImage).mockReturnValue(new Promise((r) => { done = r; }) as never);
  render(<Host />);
  load();
  const png = new File([new Uint8Array([137, 80, 78, 71])], "image.png", { type: "image/png" });
  fireEvent.paste(field(), { clipboardData: { files: [png], getData: () => "" } });
  expect(pasteChatImage).toHaveBeenCalledWith(ep, png, "Скриншот.png");
  const atts = screen.getByRole("list", { name: "Вложения" });
  expect(atts).toHaveTextContent("загружается…");
  // Чип вложения — бейдж Aurora без точки (с миниатюрой и «×»).
  expect(within(atts).getByRole("listitem")).toHaveClass("badge", "badge--plain", "chat-draft");
  expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
  await act(async () => {
    done({ id: "a1", status: "ready", attachment: attMsg("a1", { name: "Скриншот.png" }) });
  });
  // Журнал уже прислал запись вложения — в ленте её всё равно нет.
  act(() => chat.sink.onChat({ seq: 2, op: "add", message: attMsg("a1", { name: "Скриншот.png" }) }));
  expect(log()).not.toHaveTextContent("Скриншот.png");
  await userEvent.click(screen.getByRole("button", { name: "Отправить" }));
  expect(postChat).toHaveBeenCalledWith(ep, { text: "", client_id: "c1", attachments: ["a1"] });
  expect(screen.queryByRole("list", { name: "Вложения" })).toBeNull();
  expect(log()).toHaveTextContent("Скриншот.png");
});

test("убранное вложение не уходит, в ленте не появляется и убирается у ассистента", async () => {
  vi.mocked(attachChatFile).mockResolvedValue({ id: "a2", status: "ready", attachment: attMsg("a2", { type: "doc", name: "План.pptx" }) } as never);
  render(<Host />);
  load();
  await vi.waitFor(() => expect(h.drop).not.toBeNull());
  await act(async () => { h.drop!({ type: "drop", x: 10, y: 10, paths: ["C:\\docs\\План.pptx"] }); });
  expect(attachChatFile).toHaveBeenCalledWith(ep, "C:\\docs\\План.pptx");
  await userEvent.click(screen.getByRole("button", { name: "Убрать вложение План.pptx" }));
  expect(removeChatAttachment).toHaveBeenCalledWith(ep, "a2");
  await userEvent.type(field(), "без файла{Enter}");
  expect(postChat).toHaveBeenCalledWith(ep, { text: "без файла", client_id: "c1", attachments: [] });
  expect(log()).not.toHaveTextContent("План.pptx");
});

test("перетаскивание: подсветка над чатом; брошенное мимо чата не прикладывается", async () => {
  render(<Host />);
  load();
  await vi.waitFor(() => expect(h.drop).not.toBeNull());
  act(() => h.drop!({ type: "over", x: 5, y: 5 }));
  expect(screen.getByText("Отпустите, чтобы приложить")).toBeInTheDocument();
  act(() => h.drop!({ type: "leave" }));
  expect(screen.queryByText("Отпустите, чтобы приложить")).toBeNull();
  h.overChat.mockReturnValue(false);
  await act(async () => { h.drop!({ type: "drop", x: 900, y: 5, paths: ["C:\\a.pdf"] }); });
  expect(attachChatFile).not.toHaveBeenCalled();
});

test("«📎» — диалог оболочки, файлы уходят attachChatFile; не разобрано — видно", async () => {
  h.pick.mockResolvedValue(["C:\\docs\\Сломанный.pdf"]);
  vi.mocked(attachChatFile).mockResolvedValue({ id: "a4", status: "failed", error: "битый PDF", attachment: attMsg("a4") } as never);
  render(<Host />);
  load();
  await userEvent.click(screen.getByRole("button", { name: "Приложить файл" }));
  expect(attachChatFile).toHaveBeenCalledWith(ep, "C:\\docs\\Сломанный.pdf");
  expect(await screen.findByText("не приложено: битый PDF")).toBeInTheDocument();
});

test("картинка для модели без зрения — с пометкой", async () => {
  vi.mocked(pasteChatImage).mockResolvedValue({ id: "a1", status: "ready", attachment: attMsg("a1") } as never);
  render(<Host vision={false} />);
  load();
  const png = new File([new Uint8Array([1])], "shot.png", { type: "image/png" });
  await act(async () => { fireEvent.paste(field(), { clipboardData: { files: [png], getData: () => "" } }); });
  expect(within(screen.getByRole("list", { name: "Вложения" })).getByText("модель не видит изображения")).toBeInTheDocument();
});

test("«Стоп» — пока ответ пишется и виден", async () => {
  render(<Host />);
  load([agentMsg("m1", { status: "writing", text: "" })]);
  expect(screen.queryByRole("button", { name: "Остановить ответ" })).toBeNull();
  act(() => chat.sink.onChatPartial({ id: "m1", text: "Сейчас" }));
  await userEvent.click(screen.getByRole("button", { name: "Остановить ответ" }));
  expect(stopChat).toHaveBeenCalledWith(ep, "m1");
});

test("Atlas Aurora: строка ввода — aurora-edge; быстрые вопросы — кнопки над полем; «Стоп» — кнопка со значком", () => {
  render(<Host />);
  load([agentMsg("m1", { status: "writing", text: "" })]);
  const box = field().closest(".chat-compose")!;
  expect(box).toHaveClass("aurora-edge");
  const quick = screen.getByRole("group", { name: "Быстрые вопросы" });
  // Как в макете MeetLive: btn--outline btn--sm (32), не плотные 28.
  for (const b of within(quick).getAllByRole("button")) {
    expect(b).toHaveClass("btn", "btn--outline", "btn--sm");
    expect(b).not.toHaveAttribute("data-density");
  }
  // Над полем: группа идёт в документе раньше поля.
  expect(quick.compareDocumentPosition(field()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  act(() => chat.sink.onChatPartial({ id: "m1", text: "Сейчас" }));
  const stop = screen.getByRole("button", { name: "Остановить ответ" });
  expect(stop).toHaveClass("btn", "btn--outline");
  expect(stop).toHaveTextContent(/^Стоп$/);
  expect(stop.querySelector("svg")).not.toBeNull();
  expect(stop).toHaveAccessibleDescription("Остановить ответ ассистента");
});

test("агент выключен — поле недоступно и видно почему", () => {
  render(<Host reason="Ассистент выключен" />);
  expect(field()).toBeDisabled();
  expect(screen.getAllByRole("status").some((el) => el.textContent?.includes("Ассистент выключен"))).toBe(true);
  expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
});

test("убрали, пока вложение загружалось, — убирается у ассистента, когда загрузка кончится", async () => {
  let done!: (v: unknown) => void;
  vi.mocked(attachChatFile).mockReturnValue(new Promise((r) => { done = r; }) as never);
  render(<Host />);
  load();
  await vi.waitFor(() => expect(h.drop).not.toBeNull());
  await act(async () => { h.drop!({ type: "drop", x: 10, y: 10, paths: ["C:/docs/Большой.pdf"] }); });
  await userEvent.click(screen.getByRole("button", { name: "Убрать вложение Большой.pdf" }));
  expect(removeChatAttachment).not.toHaveBeenCalled();
  await act(async () => { done({ id: "a5", status: "ready", attachment: attMsg("a5", { type: "doc" }) }); });
  expect(removeChatAttachment).toHaveBeenCalledWith(ep, "a5");
  expect(screen.queryByRole("list", { name: "Вложения" })).toBeNull();
});

test("в буфере текст и картинка (Excel, Word) — вставляется только текст", () => {
  render(<Host />);
  load();
  const png = new File([new Uint8Array([1])], "image.png", { type: "image/png" });
  const event = fireEvent.paste(field(), {
    clipboardData: { files: [png], getData: (t: string) => (t === "text/plain" ? "A1\tB1" : "") },
  });
  expect(event).toBe(true); // не отменено — текст вставит браузер
  expect(pasteChatImage).not.toHaveBeenCalled();
  expect(screen.queryByRole("list", { name: "Вложения" })).toBeNull();
});
