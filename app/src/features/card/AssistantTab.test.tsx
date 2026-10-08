import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const h = vi.hoisted(() => ({
  drop: null as null | ((e: unknown) => void),
  pick: vi.fn(async () => [] as string[]),
  open: vi.fn(async (_path: string) => undefined),
}));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecordingChat: vi.fn(),
  continueChat: vi.fn(),
  recordingChatClick: vi.fn(async () => ({ message: {}, job: null })),
  recordingChatReact: vi.fn(async () => ({ ok: true, changed: true })),
  recordingChatConfirm: vi.fn(async () => ({ ok: true })),
  recordingChatPaste: vi.fn(),
  recordingChatAttach: vi.fn(),
  recordingChatRemove: vi.fn(async () => ({ ok: true, removed: true })),
  cancelJob: vi.fn(async () => ({})),
  getKbDocs: vi.fn(async () => ({ root: null, docs: [], more: false })),
  newChatClientId: vi.fn(() => "c1"),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: () => true,
  onFileDrop: vi.fn(async (cb: (e: unknown) => void) => { h.drop = cb; return () => { h.drop = null; }; }),
  overChatDrop: () => true,
  pickChatFiles: h.pick,
  openMaterial: h.open,
}));
import {
  cancelJob, continueChat, getKbDocs, getRecordingChat, recordingChatAttach, recordingChatClick, recordingChatConfirm,
  recordingChatPaste, recordingChatReact, recordingChatRemove,
} from "../../lib/api";
import type { ChatMessage, ChatUpdatedEvent, Job, RecordingChat } from "../../lib/types";
import { COPY_OPENED, resetKbDocs } from "../../live/useChat";
import { agentMsg, attMsg, userMsg } from "../../test/chatFixtures";
import { AssistantTab, PERSONAL_QUESTIONS, TRANSCRIBING } from "./AssistantTab";

const ep = { base: "http://h", token: "t" };
const ID = "2026-10-07_10-00";
const FOLDER = "C:\\rec\\2026-10-07_10-00";

const answer = (o: Partial<RecordingChat> = {}): RecordingChat =>
  ({ messages: [], seq: 0, legacy: null, live: false, job: null, enabled: true, ...o });
const chatJob = (state: Job["state"] = "running", o: Partial<Job> = {}): Job => ({
  id: "j1", kind: "chat", folder: FOLDER, state, stage: "chat", label: "ассистент отвечает", done: null, total: null,
  note: null, ...o,
} as Job);
const event = (o: Partial<ChatUpdatedEvent> = {}): ChatUpdatedEvent =>
  ({ kind: "chat.updated", id: ID, ts: Date.now() / 1000, ...o } as ChatUpdatedEvent);

function Tab({ jobs = [], ev = null, onOpenSettings }: { jobs?: Job[]; ev?: ChatUpdatedEvent | null; onOpenSettings?: (s: string) => void }) {
  return <AssistantTab endpoint={ep} id={ID} folder={FOLDER} jobs={jobs} event={ev} onOpenSettings={onOpenSettings} />;
}
const log = () => screen.getByRole("log", { name: "Чат с ассистентом" });
const ready = () => screen.findByRole("log", { name: "Чат с ассистентом" });
const field = () => screen.getByRole("textbox", { name: "Сообщение ассистенту" });

beforeEach(() => {
  vi.clearAllMocks();
  resetKbDocs();
});

const journal: ChatMessage[] = [
  userMsg("m1", { text: "Что решили по срокам?", t: undefined, after_meeting: true }),
  agentMsg("m2", { text: "Запуск — **15 ноября**.", mode: "reply", re: "m1", t: undefined, buttons: ["Подробнее", "Не надо"] }),
];

test("журнал встречи: та же лента — сообщения, кнопки агента, реакции, копирование", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  render(<Tab />);
  expect(await within(await ready()).findByText("15 ноября")).toBeInTheDocument();
  expect(within(log()).getByText("Что решили по срокам?")).toBeInTheDocument();
  expect(within(log()).getByRole("button", { name: "Копировать" })).toBeInTheDocument();
  await userEvent.click(within(log()).getByRole("button", { name: "Полезно" }));
  expect(recordingChatReact).toHaveBeenCalledWith(ep, ID, "m2", "👍", true);
  await userEvent.click(within(log()).getByRole("button", { name: "Подробнее" }));
  expect(recordingChatClick).toHaveBeenCalledWith(ep, ID, "m2", "Подробнее", "c1");
  expect(screen.getByRole("group", { name: "Продолжить разговор" })).toBeInTheDocument();
});

