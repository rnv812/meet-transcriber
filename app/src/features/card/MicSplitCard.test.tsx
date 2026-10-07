/**
 * Карточка записи звонка с микрофоном по голосам (0.3.3): «в комнате» в
 * расшифровке, подсказка по статусу разделения и «Показать» убранные повторы
 * (открывает панель «Спикеры»).
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import type { Recording, SpeakersView, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(),
  getAnalysis: vi.fn(),
  getSpeakers: vi.fn(),
  getOwnerVoice: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => false,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
  agentKillRecording: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const transcript: Transcript = {
  version: 1, title: null,
  segments: [
    { start: 0, end: 4, speaker: "Спикер 1", text: "Начинаем планёрку.", uncertain: false },
    { start: 5, end: 7, speaker: "Вы", text: "Да, слышно.", uncertain: false, track: "mic" },
    { start: 9, end: 11, speaker: "Спикер 2", text: "А машина есть?", uncertain: false, track: "mic", room: true },
  ],
};
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-10-05T10:00:00", duration_s: 60,
  tracks: {}, has_transcript: true, has_voices: true, title: "Встреча", source: "record",
};
const speakersView: SpeakersView = {
  owner: "Вы", history: [], pos: 0, speakers: [],
  mic_removed: [{ start: 20, end: 21, text: "всем привет", reason: "neighbour", track: "mic" }],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue({ analysis: { auto: false, consent: "granted" } });
  vi.mocked(api.getAssistant).mockResolvedValue(
    { provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "none" });
  vi.mocked(api.getSpeakers).mockResolvedValue(speakersView);
  vi.mocked(api.getOwnerVoice).mockResolvedValue(
    { samples: [], take: null, ready: true, reason: null, recording: false, seconds: 25 });
});

test("человек рядом с вами — «в комнате»; без образца голоса — подсказка записать его", async () => {
  vi.mocked(api.getRecording).mockResolvedValue({
    ...rec, transcript, mic_split: { status: "no_profile", room_speakers: 0, dropped: {} } });
  const onOpenSettings = vi.fn();
  render(<RecordingCard id="r1" endpoint={ep} onOpenSettings={onOpenSettings} />);
  const turn = (await screen.findByText(/А машина есть/)).closest("[data-turn]") as HTMLElement;
  expect(within(turn).getByText("в комнате")).toBeInTheDocument();
  const mine = screen.getByText(/Да, слышно/).closest("[data-turn]") as HTMLElement;
  expect(within(mine).queryByText("в комнате")).toBeNull();
  // Заметная строка, а не тихая заметка: «Записать образец» сразу открывает окно записи с текстом.
  const nudge = screen.getByRole("note", { name: "Образец голоса" });
  expect(nudge).toHaveClass("ownv-nudge");
  expect(nudge).toHaveTextContent(/Микрофон не разделён на голоса.*Запишите образец своего голоса — прочитайте вслух/);
  await userEvent.click(within(nudge).getByRole("button", { name: "Записать образец" }));
  const dialog = screen.getByRole("dialog", { name: "Мой голос" });
  expect(await within(dialog).findByText(/Утро выдалось тихим/)).toBeInTheDocument();
  expect(onOpenSettings).not.toHaveBeenCalled();
});

test("убранные повторы: строка в карточке, «Показать» открывает панель со списком", async () => {
  vi.mocked(api.getRecording).mockResolvedValue({
    ...rec, transcript, mic_split: { status: "ok", room_speakers: 1, dropped: { neighbour: 1 } } });
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText(/А машина есть/);
  expect(screen.getByText(/С микрофона убраны повторы: 1 дубль соседа/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Показать" }));
  const panel = await screen.findByRole("dialog", { name: "Спикеры встречи" });
  const box = await within(panel).findByRole("group", { name: "Убрано с микрофона" });
  // Список сразу раскрыт: второе «Показать» не нужно.
  await waitFor(() => expect(within(box).getByText("всем привет")).toBeInTheDocument());
});
