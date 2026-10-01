import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AutostartRow } from "./AutostartRow";
import * as shell from "../../lib/shell";

vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  autostartAvailable: vi.fn(async () => true),
  getAutostart: vi.fn(async () => true),
  setAutostart: vi.fn(async () => {}),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(shell.autostartAvailable).mockResolvedValue(true);
  vi.mocked(shell.getAutostart).mockResolvedValue(true);
  vi.mocked(shell.setAutostart).mockResolvedValue(undefined);
});

test("показывает сохранённый выбор и переключает сразу, без «Сохранить»", async () => {
  render(<AutostartRow />);
  const toggle = await screen.findByRole("switch", { name: "Запускать вместе с Windows" });
  expect(toggle).toBeChecked();
  await userEvent.click(toggle);
  expect(shell.setAutostart).toHaveBeenCalledWith(false);
  expect(toggle).not.toBeChecked();
});

test("выбора не было — выключен", async () => {
  vi.mocked(shell.getAutostart).mockResolvedValue(null);
  render(<AutostartRow />);
  expect(await screen.findByRole("switch", { name: "Запускать вместе с Windows" })).not.toBeChecked();
});

test("не переключилось — значение возвращается, текст ошибки", async () => {
  vi.mocked(shell.setAutostart).mockRejectedValue("Не удалось изменить автозапуск: доступ запрещён");
  render(<AutostartRow />);
  const toggle = await screen.findByRole("switch", { name: "Запускать вместе с Windows" });
  await userEvent.click(toggle);
  expect(await screen.findByText("Не удалось изменить автозапуск: доступ запрещён")).toBeInTheDocument();
  expect(toggle).toBeChecked();
});

test("оболочка без автозапуска (или браузер) — строки нет", async () => {
  vi.mocked(shell.autostartAvailable).mockResolvedValue(false);
  const { container } = render(<AutostartRow />);
  await waitFor(() => expect(shell.autostartAvailable).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});