test("«Продолжить разговор»: сообщение уходит continueChat, видно сразу; запись журнала заменяет его", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  vi.mocked(continueChat).mockResolvedValue({ message: userMsg("m3", { text: "А бюджет?", client_id: "c1" }), job: chatJob("queued") });
  const view = render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  await userEvent.type(field(), "А бюджет?{Enter}");
  expect(continueChat).toHaveBeenCalledWith(ep, ID, { text: "А бюджет?", client_id: "c1", attachments: [] });
  expect(log()).toHaveTextContent("А бюджет?");
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    messages: [...journal, userMsg("m3", { text: "А бюджет?", client_id: "c1", t: undefined })], seq: 3,
  }));
  view.rerender(<Tab ev={event()} />);
  await waitFor(() => expect(getRecordingChat).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(within(log()).getAllByText("А бюджет?")).toHaveLength(1));
  expect(screen.queryByText("отправляется…")).toBeNull();
});

test("ход ответа: «думает…» пока задача идёт, потом текст ответа потоком и готовый ответ", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal.slice(0, 1), seq: 1, job: chatJob() }));
  const view = render(<Tab jobs={[chatJob()]} />);
  expect(await screen.findByText("Ассистент думает…")).toBeInTheDocument();
  // Задача начала ответ: запись «пишет» и её текст из chat.updated.partial.
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    messages: [...journal.slice(0, 1), agentMsg("m2", { status: "writing", text: "", mode: "reply", t: undefined })], seq: 2,
    job: chatJob(),
  }));
  view.rerender(<Tab jobs={[chatJob()]} ev={event({ partial: { id: "m2", text: "Запуск —" } })} />);
  expect(await within(await ready()).findByText("Запуск —")).toBeInTheDocument();
  expect(screen.queryByText("Ассистент думает…")).toBeNull();
  view.rerender(<Tab jobs={[chatJob()]} ev={event({ partial: { id: "m2", text: "Запуск — 15 ноября" } })} />);
  expect(await within(await ready()).findByText("Запуск — 15 ноября")).toBeInTheDocument();
  expect(getRecordingChat).toHaveBeenCalledTimes(2);      // известная запись — без перечитывания
  // Готово: задача кончилась — лента перечитана, ответ на месте.
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 3 }));
  view.rerender(<Tab jobs={[chatJob("done")]} />);
  expect(await within(await ready()).findByText("15 ноября")).toBeInTheDocument();
  expect(screen.queryByText("Ассистент думает…")).toBeNull();
});

test("«Стоп» после встречи снимает задачу ответа", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    messages: [...journal.slice(0, 1), agentMsg("m2", { status: "writing", text: "", mode: "reply", t: undefined })], seq: 2,
  }));
  const view = render(<Tab jobs={[chatJob()]} />);
  await within(await ready()).findByText("Что решили по срокам?");
  view.rerender(<Tab jobs={[chatJob()]} ev={event({ partial: { id: "m2", text: "Сейчас" } })} />);
  await userEvent.click(await screen.findByRole("button", { name: "Остановить ответ" }));
  expect(cancelJob).toHaveBeenCalledWith(ep, "j1");
});

test("старая встреча: прежние подсказки — только чтение, под «Подсказки (старый ассистент)»; вопросов meet ask нет", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    legacy: {
      hints: [{ id: "h1", kind: "risk", text: "Нет владельца интеграции", why: "этап 2 без ответственного", source_t: 125,
        ref: null, pinned: false, dismissed: false, created_at: 1, updated_at: 1 }],
      qa: [{ q: "Какой срок?", a: "**Пятница**", at: 1, provider: "claude-code" }],
    },
  }));
  render(<Tab />);
  const old = await screen.findByRole("region", { name: "Подсказки (старый ассистент)" });
  expect(within(old).getByRole("heading", { name: "Подсказки (старый ассистент)" })).toBeInTheDocument();
  expect(within(old).getByText("Нет владельца интеграции")).toBeInTheDocument();
  expect(within(old).getByText("Риск или неясность")).toBeInTheDocument();
  expect(within(old).getByText("02:05")).toBeInTheDocument();
  expect(within(old).getByText("Подсказки старого режима ассистента — только для чтения.")).toBeInTheDocument();
  // Вопросы `meet ask` — на вкладке «Агент» (ревью I4).
  expect(screen.queryByText("Какой срок?")).toBeNull();
  expect(within(old).queryByRole("button")).toBeNull();
  // Чата нет — можно спросить ассистента.
  expect(screen.getByRole("button", { name: "Спросить ассистента о встрече" })).toBeEnabled();
});

