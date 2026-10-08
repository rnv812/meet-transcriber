import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { LEGACY_SECTION, SettingsPane, sectionsOf } from "./SettingsPane";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getOwnerVoice: vi.fn(),
  getAssistant: vi.fn(),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  setAutoRecord: vi.fn(),
  getHotwords: vi.fn(),
  putHotwords: vi.fn(),
  getEngine: vi.fn(),
  getModels: vi.fn(),
  removeModel: vi.fn(),
  getJobs: vi.fn(),
  getDiagnostics: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getHfStatus: vi.fn(),
  setHfToken: vi.fn(),
  deleteHfToken: vi.fn(),
  recheckHf: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  auto_record: { enabled: true, processes: ["zoom.exe"], grace_minutes: 10, min_call_seconds: 60 },
  hooks: { post_record: false, command: [], prompt: "", recurring_window: null },
  recording: { out_dir: null, voices_dir: null, speaker_name: "Вы", auto_transcribe: true },
  asr: { backend: "faster-whisper", model: "large-v3", cpu_model: "small", device: "auto", language: "ru", align: true, overlap: true },
  integrations: { gpu_marker: false, gpu_marker_path: null },
  ui: { notifications: "all", wizard_done: true },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...structuredClone(settings), ...u }, restart_required: [],
  }));
  vi.mocked(api.setAutoRecord).mockResolvedValue({} as never);
  vi.mocked(api.getHotwords).mockResolvedValue({ text: "", budget: 400, used: 0 });
  vi.mocked(api.getEngine).mockRejectedValue(new Error("x"));
  vi.mocked(api.getModels).mockRejectedValue(new Error("x"));
  vi.mocked(api.getJobs).mockResolvedValue({ items: [] });
  vi.mocked(api.getDiagnostics).mockResolvedValue({ paths: { data_dir: "C:\data\meet" } });
  vi.mocked(api.getDevices).mockResolvedValue({ available: true, pinning: false, system: { name: "Динамики", rate: 48000 }, mic: { name: "Микрофон", rate: 48000 } });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: true, running: ["zoom.exe", "teams.exe"] });
  vi.mocked(api.getHfStatus).mockResolvedValue({
    configured: true, source: "keyring", check: { ok: true, reason: "ok", message: "Доступ есть" },
  });
  vi.mocked(api.deleteHfToken).mockResolvedValue({ configured: false, source: null, check: null });
  vi.mocked(api.getOwnerVoice).mockResolvedValue({
    samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25,
  });
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "auto", checking: false, knowledge_dir: null,
    available: { "claude-code": { found: true, path: "claude" } },
  });
});

test("переключатель автозаписи сразу вызывает setAutoRecord, без «Сохранить»", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir="C:\rec" />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  const sw = await screen.findByRole("switch", { name: /Записывать звонки автоматически/ });
  expect(sw).toBeChecked();
  await userEvent.click(sw);
  await waitFor(() => expect(api.setAutoRecord).toHaveBeenCalledWith(ep, false));
  expect(api.patchSettings).not.toHaveBeenCalled();
});

test("«Ждать повторного подключения» — ползунок 1–60 минут, сохраняется патчем секции, предупреждает о перезапуске", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  expect(screen.getByText("Параметры ниже применяются после перезапуска приложения.")).toBeInTheDocument();
  const wait = await screen.findByRole("slider", { name: "Ждать повторного подключения" });
  expect(wait).toHaveValue("10");
  expect(wait).toHaveAttribute("min", "1");
  expect(wait).toHaveAttribute("max", "60");
  expect(wait).toHaveAttribute("aria-valuetext", "10 мин");
  fireEvent.change(wait, { target: { value: "15" } });
  expect(screen.getByText("15 мин")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1]).toEqual({ auto_record: { grace_minutes: 15 } });
});

test("ожидание повторного подключения: у ползунка есть «?»", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  await screen.findByRole("slider", { name: "Ждать повторного подключения" });
  const tip = screen.getByRole("button", { name: "Что такое ожидание повторного подключения" });
  await userEvent.click(tip);
  expect(tip).toHaveAccessibleDescription(
    /Если вы вышли из звонка и вернулись в течение этого времени, запись продолжится в тот же файл/);
});

test("уведомления: радио «Только важные» сохраняется в ui.notifications", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir="C:\rec" />);
  await userEvent.click(await screen.findByRole("radio", { name: "Только важные" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  const call = vi.mocked(api.patchSettings).mock.calls[0]?.[1] as { ui: { notifications: string } };
  expect(call.ui.notifications).toBe("important");
});

test("«О программе» показывает путь к данным", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "О программе" }));
  expect(await screen.findByText("C:\data\meet")).toBeInTheDocument();
  // Вне оболочки страницы выпусков нет — копируется только путь к данным.
  expect(screen.getAllByRole("button", { name: "Копировать" })).toHaveLength(1);
});

test("«Открыть» папку записей — только в оболочке: в браузере открыть нечем", async () => {
  const { unmount } = render(<SettingsPane endpoint={ep} recordingsDir="D:/rec" />);
  expect(await screen.findByText("D:/rec")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Открыть" })).toBeNull();
  unmount();
  (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = {};
  try {
    render(<SettingsPane endpoint={ep} recordingsDir="D:/rec" />);
    expect(await screen.findByRole("button", { name: "Открыть" })).toBeInTheDocument();
  } finally {
    delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  }
});

test("ошибка сохранения — текстом резидента, без «Error:»", async () => {
  vi.mocked(api.patchSettings).mockRejectedValue(new api.ApiError(400, "неизвестный ключ"));
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  fireEvent.change(await screen.findByRole("slider", { name: "Ждать повторного подключения" }), { target: { value: "45" } });
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByText("неизвестный ключ")).toBeInTheDocument();
  expect(screen.queryByText(/Error:/)).toBeNull();
});

const openEngine = async (props: { onRunWizard?: () => void } = {}) => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} {...props} />);
  await userEvent.click(await screen.findByRole("button", { name: "Движок и модели" }));
};
const openSpeakers = async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Спикеры" }));
};

test("Hugging Face: статус из /hf/status, значение токена не показывается", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(
    { ...structuredClone(settings), integrations: { gpu_marker: false, gpu_marker_path: null, hf_token: "hf_secret" } });
  await openSpeakers();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  expect(await within(row).findByText(/сохранён в диспетчере учётных данных Windows/)).toBeInTheDocument();
  expect(within(row).getByText("доступ есть")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("hf_secret")).toBeNull();
  expect(document.body).not.toHaveTextContent("hf_secret");
  expect(within(row).queryByLabelText("Новый токен")).toBeNull();
});

