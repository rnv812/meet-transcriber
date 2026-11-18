/** «Настройки → Диагностика» на Windows: строк macOS (разрешения, уведомления) нет, резидент о разрешениях не спрашивается. */

import { render, screen } from "@testing-library/react";
import * as api from "../../lib/api";
import { DiagnosticsPane } from "./DiagnosticsPane";

vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  inTauri: () => true,
}));

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getDiagnostics: vi.fn(),
  getPermissions: vi.fn(),
}));

test("на Windows разрешений macOS нет", async () => {
  vi.mocked(api.getDiagnostics).mockResolvedValue({ watch_log: [], record_log: [], paths: {}, dev_mode: false });
  render(<DiagnosticsPane endpoint={{ base: "/api", token: null }} />);
  expect(await screen.findByText("Установленное приложение")).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Микрофон" })).toBeNull();
  expect(api.getPermissions).not.toHaveBeenCalled();
});

test("Windows: строки об уведомлениях macOS нет", async () => {
  vi.mocked(api.getDiagnostics).mockResolvedValue({ watch_log: [], record_log: [], paths: {}, dev_mode: false });
  render(<DiagnosticsPane endpoint={{ base: "/api", token: null }} />);
  expect(await screen.findByText("Установленное приложение")).toBeInTheDocument();
  expect(screen.queryByText("Уведомления")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Открыть настройки уведомлений" })).not.toBeInTheDocument();
});
