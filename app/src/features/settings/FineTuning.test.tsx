import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getOwnerVoice: vi.fn(),
  getAssistant: vi.fn(),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getHotwords: vi.fn(),
  getEngine: vi.fn(),
  getModels: vi.fn(),
  getJobs: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getHfStatus: vi.fn(),
  listLocalModels: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  auto_record: { enabled: true, processes: [], browsers: [], call_sites: ["Google Meet"], browser_require_site: false },
  asr: { backend: "faster-whisper", model: "large-v3", cpu_model: "small", device: "auto", language: "ru", align: true },
  llm: { provider: "claude-code", enabled: ["claude-code", "openai-compatible"], base_url: "http://127.0.0.1:1234/v1" },
  assist: { participant: true, window_seconds: 30 },
  assistant: { knowledge_dir: null },
  integrations: { gpu_marker: true, gpu_marker_path: null, jira_pattern: "" },
  transcript_view: { jira: true },
  hooks: { post_record: false, command: [] },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...structuredClone(settings), ...(u as object) } as Record<string, unknown>, restart_required: [],
  }));
  vi.mocked(api.getHotwords).mockResolvedValue({ text: "", budget: 400, used: 0 });
  vi.mocked(api.getEngine).mockRejectedValue(new Error("x"));
  vi.mocked(api.getModels).mockRejectedValue(new Error("x"));
  vi.mocked(api.getJobs).mockResolvedValue({ items: [] });
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
  vi.mocked(api.getHfStatus).mockResolvedValue({ configured: false, source: null, check: null });
  vi.mocked(api.getOwnerVoice).mockResolvedValue({
    samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25,
  });
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "claude-code", checking: false, knowledge_dir: null,
    available: { "claude-code": { found: true, path: "claude" } },
  });
  vi.mocked(api.listLocalModels).mockResolvedValue({ ok: true, models: [], error: null } as never);
});

const FINE = "Тонкая настройка";

/** Раздел → что лежит в его «Тонкой настройке» (из таблицы спека). */
const CASES: [string, (s: typeof screen) => HTMLElement[]][] = [
  ["asr", (s) => [s.getByRole("switch", { name: "Уточнять время каждого слова" })]],
  ["auto", (s) => [
    s.getByRole("checkbox", { name: /Только если в заголовке окна сайт звонка/ }),
    s.getByRole("list", { name: "Сайты звонков" }),
  ]],
  ["models", (s) => [s.getByRole("checkbox", { name: "Локальную модель — через прокси" })]],
  ["assistant", (s) => [
    s.getByLabelText("Окно живой расшифровки, с"),
    s.getByRole("switch", { name: "Не отвлекать по умолчанию" }),
  ]],
  ["jira", (s) => [s.getByRole("textbox", { name: "Шаблон ключа для текста" })]],
  ["advanced", (s) => [s.getByRole("textbox", { name: "Путь к файлу-маркеру" })]],
];

test.each(CASES)("«%s»: внизу раздела — свёрнутая «Тонкая настройка» с редкими строками", async (section, rows) => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial={section} />);
  const head = await screen.findByRole("button", { name: FINE });
  expect(head).toHaveAttribute("aria-expanded", "false");
  expect(() => rows(screen)).toThrow();
  // Блок — последний в разделе.
  const cards = document.querySelectorAll(".settings__content > section");
  expect(cards[cards.length - 1]).toContainElement(head);
  await userEvent.click(head);
  expect(head).toHaveAttribute("aria-expanded", "true");
  for (const el of rows(screen)) expect(el).toBeInTheDocument();
});

test("основные строки остаются на виду: «Звонки в браузере», «Язык речи», «Модели», «Маркер видеокарты»", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="auto" />);
  const browsers = await screen.findByRole("group", { name: "Звонки в браузере" });
  expect(within(browsers).getByRole("checkbox", { name: "Google Chrome" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Распознавание" }));
  expect(await screen.findByLabelText("Язык речи")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Модели ИИ" }));
  expect(await screen.findByRole("group", { name: "Модели" })).toBeInTheDocument();
  expect(screen.getByLabelText("Адрес сервера")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Дополнительно" }));
  expect(screen.getByRole("switch", { name: "Сообщать другим программам о занятости видеокарты" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Запуск Claude Code" })).toBeInTheDocument();
});

test("«Ассистент», участник выключен: прежний режим подсказок — в «Тонкой настройке»", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({ ...structuredClone(settings), assist: { participant: false } });
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" />);
  await userEvent.click(await screen.findByRole("button", { name: FINE }));
  expect(screen.getByRole("radiogroup", { name: "Активность подсказок" })).toBeInTheDocument();
  expect(screen.getByRole("radiogroup", { name: "Модель для живых подсказок" })).toBeInTheDocument();
  expect(screen.getByLabelText("Сколько подсказок держать")).toBeInTheDocument();
});

test("«Локальную модель — через прокси» без локальной модели — недоступно, с причиной", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({ ...structuredClone(settings), llm: { provider: "claude-code", enabled: ["claude-code"] } });
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="models" />);
  await userEvent.click(await screen.findByRole("button", { name: FINE }));
  expect(screen.getByRole("checkbox", { name: "Локальную модель — через прокси" })).toBeDisabled();
  expect(screen.getByText(/Включите локальную модель/)).toBeInTheDocument();
});

test("правка в «Тонкой настройке» сохраняется как обычно", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="asr" />);
  await userEvent.click(await screen.findByRole("button", { name: FINE }));
  await userEvent.click(screen.getByRole("switch", { name: "Уточнять время каждого слова" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { asr: { align: false } }));
});

test("Jira: заданный шаблон ключа — «Тонкая настройка» раскрыта сразу", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({
    ...structuredClone(settings), integrations: { ...settings.integrations, jira_pattern: "DEMO-\\d+" },
  });
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="jira" />);
  expect(await screen.findByRole("button", { name: FINE })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("textbox", { name: "Шаблон ключа для текста" })).toHaveValue("DEMO-\\d+");
});
