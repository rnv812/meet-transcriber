import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import * as api from "../../lib/api";
import type { AssistantInfo } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getAssistant: vi.fn(),
  checkProvider: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  recording: { speaker_name: "Вы", auto_transcribe: true },
  ui: { notifications: "all" },
  llm: { provider: "auto", model: "sonnet", base_url: "http://127.0.0.1:1234/v1", local_model: null },
  assist: { vault: null, window_seconds: 20, port: 8765, voices: true },
  assistant: { knowledge_dir: "D:\\kb", notes_dir: null, notes_subdir: "Встречи" },
  agent: { launch: {
    "claude-code": { args: "", env: [] },
    codex: { args: "-m gpt-5", env: [{ key: "CODEX_HOME", value: "D:\\codex" }] },
  } },
};
const info: AssistantInfo = {
  provider: "claude-code", setting: "auto", checking: false, knowledge_dir: "D:\\kb",
  available: {
    "claude-code": { found: true, path: "C:\\bin\\claude.exe" },
    codex: { found: true, path: "C:\\bin\\codex.exe" },
    "openai-compatible": { found: false, base_url: "http://127.0.0.1:1234/v1" },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...structuredClone(settings), ...(u as object) }, restart_required: [],
  }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
  vi.mocked(api.getAssistant).mockResolvedValue(structuredClone(info));
});

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" />);
const group = (name: string) => screen.getByRole("group", { name });
const save = () => screen.getByRole("button", { name: "Сохранить" });

test("поля обоих агентов, строка «Команда запуска» и сохранение в agent.launch", async () => {
  open();
  const claude = await screen.findByRole("group", { name: "Запуск Claude Code" });
  expect(screen.getByText("Запуск агента (вкладка «Агент»)")).toBeInTheDocument();
  expect(within(group("Запуск Codex")).getByRole("textbox", { name: "Дополнительные параметры" })).toHaveValue("-m gpt-5");
  expect(within(group("Запуск Codex")).getByRole("textbox", { name: "Переменные окружения" })).toHaveValue("CODEX_HOME=D:\\codex");
  expect(within(claude).getByText(/^claude --session-id "<id сеанса>" --add-dir D:\\kb --append-system-prompt/))
    .toBeInTheDocument();
  // Вставкой, а не набором по букве: длинные строки под нагрузкой набираются долго.
  await userEvent.click(within(claude).getByRole("textbox", { name: "Дополнительные параметры" }));
  await userEvent.paste("--model opus");
  await userEvent.click(within(claude).getByRole("textbox", { name: "Переменные окружения" }));
  await userEvent.paste("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1");
  expect(within(claude).getByText(
    "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 claude --session-id \"<id сеанса>\" --add-dir D:\\kb "
    + "--append-system-prompt \"<подсказка о встрече>\" --model opus",
  )).toBeInTheDocument();
  await userEvent.click(save());
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { agent: { launch: {
    "claude-code": { args: "--model opus", env: [{ key: "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", value: "1" }] },
    codex: settings.agent.launch.codex,
    opencode: { args: "", env: [] },
  } } }));
});

test("незакрытая кавычка и негодная переменная — ошибка на месте, «Сохранить» недоступно", async () => {
  open();
  const claude = await screen.findByRole("group", { name: "Запуск Claude Code" });
  const args = within(claude).getByRole("textbox", { name: "Дополнительные параметры" });
  await userEvent.click(args);
  await userEvent.paste("--add-dir \"D:\\Docs");
  expect(within(claude).getByText(/^Незакрытая кавычка в параметрах запуска/)).toBeInTheDocument();
  expect(within(claude).queryByText(/^claude /)).toBeNull();
  expect(save()).toBeDisabled();
  await userEvent.type(args, "\"");
  expect(within(claude).queryByText(/Незакрытая кавычка/)).toBeNull();
  expect(save()).toBeEnabled();
  const env = within(claude).getByRole("textbox", { name: "Переменные окружения" });
  await userEvent.type(env, "1BAD=x");
  expect(within(claude).getByText(/^Строка 1: недопустимое имя «1BAD»/)).toBeInTheDocument();
  expect(env).toHaveValue("1BAD=x"); // набранное не теряется
  expect(save()).toBeDisabled();
});

