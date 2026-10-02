/**
 * Разметка встречи в «Расшифровке» (M3): значки типов, фильтры со свёрнутыми
 * репликами, полоса у важных, заголовки глав с ✦, «Наблюдения» со ссылками,
 * настройки, которые всё это прячут. Данные выдуманные.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import { buildView } from "../../lib/analysisView";
import { mergeTurns } from "../../lib/speakers";
import type { Analysis, Recording, Segment, Transcript } from "../../lib/types";
import { RecordingCard } from "./RecordingCard";
import { TranscriptView } from "./TranscriptView";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getRecording: vi.fn(),
  getSettings: vi.fn(),
  getAssistant: vi.fn(),
  getSummary: vi.fn(),
  getQa: vi.fn(),
  getAnalysis: vi.fn(),
  getAgentContext: vi.fn(),
  getLiveDraft: vi.fn(),
}));
vi.mock("../../lib/shell", () => ({
  inTauri: () => false,
  saveText: vi.fn(async () => "x"),
  openFolder: vi.fn(async () => {}),
  openUrl: vi.fn(async () => {}),
  agentKillRecording: vi.fn(async () => {}),
}));

const seg = (start: number, end: number, speaker: string, text: string): Segment =>
  ({ start, end, speaker, text, uncertain: false });
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
  features: ["types", "importance", "chapters", "insights"],
  phrase_types: { 1: "question", 2: "decision", 3: "task", 4: "risk", 6: "idea" },
  importance: { 2: 0.95, 3: 0.7, 4: 0.8 },
  chapters: [
    { start_i: 0, end_i: 4, title: "Бюджет на квартал", short: "Бюджет" },
    { start_i: 5, end_i: 6, title: "Найм в команду", short: "Найм" },
  ],
  insights: [
    { id: "i1", kind: "attention", text: "Расчёт нужен до роста цен.", refs: [3, 4], why: "Срок и риск названы рядом." },
    { id: "i2", kind: "followup", text: "Открыть вакансию тестировщика.", refs: [6], why: "" },
  ],
};
const TURNS = mergeTurns(SEGMENTS);
const ALL = { types: true, importance: true, chapters: true, insights: true };
const VIEW = buildView(TURNS, ANALYSIS, SEGMENTS.length, ALL);

function view(props: Partial<Parameters<typeof TranscriptView>[0]> = {}) {
  return render(<TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} view={VIEW} {...props} />);
}
const turnRows = (c: HTMLElement) => [...c.querySelectorAll<HTMLElement>(".turn")];

beforeEach(() => {
  try { window.localStorage.clear(); } catch { /* нет хранилища */ }
});

test("значок типа в начале реплики, с подписью; у утверждения значка нет", () => {
  const { container } = view();
  const rows = turnRows(container);
  expect(within(rows[1]!).getByRole("img", { name: "Вопрос" })).toHaveAttribute("title", "Вопрос");
  expect(within(rows[2]!).getByRole("img", { name: "Решение" })).toBeInTheDocument();
  expect(within(rows[4]!).getByRole("img", { name: "Риск" })).toBeInTheDocument();
  expect(rows[0]!.querySelector(".turn__type")).toBeNull();
});

test("полоса слева — у самых важных реплик (верхние ~15 %)", () => {
  const { container } = view();
  const key = turnRows(container).map((r) => r.classList.contains("turn--key"));
  expect(key).toEqual([false, false, true, false, false, false, false]);
});

test("заголовки глав «Глава N · название» перед первой репликой главы, ✦ — обсудить с агентом", async () => {
  const onAskChapter = vi.fn();
  const { container } = view({ onAskChapter });
  const heads = [...container.querySelectorAll(".chapter-head")];
  expect(heads.map((h) => h.querySelector("h3")!.textContent)).toEqual(["Глава 1 · Бюджет на квартал", "Глава 2 · Найм в команду"]);
  expect(heads[1]!.nextElementSibling).toHaveAttribute("data-turn", "5");
  expect(heads[0]).toHaveTextContent("00:00–00:44");
  await userEvent.click(within(heads[1] as HTMLElement).getByRole("button", { name: "Обсудить главу «Найм в команду» с агентом" }));
  expect(onAskChapter).toHaveBeenCalledWith(1);
});

