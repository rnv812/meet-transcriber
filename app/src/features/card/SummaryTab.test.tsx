import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SummaryTab } from "./SummaryTab";
import * as api from "../../lib/api";
import type { AssistantInfo, Job } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSummary: vi.fn(),
  getLiveDraft: vi.fn(),
  makeSummary: vi.fn(),
}));

const ep = { base: "/api", token: null };
const FOLDER = "C:\\rec\\r1";
const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null,
  checking: false, ...o,
});
const job = (o: Partial<Job> = {}): Job => ({
  id: "s1", kind: "summary", folder: "C:/rec/r1", state: "running", stage: null, label: null,
  done: null, total: null, note: null, result: null, error: null, ...o,
});
const MD = "# Итоги\n\n## Решения\n\n- выпускаем **в пятницу**\n\n| Кто | Что | Срок |\n|---|---|---|\n| Демьян | отчёт | пт |";

const noSummary = () => vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
const hasSummary = (markdown = MD) =>
  vi.mocked(api.getSummary).mockResolvedValue({ markdown, created_at: 1000 });

function show(props: Partial<Parameters<typeof SummaryTab>[0]> = {}) {
  const all = { endpoint: ep, id: "r1", folder: FOLDER, jobs: [] as Job[], assistant: assistant(), ...props };
  const view = render(<SummaryTab {...all} />);
  return { ...view, update: (more: Partial<typeof all>) => view.rerender(<SummaryTab {...all} {...more} />) };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
});

test("итогов нет: пустое состояние и «Сделать итоги» → makeSummary, затем «Модель думает…»", async () => {
  noSummary();
  vi.mocked(api.makeSummary).mockResolvedValue(job({ state: "queued" }));
  show();
  expect(await screen.findByText("Итогов пока нет")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сделать итоги" }));
  expect(api.makeSummary).toHaveBeenCalledWith(ep, "r1");
  expect(await screen.findByText("Модель думает…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сделать итоги" })).toBeNull();
});

test("без провайдера «Сделать итоги» неактивна, подсказка и ссылка в настройки", async () => {
  noSummary();
  const onOpenSettings = vi.fn();
  show({ assistant: assistant({ provider: null }), onOpenSettings });
  const button = await screen.findByRole("button", { name: "Сделать итоги" });
  expect(button).toBeDisabled();
  expect(screen.getByText("Подключите Claude Code или Codex в настройках")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки" }));
  expect(onOpenSettings).toHaveBeenCalledWith("assistant");
});

test("провайдер ещё проверяется: кнопка доступна, подсказки нет", async () => {
  noSummary();
  show({ assistant: assistant({ provider: null, checking: true }) });
  expect(await screen.findByRole("button", { name: "Сделать итоги" })).toBeEnabled();
  expect(screen.queryByText("Подключите Claude Code или Codex в настройках")).toBeNull();
});

test("409 от резидента показывается текстом", async () => {
  noSummary();
  vi.mocked(api.makeSummary).mockRejectedValue(new api.ApiError(409, "Дождитесь окончания расшифровки"));
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Сделать итоги" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Дождитесь окончания расшифровки");
});

test("итоги есть: заголовки из Markdown; выгрузка — на уровне карточки, не здесь", async () => {
  hasSummary();
  show();
  expect(await screen.findByRole("heading", { name: "Решения" })).toBeInTheDocument();
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("в пятницу").tagName).toBe("STRONG");
  expect(screen.getByRole("button", { name: "Переделать" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "В заметки" })).toBeNull();
});

test("«Копировать» кладёт сырой Markdown и пишет «Скопировано»", async () => {
  hasSummary();
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Копировать" }));
  expect(await navigator.clipboard.readText()).toBe(MD);
  expect(screen.getByRole("button", { name: "Скопировано" })).toBeInTheDocument();
});

test("«Переделать» зовёт makeSummary", async () => {
  hasSummary();
  vi.mocked(api.makeSummary).mockResolvedValue(job({ state: "queued" }));
  show();
  await userEvent.click(await screen.findByRole("button", { name: "Переделать" }));
  expect(api.makeSummary).toHaveBeenCalledWith(ep, "r1");
  expect(await screen.findByText("Модель думает…")).toBeInTheDocument();
});