test("«Изменить токен» → «Проверить и сохранить»: успех перечитывает статус", async () => {
  vi.mocked(api.setHfToken).mockResolvedValue({ ok: true, reason: "ok", message: "Доступ есть" });
  await openSpeakers();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  await userEvent.click(await within(row).findByRole("button", { name: "Изменить токен" }));
  await userEvent.type(within(row).getByLabelText("Новый токен"), "hf_new");
  const status = vi.mocked(api.getHfStatus).mock.calls.length;
  await userEvent.click(within(row).getByRole("button", { name: "Проверить и сохранить" }));
  expect(api.setHfToken).toHaveBeenCalledWith(ep, "hf_new");
  await waitFor(() => expect(vi.mocked(api.getHfStatus).mock.calls.length).toBeGreaterThan(status));
  expect(within(row).queryByLabelText("Новый токен")).toBeNull();
});

test("неверный токен — «Неверный токен», поле остаётся для исправления", async () => {
  vi.mocked(api.setHfToken).mockResolvedValue({ ok: false, reason: "invalid_token", message: "Неверный токен" });
  await openSpeakers();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  await userEvent.click(await within(row).findByRole("button", { name: "Изменить токен" }));
  await userEvent.type(within(row).getByLabelText("Новый токен"), "hf_bad");
  await userEvent.click(within(row).getByRole("button", { name: "Проверить и сохранить" }));
  expect(await within(row).findByRole("alert")).toHaveTextContent("Неверный токен");
  expect(within(row).getByLabelText("Новый токен")).toBeInTheDocument();
});

test("«Удалить токен» — DELETE /hf/token, статус «не задан»", async () => {
  await openSpeakers();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  await userEvent.click(await within(row).findByRole("button", { name: "Удалить токен…" }));
  const ask = screen.getByRole("alertdialog", { name: "Удалить токен Hugging Face?" });
  expect(ask).toHaveTextContent("без разделения на спикеров");
  expect(api.deleteHfToken).not.toHaveBeenCalled();
  await userEvent.click(within(ask).getByRole("button", { name: "Удалить" }));
  expect(api.deleteHfToken).toHaveBeenCalledWith(ep);
  expect(await within(row).findByText(/Не задан/)).toBeInTheDocument();
});

test("токен из переменной среды — удалить из приложения нельзя", async () => {
  vi.mocked(api.getHfStatus).mockResolvedValue({ configured: true, source: "env", check: null });
  await openSpeakers();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  expect(await within(row).findByText(/из переменной среды HF_TOKEN/)).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: "Удалить токен…" })).toBeNull();
});

test("«Приложение»: кнопка «Запустить мастер»", async () => {
  const onRunWizard = vi.fn();
  render(<SettingsPane endpoint={ep} recordingsDir={null} onRunWizard={onRunWizard} />);
  expect(await screen.findByRole("heading", { name: "Приложение" })).toBeInTheDocument();
  await userEvent.click(await screen.findByRole("button", { name: "Запустить мастер" }));
  expect(onRunWizard).toHaveBeenCalledWith("hardware");
});

test("движок не загрузился — сообщение без «Error:»", async () => {
  vi.mocked(api.getEngine).mockRejectedValue(new Error("нет python"));
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Движок и модели" }));
  expect(await screen.findByText("Движок не загрузился: нет python")).toBeInTheDocument();
});

const engineState: api.EngineState = {
  installed: true, missing: [], flavor: "cuda", download_gb: 3, python: "3.12", target: "C:\meet\engine\0.1.0",
  ffmpeg: true, gpu: { available: true, name: "RTX 5070 Ti" },
  components: [{ module: "torch", title: "PyTorch", installed: true }],
};

test("движок: только сведения; переустановка — через мастер на шаге движка, без установки резидентом", async () => {
  vi.mocked(api.getEngine).mockResolvedValue(structuredClone(engineState));
  const onRunWizard = vi.fn();
  await openEngine({ onRunWizard });
  expect(await screen.findByText("установлен")).toBeInTheDocument();
  expect(screen.getByText("PyTorch")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Установить движок" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Переустановить" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Переустановить движок" }));
  expect(onRunWizard).toHaveBeenCalledWith("engine");
});

test("«Звук»: выбранный микрофон сохраняется патчем recording.mic_device", async () => {
  vi.mocked(api.getDevices).mockResolvedValue({
    available: true, pinning: true,
    inputs: [{ name: "Микрофон", default: true }, { name: "USB-микрофон", default: false }],
    outputs: [{ name: "Динамики", default: true }],
  });
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Звук" }));
  const mic = await screen.findByRole("combobox", { name: "Микрофон" });
  await waitFor(() => expect(mic).toHaveDisplayValue("Как в системе (сейчас: Микрофон)"));
  await userEvent.selectOptions(mic, "USB-микрофон");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1]).toEqual({ recording: { mic_device: { name: "USB-микрофон" } } });
});

test("«Автозапись»: пресет программы звонков сохраняется списком exe", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  expect(await screen.findByRole("checkbox", { name: "Zoom" })).toBeChecked();
  expect(screen.queryByRole("checkbox", { name: "teams.exe" })).toBeNull(); // не стена процессов
  await userEvent.click(screen.getByRole("checkbox", { name: "Microsoft Teams" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1])
    .toEqual({ auto_record: { processes: ["zoom.exe", "ms-teams.exe", "Teams.exe"] } });
});

test("«Автозапись»: браузер для звонков сохраняется отдельно от программ", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  const group = await screen.findByRole("group", { name: "Звонки в браузере" });
  await userEvent.click(within(group).getByRole("checkbox", { name: "Google Chrome" }));
  // Строгий режим и сайты звонков — в «Тонкой настройке».
  await userEvent.click(screen.getByRole("button", { name: "Тонкая настройка" }));
  await userEvent.click(screen.getByRole("checkbox", { name: /Только если в заголовке окна сайт звонка/ }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1])
    .toEqual({ auto_record: { browsers: ["chrome.exe"], browser_require_site: true } });
});

test("«Спикеры»: порог узнавания голоса — ползунок в процентах, сохраняется в asr.voice_threshold", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir="C:\rec" />);
  await userEvent.click(await screen.findByRole("button", { name: "Спикеры" }));
  const slider = await screen.findByRole("slider", { name: /Порог узнавания голоса/ });
  expect(slider).toHaveValue("75");
  expect(screen.getByLabelText("Что такое порог узнавания голоса")).toBeInTheDocument();
  fireEvent.change(slider, { target: { value: "82" } });
  expect(screen.getByText("82%")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  const call = vi.mocked(api.patchSettings).mock.calls[0]?.[1] as { asr: { voice_threshold: number } };
  expect(call.asr.voice_threshold).toBe(0.82);
});

