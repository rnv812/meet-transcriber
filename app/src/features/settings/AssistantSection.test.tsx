import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import {
  AGY_PLAN_NOTICE, AUTO_ORDER, AUTO_SHORT, RECHECK_MS, RECHECK_TRIES, agyModelError, codexModelError, defaultCard,
  enabledOf, loopback, opencodeModelError, privacyLine, proxyError,
} from "./ModelsSection";
import { cardOfLabel } from "./ModelCard";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { AssistantInfo, CliModels, ProviderCheck } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getAssistant: vi.fn(),
  checkProvider: vi.fn(),
  listLocalModels: vi.fn(),
  getCliModels: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  pickFolder: vi.fn(),
  openUrl: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const settings = {
  recording: { speaker_name: "Вы", auto_transcribe: true },
  ui: { notifications: "all" },
  llm: { provider: "auto", model: "sonnet", base_url: "http://127.0.0.1:1234/v1", local_model: null,
    enabled: ["claude-code", "codex"] },
  assist: { vault: null, window_seconds: 20, port: 8765, voices: true },
  assistant: { knowledge_dir: "D:\\kb", notes_dir: null, notes_subdir: "Встречи" },
};
const info: AssistantInfo = {
  provider: "claude-code",
  setting: "auto",
  checking: false,
  knowledge_dir: "D:\\kb",
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
  vi.mocked(api.listLocalModels).mockResolvedValue({ ok: true, models: [] });
  vi.mocked(api.getCliModels).mockResolvedValue({ models: [] });
  vi.mocked(shell.pickFolder).mockResolvedValue(null);
});

/** Модели Codex из CLI (`model/list`): у каждой свои уровни. */
const CODEX_LIST: CliModels = {
  models: [
    { id: "gpt-6.1-terra", label: "GPT-6.1 Terra", default: true, default_effort: "low",
      efforts: [{ id: "low" }, { id: "high" }, { id: "ultra" }] },
    { id: "gpt-6-luna", label: "GPT-6-Luna", efforts: [{ id: "medium" }, { id: "max" }] },
  ],
};

/** «Модели и анализ» (0.6.2; 0.4 — «Модели ИИ»): провайдеры, модели Claude Code и OpenCode, локальная модель, прокси. */
const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="models" />);
/** «Ассистент» (0.4): участник встречи, база знаний, живая расшифровка. */
const openAssistant = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" />);
/** Строка модели в таблице «Модели» (0.4: таблица вместо групп). */
const option = (name: string) => screen.getByRole("row", { name });

test("раздел «Модели и анализ» есть в меню и открывается по initial", async () => {
  open();
  expect(screen.getByRole("button", { name: "Модели и анализ" })).toHaveAttribute("aria-current", "page");
  expect(await screen.findByRole("radio", { name: "Авто" })).toBeChecked();
});

test("статусы провайдеров — из getAssistant().available: бейджи DS, путь и адрес — в подсказке", async () => {
  open();
  const found = await within(await screen.findByRole("row", { name: "Claude Code" })).findByText("найден");
  expect(found).toHaveClass("badge", "badge--fresh");
  expect(found).toHaveAccessibleDescription("C:\\Users\\me\\.local\\bin\\claude.exe");
  expect(within(option("Codex")).getByText("не найден")).toHaveClass("badge"); expect(within(option("Codex")).getByText("не найден")).not.toHaveClass("badge--error");
  expect(within(option("Codex")).getByText(/не найден — установите/)).toBeInTheDocument();
  expect(within(option("Codex")).getByText("github.com/openai/codex")).toBeInTheDocument();
  const local = within(option("Локальная (LM Studio / Ollama)")).getByText("недоступен");
  expect(local).toHaveClass("badge"); expect(local).not.toHaveClass("badge--error");
  expect(local).toHaveAccessibleDescription("адрес: http://127.0.0.1:1234/v1");
  expect(within(option("Авто")).getByText("сейчас: Claude Code")).toHaveClass("badge", "badge--info");
});

