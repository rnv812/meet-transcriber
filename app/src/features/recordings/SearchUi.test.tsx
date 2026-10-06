import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { QueryChips } from "./QueryChips";
import { RecordingsList } from "./RecordingsList";
import * as api from "../../lib/api";
import type { Chip } from "../../lib/libraryQuery";
import type { Category, Facets, LibraryItem, Recording } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getFacets: vi.fn(),
  getParticipants: vi.fn(),
  getCategoriesInfo: vi.fn(),
}));

const ep = { base: "/api", token: null };
const categories: Category[] = [
  { id: "daily", name: "Дейлик", color: "#4c8bf5", description: "" },
  { id: "retro", name: "Ретроспектива", color: "#a0703c", description: "" },
];
const groups = [{ id: "g-alpha001", name: "Проект Альфа", color: "#2fa36b" }];
const rec = (id: string, title: string | null, started_at: string | null, extra: Partial<LibraryItem> = {}): LibraryItem => ({
  id, path: `C:/rec/${id}`, started_at, duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: true, has_voices: false, title, source: "record", ...extra,
});
const items: Recording[] = [
  rec("t1", "Утренняя", "2026-10-06T09:00:00"),
  rec("s1", "Сентябрьская", "2026-09-10T10:00:00"),
  rec("n1", "Безымянная дата", null),
];
const refresh = vi.fn(async () => {});
const resident = { status: "online" as const, endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 };
const facets = (over: Partial<Facets> = {}): Facets => ({
  total: 12, scope: "library",
  categories: { items: [{ id: "daily", count: 5 }, { id: "retro", count: 0 }], none: 7 },
  groups: { items: [{ id: "g-alpha001", count: 4 }], unknown: [], none: 8 },
  people: [{ name: "Анна Петрова", count: 6 }, { name: "Борис", count: 2 }],
  has: { summary: 3, analysis: 0, assistant: 1, transcript: 12 },
  duration: { lt15: 2, m15_60: 7, gt60: 3 }, ...over,
});

/** Как в App: текст, метки и категории живут снаружи списка. */
function Harness({ list = items, initialQ = "", initialChips = [] as Chip[], initialCats = [] as string[], onState }: {
  list?: LibraryItem[]; initialQ?: string; initialChips?: Chip[]; initialCats?: string[];
  onState?: (s: { q: string; chips: Chip[]; cats: string[] }) => void;
}) {
  const [q, setQ] = useState(initialQ);
  const [chips, setChips] = useState<Chip[]>(initialChips);
  const [cats, setCats] = useState<string[]>(initialCats);
  onState?.({ q, chips, cats });
  return (
    <RecordingsList selected={null} onSelect={onSelect} library={{ items: list, jobs: [], loading: false, error: null, refresh }}
      resident={resident} q={q} onQ={setQ} chips={chips} onChips={setChips} groups={groups}
      categories={categories} categoryFilter={cats} onCategoryFilter={setCats} />
  );
}
const onSelect = vi.fn();

let state: { q: string; chips: Chip[]; cats: string[] };
const track = (s: typeof state) => { state = s; };
const box = () => screen.getByRole("combobox", { name: "Поиск по записям" });
const listbox = () => document.getElementById(box().getAttribute("aria-controls")!)!;
const options = () => within(listbox()).queryAllByRole("option").map((o) => o.textContent);
const chipRow = () => screen.queryByRole("group", { name: "Условия поиска" });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-06T12:00:00"));
  window.localStorage.clear();
  vi.clearAllMocks();
  vi.mocked(api.getFacets).mockResolvedValue(facets());
  vi.mocked(api.getParticipants).mockResolvedValue([]);
});
afterEach(() => vi.useRealTimers());

// --- combobox ------------------------------------------------------------------------