// --- движок распознавания и модели GigaAM -------------------------------------

const modelItem = (over: Partial<api.Model>): api.Model => ({
  id: "x", kind: "asr", title: "x", note: "", size_gb: 1, downloaded: false, size_on_disk: 0,
  selected: false, blocked: false, ...over,
});

const catalogue = (): api.ModelsState => ({
  items: [
    modelItem({ id: "large-v3", backend: "faster-whisper", downloaded: true,
      title: "Whisper large-v3 — русский fine-tune", size_gb: 3.1, recommended: true, selected: true }),
    modelItem({ id: "gigaam/v3_e2e_rnnt", backend: "gigaam", title: "GigaAM v3 — русский", size_gb: 0.45,
      note: "по умолчанию на процессоре", downloaded: true, size_on_disk: 449_000_000, selected: true, removable: true }),
    modelItem({ id: "gigaam/v3_e2e_ctc", backend: "gigaam", title: "GigaAM v3 CTC — русский", size_gb: 0.44,
      note: "быстрее, чуть менее точно" }),
    modelItem({ id: "medium", backend: "faster-whisper", title: "Whisper medium", size_gb: 1.5 }),
  ],
  cache: "C:\\hf", token: true, selected: "large-v3",
  can_download: true, can_download_gigaam: true, gigaam_cache: "C:\\data\\meet\\models\\gigaam",
});

const modelRow = (title: string) => {
  const label = screen.getByText(title);
  return label.closest(".srow") as HTMLElement;
};

// --- «Распознавание»: устройство, движок и модель для видеокарты и процессора ---

const OTHER = "\u0000other";
const openAsr = async (engine: Partial<api.EngineState> | null, asr: Record<string, unknown> = {}) => {
  if (engine) vi.mocked(api.getEngine).mockResolvedValue({ ...structuredClone(engineState), ...engine });
  vi.mocked(api.getSettings).mockResolvedValue({ ...structuredClone(settings), asr: { ...settings.asr, ...asr } });
  vi.mocked(api.getModels).mockResolvedValue(catalogue());
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Распознавание" }));
};
const deviceRow = (name: "Видеокарта" | "Процессор") => screen.getByRole("group", { name });
const savedPatch = async () => {
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  return vi.mocked(api.patchSettings).mock.calls[0]?.[1];
};

test("«Распознавание»: строки видеокарты и процессора показывают движок и модель из настроек", async () => {
  await openAsr({ cuda_ok: true });
  const gpu = await screen.findByRole("group", { name: "Видеокарта" });
  expect(within(within(gpu).getByRole("radiogroup", { name: "Движок" })).getByRole("radio", { name: "Whisper" }))
    .toBeChecked();
  expect(await within(gpu).findByRole("combobox", { name: "Модель Whisper" })).toHaveValue("large-v3");
  expect(within(gpu).getByRole("option", { name: "Whisper large-v3 — русский fine-tune — скачана" })).toBeInTheDocument();
  expect(within(gpu).getByRole("option", { name: "Whisper medium — 1.5 ГБ, не скачана" })).toBeInTheDocument();
  const cpu = deviceRow("Процессор");
  // Нет cpu_backend в настройках — GigaAM, как у резидента по умолчанию.
  expect(within(cpu).getByRole("radio", { name: "GigaAM" })).toBeChecked();
  expect(within(cpu).getByRole("combobox", { name: "Модель GigaAM" })).toHaveValue("v3_e2e_rnnt");
  // Модель Whisper процессора — не отдельное поле, а запасной путь GigaAM.
  expect(within(cpu).queryByRole("combobox", { name: "Модель Whisper" })).toBeNull();
  expect(cpu).toHaveTextContent("Записи не на русском распознаёт Whisper: small");
});

test.each([
  // устройство, видеокарта годится, кто распознаёт, строка «Сейчас», пояснение у другой строки
  ["auto", true, "cuda", "Сейчас: видеокарта (RTX 5070 Ti)", "Запасной вариант, если видеокарта недоступна"],
  ["auto", false, "cpu", "Сейчас: процессор — видеокарта недоступна (установлен движок для процессора)",
    "Недоступна: установлен движок для процессора"],
  ["cpu", true, "cpu", "Сейчас: процессор", "Используется, если выбрать устройство «Видеокарта» или «Авто»"],
  ["cpu", false, "cpu", "Сейчас: процессор", "Недоступна: установлен движок для процессора"],
  ["cuda", true, "cuda", "Сейчас: видеокарта (RTX 5070 Ti)", "Используется, если выбрать устройство «Процессор»"],
  ["cuda", false, "cpu", "Сейчас: процессор — видеокарта недоступна (установлен движок для процессора)",
    "Недоступна: установлен движок для процессора"],
] as const)("«Распознавание»: устройство %s, видеокарта годится — %s: распознаёт %s", async (device, ok, active, now, note) => {
  await openAsr({ cuda_ok: ok, cuda_reason: ok ? null : "установлен движок для процессора" }, { device });
  expect(await screen.findByText(now)).toBeInTheDocument();
  const on = deviceRow(active === "cuda" ? "Видеокарта" : "Процессор");
  const off = deviceRow(active === "cuda" ? "Процессор" : "Видеокарта");
  expect(on).toHaveAttribute("data-state", "active");
  expect(within(on).getByText("используется")).toBeInTheDocument();
  expect(within(off).queryByText("используется")).toBeNull();
  expect(off).toHaveAttribute("data-state", ok ? "secondary" : "disabled");
  expect(off).toHaveTextContent(note);
});

test("«Распознавание»: состояние движка не загрузилось — при «Авто» ни одна строка не отмечена", async () => {
  await openAsr(null);
  expect(await screen.findByText(/состояние движка не загрузилось/)).toBeInTheDocument();
  expect(deviceRow("Видеокарта")).toHaveAttribute("data-state", "secondary");
  expect(deviceRow("Процессор")).toHaveAttribute("data-state", "secondary");
  expect(deviceRow("Процессор")).toHaveTextContent("Запасной вариант, если видеокарта недоступна");
});