test("над журналом — когда кончилась встреча и что переписка сохранена; без времени конца — только второе", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  const view = render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  expect(screen.getByText("Переписка сохранена в папке встречи")).toBeInTheDocument();
  view.rerender(<AssistantTab endpoint={ep} id={ID} folder={FOLDER} jobs={[]} endedAt={new Date(2026, 9, 7, 11, 38)} />);
  expect(await screen.findByText("Встреча закончилась в 11:38 · переписка сохранена в папке встречи")).toBeInTheDocument();
});

test("чата нет — и строки о сохранённой переписке нет", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer());
  render(<AssistantTab endpoint={ep} id={ID} folder={FOLDER} jobs={[]} endedAt={new Date(2026, 9, 7, 11, 38)} />);
  await screen.findByRole("button", { name: "Спросить ассистента о встрече" });
  expect(screen.queryByText(/переписка сохранена/i)).toBeNull();
});

test("чата нет: «Спросить ассистента о встрече» открывает строку ввода с фокусом", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer());
  vi.mocked(continueChat).mockResolvedValue({ message: userMsg("m1", { client_id: "c1" }), job: chatJob("queued") });
  render(<Tab />);
  await userEvent.click(await screen.findByRole("button", { name: "Спросить ассистента о встрече" }));
  expect(field()).toHaveFocus();
  expect(screen.getByRole("group", { name: "Быстрые вопросы" })).toHaveTextContent("Кратко итоги");
  await userEvent.click(screen.getByRole("button", { name: "Кратко итоги" }));
  expect(continueChat).toHaveBeenCalledWith(ep, ID, { text: "Кратко итоги", client_id: "c1", attachments: [] });
});

test("профиль сессии виден; в «Личном» — свои быстрые вопросы, без рабочих", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2, profile: "personal" }));
  const view = render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  expect(screen.getByText("Профиль:").parentElement).toHaveTextContent("Профиль: Личный");
  const quick = screen.getByRole("group", { name: "Быстрые вопросы" });
  for (const q of PERSONAL_QUESTIONS) expect(quick).toHaveTextContent(q);
  expect(quick).not.toHaveTextContent("Что мне сделать?");
  expect(quick).not.toHaveTextContent("Какие решения приняли?");
  view.unmount();

  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2, profile: "work" }));
  const work = render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  expect(screen.getByText("Профиль:").parentElement).toHaveTextContent("Профиль: Рабочая встреча");
  expect(screen.getByRole("group", { name: "Быстрые вопросы" })).toHaveTextContent("Что мне сделать?");
  work.unmount();

  // Старый резидент или встреча до 0.3.7 — профиля нет, строки нет.
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  expect(screen.queryByText("Профиль:")).toBeNull();
});

test("агент-участник выключен в настройках — как у резидента: писать нельзя, видно почему", async () => {
  const settings = vi.fn();
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ enabled: false }));
  const view = render(<Tab onOpenSettings={settings} />);
  expect(await screen.findByText("Чат с ассистентом выключен в настройках")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Спросить ассистента о встрече" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки" }));
  expect(settings).toHaveBeenCalledWith("assistant");
  // С журналом: лента видна, строка ввода недоступна.
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ enabled: false, messages: journal, seq: 2 }));
  view.unmount();
  render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  expect(field()).toBeDisabled();
  expect(screen.getByText("Чат с ассистентом выключен в настройках")).toBeInTheDocument();
});

test("вложения после встречи: Ctrl+V, перетаскивание и «📎» — резиденту; «×» убирает", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  vi.mocked(recordingChatPaste).mockResolvedValue({ id: "a1", status: "ready", attachment: attMsg("a1") } as never);
  vi.mocked(recordingChatAttach).mockResolvedValue({
    id: "a2", status: "ready", attachment: attMsg("a2", { type: "doc", name: "План.pptx" }),
  } as never);
  vi.mocked(continueChat).mockResolvedValue({ message: userMsg("m3", { client_id: "c1" }), job: null });
  render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  const png = new File([new Uint8Array([137, 80])], "image.png", { type: "image/png" });
  await act(async () => { fireEvent.paste(field(), { clipboardData: { files: [png], getData: () => "" } }); });
  expect(recordingChatPaste).toHaveBeenCalledWith(ep, ID, png, "Скриншот.png");
  await vi.waitFor(() => expect(h.drop).not.toBeNull());
  await act(async () => { h.drop!({ type: "drop", x: 5, y: 5, paths: ["C:\\docs\\План.pptx"] }); });
  expect(recordingChatAttach).toHaveBeenCalledWith(ep, ID, "C:\\docs\\План.pptx");
  h.pick.mockResolvedValueOnce(["C:\\docs\\Отчёт.pdf"]);
  vi.mocked(recordingChatAttach).mockResolvedValueOnce({
    id: "a3", status: "ready", attachment: attMsg("a3", { type: "doc", name: "Отчёт.pdf" }),
  } as never);
  await userEvent.click(screen.getByRole("button", { name: "Приложить файл" }));
  expect(recordingChatAttach).toHaveBeenLastCalledWith(ep, ID, "C:\\docs\\Отчёт.pdf");
  await userEvent.click(await screen.findByRole("button", { name: "Убрать вложение Отчёт.pdf" }));
  expect(recordingChatRemove).toHaveBeenCalledWith(ep, ID, "a3");
  await userEvent.type(field(), "посмотри{Enter}");
  expect(continueChat).toHaveBeenCalledWith(ep, ID, { text: "посмотри", client_id: "c1", attachments: ["a1", "a2"] });
});

