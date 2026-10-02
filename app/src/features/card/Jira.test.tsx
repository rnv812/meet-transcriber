/**
 * Ссылки на задачи Jira в карточке (M3): реплики (вместе с подсветкой
 * поиска), итоги, наблюдения; открытие через open_url. Данные выдуманные.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { buildView } from "../../lib/analysisView";
import { jiraLinker, JiraLinks } from "../../lib/jira";
import { Markdown } from "../../lib/markdown";
import * as shell from "../../lib/shell";
import { mergeTurns } from "../../lib/speakers";
import type { Analysis, Segment } from "../../lib/types";
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
  expect(link).toHaveAttribute("title", "Открыть SPR-131 в Jira");
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
