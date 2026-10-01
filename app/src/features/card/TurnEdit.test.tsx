import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  getSpeakers: vi.fn(),
  relabelTurns: vi.fn(),
  splitTurn: vi.fn(),
  undoSpeakers: vi.fn(),
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
// Реплики: [0] Спикер 1 (сегменты 0–1), [1] Спикер 1 после паузы (2), [2] Спикер 2 (3), [3] Спикер 1 (4).
const transcript: Transcript = {
  version: 1, title: null,
  segments: [
    seg(0, 4, "Спикер 1", "Начинаем планёрку."),
    seg(4.5, 6, "Спикер 1", "Первый пункт."),
    seg(10, 12, "Спикер 1", "Второй пункт."),
    seg(12.5, 14, "Спикер 2", "Склад готов."),
    seg(15, 17, "Спикер 1", "Хорошо."),
  ],
};
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 60,
  tracks: { sys: "s.opus" }, has_transcript: true, has_voices: true, title: "Встреча", source: "record",
};
const view: SpeakersView = { owner: "Вы", history: [], pos: 1, speakers: [],
  step: { id: "s", at: "2026-09-30T10:00:00", enrolled: [], created_people: [],
    ops: [{ type: "relabel", from: ["Спикер 1"], to: "Анна Смирнова", segments: 2, turns: 1 }] } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript });
  vi.mocked(api.getSettings).mockResolvedValue({ recording: { speaker_name: "Вы" } });
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: null, setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getSpeakers).mockResolvedValue({ owner: "Вы", history: [], pos: 0, speakers: [] });
  vi.mocked(api.relabelTurns).mockResolvedValue(view);
  vi.mocked(api.undoSpeakers).mockResolvedValue({ ...view, pos: 0 });
});

const people = [{ name: "Анна Смирнова", color: "#a33", has_avatar: false }];
const speakerButtons = () => screen.getAllByRole("button", { name: /^Спикер [12]$/ })
  .filter((b) => b.classList.contains("turn__speaker"));

test("меню реплики: только эта реплика — человек из базы", async () => {
  render(<RecordingCard id="r1" endpoint={ep} people={people} />);
  await screen.findByText(/Начинаем планёрку/);
  await userEvent.click(speakerButtons()[0]!);
  const menu = await screen.findByRole("dialog", { name: "Кому отдать реплики" });
  expect(within(menu).getByText("Реплика 00:00 · Спикер 1")).toBeInTheDocument();
  expect(within(menu).getByRole("radio", { name: "Только эта реплика" })).toBeChecked();
  // Нынешнего спикера среди вариантов нет; есть спикеры встречи, база, «Это я», новый безымянный.
  expect(within(menu).queryByRole("option", { name: /^Спикер 1/ })).toBeNull();
  expect(within(menu).getByRole("option", { name: /^Спикер 2/ })).toBeInTheDocument();
  expect(within(menu).getByRole("option", { name: /^Это я — Вы/ })).toBeInTheDocument();
  expect(within(menu).getByRole("option", { name: /^Новый спикер без имени/ })).toBeInTheDocument();
  await userEvent.click(within(menu).getByRole("option", { name: /Анна Смирнова/ }));
  expect(api.relabelTurns).toHaveBeenCalledWith(ep, "r1", {
    idx: [0, 1], labels: ["Спикер 1", "Спикер 1"], count: 5, to: "Анна Смирнова" });
  expect(await screen.findByText("1 реплика → Анна Смирнова")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Отменить" }));
  await waitFor(() => expect(api.undoSpeakers).toHaveBeenCalledWith(ep, "r1"));
});

test("меню реплики: эта и следующие подряд того же спикера, новый безымянный", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText(/Начинаем планёрку/);
  await userEvent.click(speakerButtons()[0]!);
  const menu = await screen.findByRole("dialog", { name: "Кому отдать реплики" });
  await userEvent.click(within(menu).getByRole("radio", { name: /Эта и следующие подряд того же спикера \(2\)/ }));
  await userEvent.click(within(menu).getByRole("option", { name: /Новый спикер без имени/ }));
  expect(api.relabelTurns).toHaveBeenCalledWith(ep, "r1", {
    idx: [0, 1, 2], labels: ["Спикер 1", "Спикер 1", "Спикер 1"], count: 5, to: null });
});

test("новый человек по введённому имени; ошибка резидента видна в меню", async () => {
  vi.mocked(api.relabelTurns).mockRejectedValueOnce(new api.ApiError(409, "Расшифровка изменилась — обновите карточку и повторите"));
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText(/Склад готов/);
  await userEvent.click(speakerButtons()[2]!);
  const menu = await screen.findByRole("dialog", { name: "Кому отдать реплики" });
  expect(within(menu).queryByRole("radiogroup")).toBeNull(); // серии нет — выбора тоже
  await userEvent.type(within(menu).getByRole("combobox"), "Глеб Демьянов{Enter}");
  expect(api.relabelTurns).toHaveBeenCalledWith(ep, "r1", {
    idx: [3], labels: ["Спикер 2"], count: 5, to: "Глеб Демьянов" });
  expect(await within(menu).findByRole("alert")).toHaveTextContent("обновите карточку");
});

