import { createRef } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import * as api from "../../lib/api";
import { SPEAKER_RINGS, speakerTones } from "../../lib/tones";
import type { SpeakerRow, SpeakersView } from "../../lib/types";
import { TIP_DELAY_MS } from "../../ui/Tip";
import { CardHeader } from "./CardHeader";
import { SpeakersPanel } from "./speakers/SpeakersPanel";
import { Turns } from "./Turns";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSpeakers: vi.fn(),
}));

const ep = { base: "/api", token: null };
const rec = {
  id: "r1", path: "p", started_at: null, duration_s: 60, tracks: {}, has_transcript: true,
  has_voices: false, title: "Встреча", source: "record",
};
const people = [{ name: "Борис", color: "#c0793a", has_avatar: false, role: "CTO Acme" }];
const speakers = ["Анна", "Борис", "Спикер 3"];
const tones = speakerTones(speakers, people);
const tone = (el: Element | null | undefined) => (el as HTMLElement | null)?.style.getPropertyValue("--person") ?? "";

test("цвет спикера один: кольцо в шапке, точка в ленте и строка панели — из одной карты", async () => {
  render(<CardHeader rec={rec} speakers={speakers} people={people} tones={tones} endpoint={ep} onRename={() => {}} />);
  const chip = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) });
  expect(tone(chip("Анна").querySelector(".person-mark"))).toBe(SPEAKER_RINGS[0]);
  expect(tone(chip("Борис").querySelector(".person-mark"))).toBe("#c0793a");
  expect(tone(chip("Спикер 3").querySelector(".person-mark"))).toBe("");

  const turns = [
    { speaker: "Анна", start: 0, end: 2, texts: ["Привет"], uncertain: false },
    { speaker: "Спикер 3", start: 3, end: 4, texts: ["Да"], uncertain: false },
  ];
  const { container } = render(<Turns turns={turns} colors={tones} playable={false} onPlay={() => {}} />);
  const names = container.querySelectorAll(".turn__speaker");
  expect(tone(names[0])).toBe(SPEAKER_RINGS[0]);
  expect(tone(names[1])).toBe("");

  const row = (label: string, extra: Partial<SpeakerRow> = {}): SpeakerRow => ({
    label, name: label, seconds: 60, share: 0.4, turns: 3, samples: [], has_voice: true, suggestions: [], ...extra,
  });
  const view: SpeakersView = { owner: "Вы", history: [], pos: 0, speakers: [row("Борис"), row("Анна"), row("Спикер 3")] };
  vi.mocked(api.getSpeakers).mockResolvedValue(view);
  render(<SpeakersPanel endpoint={ep} recordingId="r1" people={people} tones={tones} open focus={null} version={1}
    playable cardRef={createRef<HTMLElement>()} onClose={() => {}} onPlay={() => {}} onShowTurns={() => {}}
    onChanged={() => {}} />);
  const region = async (label: string) => screen.findByRole("region", { name: new RegExp(`^${label}`) });
  // Порядок в панели — по доле, цвет — тот же, что в шапке (по имени, а не по месту в панели).
  expect(tone(await region("Анна"))).toBe(SPEAKER_RINGS[0]);
  expect(tone(await region("Борис"))).toBe("#c0793a");
  expect(tone(await region("Спикер 3"))).toBe("");
  expect((await region("Анна")).querySelector(".person-mark")).not.toBeNull();
});

test("чип человека в шапке: «Кто это» из базы голосов — в подсказке; без роли — прежняя «Кто это?»", () => {
  vi.useFakeTimers();
  try {
    render(<CardHeader rec={rec} speakers={speakers} people={people} tones={tones} endpoint={ep} onRename={() => {}} />);
    const boris = screen.getByRole("button", { name: "Борис" });
    expect(boris).not.toHaveAttribute("title");
    expect(boris).toHaveAccessibleDescription("CTO Acme");
    fireEvent.mouseEnter(boris);
    act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
    expect(document.body.querySelector(".tooltip.tip")).toHaveTextContent("CTO Acme");
    expect(screen.getByRole("button", { name: "Анна" })).toHaveAccessibleDescription("Кто это?");
  } finally {
    vi.useRealTimers();
  }
});

test("панель спикеров: «Показать все реплики» — тихая кнопка Aurora, действия строки — ряд от края карточки", async () => {
  const view: SpeakersView = {
    owner: "Вы", history: [], pos: 0, speakers: [
      { label: "Спикер 1", name: null, seconds: 60, share: 0.6, turns: 4, samples: [], has_voice: true, suggestions: [] },
      { label: "Спикер 2", name: null, seconds: 30, share: 0.4, turns: 2, samples: [], has_voice: true, suggestions: [] },
    ],
  };
  vi.mocked(api.getSpeakers).mockResolvedValue(view);
  render(<SpeakersPanel endpoint={ep} recordingId="r1" people={[]} open focus={null} version={1}
    playable cardRef={createRef<HTMLElement>()} onClose={() => {}} onPlay={() => {}} onShowTurns={() => {}}
    onChanged={() => {}} />);
  const first = await screen.findByRole("region", { name: /^Спикер 1/ });
  expect(within(first).getByRole("button", { name: "Показать все реплики" })).toHaveClass("btn", "btn--ghost", "btn--sm");
  const merge = within(first).getByRole("button", { name: "Объединить с…" });
  expect(merge.closest(".ctl-row")).not.toBeNull();
  expect(within(first).getByRole("button", { name: "Разделить…" }).closest(".ctl-row")).toBe(merge.closest(".ctl-row"));
});