test("«Модели» — таблица DS: радио «по умолчанию» .rd, «включена» — переключатель", async () => {
  open();
  const table = await screen.findByRole("table", { name: "Модели" });
  expect(table).toHaveClass("tbl");
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["Модель", "По умолчанию", "Включена", "Проверка"]);
  for (const radio of within(table).getAllByRole("radio")) expect(radio).toHaveClass("rd");
  expect(within(option("Codex")).getByRole("switch", { name: "Включить: Codex" })).toHaveClass("switch");
  expect(within(table).queryByRole("checkbox")).toBeNull();
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
  const claude = await screen.findByRole("row", { name: "Claude Code" });
  expect(await within(claude).findByText("claude.ai/code")).toBeInTheDocument();
  expect(within(option("Codex")).getByText("найден")).toHaveAccessibleDescription("C:\\codex.exe");
  expect(within(option("Локальная (LM Studio / Ollama)")).getByText("доступен"))
    .toHaveAccessibleDescription("адрес: http://127.0.0.1:1234/v1");
  expect(within(option("Авто")).getByText("сейчас: Локальная (LM Studio / Ollama)")).toBeInTheDocument();
});

test("выбран Codex — «Авто» не выдаёт Codex за свой выбор, а показывает порядок", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(structuredClone(settings), { llm: { provider: "codex" } }));
  vi.mocked(api.getAssistant).mockResolvedValue({ ...structuredClone(info), provider: "codex", setting: "codex" });
  open();
  const auto = await screen.findByRole("row", { name: "Авто" });
  // Порядок выбора — подсказкой у короткой подписи.
  expect(await within(auto).findByText(AUTO_SHORT)).toHaveAccessibleDescription(AUTO_ORDER);
  expect(within(auto).queryByText(/сейчас:/)).not.toBeInTheDocument();
});

test("пока резидент проверяет вход — у «Авто» «определяю…»", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({ ...structuredClone(info), provider: null, checking: true });
  open();
  const auto = await screen.findByRole("row", { name: "Авто" });
  expect(await within(auto).findByText("определяю…")).toBeInTheDocument();
});

test("«Проверить» Codex: «Проверяю…» без повторного нажатия, потом текст ошибки входа", async () => {
  let finish: (v: ProviderCheck) => void = () => {};
  vi.mocked(api.checkProvider).mockImplementation(() => new Promise((r) => { finish = r; }));
  open();
  await screen.findByText("сейчас: Claude Code");
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
  expect(screen.queryByText("Локальная модель не использует базу знаний.")).toBeNull();
  await userEvent.click(screen.getByRole("radio", { name: "Локальная (LM Studio / Ollama)" }));
  expect(screen.getByText("Локальная модель не использует базу знаний.")).toBeInTheDocument();
  expect(screen.getByLabelText("Адрес сервера")).toHaveValue("http://127.0.0.1:1234/v1");
  await userEvent.type(screen.getByRole("textbox", { name: "Имя модели" }), "qwen3");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    llm: { provider: "openai-compatible", enabled: ["claude-code", "codex", "openai-compatible"], local_model: "qwen3" },
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
  const model = await screen.findByRole("textbox", { name: "Имя модели" });
  await userEvent.clear(model);
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { local_model: null } }));
});

test("база знаний: «Выбрать папку…» берёт путь из диалога, «Очистить» — null", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\materials");
  openAssistant();
  const kb = await screen.findByRole("group", { name: "База знаний для ассистента" });
  expect(within(kb).getByText("D:\\kb")).toBeInTheDocument();
  await userEvent.click(within(kb).getByRole("button", { name: "Очистить" }));
  expect(within(kb).getByText("Не задана")).toBeInTheDocument();
  await userEvent.click(within(kb).getByRole("button", { name: "Выбрать папку…" }));
  expect(await within(kb).findByText("E:\\materials")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assistant: { knowledge_dir: "E:\\materials" },
  }));
});

test("папки заметок в «Ассистенте» больше нет — встречи выгружаются в «Экспорте»", async () => {
  openAssistant();
  await screen.findByRole("group", { name: "База знаний для ассистента" });
  expect(screen.queryByRole("group", { name: "Папка заметок" })).toBeNull();
  expect(screen.queryByLabelText("Подпапка для встреч")).toBeNull();
});