test("«Распознавание»: другое устройство в черновике — «После сохранения», отметка переезжает сразу", async () => {
  await openAsr({ cuda_ok: true });
  await screen.findByText("Сейчас: видеокарта (RTX 5070 Ti)");
  await userEvent.selectOptions(screen.getByLabelText("Устройство"), "cpu");
  expect(screen.getByText("После сохранения: процессор")).toBeInTheDocument();
  expect(deviceRow("Процессор")).toHaveAttribute("data-state", "active");
  expect(await savedPatch()).toEqual({ asr: { device: "cpu" } });
});

test("«Распознавание»: старый резидент без cuda_ok — видеокарта годится, если она видна", async () => {
  await openAsr({ gpu: { available: false, name: null } });
  expect(await screen.findByText("Сейчас: процессор — видеокарта недоступна (видеокарта NVIDIA не найдена)"))
    .toBeInTheDocument();
});

test("«Распознавание»: недоступная видеокарта — строка приглушена, выбор в ней недоступен, причина видна", async () => {
  await openAsr({ cuda_ok: false, cuda_reason: "не найдены библиотеки CUDA (cuBLAS, cuDNN)" });
  const gpu = await screen.findByRole("group", { name: "Видеокарта" });
  await waitFor(() => expect(gpu).toHaveAttribute("data-state", "disabled"));
  expect(gpu).toHaveTextContent("Недоступна: не найдены библиотеки CUDA (cuBLAS, cuDNN)");
  for (const radio of within(gpu).getAllByRole("radio")) expect(radio).toBeDisabled();
  expect(await within(gpu).findByRole("combobox", { name: "Модель Whisper" })).toBeDisabled();
  expect(deviceRow("Процессор")).toHaveAttribute("data-state", "active");
});

test("«Распознавание»: смена движка меняет поле модели; сохраняются прежние ключи asr", async () => {
  await openAsr({ cuda_ok: true });
  const gpu = await screen.findByRole("group", { name: "Видеокарта" });
  await within(gpu).findByRole("combobox", { name: "Модель Whisper" });
  await userEvent.click(within(gpu).getByRole("radio", { name: "GigaAM" }));
  expect(within(gpu).queryByRole("combobox", { name: "Модель Whisper" })).toBeNull();
  expect(within(gpu).getByRole("combobox", { name: "Модель GigaAM" })).toHaveValue("v3_e2e_rnnt");
  // Модель GigaAM одна на оба устройства — так и сказано, когда она у обоих.
  expect(gpu).toHaveTextContent("Одна модель GigaAM для видеокарты и процессора");
  expect(gpu).toHaveTextContent("Записи не на русском распознаёт Whisper: Whisper large-v3 — русский fine-tune");

  const cpu = deviceRow("Процессор");
  await userEvent.click(within(cpu).getByRole("radio", { name: "Whisper" }));
  expect(within(cpu).queryByRole("combobox", { name: "Модель GigaAM" })).toBeNull();
  // «small» нет в каталоге — «Другая…» и поле с id.
  expect(within(cpu).getByRole("combobox", { name: "Модель Whisper" })).toHaveValue(OTHER);
  expect(within(cpu).getByRole("textbox", { name: "Модель Whisper: id модели или папка" })).toHaveValue("small");
  expect(gpu).not.toHaveTextContent("Одна модель GigaAM");
  await userEvent.selectOptions(within(gpu).getByRole("combobox", { name: "Модель GigaAM" }), "v3_e2e_ctc");
  expect(await savedPatch())
    .toEqual({ asr: { backend: "gigaam", cpu_backend: "faster-whisper", gigaam_model: "v3_e2e_ctc" } });
});

test("«Распознавание»: модель Whisper для записей не на русском — по «изменить» под строкой GigaAM", async () => {
  await openAsr({ cuda_ok: true });
  const cpu = await screen.findByRole("group", { name: "Процессор" });
  expect(within(cpu).queryByRole("combobox", { name: "Модель Whisper для записей не на русском" })).toBeNull();
  const change = within(cpu).getByRole("button", { name: "изменить" });
  expect(change).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(change);
  expect(within(cpu).getByRole("button", { name: "скрыть" })).toHaveAttribute("aria-expanded", "true");
  const picker = await within(cpu).findByRole("combobox", { name: "Модель Whisper для записей не на русском" });
  await userEvent.selectOptions(picker, "medium");
  expect(cpu).toHaveTextContent("Записи не на русском распознаёт Whisper: Whisper medium");
  // «Другая…» — своё значение.
  await userEvent.selectOptions(picker, OTHER);
  const custom = within(cpu).getByRole("textbox", { name: "Модель Whisper для записей не на русском: id модели или папка" });
  await userEvent.clear(custom);
  await userEvent.type(custom, "D:\\models\\whisper");
  expect(await savedPatch()).toEqual({ asr: { cpu_model: "D:\\models\\whisper" } });
});

test("«Распознавание»: язык не русский — под GigaAM сказано, что распознаёт Whisper", async () => {
  await openAsr({ cuda_ok: true }, { language: "en" });
  const cpu = await screen.findByRole("group", { name: "Процессор" });
  expect(cpu).toHaveTextContent("Язык речи «en»: GigaAM его не понимает, распознаёт Whisper: small");
});

test("«Распознавание»: каталог не загрузился — модель Whisper вводится полем", async () => {
  vi.mocked(api.getEngine).mockResolvedValue({ ...structuredClone(engineState), cuda_ok: true });
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Распознавание" }));
  const gpu = deviceRow("Видеокарта");
  expect(within(gpu).getByRole("textbox", { name: "Модель Whisper" })).toHaveValue("large-v3");
  await userEvent.type(within(gpu).getByRole("textbox", { name: "Модель Whisper" }), "-x");
  expect(await savedPatch()).toEqual({ asr: { model: "large-v3-x" } });
});

test("«Распознавание»: сохранённый движок «whisper.cpp» показывается как Whisper", async () => {
  await openAsr({ cuda_ok: true }, { backend: "whisper.cpp", cpu_backend: "faster-whisper" });
  const gpu = await screen.findByRole("group", { name: "Видеокарта" });
  expect(within(gpu).getByRole("radio", { name: "Whisper" })).toBeChecked();
  expect(within(deviceRow("Процессор")).getByRole("radio", { name: "Whisper" })).toBeChecked();
});

