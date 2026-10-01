import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QaTab } from "./QaTab";
import * as api from "../../lib/api";
import type { AssistantInfo, Job, QaItem } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getQa: vi.fn(),
  ask: vi.fn(),
}));

const ep = { base: "/api", token: null };
const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null,
  checking: false, ...o,
});
const job = (o: Partial<Job> = {}): Job => ({
  id: "a1", kind: "ask", folder: "C:/rec/r1", state: "queued", stage: null, label: null,
  done: null, total: null, note: null, result: null, error: null, ...o,
});
const item = (q: string, a: string): QaItem => ({ q, a, at: 1_790_000_000, provider: "claude-code" });

function show(props: Partial<Parameters<typeof QaTab>[0]> = {}) {
  const all = { endpoint: ep, id: "r1", folder: "C:/rec/r1", jobs: [] as Job[], assistant: assistant(), ...props };
  const view = render(<QaTab {...all} />);
  return { ...view, update: (more: Partial<typeof all>) => view.rerender(<QaTab {...all} {...more} />) };
}
const box = () => screen.getByRole("textbox", { name: "Вопрос по встрече" });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
});

test("лента прошлых вопросов и ответов, ответ — Markdown", async () => {
  vi.mocked(api.getQa).mockResolvedValue({ items: [item("Кто делает отчёт?", "**Демьян**, к пятнице")] });
  show();
  expect(await screen.findByText("Кто делает отчёт?")).toBeInTheDocument();
  expect(screen.getByText("Демьян").tagName).toBe("STRONG");
});

test("пусто — подсказка", async () => {
  show();
  expect(await screen.findByText("Вопросов пока не было")).toBeInTheDocument();
});

test("Enter → ask; вопрос сразу виден с «Модель думает…», поле заблокировано; job.done → ответ", async () => {
  vi.mocked(api.ask).mockResolvedValue(job());
  const { update } = show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "что решили?{Enter}");
  expect(api.ask).toHaveBeenCalledWith(ep, "r1", "что решили?");
  expect(screen.getByText("что решили?")).toBeInTheDocument();
  expect(screen.getByText("Модель думает…")).toBeInTheDocument();
  await waitFor(() => expect(box()).toBeDisabled());
  await waitFor(() => expect(box()).toHaveValue(""));

  update({ jobs: [job({ state: "running" })] });
  expect(box()).toBeDisabled();

  vi.mocked(api.getQa).mockResolvedValue({ items: [item("что решили?", "Выпускаем в пятницу")] });
  update({ jobs: [job({ state: "done" })] });
  expect(await screen.findByText("Выпускаем в пятницу")).toBeInTheDocument();
  expect(screen.queryByText("Модель думает…")).toBeNull();
  expect(screen.getAllByText("что решили?")).toHaveLength(1);
  expect(box()).toBeEnabled();
});

test("Shift+Enter — перенос строки, не отправка; пустой вопрос не уходит", async () => {
  show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "   {Enter}");
  expect(api.ask).not.toHaveBeenCalled();
  await userEvent.clear(box());
  await userEvent.type(box(), "первая{Shift>}{Enter}{/Shift}вторая");
  expect(api.ask).not.toHaveBeenCalled();
  expect(box()).toHaveValue("первая\nвторая");
});

test("упавшая задача — ошибка под вопросом", async () => {
  vi.mocked(api.ask).mockResolvedValue(job());
  const { update } = show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "что решили?{Enter}");
  await waitFor(() => expect(box()).toBeDisabled());
  update({ jobs: [job({ state: "failed", error: "codex: нет входа" })] });
  const pending = screen.getByText("что решили?", { selector: ".qa__q" }).closest(".qa__item") as HTMLElement;
  expect(await within(pending).findByText("codex: нет входа")).toBeInTheDocument();
  expect(screen.queryByText("Модель думает…")).toBeNull();
  expect(box()).toBeEnabled();
});

