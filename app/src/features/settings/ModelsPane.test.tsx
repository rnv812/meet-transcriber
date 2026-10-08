import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelsPane } from "./ModelsPane";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import type { Job } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getModels: vi.fn(),
  getJobs: vi.fn(),
  getHfStatus: vi.fn(),
  downloadModel: vi.fn(),
  removeModel: vi.fn(),
}));
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: vi.fn(() => true),
  retryGigaamInstall: vi.fn(),
}));

const ep = { base: "/api", token: null };
const failed = "GigaAM не установилась: нет связи с GitHub — проверьте подключение или прокси в настройках — используется Whisper";

const state = (error: string | null): api.ModelsState => ({
  items: [], cache: "C:\\hf", token: true, selected: null, can_download: true, can_download_gigaam: false,
  gigaam_cache: "C:\\data\\models\\gigaam", gigaam_install_error: error,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getJobs).mockResolvedValue({ items: [] });
  vi.mocked(api.getHfStatus).mockResolvedValue({ configured: false, source: null, check: null });
});

test("GigaAM не установилась — причина, «используется Whisper» и «Повторить»", async () => {
  vi.mocked(api.getModels).mockResolvedValueOnce(state(failed)).mockResolvedValue(state(null));
  vi.mocked(shell.retryGigaamInstall).mockResolvedValue();
  render(<ModelsPane endpoint={ep} />);
  expect(await screen.findByText(failed, { exact: false })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(shell.retryGigaamInstall).toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByText(failed, { exact: false })).toBeNull());
});

test("повтор не удался — понятная ошибка, причина остаётся", async () => {
  vi.mocked(api.getModels).mockResolvedValue(state(failed));
  vi.mocked(shell.retryGigaamInstall).mockRejectedValue("нет связи с GitHub — проверьте подключение или прокси в настройках");
  render(<ModelsPane endpoint={ep} />);
  await userEvent.click(await screen.findByRole("button", { name: "Повторить" }));
  expect(await screen.findByText(/^GigaAM не установилась: нет связи с GitHub — проверьте/)).toBeInTheDocument();
});

test("вне приложения кнопки «Повторить» нет; без ошибки — и строки нет", async () => {
  vi.mocked(shell.inTauri).mockReturnValue(false);
  vi.mocked(api.getModels).mockResolvedValue(state(failed));
  const { unmount } = render(<ModelsPane endpoint={ep} />);
  expect(await screen.findByText(failed, { exact: false })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Повторить" })).toBeNull();
  unmount();
  vi.mocked(api.getModels).mockResolvedValue(state(null));
  render(<ModelsPane endpoint={ep} />);
  await screen.findByText("Папка моделей");
  expect(screen.queryByText(/GigaAM не установилась/)).toBeNull();
});

const model = (id: string, title: string, extra: Partial<api.Model> = {}): api.Model => ({
  id, kind: "asr", title, note: "", size_gb: 1.5, downloaded: false, size_on_disk: 0, selected: false,
  blocked: false, ...extra,
});
const catalogue = (): api.ModelsState => ({
  ...state(null), can_download_gigaam: true,
  items: [
    model("Systran/faster-whisper-small", "Whisper small"),
    model("gigaam/v3_e2e_rnnt", "GigaAM RNNT", { backend: "gigaam", removable: true }),
    model("gigaam/v3_e2e_ctc", "GigaAM CTC", { backend: "gigaam", removable: true }),
  ],
});
const running = (id: string, folder: string, done: number): Job => ({
  id, kind: "download-model", folder, state: "running", stage: "model", label: "загрузка модели",
  done, total: 4, note: folder, result: null, error: null,
});

test("разные модели качаются одновременно — у каждой свой ход, «Скачать» у других доступна", async () => {
  vi.mocked(api.getModels).mockResolvedValue(catalogue());
  vi.mocked(api.downloadModel).mockImplementation(async (_ep, id) =>
    id === "Systran/faster-whisper-small" ? running("j1", id, 1) : running("j2", id, 2));
  render(<ModelsPane endpoint={ep} />);
  const small = await screen.findByRole("group", { name: "Whisper small" });
  const rnnt = screen.getByRole("group", { name: "GigaAM RNNT" });
  await userEvent.click(within(small).getByRole("button", { name: "Скачать" }));
  expect(await within(small).findByText("25 %")).toBeInTheDocument();
  // Идёт одна загрузка — другие кнопки не заблокированы.
  const next = within(rnnt).getByRole("button", { name: "Скачать" });
  expect(next).toBeEnabled();
  expect(screen.queryByText(/Дождитесь окончания другой загрузки/)).toBeNull();
  await userEvent.click(next);
  expect(await within(rnnt).findByText("50 %")).toBeInTheDocument();
  expect(within(small).getByText("25 %")).toBeInTheDocument();
  expect(api.downloadModel).toHaveBeenCalledTimes(2);
});

test("удалить нельзя только модель, которая сейчас качается", async () => {
  vi.mocked(api.getModels).mockResolvedValue(catalogue());
  vi.mocked(api.getJobs).mockResolvedValue({ items: [running("j2", "gigaam/v3_e2e_rnnt", 2)] });
  render(<ModelsPane endpoint={ep} />);
  const rnnt = await screen.findByRole("group", { name: "GigaAM RNNT" });
  // Идущая загрузка подхвачена при открытии экрана.
  expect(await within(rnnt).findByText("50 %")).toBeInTheDocument();
  const busy = within(rnnt).getByRole("button", { name: "Удалить модель GigaAM RNNT" });
  expect(busy).toBeDisabled();
  // Причина — подсказкой Aurora (ui/Tip), а не системным title.
  expect(busy).not.toHaveAttribute("title");
  expect(busy).toHaveAccessibleDescription("Модель скачивается — удалить её можно после загрузки");
  const ctc = screen.getByRole("group", { name: "GigaAM CTC" });
  expect(within(ctc).getByRole("button", { name: "Удалить модель GigaAM CTC" })).toBeEnabled();
  expect(within(ctc).getByRole("button", { name: "Скачать" })).toBeEnabled();
});

test("подхватывает все идущие загрузки, а не одну", async () => {
  vi.mocked(api.getModels).mockResolvedValue(catalogue());
  vi.mocked(api.getJobs).mockResolvedValue({ items: [
    running("j1", "Systran/faster-whisper-small", 1), running("j2", "gigaam/v3_e2e_ctc", 3),
  ] });
  render(<ModelsPane endpoint={ep} />);
  const small = await screen.findByRole("group", { name: "Whisper small" });
  expect(await within(small).findByText("25 %")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "GigaAM CTC" })).getByText("75 %")).toBeInTheDocument();
});