test("«Распознавание»: вопрос при уходе знает о правке движка и сохраняет её", async () => {
  const guard = { current: null } as { current: import("./SettingsPane").SettingsGuard | null };
  vi.mocked(api.getEngine).mockResolvedValue({ ...structuredClone(engineState), cuda_ok: true });
  render(<SettingsPane endpoint={ep} recordingsDir={null} guardRef={guard} />);
  await userEvent.click(await screen.findByRole("button", { name: "Распознавание" }));
  await userEvent.click(within(deviceRow("Процессор")).getByRole("radio", { name: "Whisper" }));
  expect(guard.current?.dirty).toEqual(["Распознавание"]);
  expect(await guard.current!.save()).toBe(true);
  expect(api.patchSettings).toHaveBeenCalledWith(ep, { asr: { cpu_backend: "faster-whisper" } });
  await waitFor(() => expect(guard.current?.dirty).toEqual([]));
});

test("«Движок и модели»: выбора движка нет — ссылка ведёт в «Распознавание»", async () => {
  await openEngine();
  expect(screen.queryByRole("radiogroup")).toBeNull();
  await userEvent.click(await screen.findByRole("button", { name: "«Распознавание»" }));
  expect(screen.getByRole("heading", { name: "Распознавание" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Видеокарта" })).toBeInTheDocument();
});

test("«Движок и модели»: модели GigaAM — размер, где выбрана, удаление", async () => {
  vi.mocked(api.getModels).mockResolvedValue(catalogue());
  vi.mocked(api.removeModel).mockResolvedValue({ ok: true });
  await openEngine();
  await screen.findByText("GigaAM v3 — русский");
  const rnnt = modelRow("GigaAM v3 — русский");
  const ctc = modelRow("GigaAM v3 CTC — русский");
  expect(rnnt).toHaveTextContent("0.45 ГБ");
  expect(ctc).toHaveTextContent("быстрее, чуть менее точно");
  // Выбирают в «Распознавании»; здесь видно только, где модель выбрана.
  expect(within(rnnt).getByText("Выбрана")).toBeInTheDocument();
  expect(rnnt).toHaveTextContent("Выбрана в «Распознавании»: процессор");
  expect(modelRow("Whisper large-v3 — русский fine-tune")).toHaveTextContent("Выбрана в «Распознавании»: видеокарта");
  expect(within(ctc).queryByText("Выбрана")).toBeNull();
  expect(screen.queryByRole("button", { name: "Выбрать" })).toBeNull();
  expect(within(ctc).queryByRole("button", { name: /Удалить/ })).toBeNull();
  expect(screen.getByText("C:\\data\\meet\\models\\gigaam")).toBeInTheDocument();

  // Отметка следует черновику «Распознавания»: процессор на Whisper — GigaAM больше нигде не выбрана.
  await userEvent.click(screen.getByRole("button", { name: "Распознавание" }));
  await userEvent.click(within(screen.getByRole("group", { name: "Процессор" })).getByRole("radio", { name: "Whisper" }));
  await userEvent.click(screen.getByRole("button", { name: "Движок и модели" }));
  await screen.findByText("GigaAM v3 — русский");
  const unused = modelRow("GigaAM v3 — русский");
  expect(within(unused).queryByText("Выбрана")).toBeNull();
  const loads = vi.mocked(api.getModels).mock.calls.length;
  await userEvent.click(within(unused).getByRole("button", { name: "Удалить модель GigaAM v3 — русский" }));
  // И невыбранную модель — только после подтверждения в её строке: это гигабайты повторной загрузки.
  expect(api.removeModel).not.toHaveBeenCalled();
  const ask = within(unused).getByRole("alertdialog");
  expect(ask).toHaveTextContent("будут удалены с диска");
  await userEvent.click(within(ask).getByRole("button", { name: "Удалить" }));
  expect(api.removeModel).toHaveBeenCalledWith(ep, "gigaam/v3_e2e_rnnt");
  await waitFor(() => expect(vi.mocked(api.getModels).mock.calls.length).toBeGreaterThan(loads));
});

test("«Движок и модели»: без пакета GigaAM его модели не скачать", async () => {
  vi.mocked(api.getModels).mockResolvedValue({ ...catalogue(), can_download_gigaam: false });
  await openEngine();
  await screen.findByText("GigaAM v3 CTC — русский");
  expect(within(modelRow("GigaAM v3 CTC — русский")).getByRole("button", { name: "Скачать" })).toBeDisabled();
  expect(within(modelRow("Whisper large-v3 — русский fine-tune")).getByRole("button", { name: "Обновить" }))
    .toBeEnabled();
});

test("«Движок и модели»: скачанная GigaAM — «Скачана», без «Обновить»; недокачанную можно удалить", async () => {
  const state = catalogue();
  state.items[2] = { ...state.items[2]!, downloaded: false, removable: true, size_on_disk: 1000 };
  vi.mocked(api.getModels).mockResolvedValue(state);
  await openEngine();
  await screen.findByText("GigaAM v3 — русский");
  const rnnt = modelRow("GigaAM v3 — русский");
  expect(within(rnnt).getByText("Скачана")).toBeInTheDocument();
  expect(within(rnnt).queryByRole("button", { name: "Обновить" })).toBeNull();
  const ctc = modelRow("GigaAM v3 CTC — русский");
  expect(ctc).toHaveTextContent("загрузка не завершена");
  expect(within(ctc).getByRole("button", { name: "Скачать" })).toBeEnabled();
  expect(within(ctc).getByRole("button", { name: "Удалить модель GigaAM v3 CTC — русский" })).toBeEnabled();
});

test("«Движок и модели»: удаление выбранной GigaAM — только после подтверждения", async () => {
  vi.mocked(api.getModels).mockResolvedValue(catalogue());
  vi.mocked(api.removeModel).mockResolvedValue({ ok: true });
  await openEngine();
  await screen.findByText("GigaAM v3 — русский");
  const rnnt = modelRow("GigaAM v3 — русский");
  await userEvent.click(within(rnnt).getByRole("button", { name: "Удалить модель GigaAM v3 — русский" }));
  const ask = within(rnnt).getByRole("alertdialog", { name: "Удалить модель «GigaAM v3 — русский»?" });
  expect(ask).toHaveTextContent("выбрана для распознавания — она скачается снова");
  expect(api.removeModel).not.toHaveBeenCalled();
  await userEvent.click(within(ask).getByRole("button", { name: "Отмена" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await userEvent.click(within(rnnt).getByRole("button", { name: "Удалить модель GigaAM v3 — русский" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Удалить" }));
  expect(api.removeModel).toHaveBeenCalledWith(ep, "gigaam/v3_e2e_rnnt");
});

test("«Движок и модели»: движок без GigaAM — установлен, у GigaAM пометка", async () => {
  vi.mocked(api.getEngine).mockResolvedValue({
    ...structuredClone(engineState),
    components: [
      { module: "torch", title: "PyTorch", installed: true },
      { module: "gigaam", title: "распознавание речи (GigaAM)", installed: false, optional: true,
        note: "не установлена — будет установлена при обновлении движка" },
    ],
  });
  await openEngine();
  expect(await screen.findByText("установлен")).toBeInTheDocument();
  expect(screen.getByText("распознавание речи (GigaAM) — не установлена — будет установлена при обновлении движка"))
    .toBeInTheDocument();
});

// --- раскладка, несохранённое, ширины полей -----------------------------------

test("колонка по центру: шапка с «Сохранить» — внутри той же колонки, и в «О программе» тоже", async () => {
  const { container } = render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await screen.findByLabelText("Ваше имя в расшифровке");
  const column = container.querySelector(".settings__column")!;
  expect(column.querySelector(".settings__head")).toContainElement(screen.getByRole("button", { name: "Сохранить" }));
  expect(column.querySelector(".settings__content")).not.toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "О программе" }));
  // Без черновика кнопок нет, но место под них то же: заголовок не сдвигается.
  expect(container.querySelector(".settings__head .settings__actions")).not.toBeNull();
  expect(screen.queryByRole("button", { name: "Сохранить" })).toBeNull();
});

test("правка — точка у своего раздела в меню и строка «Есть несохранённые изменения»; в другом разделе — где именно", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  const name = await screen.findByLabelText("Ваше имя в расшифровке");
  const menuItem = (title: string) => screen.getByRole("button", { name: title });
  expect(menuItem("Приложение").querySelector("[data-dirty]")).toBeNull();
  expect(screen.getByRole("button", { name: "Сбросить…" })).toBeDisabled();
  await userEvent.type(name, "а");
  expect(menuItem("Приложение").querySelector("[data-dirty]")).not.toBeNull();
  expect(menuItem("Приложение")).toHaveAttribute("title", "Есть несохранённые изменения");
  // Экранному диктору — описанием кнопки, имя раздела то же.
  expect(menuItem("Приложение")).toHaveAccessibleDescription("Есть несохранённые изменения");
  expect(menuItem("Распознавание").querySelector("[data-dirty]")).toBeNull();
  expect(document.querySelector(".settings__state")).toHaveTextContent("Есть несохранённые изменения");
  await userEvent.click(menuItem("Распознавание"));
  expect(screen.getByText("Не сохранено: Приложение")).toBeInTheDocument();
  // Движок и модель выбираются только в «Распознавании» — и точка только там.
  await userEvent.click(within(screen.getByRole("group", { name: "Видеокарта" })).getByRole("radio", { name: "GigaAM" }));
  expect(menuItem("Распознавание").querySelector("[data-dirty]")).not.toBeNull();
  expect(menuItem("Движок и модели").querySelector("[data-dirty]")).toBeNull();
});

test("«Сбросить…» спрашивает и только потом отменяет правки всех разделов", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  const name = await screen.findByLabelText("Ваше имя в расшифровке");
  await userEvent.type(name, "а");
  const loads = vi.mocked(api.getSettings).mock.calls.length;
  await userEvent.click(screen.getByRole("button", { name: "Сбросить…" }));
  const ask = screen.getByRole("alertdialog", { name: "Отменить несохранённые изменения?" });
  expect(ask).toHaveTextContent("«Приложение»");
  expect(within(ask).getByRole("button", { name: "Оставить правки" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(name).toHaveValue("Выа");
  await userEvent.click(screen.getByRole("button", { name: "Сбросить…" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Сбросить" }));
  await waitFor(() => expect(vi.mocked(api.getSettings).mock.calls.length).toBe(loads + 1));
  await waitFor(() => expect(screen.getByLabelText("Ваше имя в расшифровке")).toHaveValue("Вы"));
});

test("после сохранения — «Сохранено.» в той же строке, точки в меню нет", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.type(await screen.findByLabelText("Ваше имя в расшифровке"), "а");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByText("Сохранено.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Приложение" }).querySelector("[data-dirty]")).toBeNull();
});

test("guardRef: список разделов с правками и save для вопроса при уходе", async () => {
  const guard = { current: null } as { current: import("./SettingsPane").SettingsGuard | null };
  render(<SettingsPane endpoint={ep} recordingsDir={null} guardRef={guard} />);
  await userEvent.type(await screen.findByLabelText("Ваше имя в расшифровке"), "а");
  expect(guard.current?.dirty).toEqual(["Приложение"]);
  expect(guard.current?.canSave).toBe(true);
  expect(await guard.current!.save()).toBe(true);
  expect(api.patchSettings).toHaveBeenCalledWith(ep, { recording: { speaker_name: "Выа" } });
  await waitFor(() => expect(guard.current?.dirty).toEqual([]));
});

test("пути, команды и id моделей — во всю ширину; зависимые поля недоступны при выключенном переключателе", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Распознавание" }));
  // Каталог моделей не загрузился — id модели вводится полем во всю ширину.
  const model = screen.getByLabelText("Модель Whisper");
  expect(model).toHaveClass("input--wide");
  expect(model.closest(".srow")).toHaveClass("srow--stack");
  expect(screen.getByLabelText("Язык речи")).toHaveClass("input--short");
  await userEvent.click(screen.getByRole("button", { name: "Дополнительно" }));
  const command = screen.getByLabelText("Команда");
  expect(command).toBeDisabled();
  expect(command).toHaveClass("input--wide");
  expect(screen.getByText("Включите «Запускать команду после записи», чтобы задать команду")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("switch", { name: "Запускать команду после записи" }));
  expect(screen.getByLabelText("Команда")).toBeEnabled();
});

test("команда после записи: сказано, что ассистент дописывает ленту и сводку уже после неё", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Распознавание" }));
  await userEvent.click(screen.getByRole("button", { name: "Дополнительно" }));
  expect(screen.getByText(/ассистент дописывает ленту и сводку после неё/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Как задать команду" }));
  const tip = (await screen.findByText("live_transcript.md")).parentElement;
  expect(tip).toHaveTextContent("live_state.json");
  expect(tip).toHaveTextContent("дописывает");
});

test("«Оформление» — первый раздел, без «Сохранить»: применяется сразу", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="appearance" />);
  const nav = await screen.findByRole("navigation", { name: "Разделы настроек" });
  expect(within(nav).getAllByRole("button")[0]).toHaveTextContent("Оформление");
  expect(await screen.findByRole("radiogroup", { name: "Тема" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
});

test("ключи оформления не попадают в черновик других разделов", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="appearance" />);
  await userEvent.click(await screen.findByRole("radio", { name: "Светлая" }));
  const nav = screen.getByRole("navigation", { name: "Разделы настроек" });
  expect(within(nav).queryByTitle("Есть несохранённые изменения")).not.toBeInTheDocument();
});

// --- 0.4: группы меню, узкие разделы, перенос строк ---------------------------

const GROUPS: [string, string[]][] = [
  ["Общее", ["Оформление", "Приложение"]],
  ["Запись", ["Звук", "Автозапись"]],
  ["Расшифровка", ["Распознавание", "Спикеры", "Словарь", "Движок и модели"]],
  ["ИИ", ["Модели ИИ", "Ассистент", "Анализ встречи"]],
  ["Встречи", ["Категории", "Экспорт", "Jira"]],
  ["Система", ["Дополнительно", "Диагностика", "О программе"]],
];

test("меню — группы с подписями обычным регистром, в каждой свои разделы по порядку", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  const nav = await screen.findByRole("navigation", { name: "Разделы настроек" });
  expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(GROUPS.flatMap(([, items]) => items));
  for (const [title, items] of GROUPS) {
    const group = within(nav).getByRole("group", { name: title });
    expect(within(group).getByText(title)).toBeVisible();
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(items);
  }
});

test("без initial открывается «Приложение»; заголовок раздела — второго уровня, панель плотная", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  const head = await screen.findByRole("heading", { level: 2, name: "Приложение" });
  expect(screen.getByRole("button", { name: "Приложение" })).toHaveAttribute("aria-current", "page");
  expect(head.closest("[data-density='compact']")).not.toBeNull();
});

test.each([
  ["recording", "Приложение"], ["markup", "Анализ встречи"], ["sound", "Звук"], ["auto", "Автозапись"],
  ["asr", "Распознавание"], ["engine", "Движок и модели"], ["export", "Экспорт"], ["assistant", "Ассистент"],
  ["analysis", "Анализ встречи"], ["categories", "Категории"], ["diagnostics", "Диагностика"],
  ["about", "О программе"], ["advanced", "Дополнительно"], ["appearance", "Оформление"],
  // Новые id тоже открываются напрямую.
  ["app", "Приложение"], ["speakers", "Спикеры"], ["dictionary", "Словарь"], ["models", "Модели ИИ"], ["jira", "Jira"],
])("id «%s» открывает раздел «%s»", async (id, title) => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial={id} />);
  expect(await screen.findByRole("heading", { level: 2, name: title })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: title })).toHaveAttribute("aria-current", "page");
});

