/**
 * Ссылки на задачи Jira в карточке (M3): реплики (вместе с подсветкой
 * поиска), итоги, наблюдения; открытие через open_url. Данные выдуманные.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildView } from "../../lib/analysisView";
import { jiraCard, jiraLinker, JiraLinks, turnLinks } from "../../lib/jira";
import { Markdown } from "../../lib/markdown";
import * as shell from "../../lib/shell";
import { mergeTurns } from "../../lib/speakers";
import type { Analysis, JiraRefs, Segment } from "../../lib/types";
import { LinkedText } from "../../ui/LinkedText";
import { TranscriptView } from "./TranscriptView";

vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  openUrl: vi.fn(async () => {}),
}));

const linker = jiraLinker({ integrations: { jira_base_url: "https://jira.example.com" } })!;
const seg = (start: number, speaker: string, text: string): Segment =>
  ({ start, end: start + 4, speaker, text, uncertain: false });
const SEGMENTS = [
  seg(0, "Анна", "Экспорт ведём в SPR-131, там же кодировка."),
  seg(10, "Борис", "Алерты — это OPS-7, порог вынесем в конфиг."),
];
const TURNS = mergeTurns(SEGMENTS);

function transcript(jira = linker, props: Partial<Parameters<typeof TranscriptView>[0]> = {}) {
  return render(
    <JiraLinks.Provider value={jira}>
      <TranscriptView turns={TURNS} colors={new Map()} playable onPlay={() => {}} {...props} />
    </JiraLinks.Provider>,
  );
}

beforeEach(() => { vi.mocked(shell.openUrl).mockClear(); });

test("ключ задачи в реплике — ссылка; щелчок открывает задачу через оболочку", async () => {
  transcript();
  const link = screen.getByRole("link", { name: "SPR-131" });
  expect(link).toHaveAttribute("href", "https://jira.example.com/browse/SPR-131");
  expect(link).not.toHaveAttribute("title");
  expect(link).toHaveAccessibleDescription("Открыть SPR-131 в Jira");
  await userEvent.click(link);
  expect(shell.openUrl).toHaveBeenCalledWith("https://jira.example.com/browse/SPR-131");
  // Текст реплики не изменился: правка слов и разделение считают по нему.
  expect(screen.getByText(/Экспорт ведём в/).textContent).toBe("Экспорт ведём в SPR-131, там же кодировка.");
});

test("Ctrl+щелчок по ссылке — выбор реплики, а не переход", () => {
  const onSelect = vi.fn();
  transcript(linker, { onSelect });
  fireEvent.click(screen.getByRole("link", { name: "OPS-7" }), { ctrlKey: true });
  expect(shell.openUrl).not.toHaveBeenCalled();
  expect(onSelect).toHaveBeenCalledWith(1, "toggle");
});

test("без адреса Jira — ссылок нет", () => {
  transcript(null as never);
  expect(screen.queryByRole("link")).toBeNull();
});

test("поиск по ключу: подсветка внутри ссылки, текущее совпадение отмечено", async () => {
  const { container } = transcript();
  await userEvent.type(screen.getByRole("searchbox", { name: "Найти в расшифровке" }), "\"SPR-131\"");
  await waitFor(() => expect(container.querySelector("mark.hit")).not.toBeNull());
  const link = screen.getByRole("link", { name: "SPR-131" });
  const mark = link.querySelector("mark.hit")!;
  expect(mark).toHaveAttribute("data-hit", "0");
  expect(mark).toHaveClass("hit--current");
});

test("совпадение поиска шире ключа режется по границе ссылки, номер у кусков один", () => {
  const text = "в SPR-131 сегодня";
  const { container } = render(<p><LinkedText text={text} ranges={[[0, 9], [10, 17]]} firstHit={4} linker={linker} /></p>);
  const marks = [...container.querySelectorAll("mark")];
  expect(marks.map((m) => [m.textContent, m.getAttribute("data-hit"), !!m.closest("a")])).toEqual([
    ["в ", "4", false], ["SPR-131", "4", true], ["сегодня", "5", false],
  ]);
  expect(container.textContent).toBe(text);
});

test("наблюдения: ключи задач — ссылки", () => {
  const analysis: Analysis = {
    version: 1, model: "t", created_at: 1, fingerprint: "f", segments: 2, features: ["insights"],
    insights: [{ id: "i1", kind: "followup", text: "Проверить порог в OPS-7.", refs: [1], why: "См. SPR-131." }],
  };
  const view = buildView(TURNS, analysis, 2, { types: false, importance: false, chapters: false, insights: true });
  transcript(linker, { view });
  const block = screen.getByRole("region", { name: "Наблюдения анализа встречи" });
  expect(within(block).getByRole("link", { name: "OPS-7" })).toHaveAttribute("href", "https://jira.example.com/browse/OPS-7");
});

test("итоги: ключи — ссылки, но не внутри `кода`", () => {
  render(<Markdown source={"## Задачи\n\n- **SPR-131**: экспорт\n- код `OPS-7` не трогаем"} jira={linker} />);
  expect(screen.getByRole("link", { name: "SPR-131" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "OPS-7" })).toBeNull();
  expect(screen.getByText("OPS-7").tagName).toBe("CODE");
});

test("двойной щелчок по ключу (выделение слова) не открывает задачу второй раз; ссылку не тащат", () => {
  transcript();
  const link = screen.getByRole("link", { name: "SPR-131" });
  fireEvent.click(link, { detail: 1 });
  fireEvent.click(link, { detail: 2 });
  expect(shell.openUrl).toHaveBeenCalledTimes(1);
  expect(link).toHaveAttribute("draggable", "false");
});

// --- 0.3.1: задачи, сказанные словами (ссылки от резидента) ----------------------------------

const SPOKEN = [
  seg(0, "Анна", "Смотрим орион двадцать один двадцать два, там экспорт."),
  seg(1, "Анна", "И в баге 4452 тоже."),
  seg(10, "Борис", "Про SPR-131 ещё поговорим, орион 2122 закрыт."),
];
const SPOKEN_TURNS = mergeTurns(SPOKEN);
const REFS: JiraRefs = {
  refs: [
    { segment: 0, start: 8, end: 40, key: "ORION-2122", source: "spoken", spoken: "орион двадцать один двадцать два" },
    { segment: 1, start: 9, end: 13, key: "KDEV-4452", source: "context", spoken: "4452" },
    { segment: 2, start: 4, end: 11, key: "SPR-131", source: "literal", spoken: "SPR-131" },
    { segment: 2, start: 27, end: 37, key: "ORION-2122", source: "spoken", spoken: "орион 2122" },
  ],
  phrases: [{ text: "кей дев сорок четыре", key: "KDEV-44", source: "spoken" }],
};

function spoken(props: Partial<Parameters<typeof TranscriptView>[0]> = {}, refs = REFS, turns = SPOKEN_TURNS) {
  return render(
    <JiraLinks.Provider value={jiraCard(linker, refs, turns)}>
      <TranscriptView turns={turns} colors={new Map()} playable onPlay={() => {}} {...props} />
    </JiraLinks.Provider>,
  );
}

test("сказанная задача — значок с ключом поверх слов; подсказка — слова и «открыть в Jira»", async () => {
  const { container } = spoken();
  const turns = within(container.querySelector<HTMLElement>(".turns")!);
  const [first, again] = turns.getAllByRole("link", { name: "ORION-2122" });
  expect(first).toHaveClass("jira-ref");
  expect(first).toHaveAttribute("data-key", "ORION-2122");
  expect(first).toHaveAttribute("href", "https://jira.example.com/browse/ORION-2122");
  expect(first).toHaveAccessibleDescription("«орион двадцать один двадцать два» → ORION-2122 · открыть в Jira");
  expect(first!.textContent).toBe("орион двадцать один двадцать два");
  expect(again!.textContent).toBe("орион 2122");
  // Ключ, написанный текстом, — обычная ссылка.
  expect(turns.getByRole("link", { name: "SPR-131" })).not.toHaveClass("jira-ref");
  // Текст реплики в DOM не изменился (ключ — псевдоэлемент).
  const text = container.querySelector("[data-turn='0'] .turn__text")!;
  expect(text.textContent).toBe("Смотрим орион двадцать один двадцать два, там экспорт. И в баге 4452 тоже.");
  await userEvent.click(turns.getByRole("link", { name: "KDEV-4452" }));
  expect(shell.openUrl).toHaveBeenCalledWith("https://jira.example.com/browse/KDEV-4452");
});

test("ссылки — только от резидента: «орион 2122» без его ответа — не ссылка", () => {
  render(
    <JiraLinks.Provider value={jiraCard(linker, { refs: [], phrases: [] }, SPOKEN_TURNS)}>
      <TranscriptView turns={SPOKEN_TURNS} colors={new Map()} playable onPlay={() => {}} />
    </JiraLinks.Provider>,
  );
  expect(screen.queryByRole("link")).toBeNull();
});

test("текст поправили после ответа резидента — те же слова ищутся заново, пропавшие — без ссылки", () => {
  const edited = mergeTurns([
    seg(0, "Анна", "Да, смотрим орион двадцать один двадцать два, там экспорт."),
    seg(1, "Анна", "И в баге тоже."),
    SPOKEN[2]!,
  ]);
  const links = turnLinks(edited, REFS.refs);
  const text = edited[0]!.texts.join(" ");
  expect(links.get(0)!.map((m) => [m.key, text.slice(m.start, m.end)])).toEqual([
    ["ORION-2122", "орион двадцать один двадцать два"]]);
  expect(links.get(1)!.map((m) => m.key)).toEqual(["SPR-131", "ORION-2122"]);
});

test("«Задачи Jira»: уникальные ключи с репликами в строке над лентой; ключ открывает Jira, реплика — переход", async () => {
  spoken();
  // Без анализа — та же строка, только с задачами (ни блока «Наблюдения», ни фильтров).
  expect(screen.queryByRole("region")).toBeNull();
  const list = screen.getByRole("group", { name: "Задачи Jira, названные во встрече" });
  expect(list).toHaveTextContent(/^Задачи Jira:/);
  const items = within(list).getAllByRole("listitem");
  expect(items.map((li) => within(li).getByRole("link").textContent)).toEqual(["ORION-2122", "KDEV-4452", "SPR-131"]);
  // ORION-2122 звучит в двух репликах — у неё две: время на виду, спикер — в имени кнопки.
  expect(within(items[0]!).getAllByRole("button").map((b) => b.textContent)).toEqual(["00:00", "00:10"]);
  expect(within(items[0]!).getAllByRole("button").map((b) => b.getAttribute("aria-label")))
    .toEqual(["00:00 · Анна", "00:10 · Борис"]);
  await userEvent.click(within(items[0]!).getByRole("link", { name: "ORION-2122" }));
  expect(shell.openUrl).toHaveBeenCalledWith("https://jira.example.com/browse/ORION-2122");
});

test("«Задачи Jira» — в строке фильтров, не в блоке «Наблюдения»", () => {
  const analysis: Analysis = {
    version: 1, model: "t", created_at: 1, fingerprint: "f", segments: 3, features: ["insights"],
    insights: [{ id: "i1", kind: "followup", text: "Проверить кей дев сорок четыре.", refs: [1], why: "" }],
  };
  const view = buildView(SPOKEN_TURNS, analysis, 3, { types: false, importance: false, chapters: false, insights: true });
  spoken({ view });
  const block = screen.getByRole("region", { name: "Наблюдения анализа встречи" });
  expect(within(block).queryByRole("group", { name: "Задачи Jira, названные во встрече" })).toBeNull();
  expect(screen.getByRole("group", { name: "Задачи Jira, названные во встрече" })).toBeInTheDocument();
  // Наблюдение: фраза от резидента — значок с ключом.
  const link = within(block).getByRole("link", { name: "KDEV-44" });
  expect(link).toHaveClass("jira-ref");
  expect(link.textContent).toBe("кей дев сорок четыре");
});

test("итоги: фразы от резидента — ссылки, ключи текстом — тоже", () => {
  render(<Markdown source={"- Починить SPR-131\n- Обсудили кей дев сорок четыре, `кей дев сорок четыре` — нет"}
    jira={jiraCard(linker, REFS, SPOKEN_TURNS)} />);
  expect(screen.getByRole("link", { name: "SPR-131" })).toBeInTheDocument();
  const links = screen.getAllByRole("link", { name: "KDEV-44" });
  expect(links).toHaveLength(1);
  expect(links[0]).toHaveAccessibleDescription("«кей дев сорок четыре» → KDEV-44 · открыть в Jira");
});
