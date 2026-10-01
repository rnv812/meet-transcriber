import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import { AUTO_ORDER, RECHECK_MS, RECHECK_TRIES } from "./AssistantSection";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { AssistantInfo, ProviderCheck } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getAssistant: vi.fn(),
  checkProvider: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  pickFolder: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  recording: { speaker_name: "Вы", auto_transcribe: true },
  ui: { notifications: "all" },
  llm: { provider: "auto", model: "sonnet", base_url: "http://127.0.0.1:1234/v1", local_model: null },
  assist: { vault: null, window_seconds: 20, port: 8765, voices: true },
  assistant: { knowledge_dir: "D:\\kb", notes_dir: null, notes_subdir: "Встречи" },
};
const info: AssistantInfo = {
  provider: "claude-code",
  setting: "auto",
  checking: false,
  knowledge_dir: "D:\\kb",
  notes_dir: null,
  available: {
    "claude-code": { found: true, path: "C:\\Users\\me\\.local\\bin\\claude.exe" },
    codex: { found: false, path: null },
    "openai-compatible": { found: false, base_url: "http://127.0.0.1:1234/v1" },
  },
};

type Patch = Record<string, Record<string, unknown>>;
const merge = (base: Patch, u: Patch): Patch => {
  const out = structuredClone(base);
  for (const [g, v] of Object.entries(u)) out[g] = { ...(out[g] ?? {}), ...v };
  return out;
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: merge(settings, u as Patch), restart_required: [],
  }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
  vi.mocked(api.getAssistant).mockResolvedValue(structuredClone(info));
  vi.mocked(api.checkProvider).mockResolvedValue({ ok: true, error: null, provider: "codex" });
  vi.mocked(shell.pickFolder).mockResolvedValue(null);
});

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" />);
const option = (name: string) => screen.getByRole("group", { name });

test("раздел «Ассистент» есть в меню и открывается по initial", async () => {
  open();
  expect(screen.getByRole("button", { name: "Ассистент" })).toHaveAttribute("aria-current", "page");
  expect(await screen.findByRole("radio", { name: "Авто" })).toBeChecked();
});

test("статусы провайдеров — из getAssistant().available", async () => {
  open();
  expect(await screen.findByText("найден: C:\\Users\\me\\.local\\bin\\claude.exe")).toBeInTheDocument();
  expect(within(option("Codex")).getByText(/не найден — установите/)).toBeInTheDocument();
  expect(within(option("Codex")).getByText("github.com/openai/codex")).toBeInTheDocument();
  expect(within(option("Локальная (LM Studio / Ollama)"))
    .getByText("адрес: http://127.0.0.1:1234/v1 — недоступен")).toBeInTheDocument();
  expect(within(option("Авто")).getByText("сейчас: Claude Code")).toBeInTheDocument();
});

test("не найден Claude Code — ссылка claude.ai/code; локальная отвечает — «доступен»", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({
    ...structuredClone(info), provider: "openai-compatible",
    available: {
      "claude-code": { found: false, path: null },
      codex: { found: true, path: "C:\\codex.exe" },
      "openai-compatible": { found: true, base_url: "http://127.0.0.1:1234/v1" },
    },
  });
  open();
  const claude = await screen.findByRole("group", { name: "Claude Code" });
  expect(await within(claude).findByText("claude.ai/code")).toBeInTheDocument();
  expect(within(option("Codex")).getByText("найден: C:\\codex.exe")).toBeInTheDocument();
  expect(within(option("Локальная (LM Studio / Ollama)"))
    .getByText("адрес: http://127.0.0.1:1234/v1 — доступен")).toBeInTheDocument();
  expect(within(option("Авто")).getByText("сейчас: Локальная (LM Studio / Ollama)")).toBeInTheDocument();
});

test("выбран Codex — «Авто» не выдаёт Codex за свой выбор, а показывает порядок", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(structuredClone(settings), { llm: { provider: "codex" } }));
  vi.mocked(api.getAssistant).mockResolvedValue({ ...structuredClone(info), provider: "codex", setting: "codex" });
  open();
  const auto = await screen.findByRole("group", { name: "Авто" });
  expect(await within(auto).findByText(AUTO_ORDER)).toBeInTheDocument();
  expect(within(auto).queryByText(/сейчас:/)).not.toBeInTheDocument();
});

