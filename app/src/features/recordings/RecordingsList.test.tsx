import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingsList } from "./RecordingsList";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  patchRecording: vi.fn(async () => ({})),
  deleteRecording: vi.fn(async () => ({ ok: true })),
  kbExport: vi.fn(async () => ({ path: "D:/База/2026-09-30 - Планёрка", files: [], kept: [] })),
  mergeRecordings: vi.fn(),
}));
const killed: string[] = [];
vi.mock("../../lib/shell", async (orig) => ({
  ...(await orig<typeof import("../../lib/shell")>()),
  agentKillRecording: vi.fn(async (rid: string) => { killed.push(rid); }),
}));
import type { Job, Recording } from "../../lib/types";

const ep = { base: "/api", token: null };
const rec = (id: string, extra: Partial<Recording> = {}): Recording => ({
  id, path: `C:/rec/${id}`, started_at: "2026-09-30T10:00:00", duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: false, has_voices: false, title: null, source: "record", ...extra,
});
const job = (folder: string, extra: Partial<Job>): Job => ({
  id: "j1", kind: "transcribe", folder, state: "running", stage: "asr", label: null,
  done: 1, total: 2, note: null, result: null, error: null, ...extra,
});

const items = [
  rec("a", { has_transcript: true, title: "Планёрка" }),
  rec("b"),
  rec("c"),
];
const jobs = [
  job("C:/rec/b", {}),
  job("C:/rec/c", { state: "failed", error: "нет памяти" }),
];
const refresh = vi.fn(async () => {});
const library = { items, jobs, loading: false, error: null, refresh };
const resident = { status: "online" as const, endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 };

function setup(props: Partial<Parameters<typeof RecordingsList>[0]> = {}) {
  const onSelect = vi.fn();
  const onQ = vi.fn();
  render(
    <RecordingsList selected="a" onSelect={onSelect} library={library} resident={resident} q="" onQ={onQ} {...props} />,
  );
  return { onSelect, onQ };
}

// Сегодня — 6 октября: записи от 30 сентября в разделе «Среда, 30 сентября» (6 дней назад, развёрнут).
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-06T12:00:00"));
  window.localStorage.clear();
});
afterEach(() => vi.useRealTimers());

/** Строки записей во всех развёрнутых разделах, сверху вниз. */
const mains = () => [...document.querySelectorAll<HTMLButtonElement>(".rec-item > .rec-item__main")];

test("бейджи статусов: у готовой нет, у идущей процент, у упавшей ошибка", () => {
  setup();
  const opts = mains();
  expect(opts).toHaveLength(3);
  expect(opts[0]!).toHaveTextContent("Планёрка");
  expect(opts[0]!).not.toHaveTextContent(/Ошибка|Распознавание|очереди/);
  expect(opts[0]!).toHaveAttribute("aria-current", "true");
  expect(opts[1]!).toHaveTextContent("Распознавание 50%");
  expect(opts[1]!).not.toHaveAttribute("aria-current");
  expect(opts[2]!).toHaveTextContent("Ошибка");
});

test("клик по элементу вызывает onSelect(id)", async () => {
  const { onSelect } = setup();
  await userEvent.click(mains()[1]!);
  expect(onSelect).toHaveBeenCalledWith("b");
});

test("поиск по тексту: под записью фрагменты с подсветкой, клик — открыть на реплике", async () => {
  const found = [{
    ...rec("a", { has_transcript: true, title: "Планёрка" }),
    hits: [
      { t: 30, speaker: "Борис", snippet: "Бюджет утвердим завтра.", ranges: [[0, 6]] as [number, number][] },
      { t: 75, speaker: "Анна", snippet: "…по бюджету вопросов нет.", ranges: [[4, 11]] as [number, number][] },
    ],
    total: 5,
  }];
  const onOpenHit = vi.fn();
  setup({ library: { ...library, items: found }, q: "бюджет", onOpenHit });
  const hits = within(screen.getByRole("list", { name: "Найдено в записи «Планёрка»" })).getAllByRole("button");
  expect(hits).toHaveLength(2);
  expect(hits[0]!).toHaveTextContent("00:30Борис: Бюджет утвердим завтра.");
  expect([...document.querySelectorAll("mark.hit")].map((m) => m.textContent)).toEqual(["Бюджет", "бюджету"]);
  expect(screen.getByText("Ещё совпадений: 3")).toBeInTheDocument();
  await userEvent.click(hits[1]!);
  expect(onOpenHit).toHaveBeenCalledWith("a", 75);
});

