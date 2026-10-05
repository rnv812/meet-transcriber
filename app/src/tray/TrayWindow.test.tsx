import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../lib/api";

const shell = vi.hoisted(() => ({
  hide: vi.fn(async () => {}),
  fit: vi.fn(async (_h: number) => {}),
  open: vi.fn(async (_t: unknown) => {}),
}));
vi.mock("../lib/shell", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/shell")>()),
  trayPanelHide: shell.hide,
  trayPanelFit: shell.fit,
  trayPanelOpen: shell.open,
}));

const { TrayWindow } = await import("./TrayWindow");

beforeEach(() => {
  vi.spyOn(api, "getRecentRecordings").mockResolvedValue({ root: "", items: [] });
  vi.spyOn(api, "getAssistant").mockRejectedValue(new Error("нет"));
  shell.hide.mockClear();
  shell.fit.mockClear();
  shell.open.mockClear();
});
afterEach(() => vi.restoreAllMocks());

test("панель — диалог с фокусом; Esc прячет её, высота уходит оболочке", async () => {
  render(<TrayWindow />);
  const dialog = screen.getByRole("dialog", { name: "Запись Meet" });
  expect(dialog).toHaveFocus();
  expect(shell.fit).toHaveBeenCalled();
  await userEvent.keyboard("{Escape}");
  expect(shell.hide).toHaveBeenCalledTimes(1);
});

test("«Открыть Meet» уходит оболочке", async () => {
  render(<TrayWindow />);
  await userEvent.click(screen.getByRole("button", { name: "Открыть Meet" }));
  await waitFor(() => expect(shell.open).toHaveBeenCalledWith({}));
});