test("отказ резидента (409) — ошибка под вопросом", async () => {
  vi.mocked(api.ask).mockRejectedValue(new api.ApiError(409, "Подключите Claude Code или Codex в настройках"));
  show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "что решили?{Enter}");
  const pending = screen.getByText("что решили?", { selector: ".qa__q" }).closest(".qa__item") as HTMLElement;
  expect(await within(pending).findByText("Подключите Claude Code или Codex в настройках")).toBeInTheDocument();
  expect(box()).toBeEnabled();
  // Текст вопроса остался в поле — можно отправить ещё раз.
  expect(box()).toHaveValue("что решили?");
});

test("идёт чужая (из прошлого показа) задача-вопрос этой записи — поле заблокировано", async () => {
  show({ jobs: [job({ state: "running" })] });
  await screen.findByText("Вопросов пока не было");
  expect(box()).toBeDisabled();
});

test("без провайдера — поле заблокировано, подсказка", async () => {
  show({ assistant: assistant({ provider: null }) });
  await screen.findByText("Вопросов пока не было");
  expect(box()).toBeDisabled();
  expect(screen.getByText("Подключите Claude Code или Codex в настройках")).toBeInTheDocument();
});

const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
};

test("пока ask не ответил, вопрос остаётся в поле", async () => {
  const sent = deferred<Job>();
  vi.mocked(api.ask).mockReturnValue(sent.promise);
  show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "что решили?{Enter}");
  expect(box()).toHaveValue("что решили?");
  await act(async () => { sent.resolve(job()); });
  expect(box()).toHaveValue("");
});

test("задача пропала после появления, ответ записан — вопрос встаёт в ленту", async () => {
  vi.mocked(api.ask).mockResolvedValue(job());
  const { update } = show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "что решили?{Enter}");
  update({ jobs: [job({ state: "running" })] });
  vi.mocked(api.getQa).mockResolvedValue({ items: [item("что решили?", "Выпускаем в пятницу")] });
  update({ jobs: [] });
  expect(await screen.findByText("Выпускаем в пятницу")).toBeInTheDocument();
  expect(screen.queryByText("Модель думает…")).toBeNull();
  expect(screen.getAllByText("что решили?")).toHaveLength(1);
  expect(box()).toBeEnabled();
});

test("задача пропала, ответа нет — ошибка под вопросом, поле снова доступно", async () => {
  vi.mocked(api.ask).mockResolvedValue(job());
  const { update } = show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "что решили?{Enter}");
  update({ jobs: [job({ state: "running" })] });
  update({ jobs: [] });
  const pending = screen.getByText("что решили?", { selector: ".qa__q" }).closest(".qa__item") as HTMLElement;
  expect(await within(pending).findByText(/Ответ не получен/)).toBeInTheDocument();
  expect(screen.queryByText("Модель думает…")).toBeNull();
  expect(box()).toBeEnabled();
});

test("задача так и не появилась за два обновления списка — ожидание снято", async () => {
  vi.mocked(api.ask).mockResolvedValue(job());
  const { update } = show();
  await screen.findByText("Вопросов пока не было");
  await userEvent.type(box(), "что решили?{Enter}");
  await waitFor(() => expect(box()).toHaveValue(""));
  const loads = vi.mocked(api.getQa).mock.calls.length;
  update({ jobs: [job({ id: "x", folder: "C:/rec/r2" })] });
  expect(screen.getByText("Модель думает…")).toBeInTheDocument();
  update({ jobs: [job({ id: "x", folder: "C:/rec/r2", state: "done" })] });
  await waitFor(() => expect(screen.queryByText("Модель думает…")).toBeNull());
  expect(vi.mocked(api.getQa).mock.calls.length).toBeGreaterThan(loads);
  expect(box()).toBeEnabled();
});

test("устаревший ответ getQa не перекрывает свежий", async () => {
  const first = deferred<{ items: QaItem[] }>();
  vi.mocked(api.getQa).mockReturnValueOnce(first.promise)
    .mockResolvedValue({ items: [item("свежий?", "да")] });
  const { update } = show();
  update({ jobs: [job({ id: "z", state: "done" })] });
  expect(await screen.findByText("свежий?")).toBeInTheDocument();
  await act(async () => { first.resolve({ items: [item("старый?", "нет")] }); });
  expect(screen.getByText("свежий?")).toBeInTheDocument();
  expect(screen.queryByText("старый?")).toBeNull();
});
