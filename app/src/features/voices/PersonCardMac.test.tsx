/** Образец в «Голосах» на macOS — тот же кусок дорожки, но в WebM. */
import { fireEvent, render, screen } from "@testing-library/react";
import * as api from "../../lib/api";
import type { Person } from "../../lib/types";
import { PersonCard } from "./PersonCard";

vi.mock("../../lib/platform", async (orig) => {
  const real = await orig<typeof import("../../lib/platform")>();
  return { ...real, OS: "macos", IS_MAC: true, OS_TEXT: real.osTexts("macos") };
});
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPerson: vi.fn(),
  getSample: vi.fn(),
}));

const ep = { base: "/api", token: null };
const person: Person = { name: "Демьян", samples: 3, meetings: 1, seconds: 600, has_avatar: false, color: "#4b6bd6" };
const noop = () => {};

test("▶ образца на macOS просит дорожку в WebM", async () => {
  vi.mocked(api.getPerson).mockResolvedValue({ name: "Демьян", color: "#000", has_avatar: false, samples: 3, meetings: [] });
  vi.mocked(api.getSample).mockResolvedValue({
    recording: "r1", start: 12, end: 16.2, track: "sys", text: "Склад готов.", words: [],
  });
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.load = vi.fn();
  render(<PersonCard endpoint={ep} person={person} others={[]} onAvatar={noop} onRenamed={noop} onRemoved={noop}
    onOpenRecording={noop} />);
  fireEvent.click(await screen.findByRole("button", { name: "Прослушать образец" }));
  expect(document.querySelector("audio")!.getAttribute("src"))
    .toBe("/api/recordings/r1/audio?track=sys&format=webm#t=12,16.2");
});