test("ввод в поиск передаётся наверх", async () => {
  const { onQ } = setup();
  await userEvent.type(screen.getByRole("searchbox"), "а");
  expect(onQ).toHaveBeenLastCalledWith("а");
});

test("зона импорта: выбрать файл в браузере — сообщение, без пути", async () => {
  setup();
  expect(screen.getByText(/Перетащите аудио или видео сюда/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "выбрать файл" }));
  expect(await screen.findByText("Импорт — из приложения или перетаскиванием в окно приложения")).toBeInTheDocument();
});

// --- переименование и меню ------------------------------------------------------

const item = (name: string) => screen.getByText(name).closest("li")!;
const titleInput = () => screen.getByRole("textbox", { name: "Название записи" });

test("F2 на записи — поле на месте названия; Enter сохраняет, фокус возвращается", async () => {
  const onChanged = vi.fn();
  setup({ onChanged });
  mains()[0]!.focus();
  await userEvent.keyboard("{F2}");
  expect(titleInput()).toHaveValue("Планёрка");
  expect(titleInput()).toHaveAttribute("maxLength", "200");
  await userEvent.clear(titleInput());
  await userEvent.type(titleInput(), "  Планёрка команды  {Enter}");
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "a", { title: "Планёрка команды" });
  await vi.waitFor(() => expect(onChanged).toHaveBeenCalledWith("a"));
  expect(refresh).toHaveBeenCalled();
  expect(mains()[0]!).toHaveFocus();
});

test("Esc отменяет, пустое название — вернуть автоматическое, уход фокуса — сохранить", async () => {
  vi.mocked(api.patchRecording).mockClear();
  setup();
  await userEvent.dblClick(screen.getByText("Планёрка"));
  await userEvent.type(titleInput(), "Другое{Escape}");
  expect(api.patchRecording).not.toHaveBeenCalled();
  expect(screen.getByText("Планёрка")).toBeInTheDocument();
  await userEvent.dblClick(screen.getByText("Планёрка"));
  await userEvent.clear(titleInput());
  await userEvent.click(document.body);
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "a", { title: null });
});