test("строка поиска — combobox ARIA 1.2: список подсказок, текущий вариант, ↓/↑, Esc закрывает только список", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  const input = box();
  expect(input).toHaveAttribute("type", "search");
  expect(input).toHaveAttribute("aria-expanded", "false");
  expect(input).toHaveAttribute("aria-autocomplete", "list");
  expect(input.getAttribute("aria-controls")).toBe(listbox().id);
  expect(input).not.toHaveAttribute("aria-activedescendant");
  await user.type(input, "кат");
  expect(input).toHaveAttribute("aria-expanded", "true");
  expect(options()).toEqual(["Искать «кат» в текстеEnter", "категория:встречи этой категории"]);
  const all = within(listbox()).getAllByRole("option");
  expect(all[0]).toHaveAttribute("aria-selected", "true");
  expect(input).toHaveAttribute("aria-activedescendant", all[0]!.id);
  await user.keyboard("{ArrowDown}");
  expect(all[1]).toHaveAttribute("aria-selected", "true");
  expect(input).toHaveAttribute("aria-activedescendant", all[1]!.id);
  await user.keyboard("{ArrowDown}");
  expect(input).toHaveAttribute("aria-activedescendant", all[0]!.id);
  await user.keyboard("{ArrowUp}");
  expect(input).toHaveAttribute("aria-activedescendant", all[1]!.id);
  // Дописать префикс: в поле «категория:», список — уже со значениями.
  await user.keyboard("{Enter}");
  expect(state.q).toBe("категория:");
  expect(input).toHaveFocus();
  expect(options()).toEqual(["Применить условияEnter", "Без категории", "Дейлик", "Ретроспектива"]);
  await user.keyboard("{Escape}");
  expect(input).toHaveAttribute("aria-expanded", "false");
  expect(state.q).toBe("категория:");
  // ↓ открывает снова; щелчок по варианту ставит метку (категория — в запоминаемые).
  await user.keyboard("{ArrowDown}");
  await user.click(within(listbox()).getByRole("option", { name: "Ретроспектива" }));
  expect(state).toMatchObject({ q: "", cats: ["retro"] });
  expect(within(chipRow()!).getByText("Ретроспектива")).toBeInTheDocument();
  expect(input).toHaveFocus();
});

test("Enter без выбора ищет текст, а готовые префиксы — в метки; Backspace в пустом поле снимает последнюю", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} initialCats={["daily"]} />);
  await user.type(box(), "бюджет группа:альфа дольше:30м{Enter}");
  expect(state).toMatchObject({ q: "бюджет ", cats: ["daily"],
    chips: [{ kind: "group", value: "g-alpha001" }, { kind: "longer", value: "1800" }] });
  expect(box()).toHaveAttribute("aria-expanded", "false");
  expect(within(chipRow()!).getAllByRole("button", { name: /^Убрать/ }).map((b) => b.getAttribute("aria-label"))).toEqual([
    "Убрать «Дейлик» из фильтра", "Убрать «Проект Альфа» из фильтра", "Убрать «Дольше 30 мин» из фильтра"]);
  await user.clear(box());
  await user.keyboard("{Backspace}");
  expect(state.chips).toEqual([{ kind: "group", value: "g-alpha001" }]);
  await user.keyboard("{Backspace}{Backspace}");
  expect(state).toMatchObject({ chips: [], cats: [] });
  expect(chipRow()).toBeNull();
});

test("числовая дата — сразу метка по Enter; «Искать «05.10» как текст» — фразой", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.type(box(), "05.10");
  expect(options()).toEqual(["Дата: 5 октябряEnter", "Искать «05.10» как текст"]);
  await user.keyboard("{ArrowDown}{Enter}");
  expect(state).toMatchObject({ q: `"05.10"`, chips: [] });
  await user.clear(box());
  await user.type(box(), "05.10{Enter}");
  expect(state).toMatchObject({ q: "", chips: [{ kind: "date", value: "2026-10-05..2026-10-05", label: "5 октября" }] });
  expect(within(chipRow()!).getByText("5 октября")).toBeInTheDocument();
});

test("словесная дата без префикса — только подсказка: Enter ищет текст", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.type(box(), "вчера");
  expect(options()).toEqual(["Искать «вчера» в текстеEnter", "Дата: Вчера5 октября"]);
  await user.keyboard("{Enter}");
  expect(state).toMatchObject({ q: "вчера", chips: [] });
  await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
  expect(state).toMatchObject({ q: "", chips: [{ kind: "date", value: "2026-10-05..2026-10-05", label: "Вчера" }] });
});

test("участники — с задержкой, по последнему слову; прежний запрос отменяется; выбор — метка", async () => {
  vi.mocked(api.getParticipants).mockImplementation(async (_ep, q) =>
    (q === "Ан" ? [{ name: "Анна Петрова", meetings: 6, last_at: null, owner: false }] : []));
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.type(box(), "бюджет участник:Ан");
  await waitFor(() => expect(options()).toContain("Анна Петрова6 встреч"));
  // Набор быстрее задержки: запрос — один, за последнее слово.
  expect(api.getParticipants).toHaveBeenCalledTimes(1);
  expect(api.getParticipants).toHaveBeenCalledWith(ep, "Ан", 8, expect.any(AbortSignal));
  await user.click(within(listbox()).getByRole("option", { name: /Анна Петрова/ }));
  expect(state).toMatchObject({ q: "бюджет ", chips: [{ kind: "person", value: "Анна Петрова" }] });
});

