import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import {
  AGENT_MODE_HINTS, AGENT_MODE_LABEL, FREEDOM_ACT_NOTE, FREEDOM_FILES_NOTE, FREEDOM_LABEL, FREEDOM_OFF_HINT, KB_MAP_LABEL, PARTICIPANT_LABEL, PROFILE_DEFAULT_LABEL,
  VISION_NOTE,
} from "./ParticipantRows";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { AssistantInfo } from "../../lib/types";

/** Настройки агента-участника в разделе «Ассистент» (0.3.6). */

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
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  pickFolder: vi.fn(),
  openUrl: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
type Patch = Record<string, Record<string, unknown>>;
const settings: Patch = {
  recording: { speaker_name: "Вы" },
  ui: { notifications: "all" },
  llm: { provider: "auto", model: "sonnet", base_url: "http://127.0.0.1:1234/v1", enabled: ["claude-code", "codex"] },
  assist: { window_seconds: 20, activity: "calm", kb_map: true, kb_exclude: ["Личное/", ".trash/"], frequency: "more" },
  assistant: { knowledge_dir: "D:\\KB", notes_dir: null },
};
const info = (provider = "claude-code"): AssistantInfo => ({
  provider, setting: "auto", checking: false, knowledge_dir: "D:\\KB",
  available: { "claude-code": { found: true, path: "C:\\claude.exe" }, codex: { found: true, path: "C:\\codex.exe" } },
} as AssistantInfo);
const merge = (base: Patch, u: Patch): Patch => {
  const out = structuredClone(base);
  for (const [g, v] of Object.entries(u)) out[g] = { ...(out[g] ?? {}), ...v };
  return out;
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({ settings: merge(settings, u as Patch), restart_required: [] }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
  vi.mocked(api.getAssistant).mockResolvedValue(info());
  vi.mocked(shell.pickFolder).mockResolvedValue(null);
});

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="assistant" />);
const save = () => userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

test("участник включён (по умолчанию): частота, структура базы, исключения; прежних подсказок нет", async () => {
  open();
  expect(await screen.findByRole("switch", { name: PARTICIPANT_LABEL })).toHaveAttribute("aria-checked", "true");
  const freq = screen.getByRole("radiogroup", { name: "Как часто писать" });
  expect(within(freq).getByRole("radio", { name: "чаще" })).toBeChecked();
  expect(screen.getByRole("switch", { name: KB_MAP_LABEL })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("list", { name: "Исключённые папки базы знаний" })).toHaveTextContent("Личное/");
  expect(screen.getByText(VISION_NOTE)).toBeInTheDocument();
  // Прежние подсказки и их ритм скрыты; «Не отвлекать по умолчанию» — общее, в «Тонкой настройке».
  await userEvent.click(screen.getByRole("button", { name: "Тонкая настройка" }));
  expect(screen.queryByRole("radiogroup", { name: "Активность подсказок" })).toBeNull();
  expect(screen.queryByRole("radiogroup", { name: "Модель для живых подсказок" })).toBeNull();
  expect(screen.queryByLabelText("Сколько подсказок держать")).toBeNull();
  expect(screen.getByRole("switch", { name: "Не отвлекать по умолчанию" })).toBeInTheDocument();
  await userEvent.click(within(freq).getByRole("radio", { name: "реже" }));
  await userEvent.click(screen.getByRole("switch", { name: KB_MAP_LABEL }));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { assist: { frequency: "less", kb_map: false } }));
});

test("«Профиль по умолчанию»: рабочая встреча, если не задан; личный сохраняется ключом", async () => {
  open();
  const group = await screen.findByRole("radiogroup", { name: PROFILE_DEFAULT_LABEL });
  // Короткое перечисление — сегменты Aurora (.tabs--sm), как «Профиль» в панели встречи.
  expect(group).toHaveClass("tabs", "tabs--sm");
  expect(within(group).getAllByRole("radio").map((r) => r.textContent)).toEqual(["Рабочая встреча", "Личный"]);
  expect(within(group).getByRole("radio", { name: "Рабочая встреча" })).toBeChecked();
  expect(screen.getByText(/база знаний, прошлые встречи, подсказки по встрече/)).toBeInTheDocument();
  await userEvent.click(within(group).getByRole("radio", { name: "Личный" }));
  expect(screen.getByText(/без базы знаний и рабочих советов/)).toBeInTheDocument();
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { assist: { profile: "personal" } }));
});

