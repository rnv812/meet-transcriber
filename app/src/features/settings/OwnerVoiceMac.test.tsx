/** «Мой голос» на macOS: пример из прошлой встречи — тот же кусок микрофона, но в WebM. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import type { OwnerVoiceStatus } from "../../lib/types";
import { OwnerVoiceRow } from "./OwnerVoice";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getOwnerVoice: vi.fn(),
}));

const ep = { base: "/api", token: null };

test("▶ примера на macOS просит микрофон в WebM", async () => {
  const status: OwnerVoiceStatus = {
    samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25,
    suggestion: {
      meetings: ["2026-10-03_10-00"],
      samples: [{ recording: "2026-10-03_10-00", start: 12, end: 16.5, track: "mic" }],
      seconds: 412, quality: 0.83, date: "2026-10-06",
    },
    derive: { running: false, error: null, last: null },
  };
  vi.mocked(api.getOwnerVoice).mockResolvedValue(status);
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.load = vi.fn();
  render(<OwnerVoiceRow endpoint={ep} device={null} />);
  await userEvent.click(await screen.findByRole("button", { name: /^Послушать пример 1/ }));
  expect(document.querySelector("audio")!.getAttribute("src"))
    .toBe("/api/recordings/2026-10-03_10-00/audio?track=mic&format=webm#t=12,16.5");
});