// --- метки и «Фильтры» — одно состояние --------------------------------------------

const panel = () => screen.getByRole("dialog", { name: "Фильтры" });
const dim = (name: string) => within(panel()).getByRole("group", { name });
const check = (dimName: string, label: string) => within(dim(dimName)).getByRole("checkbox", { name: label });

test("«Фильтры»: щелчок ставит ту же метку, что префикс; метка снята — флажок снят", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.click(screen.getByRole("button", { name: "Фильтры" }));
  await waitFor(() => expect(check("Участник", "Анна Петрова").closest("label")).toHaveTextContent("Анна Петрова6"));
  expect(check("Группа", "Проект Альфа").closest("label")).toHaveTextContent("Проект Альфа4");
  expect(check("Группа", "Без группы").closest("label")).toHaveTextContent("Без группы8");
  expect(check("Есть", "Анализ").closest("label")).toHaveClass("filters__option--zero");
  expect(check("Длительность", "15–60 мин").closest("label")).toHaveTextContent("15–60 мин7");

  await user.click(check("Участник", "Анна Петрова"));
  await user.click(check("Группа", "Проект Альфа"));
  await user.click(check("Есть", "Итоги"));
  await user.click(check("Длительность", "15–60 мин"));
  expect(state.chips).toEqual([
    { kind: "person", value: "Анна Петрова" }, { kind: "group", value: "g-alpha001" }, { kind: "has", value: "summary" },
    { kind: "longer", value: "900" }, { kind: "shorter", value: "3600" }]);
  // Счётчики — для нового фильтра, с задержкой, прежние запросы отменены.
  await waitFor(() => expect(api.getFacets).toHaveBeenLastCalledWith(ep, undefined, {
    groups: ["g-alpha001"], people: ["Анна Петрова"], has: ["summary"], min_s: 900, max_s: 3600 }, expect.any(AbortSignal)));
  const signals = vi.mocked(api.getFacets).mock.calls.map((c) => c[3]!);
  expect(signals.slice(0, -1).every((s) => s.aborted)).toBe(true);
  // Другая длительность заменяет, повторный щелчок снимает.
  await user.click(check("Длительность", "Больше 1 ч"));
  expect(state.chips.filter((c) => c.kind === "longer" || c.kind === "shorter")).toEqual([{ kind: "longer", value: "3600" }]);
  expect(check("Длительность", "15–60 мин")).not.toBeChecked();
  await user.click(check("Длительность", "Больше 1 ч"));
  expect(state.chips.some((c) => c.kind === "longer")).toBe(false);
  await user.keyboard("{Escape}");

  // Метка снята «✕» — флажок в панели снят.
  await user.click(within(chipRow()!).getByRole("button", { name: "Убрать «Анна Петрова» из фильтра" }));
  await user.click(screen.getByRole("button", { name: /^Фильтры/ }));
  expect(check("Участник", "Анна Петрова")).not.toBeChecked();
  expect(check("Группа", "Проект Альфа")).toBeChecked();
  await user.keyboard("{Escape}");

  // Набранный префикс — та же метка: панель его видит (открытие сначала переносит текст в метки).
  await user.type(box(), "нет:анализ");
  await user.click(screen.getByRole("button", { name: /^Фильтры/ }));
  expect(state.q).toBe("");
  expect(within(chipRow()!).getByText("Нет анализа")).toBeInTheDocument();
  expect(check("Есть", "Анализ")).not.toBeChecked();  // «нет» — не «есть»
  await user.click(check("Есть", "Анализ"));
  expect(state.chips.filter((c) => c.value === "analysis")).toEqual([{ kind: "has", value: "analysis" }]);
});

