import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import * as api from "../../lib/api";
import { DEFAULT_PREFS, markupPrefs } from "../../lib/markupPrefs";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  assistant: { knowledge_dir: null, auto_title: false },
  analysis: {
    auto: true, types: true, importance: true, chapters: true, insights: true, category: true, title: true, issues: true,
  },
  transcript_view: {
    types: true, importance: true, chapters: true, insights: true, curve: "hover", bar_labels: true, jira: true,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...structuredClone(settings), ...(u as object) } as Record<string, unknown>, restart_required: [],
  }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
});

const open = (initial = "analysis") => render(<SettingsPane endpoint={ep} recordingsDir={null} initial={initial} />);
const toggle = (name: string) => screen.findByRole("switch", { name });
const save = () => userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

const ITEMS = ["Типы реплик", "Важность", "Главы", "Наблюдения", "Ссылки на задачи"];

test("раздел «Анализ встречи» есть в меню и открывается по initial", async () => {
  open();
  expect(screen.getByRole("button", { name: "Анализ встречи" })).toHaveAttribute("aria-current", "page");
  expect(await toggle("Анализировать встречу после расшифровки")).toHaveAttribute("aria-checked", "true");
  for (const name of ["Определять категорию автоматически", "Название встречи"]) {
    expect(await toggle(name)).toHaveAttribute("aria-checked", "true");
  }
  expect(await toggle("Придумывать название встречи")).toHaveAttribute("aria-checked", "false");
  expect(screen.getByRole("button", { name: "Что такое анализ встречи" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Как придумывается название встречи" })).toBeInTheDocument();
});

test("таблица «Размечать / Показывать»: пять элементов, по два переключателя", async () => {
  open();
  const table = await screen.findByRole("table", { name: "Что размечать и что показывать" });
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["Элемент", "Размечать", "Показывать в карточке"]);
  expect(within(table).getAllByRole("rowheader").map((h) => h.querySelector(".srow__label")?.textContent)).toEqual(ITEMS);
  for (const item of ITEMS) {
    expect(within(table).getByRole("switch", { name: `${item}: размечать` })).toHaveAttribute("aria-checked", "true");
    expect(within(table).getByRole("switch", { name: `${item}: показывать` })).toBeEnabled();
  }
  // Прежних отдельных списков больше нет.
  expect(screen.queryByRole("switch", { name: "Значки типов реплик" })).toBeNull();
  expect(screen.queryByRole("switch", { name: "Важность реплик" })).toBeNull();
});

test("таблица пишет оба ключа: analysis.* и transcript_view.* (Ссылки на задачи — transcript_view.jira)", async () => {
  open();
  await userEvent.click(await toggle("Главы: размечать"));
  await userEvent.click(await toggle("Наблюдения: показывать"));
  await userEvent.click(await toggle("Ссылки на задачи: размечать"));
  await userEvent.click(await toggle("Типы реплик: показывать"));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    analysis: { chapters: false, issues: false },
    transcript_view: { insights: false, types: false },
  }));
});

test("«Показывать» недоступно, пока «Размечать» выключено: подсказка «Сначала включите разметку»", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({
    ...structuredClone(settings), analysis: { ...settings.analysis, importance: false },
  });
  open();
  const show = await toggle("Важность: показывать");
  expect(show).toBeDisabled();
  expect(show).toHaveAccessibleDescription("Сначала включите разметку");
  expect(screen.getByRole("switch", { name: "Главы: показывать" })).toBeEnabled();
  await userEvent.click(screen.getByRole("switch", { name: "Важность: размечать" }));
  expect(screen.getByRole("switch", { name: "Важность: показывать" })).toBeEnabled();
  expect(screen.queryByText("Сначала включите разметку")).toBeNull();
});