test("меню «⋯»: пункты, переименование и удаление с подтверждением; Esc закрывает", async () => {
  const onDeleting = vi.fn();
  setup({ onDeleting, resident: { ...resident, snapshot: { meetings_dir: "D:/База" } as never } });
  const more = within(item("Планёрка")).getByRole("button", { name: "Действия с записью «Планёрка»" });
  await userEvent.click(more);
  const menu = screen.getByRole("menu", { name: "Действия с записью «Планёрка»" });
  expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(
    ["Переименовать", "Категория", "Экспорт в базу знаний", "Удалить…"]);
  expect(within(menu).getAllByRole("menuitem")[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowUp}");
  expect(within(menu).getByRole("menuitem", { name: "Удалить…" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(more).toHaveFocus();

  await userEvent.click(more);
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить…" }));
  // Подтверждение — общее окно: меню закрыто, фокус на «Отмена».
  expect(screen.queryByRole("menu")).toBeNull();
  const ask = screen.getByRole("alertdialog", { name: "Удалить запись?" });
  expect(ask).toHaveTextContent("Это действие нельзя отменить.");
  expect(within(ask).getByRole("button", { name: "Отмена" })).toHaveFocus();
  killed.length = 0;
  vi.mocked(api.deleteRecording).mockImplementationOnce(async () => {
    // Агент во вкладке «Агент» уже погашен: его рабочая папка не держит запись.
    expect(killed).toEqual(["a"]);
    return { ok: true };
  });
  await userEvent.click(within(ask).getByRole("button", { name: "Удалить" }));
  expect(onDeleting).toHaveBeenCalledWith("a");
  await vi.waitFor(() => expect(api.deleteRecording).toHaveBeenCalledWith(ep, "a"));

  await userEvent.click(more);
  await userEvent.click(screen.getByRole("menuitem", { name: "Переименовать" }));
  expect(titleInput()).toHaveFocus();
});

test("правая кнопка открывает то же меню; выгрузка в базу знаний сообщает путь", async () => {
  setup({ resident: { ...resident, snapshot: { meetings_dir: "D:/База" } as never } });
  fireEvent.contextMenu(item("Планёрка"), { clientX: 40, clientY: 50 });
  await userEvent.click(screen.getByRole("menuitem", { name: "Экспорт в базу знаний" }));
  expect(api.kbExport).toHaveBeenCalledWith(ep, "a");
  expect(await screen.findByRole("status")).toHaveTextContent("Выгружено в базу знаний: D:/База/2026-09-30 - Планёрка");
});

test("без папки для встреч и у нерасшифрованной записи — без выгрузки; ошибка видна", async () => {
  vi.mocked(api.patchRecording).mockRejectedValueOnce(new Error("записи нет"));
  setup();
  await userEvent.click(within(item("Планёрка")).getByRole("button", { name: /Действия/ }));
  expect(screen.queryByRole("menuitem", { name: "Экспорт в базу знаний" })).toBeNull();
  await userEvent.click(screen.getByRole("menuitem", { name: "Переименовать" }));
  await userEvent.type(titleInput(), "Х{Enter}");
  expect(await screen.findByRole("alert")).toHaveTextContent("записи нет");
});

test("новое название видно сразу, ошибка возвращает прежнее", async () => {
  let fail!: (e: Error) => void;
  vi.mocked(api.patchRecording).mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }));
  setup();
  mains()[0]!.focus();
  await userEvent.keyboard("{F2}");
  await userEvent.clear(titleInput());
  await userEvent.type(titleInput(), "Новое{Enter}");
  expect(mains()[0]!).toHaveTextContent("Новое");
  fail(new Error("записи нет"));
  expect(await screen.findByRole("alert")).toHaveTextContent("записи нет");
  expect(mains()[0]!).toHaveTextContent("Планёрка");
});

test("повторное нажатие «⋯» закрывает меню", async () => {
  setup();
  const more = within(item("Планёрка")).getByRole("button", { name: /Действия/ });
  await userEvent.click(more);
  expect(screen.getByRole("menu")).toBeInTheDocument();
  await userEvent.click(more);
  expect(screen.queryByRole("menu")).toBeNull();
  expect(more).toHaveAttribute("aria-expanded", "false");
});

// --- выбор нескольких и объединение ----------------------------------------------

const many = [
  rec("d", { title: "Первая часть" }),
  rec("e", { title: "Вторая часть" }),
  rec("f", { title: "Третья часть" }),
  rec("g", { title: "Пишется" }),
];
const pickLib = { ...library, items: many, jobs: [job("C:/rec/g", { state: "queued" })] };
const mainOf = (title: string) => screen.getByText(title).closest("button")!;
const pickBox = (title: string) => screen.queryByRole("checkbox", { name: `Выбрать «${title}»` });

test("Ctrl+щелчок — режим выбора: открытая и отмеченная, флажки у всех, «Объединить (2)»", async () => {
  vi.mocked(api.mergeRecordings).mockResolvedValue({ recording: "d_merged", job: job("C:/rec/d_merged", {}) });
  const { onSelect } = setup({ library: pickLib, selected: "d" });
  expect(pickBox("Первая часть")).toBeNull();
  const user = userEvent.setup();
  await user.keyboard("{Control>}");
  await user.click(mainOf("Вторая часть"));
  await user.keyboard("{/Control}");
  expect(onSelect).not.toHaveBeenCalled();
  expect(pickBox("Первая часть")).toBeChecked();
  expect(pickBox("Вторая часть")).toBeChecked();
  expect(pickBox("Третья часть")).not.toBeChecked();
  const bar = screen.getByRole("toolbar", { name: "Выбранные записи" });
  expect(within(bar).getByText("Выбрано: 2")).toBeInTheDocument();
  killed.length = 0;
  await user.click(within(bar).getByRole("button", { name: "Объединить (2)" }));
  await vi.waitFor(() => expect(api.mergeRecordings).toHaveBeenCalledWith(ep, ["d", "e"], false));
  // Исходные удалятся после расшифровки — агенты в их папках погашены заранее.
  expect(killed).toEqual(["d", "e"]);
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Встречи объединены. Исходные записи будут удалены после расшифровки");
  expect(onSelect).toHaveBeenCalledWith("d_merged");
  expect(refresh).toHaveBeenCalled();
  expect(screen.queryByRole("toolbar")).toBeNull();
});

test("Shift+щелчок — диапазон; флажок снимает; «Сохранить исходные записи» уходит в запрос", async () => {
  vi.mocked(api.mergeRecordings).mockResolvedValue({ recording: "d_merged", job: job("C:/rec/d_merged", {}) });
  setup({ library: pickLib, selected: "d" });
  const user = userEvent.setup();
  await user.keyboard("{Shift>}");
  await user.click(mainOf("Третья часть"));
  await user.keyboard("{/Shift}");
  expect(screen.getByText("Выбрано: 3")).toBeInTheDocument();
  await user.click(pickBox("Вторая часть")!);
  expect(screen.getByText("Выбрано: 2")).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: "Сохранить исходные записи" }));
  killed.length = 0;
  await user.click(screen.getByRole("button", { name: "Объединить (2)" }));
  await vi.waitFor(() => expect(api.mergeRecordings).toHaveBeenCalledWith(ep, ["d", "f"], true));
  expect(killed).toEqual([]);  // исходные остаются — агентов не трогаем
  expect(await screen.findByRole("status")).toHaveTextContent(/^Встречи объединены$/);
});