test("участник выключен: прежние подсказки видны (запасной режим), настроек участника нет", async () => {
  open();
  await userEvent.click(await screen.findByRole("switch", { name: PARTICIPANT_LABEL }));
  await userEvent.click(screen.getByRole("button", { name: "Тонкая настройка" }));
  expect(screen.getByRole("radiogroup", { name: "Активность подсказок" })).toBeInTheDocument();
  expect(screen.getByLabelText("Сколько подсказок держать")).toBeInTheDocument();
  expect(screen.queryByRole("radiogroup", { name: "Как часто писать" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Исключённые папки" })).toBeNull();
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { assist: { participant: false } }));
});

test("исключения: папка из диалога — путь внутри базы; вне базы — отказ; вручную; убрать; по умолчанию", async () => {
  open();
  const editor = await screen.findByRole("group", { name: "Исключённые папки" });
  vi.mocked(shell.pickFolder).mockResolvedValueOnce("D:\\KB\\Проекты\\Секрет");
  await userEvent.click(within(editor).getByRole("button", { name: "Выбрать папку…" }));
  expect(shell.pickFolder).toHaveBeenCalledWith("D:\\KB");
  expect(within(editor).getByRole("list")).toHaveTextContent("Проекты/Секрет/");
  vi.mocked(shell.pickFolder).mockResolvedValueOnce("C:\\Users\\me\\Desktop");
  await userEvent.click(within(editor).getByRole("button", { name: "Выбрать папку…" }));
  expect(within(editor).getByRole("alert")).toHaveTextContent("вне базы знаний");
  await userEvent.type(within(editor).getByRole("textbox", { name: "Папка внутри базы знаний" }), "..\\вне{Enter}");
  expect(within(editor).getByRole("alert")).toHaveTextContent("без буквы диска");
  await userEvent.clear(within(editor).getByRole("textbox", { name: "Папка внутри базы знаний" }));
  await userEvent.type(within(editor).getByRole("textbox", { name: "Папка внутри базы знаний" }), "Архив\\2024{Enter}");
  await userEvent.click(within(editor).getByRole("button", { name: "Убрать исключение .trash/" }));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assist: { kb_exclude: ["Личное/", "Проекты/Секрет/", "Архив/2024/"] },
  }));
  await userEvent.click(within(editor).getByRole("button", { name: "По умолчанию" }));
  expect(within(editor).getByRole("list")).toHaveTextContent("Личное/.trash/");
});

test("без базы знаний — выбрать папку нельзя, видно почему", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { assistant: { knowledge_dir: null } }));
  open();
  const editor = await screen.findByRole("group", { name: "Исключённые папки" });
  expect(within(editor).getByRole("button", { name: "Выбрать папку…" })).toBeDisabled();
  expect(screen.getByText(/Сначала задайте базу знаний выше/)).toBeInTheDocument();
});

test("модель по умолчанию — Codex: «исключения — только просьба» и без пометки о картинках", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(info("codex"));
  open();
  expect(await screen.findByText("Codex: исключения — только просьба")).toBeInTheDocument();
  expect(screen.getByText(VISION_NOTE)).toBeInTheDocument();
});

test("Claude Code — пометки «только просьба» нет", async () => {
  open();
  await screen.findByRole("group", { name: "Исключённые папки" });
  expect(screen.queryByText(/исключения — только просьба/)).toBeNull();
});

test("локальная модель по умолчанию — пометка, что она не видит картинки", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(info("openai-compatible"));
  open();
  expect(await screen.findByText(`${VISION_NOTE}. Локальная модель картинки не видит`)).toBeInTheDocument();
});