test("LEGACY_SECTION: все прежние id 0.3.7, «recording» → «app», «markup» → «analysis»", () => {
  expect(Object.keys(LEGACY_SECTION).sort()).toEqual([
    "about", "advanced", "analysis", "appearance", "asr", "assistant", "auto", "categories", "diagnostics", "engine",
    "export", "markup", "recording", "sound",
  ]);
  expect(LEGACY_SECTION.recording).toBe("app");
  expect(LEGACY_SECTION.markup).toBe("analysis");
  expect(GROUPS.flatMap(([, items]) => items)).toHaveLength(17);
});

test("повторная просьба открыть прежний раздел (новый initialTick) возвращает в его новый раздел", async () => {
  const { rerender } = render(<SettingsPane endpoint={ep} recordingsDir={null} initial="markup" initialTick={1} />);
  expect(await screen.findByRole("heading", { level: 2, name: "Анализ встречи" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Звук" }));
  rerender(<SettingsPane endpoint={ep} recordingsDir={null} initial="markup" initialTick={2} />);
  expect(await screen.findByRole("heading", { level: 2, name: "Анализ встречи" })).toBeInTheDocument();
});

test("«Спикеры»: токен Hugging Face, «Мой голос», порог узнавания и одновременная речь — подгруппы-карточки", async () => {
  await openSpeakers();
  expect(await screen.findByRole("group", { name: "Токен Hugging Face" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Мой голос" })).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: /Порог узнавания голоса/ })).toBeInTheDocument();
  expect(screen.getByRole("switch", { name: "Отмечать одновременную речь" })).toBeChecked();
  expect(screen.getAllByRole("heading", { level: 3 }).length).toBeGreaterThanOrEqual(2);
});

test("«Звук» и «Движок и модели» — без голоса, токена и мастера: там ссылки на «Спикеры»", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="sound" onRunWizard={vi.fn()} />);
  expect(await screen.findByRole("combobox", { name: "Микрофон" })).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Мой голос" })).toBeNull();
  expect(screen.getByRole("button", { name: "«Спикеры»" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Движок и модели" }));
  expect(await screen.findByText(/Движок не загрузился/)).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Токен Hugging Face" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Запустить мастер" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "«Спикеры»" }));
  expect(screen.getByRole("heading", { level: 2, name: "Спикеры" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Токен Hugging Face" })).toBeInTheDocument();
});

test("«Распознавание» — устройство, движок, язык и время слов; без порога, одновременной речи и словаря", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="asr" />);
  expect(await screen.findByLabelText("Язык речи")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Видеокарта" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Тонкая настройка" }));
  expect(screen.getByRole("switch", { name: "Уточнять время каждого слова" })).toBeInTheDocument();
  expect(screen.queryByRole("slider")).toBeNull();
  expect(screen.queryByRole("switch", { name: "Отмечать одновременную речь" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Термины распознавания" })).toBeNull();
});

test("«Словарь»: термины и исправления для будущих расшифровок; исправление — в asr.replacements, точка у «Словаря»", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="dictionary" />);
  expect(await screen.findByRole("textbox", { name: "Термины распознавания" })).toBeInTheDocument();
  await userEvent.type(screen.getByRole("textbox", { name: "Как распознаётся" }), "кафка");
  await userEvent.type(screen.getByRole("textbox", { name: "Как правильно" }), "Kafka");
  await userEvent.click(screen.getByRole("button", { name: "Добавить" }));
  expect(screen.getByRole("button", { name: "Словарь" }).querySelector("[data-dirty]")).not.toBeNull();
  expect(screen.getByRole("button", { name: "Распознавание" }).querySelector("[data-dirty]")).toBeNull();
  expect(await savedPatch()).toEqual({ asr: { replacements: [{ from: "кафка", to: "Kafka" }] } });
});

test("«Приложение»: уведомления, имя, расшифровка сразу, папка записей и мастер первого запуска", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir="D:/rec" onRunWizard={vi.fn()} />);
  expect(await screen.findByLabelText("Ваше имя в расшифровке")).toBeInTheDocument();
  expect(screen.getByRole("radiogroup", { name: "Уведомления" })).toBeInTheDocument();
  expect(screen.getByRole("switch", { name: "Расшифровывать сразу после записи" })).toBeInTheDocument();
  expect(screen.getByText("D:/rec")).toBeInTheDocument();
  expect(screen.getByText("Мастер первого запуска")).toBeInTheDocument();
});

test("«Модели ИИ» — провайдеры и прокси; «Ассистент» — участник и база знаний, без моделей и запуска агента", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="models" />);
  expect(await screen.findByRole("group", { name: "Модели" })).toBeInTheDocument();
  expect(screen.getByRole("radiogroup", { name: "Прокси для подключения к моделям" })).toBeInTheDocument();
  expect(screen.queryByRole("switch", { name: "Ассистент — участник встречи" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Ассистент" }));
  expect(await screen.findByRole("switch", { name: "Ассистент — участник встречи" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "База знаний для ассистента" })).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Модели" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Запуск Claude Code" })).toBeNull();
  // Модель, которая ведёт ассистента, выбирается в «Моделях ИИ» — туда ссылка.
  await userEvent.click(screen.getByRole("button", { name: "«Модели ИИ»" }));
  expect(screen.getByRole("heading", { level: 2, name: "Модели ИИ" })).toBeInTheDocument();
});