test("фильтры по типам: несколько сразу, остальное — «… N реплик», щелчок разворачивает", async () => {
  const { container } = view();
  const chips = screen.getByRole("group", { name: "Показать только реплики этих типов" });
  expect(within(chips).getByRole("button", { name: /Вопросы/ })).toHaveTextContent("1");
  await userEvent.click(within(chips).getByRole("button", { name: /Вопросы/ }));
  await userEvent.click(within(chips).getByRole("button", { name: /Риски/ }));
  expect(turnRows(container).map((r) => r.dataset.turn)).toEqual(["1", "4"]);
  const more = [...container.querySelectorAll<HTMLElement>(".turns-more")];
  expect(more.map((m) => m.textContent)).toEqual(["… 1 реплика", "… 2 реплики", "… 2 реплики"]);
  // Заголовки глав видны и с фильтром.
  expect(container.querySelectorAll(".chapter-head")).toHaveLength(2);
  await userEvent.click(more[1]!);
  expect(turnRows(container).map((r) => r.dataset.turn)).toEqual(["1", "2", "3", "4"]);
  await userEvent.click(screen.getByRole("button", { name: "Показать все" }));
  expect(turnRows(container)).toHaveLength(7);
});

test("поиск находит и свёрнутые фильтром реплики: они показаны", async () => {
  const { container } = view();
  await userEvent.click(screen.getByRole("button", { name: /Идеи/ }));
  expect(turnRows(container).map((r) => r.dataset.turn)).toEqual(["6"]);
  await userEvent.type(screen.getByRole("searchbox", { name: "Найти в расшифровке" }), "облако");
  await waitFor(() => expect(turnRows(container).map((r) => r.dataset.turn)).toEqual(["1", "2", "6"]));
  expect(container.querySelectorAll("mark.hit")).toHaveLength(2);
});

test("«Наблюдения»: вид, текст, «Почему», ссылки на реплики, ✦; блок сворачивается", async () => {
  const onAskInsight = vi.fn();
  const scrolled: string[] = [];
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) { scrolled.push(this.getAttribute("data-turn") ?? ""); });
  try {
    const { container } = view({ onAskInsight });
    const block = screen.getByRole("region", { name: "Наблюдения анализа встречи" });
    const items = within(block).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByRole("img", { name: "Обратить внимание" })).toBeInTheDocument();
    expect(items[0]).toHaveTextContent("Расчёт нужен до роста цен.");
    expect(items[0]).not.toHaveTextContent("Срок и риск названы рядом.");
    await userEvent.click(within(items[0]!).getByRole("button", { name: "Почему" }));
    expect(items[0]).toHaveTextContent("Срок и риск названы рядом.");
    expect(within(items[1]!).queryByRole("button", { name: "Почему" })).toBeNull();
    // Ссылка на реплику, свёрнутую фильтром: она раскрывается, к ней прокрутка и подсветка.
    await userEvent.click(screen.getByRole("button", { name: /Идеи/ }));
    await userEvent.click(within(items[0]!).getByRole("button", { name: "00:40 · Анна" }));
    const target = container.querySelector<HTMLElement>('[data-turn="4"]')!;
    expect(target).toBeInTheDocument();
    expect(target).toHaveClass("turn--flash");
    expect(scrolled).toContain("4");
    await userEvent.click(within(items[1]!).getByRole("button", { name: "Обсудить с агентом" }));
    expect(onAskInsight).toHaveBeenCalledWith(expect.objectContaining({ id: "i2", refs: [6] }));
    await userEvent.click(within(block).getByRole("button", { name: /Наблюдения/ }));
    expect(within(block).queryByRole("list")).toBeNull();
  } finally {
    Element.prototype.scrollIntoView = original;
  }
});

test("без анализа — обычная лента: ни значков, ни фильтров, ни глав, ни наблюдений", () => {
  const { container } = view({ view: null });
  expect(container.querySelector(".turn__type, .chapter-head, .tfilters, .insights, .turn--key")).toBeNull();
  expect(turnRows(container)).toHaveLength(7);
});

// --- карточка целиком: настройки и ✦ у главы ---------------------------------------------------