test("«Период»: эта неделя, этот месяц, «Выбрать…» через dateExpr", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.click(screen.getByRole("button", { name: "Фильтры" }));
  await user.click(check("Период", "Эта неделя"));
  expect(state.chips).toEqual([{ kind: "date", value: "2026-10-05..2026-10-11", label: "Эта неделя", expr: "дата:эта неделя" }]);
  await user.click(check("Период", "Этот месяц"));
  expect(state.chips).toEqual([{ kind: "date", value: "2026-10-01..2026-10-31", label: "Этот месяц", expr: "дата:этот месяц" }]);
  const pick = within(dim("Период")).getByRole("textbox", { name: "Выбрать период" });
  await user.type(pick, "абв");
  expect(within(dim("Период")).getByRole("status")).toHaveTextContent("Не понял дату");
  await user.clear(pick);
  await user.type(pick, "с 1.09 по 15.09");
  expect(within(dim("Период")).getByRole("status")).toHaveTextContent("1 сен – 15 сен");
  await user.keyboard("{Enter}");
  expect(state.chips).toEqual([{ kind: "date", value: "2026-09-01..2026-09-15", label: "1 сен – 15 сен" }]);
  expect(check("Период", "1 сен – 15 сен")).toBeChecked();
  expect(check("Период", "Этот месяц")).not.toBeChecked();
  await user.click(check("Период", "1 сен – 15 сен"));
  expect(state.chips).toEqual([]);
});

test("«ещё…» у участников — к строке поиска с «участник:», список участников сразу открыт", async () => {
  vi.mocked(api.getParticipants).mockResolvedValue([
    { name: "Анна Петрова", meetings: 6, last_at: null, owner: false },
    { name: "Борис", meetings: 2, last_at: null, owner: false }]);
  const user = userEvent.setup();
  render(<Harness onState={track} initialQ="бюджет" />);
  await user.click(screen.getByRole("button", { name: "Фильтры" }));
  await user.click(within(dim("Участник")).getByRole("button", { name: "ещё…" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(state.q).toBe("бюджет участник:");
  await waitFor(() => expect(box()).toHaveFocus());
  await waitFor(() => expect(options()).toEqual(["Искать «бюджет» в текстеEnter", "Анна Петрова6 встреч", "Борис2 встречи"]));
  expect(box()).toHaveAttribute("aria-expanded", "true");
  expect(api.getParticipants).toHaveBeenCalledWith(ep, "", 8, expect.any(AbortSignal));
  await user.keyboard("{ArrowDown}{Enter}");
  expect(state).toMatchObject({ q: "бюджет ", chips: [{ kind: "person", value: "Анна Петрова" }] });
});

test("список не открывается, когда в нём только «Искать…»; ↓ открывает и его", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.type(box(), "бюджет");
  expect(box()).toHaveAttribute("aria-expanded", "false");
  expect(listbox()).not.toBeVisible();
  await user.keyboard("{ArrowDown}");
  expect(box()).toHaveAttribute("aria-expanded", "true");
  expect(options()).toEqual(["Искать «бюджет» в текстеEnter"]);
  // Дальше набор — снова по правилу: один вариант — список закрыт.
  await user.type(box(), "ы");
  expect(box()).toHaveAttribute("aria-expanded", "false");
});

test("негодный префикс — пояснение в списке: aria-disabled, стрелки и щелчок его не выбирают", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.type(box(), "бюджет группа:нет-такой");
  expect(options()).toEqual(["Искать «бюджет» в текстеEnter", "Нет группы «нет-такой»"]);
  const hint = within(listbox()).getByRole("option", { name: "Нет группы «нет-такой»" });
  expect(hint).toHaveAttribute("aria-disabled", "true");
  await user.keyboard("{ArrowDown}");
  expect(box().getAttribute("aria-activedescendant")).not.toBe(hint.id);
  await user.click(hint);
  expect(state.q).toBe("бюджет группа:нет-такой");
  // Enter — поиск; негодный префикс остаётся в поле, чтобы его поправить.
  await user.keyboard("{Enter}");
  expect(state).toMatchObject({ q: "бюджет группа:нет-такой", chips: [] });
});

test("IME: Enter, завершающий набор (keyCode 229), не ищет", () => {
  render(<Harness onState={track} />);
  fireEvent.change(box(), { target: { value: "после:5.10" } });
  fireEvent.keyDown(box(), { key: "Enter", keyCode: 229 });
  expect(state).toMatchObject({ q: "после:5.10", chips: [] });
  fireEvent.keyDown(box(), { key: "Enter", keyCode: 13 });
  expect(state.chips).toEqual([{ kind: "date", value: "2026-10-05..", label: "с 5 октября" }]);
});

test("открытие «Фильтров» переносит «после:» и «до:» одним диапазоном — фильтр не шире", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.type(box(), "после:1.09 до:15.09");
  await user.click(screen.getByRole("button", { name: "Фильтры" }));
  expect(state).toMatchObject({ q: "", chips: [{ kind: "date", value: "2026-09-01..2026-09-15", label: "1 сен – 15 сен" }] });
  expect(check("Период", "1 сен – 15 сен")).toBeChecked();
  await waitFor(() => expect(api.getFacets).toHaveBeenLastCalledWith(ep, undefined,
    { from: "2026-09-01", to: "2026-09-15" }, expect.any(AbortSignal)));
});

