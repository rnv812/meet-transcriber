import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import { folderTemplateError } from "./ExportSection";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getExportPreview: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  pickFolder: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  recording: { speaker_name: "Вы", auto_transcribe: true },
  ui: { notifications: "all" },
  export: {
    meetings_dir: null, folder_template: "{date} - {title}",
    transcript_name: "Транскрипт.md", summary_name: "Итоги.md",
    include_transcript: true, include_summary: true, include_audio: false, include_srt: false,
    auto_export: true,
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
  vi.mocked(api.getExportPreview).mockImplementation(async (_e, q) => ({
    folder: String(q.folder_template).replace("{date}", "2026-09-30").replace("{year}", "2026")
      .replace("{title}", "Планирование спринта"),
    files: ["Транскрипт.md", "Итоги.md"],
    error: null,
  }));
  vi.mocked(shell.pickFolder).mockResolvedValue(null);
});

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="export" />);
const save = () => userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

test("раздел «Экспорт встреч» есть в меню и открывается по initial", async () => {
  open();
  expect(screen.getByRole("button", { name: "Экспорт встреч" })).toHaveAttribute("aria-current", "page");
  expect(await screen.findByRole("group", { name: "Папка для встреч" })).toBeInTheDocument();
});

test("папка для встреч: «Выбрать папку…» и «Очистить»", async () => {
  vi.mocked(shell.pickFolder).mockResolvedValue("E:\\Vault\\Встречи");
  open();
  const row = await screen.findByRole("group", { name: "Папка для встреч" });
  expect(within(row).getByText("не задана")).toBeInTheDocument();
  await userEvent.click(within(row).getByRole("button", { name: "Выбрать папку…" }));
  expect(await within(row).findByText("E:\\Vault\\Встречи")).toBeInTheDocument();
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    export: { meetings_dir: "E:\\Vault\\Встречи" },
  }));
});

test("шаблон папки: живой пример по /export/preview", async () => {
  open();
  expect(await screen.findByText("Пример: 2026-09-30 - Планирование спринта")).toBeInTheDocument();
  expect(screen.getByText("В папке: Транскрипт.md, Итоги.md")).toBeInTheDocument();
  const input = screen.getByLabelText("Шаблон папки");
  await userEvent.clear(input);
  await userEvent.type(input, "{{year}/{{date} - {{title}");
  expect(await screen.findByText("Пример: 2026/2026-09-30 - Планирование спринта")).toBeInTheDocument();
  expect(api.getExportPreview).toHaveBeenLastCalledWith(ep, expect.objectContaining({
    folder_template: "{year}/{date} - {title}",
  }));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    export: { folder_template: "{year}/{date} - {title}" },
  }));
});

test.each([
  ["../{{title}", "В шаблоне папки нельзя использовать «..» и «.»"],
  ["/{{title}", "Шаблон папки должен быть относительным — без «/» в начале"],
  ["{{date} - {{name}", "Неизвестная подстановка {name}"],
])("шаблон «%s» — объяснение и «Сохранить» недоступно", async (typed, text) => {
  open();
  const input = await screen.findByLabelText("Шаблон папки");
  await userEvent.clear(input);
  await userEvent.type(input, typed);
  expect(screen.getByText(new RegExp(text.replace(/[{}.*?()]/g, "\\$&")))).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

test("«?» у шаблона перечисляет подстановки", async () => {
  open();
  const help = await screen.findByRole("button", { name: "Подстановки в шаблоне" });
  await userEvent.click(help);
  const tip = screen.getByRole("tooltip");
  for (const token of ["{date}", "{time}", "{year}", "{month}", "{day}", "{title}"]) {
    expect(within(tip).getByText(token)).toBeInTheDocument();
  }
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("имена файлов, состав и автоматика уходят в export", async () => {
  open();
  const name = await screen.findByLabelText("Имя файла транскрипта");
  await userEvent.clear(name);
  await userEvent.type(name, "{{date} Транскрипт.md");
  await userEvent.click(screen.getByRole("checkbox", { name: "Аудиозапись" }));
  await userEvent.click(screen.getByRole("checkbox", { name: "Итоги встречи" }));
  await userEvent.click(screen.getByRole("switch", { name: "Выгружать автоматически после расшифровки" }));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    export: {
      transcript_name: "{date} Транскрипт.md", include_audio: true, include_summary: false,
      auto_export: false,
    },
  }));
});

test("ошибка проверки от резидента показывается вместо примера", async () => {
  vi.mocked(api.getExportPreview).mockResolvedValue({ folder: null, files: [], error: "Имя файла не может быть пустым" });
  open();
  expect(await screen.findByText("Имя файла не может быть пустым")).toBeInTheDocument();
});

test("проверка шаблона в окне совпадает с резидентом", () => {
  expect(folderTemplateError("{date} - {title}")).toBeNull();
  expect(folderTemplateError("{year}/{date} - {title}")).toBeNull();
  expect(folderTemplateError("")).toBe("Шаблон папки не может быть пустым");
  expect(folderTemplateError("C:/{title}")).toBe("Шаблон папки не может содержать букву диска");
  expect(folderTemplateError("{year}//{title}")).toBe("В шаблоне папки есть пустая часть пути — уберите лишнюю «/»");
});

test("одинаковые имена файлов — объяснение и «Сохранить» недоступно", async () => {
  open();
  const name = await screen.findByLabelText("Имя файла итогов");
  await userEvent.clear(name);
  await userEvent.type(name, "Транскрипт");
  expect(screen.getByText("Имена файлов транскрипта и итогов совпадают — задайте разные")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

test("подсказка автоматики говорит, что ручные правки не перезаписываются", async () => {
  open();
  expect(await screen.findByText(/Файлы, которые вы изменили вручную, не перезаписываются/)).toBeInTheDocument();
});
