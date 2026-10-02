import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
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

test("«Ждать повторного подключения, мин» сохраняется патчем секции и предупреждает о перезапуске", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  expect(screen.getByText("Параметры ниже применяются после перезапуска приложения.")).toBeInTheDocument();
  const wait = await screen.findByLabelText("Ждать повторного подключения, мин");
  expect(wait).toHaveValue(10);
  expect(wait).toHaveAttribute("min", "1");
  expect(wait).toHaveAttribute("max", "60");
  await userEvent.clear(wait);
  await userEvent.type(wait, "15");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1]).toEqual({ auto_record: { grace_minutes: 15 } });
});

test("ожидание повторного подключения: вне 1–60 не сохраняется, на выходе из поля — к краю", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  const wait = await screen.findByLabelText("Ждать повторного подключения, мин");
  await userEvent.clear(wait);
  await userEvent.type(wait, "90");
  await userEvent.tab();
  expect(wait).toHaveValue(60);
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
  const grace = await screen.findByRole("spinbutton", { name: /Ждать повторного подключения/ });
  await userEvent.clear(grace);
  await userEvent.type(grace, "45");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByText("неизвестный ключ")).toBeInTheDocument();
  expect(screen.queryByText(/Error:/)).toBeNull();
});

const openEngine = async (props: { onRunWizard?: () => void } = {}) => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} {...props} />);
  await userEvent.click(await screen.findByRole("button", { name: "Движок и модели" }));
};

test("Hugging Face: статус из /hf/status, значение токена не показывается", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(
    { ...structuredClone(settings), integrations: { gpu_marker: false, gpu_marker_path: null, hf_token: "hf_secret" } });
  await openEngine();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  expect(await within(row).findByText(/сохранён в диспетчере учётных данных Windows/)).toBeInTheDocument();
  expect(within(row).getByText("доступ есть")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("hf_secret")).toBeNull();
  expect(document.body).not.toHaveTextContent("hf_secret");
  expect(within(row).queryByLabelText("Новый токен")).toBeNull();
});

test("«Изменить токен» → «Проверить и сохранить»: успех перечитывает статус и модели", async () => {
  vi.mocked(api.setHfToken).mockResolvedValue({ ok: true, reason: "ok", message: "Доступ есть" });
  await openEngine();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  await userEvent.click(await within(row).findByRole("button", { name: "Изменить токен" }));
  await userEvent.type(within(row).getByLabelText("Новый токен"), "hf_new");
  const models = vi.mocked(api.getModels).mock.calls.length;
  const status = vi.mocked(api.getHfStatus).mock.calls.length;
  await userEvent.click(within(row).getByRole("button", { name: "Проверить и сохранить" }));
  expect(api.setHfToken).toHaveBeenCalledWith(ep, "hf_new");
  await waitFor(() => expect(vi.mocked(api.getHfStatus).mock.calls.length).toBeGreaterThan(status));
  expect(vi.mocked(api.getModels).mock.calls.length).toBeGreaterThan(models);
  expect(within(row).queryByLabelText("Новый токен")).toBeNull();
});

test("неверный токен — «Неверный токен», поле остаётся для исправления", async () => {
  vi.mocked(api.setHfToken).mockResolvedValue({ ok: false, reason: "invalid_token", message: "Неверный токен" });
  await openEngine();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  await userEvent.click(await within(row).findByRole("button", { name: "Изменить токен" }));
  await userEvent.type(within(row).getByLabelText("Новый токен"), "hf_bad");
  await userEvent.click(within(row).getByRole("button", { name: "Проверить и сохранить" }));
  expect(await within(row).findByRole("alert")).toHaveTextContent("Неверный токен");
  expect(within(row).getByLabelText("Новый токен")).toBeInTheDocument();
});

test("«Удалить токен» — DELETE /hf/token, статус «не задан»", async () => {
  await openEngine();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  await userEvent.click(await within(row).findByRole("button", { name: "Удалить токен" }));
  expect(api.deleteHfToken).toHaveBeenCalledWith(ep);
  expect(await within(row).findByText(/Не задан/)).toBeInTheDocument();
});

test("токен из переменной среды — удалить из приложения нельзя", async () => {
  vi.mocked(api.getHfStatus).mockResolvedValue({ configured: true, source: "env", check: null });
  await openEngine();
  const row = await screen.findByRole("group", { name: "Токен Hugging Face" });
  expect(await within(row).findByText(/из переменной среды HF_TOKEN/)).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: "Удалить токен" })).toBeNull();
});

