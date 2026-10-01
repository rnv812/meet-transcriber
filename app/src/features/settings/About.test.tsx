import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getDiagnostics: vi.fn(async () => ({ paths: { data_dir: "C:\data\meet" } })),
}));
vi.mock("../../lib/shell", () => ({ openUrl: vi.fn(async () => {}) }));

import * as shell from "../../lib/shell";
import { About, RELEASES_URL } from "./About";

const endpoint = { base: "http://127.0.0.1:1", token: "t" };

test("«Скачать новую версию» открывает Releases форка в браузере", async () => {
  render(<About endpoint={endpoint} />);
  await waitFor(() => expect(screen.getByText("C:\data\meet")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "Скачать новую версию" }));
  expect(shell.openUrl).toHaveBeenCalledWith("https://github.com/rnv812/ai_transcriber/releases");
  expect(RELEASES_URL).toBe("https://github.com/rnv812/ai_transcriber/releases");
});