test("пока резидент проверяет вход — у «Авто» «определяю…»", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({ ...structuredClone(info), provider: null, checking: true });
  open();
  const auto = await screen.findByRole("group", { name: "Авто" });
  expect(await within(auto).findByText("определяю…")).toBeInTheDocument();
});

test("«Проверить» Codex: «Проверяю…» без повторного нажатия, потом текст ошибки входа", async () => {
  let finish: (v: ProviderCheck) => void = () => {};
  vi.mocked(api.checkProvider).mockImplementation(() => new Promise((r) => { finish = r; }));
  open();
  await screen.findByText("найден: C:\\Users\\me\\.local\\bin\\claude.exe");
  await userEvent.click(within(option("Codex")).getByRole("button", { name: "Проверить" }));
  expect(api.checkProvider).toHaveBeenCalledWith(ep, "codex");
  const busy = within(option("Codex")).getByRole("button", { name: "Проверяю…" });
  expect(busy).toBeDisabled();
  finish({ ok: false, error: "не авторизован: Not logged in", provider: "codex" });
  expect(await within(option("Codex")).findByText("не авторизован: Not logged in")).toBeInTheDocument();
  expect(within(option("Codex")).getByRole("button", { name: "Проверить" })).toBeEnabled();
  // Резидент сбросил кэш выбора — сведения перечитаны.
  await waitFor(() => expect(api.getAssistant).toHaveBeenCalledTimes(2));
});

test("«Проверить» удачно — «работает»; у «Авто» проверяется auto", async () => {
  vi.mocked(api.checkProvider).mockResolvedValue({ ok: true, error: null, provider: "claude-code" });
  open();
  await screen.findByText("сейчас: Claude Code");
  await userEvent.click(within(option("Авто")).getByRole("button", { name: "Проверить" }));
  expect(api.checkProvider).toHaveBeenCalledWith(ep, "auto");
  expect(await within(option("Авто")).findByText("работает")).toBeInTheDocument();
});

test("ошибка запроса проверки — текстом, без «Error:»", async () => {
  vi.mocked(api.checkProvider).mockRejectedValue(new api.ApiError(400, "неизвестный провайдер: x"));
  open();
  await screen.findByText("сейчас: Claude Code");
  await userEvent.click(within(option("Claude Code")).getByRole("button", { name: "Проверить" }));
  expect(await within(option("Claude Code")).findByText("неизвестный провайдер: x")).toBeInTheDocument();
});

test("выбор «Локальная» — поля адреса и модели, предупреждение; сохранение перечитывает getAssistant", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  expect(screen.queryByText("Локальная модель не читает базу знаний")).toBeNull();
  await userEvent.click(screen.getByRole("radio", { name: "Локальная (LM Studio / Ollama)" }));
  expect(screen.getByText("Локальная модель не читает базу знаний")).toBeInTheDocument();
  expect(screen.getByLabelText("Адрес")).toHaveValue("http://127.0.0.1:1234/v1");
  await userEvent.type(screen.getByRole("textbox", { name: "Модель" }), "qwen3");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    llm: { provider: "openai-compatible", local_model: "qwen3" },
  }));
  await waitFor(() => expect(api.getAssistant).toHaveBeenCalledTimes(2));
});

test("несохранённые правки модели — «Проверить» предупреждает, что проверит сохранённое", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  expect(screen.queryByText(/Проверяются сохранённые настройки/)).toBeNull();
  await userEvent.click(screen.getByRole("radio", { name: "Codex" }));
  expect(screen.getByText(/Проверяются сохранённые настройки/)).toBeInTheDocument();
});

test("пустое имя локальной модели уходит как null", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, {
    llm: { provider: "openai-compatible", local_model: "qwen3" },
  }));
  open();
  const model = await screen.findByRole("textbox", { name: "Модель" });
  await userEvent.clear(model);
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { local_model: null } }));
});

