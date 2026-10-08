/**
 * Перемотка из плеера прокручивает «Расшифровку» к реплике, которая звучит в этот момент,
 * а обычное воспроизведение — нет; отметка «сейчас играет». Данные выдуманные.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import { buildView } from "../../lib/analysisView";
import { mergeTurns } from "../../lib/speakers";
import type { Analysis, Recording, Segment, Transcript } from "../../lib/types";
import { RecordingCard } from "./RecordingCard";
import { TranscriptView } from "./TranscriptView";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(), getSettings: vi.fn(), getAssistant: vi.fn(), getSummary: vi.fn(), getQa: vi.fn(),
  getAnalysis: vi.fn(), getAgentContext: vi.fn(), getLiveDraft: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => false, saveText: vi.fn(async () => "x"), openFolder: vi.fn(async () => {}),
  openUrl: vi.fn(async () => {}), agentKillRecording: vi.fn(async () => {}),
}));

const seg = (start: number, end: number, speaker: string, text: string): Segment =>
  ({ start, end, speaker, text, uncertain: false });
// Реплика i начинается на 10·i секунде; запись — 70 с.
const SEGMENTS: Segment[] = [
  seg(0, 4, "Анна", "Начнём с бюджета на квартал."),
  seg(10, 14, "Борис", "Сколько закладываем на облако?"),
  seg(20, 26, "Анна", "Решили: облако — в пределах прошлого квартала."),
  seg(30, 34, "Борис", "Я подготовлю расчёт к пятнице."),
  seg(40, 44, "Анна", "Есть риск, что поставщик поднимет цены."),
  seg(50, 54, "Борис", "Тогда переходим к найму."),
  seg(60, 64, "Анна", "Нужен ещё один тестировщик."),
];
const ANALYSIS: Analysis = {
  version: 1, model: "test", created_at: 1, fingerprint: "f", segments: SEGMENTS.length,
  features: ["types", "importance", "chapters"],
  phrase_types: { 1: "question", 2: "decision", 3: "task", 4: "risk", 6: "idea" },
  importance: { 2: 0.95, 3: 0.7, 4: 0.8 },
  chapters: [
    { start_i: 0, end_i: 4, title: "Бюджет на квартал", short: "Бюджет" },
    { start_i: 5, end_i: 6, title: "Найм в команду", short: "Найм" },
  ],
};
const TURNS = mergeTurns(SEGMENTS);

const ep = { base: "/api", token: null };
const transcript: Transcript = { version: 1, title: null, segments: SEGMENTS };
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 70,
  tracks: { sys: "s.opus" }, has_transcript: true, has_voices: false, title: "Планирование", source: "record",
};
const WIDTH = 700; // 10 px на секунду

type Scrolled = { turn: string | undefined; more: string | undefined; margin: string };
let scrolled: Scrolled[];
const originalScroll = Element.prototype.scrollIntoView;
const originalRect = Element.prototype.getBoundingClientRect;
beforeEach(() => {
  scrolled = [];
  Element.prototype.scrollIntoView = vi.fn(function (this: HTMLElement) {
    scrolled.push({ turn: this.dataset.turn, more: this.dataset.more, margin: this.style.scrollMarginTop });
  });
  Element.prototype.getBoundingClientRect = function () {
    return { left: 0, right: WIDTH, width: WIDTH, top: 0, bottom: 20, height: 20, x: 0, y: 0, toJSON() {} } as DOMRect;
  };
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.load = vi.fn();
  try { window.localStorage.clear(); } catch { /* нет хранилища */ }
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript });
  vi.mocked(api.getSettings).mockResolvedValue({});
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "ready", analysis: ANALYSIS } as never);
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md"], live: false });
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
});
afterEach(() => {
  Element.prototype.scrollIntoView = originalScroll;
  Element.prototype.getBoundingClientRect = originalRect;
});

async function card() {
  const out = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начнём с бюджета на квартал.");
  await waitFor(() => expect(out.container.querySelectorAll(".chapter-head")).toHaveLength(2));
  return { ...out, audio: out.container.querySelector("audio")! };
}
const bar = () => screen.getByRole("slider", { name: "Позиция" });
const clickBar = (sec: number) => {
  fireEvent.pointerDown(bar(), { clientX: sec * 10, button: 0, pointerId: 1 });
  fireEvent.pointerUp(bar(), { clientX: sec * 10, pointerId: 1 });
};
const nowRows = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>("[data-now]")].map((r) => r.dataset.turn);

test("щелчок по полосе — к реплике, звучащей в этот момент; ~30 % от верха, с подсветкой", async () => {
  const { container } = await card();
  clickBar(24); // реплика 2 (с 20 с)
  await waitFor(() => expect(scrolled).toEqual([{ turn: "2", more: undefined, margin: "30vh" }]));
  expect(container.querySelector('[data-turn="2"]')).toHaveClass("turn--flash");
  clickBar(5);
  await waitFor(() => expect(scrolled.map((s) => s.turn)).toEqual(["2", "0"]));
});

