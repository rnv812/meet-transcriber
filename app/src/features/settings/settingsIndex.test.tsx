import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MENU, SETTINGS_INDEX, searchSettings, type SectionId } from "./settingsIndex";
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
  getDiagnostics: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getHfStatus: vi.fn(),
  listLocalModels: vi.fn(),
  getStorage: vi.fn(),
  getCategoriesInfo: vi.fn(),
  getExportPreview: vi.fn(),
}));

const ep = { base: "/api", token: null };

/** Всё включено: видны строки, которые зависят от переключателей. */
const full = {
  auto_record: { enabled: true, processes: ["zoom.exe"], browsers: ["chrome.exe"], call_sites: ["Google Meet"], grace_minutes: 10, min_call_seconds: 60 },
  hooks: { post_record: true, command: ["x"], prompt: "", recurring_window: null },
  recording: { out_dir: null, speaker_name: "Вы", auto_transcribe: true },
  asr: { backend: "faster-whisper", model: "large-v3", cpu_model: "small", device: "auto", language: "ru", align: true, overlap: true },
  llm: {
    provider: "claude-code", enabled: ["claude-code", "codex", "opencode", "openai-compatible"],
    base_url: "http://127.0.0.1:1234/v1", model: "sonnet",
  },
  assist: { participant: true },
  assistant: { knowledge_dir: "D:\\kb", auto_title: true },
  integrations: { gpu_marker: true, gpu_marker_path: null, jira_base_url: "https://jira.example.com", jira_projects: [{ key: "SPR", aliases: [] }] },
  transcript_view: { jira: true },
  export: { meetings_dir: "D:\\kb\\Встречи" },
  ui: { notifications: "all", wizard_done: true },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(full));
  vi.mocked(api.getHotwords).mockResolvedValue({ text: "", budget: 400, used: 0 });
  vi.mocked(api.getEngine).mockResolvedValue({
    installed: true, missing: [], flavor: "cuda", download_gb: 3, python: "3.12", target: "C:\\meet\\engine",
    ffmpeg: true, gpu: { available: true, name: "RTX" }, components: [{ module: "torch", title: "PyTorch", installed: true }],
  });
  vi.mocked(api.getModels).mockResolvedValue({
    items: [], cache: "C:\\hf", token: true, selected: null, can_download: true, can_download_gigaam: false,
    gigaam_cache: "C:\\gigaam", gigaam_install_error: null,
  });
  vi.mocked(api.getJobs).mockResolvedValue({ items: [] });
  vi.mocked(api.getDiagnostics).mockResolvedValue({
    paths: { data_dir: "C:\\data", recordings: "C:\\rec", config: "C:\\data\\config.json", watch_log: "C:\\data\\watch.log" },
  });
  vi.mocked(api.getDevices).mockResolvedValue({
    available: true, pinning: true, system: { name: "Динамики", rate: 48000 }, mic: { name: "Микрофон", rate: 48000 },
  });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: true, running: [] });
  vi.mocked(api.getHfStatus).mockResolvedValue({ configured: true, source: "keyring", check: { ok: true, reason: "ok", message: "" } });
  vi.mocked(api.getOwnerVoice).mockResolvedValue({
    samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25,
  });
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "claude-code", checking: false, knowledge_dir: "D:\\kb",
    available: { "claude-code": { found: true, path: "claude" } },
  });
  vi.mocked(api.listLocalModels).mockResolvedValue({ ok: true, models: [], error: null } as never);
  vi.mocked(api.getStorage).mockResolvedValue({
    root: null, custom: false, home: "C:\\meet", engine_dir: "C:\\meet\\engine", models_dir: "C:\\meet\\models",
    hf_cache: "C:\\hf", shared_cache: "C:\\hf",
  } as api.StorageInfo);
  vi.mocked(api.getCategoriesInfo).mockResolvedValue({ categories: [], defaults: [], counts: {}, none: 0 });
  vi.mocked(api.getExportPreview).mockResolvedValue({ folder: "x", files: [], error: null });
});

/** Подписи строк в открытом разделе: `Row`, `Switch`, `Radio` и составные строки (`.srow__label`). */
function shownLabels(): string[] {
  return [...document.querySelectorAll(".settings__content .srow__label")]
    // Строки каталога моделей — данные резидента, а не настройки.
    .filter((el) => !el.closest(".model-row"))
    .map((el) => (el.textContent ?? "").trim())
    .filter(Boolean);
}

