import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelsPane } from "./ModelsPane";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getModels: vi.fn(),
  getJobs: vi.fn(),
  getHfStatus: vi.fn(),
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
  render(<ModelsPane endpoint={ep} selectedModel={null} onSelect={() => {}} />);
  expect(await screen.findByText(failed, { exact: false })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(shell.retryGigaamInstall).toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByText(failed, { exact: false })).toBeNull());
});

test("повтор не удался — понятная ошибка, причина остаётся", async () => {
  vi.mocked(api.getModels).mockResolvedValue(state(failed));
  vi.mocked(shell.retryGigaamInstall).mockRejectedValue("нет связи с GitHub — проверьте подключение или прокси в настройках");
  render(<ModelsPane endpoint={ep} selectedModel={null} onSelect={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Повторить" }));
  expect(await screen.findByText(/^GigaAM не установилась: нет связи с GitHub — проверьте/)).toBeInTheDocument();
});

test("вне приложения кнопки «Повторить» нет; без ошибки — и строки нет", async () => {
  vi.mocked(shell.inTauri).mockReturnValue(false);
  vi.mocked(api.getModels).mockResolvedValue(state(failed));
  const { unmount } = render(<ModelsPane endpoint={ep} selectedModel={null} onSelect={() => {}} />);
  expect(await screen.findByText(failed, { exact: false })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Повторить" })).toBeNull();
  unmount();
  vi.mocked(api.getModels).mockResolvedValue(state(null));
  render(<ModelsPane endpoint={ep} selectedModel={null} onSelect={() => {}} />);
  await screen.findByText("Папка моделей");
  expect(screen.queryByText(/GigaAM не установилась/)).toBeNull();
});