test("«Ссылки на задачи: показывать» доступно и без разметки: ключи в тексте узнаются без анализа", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({
    ...structuredClone(settings), analysis: { ...settings.analysis, issues: false },
  });
  open();
  const show = await toggle("Ссылки на задачи: показывать");
  expect(show).toBeEnabled();
  expect(show).not.toHaveAccessibleDescription("Сначала включите разметку");
  expect(screen.queryByText("Сначала включите разметку")).toBeNull();
  await userEvent.click(show);
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { transcript_view: { jira: false } }));
});

test("«Ссылки на задачи: показывать» — тот же ключ, что «Ссылки на задачи Jira» в разделе «Jira»", async () => {
  open();
  await userEvent.click(await toggle("Ссылки на задачи: показывать"));
  // Правка видна в обоих разделах.
  expect(screen.getByRole("button", { name: "Jira" }).querySelector("[data-dirty]")).not.toBeNull();
  expect(screen.getByRole("button", { name: "Анализ встречи" }).querySelector("[data-dirty]")).not.toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "«Jira»" }));
  expect(screen.getByRole("heading", { level: 2, name: "Jira" })).toBeInTheDocument();
  expect(await toggle("Ссылки на задачи Jira")).toHaveAttribute("aria-checked", "false");
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { transcript_view: { jira: false } }));
});

test("прежний раздел «Подсветка расшифровки» (markup) — «Анализ встречи»: плеер и подсказки", async () => {
  open("markup");
  expect(screen.getByRole("button", { name: "Анализ встречи" })).toHaveAttribute("aria-current", "page");
  expect(await toggle("Подписи глав на полосе плеера")).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("radio", { name: "При наведении" })).toBeChecked();
  for (const tip of ["Что такое подсветка и разметка", "Что такое кривая важности", "Клавиши плеера"]) {
    expect(screen.getByRole("button", { name: tip })).toBeInTheDocument();
  }
});

test("плеер: кривая и подписи глав уходят в transcript_view", async () => {
  open();
  await userEvent.click(await toggle("Подписи глав на полосе плеера"));
  await userEvent.click(screen.getByRole("radio", { name: "Всегда" }));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    transcript_view: { bar_labels: false, curve: "always" },
  }));
});

test("переключатели уходят в analysis и assistant.auto_title", async () => {
  open();
  await userEvent.click(await toggle("Анализировать встречу после расшифровки"));
  await userEvent.click(await toggle("Определять категорию автоматически"));
  await userEvent.click(await toggle("Придумывать название встречи"));
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    analysis: { auto: false, category: false },
    assistant: { auto_title: true },
  }));
});

test("старый резидент без секций analysis и transcript_view: всё включено по умолчанию", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({ assistant: { knowledge_dir: null } });
  open();
  expect(await toggle("Главы: размечать")).toHaveAttribute("aria-checked", "true");
  expect(await toggle("Главы: показывать")).toHaveAttribute("aria-checked", "true");
  expect(await toggle("Придумывать название встречи")).toHaveAttribute("aria-checked", "false");
});

test("«Улучшать расшифровку автоматически» выключено по умолчанию и уходит в analysis.improve_auto", async () => {
  open();
  const sw = await toggle("Улучшать расшифровку автоматически после распознавания");
  expect(sw).toHaveAttribute("aria-checked", "false");
  expect(screen.getByRole("button", { name: "Что такое улучшение расшифровки" })).toBeInTheDocument();
  await userEvent.click(sw);
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { analysis: { improve_auto: true } }));
});

test("что показывать: «Подсветка и разметка» вместе с «Анализом встречи»; нет разделов — всё включено", () => {
  expect(markupPrefs({})).toEqual(DEFAULT_PREFS);
  const p = markupPrefs({
    transcript_view: { types: false, importance: true, chapters: false, insights: true, curve: "off", bar_labels: false },
    analysis: { importance: false, insights: false },
  });
  expect(p.parts).toEqual({ types: true, importance: false, chapters: true, insights: false });
  expect([p.typeIcons, p.keyBorder, p.chapterHeads, p.insights, p.curve, p.barLabels])
    .toEqual([false, true, false, true, "off", false]);
  expect(markupPrefs({ transcript_view: { curve: "сбоку" } }).curve).toBe("hover");
});
