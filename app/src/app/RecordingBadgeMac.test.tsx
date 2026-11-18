/**
 * macOS: микрофон без разрешения отдаёт ровные нули — резидент присылает `mic_silent`, у рейки
 * предупреждение «Микрофон не слышно», по нажатию — раздел «Микрофон» в «Системных настройках».
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MIC_SILENT, RecordingWarnings } from "./RecordingBadge";
import * as shell from "../lib/shell";
import type { Snapshot } from "../lib/types";

vi.mock("../lib/platform", async (orig) => {
  const real = await orig<typeof import("../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});

const ep = { base: "/api", token: null };
afterEach(() => vi.restoreAllMocks());
const snap = (extra: Partial<Snapshot>): Snapshot => ({
  status: "recording", source: "manual", folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});

test("микрофон не слышно: по нажатию — раздел «Микрофон» в настройках", async () => {
  const open = vi.spyOn(shell, "openPrivacyPane").mockResolvedValue();
  render(<RecordingWarnings endpoint={ep} snapshot={snap({ mic_silent: true })} />);
  const warn = screen.getByRole("button", { name: "Микрофон не слышно — проверьте разрешение" });
  expect(MIC_SILENT).toBe("Микрофон не слышно — проверьте разрешение");
  expect(warn).toHaveAccessibleDescription(/Открыть настройки/);
  await userEvent.click(warn);
  expect(open).toHaveBeenCalledWith("microphone");
});

test("раздел не открылся — ошибка видна", async () => {
  vi.spyOn(shell, "openPrivacyPane").mockRejectedValue(new Error("нет доступа"));
  render(<RecordingWarnings endpoint={ep} snapshot={snap({ mic_silent: true })} />);
  await userEvent.click(screen.getByRole("button", { name: MIC_SILENT }));
  expect(await screen.findByRole("alert")).toHaveTextContent("нет доступа");
});
