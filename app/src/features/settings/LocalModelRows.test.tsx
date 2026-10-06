import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import { modelLabel, sameModel, sizeText } from "./LocalModelRows";
import * as api from "../../lib/api";
import type { AssistantInfo, LocalModels } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getAssistant: vi.fn(),
  checkProvider: vi.fn(),
  listLocalModels: vi.fn(),
}));

const ep = { base: "/api", token: null };
const URL1 = "http://127.0.0.1:1234/v1";
const settings = (local_model: string | null = null, base_url = URL1) => ({
  recording: { speaker_name: "Вы", auto_transcribe: true },
  ui: { notifications: "all" },
  llm: { provider: "openai-compatible", model: "sonnet", base_url, local_model, enabled: ["openai-compatible"] },
  assist: { vault: null, window_seconds: 20, port: 8765, voices: true },
  assistant: { knowledge_dir: null, notes_dir: null, notes_subdir: "Встречи" },
});
const info: AssistantInfo = {
  provider: "openai-compatible", setting: "openai-compatible", checking: false, knowledge_dir: null,
  available: { "openai-compatible": { found: true, base_url: URL1 } },
};
const found = (...ids: string[]): LocalModels => ({
  ok: true, source: "openai", missing: false, warning: null, error: null, reason: null,
  models: ids.map((id) => ({ id, size: null, params: null, context: null })),
});
const failed = (reason: LocalModels["reason"], error: string): LocalModels => ({
  ok: false, models: [], reason, error, missing: false, warning: null,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(settings());
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...settings(), ...(u as object) }, restart_required: [],
  }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
  vi.mocked(api.getAssistant).mockResolvedValue(structuredClone(info));
  vi.mocked(api.checkProvider).mockResolvedValue({ ok: true, error: null, provider: "openai-compatible" });
  vi.mocked(api.listLocalModels).mockResolvedValue(found("qwen3:8b", "gemma3:4b"));
});

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" />);
const picker = () => screen.findByRole("combobox", { name: "Модели на сервере" });

test("модели сервера ищутся сразу и выбираются из списка в поле имени", async () => {
  open();
  const select = await picker();
  expect(api.listLocalModels).toHaveBeenCalledWith(ep, URL1, null, false);
  expect(within(select).getByRole("option", { name: "qwen3:8b" })).toBeInTheDocument();
  expect(screen.getByText("Найдено моделей: 2")).toBeInTheDocument();
  await userEvent.selectOptions(select, "gemma3:4b");
  expect(screen.getByRole("textbox", { name: "Имя модели" })).toHaveValue("gemma3:4b");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { local_model: "gemma3:4b" } }));
});

test("имя можно вписать руками — список не мешает", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(settings("qwen3:8b"));
  open();
  const select = await picker();
  expect(select).toHaveValue("qwen3:8b");
  const name = screen.getByRole("textbox", { name: "Имя модели" });
  await userEvent.clear(name);
  await userEvent.type(name, "my-finetune");
  expect(select).toHaveValue("");
  // Вписанной нет на сервере — так и сказано.
  expect(screen.getByText("Модели «my-finetune» на сервере нет — выберите другую из списка")).toBeInTheDocument();
});

test("одна модель на сервере и имя не задано — выбирается сама", async () => {
  vi.mocked(api.listLocalModels).mockResolvedValue(found("qwen2.5-7b-instruct"));
  open();
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Имя модели" })).toHaveValue("qwen2.5-7b-instruct"));
});

test("одна модель, но имя уже задано — не трогаем", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(settings("llama3"));
  vi.mocked(api.listLocalModels).mockResolvedValue(found("qwen2.5-7b-instruct"));
  open();
  expect(await screen.findByText("Модели «llama3» на сервере нет — выберите другую из списка")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Имя модели" })).toHaveValue("llama3");
});

test("смена адреса — новый поиск по несохранённому адресу", async () => {
  open();
  await picker();
  const address = screen.getByLabelText("Адрес сервера");
  await userEvent.clear(address);
  await userEvent.type(address, "http://localhost:11434/v1");
  await waitFor(() => expect(api.listLocalModels).toHaveBeenLastCalledWith(ep, "http://localhost:11434/v1", null, false),
    { timeout: 3000 });
});

