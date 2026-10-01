import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SummaryTab } from "./SummaryTab";
import * as api from "../../lib/api";
import type { AssistantInfo, Job } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSummary: vi.fn(),
  makeSummary: vi.fn(),
  toNotes: vi.fn(),
}));

const ep = { base: "/api", token: null };
const FOLDER = "C:\\rec\\r1";
const assistant = (o: Partial<AssistantInfo> = {}): AssistantInfo => ({
  provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null,
  notes_dir: "D:/Notes", checking: false, ...o,
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

test("итоги есть: заголовки из Markdown, «В заметки» → путь", async () => {
  hasSummary();
  vi.mocked(api.toNotes).mockResolvedValue({ path: "D:/Notes/Встречи/2026-09-30 Встреча.md" });
  show();
  expect(await screen.findByRole("heading", { name: "Решения" })).toBeInTheDocument();
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByText("в пятницу").tagName).toBe("STRONG");
  await userEvent.click(screen.getByRole("button", { name: "В заметки" }));
  expect(api.toNotes).toHaveBeenCalledWith(ep, "r1");
  expect(await screen.findByText("D:/Notes/Встречи/2026-09-30 Встреча.md")).toBeInTheDocument();
});

test("«В заметки» скрыта, если папка заметок не задана", async () => {
  hasSummary();
  show({ assistant: assistant({ notes_dir: null }) });
  await screen.findByRole("heading", { name: "Решения" });
  expect(screen.queryByRole("button", { name: "В заметки" })).toBeNull();
  expect(screen.getByRole("button", { name: "Переделать" })).toBeInTheDocument();
});

test("ошибка «В заметки» — текстом", async () => {
  hasSummary();
  vi.mocked(api.toNotes).mockRejectedValue(new api.ApiError(400, "Папка заметок не задана"));
  show();
  await userEvent.click(await screen.findByRole("button", { name: "В заметки" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Папка заметок не задана");
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
