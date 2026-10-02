import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { RecordingsList } from "./RecordingsList";
import * as api from "../../lib/api";
import { loadCategoryFilter, saveCategoryFilter } from "../../lib/categories";
import type { Category, Recording } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  patchRecording: vi.fn(async () => ({})),
  setRecordingCategory: vi.fn(async () => ({})),
  getCategoriesInfo: vi.fn(),
}));

const ep = { base: "/api", token: null };
const categories: Category[] = [
  { id: "daily", name: "Дейлик", color: "#4c8bf5", description: "Короткая встреча команды" },
  { id: "client", name: "Встреча с клиентом", color: "#e08a2e", description: "" },
  { id: "retro", name: "Ретроспектива", color: "#a0703c", description: "" },
];
const rec = (id: string, title: string, category: Recording["category"] = null): Recording => ({
  id, path: `C:/rec/${id}`, started_at: "2026-09-30T10:00:00", duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: true, has_voices: false, title, source: "record", category,
});
const items = [
  rec("a", "Утренний дейлик", { id: "daily", source: "ai" }),
  rec("b", "Демо для заказчика", { id: "client", source: "user" }),
  rec("d", "Без разметки"),
  rec("e", "Удалённая категория", { id: "gone", source: "user" }),
  rec("f", "Выбрано «без категории»", { id: null, source: "user" }),
];
const refresh = vi.fn(async () => {});
const resident = { status: "online" as const, endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 };
const library = (list: Recording[] = items) => ({ items: list, jobs: [], loading: false, error: null, refresh });

/** Как в App: фильтр живёт снаружи списка; список записей — уже отфильтрованный резидентом. */
function Harness({ initial = [], list = items, q = "", onFilter }: {
  initial?: string[]; list?: Recording[]; q?: string; onFilter?: (keys: string[]) => void;
}) {
  const [filter, setFilter] = useState<string[]>(initial);
  return (
    <RecordingsList selected={null} onSelect={vi.fn()} library={library(list)} resident={resident} q={q}
      onQ={vi.fn()} categories={categories} categoryFilter={filter}
      onCategoryFilter={(keys) => { setFilter(keys); onFilter?.(keys); }} onOpenSettings={onOpenSettings} />
  );
}
const onOpenSettings = vi.fn();

const item = (title: string) => screen.getByText(title).closest("li")!;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getCategoriesInfo).mockResolvedValue({
    categories, defaults: categories, counts: { daily: 31, client: 4 }, none: 290, scope: "library",
  });
});

test("в списке у записи — точка цвета, имя категории — в подсказке и для диктора", () => {
  render(<Harness />);
  const mark = item("Утренний дейлик").querySelector(".cat-mark")!;
  expect(mark).toHaveAttribute("title", "Дейлик — Короткая встреча команды");
  expect(within(item("Утренний дейлик")).getByText("Категория: Дейлик")).toBeInTheDocument();
  expect(item("Демо для заказчика").querySelector(".cat-mark")).toHaveAttribute("title", "Встреча с клиентом");
  for (const title of ["Без разметки", "Удалённая категория", "Выбрано «без категории»"]) {
    expect(item(title).querySelector(".cat-mark")).toBeNull();
  }
});

test("фильтр: счётчики по всей библиотеке от резидента, несколько категорий, «Без категории»", async () => {
  const onFilter = vi.fn();
  render(<Harness onFilter={onFilter} />);
  await userEvent.click(screen.getByRole("button", { name: "Категории" }));
  const dialog = screen.getByRole("dialog", { name: "Фильтр по категориям" });
  expect(api.getCategoriesInfo).toHaveBeenCalledWith(ep, undefined);
  const box = (name: string) => within(dialog).getByRole("checkbox", { name });
  await waitFor(() => expect(box("Дейлик").closest("label")).toHaveTextContent("Дейлик31"));
  expect(box("Без категории").closest("label")).toHaveTextContent("Без категории290");
  expect(box("Ретроспектива").closest("label")).toHaveTextContent("Ретроспектива0");
  expect(within(dialog).getByText("Число встреч — по всей библиотеке")).toBeInTheDocument();
  expect(box("Без категории")).toHaveFocus();
  await userEvent.click(box("Дейлик"));
  await userEvent.click(box("Без категории"));
  expect(onFilter).toHaveBeenLastCalledWith(["daily", "_none"]);
  await userEvent.click(box("Дейлик"));
  expect(onFilter).toHaveBeenLastCalledWith(["_none"]);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Категории · 1" })).toHaveFocus();
});