const ep = { base: "/api", token: null };
const transcript: Transcript = { version: 1, title: null, segments: SEGMENTS };
const rec: Recording = {
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 70,
  tracks: { sys: "s.opus" }, has_transcript: true, has_voices: false, title: "Планирование", source: "record",
};

function mocks(settings: Record<string, unknown> = {}, analysis: object = { state: "ready", analysis: ANALYSIS }) {
  vi.mocked(api.getRecording).mockResolvedValue({ ...rec, transcript });
  vi.mocked(api.getSettings).mockResolvedValue(settings);
  vi.mocked(api.getAssistant).mockResolvedValue({
    provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false,
  });
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.getQa).mockResolvedValue({ items: [] });
  vi.mocked(api.getAnalysis).mockResolvedValue(analysis as never);
  vi.mocked(api.getAgentContext).mockResolvedValue({ files: ["transcript.md"], live: false });
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
}

async function card() {
  const out = render(<RecordingCard id="r1" endpoint={ep} />);
  await screen.findByText("Начнём с бюджета на квартал.");
  return out;
}

test("карточка: свежий анализ — значки, главы, наблюдения", async () => {
  mocks();
  const { container } = await card();
  await waitFor(() => expect(container.querySelectorAll(".chapter-head")).toHaveLength(2));
  expect(container.querySelector(".turn__type")).not.toBeNull();
  expect(screen.getByRole("region", { name: "Наблюдения анализа встречи" })).toBeInTheDocument();
});

test("карточка: выключенное в «Подсветке и разметке» не показывается", async () => {
  mocks({ transcript_view: { types: false, importance: false, chapters: false, insights: false } });
  const { container } = await card();
  await act(async () => {});
  await waitFor(() => expect(api.getAnalysis).toHaveBeenCalled());
  await act(async () => {});
  expect(container.querySelector(".turn__type, .chapter-head, .tfilters, .insights, .turn--key")).toBeNull();
});

test("карточка: часть, выключенная в «Анализе встречи», не показывается", async () => {
  mocks({ analysis: { chapters: false, insights: false } });
  const { container } = await card();
  await waitFor(() => expect(container.querySelector(".turn__type")).not.toBeNull());
  expect(container.querySelector(".chapter-head, .insights")).toBeNull();
});

test("карточка: устаревший анализ с другим числом сегментов не показывается", async () => {
  mocks({}, { state: "stale", analysis: { ...ANALYSIS, segments: 9 } });
  const { container } = await card();
  await waitFor(() => expect(screen.getByText(/Анализ устарел/)).toBeInTheDocument());
  expect(container.querySelector(".turn__type, .chapter-head, .insights")).toBeNull();
});

test("карточка: ✦ у главы — на «Агент» со ссылкой: глава, время и первые реплики", async () => {
  mocks();
  const { container } = await card();
  await waitFor(() => expect(container.querySelectorAll(".chapter-head")).toHaveLength(2));
  await userEvent.click(screen.getByRole("button", { name: "Обсудить главу «Найм в команду» с агентом" }));
  expect(screen.getByRole("tab", { name: "Агент" })).toHaveAttribute("aria-selected", "true");
  const note = await screen.findByRole("status", { name: "Ссылка не вставлена" });
  expect(note.querySelector("pre")!.textContent).toBe([
    "Про главу встречи:",
    "Глава 2 «Найм в команду», 00:50–01:04",
    "[00:50] Борис: «Тогда переходим к найму.»",
    "[01:00] Анна: «Нужен ещё один тестировщик.»",
  ].join("\n"));
});

test("карточка: ✦ у наблюдения — текст, «почему» и реплики", async () => {
  mocks();
  await card();
  const block = await screen.findByRole("region", { name: "Наблюдения анализа встречи" });
  await userEvent.click(within(block).getAllByRole("button", { name: "Обсудить с агентом" })[0]!);
  const note = await screen.findByRole("status", { name: "Ссылка не вставлена" });
  expect(note.querySelector("pre")!.textContent).toBe([
    "Про наблюдение анализа встречи:",
    "Обратить внимание: Расчёт нужен до роста цен. Почему: Срок и риск названы рядом.",
    "[00:30] Борис: «Я подготовлю расчёт к пятнице.»",
    "[00:40] Анна: «Есть риск, что поставщик поднимет цены.»",
  ].join("\n"));
});