test("осталась «Только сводка» прежнего ассистента — предупреждение и «Вернуть чат»", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { assist: { activity: "summary" } }));
  open();
  expect(await screen.findByText(/Выбрано «Только сводка»/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Вернуть чат" }));
  expect(screen.queryByText(/Выбрано «Только сводка»/)).toBeNull();
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { assist: { activity: "calm" } }));
});

test("без базы знаний вписать исключение тоже нельзя (ревью M10)", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(merge(settings, { assistant: { knowledge_dir: null } }));
  open();
  const editor = await screen.findByRole("group", { name: "Исключённые папки" });
  expect(within(editor).getByRole("textbox", { name: "Папка внутри базы знаний" })).toBeDisabled();
  expect(within(editor).getByRole("button", { name: "Добавить" })).toBeDisabled();
  expect(PARTICIPANT_LABEL).toBe("Ассистент — участник встречи");
});

test("расширенные возможности по согласию: вкл. по умолчанию, честная подсказка, выключение сохраняется (0.3.7)", async () => {
  open();
  const sw = await screen.findByRole("switch", { name: FREEDOM_LABEL });
  expect(sw).toHaveAttribute("aria-checked", "true");
  const row = sw.closest(".srow")!;
  // Под переключателем — одна строка; подробности и оговорки — в «?».
  expect(row).toHaveTextContent("с вашими правами");
  expect(row).toHaveTextContent("файлы, команды, веб");
  expect(row).toHaveTextContent("Без вашей просьбы — только чтение этой встречи и вложений");
  await userEvent.click(within(row as HTMLElement).getByRole("button", { name: "Что дают расширенные возможности" }));
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("«Разрешить один раз»");
  expect(tip).toHaveTextContent("MCP-инструменты, чьё имя не похоже на запись, выполняются без вопроса");
  expect(tip).toHaveTextContent("запускают хуки самого репозитория");
  await userEvent.keyboard("{Escape}");
  await userEvent.click(sw);
  expect(sw).toHaveAttribute("aria-checked", "false");
  expect(sw.closest(".srow")!).toHaveTextContent(FREEDOM_OFF_HINT);
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { assist: { agent_freedom: false } }));
});

test("0.4: «Действия ассистента» — «Действует сам» по умолчанию, «Спрашивает каждое» сохраняется; без возможностей строки нет", async () => {
  open();
  const group = await screen.findByRole("radiogroup", { name: AGENT_MODE_LABEL });
  const auto = within(group).getByRole("radio", { name: "Действует сам" });
  expect(auto).toHaveAttribute("aria-checked", "true");
  const row = group.closest(".srow")!;
  expect(row).toHaveTextContent(AGENT_MODE_HINTS.auto);
  expect(row).toHaveTextContent("Ход по одной речи встречи ничего не меняет");
  await userEvent.click(within(group).getByRole("radio", { name: "Спрашивает каждое" }));
  expect(screen.getByRole("radiogroup", { name: AGENT_MODE_LABEL }).closest(".srow")!).toHaveTextContent(AGENT_MODE_HINTS.confirm);
  await userEvent.click(screen.getByRole("switch", { name: FREEDOM_LABEL }));
  expect(screen.queryByRole("radiogroup", { name: AGENT_MODE_LABEL })).toBeNull();
  await userEvent.click(screen.getByRole("switch", { name: FREEDOM_LABEL }));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    assist: expect.objectContaining({ agent_mode: "confirm" }) }));
});

test("расширенные возможности у Codex: в автомоде правит и выполняет по просьбе; с подтверждением — только чтение", async () => {
  vi.mocked(api.getAssistant).mockResolvedValue(info("codex"));
  open();
  const sw = await screen.findByRole("switch", { name: FREEDOM_LABEL });
  await waitFor(() => expect(sw.closest(".srow")!).toHaveTextContent(FREEDOM_ACT_NOTE));
  await userEvent.click(screen.getByRole("radio", { name: /Спрашивает каждое/ }));
  await waitFor(() => expect(sw.closest(".srow")!).toHaveTextContent(FREEDOM_FILES_NOTE));
});
