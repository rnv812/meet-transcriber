import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { HIGHLIGHT_MS, SettingsPane } from "./SettingsPane";
import { SettingsSearch } from "./SettingsSearch";
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
  asr: { backend: "faster-whisper", model: "large-v3", cpu_model: "small", device: "auto", language: "ru", align: true },
  llm: { provider: "claude-code", enabled: ["claude-code", "openai-compatible"], base_url: "http://127.0.0.1:1234/v1" },
  assist: { participant: true },
  assistant: { knowledge_dir: null },
  recording: { speaker_name: "Вы" },
  ui: { notifications: "all" },
};

const scrolls: ScrollIntoViewOptions[] = [];
beforeEach(() => {
  vi.clearAllMocks();
  scrolls.length = 0;
  Element.prototype.scrollIntoView = function (o?: boolean | ScrollIntoViewOptions) {
    scrolls.push(typeof o === "object" ? o : {});
  };
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
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

afterEach(() => {
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  delete (window as { matchMedia?: unknown }).matchMedia;
});

const open = async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await screen.findByLabelText("Ваше имя в расшифровке");
  return screen.getByRole("combobox", { name: "Поиск по настройкам" });
};
const options = () => within(screen.getByRole("listbox", { name: "Найденные настройки" })).getAllByRole("option");

test("поле «Поиск по настройкам» над меню — Aurora field--sm; ввод заменяет меню выдачей «Раздел › Параметр»", async () => {
  const field = await open();
  expect(field).toHaveClass("field", "field--sm");
  expect(field).toHaveAttribute("placeholder", "Поиск");
  expect(screen.getByRole("navigation", { name: "Разделы настроек" })).toContainElement(field);
  await userEvent.type(field, "прокси");
  expect(screen.queryByRole("button", { name: "Звук" })).toBeNull();
  const names = options().map((o) => o.textContent);
  expect(names[0]).toBe("Модели ИИ › Прокси для подключения к моделям");
  expect(names).toContain("Модели ИИ › Локальную модель — через прокси");
  expect(field).toHaveAttribute("aria-expanded", "true");
  expect(field).toHaveAttribute("aria-activedescendant", options()[0]!.id);
});

test("ничего не найдено — так и сказано; меню вернётся после очистки", async () => {
  const field = await open();
  await userEvent.type(field, "абракадабра");
  expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
  expect(screen.queryByRole("option")).toBeNull();
  await userEvent.clear(field);
  expect(screen.getByRole("button", { name: "Звук" })).toBeInTheDocument();
});

test("Esc очищает поиск, меню возвращается", async () => {
  const field = await open();
  await userEvent.type(field, "голос");
  expect(screen.queryByRole("button", { name: "Звук" })).toBeNull();
  await userEvent.keyboard("{Escape}");
  expect(field).toHaveValue("");
  expect(screen.getByRole("button", { name: "Звук" })).toBeInTheDocument();
});

test("↑/↓ по выдаче, Enter — раздел, «Тонкая настройка» раскрыта, строка прокручена и подсвечена", async () => {
  const field = await open();
  await userEvent.type(field, "прокси");
  const target = options().findIndex((o) => o.textContent === "Модели ИИ › Локальную модель — через прокси");
  expect(target).toBeGreaterThan(0);
  await userEvent.keyboard("{ArrowDown}".repeat(target));
  expect(options()[target]).toHaveAttribute("aria-selected", "true");
  expect(field).toHaveAttribute("aria-activedescendant", options()[target]!.id);
  await userEvent.keyboard("{ArrowUp}{ArrowDown}{Enter}");
  expect(await screen.findByRole("heading", { level: 2, name: "Модели ИИ" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Тонкая настройка" })).toHaveAttribute("aria-expanded", "true");
  const box = await screen.findByRole("switch", { name: "Локальную модель — через прокси" });
  const row = box.closest(".srow")!;
  await waitFor(() => expect(row).toHaveClass("setting-found"));
  expect(box).toHaveFocus();
  expect(scrolls.at(-1)).toEqual({ block: "center", behavior: "smooth" });
  // Поиск очищен — снова меню, раздел выбран.
  expect(field).toHaveValue("");
  expect(screen.getByRole("button", { name: "Модели ИИ" })).toHaveAttribute("aria-current", "page");
  await waitFor(() => expect(row).not.toHaveClass("setting-found"), { timeout: HIGHLIGHT_MS + 1000 });
});

test("клик по строке выдачи — открывает раздел; при reduced-motion прокрутка без анимации", async () => {
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q })) as unknown as typeof window.matchMedia;
  const field = await open();
  await userEvent.type(field, "главы");
  await userEvent.click(screen.getByRole("option", { name: "Анализ встречи › Главы" }));
  expect(await screen.findByRole("heading", { level: 2, name: "Анализ встречи" })).toBeInTheDocument();
  // Строка таблицы «Размечать / Показывать»: фокус — на первом переключателе.
  await waitFor(() => expect(screen.getByRole("switch", { name: "Главы: размечать" })).toHaveFocus());
  expect(screen.getByRole("switch", { name: "Главы: размечать" }).closest("tr")).toHaveClass("setting-found");
  expect(scrolls.at(-1)).toEqual({ block: "center", behavior: "auto" });
});

test("обычная строка не раскрывает «Тонкую настройку»", async () => {
  const field = await open();
  await userEvent.type(field, "язык речи");
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByRole("heading", { level: 2, name: "Распознавание" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("Язык речи")).toHaveFocus());
  expect(screen.getByRole("button", { name: "Тонкая настройка" })).toHaveAttribute("aria-expanded", "false");
});

test("Ctrl+F на панели настроек — к полю поиска (и в русской раскладке: по коду клавиши)", async () => {
  const field = await open();
  expect(field).not.toHaveFocus();
  fireEvent.keyDown(window, { key: "а", code: "KeyF", ctrlKey: true });
  expect(field).toHaveFocus();
});

test("подсветка — фон --selection, без анимации при reduced-motion", () => {
  const css = readFileSync(join(process.cwd(), "src", "features", "settings", "settings.css"), "utf8");
  expect(/\.setting-found \{([^}]*)\}/.exec(css)?.[1]).toMatch(/background: var\(--selection\)/);
  expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{ \.setting-found \{ transition: none; \} \}/);
});

test("своя «×» вместо системной: очищает запрос и возвращает фокус в поле", async () => {
  const onQuery = vi.fn();
  const ref = { current: null as HTMLInputElement | null };
  const { rerender } = render(<SettingsSearch query="" onQuery={onQuery} onPick={() => {}} inputRef={ref} />);
  expect(screen.queryByRole("button", { name: "Очистить поиск" })).toBeNull();
  rerender(<SettingsSearch query="прокси" onQuery={onQuery} onPick={() => {}} inputRef={ref} />);
  await userEvent.click(screen.getByRole("button", { name: "Очистить поиск" }));
  expect(onQuery).toHaveBeenLastCalledWith("");
  expect(screen.getByRole("combobox", { name: "Поиск по настройкам" })).toHaveFocus();
});