test("чип-источник: агент упомянул приложенный файл — щелчок открывает исходник, иначе копию во встрече", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    seq: 3,
    messages: [
      attMsg("a1", { type: "doc", name: "План запуска.pptx", source: "C:\\docs\\План запуска.pptx",
        path: `${FOLDER}\\assistant\\materials\\a1.txt` }),
      userMsg("m1", { text: "глянь", attachments: ["a1"], t: undefined }),
      agentMsg("m2", { text: "В «План запуска.pptx» срок — 15.11.", t: undefined }),
    ],
  }));
  h.open.mockRejectedValueOnce(new Error("этот файл приложение не открывает"));
  render(<Tab />);
  const chip = await screen.findByRole("button", { name: "Источник: План запуска.pptx" });
  await userEvent.click(chip);
  expect(h.open).toHaveBeenNthCalledWith(1, "C:\\docs\\План запуска.pptx");
  expect(h.open).toHaveBeenNthCalledWith(2, `${FOLDER}\\assistant\\materials\\a1.txt`);
  // Открыта копия текста — об этом сказано (ревью M5).
  expect(await screen.findByText(COPY_OPENED)).toBeInTheDocument();
});

test("чип-источник: документ базы знаний по пути — открывается от корня базы", async () => {
  vi.mocked(getKbDocs).mockResolvedValue({ root: "D:\\KB", docs: ["Проекты/Альфа/Биллинг.md", "Личное.md"], more: false });
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    seq: 1, messages: [agentMsg("m1", { text: "Таймаут шлюза описан в Проекты/Альфа/Биллинг.md.", t: undefined })],
  }));
  render(<Tab />);
  await userEvent.click(await screen.findByRole("button", { name: "Источник: Биллинг.md" }));
  expect(h.open).toHaveBeenCalledWith("D:\\KB\\Проекты\\Альфа\\Биллинг.md");
  expect(getKbDocs).toHaveBeenCalledTimes(1);
});

test("«Личный»: документ базы знаний в тексте — без чипа-источника (ревью M4)", async () => {
  vi.mocked(getKbDocs).mockResolvedValue({ root: "D:\\KB", docs: ["Проекты/Альфа/Биллинг.md"], more: false });
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    profile: "personal",
    seq: 1, messages: [agentMsg("m1", { text: "Раньше упоминался Проекты/Альфа/Биллинг.md.", t: undefined })],
  }));
  render(<Tab />);
  expect(await within(await ready()).findByText(/Биллинг\.md/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Источник: Биллинг.md" })).toBeNull();
  expect(getKbDocs).not.toHaveBeenCalled();
});

test("пока встреча расшифровывается — лента видна, писать нельзя, видно почему (ревью I3)", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  const transcribing = { ...chatJob("running"), id: "t1", kind: "transcribe" } as Job;
  const view = render(<Tab jobs={[transcribing]} />);
  await within(await ready()).findByText("15 ноября");
  expect(field()).toBeDisabled();
  expect(screen.getByText(TRANSCRIBING)).toBeInTheDocument();
  // Расшифровка другой записи — не мешает.
  view.rerender(<Tab jobs={[{ ...transcribing, folder: "C:\\rec\\другая" }]} />);
  expect(field()).toBeEnabled();
});

test("«Стоп» и пока ответ в очереди: снимает задачу (ревью M8)", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal.slice(0, 1), seq: 1 }));
  render(<Tab jobs={[chatJob("queued")]} />);
  expect(await screen.findByText("Ассистент думает…")).toBeInTheDocument();
  const stop = screen.getByRole("button", { name: "Остановить ответ" });
  // Atlas Aurora: та же кнопка, что у строки ввода чата, — контур, значок и слово «Стоп».
  expect(stop).toHaveClass("btn", "btn--outline");
  expect(stop).toHaveTextContent(/^Стоп$/);
  await userEvent.click(stop);
  expect(cancelJob).toHaveBeenCalledWith(ep, "j1");
});