test("переменные набираются построчно: Enter — новая строка, две переменные", async () => {
  open();
  const claude = await screen.findByRole("group", { name: "Запуск Claude Code" });
  const env = within(claude).getByRole("textbox", { name: "Переменные окружения" });
  await userEvent.type(env, "A=1{Enter}B=2");
  expect(env).toHaveValue("A=1\nB=2");
  await userEvent.type(env, "{Enter}  C=3");
  expect(env).toHaveValue("A=1\nB=2\n  C=3"); // пробелы в начале строки не съедаются
  await userEvent.click(save());
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { agent: { launch: {
    "claude-code": { args: "", env: [{ key: "A", value: "1" }, { key: "B", value: "2" }, { key: "C", value: "3" }] },
    codex: settings.agent.launch.codex,
    opencode: { args: "", env: [] },
  } } }));
});

test("вставка нескольких строк; ошибка — у каждой плохой строки, со своим номером", async () => {
  open();
  const claude = await screen.findByRole("group", { name: "Запуск Claude Code" });
  const env = within(claude).getByRole("textbox", { name: "Переменные окружения" });
  await userEvent.click(env);
  await userEvent.paste("A=1\nбез знака\nB=2\n1C=3");
  expect(env).toHaveValue("A=1\nбез знака\nB=2\n1C=3");
  expect(within(claude).getByText("Строка 2: нужен вид ИМЯ=значение")).toBeInTheDocument();
  expect(within(claude).getByText(/^Строка 4: недопустимое имя «1C»/)).toBeInTheDocument();
  expect(save()).toBeDisabled();
  await userEvent.clear(env);
  await userEvent.paste("A=1\nB=2");
  expect(within(claude).queryByText(/^Строка/)).toBeNull();
  expect(save()).toBeEnabled();
});

test("«Сбросить» очищает параметры одного агента", async () => {
  open();
  const codex = await screen.findByRole("group", { name: "Запуск Codex" });
  await userEvent.click(within(codex).getByRole("button", { name: "Параметры Codex по умолчанию" }));
  expect(within(codex).getByRole("textbox", { name: "Дополнительные параметры" })).toHaveValue("");
  expect(within(codex).getByRole("textbox", { name: "Переменные окружения" })).toHaveValue("");
  expect(within(codex).getByRole("button", { name: "Параметры Codex по умолчанию" })).toBeDisabled();
  await userEvent.click(save());
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { agent: { launch: {
    "claude-code": { args: "", env: [] }, codex: { args: "", env: [] }, opencode: { args: "", env: [] },
  } } }));
});

test("подсказка: примеры параметров и что фоновые задачи их не получают", async () => {
  open();
  const claude = await screen.findByRole("group", { name: "Запуск Claude Code" });
  await userEvent.click(within(claude).getByRole("button", { name: "Какие параметры можно задать для Claude Code" }));
  expect(await screen.findByText(/Для фоновых задач используется модель из настройки «Модель Claude Code»/))
    .toBeInTheDocument();
  expect(screen.getByText("--permission-mode acceptEdits")).toBeInTheDocument();
});

test("OpenCode: свои поля, «Команда запуска» без наших флагов, сохранение в agent.launch.opencode", async () => {
  open();
  const oc = await screen.findByRole("group", { name: "Запуск OpenCode" });
  expect(within(oc).getByText(/^opencode$/)).toBeInTheDocument();
  await userEvent.click(within(oc).getByRole("textbox", { name: "Дополнительные параметры" }));
  await userEvent.paste("-m openai/gpt-5");
  expect(within(oc).getByText("opencode -m openai/gpt-5")).toBeInTheDocument();
  await userEvent.click(save());
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { agent: { launch: {
    "claude-code": { args: "", env: [] },
    codex: settings.agent.launch.codex,
    opencode: { args: "-m openai/gpt-5", env: [] },
  } } }));
});

test("подсказка OpenCode: как приходят подсказка о встрече и база знаний", async () => {
  open();
  const oc = await screen.findByRole("group", { name: "Запуск OpenCode" });
  await userEvent.click(within(oc).getByRole("button", { name: "Какие параметры можно задать для OpenCode" }));
  expect(await screen.findByText(/Для фоновых задач используется «Модель OpenCode»/)).toBeInTheDocument();
  expect(screen.getAllByText("OPENCODE_CONFIG_CONTENT").length).toBeGreaterThan(0);
});
