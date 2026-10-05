/**
 * «Сначала текст, спикеры потом» (Р4): текст встречи виден сразу после
 * распознавания — спокойно, без спикеров и без действий, которым они нужны;
 * спикеры приходят на тот же список реплик: место чтения, поиск и отметка
 * «сейчас играет» не теряются. Данные выдуманные.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import { placeOf, restorePlace } from "../../lib/keepPlace";
import { mergeTurns } from "../../lib/speakers";
import { statusOf } from "../../lib/status";
import type { Job, Recording, Segment, Transcript } from "../../lib/types";
import { badgeOf, RecordingItem } from "../recordings/RecordingItem";
import { RecordingCard } from "./RecordingCard";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(), getSettings: vi.fn(), getAssistant: vi.fn(), getSummary: vi.fn(), getQa: vi.fn(),
  getAnalysis: vi.fn(), getAgentContext: vi.fn(), getLiveDraft: vi.fn(), transcribe: vi.fn(), getImprove: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => false, saveText: vi.fn(async () => "x"), openFolder: vi.fn(async () => {}),
  openUrl: vi.fn(async () => {}), agentKillRecording: vi.fn(async () => {}),
}));

const ep = { base: "/api", token: null };
const seg = (start: number, end: number, speaker: string | null, text: string, extra = {}): Segment =>
  ({ start, end, speaker, text, uncertain: false, ...extra });
// Текст до спикеров: собеседники без подписи, микрофон — владелец.
const TEXT: Transcript = {
  version: 1, title: null, phase: "text",
  segments: [
    seg(0, 8, null, "Добрый день, начнём с бюджета."),
    seg(10, 14, "Вы", "Да, давайте.", { track: "mic" }),
    seg(20, 28, null, "Облако — в пределах прошлого квартала."),
  ],
};
// Окончательная: те же слова, собеседники разделены.
const FINAL: Transcript = {
  version: 1, title: null,
  segments: [
    seg(0, 3, "Анна", "Добрый день,", { has_words: true }),
    seg(4, 8, "Борис", "начнём с бюджета.", { has_words: true }),
    seg(10, 14, "Вы", "Да, давайте.", { track: "mic" }),
    seg(20, 28, "Анна", "Облако — в пределах прошлого квартала.", { has_words: true }),
  ],
};
const base: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-10-05T10:00:00", duration_s: 30,
  tracks: { sys: "s.opus", mic: "m.opus" }, has_transcript: true, has_voices: false, title: "Планёрка",
  source: "record",
};
const textRec: Recording = { ...base, transcript_phase: "text" };
const finalRec: Recording = { ...base, has_voices: true, transcript_phase: null };
const job = (state: Job["state"], extra: Partial<Job> = {}): Job => ({
  id: "t1", kind: "transcribe", folder: "C:/rec/r1", state, stage: "diarize", label: "диаризация",
  done: null, total: null, note: "sys", step: 5, steps: 7, fraction: 0.62, result: null, error: null,
  text_ready: true, ...extra,
});

let served: { rec: Recording; transcript: Transcript };
const originalRect = Element.prototype.getBoundingClientRect;

beforeEach(() => {
  vi.clearAllMocks();
  served = { rec: textRec, transcript: TEXT };
  vi.mocked(api.getRecording).mockImplementation(async () => structuredClone({ ...served.rec, transcript: served.transcript }));
  vi.mocked(api.getSettings).mockResolvedValue({ recording: { speaker_name: "Вы" } });
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAnalysis).mockResolvedValue({ state: "none" });
  vi.mocked(api.getImprove).mockResolvedValue({ state: "none", hint: false } as never);
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: [], live: false });
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
  vi.mocked(api.transcribe).mockResolvedValue(job("queued") as never);
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
  HTMLMediaElement.prototype.load = vi.fn();
});
afterEach(() => { Element.prototype.getBoundingClientRect = originalRect; });

// --- статус записи ----------------------------------------------------------------

test("статус: текст до спикеров — своё состояние, не «готово» и не «идёт расшифровка»", () => {
  expect(statusOf(textRec, [job("running")], null)).toMatchObject({ kind: "text", job: { id: "t1" } });
  expect(statusOf(textRec, [job("queued")], null)).toMatchObject({ kind: "text", job: { state: "queued" } });
  // Задачи нет (отменили, резидент перезапустился) — прервано, нужна новая расшифровка.
  expect(statusOf(textRec, [], null)).toEqual({ kind: "text", job: null });
  expect(statusOf(textRec, [job("failed", { error: "CUDA out of memory" })], null))
    .toEqual({ kind: "text", job: null, error: "CUDA out of memory" });
  expect(statusOf(finalRec, [job("done")], null)).toEqual({ kind: "ready" });
  // Задача кончилась, карточка ещё перечитывается — «заканчиваю», а не «прервалась».
  expect(statusOf(textRec, [job("done")], null, { reloading: true }))
    .toMatchObject({ kind: "text", job: { state: "done" } });
  // Перечитали, а текст всё ещё без спикеров (окончательная не записалась) — прервано.
  expect(statusOf(textRec, [job("done")], null, { reloading: false })).toEqual({ kind: "text", job: null });
  expect(statusOf(textRec, [job("done")], null)).toEqual({ kind: "text", job: null });
});

test("бейдж в списке: идёт — доля и «спикеры», прервано — «Без спикеров»", () => {
  expect(badgeOf({ kind: "text", job: job("running") }, 0.62)).toEqual({ text: "62% · Определяю спикеров", tone: "run" });
  expect(badgeOf({ kind: "text", job: job("queued") })).toEqual({ text: "В очереди", tone: "" });
  expect(badgeOf({ kind: "text", job: null })).toEqual({ text: "Без спикеров", tone: "" });
  expect(badgeOf({ kind: "text", job: job("done") })).toEqual({ text: "Обновляю…", tone: "" });
  // Повтор прерванной: пока новый текст не готов — этап распознавания, а не «спикеры».
  expect(badgeOf({ kind: "text", job: job("running", { text_ready: null, stage: "asr", note: "sys" }) }, 0.2))
    .toEqual({ text: "20% · Распознавание собеседников", tone: "run" });
});

// --- карточка ---------------------------------------------------------------------

async function card(jobs: Job[]) {
  const out = render(<RecordingCard id="r1" endpoint={ep} jobs={jobs} />);
  await screen.findByText("Добрый день, начнём с бюджета.");
  return out;
}

test("текст до спикеров: виден сразу, спокойная строка хода, ни спикеров, ни действий над ними", async () => {
  const { container } = await card([job("running")]);
  expect(screen.getByRole("status", { name: "Ход расшифровки" })).toHaveTextContent("Текст готов · определяю спикеров…");
  // Собеседники без подписи («Неизвестный» не пишем), владелец — простой подписью, не кнопкой.
  expect(screen.queryByText("Неизвестный")).toBeNull();
  expect(container.querySelectorAll("button.turn__speaker")).toHaveLength(0);
  expect(container.querySelector(".turn__speaker")).toHaveTextContent("Вы");
  // Ни чипов спикеров в шапке, ни «Улучшить», ни «Спросить агента» у реплик.
  expect(screen.queryByRole("button", { name: /^Спикеры \(/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Улучшить расшифровку" })).toBeNull();
  expect(container.querySelector(".turn__ask")).toBeNull();
  expect(screen.queryByRole("tab", { name: "Итоги" })).toBeNull();
  // Поиск по тексту работает.
  expect(screen.getByRole("searchbox", { name: "Найти в расшифровке" })).toBeInTheDocument();
  // Отмена — из строки хода.
  expect(within(screen.getByRole("status", { name: "Ход расшифровки" }))
    .getByRole("button", { name: "Отменить расшифровку…" })).toBeInTheDocument();
});

test("карточка перечитывается, когда задача сказала «текст готов»", async () => {
  served = { rec: { ...base, has_transcript: false }, transcript: null as unknown as Transcript };
  const { rerender } = render(<RecordingCard id="r1" endpoint={ep} jobs={[job("running", { text_ready: null, stage: "asr" })]} />);
  await screen.findAllByText(/Распознавание/);
  served = { rec: textRec, transcript: TEXT };
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("running")]} />);
  await screen.findByText("Добрый день, начнём с бюджета.");
});

test("спикеры приходят на тот же список: поиск и элемент расшифровки остаются, появляются подписи", async () => {
  const { container, rerender } = await card([job("running")]);
  const view = container.querySelector(".transcript");
  const find = screen.getByRole("searchbox", { name: "Найти в расшифровке" });
  await userEvent.type(find, "бюджет");
  served = { rec: finalRec, transcript: FINAL };
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("done", { text_ready: true })]} />);
  await waitFor(() => expect(container.querySelectorAll("button.turn__speaker").length).toBeGreaterThan(0));
  expect(container.querySelector(".transcript")).toBe(view);
  expect(screen.getByRole("searchbox", { name: "Найти в расшифровке" })).toHaveValue("бюджет");
  expect(screen.queryByRole("status", { name: "Ход расшифровки" })).toBeNull();
  expect(screen.getByRole("button", { name: /^Спикеры \(/ })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Итоги" })).toBeInTheDocument();
});

test("отметка «сейчас играет» переходит на реплику того же момента в новом списке", async () => {
  const { container, rerender } = await card([job("running")]);
  Element.prototype.getBoundingClientRect = function () {
    return { left: 0, right: 300, width: 300, top: 0, bottom: 20, height: 20, x: 0, y: 0, toJSON() {} } as DOMRect;
  };
  const bar = screen.getByRole("slider", { name: "Позиция" });
  fireEvent.pointerDown(bar, { clientX: 220, button: 0, pointerId: 1 }); // 22 с из 30
  fireEvent.pointerUp(bar, { clientX: 220, pointerId: 1 });
  const now = () => [...container.querySelectorAll<HTMLElement>("[data-now]")].map((r) => r.dataset.turn);
  await waitFor(() => expect(now()).toEqual(["2"]));
  served = { rec: finalRec, transcript: FINAL };
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("done")]} />);
  await waitFor(() => expect(container.querySelectorAll("button.turn__speaker").length).toBeGreaterThan(0));
  // В новом списке на 22-й секунде — четвёртая реплика (Анна разделена с Борисом).
  expect(now()).toEqual(["3"]);
});

test("прервано между фазами: текст остаётся с пометкой и «Расшифровать заново»", async () => {
  await card([job("failed", { error: "код возврата 1", text_ready: true })]);
  const note = screen.getByRole("status", { name: "Ход расшифровки" });
  expect(note).toHaveTextContent("Спикеры не определены: расшифровка прервалась");
  await userEvent.click(within(note).getByRole("button", { name: "Расшифровать заново" }));
  expect(api.transcribe).toHaveBeenCalledWith(ep, "r1");
});

test("в очереди на повтор: текст виден, строка хода — «в очереди»", async () => {
  await card([job("queued", { text_ready: null })]);
  expect(screen.getByRole("status", { name: "Ход расшифровки" }))
    .toHaveTextContent("Расшифровка в очереди · текст пока без спикеров");
});

// --- место чтения ---------------------------------------------------------------

test("место чтения: момент на верхней кромке остаётся на ней, когда реплики пересобраны", () => {
  const scroller = document.createElement("div");
  const box = document.createElement("div");
  scroller.append(box);
  const before = mergeTurns(TEXT.segments);
  const after = mergeTurns(FINAL.segments);
  const rows = (n: number, h: number[]) => {
    box.replaceChildren(...Array.from({ length: n }, (_, i) => {
      const el = document.createElement("div");
      el.dataset.turn = String(i);
      el.dataset.h = String(h[i]);
      return el;
    }));
  };
  // Раскладка: строки друг под другом, высота — из data-h; кромка прокрутки — 0.
  Element.prototype.getBoundingClientRect = function (this: HTMLElement) {
    if (this === scroller) return { top: 0, bottom: 300, height: 300 } as DOMRect;
    const all = [...box.children] as HTMLElement[];
    let top = -scroller.scrollTop;
    for (const el of all) {
      const h = Number(el.dataset.h);
      if (el === this) return { top, bottom: top + h, height: h } as DOMRect;
      top += h;
    }
    return { top: 0, bottom: 0, height: 0 } as DOMRect;
  };
  rows(3, [100, 40, 100]);
  scroller.scrollTop = 150; // кромка — на 10 px ниже начала третьей строки (она со 140 px)
  const place = placeOf(scroller, box, before)!;
  expect(place.t).toBeCloseTo(20.8);
  rows(4, [50, 70, 40, 100]); // реплики Анны и Бориса — две строки, подписи выше
  restorePlace(scroller, box, after, place);
  // Та же 20,8-я секунда — на кромке: 160 (начало четвёртой строки) + 10.
  expect(scroller.scrollTop).toBeCloseTo(170);
});

test("место чтения: в самом начале — не трогаем", () => {
  const scroller = document.createElement("div");
  const box = document.createElement("div");
  scroller.append(box);
  expect(placeOf(scroller, box, mergeTurns(TEXT.segments))).toBeNull();
});

test("переход «текст → спикеры» не мигает: нет «Загружаю запись…» между ними", async () => {
  const { rerender } = await card([job("running")]);
  served = { rec: finalRec, transcript: FINAL };
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("done")]} />);
  expect(screen.queryByText("Загружаю запись…")).toBeNull();
  await act(async () => {});
  expect(screen.queryByText("Загружаю запись…")).toBeNull();
  expect(screen.getByText("начнём с бюджета.", { exact: false })).toBeInTheDocument();
});

test("место чтения в карточке: пришли спикеры — читаемый момент остаётся у верхней кромки", async () => {
  const { container, rerender } = await card([job("running")]);
  const body = container.querySelector<HTMLElement>(".card__body")!;
  body.style.overflowY = "auto";
  const HEIGHTS: Record<number, number[]> = { 3: [100, 40, 100], 4: [50, 70, 40, 100] };
  Element.prototype.getBoundingClientRect = function (this: HTMLElement) {
    if (this === body) return { top: 0, bottom: 300, height: 300 } as DOMRect;
    if (this.dataset?.turn === undefined) return { top: 0, bottom: 0, height: 0 } as DOMRect;
    const rows = [...container.querySelectorAll<HTMLElement>(".turns [data-turn]")];
    const h = HEIGHTS[rows.length] ?? [];
    let top = -body.scrollTop;
    for (const [i, el] of rows.entries()) {
      if (el === this) return { top, bottom: top + h[i]!, height: h[i]! } as DOMRect;
      top += h[i]!;
    }
    return { top: 0, bottom: 0, height: 0 } as DOMRect;
  };
  body.scrollTop = 150;
  served = { rec: finalRec, transcript: FINAL };
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("done")]} />);
  await waitFor(() => expect(container.querySelectorAll("button.turn__speaker").length).toBeGreaterThan(0));
  expect(body.scrollTop).toBeCloseTo(170);
});


test("задача кончилась, карточка ещё перечитывается: ни «прервалась», ни «Расшифровать заново»", async () => {
  const { rerender } = await card([job("running")]);
  let release: () => void = () => {};
  vi.mocked(api.getRecording).mockImplementation(() => new Promise((resolve) => {
    release = () => resolve(structuredClone({ ...finalRec, transcript: FINAL }));
  }));
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("done")]} />);
  const note = screen.getByRole("status", { name: "Ход расшифровки" });
  expect(note).toHaveTextContent("Текст готов · спикеры определены, обновляю…");
  expect(screen.queryByText(/прервалась/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Расшифровать заново" })).toBeNull();
  expect(within(note).queryByRole("button")).toBeNull();
  await act(async () => { release(); });
  await waitFor(() => expect(screen.queryByRole("status", { name: "Ход расшифровки" })).toBeNull());
});

test("повтор прерванной: пока новый текст не готов — «распознаю заново», прежний текст виден", async () => {
  await card([job("running", { text_ready: null, stage: "asr" })]);
  expect(screen.getByRole("status", { name: "Ход расшифровки" }))
    .toHaveTextContent("Распознаю заново · прежний текст без спикеров");
});

test("место чтения держится и когда текст до спикеров сменился новым (повтор)", async () => {
  const { container, rerender } = await card([job("running", { id: "t2", text_ready: null, stage: "asr" })]);
  const body = container.querySelector<HTMLElement>(".card__body")!;
  body.style.overflowY = "auto";
  const HEIGHTS: Record<number, number[]> = { 3: [100, 40, 100], 4: [50, 70, 40, 100] };
  Element.prototype.getBoundingClientRect = function (this: HTMLElement) {
    if (this === body) return { top: 0, bottom: 300, height: 300 } as DOMRect;
    if (this.dataset?.turn === undefined) return { top: 0, bottom: 0, height: 0 } as DOMRect;
    const rows = [...container.querySelectorAll<HTMLElement>(".turns [data-turn]")];
    const h = HEIGHTS[rows.length] ?? [];
    let top = -body.scrollTop;
    for (const [i, el] of rows.entries()) {
      if (el === this) return { top, bottom: top + h[i]!, height: h[i]! } as DOMRect;
      top += h[i]!;
    }
    return { top: 0, bottom: 0, height: 0 } as DOMRect;
  };
  body.scrollTop = 150;
  // Новый текст до спикеров: те же слова, реплики собраны иначе.
  const again: Transcript = { ...FINAL, phase: "text", segments: FINAL.segments.map((s, i) =>
    (i === 1 ? { ...s, speaker: "Вы", track: "mic" } : { ...s, speaker: s.speaker === "Вы" ? "Вы" : null })) };
  served = { rec: textRec, transcript: again };
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("running", { id: "t2" })]} />);
  await waitFor(() => expect(container.querySelectorAll(".turns [data-turn]")).toHaveLength(4));
  expect(body.scrollTop).toBeCloseTo(170);
});


test("фрагмент поиска из текста до спикеров — без подписи и без «: »", () => {
  const { container } = render(<RecordingItem rec={{ ...textRec, hits: [{ t: 0, speaker: "", snippet: "начнём с бюджета", ranges: [[8, 15]] }],
    total: 1 }} status={{ kind: "text", job: null }} selected={false} onSelect={() => {}} />);
  const hit = container.querySelector<HTMLElement>(".rec-hit")!;
  expect(hit).toHaveTextContent("начнём с бюджета");
  expect(hit.querySelector(".rec-hit__who")).toBeNull();
  expect(hit.textContent).not.toContain(": ");
});


test("перечитывание дольше минуты: всё ещё «обновляю…» — без часов", async () => {
  const { rerender } = await card([job("running")]);
  let release: () => void = () => {};
  vi.mocked(api.getRecording).mockImplementation(() => new Promise((resolve) => {
    release = () => resolve(structuredClone({ ...finalRec, transcript: FINAL }));
  }));
  const done = job("done", { finished_at: Date.now() / 1000 - 120 });
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[done]} />);
  const later = Date.now() + 120_000;
  const spy = vi.spyOn(Date, "now").mockReturnValue(later);
  try {
    rerender(<RecordingCard id="r1" endpoint={ep} jobs={[{ ...done }]} />);
    expect(screen.getByRole("status", { name: "Ход расшифровки" }))
      .toHaveTextContent("Текст готов · спикеры определены, обновляю…");
    expect(screen.queryByRole("button", { name: "Расшифровать заново" })).toBeNull();
  } finally {
    spy.mockRestore();
  }
  await act(async () => { release(); });
  await waitFor(() => expect(screen.queryByRole("status", { name: "Ход расшифровки" })).toBeNull());
});

test("перечитали, а текст всё ещё без спикеров (окончательная не записалась) — прервалась", async () => {
  const { rerender } = await card([job("running")]);
  rerender(<RecordingCard id="r1" endpoint={ep} jobs={[job("done")]} />);
  const note = await screen.findByText("Спикеры не определены: расшифровка прервалась");
  expect(note).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Расшифровать заново" })).toBeInTheDocument();
});