test("отказ в диалоге выбора папки ничего не меняет", async () => {
  openAssistant();
  const kb = await screen.findByRole("group", { name: "База знаний для ассистента" });
  await userEvent.click(within(kb).getByRole("button", { name: "Выбрать папку…" }));
  await waitFor(() => expect(shell.pickFolder).toHaveBeenCalledWith("D:\\kb"));
  expect(within(kb).getByText("D:\\kb")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

test("окно живой расшифровки уходит в assist.window_seconds; вне 5..120 — не сохранить", async () => {
  openAssistant();
  await userEvent.click(await screen.findByRole("button", { name: "Тонкая настройка" }));
  const win = await screen.findByLabelText("Окно живой расшифровки, с");
  expect(win).toHaveAttribute("min", "5");
  expect(win).toHaveAttribute("max", "120");
  expect(win).toHaveAttribute("step", "5");
  await userEvent.clear(win);
  await userEvent.type(win, "200");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  // Ошибку, из-за которой не сохранить, нельзя свернуть.
  await userEvent.click(screen.getByRole("button", { name: "Тонкая настройка" }));
  expect(screen.getByText("От 5 до 120 секунд")).toBeVisible();
  await userEvent.clear(win);
  await userEvent.type(win, "30");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { assist: { window_seconds: 30 } }));
});

test("окно вне 5..120 из файла настроек не мешает сохранять другое", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { assist: { window_seconds: 3 } }));
  openAssistant();
  const kb = await screen.findByRole("group", { name: "База знаний для ассистента" });
  await userEvent.click(within(kb).getByRole("button", { name: "Очистить" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assistant: { knowledge_dir: null },
  }));
});

test("повторный запрос раздела возвращает на «Ассистент»", async () => {
  const { rerender } = render(
    <SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" initialTick={1} />);
  await screen.findByRole("switch", { name: "Ассистент — участник встречи" });
  await userEvent.click(screen.getByRole("button", { name: "Звук" }));
  expect(screen.getByRole("button", { name: "Ассистент" })).not.toHaveAttribute("aria-current");
  rerender(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" initialTick={2} />);
  expect(screen.getByRole("button", { name: "Ассистент" })).toHaveAttribute("aria-current", "page");
});

test("«определяю…» переспрашивает резидента не бесконечно", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    vi.mocked(api.getAssistant).mockResolvedValue({ ...structuredClone(info), provider: null, checking: true });
    open();
    await screen.findByRole("row", { name: "Авто" });
    for (let i = 0; i < 40; i++) await vi.advanceTimersByTimeAsync(RECHECK_MS);
    expect(vi.mocked(api.getAssistant).mock.calls.length).toBeLessThanOrEqual(RECHECK_TRIES + 1);
  } finally {
    vi.useRealTimers();
  }
});

test("«не найден — установите»: ссылка открывается в браузере через оболочку", async () => {
  open();
  const group = await screen.findByRole("row", { name: "Codex" });
  const codex = await within(group).findByRole("button", { name: "github.com/openai/codex" });
  await userEvent.click(codex);
  expect(shell.openUrl).toHaveBeenCalledWith("https://github.com/openai/codex");
});

const proxyGroup = () => screen.getByRole("radiogroup", { name: "Прокси для подключения к моделям" });

test("прокси: «Как в системе» подписан адресом из getAssistant().proxy", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({
    ...structuredClone(info),
    proxy: { mode: "system", effective: "http://127.0.0.1:3067", source: "system", system: "http://127.0.0.1:3067" },
  });
  open();
  expect(await screen.findByRole("radio", { name: "Как в системе (сейчас: 127.0.0.1:3067)" })).toBeChecked();
  expect(within(proxyGroup()).getByRole("radio", { name: "Без прокси" })).not.toBeChecked();
  expect(screen.queryByLabelText("Адрес прокси")).toBeNull();
});

test("прокси: в системе не задан — так и сказано; без сведений резидента — просто «Как в системе»", async () => {
  vi.mocked(api.getAssistant).mockResolvedValueOnce({
    ...structuredClone(info),
    proxy: { mode: "system", effective: null, source: null, system: null },
  });
  open();
  expect(await screen.findByRole("radio", { name: "Как в системе (сейчас: не задан)" })).toBeChecked();
});

test("прокси: прежний резидент без сведений — вариант без подписи", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  expect(within(proxyGroup()).getByRole("radio", { name: "Как в системе" })).toBeChecked();
});

test("прокси: «Без прокси» уходит в llm.proxy = none", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  await userEvent.click(within(proxyGroup()).getByRole("radio", { name: "Без прокси" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { proxy: "none" } }));
});

