import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import * as api from "../../lib/api";
import type { Raw } from "./Section";
import { SpeakersSection } from "./SpeakersSection";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getOwnerVoice: vi.fn(),
  getHfStatus: vi.fn(),
}));

const ep = { base: "/api", token: null };

function Harness({ initial, onDraft }: { initial: Raw; onDraft?: (d: Raw) => void }) {
  const [draft, setDraft] = useState<Raw>(initial);
  return (
    <SpeakersSection draft={draft} endpoint={ep}
      set={(g, k, v) => setDraft((cur) => {
        const next = { ...cur, [g]: { ...(cur[g] ?? {}), [k]: v } };
        onDraft?.(next);
        return next;
      })} />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getOwnerVoice).mockResolvedValue({
    samples: [{ id: "s1", source: "enroll", date: "2026-10-05", seconds: 21, device: "USB-микрофон",
      recording: null, quality: 0.8 }],
    take: null, ready: true, reason: null, recording: false, seconds: 25,
  });
  vi.mocked(api.getHfStatus).mockResolvedValue({ configured: false, source: null, check: null });
});

test("«Мой голос» (из «Звука»): что записано и «Перезаписать» — для микрофона из черновика", async () => {
  render(<Harness initial={{ recording: { mic_device: { name: "USB-микрофон" } }, asr: {} }} />);
  const group = screen.getByRole("group", { name: "Мой голос" });
  expect(await within(group).findByText("записан 05.10 · USB-микрофон")).toBeInTheDocument();
  expect(within(group).getByRole("button", { name: "Перезаписать" })).toBeInTheDocument();
  expect(within(group).getByRole("button", { name: "Удалить образец: записан 05.10 · USB-микрофон" }))
    .toBeInTheDocument();
});

test("токен Hugging Face (из «Движка и моделей») — в разделе, со статусом", async () => {
  render(<Harness initial={{ asr: {} }} />);
  const row = screen.getByRole("group", { name: "Токен Hugging Face" });
  expect(await within(row).findByText(/Не задан/)).toBeInTheDocument();
});

test("порог узнавания и одновременная речь пишутся в asr", async () => {
  const drafts: Raw[] = [];
  render(<Harness initial={{ asr: { voice_threshold: 0.75, overlap: true } }} onDraft={(d) => drafts.push(d)} />);
  fireEvent.change(screen.getByRole("slider", { name: /Порог узнавания голоса/ }), { target: { value: "90" } });
  expect(screen.getByText("90%")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("switch", { name: "Отмечать одновременную речь" }));
  expect(drafts.at(-1)?.asr).toEqual({ voice_threshold: 0.9, overlap: false });
});