const flush = () => act(() => new Promise((r) => setTimeout(r, 30)));

/** Обойти все разделы, раскрыв «Тонкую настройку»: раздел → подписи. */
async function collect(): Promise<Map<SectionId, Set<string>>> {
  const out = new Map<SectionId, Set<string>>();
  const view = render(<SettingsPane endpoint={ep} recordingsDir="D:\\rec" onRunWizard={() => {}} />);
  await screen.findByRole("radiogroup", { name: "Уведомления" });
  for (const { id, title } of MENU) {
    await userEvent.click(screen.getByRole("button", { name: title }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(title));
    await flush();
    for (const fine of screen.queryAllByRole("button", { name: "Тонкая настройка" })) {
      if (fine.getAttribute("aria-expanded") === "false") await userEvent.click(fine);
    }
    await flush();
    out.set(id, new Set(shownLabels()));
  }
  view.unmount();
  return out;
}

const indexed = (section: SectionId, label: string) =>
  SETTINGS_INDEX.some((e) => e.section === section && e.label === label);

test("каждая подпись строки в каждом разделе есть в индексе поиска (всё включено и участник выключен)", async () => {
  const missing: string[] = [];
  const check = (seen: Map<SectionId, Set<string>>) => {
    for (const [section, labels] of seen) {
      for (const label of labels) if (!indexed(section, label)) missing.push(`${section} › ${label}`);
    }
  };
  const first = await collect();
  check(first);
  // Участник выключен — видны прежние подсказки; ни одна модель не включена.
  vi.mocked(api.getSettings).mockResolvedValue({
    ...structuredClone(full), assist: { participant: false }, llm: { provider: "auto", enabled: [] },
  });
  const second = await collect();
  check(second);
  expect([...new Set(missing)]).toEqual([]);
  // Обход действительно что-то видел: строки из «Тонкой настройки» и составные.
  expect(first.get("asr")).toContain("Уточнять время каждого слова");
  expect(first.get("auto")).toContain("Сайты звонков");
  expect(second.get("assistant")).toContain("Активность подсказок");
  expect(first.get("analysis")).toContain("Главы");
}, 60_000);

test("индекс: известные разделы, без повторов; «Тонкая настройка» помечена advanced", () => {
  const ids = new Set(MENU.map((m) => m.id));
  const keys = SETTINGS_INDEX.map((e) => `${e.section}›${e.label}`);
  expect(SETTINGS_INDEX.filter((e) => !ids.has(e.section))).toEqual([]);
  expect(keys.filter((k, i) => keys.indexOf(k) !== i)).toEqual([]);
  const advanced = SETTINGS_INDEX.filter((e) => e.advanced).map((e) => `${e.section} › ${e.label}`).sort();
  expect(advanced).toEqual([
    "advanced › Путь к файлу-маркеру",
    "assistant › Активность подсказок",
    "assistant › Модель для живых подсказок",
    "assistant › Не отвлекать по умолчанию",
    "assistant › Окно живой расшифровки, с",
    "assistant › Сколько подсказок держать",
    "asr › Уточнять время каждого слова",
    "auto › Сайты звонков",
    "auto › Только если в заголовке окна сайт звонка",
    "jira › Шаблон ключа для текста",
    "models › Локальную модель — через прокси",
  ].sort());
});

test("поиск: по подписи, пояснению и ключевым словам; регистр и «ё» не важны; пусто — ничего", () => {
  expect(searchSettings("")).toEqual([]);
  expect(searchSettings("   ")).toEqual([]);
  const labels = (q: string) => searchSettings(q).map((e) => `${e.section} › ${e.label}`);
  expect(labels("прокси")).toEqual(expect.arrayContaining([
    "models › Прокси для подключения к моделям", "models › Локальную модель — через прокси",
  ]));
  // Начало подписи — выше совпадения в середине.
  expect(labels("прокси")[0]).toBe("models › Прокси для подключения к моделям");
  expect(labels("ТОКЕН")).toContain("speakers › Токен Hugging Face");
  expect(labels("hf")).toContain("speakers › Токен Hugging Face");
  expect(labels("нахлест")).toEqual(["speakers › Отмечать одновременную речь"]);
  expect(labels("нахлёст")).toEqual(labels("нахлест"));
  // Несколько слов — все должны найтись.
  expect(labels("время слова")).toEqual(["asr › Уточнять время каждого слова"]);
  expect(labels("нет такой настройки")).toEqual([]);
});