test("прокси: свой адрес — проверка на месте, «Сохранить» недоступно, пока адрес негоден", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  await userEvent.click(within(proxyGroup()).getByRole("radio", { name: "Свой адрес…" }));
  const input = screen.getByLabelText("Адрес прокси");
  expect(screen.getByText(/Укажите адрес прокси/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.type(input, "127.0.0.1:8080");
  expect(screen.getByText(/http:\/\/, https:\/\/ или socks5:\/\//)).toBeInTheDocument();
  await userEvent.clear(input);
  await userEvent.type(input, "http://10.0.0.1:3128");
  expect(screen.queryByText(/http:\/\/, https:\/\/ или socks5:\/\//)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { proxy: "http://10.0.0.1:3128" } }));
});

test("прокси: сохранённый свой адрес показан в поле", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { llm: { proxy: "socks5://127.0.0.1:1080" } }));
  open();
  expect(await screen.findByLabelText("Адрес прокси")).toHaveValue("socks5://127.0.0.1:1080");
  expect(within(proxyGroup()).getByRole("radio", { name: "Свой адрес…" })).toBeChecked();
});

test("прокси: «?» объясняет, зачем он нужен", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  await userEvent.click(screen.getByRole("button", { name: "Зачем нужен прокси" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent(
    "Claude Code, Codex, OpenCode и Antigravity CLI сами не используют системный прокси Windows — приложение передаёт его им. "
    + "Прокси нужен, если доступ к сервисам идёт через VPN или прокси-сервер.");
});

test.each([
  ["system", null],
  ["none", null],
  ["http://127.0.0.1:8080", null],
  ["socks5://user:pass@[::1]:1080/", null],
  ["", "Укажите адрес прокси"],
  ["ftp://h:21", "http://, https:// или socks5://"],
  ["http://h", "нет порта"],
  ["http://:80", "нет узла"],
  ["http://h:99999", "не распознан"],
  ["http://h:80/path", "только схема"],
])("proxyError(%j)", (value, fragment) => {
  const error = proxyError(value);
  if (fragment === null) expect(error).toBeNull();
  else expect(error).toContain(fragment);
});

test("живые подсказки (агент-участник выключен): по умолчанию «Сдержанно» и «Как у агента»; выбор уходит в assist", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { assist: { participant: false } }));
  openAssistant();
  await userEvent.click(await screen.findByRole("button", { name: "Тонкая настройка" }));
  const activity = await screen.findByRole("radiogroup", { name: "Активность подсказок" });
  expect(within(activity).getByRole("radio", { name: "Сдержанно" })).toBeChecked();
  const tier = screen.getByRole("radiogroup", { name: "Модель для живых подсказок" });
  expect(within(tier).getByRole("radio", { name: "Как у агента" })).toBeChecked();
  await userEvent.click(within(activity).getByRole("radio", { name: "Активно" }));
  await userEvent.click(within(tier).getByRole("radio", { name: "Быстрее" }));
  // «Быстрее» поясняется для того, кто отвечает сейчас (Claude Code).
  expect(screen.getByText("«Быстрее» — Claude Code: модель Haiku без размышлений")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("combobox", { name: "Сколько подсказок держать" }));
  await userEvent.click(screen.getByRole("option", { name: "3" }));
  await userEvent.click(screen.getByRole("switch", { name: "Не отвлекать по умолчанию" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assist: { activity: "active", hints_model: "fast", max_hints: 3, quiet_default: true },
  }));
});

test("«Только сводка» — число подсказок не выбирается; у каждой настройки есть «?»", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { assist: { activity: "summary", max_hints: 0, participant: false } }));
  openAssistant();
  await userEvent.click(await screen.findByRole("button", { name: "Тонкая настройка" }));
  const max = await screen.findByRole("combobox", { name: "Сколько подсказок держать" });
  expect(max).toBeDisabled();
  expect(max).toHaveTextContent("По активности (5 или 8)");
  for (const label of ["Что такое активность подсказок", "Какая модель ведёт подсказки", "Что значит «Не отвлекать»"]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
});