test("«Дополнительно»: команда после записи, маркер видеокарты и запуск агента (вкладка «Агент»)", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="advanced" />);
  expect(await screen.findByRole("switch", { name: "Запускать команду после записи" })).toBeInTheDocument();
  expect(screen.getByRole("switch", { name: "Сообщать другим программам о занятости видеокарты" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Запуск агента (вкладка «Агент»)" })).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Запуск Claude Code" })).toBeInTheDocument();
});

test("«Анализ встречи» вобрал «Подсветку расшифровки» (адрес и проекты Jira — нет); Jira — свой раздел", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="analysis" />);
  expect(await screen.findByRole("switch", { name: "Типы реплик: показывать" })).toBeInTheDocument();
  expect(screen.getByRole("switch", { name: "Подписи глав на полосе плеера" })).toBeInTheDocument();
  expect(screen.queryByRole("switch", { name: "Ссылки на задачи Jira" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Jira" }));
  expect(await screen.findByRole("switch", { name: "Ссылки на задачи Jira" })).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Адрес Jira" })).toBeInTheDocument();
});

test("подзаголовки — обычным регистром: стили настроек не делают текст прописным", () => {
  for (const name of ["settings.css", "jira-settings.css"]) {
    const css = readFileSync(join(process.cwd(), "src", "features", "settings", name), "utf8");
    expect(css).not.toMatch(/text-transform:\s*uppercase/);
  }
});

test("меню: наведение тише выбранного (--surface-2 против --surface-3 с контуром); Jira — без старых переменных", () => {
  const css = readFileSync(join(process.cwd(), "src", "features", "settings", "settings.css"), "utf8");
  const rule = (sel: string) => new RegExp(`${sel.replace(/[.[\]"=]/g, "\\$&")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";
  expect(rule('.settings__item:hover')).toMatch(/background: var\(--surface-2\)/);
  expect(rule('.settings__item[aria-current="page"]')).toMatch(/background: var\(--surface-3\)/);
  const jira = readFileSync(join(process.cwd(), "src", "features", "settings", "jira-settings.css"), "utf8");
  expect(jira).not.toMatch(/var\(--(text|text-2|text-3|line|surface|surface-hover)\)/);
});

test("меню: подписи групп жирные, группы разделены линией и отступом", () => {
  const css = readFileSync(join(process.cwd(), "src", "features", "settings", "settings.css"), "utf8");
  const rule = (sel: string) => new RegExp(`${sel.replace(/[.+]/g, "\\$&")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";
  expect(rule(".settings__group-title")).toMatch(/font-weight: 700/);
  expect(rule(".settings__group-title")).toMatch(/color: var\(--ink\)/);
  expect(rule(".settings__group + .settings__group")).toMatch(/border-top: 1px solid var\(--hairline\)/);
});

test.each([
  ["recording", "speaker_name", ["app"]], ["recording", "auto_transcribe", ["app"]], ["recording", "mic_device", ["sound"]],
  ["ui", "notifications", ["app"]], ["ui", "theme", []], ["auto_record", "grace_minutes", ["auto"]],
  ["asr", "device", ["asr"]], ["asr", "align", ["asr"]], ["asr", "language", ["asr"]],
  ["asr", "voice_threshold", ["speakers"]], ["asr", "overlap", ["speakers"]], ["asr", "replacements", ["dictionary"]],
  ["llm", "provider", ["models"]], ["llm", "proxy", ["models"]], ["assist", "participant", ["assistant"]],
  ["assist", "window_seconds", ["assistant"]], ["assistant", "knowledge_dir", ["assistant"]],
  ["assistant", "auto_title", ["analysis"]], ["agent", "launch", ["advanced"]], ["analysis", "auto", ["analysis"]],
  ["transcript_view", "types", ["analysis"]], ["transcript_view", "curve", ["analysis"]], ["transcript_view", "jira", ["analysis", "jira"]],
  ["integrations", "jira_base_url", ["jira"]], ["integrations", "gpu_marker", ["advanced"]], ["hooks", "command", ["advanced"]],
  ["export", "folder_template", ["export"]],
] as const)("sectionsOf(%s, %s) — %j", (group, key, sections) => {
  expect(sectionsOf(group, key)).toEqual(sections);
});

test("правка порога узнавания — точка у «Спикеров», в вопросе при уходе — «Спикеры»", async () => {
  const guard = { current: null } as { current: import("./SettingsPane").SettingsGuard | null };
  render(<SettingsPane endpoint={ep} recordingsDir={null} guardRef={guard} initial="speakers" />);
  fireEvent.change(await screen.findByRole("slider", { name: /Порог узнавания голоса/ }), { target: { value: "80" } });
  expect(screen.getByRole("button", { name: "Спикеры" }).querySelector("[data-dirty]")).not.toBeNull();
  expect(screen.getByRole("button", { name: "Распознавание" }).querySelector("[data-dirty]")).toBeNull();
  expect(guard.current?.dirty).toEqual(["Спикеры"]);
  expect(await guard.current!.save()).toBe(true);
  expect(api.patchSettings).toHaveBeenCalledWith(ep, { asr: { voice_threshold: 0.8 } });
});