test("резидент без /facets: в «Фильтрах» только категории — прочие условия он бы пропустил", async () => {
  vi.mocked(api.getFacets).mockRejectedValue(new api.ApiError(404, "нет"));
  vi.mocked(api.getCategoriesInfo).mockResolvedValue({
    categories, defaults: categories, counts: { daily: 3 }, none: 1, scope: "library" });
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  await user.click(screen.getByRole("button", { name: "Фильтры" }));
  await waitFor(() => expect(within(dim("Категория")).getByRole("checkbox", { name: "Дейлик" }).closest("label"))
    .toHaveTextContent("Дейлик3"));
  expect(within(panel()).getAllByRole("group").map((g) => g.querySelector("legend")?.textContent)).toEqual(["Категория"]);
  expect(within(panel()).getByText(/Служба записи старой версии/)).toBeInTheDocument();
});

test("при поиске «Свернуть все» из меню раздела сворачивает — до конца поиска, без запоминания", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} initialQ="бюджет" />);
  fireEvent.contextMenu(screen.getByRole("button", { name: /^Сентябрь ·/ }), { clientX: 10, clientY: 10 });
  await user.click(screen.getByRole("menuitem", { name: "Свернуть все" }));
  for (const name of [/^Сегодня ·/, /^Сентябрь ·/, /^Без даты ·/]) {
    expect(screen.getByRole("button", { name })).toHaveAttribute("aria-expanded", "false");
  }
  expect(window.localStorage.getItem("meet.sections.v1")).toBeNull();
  fireEvent.contextMenu(screen.getByRole("button", { name: /^Сентябрь ·/ }), { clientX: 10, clientY: 10 });
  await user.click(screen.getByRole("menuitem", { name: "Свернуть остальные" }));
  expect(screen.getByRole("button", { name: /^Сентябрь ·/ })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: /^Сегодня ·/ })).toHaveAttribute("aria-expanded", "false");
  expect(window.localStorage.getItem("meet.sections.v1")).toBeNull();
});

test("«Сбросить» в метках и «Сбросить все» в панели снимают и категории, и метки сеанса", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} initialCats={["daily"]} initialChips={[{ kind: "person", value: "Борис" }]} />);
  await user.click(within(chipRow()!).getByRole("button", { name: "Сбросить" }));
  expect(state).toMatchObject({ chips: [], cats: [] });
});

test("условия сеанса без результата — «Сбросить условия» (запомненные категории остаются)", async () => {
  const user = userEvent.setup();
  render(<Harness list={[]} onState={track} initialQ="бюджет дата:вчера" initialCats={["daily"]}
    initialChips={[{ kind: "person", value: "Борис" }]} />);
  await user.click(screen.getByRole("button", { name: "Сбросить условия" }));
  expect(state).toMatchObject({ q: "бюджет", chips: [], cats: ["daily"] });
});

// --- результаты -----------------------------------------------------------------------

test("подсветка названия по title_ranges, участника в подписи фрагмента; «Ещё совпадений» — открыть запись", async () => {
  const found = [rec("a", "Бюджет на квартал", "2026-10-06T09:00:00", {
    title_ranges: [[0, 6]], total: 4,
    hits: [{ t: 30, speaker: "Анна Петрова", snippet: "по бюджету", ranges: [[3, 10]] },
      { t: 60, speaker: "Борис", snippet: "бюджет", ranges: [[0, 6]] }],
  })];
  render(<Harness list={found} initialQ="бюджет" initialChips={[{ kind: "person", value: "Анна" }]} />);
  const row = screen.getByText("на квартал", { exact: false }).closest("li")!;
  expect([...row.querySelectorAll(".rec-item__title mark.hit")].map((m) => m.textContent)).toEqual(["Бюджет"]);
  expect([...row.querySelectorAll(".rec-hit__who mark.hit")].map((m) => m.textContent)).toEqual(["Анна"]);
  expect(screen.getByText("Найдено: 1 встреча, 4 места")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Ещё совпадений: 2" }));
  expect(onSelect).toHaveBeenCalledWith("a");
});

test("с условиями сеанса разделы развёрнуты и «Найдено…» — без мест; одни категории — как раньше", () => {
  const old = [rec("y", "Давняя", "2024-03-01T10:00:00")];
  const { unmount } = render(<Harness list={old} initialCats={["daily"]} />);
  expect(screen.getByRole("button", { name: /^2024/ })).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText(/^Найдено/)).toBeNull();
  unmount();
  render(<Harness list={old} initialChips={[{ kind: "person", value: "Анна" }]} />);
  expect(screen.getByRole("button", { name: /^2024/ })).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Найдено: 1 встреча")).toBeInTheDocument();
});