test("модель Claude Code: правка уходит в llm.model; под таблицей — карточка только выбранной модели", async () => {
  open();
  const model = await screen.findByRole("textbox", { name: "Модель Claude Code" });
  expect(model).toHaveValue("sonnet");
  // Карточка одной модели: «Авто» сейчас выбирает Claude Code — его и показываем.
  expect(screen.getByRole("heading", { name: "Настройки: Claude Code" })).toBeInTheDocument();
  expect(screen.getByText("Её сейчас выбирает «Авто»")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Модель Codex" })).toBeNull();
  await userEvent.clear(model);
  await userEvent.type(model, "opus");
  // Список Claude Code следует за полем.
  expect(screen.getByRole("combobox", { name: "Список: Модель Claude Code" })).toHaveTextContent("Opus");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { model: "opus" } }));
  // Модель по умолчанию — Codex: карточка — его.
  await userEvent.click(screen.getByRole("radio", { name: "Codex" }));
  expect(screen.queryByRole("textbox", { name: "Модель Claude Code" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "Модель Codex" })).toBeInTheDocument();
  expect(screen.getByText("Модель по умолчанию")).toBeInTheDocument();
  // «Настроить» у строки — карточка этой модели, выбранная строка отмечена.
  await userEvent.click(screen.getByRole("button", { name: "Настроить: Claude Code" }));
  expect(screen.getByRole("textbox", { name: "Модель Claude Code" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Настроить: Claude Code" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Выбрана в таблице; по умолчанию — Codex")).toBeInTheDocument();
  // Щелчок по строке — тоже.
  await userEvent.click(within(option("Antigravity CLI")).getByText("Antigravity CLI"));
  expect(screen.getByRole("textbox", { name: "Модель Antigravity CLI" })).toBeInTheDocument();
  expect(option("Antigravity CLI")).toHaveClass("mtable__row--on");
});

test("0.5: уровень рассуждений — рядом с моделью Claude Code; «по умолчанию» — пусто", async () => {
  open();
  const effort = await screen.findByRole("combobox", { name: "Уровень рассуждений Claude Code" });
  expect(effort).toHaveTextContent("По умолчанию");
  expect(effort.closest(".model-effort")).toContainElement(screen.getByRole("textbox", { name: "Модель Claude Code" }));
  await userEvent.click(effort);
  await userEvent.click(screen.getByRole("option", { name: /^высокий/ }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { effort: "high" } }));
  expect(api.getCliModels).not.toHaveBeenCalled();          // у Claude Code список постоянный
});

/** Список уровней (открыть и прочесть пункты, закрыть). */
async function optionsOf(name: string): Promise<string[]> {
  await userEvent.click(screen.getByRole("combobox", { name }));
  const names = screen.getAllByRole("option").map((o) => o.textContent ?? "");
  await userEvent.keyboard("{Escape}");
  return names;
}

test("0.6.2: Codex — модели и уровни из CLI; уровень вне списка модели сбрасывается на «по умолчанию»", async () => {
  vi.mocked(api.getCliModels).mockResolvedValue(structuredClone(CODEX_LIST));
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { llm: { provider: "codex", codex_effort: "high" } }));
  open();
  const list = await screen.findByRole("combobox", { name: "Список: Модель Codex" });
  expect(api.getCliModels).toHaveBeenCalledWith(ep, "codex", false);
  expect(list).toHaveTextContent("По умолчанию");
  expect(screen.getByText("Моделей в списке: 2")).toBeInTheDocument();
  // Без своей модели уровни — модели CLI по умолчанию (terra): low, high, ultra; «сейчас: низкий».
  let names = await optionsOf("Уровень рассуждений Codex");
  expect(names).toEqual(["По умолчаниюсейчас: низкий", "низкий", "высокий", "ультра"]);
  expect(screen.getByRole("combobox", { name: "Уровень рассуждений Codex" })).toHaveTextContent("высокий");
  // Другая модель: у неё нет «высокого» — уровень в черновике становится «по умолчанию».
  await userEvent.click(list);
  await userEvent.click(screen.getByRole("option", { name: /^GPT-6-Luna/ }));
  expect(screen.getByRole("textbox", { name: "Модель Codex" })).toHaveValue("gpt-6-luna");
  await waitFor(() => expect(screen.getByRole("combobox", { name: "Уровень рассуждений Codex" })).toHaveTextContent("По умолчанию"));
  names = await optionsOf("Уровень рассуждений Codex");
  expect(names.slice(1)).toEqual(["средний", "максимальный"]);
  // «Обновить список» — мимо кэша резидента.
  await userEvent.click(screen.getByRole("button", { name: "Обновить список" }));
  await waitFor(() => expect(api.getCliModels).toHaveBeenCalledWith(ep, "codex", true));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    llm: { codex_model: "gpt-6-luna", codex_effort: "" } }));
});