test("❓ после встречи — просьба пояснить: реакция уходит резиденту, просьба видна в ленте (ревью I2)", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  const view = render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  await userEvent.click(within(log()).getByRole("button", { name: "Поясни" }));
  expect(recordingChatReact).toHaveBeenCalledWith(ep, ID, "m2", "❓", true);
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    seq: 4, messages: [...journal, userMsg("m3", { text: "❓ Поясни это сообщение", via: "reaction", re: "m2", t: undefined })],
  }));
  view.rerender(<Tab ev={event()} jobs={[chatJob("queued")]} />);
  const asked = (await within(log()).findByText("❓ Поясни это сообщение")).closest("li")!;
  expect(asked).toHaveTextContent("реакция");
  expect(screen.getByText("Ассистент думает…")).toBeInTheDocument();
});

test("после встречи: отклик на 👎, ❓ «поясняет…» до ответа, пояснение ссылается на сообщение", async () => {
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: journal, seq: 2 }));
  const view = render(<Tab />);
  await within(await ready()).findByText("15 ноября");
  const m2 = () => within(log()).getByText("15 ноября").closest("li")!;
  await userEvent.click(within(m2()).getByRole("button", { name: "Не по теме" }));
  expect(within(m2()).getByText("Учту: скорректирую, о чём пишу")).toBeInTheDocument();
  await userEvent.click(within(m2()).getByRole("button", { name: "Не по теме" }));   // сняли — отклик ушёл
  expect(within(m2()).queryByText(/Учту/)).toBeNull();

  const now = Date.now() / 1000;
  await userEvent.click(within(m2()).getByRole("button", { name: "Поясни" }));
  expect(within(m2()).getByText("Ассистент поясняет…")).toBeInTheDocument();
  const asked = { ...journal[1]!, reactions: { "❓": now } };
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    seq: 4, messages: [journal[0]!, asked, userMsg("m3", { text: "❓ Поясни это сообщение", via: "reaction", re: "m2", t: undefined, at: now + 1 })],
  }));
  view.rerender(<Tab ev={event()} jobs={[chatJob("running")]} />);
  await within(log()).findByText("❓ Поясни это сообщение");
  expect(within(m2()).getByText("Ассистент поясняет…")).toBeInTheDocument();
  vi.mocked(getRecordingChat).mockResolvedValue(answer({
    seq: 6, messages: [journal[0]!, asked, userMsg("m3", { text: "❓ Поясни это сообщение", via: "reaction", re: "m2", t: undefined, at: now + 1 }),
      agentMsg("m4", { text: "Срок из плана запуска, слайд 7.", mode: "reply", re: "m3", explains: "m2", t: undefined, at: now + 5 })],
  }));
  view.rerender(<Tab ev={event({ ts: now + 6 })} jobs={[]} />);
  const reply = (await within(log()).findByText("Срок из плана запуска, слайд 7.")).closest("li")!;
  expect(within(reply).getByText("пояснение")).toBeInTheDocument();
  expect(within(reply).getByRole("button", { name: "к сообщению «Запуск — 15 ноября.»" })).toBeInTheDocument();
  expect(within(m2()).queryByText("Ассистент поясняет…")).toBeNull();
});

test("карточка подтверждения Meet после встречи: кнопки — в самой карточке ленты, решение уходит в журнал записи", async () => {
  const card: ChatMessage = {
    ...agentMsg("m3"), kind: "system", status: undefined, mode: undefined, t: undefined, card: "confirm", tool: "Bash",
    title: "команду", text: "Ассистент хочет выполнить: команду", args: "ls", expires_at: Date.now() / 1000 + 100,
  };
  vi.mocked(getRecordingChat).mockResolvedValue(answer({ messages: [...journal, card], seq: 3 }));
  render(<Tab />);
  // Отдельной области над лентой нет: в длинной переписке после встречи её не было видно — ход висел.
  const box = await within(await screen.findByRole("log", { name: "Чат с ассистентом" }))
    .findByRole("group", { name: "Ассистент хочет выполнить: команду" });
  expect(screen.queryByRole("region", { name: "Подтверждение действия" })).toBeNull();
  expect(box).not.toHaveTextContent("над лентой");
  expect(within(box).getByRole("button", { name: "Разрешить один раз" })).toBeEnabled();
  await userEvent.click(within(box).getByRole("button", { name: "Отклонить" }));
  expect(recordingChatConfirm).toHaveBeenCalledWith(ep, ID, "m3", false, false);
});