test("задача идёт → «Модель думает…»; job.done → итоги перечитаны", async () => {
  noSummary();
  const { update } = show({ jobs: [job({ state: "running" })] });
  expect(await screen.findByText("Модель думает…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сделать итоги" })).toBeNull();
  // Чужая папка и чужой вид задачи не в счёт.
  hasSummary("# Новые итоги");
  update({ jobs: [job({ state: "done" }), job({ id: "x", folder: "C:/rec/r2" }), job({ id: "a", kind: "ask" })] });
  expect(await screen.findByRole("heading", { name: "Новые итоги" })).toBeInTheDocument();
  expect(screen.queryByText("Модель думает…")).toBeNull();
  expect(api.getSummary).toHaveBeenCalledTimes(2);
});

test("упавшая задача: текст ошибки и «Повторить»", async () => {
  noSummary();
  vi.mocked(api.makeSummary).mockResolvedValue(job({ id: "s2", state: "queued" }));
  show({ jobs: [job({ state: "failed", error: "claude: лимит исчерпан" })] });
  expect(await screen.findByText("claude: лимит исчерпан")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(api.makeSummary).toHaveBeenCalledWith(ep, "r1");
  expect(await screen.findByText("Модель думает…")).toBeInTheDocument();
  expect(screen.queryByText("claude: лимит исчерпан")).toBeNull();
});

const deferred = <T,>() => {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
};

test("поставленная задача пропала из списка (перезапуск резидента) — ожидание снято, итоги перечитаны", async () => {
  noSummary();
  vi.mocked(api.makeSummary).mockResolvedValue(job({ id: "s2", state: "queued" }));
  const { update } = show();
  await userEvent.click(await screen.findByRole("button", { name: "Сделать итоги" }));
  update({ jobs: [job({ id: "s2", state: "running" })] });
  expect(await screen.findByText("Модель думает…")).toBeInTheDocument();
  const loads = vi.mocked(api.getSummary).mock.calls.length;
  update({ jobs: [] });
  await waitFor(() => expect(screen.queryByText("Модель думает…")).toBeNull());
  expect(vi.mocked(api.getSummary).mock.calls.length).toBeGreaterThan(loads);
  expect(await screen.findByRole("button", { name: "Сделать итоги" })).toBeEnabled();
});

test("поставленная задача так и не появилась за два обновления списка — ожидание снято", async () => {
  noSummary();
  vi.mocked(api.makeSummary).mockResolvedValue(job({ id: "s2", state: "queued" }));
  const { update } = show();
  await userEvent.click(await screen.findByRole("button", { name: "Сделать итоги" }));
  expect(await screen.findByText("Модель думает…")).toBeInTheDocument();
  const loads = vi.mocked(api.getSummary).mock.calls.length;
  update({ jobs: [job({ id: "other", folder: "C:/rec/r2" })] });
  expect(screen.getByText("Модель думает…")).toBeInTheDocument();
  update({ jobs: [job({ id: "other", folder: "C:/rec/r2", state: "done" })] });
  await waitFor(() => expect(screen.queryByText("Модель думает…")).toBeNull());
  expect(vi.mocked(api.getSummary).mock.calls.length).toBeGreaterThan(loads);
});

test("поставленная задача не появилась за 10 с — ожидание снято", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    noSummary();
    vi.mocked(api.makeSummary).mockResolvedValue(job({ id: "s2", state: "queued" }));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    show();
    await user.click(await screen.findByRole("button", { name: "Сделать итоги" }));
    expect(await screen.findByText("Модель думает…")).toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(10_000); });
    await waitFor(() => expect(screen.queryByText("Модель думает…")).toBeNull());
  } finally {
    vi.useRealTimers();
  }
});