test("0.6.2: Codex — список не получен: ошибка текстом, имя вписывается в поле, уровни запасные", async () => {
  vi.mocked(api.getCliModels).mockResolvedValue({ models: [], error: "codex app-server: нет ответа" });
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { llm: { provider: "codex" } }));
  open();
  expect(await screen.findByText(/Список моделей не получен: codex app-server: нет ответа/)).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Список: Модель Codex" })).toBeNull();
  const model = screen.getByRole("textbox", { name: "Модель Codex" });
  await userEvent.type(model, "gpt&calc");
  expect(screen.getByText(/недопустимые символы/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.clear(model);
  await userEvent.type(model, "gpt-5.5");
  const names = await optionsOf("Уровень рассуждений Codex");
  expect(names.some((n) => n.startsWith("минимальный"))).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { codex_model: "gpt-5.5" } }));
});

test("OpenCode: не найден — ссылка opencode.ai/docs/; найден — путь", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({
    ...structuredClone(info),
    available: { ...structuredClone(info.available), opencode: { found: false, path: null } },
  });
  open();
  const oc = await screen.findByRole("row", { name: "OpenCode" });
  expect(await within(oc).findByText("opencode.ai/docs/")).toBeInTheDocument();
  await userEvent.click(within(oc).getByText("opencode.ai/docs/"));
  expect(shell.openUrl).toHaveBeenCalledWith("https://opencode.ai/docs/");
});

test("OpenCode: своё поле модели провайдер/модель, проверка на месте", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  expect(screen.queryByRole("textbox", { name: "Модель OpenCode" })).toBeNull();
  await userEvent.click(screen.getByRole("radio", { name: "OpenCode" }));
  const model = screen.getByRole("textbox", { name: "Модель OpenCode" });
  expect(model).toHaveValue("");
  await userEvent.type(model, "sonnet");
  expect(screen.getByText(/в виде провайдер\/модель/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.clear(model);
  await userEvent.type(model, "anthropic/claude-sonnet-4-5");
  expect(screen.queryByText(/в виде провайдер\/модель/)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    llm: { provider: "opencode", enabled: ["claude-code", "codex", "opencode"], opencode_model: "anthropic/claude-sonnet-4-5" },
  }));
});

test("opencodeModelError — как у резидента", () => {
  expect(opencodeModelError("")).toBeNull();
  expect(opencodeModelError("ollama/qwen3:8b")).toBeNull();
  expect(opencodeModelError("openrouter/meta-llama/llama-3.3-70b")).toBeNull();
  expect(opencodeModelError("sonnet")).toMatch(/провайдер\/модель/);
  expect(opencodeModelError("a/b c")).toMatch(/недопустимые символы/);
  expect(opencodeModelError("a/%PATH%")).toMatch(/недопустимые символы/);
});

test("«Проверить» OpenCode уходит провайдеру opencode", async () => {
  vi.mocked(api.checkProvider).mockResolvedValue({ ok: false, error: "не авторизован: нет входа", provider: "opencode" });
  open();
  const oc = await screen.findByRole("row", { name: "OpenCode" });
  await userEvent.click(within(oc).getByRole("button", { name: "Проверить" }));
  expect(await within(oc).findByText("не авторизован: нет входа")).toBeInTheDocument();
  expect(api.checkProvider).toHaveBeenCalledWith(ep, "opencode");
});

test("«Авто» OpenCode и Antigravity CLI не выбирает — так и подписано", async () => {
  expect(AUTO_ORDER).toBe(
    "первый готовый из включённых: Claude Code → Codex → локальная (OpenCode и Antigravity CLI — только явным выбором)");
});

test("Antigravity CLI: не найден — ссылка на установку", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue({
    ...structuredClone(info),
    available: { ...structuredClone(info.available), agy: { found: false, path: null } },
  });
  open();
  const row = await screen.findByRole("row", { name: "Antigravity CLI" });
  expect(await within(row).findByText("antigravity.google/docs/cli")).toBeInTheDocument();
  await userEvent.click(within(row).getByText("antigravity.google/docs/cli"));
  expect(shell.openUrl).toHaveBeenCalledWith("https://antigravity.google/docs/cli");
});