test("база знаний: «Выбрать папку…» берёт путь из диалога, «Очистить» — null", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\notes");
  open();
  const notes = await screen.findByRole("group", { name: "Папка заметок" });
  await userEvent.click(within(notes).getByRole("button", { name: "Выбрать папку…" }));
  expect(await within(notes).findByText("E:\\notes")).toBeInTheDocument();
  const kb = screen.getByRole("group", { name: "База знаний" });
  expect(within(kb).getByText("D:\\kb")).toBeInTheDocument();
  await userEvent.click(within(kb).getByRole("button", { name: "Очистить" }));
  expect(within(kb).getByText("не задана")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assistant: { knowledge_dir: null, notes_dir: "E:\\notes" },
  }));
});

test("отказ в диалоге выбора папки ничего не меняет", async () => {
  open();
  const kb = await screen.findByRole("group", { name: "База знаний" });
  await userEvent.click(within(kb).getByRole("button", { name: "Выбрать папку…" }));
  await waitFor(() => expect(shell.pickFolder).toHaveBeenCalledWith("D:\\kb"));
  expect(within(kb).getByText("D:\\kb")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

test.each(["..\\x", "a/../b", "C:\\Встречи", "/abs", "\\\\srv\\share"])(
  "подпапка «%s» — подсказка и «Сохранить» недоступно", async (bad) => {
    open();
    const sub = await screen.findByLabelText("Подпапка для встреч");
    await userEvent.clear(sub);
    await userEvent.type(sub, bad);
    expect(screen.getByText("Только имя подпапки, без .. и полного пути")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

test("подпапка с вложенностью сохраняется", async () => {
  open();
  const sub = await screen.findByLabelText("Подпапка для встреч");
  await userEvent.clear(sub);
  await userEvent.type(sub, "Работа/Встречи");
  expect(screen.queryByText("Только имя подпапки, без .. и полного пути")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assistant: { notes_subdir: "Работа/Встречи" },
  }));
});

test("окно живой расшифровки уходит в assist.window_seconds; вне 5..120 — не сохранить", async () => {
  open();
  const win = await screen.findByLabelText("Окно живой расшифровки, с");
  expect(win).toHaveAttribute("min", "5");
  expect(win).toHaveAttribute("max", "120");
  expect(win).toHaveAttribute("step", "5");
  await userEvent.clear(win);
  await userEvent.type(win, "200");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.clear(win);
  await userEvent.type(win, "30");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { assist: { window_seconds: 30 } }));
});

test("окно вне 5..120 из файла настроек не мешает сохранять другое", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { assist: { window_seconds: 3 } }));
  open();
  const sub = await screen.findByLabelText("Подпапка для встреч");
  await userEvent.type(sub, "2");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assistant: { notes_subdir: "Встречи2" },
  }));
});

test("повторный запрос раздела возвращает на «Ассистент»", async () => {
  const { rerender } = render(
    <SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" initialTick={1} />);
  await screen.findByText("сейчас: Claude Code");
  await userEvent.click(screen.getByRole("button", { name: "Запись и устройства" }));
  expect(screen.getByRole("button", { name: "Ассистент" })).not.toHaveAttribute("aria-current");
  rerender(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" initialTick={2} />);
  expect(screen.getByRole("button", { name: "Ассистент" })).toHaveAttribute("aria-current", "page");
});

test("подпапка сохраняется без пробелов по краям", async () => {
  open();
  const sub = await screen.findByLabelText("Подпапка для встреч");
  await userEvent.clear(sub);
  await userEvent.type(sub, "  Мои встречи  ");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assistant: { notes_subdir: "Мои встречи" },
  }));
});

test("подпапка, отличающаяся только пробелами, — не правка", async () => {
  open();
  const sub = await screen.findByLabelText("Подпапка для встреч");
  await userEvent.type(sub, "  ");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

test("«определяю…» переспрашивает резидента не бесконечно", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    vi.mocked(api.getAssistant).mockResolvedValue({ ...structuredClone(info), provider: null, checking: true });
    open();
    await screen.findByRole("group", { name: "Авто" });
    for (let i = 0; i < 40; i++) await vi.advanceTimersByTimeAsync(RECHECK_MS);
    expect(vi.mocked(api.getAssistant).mock.calls.length).toBeLessThanOrEqual(RECHECK_TRIES + 1);
  } finally {
    vi.useRealTimers();
  }
});
