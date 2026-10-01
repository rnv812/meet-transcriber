import { render, screen, waitFor } from "@testing-library/react";
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
  getJobs: vi.fn(),
  getDiagnostics: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  auto_record: { enabled: true, processes: ["zoom.exe"], grace_seconds: 30, min_call_seconds: 60 },
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
});

test("переключатель автозаписи сразу вызывает setAutoRecord, без «Сохранить»", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir="C:\rec" />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  const sw = await screen.findByRole("switch", { name: /Поднимать запись/ });
  expect(sw).toBeChecked();
  await userEvent.click(sw);
  await waitFor(() => expect(api.setAutoRecord).toHaveBeenCalledWith(ep, false));
  expect(api.patchSettings).not.toHaveBeenCalled();
});

test("«Хвост после звонка» сохраняется патчем секции и предупреждает о перезапуске", async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Автозапись" }));
  expect(screen.getByText("Эти параметры применятся после перезапуска приложения")).toBeInTheDocument();
  const tail = await screen.findByLabelText("Хвост после звонка");
  await userEvent.clear(tail);
  await userEvent.type(tail, "45");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  const call = vi.mocked(api.patchSettings).mock.calls[0]?.[1] as { auto_record: { grace_seconds: number } };
  expect(call.auto_record.grace_seconds).toBe(45);
  expect(vi.mocked(api.patchSettings).mock.calls[0]?.[1]).toEqual({ auto_record: { grace_seconds: 45 } });
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
  expect(screen.getAllByRole("button", { name: "Копировать" })).toHaveLength(2);
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
  const grace = await screen.findByRole("spinbutton", { name: /Хвост после звонка/ });
  await userEvent.clear(grace);
  await userEvent.type(grace, "45");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByText("неизвестный ключ")).toBeInTheDocument();
  expect(screen.queryByText(/Error:/)).toBeNull();
});

test("токен Hugging Face скрыт; «Показать» открывает его", async () => {
  vi.mocked(api.getModels).mockResolvedValue({ items: [], cache: "C:/hf", token: false, selected: null, can_download: true });
  vi.mocked(api.getSettings).mockResolvedValue(
    { ...structuredClone(settings), integrations: { gpu_marker: false, gpu_marker_path: null, hf_token: "hf_secret" } });
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Движок и модели" }));
  const input = await screen.findByLabelText("Токен Hugging Face");
  expect(input).toHaveAttribute("type", "password");
  expect(input).toHaveValue("hf_secret");
  await userEvent.click(screen.getByRole("button", { name: "Показать" }));
  expect(input).toHaveAttribute("type", "text");
  await userEvent.click(screen.getByRole("button", { name: "Скрыть" }));
  expect(input).toHaveAttribute("type", "password");
});

test("движок не загрузился — сообщение без «Error:»", async () => {
  vi.mocked(api.getEngine).mockRejectedValue(new Error("нет python"));
  render(<SettingsPane endpoint={ep} recordingsDir={null} />);
  await userEvent.click(await screen.findByRole("button", { name: "Движок и модели" }));
  expect(await screen.findByText("Движок не загрузился: нет python")).toBeInTheDocument();
});