test("0.6.2: Antigravity CLI — без входа строка с командой входа; уровни из CLI", async () => {
  vi.mocked(api.getCliModels).mockResolvedValueOnce({
    models: [], error: "Antigravity CLI: вход не выполнен", login: '"agy" acp --login --gemini-dir "C:\\meet\\agy"' });
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { llm: { provider: "agy", agy_effort: "high" } }));
  open();
  expect(await screen.findByText('"agy" acp --login --gemini-dir "C:\\meet\\agy"')).toBeInTheDocument();
  expect(screen.getByText(AGY_PLAN_NOTICE)).toBeInTheDocument();
  vi.mocked(api.getCliModels).mockResolvedValueOnce({ models: [
    { id: "gemini-3-pro", label: "Gemini 3 Pro", default: true, default_effort: "high",
      efforts: [{ id: "low", label: "Low" }, { id: "high", label: "High" }] }] });
  await userEvent.click(screen.getByRole("button", { name: "Обновить список" }));
  expect(await screen.findByRole("combobox", { name: "Список: Модель Antigravity CLI" })).toHaveTextContent("По умолчанию");
  expect(api.getCliModels).toHaveBeenLastCalledWith(ep, "agy", true);
  const names = await optionsOf("Уровень рассуждений Antigravity CLI");
  expect(names).toEqual(["По умолчаниюсейчас: высокий", "низкий", "высокий"]);
  await userEvent.click(screen.getByRole("combobox", { name: "Уровень рассуждений Antigravity CLI" }));
  await userEvent.click(screen.getByRole("option", { name: /^низкий/ }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { agy_effort: "low" } }));
});

test("Antigravity CLI: поле модели, проверка на месте и предупреждение о тарифах", async () => {
  open();
  await screen.findByText("сейчас: Claude Code");
  expect(screen.queryByRole("textbox", { name: "Модель Antigravity CLI" })).toBeNull();
  expect(screen.queryByText(AGY_PLAN_NOTICE)).toBeNull();
  await userEvent.click(screen.getByRole("radio", { name: "Antigravity CLI" }));
  const model = screen.getByRole("textbox", { name: "Модель Antigravity CLI" });
  expect(model).toHaveValue("");
  expect(screen.getByText(AGY_PLAN_NOTICE)).toBeInTheDocument();
  await userEvent.type(model, "gemini&calc");
  expect(screen.getByText(/недопустимые символы/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.clear(model);
  await userEvent.type(model, "gemini-3-pro");
  expect(screen.queryByText(/недопустимые символы/)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    llm: { provider: "agy", enabled: ["claude-code", "codex", "agy"], agy_model: "gemini-3-pro" },
  }));
});

test("agyModelError — как у резидента", () => {
  expect(agyModelError("")).toBeNull();
  expect(agyModelError("gemini-3-pro")).toBeNull();
  expect(agyModelError("gemini-3-flash@preview")).toBeNull();
  expect(agyModelError("gem ini")).toMatch(/недопустимые символы/);
  expect(agyModelError("a%PATH%")).toMatch(/недопустимые символы/);
  expect(agyModelError("a^b")).toMatch(/недопустимые символы/);
});

test("0.6.2: чья карточка без выбора в таблице; строка поиска — карточка своей модели; имя модели Codex", () => {
  expect(defaultCard({ provider: "codex" }, null)).toBe("codex");
  expect(defaultCard({ provider: "auto", enabled: ["codex", "openai-compatible"] }, null)).toBe("codex");
  expect(defaultCard({ provider: "auto", enabled: ["claude-code", "codex"] },
    { ...info, setting: "auto", provider: "codex" })).toBe("codex");
  expect(defaultCard(undefined, null)).toBe("claude-code");
  expect(cardOfLabel("Уровень рассуждений Antigravity CLI")).toBe("agy");
  expect(cardOfLabel("Адрес сервера")).toBe("openai-compatible");
  expect(cardOfLabel("Прокси для подключения к моделям")).toBeNull();
  expect(codexModelError("")).toBeNull();
  expect(codexModelError("gpt-6.1-terra")).toBeNull();
  expect(codexModelError("gpt 6")).toMatch(/недопустимые символы/);
  expect(codexModelError("-x")).toMatch(/недопустимые символы/);
});