// --- «Только этот период» -------------------------------------------------------------

test("меню заголовка: «Только этот период» ставит метку даты раздела; у «Без даты» пункта нет", async () => {
  const user = userEvent.setup();
  render(<Harness onState={track} />);
  fireEvent.contextMenu(screen.getByRole("button", { name: /^Сентябрь ·/ }), { clientX: 10, clientY: 10 });
  const menu = screen.getByRole("menu", { name: "Разделы" });
  expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent))
    .toEqual(["Свернуть все", "Развернуть все", "Свернуть остальные", "Только этот период"]);
  await user.click(within(menu).getByRole("menuitem", { name: "Только этот период" }));
  // 30 сентября (6 дней назад) — в разделе по дням: месяц — по 29-е.
  expect(state.chips).toEqual([{ kind: "date", value: "2026-09-01..2026-09-29", label: "Сентябрь" }]);
  expect(within(chipRow()!).getByText("Сентябрь")).toBeInTheDocument();
  // Другой раздел заменяет период.
  fireEvent.contextMenu(screen.getByRole("button", { name: /^Сегодня ·/ }), { clientX: 10, clientY: 10 });
  await user.click(screen.getByRole("menuitem", { name: "Только этот период" }));
  expect(state.chips).toEqual([{ kind: "date", value: "2026-10-06..2026-10-06", label: "Сегодня", expr: "дата:сегодня" }]);
  fireEvent.contextMenu(screen.getByRole("button", { name: /^Без даты ·/ }), { clientX: 10, clientY: 10 });
  expect(within(screen.getByRole("menu")).queryByRole("menuitem", { name: "Только этот период" })).toBeNull();
});

test("QueryChips: подпись каждой метки, «✕» — по одной, «Сбросить» — все", async () => {
  const onRemove = vi.fn();
  const onClear = vi.fn();
  const chips: Chip[] = [
    { kind: "category", value: "_none" }, { kind: "group", value: "g-alpha001" }, { kind: "group", value: "_none" },
    { kind: "person", value: "Анна" }, { kind: "date", value: "2026-09-01..2026-09-30", label: "Сентябрь" },
    { kind: "has", value: "summary" }, { kind: "lacks", value: "assistant" }, { kind: "longer", value: "900" },
    { kind: "shorter", value: "3600" }, { kind: "title", value: "релиз" },
  ];
  render(<QueryChips chips={chips} ctx={{ categories, groups }} onRemove={onRemove} onClear={onClear} />);
  const row = screen.getByRole("group", { name: "Условия поиска" });
  expect([...row.querySelectorAll(".cat-filter__chip-text")].map((c) => c.textContent)).toEqual([
    "Без категории", "Проект Альфа", "Без группы", "Анна", "Сентябрь", "Есть итоги", "Без ассистента",
    "Дольше 15 мин", "Короче 1 ч", "В названии: релиз"]);
  expect(row.querySelector(".query-chip--group")).toHaveAttribute("title", "Группа: Проект Альфа");
  await userEvent.click(within(row).getByRole("button", { name: "Убрать «Анна» из фильтра" }));
  expect(onRemove).toHaveBeenCalledWith({ kind: "person", value: "Анна" });
  await userEvent.click(within(row).getByRole("button", { name: "Сбросить" }));
  expect(onClear).toHaveBeenCalled();
});

test("«название:» без текста поиска: подсветка названия — своя, по правилам поиска", () => {
  const list = [rec("q", "Бюджет на квартал", "2026-10-06T09:00:00"), rec("r", "Ретро", "2026-10-06T08:00:00")];
  render(<Harness list={list} initialChips={[{ kind: "title", value: "кварталу" }]} />);
  const row = document.querySelector('[data-rec-id="q"]')!;
  expect([...row.querySelectorAll(".rec-item__title mark.hit")].map((m) => m.textContent)).toEqual(["квартал"]);
  expect(within(chipRow()!).getByText("В названии: кварталу")).toBeInTheDocument();
});