test("клавиши плеера прокручивают так же: J/L, ←/→, 0–9, Shift+←/→, Ctrl+←/→", async () => {
  const { audio } = await card();
  const press = async (keys: string, turn: string) => {
    scrolled.length = 0;
    await userEvent.keyboard(keys);
    await waitFor(() => expect(scrolled).toHaveLength(1));
    expect(scrolled[0]!.turn).toBe(turn);
  };
  audio.currentTime = 12;
  await press("l", "2"); // 22 с
  await press("j", "1"); // 12 с
  audio.currentTime = 30;
  await press("{ArrowRight}", "3"); // 35 с
  await press("{ArrowLeft}", "3"); // 30 с
  await press("9", "6"); // 63 с
  audio.currentTime = 20;
  await press("{Shift>}{ArrowRight}{/Shift}", "5"); // начало главы 2
  await press("{Control>}{ArrowLeft}{/Control}", "4"); // предыдущая реплика
});

test("глава из списка: прокрутка к её первой реплике", async () => {
  const { container } = await card();
  await userEvent.click(screen.getByRole("button", { name: "Главы" }));
  const list = screen.getByRole("dialog", { name: "Главы встречи" });
  await userEvent.click(within(list).getAllByRole("button")[1]!);
  await waitFor(() => expect(scrolled.map((s) => s.turn)).toEqual(["5"]));
  expect(container.querySelector('[data-turn="5"]')).toHaveClass("turn--flash");
});

test("реплика скрыта фильтром: к строке «… N реплик», где она спряталась; фильтр не раскрывается", async () => {
  const { container } = await card();
  await userEvent.click(screen.getByRole("button", { name: /Риски/ })); // видна только реплика 4
  const visible = () => [...container.querySelectorAll<HTMLElement>(".turn")].map((r) => r.dataset.turn);
  expect(visible()).toEqual(["4"]);
  clickBar(12); // реплика 1 — в свёрнутой строке 0–3
  await waitFor(() => expect(scrolled).toHaveLength(1));
  expect(scrolled[0]!.turn).toBeUndefined();
  expect(scrolled[0]!.more).toBe("0");
  expect(visible()).toEqual(["4"]);
});

test("без глав: реплика скрыта фильтром — к строке «… N реплик» с ней, показаны по-прежнему только нужные", () => {
  const view = buildView(TURNS, ANALYSIS, SEGMENTS.length, { types: true, importance: true, chapters: false, insights: false });
  const props = { turns: TURNS, colors: new Map<string, string>(), playable: true, onPlay: () => {}, view };
  const { container, rerender } = render(<TranscriptView {...props} />);
  fireEvent.click(screen.getByRole("button", { name: /Идеи/ })); // видна только реплика 6
  expect(container.querySelectorAll(".turn")).toHaveLength(1);
  rerender(<TranscriptView {...props} seekTo={{ t: 11, n: 1 }} />);
  expect(scrolled.map((s) => s.more)).toEqual(["0"]);
  expect(container.querySelectorAll(".turn")).toHaveLength(1);
});

test("обычное воспроизведение не прокручивает, но отметка «сейчас играет» переходит с репликой", async () => {
  const { container, audio } = await card();
  expect(nowRows(container)).toEqual([]);
  Object.defineProperty(audio, "paused", { configurable: true, value: false });
  audio.currentTime = 12;
  fireEvent.play(audio);
  await waitFor(() => expect(nowRows(container)).toEqual(["1"]));
  audio.currentTime = 33;
  await waitFor(() => expect(nowRows(container)).toEqual(["3"]));
  audio.currentTime = 66;
  await waitFor(() => expect(nowRows(container)).toEqual(["6"]));
  expect(scrolled).toEqual([]);
  expect(container.querySelector("[data-now]")).not.toHaveClass("turn--key");
});

test("перемотка двигает отметку «сейчас играет» и на паузе", async () => {
  const { container } = await card();
  clickBar(41);
  await waitFor(() => expect(nowRows(container)).toEqual(["4"]));
  clickBar(2);
  await waitFor(() => expect(nowRows(container)).toEqual(["0"]));
});

test("вкладка «Расшифровка» скрыта: прокрутка откладывается до её показа", async () => {
  await card();
  await userEvent.click(screen.getByRole("tab", { name: "Агент" }));
  clickBar(44);
  await act(async () => {});
  expect(scrolled).toEqual([]);
  await userEvent.click(screen.getByRole("tab", { name: "Расшифровка" }));
  await waitFor(() => expect(scrolled.map((s) => s.turn)).toEqual(["4"]));
});

test("важная реплика: подложка строки, колонка текста не сдвигается; «играет» — время акцентом, не рамка", () => {
  const css = readFileSync(resolve(__dirname, "markup.css"), "utf-8");
  // Важность (Atlas Aurora) — подложка всей строки и слово «важное» в строке имени, а не поле
  // и отступ строки: их перебивал бы `.turn { padding }` из card.css (он подключается позже).
  expect(css).toMatch(/\.turn--key \{[^}]*background: var\(--surface-2\)/);
  expect(css).not.toMatch(/\.turn--key[^{]*\{[^}]*(margin|padding)/);
  // Текст цвета акцента — текстовый токен Aurora `--accent-line`.
  expect(css).toMatch(/\.turn\[data-now\] \.turn__time \{ color: var\(--accent-line\)/);
  expect(css).not.toMatch(/\.turn\[data-now\][^{]*\{[^}]*box-shadow/);
});
