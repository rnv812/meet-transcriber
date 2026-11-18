/**
 * «Запускать при входе в macOS»: переключатель показывает, есть ли LaunchAgent, а если
 * macOS выключила Meet в «Объектах входа» — строка с кнопкой в этот раздел настроек.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutostartRow } from "./AutostartRow";
import * as shell from "../../lib/shell";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});

vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  autostartAvailable: vi.fn(async () => true),
  getAutostart: vi.fn(async () => true),
  setAutostart: vi.fn(async () => {}),
  autostartBlocked: vi.fn(async () => false),
  openPrivacyPane: vi.fn(async () => {}),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(shell.autostartAvailable).mockResolvedValue(true);
  vi.mocked(shell.getAutostart).mockResolvedValue(true);
  vi.mocked(shell.setAutostart).mockResolvedValue(undefined);
  vi.mocked(shell.autostartBlocked).mockResolvedValue(false);
});

const TOGGLE = "Запускать при входе в macOS";

test("выключено в «Объектах входа» — строка с кнопкой в этот раздел", async () => {
  vi.mocked(shell.autostartBlocked).mockResolvedValue(true);
  render(<AutostartRow />);
  expect(await screen.findByRole("switch", { name: TOGGLE })).toBeChecked();
  expect(await screen.findByText(/macOS не откроет Meet при входе/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Открыть «Объекты входа»" }));
  expect(shell.openPrivacyPane).toHaveBeenCalledWith("login_items");
});

test("система не запрещала — строки нет", async () => {
  render(<AutostartRow />);
  await screen.findByRole("switch", { name: TOGGLE });
  await waitFor(() => expect(shell.autostartBlocked).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Открыть «Объекты входа»" })).not.toBeInTheDocument();
});

test("выключили в Meet — строки о запрете нет, и после включения состояние проверяется снова", async () => {
  vi.mocked(shell.autostartBlocked).mockResolvedValue(true);
  render(<AutostartRow />);
  const toggle = await screen.findByRole("switch", { name: TOGGLE });
  await screen.findByRole("button", { name: "Открыть «Объекты входа»" });
  vi.mocked(shell.autostartBlocked).mockResolvedValue(false);
  await userEvent.click(toggle);
  expect(shell.setAutostart).toHaveBeenCalledWith(false);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Открыть «Объекты входа»" })).not.toBeInTheDocument());
  const calls = vi.mocked(shell.autostartBlocked).mock.calls.length;
  await userEvent.click(toggle);
  await waitFor(() => expect(vi.mocked(shell.autostartBlocked).mock.calls.length).toBeGreaterThan(calls));
});