test("Ctrl+A — все записи; запись в обработке не даёт объединить; Esc снимает выбор", async () => {
  setup({ library: pickLib, selected: "d" });
  const user = userEvent.setup();
  mainOf("Первая часть").focus();
  await user.keyboard("{Control>}a{/Control}");
  expect(screen.getByText("Выбрано: 4")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Объединить (4)" })).toBeDisabled();
  expect(screen.getByText("Записи, которые ещё пишутся или обрабатываются, объединить нельзя")).toBeInTheDocument();
  mainOf("Первая часть").focus();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("toolbar")).toBeNull();
});

test("одна отмеченная — объединять нечего; обычный щелчок снимает выбор и открывает запись", async () => {
  const { onSelect } = setup({ library: pickLib, selected: null });
  const user = userEvent.setup();
  await user.keyboard("{Control>}");
  await user.click(mainOf("Вторая часть"));
  await user.keyboard("{/Control}");
  expect(screen.getByRole("button", { name: "Объединить (1)" })).toBeDisabled();
  expect(screen.getByText("Выберите ещё хотя бы одну запись")).toBeInTheDocument();
  await user.click(mainOf("Третья часть"));
  expect(onSelect).toHaveBeenCalledWith("f");
  expect(screen.queryByRole("toolbar")).toBeNull();
});