test("«Найти модели» спрашивает сразу", async () => {
  open();
  await picker();
  vi.mocked(api.listLocalModels).mockClear();
  await userEvent.click(screen.getByRole("button", { name: "Найти модели" }));
  expect(api.listLocalModels).toHaveBeenCalledTimes(1);
});

test.each([
  ["unreachable", "Сервер не отвечает: http://127.0.0.1:1234/v1 — запущены ли LM Studio (сервер включён) или Ollama?"],
  ["not_openai", "По адресу отвечает сервер, но не OpenAI-совместимый"],
  ["empty", "Сервер отвечает, но моделей нет"],
  ["auth", "Сервер требует ключ доступа (HTTP 401)"],
] as const)("ошибка поиска (%s) — текстом, без списка", async (reason, error) => {
  vi.mocked(api.listLocalModels).mockResolvedValue(failed(reason, error));
  open();
  expect(await screen.findByText(error)).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "Модели на сервере" })).toBeNull();
  // Поле имени остаётся — модель можно вписать.
  expect(screen.getByRole("textbox", { name: "Имя модели" })).toBeInTheDocument();
});

test("резидент без поиска моделей (404) — молча, без ошибки", async () => {
  vi.mocked(api.listLocalModels).mockRejectedValue(new api.ApiError(404, "нет такого"));
  open();
  await screen.findByRole("textbox", { name: "Имя модели" });
  await waitFor(() => expect(api.listLocalModels).toHaveBeenCalled());
  expect(screen.queryByText(/нет такого/)).toBeNull();
});

test("подпись модели: размер и параметры (Ollama), контекст (vLLM)", () => {
  expect(modelLabel({ id: "qwen3:8b", size: 5225388164, params: "8.2B" })).toBe("qwen3:8b — 4,9 ГБ · 8.2B");
  expect(modelLabel({ id: "Qwen/Qwen2.5-14B", context: 32768 })).toBe("Qwen/Qwen2.5-14B — контекст 32K");
  expect(modelLabel({ id: "m" })).toBe("m");
  expect(sizeText(734003200)).toBe("700 МБ");
});


test("Ollama: «llama3.2» — это «llama3.2:latest» из списка, а не пропавшая модель", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(settings("llama3.2"));
  vi.mocked(api.listLocalModels).mockResolvedValue(found("llama3.2:latest", "qwen3:8b"));
  open();
  const select = await picker();
  expect(select).toHaveValue("llama3.2:latest");
  expect(screen.queryByText(/на сервере нет/)).toBeNull();
  expect(sameModel("hf.co/org/repo", "hf.co/org/repo:latest")).toBe(true);
  expect(sameModel("qwen3:8b", "qwen3")).toBe(false);
});

test("«Локальную модель — через прокси»: по умолчанию выключено; включить — новый поиск через прокси и сохранение", async () => {
  open();
  await picker();
  const box = screen.getByRole("checkbox", { name: "Локальную модель — через прокси" });
  expect(box).not.toBeChecked();
  await userEvent.click(box);
  await waitFor(() => expect(api.listLocalModels).toHaveBeenLastCalledWith(ep, URL1, null, true), { timeout: 3000 });
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { llm: { local_via_proxy: true } }));
});

test("«Проверить» локальную модель — с окном контекста", async () => {
  vi.mocked(api.checkProvider).mockResolvedValue({
    ok: true, error: null, provider: "openai-compatible", detail: "окно контекста модели: 32768 токенов" });
  open();
  await picker();
  const local = screen.getByRole("group", { name: "Локальная (LM Studio / Ollama)" });
  await userEvent.click(within(local).getByRole("button", { name: "Проверить" }));
  expect(await within(local).findByText("работает; окно контекста модели: 32768 токенов")).toBeInTheDocument();
});


test("«через прокси» с SOCKS-адресом — понятное сообщение, сохранить нельзя", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({
    ...settings(), llm: { ...settings().llm, proxy: "socks5://127.0.0.1:1080" } });
  open();
  await picker();
  expect(screen.queryByRole("alert")).toBeNull();
  await userEvent.click(screen.getByRole("checkbox", { name: "Локальную модель — через прокси" }));
  expect(screen.getByRole("alert")).toHaveTextContent("SOCKS-прокси для локальной модели не поддерживается");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});
