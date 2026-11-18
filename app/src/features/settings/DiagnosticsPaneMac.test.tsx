/**
 * «Настройки → Диагностика» на macOS: те же строки разрешений, что в мастере, и строка
 * «Уведомления» с кнопкой в раздел уведомлений «Системных настроек» (разрешены ли они, Meet
 * узнать не может: плагин уведомлений на компьютере всегда отвечает «разрешены»). Данные выдуманные.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import * as shell from "../../lib/shell";
import { DiagnosticsPane } from "./DiagnosticsPane";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: () => true,
  openPrivacyPane: vi.fn(async () => {}),
}));
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

test("строка «Уведомления» открывает их раздел в «Системных настройках»", async () => {
  render(<DiagnosticsPane endpoint={ep} />);
  expect(await screen.findByText("Уведомления")).toBeInTheDocument();
  expect(screen.getByText(/разрешены ли они, видно в «Системных настройках»/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Открыть настройки уведомлений" }));
  expect(shell.openPrivacyPane).toHaveBeenCalledWith("notifications");
});