test("ошибка объединения — текстом резидента", async () => {
  vi.mocked(api.mergeRecordings).mockRejectedValue(new api.ApiError(400, "2026-09-30_10-00: запись ещё идёт"));
  setup({ library: pickLib, selected: "d" });
  const user = userEvent.setup();
  await user.keyboard("{Control>}");
  await user.click(mainOf("Вторая часть"));
  await user.keyboard("{/Control}");
  await user.click(screen.getByRole("button", { name: "Объединить (2)" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("2026-09-30_10-00: запись ещё идёт");
});

test("подсказка объединения предупреждает про имена спикеров, заданные вручную", async () => {
  setup({ library: pickLib, selected: "d" });
  const user = userEvent.setup();
  await user.keyboard("{Control>}");
  await user.click(mainOf("Вторая часть"));
  await user.keyboard("{/Control}");
  const tip = screen.getByRole("button", { name: "Как объединяются встречи" });
  await user.click(tip);
  expect(tip).toHaveAccessibleDescription(/заново определяются по базе голосов/);
});

test("бейдж «ИИ» у названия от модели: нажатие — переименовать; своё название — без бейджа", async () => {
  const withAi = [rec("a", { has_transcript: true, title: "Запуск беты", title_source: "ai" }),
    rec("b", { has_transcript: true, title: "Моё", title_source: "user" })];
  const onSelect = vi.fn();
  render(<RecordingsList selected="a" onSelect={onSelect} library={{ ...library, items: withAi, jobs: [] }}
    resident={resident} q="" onQ={vi.fn()} />);
  const badges = screen.getAllByTitle("Название предложено ИИ — нажмите, чтобы изменить");
  // для экранного диктора — текстом, а не aria-label у span
  expect(within(badges[0]!).getByText("Название предложено ИИ — нажмите, чтобы изменить")).toBeInTheDocument();
  expect(badges).toHaveLength(1);
  expect(mains()[0]!).toContainElement(badges[0]!);
  await userEvent.click(badges[0]!);
  expect(onSelect).not.toHaveBeenCalled();
  const input = screen.getByRole("textbox", { name: "Название записи" });
  expect(input).toHaveValue("Запуск беты");
  await userEvent.clear(input);
  await userEvent.type(input, "Бета{Enter}");
  expect(api.patchRecording).toHaveBeenCalledWith(ep, "a", { title: "Бета" });
});

test("первая загрузка библиотеки — заготовки строк, поиск на месте; пустой поиск предлагает сбросить", async () => {
  setup({ library: { ...library, items: [], loading: true } });
  expect(screen.getByRole("list", { name: "Загрузка записей" })).toHaveAttribute("aria-busy", "true");
  expect(screen.getByRole("searchbox", { name: "Поиск по записям" })).toBeInTheDocument();
  expect(screen.queryByText("Записей пока нет")).toBeNull();
});

test("ничего не найдено — подсказка и «Сбросить поиск»", async () => {
  const { onQ } = setup({ library: { ...library, items: [] }, q: "бюджет" });
  expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сбросить поиск" }));
  expect(onQ).toHaveBeenCalledWith("");
});

test("название в строке: подсказка — полное название, только если оно обрезано", () => {
  setup();
  const title = within(screen.getByRole("list", { name: "Среда, 30 сентября" })).getByText("Планёрка");
  Object.defineProperty(title, "clientWidth", { value: 100, configurable: true });
  Object.defineProperty(title, "scrollWidth", { value: 100, configurable: true });
  fireEvent.mouseEnter(title);
  expect(title).toHaveAttribute("title", "Двойной щелчок или F2 — переименовать");
  Object.defineProperty(title, "scrollWidth", { value: 300, configurable: true });
  fireEvent.mouseEnter(title);
  expect(title.getAttribute("title")).toBe("Планёрка\nДвойной щелчок или F2 — переименовать");
});

test("бейдж идущей расшифровки: общая доля впереди этапа — это ход всей работы", async () => {
  const { badgeOf } = await import("./RecordingItem");
  const job = { id: "j", kind: "transcribe", folder: "C:/rec/b", state: "running" as const, stage: "diarize",
    label: "диаризация", done: null, total: null, note: "sys", result: null, error: null, step: 4, steps: 6, fraction: 0.62 };
  expect(badgeOf({ kind: "running", stage: "diarize", label: "Разделение на спикеров", job })?.text)
    .toBe("62% · Разделение на спикеров");
  expect(badgeOf({ kind: "running", stage: "align", label: "Выравнивание" })?.text).toBe("Выравнивание…");
});

// --- разделы по датам -------------------------------------------------------------

const dated = [
  rec("t1", { title: "Сегодняшняя", started_at: "2026-10-06T09:00:00" }),
  rec("t2", { title: "Утренняя", started_at: "2026-10-06T08:00:00" }),
  rec("y1", { title: "Вчерашняя", started_at: "2026-10-05T15:30:00" }),
  rec("s1", { title: "Сентябрьская", started_at: "2026-09-10T10:00:00" }),
  rec("o1", { title: "Прошлогодняя", started_at: "2025-03-05T10:00:00" }),
  rec("o2", { title: "Старая", started_at: "2024-02-01T10:00:00" }),
  rec("n1", { title: "Безымянная дата", started_at: null }),
];
const datedLib = { ...library, items: dated, jobs: [] };
const head = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name} · \\d+$`) });
const heads = () => screen.getAllByRole("heading", { level: 3 });
const expanded = () => heads().map((h) => within(h).getByRole("button").getAttribute("aria-expanded"));
const stored = () => JSON.parse(window.localStorage.getItem("meet.sections.v1") ?? "null");

test("разделы: заголовки со счётчиками, годовые свёрнуты и не отрисованы, дата в строке короче", () => {
  setup({ library: datedLib, selected: null });
  expect(heads().map((h) => h.textContent)).toEqual(
    ["Сегодня · 2", "Вчера · 1", "Сентябрь · 1", "Ранее в 2025 · 1", "2024 · 1", "Без даты · 1"]);
  expect(expanded()).toEqual(["true", "true", "true", "false", "false", "true"]);
  // Раздел — область с именем заголовка, у неё свой список.
  const today = screen.getByRole("region", { name: "Сегодня · 2" });
  const list = within(today).getByRole("list", { name: "Сегодня" });
  expect(head("Сегодня")).toHaveAttribute("aria-controls", list.id);
  expect(mains().map((b) => b.querySelector(".rec-item__title")!.textContent))
    .toEqual(["Сегодняшняя", "Утренняя", "Вчерашняя", "Сентябрьская", "Безымянная дата"]);
  expect(screen.queryByText("Прошлогодняя")).toBeNull();
  expect(item("Сегодняшняя")).toHaveTextContent("09:00 · 30 мин");
  expect(item("Сегодняшняя")).not.toHaveTextContent("Сегодня 09:00");
  expect(item("Сентябрьская")).toHaveTextContent("10 сен, 10:00 · 30 мин");
});

test("свёрнутое и развёрнутое запоминается — только отклонения от умолчания", async () => {
  const user = userEvent.setup();
  const { unmount } = render(<RecordingsList selected={null} onSelect={vi.fn()} library={datedLib} resident={resident}
    q="" onQ={vi.fn()} />);
  await user.click(head("Сентябрь"));
  expect(head("Сентябрь")).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Сентябрьская")).toBeNull();
  expect(stored()).toEqual({ "m:2026-09": false });
  await user.click(head("2024"));
  expect(screen.getByText("Старая")).toBeInTheDocument();
  expect(stored()).toEqual({ "m:2026-09": false, "y:2024": true });
  await user.click(head("Сентябрь"));
  expect(stored()).toEqual({ "y:2024": true });
  await user.click(head("Сентябрь"));
  unmount();
  render(<RecordingsList selected={null} onSelect={vi.fn()} library={datedLib} resident={resident} q="" onQ={vi.fn()} />);
  expect(head("Сентябрь")).toHaveAttribute("aria-expanded", "false");
  expect(head("2024")).toHaveAttribute("aria-expanded", "true");
  expect(head("Ранее в 2025")).toHaveAttribute("aria-expanded", "false");
});

test("открытая запись в свёрнутом разделе разворачивает его (без запоминания)", () => {
  const props = { onSelect: vi.fn(), library: datedLib, resident, q: "", onQ: vi.fn() };
  const { rerender } = render(<RecordingsList selected={null} {...props} />);
  expect(screen.queryByText("Старая")).toBeNull();
  rerender(<RecordingsList selected="o2" {...props} />);
  expect(head("2024")).toHaveAttribute("aria-expanded", "true");
  expect(mainOf("Старая")).toHaveAttribute("aria-current", "true");
  expect(stored()).toBeNull();
});

test("клавиатура: Enter/Пробел, ←/→ сворачивают и разворачивают, Alt+↑/↓ — между заголовками", async () => {
  const user = userEvent.setup();
  setup({ library: datedLib, selected: null });
  head("Сегодня").focus();
  await user.keyboard("{ArrowLeft}");
  expect(head("Сегодня")).toHaveAttribute("aria-expanded", "false");
  await user.keyboard("{ArrowLeft}");
  expect(head("Сегодня")).toHaveAttribute("aria-expanded", "false");
  await user.keyboard("{ArrowRight}");
  expect(head("Сегодня")).toHaveAttribute("aria-expanded", "true");
  await user.keyboard("{Enter}");
  expect(head("Сегодня")).toHaveAttribute("aria-expanded", "false");
  await user.keyboard(" ");
  expect(head("Сегодня")).toHaveAttribute("aria-expanded", "true");
  // Стрелки дальше не уходят: плеер карточки перематывал бы по ним.
  expect(fireEvent.keyDown(head("Сегодня"), { key: "ArrowRight" })).toBe(false);
  await user.keyboard("{Alt>}{ArrowDown}{/Alt}");
  expect(head("Вчера")).toHaveFocus();
  await user.keyboard("{Alt>}{ArrowDown}{ArrowDown}{/Alt}");
  expect(head("Ранее в 2025")).toHaveFocus();
  await user.keyboard("{Alt>}{ArrowUp}{/Alt}");
  expect(head("Сентябрь")).toHaveFocus();
  // Из строки Alt+↑ — к заголовку её раздела.
  mainOf("Вчерашняя").focus();
  await user.keyboard("{Alt>}{ArrowUp}{/Alt}");
  expect(head("Вчера")).toHaveFocus();
});

test("меню заголовка: «Свернуть все», «Развернуть все»; Esc возвращает фокус на заголовок", async () => {
  const user = userEvent.setup();
  setup({ library: datedLib, selected: null });
  fireEvent.contextMenu(head("Вчера"), { clientX: 30, clientY: 40 });
  const menu = screen.getByRole("menu", { name: "Разделы" });
  expect(within(menu).getAllByRole("menuitem").map((m) => m.textContent)).toEqual(["Свернуть все", "Развернуть все"]);
  await user.click(within(menu).getByRole("menuitem", { name: "Развернуть все" }));
  expect(expanded()).not.toContain("false");
  expect(stored()).toEqual({ "rest-y:2025": true, "y:2024": true });
  expect(head("Вчера")).toHaveFocus();
  fireEvent.contextMenu(head("Вчера"), { clientX: 0, clientY: 0 });
  await user.click(screen.getByRole("menuitem", { name: "Свернуть все" }));
  expect(expanded()).not.toContain("true");
  expect(mains()).toHaveLength(0);
  expect(stored()).toEqual({ today: false, yesterday: false, "m:2026-09": false, none: false });
  fireEvent.contextMenu(head("Вчера"), { clientX: 0, clientY: 0 });
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(head("Вчера")).toHaveFocus();
});

test("выбор: Shift и Ctrl+A — по видимым строкам; флажок раздела в трёх состояниях, и у свёрнутого", async () => {
  const user = userEvent.setup();
  setup({ library: datedLib, selected: "t1" });
  expect(screen.queryByRole("checkbox", { name: /Выбрать все/ })).toBeNull();
  await user.keyboard("{Shift>}");
  await user.click(mainOf("Безымянная дата"));
  await user.keyboard("{/Shift}");
  // Свёрнутые «Ранее в 2025» и «2024» в диапазон не попали.
  expect(screen.getByText("Выбрано: 5")).toBeInTheDocument();
  const year = screen.getByRole("checkbox", { name: "Выбрать все в разделе «2024»" });
  const today = screen.getByRole("checkbox", { name: "Выбрать все в разделе «Сегодня»" });
  expect(today).toBeChecked();
  expect(year).not.toBeChecked();
  await user.click(year);
  expect(screen.getByText("Выбрано: 6")).toBeInTheDocument();
  expect(year).toBeChecked();
  expect(screen.queryByText("Старая")).toBeNull();  // раздел так и свёрнут
  await user.click(pickBox("Утренняя")!);
  expect(today).toBePartiallyChecked();
  await user.click(today);
  expect(today).toBeChecked();
  expect(screen.getByText("Выбрано: 6")).toBeInTheDocument();
  await user.click(today);
  expect(today).not.toBeChecked();
  expect(screen.getByText("Выбрано: 4")).toBeInTheDocument();
  mainOf("Вчерашняя").focus();
  await user.keyboard("{Control>}a{/Control}");
  expect(screen.getByText("Выбрано: 5")).toBeInTheDocument();
  expect(year).not.toBeChecked();
});

test("поиск: все разделы развёрнуты, «Найдено: N встреч, M мест»; свернуть — только на этот поиск", async () => {
  window.localStorage.setItem("meet.sections.v1", JSON.stringify({ "m:2026-09": false }));
  const user = userEvent.setup();
  const found = dated.map((r, i) => ({ ...r, hits: [], total: i + 1, title_match: false, date: null }));
  const props = { selected: null, onSelect: vi.fn(), resident, onQ: vi.fn() };
  const { rerender } = render(<RecordingsList {...props} library={{ ...datedLib, items: found }} q="бюджет" />);
  expect(expanded()).not.toContain("false");
  expect(screen.getByText("Старая")).toBeInTheDocument();
  expect(screen.getByText("Найдено: 7 встреч, 28 мест")).toBeInTheDocument();
  await user.click(head("Сегодня"));
  expect(head("Сегодня")).toHaveAttribute("aria-expanded", "false");
  expect(stored()).toEqual({ "m:2026-09": false });
  rerender(<RecordingsList {...props} library={datedLib} q="" />);
  expect(screen.queryByText(/^Найдено/)).toBeNull();
  expect(head("Сегодня")).toHaveAttribute("aria-expanded", "true");
  expect(head("Сентябрь")).toHaveAttribute("aria-expanded", "false");
  expect(head("2024")).toHaveAttribute("aria-expanded", "false");
  // Старый резидент без числа совпадений — только встречи.
  rerender(<RecordingsList {...props} library={{ ...datedLib, items: dated.slice(0, 2) }} q="бюджет" />);
  expect(screen.getByText("Найдено: 2 встречи")).toBeInTheDocument();
});

test("Ctrl+K — к поиску списка", async () => {
  const user = userEvent.setup();
  setup({ library: datedLib, selected: null });
  head("Сегодня").focus();
  await user.keyboard("{Control>}k{/Control}");
  expect(screen.getByRole("searchbox")).toHaveFocus();
  // В терминале агента Ctrl+K — его клавиша.
  const term = document.createElement("textarea");
  term.setAttribute("data-agent-terminal", "");
  document.body.append(term);
  term.focus();
  await user.keyboard("{Control>}k{/Control}");
  expect(term).toHaveFocus();
  term.remove();
});
