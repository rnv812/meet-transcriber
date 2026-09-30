import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingBadge } from "./RecordingBadge";
import * as api from "../lib/api";
import type { Snapshot } from "../lib/types";

const ep = { base: "/api", token: null };
const snap = (extra: Partial<Snapshot>): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
});

test("при записи: REC, таймер, Стоп вызывает recordingCommand stop", async () => {
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({ status: "recording", source: "manual", elapsed_s: 754 })} />);
  expect(screen.getByText(/REC/)).toHaveTextContent("12:34");
  await userEvent.click(screen.getByRole("button", { name: /Стоп/ }));
  expect(spy).toHaveBeenCalledWith(ep, "stop");
});

test("автозапись помечена «авто»", () => {
  render(<RecordingBadge endpoint={ep} snapshot={snap({ status: "recording", source: "auto", elapsed_s: 5 })} />);
  expect(screen.getByText("авто")).toBeInTheDocument();
});

test("вне записи — Начать запись; мало места — предупреждение", async () => {
  const spy = vi.spyOn(api, "recordingCommand").mockResolvedValue({} as never);
  render(<RecordingBadge endpoint={ep} snapshot={snap({ disk_free_gb: 3.2 })} />);
  expect(screen.getByText("Мало места: 3.2 ГБ")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Начать запись" }));
  expect(spy).toHaveBeenCalledWith(ep, "start");
});
