import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StorageMissing } from "./StorageMissing";
import * as shell from "../../lib/shell";

vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  storageStatus: vi.fn(),
  storageReset: vi.fn(async () => {}),
  storageRetry: vi.fn(async () => {}),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(shell.storageStatus).mockResolvedValue({
    root: "/Volumes/Ext/Meet", home: "/Volumes/Ext/Meet", default_home: "/Users/u/Library/Application Support/meet",
    missing: "/Volumes/Ext/Meet", moving: false, cancellable: false,
  });
});

test("папки нет — путь, «Повторить» и «Вернуть на системный диск», без молчаливой переустановки", async () => {
  render(<StorageMissing />);
  expect(await screen.findByText(/\/Volumes\/Ext\/Meet не найдена/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(shell.storageRetry).toHaveBeenCalled();
  expect(shell.storageReset).not.toHaveBeenCalled();
});

test("«Вернуть на системный диск» — только после подтверждения", async () => {
  render(<StorageMissing />);
  await userEvent.click(await screen.findByRole("button", { name: "Вернуть на системный диск" }));
  expect(shell.storageReset).not.toHaveBeenCalled();
  expect(screen.getByText(/Движок придётся установить заново/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Вернуть" }));
  await waitFor(() => expect(shell.storageReset).toHaveBeenCalled());
});

test("сбой — текст ошибки", async () => {
  vi.mocked(shell.storageReset).mockRejectedValueOnce("Не удалось снять выбор папки: доступ запрещён");
  render(<StorageMissing />);
  await userEvent.click(await screen.findByRole("button", { name: "Вернуть на системный диск" }));
  await userEvent.click(screen.getByRole("button", { name: "Вернуть" }));
  expect(await screen.findByText(/доступ запрещён/)).toBeInTheDocument();
});
