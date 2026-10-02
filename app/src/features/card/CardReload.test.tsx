/**
 * Перечитывание карточки (шаг задачи анализа, выгрузка в базу знаний) не
 * сбрасывает начатую правку, если сама расшифровка не изменилась: окно
 * «Исправить…» с введённым текстом, выбранные реплики, меню спикера,
 * «Разделить реплику здесь». Изменилась — сбрасывает (номера сдвинулись).
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingCard } from "./RecordingCard";
import * as api from "../../lib/api";
import { keepTranscript, sameSegments } from "../../lib/sameTranscript";
import type { Job, Recording, TextPreview, Transcript } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(),
  getSpeakers: vi.fn(),
  previewTextFix: vi.fn(),
  getAnalysis: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => false,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
  agentKillRecording: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const seg = (start: number, end: number, speaker: string, text: string) =>
  ({ start, end, speaker, text, uncertain: false });
const transcript: Transcript = {
  version: 1, title: null,
  segments: [
    seg(0, 4, "Спикер 1", "Поднимем кубер нетис на стенде."),
    { ...seg(4.5, 6, "Спикер 1", "Проверим."), has_words: true },
    seg(10, 12, "Спикер 2", "Кубер нетис готов."),
  ],
};
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 60,
  tracks: {}, has_transcript: true, has_voices: true, title: "Встреча", source: "record",
};
const preview: TextPreview = {
  count: 2, here: { start: 0.6, end: 1.6 },
  samples: [
    { segment: 0, offset: 9, speaker: "Спикер 1", start: 0.6, end: 1.6, before: "Поднимем ", match: "кубер нетис", after: " на стенде." },
  ],
};
const job = (state: string) => ({ id: "a1", kind: "analyze", folder: "C:/rec/r1", state, stage: null, label: null,
  done: null, total: null, note: null }) as unknown as Job;

let served: Transcript = transcript;

beforeEach(() => {
  vi.clearAllMocks();
  served = transcript;
  // Как JSON с резидента: каждый ответ — новый объект.
  vi.mocked(api.getRecording).mockImplementation(async () => structuredClone({ ...rec, transcript: served, edit_head: "t1" }));
  vi.mocked(api.getSettings).mockResolvedValue({ recording: { speaker_name: "Вы" } });
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: null, setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getSpeakers).mockResolvedValue({ owner: "Вы", history: [], pos: 0, speakers: [] });
  vi.mocked(api.previewTextFix).mockResolvedValue(preview);
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "none" });
});

function select(p: HTMLElement, start: number, end: number) {
  const text = p.firstChild!;
  const range = document.createRange();
  range.setStart(text, start);
  range.setEnd(text, end);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
}

/** Задача анализа сменила состояние: карточка перечитывается (ждём ответа). */
async function reloadWith(rerender: (ui: React.ReactElement) => void, state: string) {
  const before = vi.mocked(api.getRecording).mock.calls.length;
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job(state)]} />);
  await vi.waitFor(() => expect(vi.mocked(api.getRecording).mock.calls.length).toBeGreaterThan(before));
  await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
}

test("«Исправить…» с введённым текстом переживает перечитывание карточки", async () => {
  const { rerender } = render(<RecordingCard id="r1" endpoint={ep} jobs={[job("queued")]} />);
  const p = (await screen.findByText(/Поднимем кубер нетис/)).closest("p")!;
  select(p, 9, 20);
  fireEvent.mouseUp(p);
  await userEvent.click(await screen.findByRole("button", { name: "Исправить…" }));
  const box = await screen.findByRole("dialog", { name: "Исправить распознанное" });
  const input = within(box).getByRole("textbox", { name: "Как правильно" });
  await userEvent.clear(input);
  await userEvent.type(input, "Kuber");
  await reloadWith(rerender, "running");
  await reloadWith(rerender, "done");
  const still = screen.getByRole("dialog", { name: "Исправить распознанное" });
  expect(within(still).getByRole("textbox", { name: "Как правильно" })).toHaveValue("Kuber");
});

test("выбранные реплики и меню спикера переживают перечитывание", async () => {
  const { container, rerender } = render(<RecordingCard id="r1" endpoint={ep} jobs={[job("queued")]} />);
  await screen.findByText(/Поднимем кубер нетис/);
  const rows = () => [...container.querySelectorAll(".turn")] as HTMLElement[];
  fireEvent.click(rows()[0]!, { ctrlKey: true });
  fireEvent.click(rows()[1]!, { shiftKey: true });
  expect(rows().filter((r) => r.dataset.selected)).toHaveLength(2);
  await reloadWith(rerender, "running");
  expect(rows().filter((r) => r.dataset.selected)).toHaveLength(2);

  const speaker = screen.getAllByRole("button", { name: /^Спикер 2$/ }).find((b) => b.classList.contains("turn__speaker"))!;
  await userEvent.click(speaker);
  await screen.findByRole("dialog", { name: "Кому отдать реплики" });
  await reloadWith(rerender, "done");
  expect(screen.getByRole("dialog", { name: "Кому отдать реплики" })).toBeInTheDocument();
});

test("«Разделить реплику здесь» переживает перечитывание, а смена текста его закрывает", async () => {
  const { rerender } = render(<RecordingCard id="r1" endpoint={ep} jobs={[job("queued")]} />);
  const p = (await screen.findByText(/Поднимем кубер нетис/)).closest("p")!;
  const text = p.firstChild!;
  (document as unknown as { caretRangeFromPoint: unknown }).caretRangeFromPoint = () => {
    const r = document.createRange();
    r.setStart(text, 34);
    return r;
  };
  fireEvent.contextMenu(p, { clientX: 10, clientY: 10 });
  await screen.findByRole("dialog", { name: "Разделить реплику здесь" });
  await reloadWith(rerender, "running");
  expect(screen.getByRole("dialog", { name: "Разделить реплику здесь" })).toBeInTheDocument();

  served = { ...transcript, segments: transcript.segments.map((s, i) => (i === 2 ? { ...s, text: "Kubernetes готов." } : s)) };
  await reloadWith(rerender, "done");
  await screen.findByText(/Kubernetes готов/);
  expect(screen.queryByRole("dialog", { name: "Разделить реплику здесь" })).toBeNull();
});

test("keepTranscript: та же расшифровка — прежний объект, другая — новый", () => {
  const copy = structuredClone(transcript);
  expect(keepTranscript(transcript, copy)).toBe(transcript);
  const renamed = { ...copy, names: { "Спикер 1": "Анна" } };
  const kept = keepTranscript(transcript, renamed)!;
  expect(kept).not.toBe(transcript);
  expect(kept.segments).toBe(transcript.segments);
  expect(kept.names).toEqual({ "Спикер 1": "Анна" });
  const moved = { ...copy, segments: copy.segments.map((s, i) => (i === 0 ? { ...s, speaker: "Спикер 2" } : s)) };
  expect(keepTranscript(transcript, moved)).toBe(moved);
  expect(sameSegments(transcript.segments, copy.segments.slice(1))).toBe(false);
  expect(keepTranscript(null, copy)).toBe(copy);
  expect(keepTranscript(transcript, null)).toBeNull();
});