test("Ctrl+щелчок и Shift+щелчок выбирают реплики, «Назначить выбранные…»", async () => {
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText(/Начинаем планёрку/);
  const rows = () => [...container.querySelectorAll(".turn")] as HTMLElement[];
  fireEvent.click(rows()[0]!, { ctrlKey: true });
  fireEvent.click(rows()[2]!, { shiftKey: true });
  expect(rows().filter((r) => r.dataset.selected)).toHaveLength(3);
  fireEvent.click(rows()[1]!, { ctrlKey: true });
  expect(rows().filter((r) => r.dataset.selected)).toHaveLength(2);
  expect(screen.getByText("Выбрано: 2 реплики")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Назначить выбранные…" }));
  const menu = await screen.findByRole("dialog", { name: "Кому отдать реплики" });
  expect(within(menu).getByText("Выбрано: 2 реплики")).toBeInTheDocument();
  await userEvent.click(within(menu).getByRole("option", { name: /Спикер 1/ }));
  expect(api.relabelTurns).toHaveBeenCalledWith(ep, "r1", {
    idx: [0, 1, 3], labels: ["Спикер 1", "Спикер 1", "Спикер 2"], count: 5, to: "Спикер 1" });
});

test("Esc снимает выделение", async () => {
  const { container } = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText(/Начинаем планёрку/);
  fireEvent.click(container.querySelectorAll(".turn")[3]!, { ctrlKey: true });
  expect(screen.getByText("Выбрано: 1 реплика")).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByText(/Выбрано:/)).toBeNull();
});

test("из меню — все реплики спикера в панели «Спикеры»", async () => {
  render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText(/Склад готов/);
  await userEvent.click(speakerButtons()[2]!);
  const menu = await screen.findByRole("dialog", { name: "Кому отдать реплики" });
  await userEvent.click(within(menu).getByRole("button", { name: "Все реплики спикера — в панели «Спикеры»" }));
  expect(await screen.findByRole("dialog", { name: "Спикеры встречи" })).toBeInTheDocument();
});

test("locate: место в тексте реплики → сегмент и символ в нём", async () => {
  const { locate } = await import("./TurnEdit");
  expect(locate(["Склад готов.", "Да."], 3)).toEqual({ k: 0, char: 3 });
  expect(locate(["Склад готов.", "Да."], 12)).toEqual({ k: 0, char: 12 });
  expect(locate(["Склад готов.", "Да."], 13)).toEqual({ k: 1, char: 0 });
  expect(locate(["Склад готов.", "Да."], 15)).toEqual({ k: 1, char: 2 });
  expect(locate(["Склад готов.", "Да."], 99)).toEqual({ k: 1, char: 3 });
});

test("правый щелчок по тексту — «Разделить реплику здесь», вторая часть другому спикеру", async () => {
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript: { ...transcript, segments: transcript.segments.map(
    (s, i) => (i === 1 ? { ...s, has_words: true } : s)) } });
  vi.mocked(api.splitTurn).mockResolvedValue({ ...view, step: { ...view.step!,
    ops: [{ type: "split_turn", label: "Спикер 1", to: "Спикер 2", at: 5, cut: "word" }] } });
  render(<RecordingCard id="r1" endpoint={ep} />);
  const p = (await screen.findByText(/Начинаем планёрку/)).closest("p")!;
  // Место под указателем: 23-й символ текста реплики — внутри второго сегмента («Первый| пункт.»).
  const text = p.firstChild!;
  (document as unknown as { caretRangeFromPoint: unknown }).caretRangeFromPoint = () => {
    const r = document.createRange();
    r.setStart(text, 25);
    return r;
  };
  fireEvent.contextMenu(p, { clientX: 10, clientY: 10 });
  const menu = await screen.findByRole("dialog", { name: "Разделить реплику здесь" });
  expect(within(menu).getByText(/Начинаем планёрку\. Первый/)).toBeInTheDocument();
  expect(within(menu).queryByText(/нет времени отдельных слов/)).toBeNull();
  await userEvent.click(within(menu).getByRole("option", { name: /^Спикер 2/ }));
  expect(api.splitTurn).toHaveBeenCalledWith(ep, "r1", {
    turn: [0, 1], at: 1, char: 6, to: "Спикер 2", labels: ["Спикер 1", "Спикер 1"], count: 5 });
  expect(await screen.findByText("Реплика разделена: вторая часть → Спикер 2")).toBeInTheDocument();
  delete (document as unknown as { caretRangeFromPoint?: unknown }).caretRangeFromPoint;
});