test("«Проверить» Antigravity CLI уходит провайдеру agy", async () => {
  vi.mocked(api.checkProvider).mockResolvedValue({ ok: false, error: "Antigravity CLI: вход не выполнен", provider: "agy" });
  open();
  const row = await screen.findByRole("row", { name: "Antigravity CLI" });
  await userEvent.click(within(row).getByRole("button", { name: "Проверить" }));
  expect(await within(row).findByText("Antigravity CLI: вход не выполнен")).toBeInTheDocument();
  expect(api.checkProvider).toHaveBeenCalledWith(ep, "agy");
});


// --- «Модели»: несколько включённых и одна по умолчанию (0.3.4) -------------------------

test("включённые модели: старый конфиг читается как у резидента, по умолчанию включена всегда", () => {
  expect(enabledOf({ provider: "codex" })).toEqual(["codex"]);
  expect(enabledOf({ provider: "auto" })).toEqual(["claude-code", "codex", "openai-compatible"]);
  expect(enabledOf({ provider: "opencode", enabled: ["openai-compatible", "x"] })).toEqual(["opencode", "openai-compatible"]);
});

test("подсказка приватности: локальная — только на этом компьютере", () => {
  expect(loopback("http://127.0.0.1:1234/v1")).toBe(true);
  expect(loopback("http://localhost:11434/v1")).toBe(true);
  expect(loopback("http://10.0.0.5:1234/v1")).toBe(false);
  expect(privacyLine("openai-compatible", "http://127.0.0.1:1234/v1")).toBe("локальная — данные не покидают компьютер");
  expect(privacyLine("claude-code", "http://127.0.0.1:1234/v1")).toBe("облачная — текст встречи уходит провайдеру");
  expect(privacyLine("openai-compatible", "http://10.0.0.5:1234/v1")).toBe("сервер в сети — текст встречи уходит на 10.0.0.5:1234");
});

test("включить вторую модель: у каждой включённой — куда уходит текст; сохраняется список", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, {
    llm: { provider: "openai-compatible", enabled: ["openai-compatible"], local_model: "qwen3" },
  }));
  open();
  const local = await screen.findByRole("row", { name: "Локальная (LM Studio / Ollama)" });
  expect(within(local).getByText("локальная — данные не покидают компьютер")).toBeInTheDocument();
  expect(within(local).getByRole("radio", { name: "Локальная (LM Studio / Ollama)" })).toBeChecked();
  // Модель по умолчанию выключить нельзя.
  expect(within(local).getByRole("switch", { name: "Включить: Локальная (LM Studio / Ollama)" })).toBeDisabled();
  const claude = screen.getByRole("row", { name: "Claude Code" });
  expect(within(claude).queryByText(/облачная/)).toBeNull();
  await userEvent.click(within(claude).getByRole("switch", { name: "Включить: Claude Code" }));
  expect(within(claude).getByText("облачная — текст встречи уходит провайдеру")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Локальная (LM Studio / Ollama)" })).toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    llm: { enabled: ["claude-code", "openai-compatible"] },
  }));
});

test("у «Авто» нельзя выключить последнюю его модель — ни одной включённой не бывает", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, {
    llm: { provider: "auto", enabled: ["claude-code", "opencode"] },
  }));
  open();
  const claude = await screen.findByRole("switch", { name: "Включить: Claude Code" });
  expect(claude).toBeDisabled();
  expect(claude).toHaveAccessibleDescription(/Последняя модель для «Авто»/);
  // Включили другую — первую уже можно выключить.
  await userEvent.click(screen.getByRole("switch", { name: "Включить: Codex" }));
  expect(claude).toBeEnabled();
  // OpenCode «Авто» не выбирает — его выключать можно всегда.
  expect(screen.getByRole("switch", { name: "Включить: OpenCode" })).toBeEnabled();
});

test("«Авто» без своих моделей не выбрать — сначала включить одну, само оно никого не включает (N2)", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, {
    llm: { provider: "opencode", enabled: ["opencode"] },
  }));
  open();
  const auto = await screen.findByRole("radio", { name: "Авто" });
  expect(auto).toBeDisabled();
  expect(within(option("Авто")).getByText(/Включите Claude Code, Codex или локальную модель/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("switch", { name: "Включить: Codex" }));
  expect(auto).toBeEnabled();
  await userEvent.click(auto);
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    llm: { provider: "auto", enabled: ["codex", "opencode"] },
  }));
});
