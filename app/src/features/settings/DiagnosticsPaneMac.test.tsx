/** «Настройки → Диагностика» на macOS: те же строки разрешений, что в мастере. Данные выдуманные. */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as api from "../../lib/api";
import { DiagnosticsPane } from "./DiagnosticsPane";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getDiagnostics: vi.fn(),
  getPermissions: vi.fn(),
  requestPermission: vi.fn(),
}));

const ep = { base: "/api", token: null };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getDiagnostics).mockResolvedValue({ watch_log: [], record_log: [], paths: {} });
  vi.mocked(api.getPermissions).mockResolvedValue({ supported: true, microphone: "granted", screen: "denied" });
});

test("строки «Микрофон» и «Запись экрана» с состоянием и кнопками; фокус окна перечитывает", async () => {
  render(<DiagnosticsPane endpoint={ep} />);
  const mic = await screen.findByRole("group", { name: "Микрофон" });
  expect(await within(mic).findByText("Разрешено")).toBeInTheDocument();
  const scr = screen.getByRole("group", { name: "Запись экрана" });
  expect(within(scr).getByText("Не разрешено")).toBeInTheDocument();
  expect(within(scr).getByRole("button", { name: "Разрешить" })).toBeInTheDocument();
  expect(within(scr).getByRole("button", { name: "Открыть настройки" })).toBeInTheDocument();
  vi.mocked(api.getPermissions).mockResolvedValue({ supported: true, microphone: "granted", screen: "granted" });
  fireEvent.focus(window);
  await waitFor(() => expect(within(screen.getByRole("group", { name: "Запись экрана" })).getByText("Разрешено")).toBeInTheDocument());
});