test("«Движок и модели»: кнопка «Запустить мастер»", async () => {
  const onRunWizard = vi.fn();
  await openEngine({ onRunWizard });
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
  await userEvent.click(within(group).getByRole("checkbox", { name: /Только если в заголовке окна сайт звонка/ }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1])
    .toEqual({ auto_record: { browsers: ["chrome.exe"], browser_require_site: true } });
});

test("«Распознавание»: порог узнавания голоса — ползунок в процентах, сохраняется в asr.voice_threshold", async () => {
  const { fireEvent } = await import("@testing-library/react");
  render(<SettingsPane endpoint={ep} recordingsDir="C:\rec" />);
  await userEvent.click(await screen.findByRole("button", { name: "Распознавание" }));
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
  ],
  cache: "C:\\hf", token: true, selected: "large-v3",
  can_download: true, can_download_gigaam: true, gigaam_cache: "C:\\data\\meet\\models\\gigaam",
});

const modelRow = (title: string) => {
  const label = screen.getByText(title);
  return label.closest(".srow") as HTMLElement;
};

test("«Движок и модели»: выбор движка для процессора и видеокарты сохраняется в asr", async () => {
  await openEngine();
  const cpu = await screen.findByRole("radiogroup", { name: "Распознавание на процессоре" });
  expect(within(cpu).getByRole("radio", { name: "GigaAM (русский, быстро)" })).toBeChecked();
  const gpu = screen.getByRole("radiogroup", { name: "Распознавание на видеокарте" });
  expect(within(gpu).getByRole("radio", { name: "Whisper" })).toBeChecked();
  await userEvent.click(within(cpu).getByRole("radio", { name: "Whisper (многоязычный)" }));
  await userEvent.click(within(gpu).getByRole("radio", { name: "GigaAM (быстрее, только русский)" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1])
    .toEqual({ asr: { cpu_backend: "faster-whisper", backend: "gigaam" } });
});

test("«Движок и модели»: сохранённый движок «whisper.cpp» показывается как Whisper", async () => {
  vi.mocked(api.getSettings).mockResolvedValue(
    { ...structuredClone(settings), asr: { ...settings.asr, backend: "whisper.cpp", cpu_backend: "faster-whisper" } });
  await openEngine();
  const cpu = await screen.findByRole("radiogroup", { name: "Распознавание на процессоре" });
  expect(within(cpu).getByRole("radio", { name: "Whisper (многоязычный)" })).toBeChecked();
  const gpu = screen.getByRole("radiogroup", { name: "Распознавание на видеокарте" });
  expect(within(gpu).getByRole("radio", { name: "Whisper" })).toBeChecked();
});

test("«Движок и модели»: модели GigaAM — размер, выбор и удаление", async () => {
  vi.mocked(api.getModels).mockResolvedValue(catalogue());
  vi.mocked(api.removeModel).mockResolvedValue({ ok: true });
  await openEngine();
  await screen.findByText("GigaAM v3 — русский");
  const rnnt = modelRow("GigaAM v3 — русский");
  const ctc = modelRow("GigaAM v3 CTC — русский");
  expect(rnnt).toHaveTextContent("0.45 ГБ");
  expect(ctc).toHaveTextContent("быстрее, чуть менее точно");
  expect(within(rnnt).getByText("Выбрана")).toBeInTheDocument();
  // Whisper и GigaAM выбираются независимо: у каждого своя «Выбрана».
  expect(within(modelRow("Whisper large-v3 — русский fine-tune")).getByText("Выбрана")).toBeInTheDocument();
  expect(within(ctc).queryByRole("button", { name: /Удалить/ })).toBeNull();
  expect(screen.getByText("C:\\data\\meet\\models\\gigaam")).toBeInTheDocument();

  await userEvent.click(within(ctc).getByRole("button", { name: "Выбрать" }));
  expect(within(ctc).getByText("Выбрана")).toBeInTheDocument();
  expect(within(rnnt).getByRole("button", { name: "Выбрать" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1]).toEqual({ asr: { gigaam_model: "v3_e2e_ctc" } });

  const loads = vi.mocked(api.getModels).mock.calls.length;
  await userEvent.click(within(rnnt).getByRole("button", { name: "Удалить модель GigaAM v3 — русский" }));
  // И невыбранную модель — только после подтверждения в её строке: это гигабайты повторной загрузки.
  expect(api.removeModel).not.toHaveBeenCalled();
  const ask = within(rnnt).getByRole("alertdialog");
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