test("при поиске счётчики — среди найденных", async () => {
  vi.mocked(api.getCategoriesInfo).mockResolvedValue({
    categories, defaults: categories, counts: { daily: 2 }, none: 1, scope: "search",
  });
  render(<Harness q="бюджет" />);
  await userEvent.click(screen.getByRole("button", { name: "Категории" }));
  expect(api.getCategoriesInfo).toHaveBeenCalledWith(ep, "бюджет");
  expect(await screen.findByText("Число встреч — среди найденных поиском")).toBeInTheDocument();
});

test("повторный щелчок по кнопке «Категории» закрывает окно", async () => {
  render(<Harness />);
  const button = screen.getByRole("button", { name: "Категории" });
  await userEvent.click(button);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  await userEvent.click(button);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(button).toHaveAttribute("aria-expanded", "false");
});

test("метки фильтра: у каждой своя «✕», при двух и больше — «Сбросить»", async () => {
  const onFilter = vi.fn();
  render(<Harness initial={["client", "_none"]} onFilter={onFilter} />);
  const group = screen.getByRole("group", { name: "Фильтр по категориям" });
  expect(within(group).getByText("Встреча с клиентом")).toBeInTheDocument();
  expect(within(group).getByText("Без категории")).toBeInTheDocument();
  await userEvent.click(within(group).getByRole("button", { name: "Убрать «Встреча с клиентом» из фильтра" }));
  expect(onFilter).toHaveBeenLastCalledWith(["_none"]);
  expect(within(screen.getByRole("group", { name: "Фильтр по категориям" }))
    .queryByRole("button", { name: "Сбросить" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Убрать «Без категории» из фильтра" }));
  expect(screen.queryByRole("group", { name: "Фильтр по категориям" })).toBeNull();
});

test("«Сбросить» снимает весь фильтр; пустой результат — «Показать все категории»", async () => {
  const onFilter = vi.fn();
  render(<Harness initial={["retro", "daily"]} list={[]} onFilter={onFilter} />);
  expect(screen.getByText("Нет записей в выбранных категориях")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сбросить" }));
  expect(onFilter).toHaveBeenLastCalledWith([]);
});

test("фильтр запоминается; недоступное хранилище — не ошибка", () => {
  saveCategoryFilter(["retro", "_none"]);
  expect(loadCategoryFilter()).toEqual(["retro", "_none"]);
  saveCategoryFilter([]);
  expect(window.localStorage.getItem("meet.categoryFilter")).toBeNull();
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("запрещено"); });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("запрещено"); });
  try {
    expect(loadCategoryFilter()).toEqual([]);
    expect(() => saveCategoryFilter(["daily"])).not.toThrow();
  } finally {
    get.mockRestore();
    set.mockRestore();
  }
});

test("меню «⋯» → «Категория»: список с клавиатуры, выбор уходит резиденту", async () => {
  render(<Harness />);
  await userEvent.click(within(item("Без разметки")).getByRole("button", { name: /^Действия с записью/ }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Категория" }));
  const menu = screen.getByRole("menu", { name: "Категория записи «Без разметки»" });
  const radios = within(menu).getAllByRole("menuitemradio");
  expect(radios.map((r) => r.textContent)).toEqual(["Без категории✓", "Дейлик", "Встреча с клиентом", "Ретроспектива"]);
  expect(radios[0]).toHaveAttribute("aria-checked", "true");
  expect(radios[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
  expect(api.setRecordingCategory).toHaveBeenCalledWith(ep, "d", "client");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("меню категории: текущая отмечена, «Назад» возвращает на «Категория», «Настроить категории…»", async () => {
  render(<Harness />);
  const more = within(item("Утренний дейлик")).getByRole("button", { name: /^Действия с записью/ });
  await userEvent.click(more);
  await userEvent.click(screen.getByRole("menuitem", { name: "Категория" }));
  expect(screen.getByRole("menuitemradio", { name: "Дейлик" })).toHaveFocus();
  await userEvent.click(screen.getByRole("menuitem", { name: "Назад" }));
  expect(screen.getByRole("menuitem", { name: "Категория" })).toHaveFocus();
  await userEvent.click(screen.getByRole("menuitem", { name: "Категория" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Без категории" }));
  expect(api.setRecordingCategory).toHaveBeenCalledWith(ep, "a", null);
  await userEvent.click(more);
  expect(screen.getByRole("menuitem", { name: "Переименовать" })).toHaveFocus();
  await userEvent.click(screen.getByRole("menuitem", { name: "Категория" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Настроить категории…" }));
  expect(onOpenSettings).toHaveBeenCalledWith("categories");
});

test("без категорий в настройках фильтра нет", () => {
  render(<RecordingsList selected={null} onSelect={vi.fn()} library={library()} resident={resident} q=""
    onQ={vi.fn()} categories={[]} categoryFilter={[]} onCategoryFilter={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Категории" })).toBeNull();
});