test("устаревший ответ getSummary не перекрывает свежий", async () => {
  const first = deferred<{ markdown: string; created_at: number }>();
  vi.mocked(api.getSummary).mockReturnValueOnce(first.promise)
    .mockResolvedValue({ markdown: "# Свежие", created_at: 2000 });
  const { update } = show();
  update({ jobs: [job({ state: "done" })] });
  expect(await screen.findByRole("heading", { name: "Свежие" })).toBeInTheDocument();
  await act(async () => { first.resolve({ markdown: "# Старые", created_at: 1000 }); });
  expect(screen.getByRole("heading", { name: "Свежие" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Старые" })).toBeNull();
});

test("ответ для прежней записи не попадает в новую", async () => {
  const first = deferred<{ markdown: string; created_at: number }>();
  vi.mocked(api.getSummary).mockImplementation((_ep, rid) =>
    rid === "r1" ? first.promise : Promise.resolve({ markdown: "# Запись два", created_at: 1 }));
  const { update } = show();
  update({ id: "r2", folder: "C:/rec/r2" });
  expect(await screen.findByRole("heading", { name: "Запись два" })).toBeInTheDocument();
  await act(async () => { first.resolve({ markdown: "# Запись один", created_at: 1 }); });
  expect(screen.queryByRole("heading", { name: "Запись один" })).toBeNull();
});

test("ошибка загрузки уходит после успешной перезагрузки", async () => {
  vi.mocked(api.getSummary).mockRejectedValueOnce(new api.ApiError(500, "сломалось"));
  const { update } = show();
  expect(await screen.findByRole("alert")).toHaveTextContent("сломалось");
  hasSummary();
  update({ jobs: [job({ state: "done" })] });
  expect(await screen.findByRole("heading", { name: "Решения" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).toBeNull();
});


test("итогов нет, запись шла с ассистентом — «Черновик из живого режима» и «Сделать итоги»", async () => {
  noSummary();
  vi.mocked(api.getLiveDraft).mockResolvedValue({
    summary: { topic: "Планёрка", points: [], decisions: [{ id: "d1", text: "выпуск в пятницу" }], tasks: [], open_questions: [] },
    hints: [], markdown: "**Тема:** Планёрка\n\n### Решения\n- выпуск в пятницу",
  });
  vi.mocked(api.makeSummary).mockResolvedValue(job({ state: "queued" }));
  show();
  const draft = await screen.findByRole("region", { name: "Черновик из живого режима" });
  expect(draft).toHaveTextContent("выпуск в пятницу");
  expect(draft).toHaveTextContent("Итоги модель сверит с полной расшифровкой");
  expect(api.getLiveDraft).toHaveBeenCalledWith(ep, "r1");
  await userEvent.click(screen.getByRole("button", { name: "Сделать итоги" }));
  expect(api.makeSummary).toHaveBeenCalledWith(ep, "r1");
});

test("итоги есть — черновик не спрашиваем и не показываем", async () => {
  hasSummary();
  show();
  expect(await screen.findByText("в пятницу")).toBeInTheDocument();
  expect(api.getLiveDraft).not.toHaveBeenCalled();
  expect(screen.queryByText("Черновик из живого режима")).toBeNull();
});

test("✦ у пунктов итогов: спросить агента о пункте (с разделом)", async () => {
  hasSummary();
  const onAskAgent = vi.fn();
  show({ onAskAgent });
  const ask = await screen.findByRole("button", { name: "Спросить агента об этом пункте: выпускаем в пятницу" });
  await userEvent.click(ask);
  expect(onAskAgent).toHaveBeenCalledWith({ kind: "summary", refs: [{ text: "выпускаем в пятницу", section: "Решения" }] });
  await userEvent.click(screen.getByRole("button", { name: /Спросить агента об этом пункте: Кто: Демьян/ }));
  expect(onAskAgent).toHaveBeenLastCalledWith({
    kind: "summary", refs: [{ text: "Кто: Демьян; Что: отчёт; Срок: пт", section: "Решения" }],
  });
});

test("без onAskAgent кнопок ✦ нет", async () => {
  hasSummary();
  show();
  await screen.findByText("Копировать");
  expect(screen.queryByRole("button", { name: /Спросить агента/ })).toBeNull();
});

test("✦ и у черновика из живого режима", async () => {
  noSummary();
  vi.mocked(api.getLiveDraft).mockResolvedValue({ markdown: "## Главное\n\n- бюджет на квартал", summary: {}, hints: [], saved_at: 1 } as never);
  const onAskAgent = vi.fn();
  show({ onAskAgent });
  await userEvent.click(await screen.findByRole("button", { name: "Спросить агента об этом пункте: бюджет на квартал" }));
  expect(onAskAgent).toHaveBeenCalledWith({ kind: "summary", refs: [{ text: "бюджет на квартал", section: "Главное" }] });
});
