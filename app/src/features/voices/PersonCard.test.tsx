/**
 * Карточка человека в «Голосах»: образец фразы — начало и длительность,
 * «Остановить» во время игры, остановка точно на конце; нет образца — без ▶.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import * as api from "../../lib/api";
import type { Person } from "../../lib/types";
import { PersonCard } from "./PersonCard";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getPerson: vi.fn(),
  getSample: vi.fn(),
}));

const ep = { base: "/api", token: null };
const person: Person = { name: "Демьян", samples: 3, meetings: 1, seconds: 600, has_avatar: false, color: "#4b6bd6" };
const noop = () => {};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getPerson).mockResolvedValue({ name: "Демьян", color: "#000", has_avatar: false, samples: 3, meetings: [] });
  vi.mocked(api.getSample).mockResolvedValue({
    recording: "r1", start: 12, end: 16.2, track: "sys", text: "Склад готов.", words: [],
  });
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.load = vi.fn();
});
afterEach(() => { vi.useRealTimers(); });

const card = () => render(
  <PersonCard endpoint={ep} person={person} others={[]} onAvatar={noop} onRenamed={noop} onRemoved={noop}
    onOpenRecording={noop} />);

test("образец: начало и длительность, «Остановить» во время игры, остановка точно на конце", async () => {
  card();
  const play = await screen.findByRole("button", { name: "Прослушать образец" });
  expect(screen.getByText("00:12 · 4 с")).toBeInTheDocument();
  expect(api.getSample).toHaveBeenCalledWith(ep, "Демьян");
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  fireEvent.click(play);
  const audio = document.querySelector("audio")!;
  expect(audio.getAttribute("src")).toBe("/api/recordings/r1/audio?track=sys#t=12,16.2");
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Остановить" })).toBeInTheDocument();
  audio.currentTime = 12;
  Object.defineProperty(audio, "paused", { configurable: true, value: false });
  fireEvent.playing(audio);
  act(() => { vi.advanceTimersByTime(4199); });
  expect(HTMLMediaElement.prototype.pause).not.toHaveBeenCalled();
  act(() => { vi.advanceTimersByTime(1); });
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Прослушать образец" })).toBeInTheDocument();
});

test("«Остановить» останавливает образец сразу", async () => {
  card();
  fireEvent.click(await screen.findByRole("button", { name: "Прослушать образец" }));
  fireEvent.click(screen.getByRole("button", { name: "Остановить" }));
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Прослушать образец" })).toBeInTheDocument();
});

test("образца нет — подпись без ▶ и без ошибки", async () => {
  vi.mocked(api.getSample).mockRejectedValue(new api.ApiError(404, "образца нет"));
  card();
  expect(await screen.findByText("нет подходящей фразы")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Прослушать образец" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});
