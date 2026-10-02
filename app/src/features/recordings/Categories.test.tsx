import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordingsList } from "./RecordingsList";
import * as api from "../../lib/api";
import type { Category, Recording } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  patchRecording: vi.fn(async () => ({})),
  setRecordingCategory: vi.fn(async () => ({})),
}));

const ep = { base: "/api", token: null };
const categories: Category[] = [
  { id: "daily", name: "Дейлик", color: "#4c8bf5", description: "Короткая встреча команды" },
  { id: "client", name: "Встреча с клиентом", color: "#e08a2e", description: "" },
  { id: "retro", name: "Ретроспектива", color: "#7a8b99", description: "" },
];
const rec = (id: string, title: string, category: Recording["category"] = null): Recording => ({
  id, path: `C:/rec/${id}`, started_at: "2026-09-30T10:00:00", duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: true, has_voices: false, title, source: "record", category,
});
const items = [
  rec("a", "Утренний дейлик", { id: "daily", source: "ai" }),
  rec("b", "Демо для заказчика", { id: "client", source: "user" }),
  rec("c", "Второй дейлик", { id: "daily", source: "user" }),
  rec("d", "Без разметки"),
  rec("e", "Удалённая категория", { id: "gone", source: "user" }),
  rec("f", "Выбрано «без категории»", { id: null, source: "user" }),
];
const refresh = vi.fn(async () => {});
const resident = { status: "online" as const, endpoint: ep, snapshot: null, lastEvent: null, libraryTick: 0 };

function setup(props: Partial<Parameters<typeof RecordingsList>[0]> = {}) {
  const onOpenSettings = vi.fn();
  const view = render(
    <RecordingsList selected={null} onSelect={vi.fn()} library={{ items, jobs: [], loading: false, error: null, refresh }}
      resident={resident} q="" onQ={vi.fn()} categories={categories} onOpenSettings={onOpenSettings} {...props} />,
  );
  return { onOpenSettings, ...view };
}

const titles = () => within(screen.getByRole("list", { name: "Записи" })).getAllByRole("listitem")
  .map((li) => li.querySelector(".rec-item__title")!.textContent);
const item = (title: string) => screen.getByText(title).closest("li")!;

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

test("метка категории у записи; неизвестная или «без категории» — без метки", () => {
  setup();
  expect(within(item("Утренний дейлик")).getByText("Дейлик")).toBeInTheDocument();
  expect(within(item("Демо для заказчика")).getByText("Встреча с клиентом")).toBeInTheDocument();
  for (const title of ["Без разметки", "Удалённая категория", "Выбрано «без категории»"]) {
    expect(item(title).querySelector(".cat-chip")).toBeNull();
  }
});

test("фильтр: несколько категорий, счётчики, «Без категории»", async () => {
  setup();
  await userEvent.click(screen.getByRole("button", { name: "Категории" }));
  const dialog = screen.getByRole("dialog", { name: "Фильтр по категориям" });
  const box = (name: string) => within(dialog).getByRole("checkbox", { name: new RegExp(`^${name}`) });
  // Удалённая категория и выбранное «без категории» считаются вместе с неразмеченной.
  expect(box("Без категории").closest("label")).toHaveTextContent("Без категории3");
  expect(box("Дейлик").closest("label")).toHaveTextContent("Дейлик2");
  expect(box("Ретроспектива").closest("label")).toHaveTextContent("Ретроспектива0");
  expect(box("Без категории")).toHaveFocus();
  await userEvent.click(box("Дейлик"));
  expect(titles()).toEqual(["Утренний дейлик", "Второй дейлик"]);
  await userEvent.click(box("Встреча с клиентом"));
  expect(titles()).toEqual(["Утренний дейлик", "Демо для заказчика", "Второй дейлик"]);
  await userEvent.click(box("Дейлик"));
  await userEvent.click(box("Без категории"));
  expect(titles()).toEqual(["Демо для заказчика", "Без разметки", "Удалённая категория", "Выбрано «без категории»"]);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Категории · 2" })).toHaveFocus();
});

test("фильтр вместе с поиском: только найденное, счётчики — по найденному", async () => {
  const found = [items[0]!, items[3]!];
  setup({ q: "дейлик", library: { items: found, jobs: [], loading: false, error: null, refresh } });
  await userEvent.click(screen.getByRole("button", { name: "Категории" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByRole("checkbox", { name: /^Дейлик/ }).closest("label")).toHaveTextContent("Дейлик1");
  await userEvent.click(within(dialog).getByRole("checkbox", { name: /^Дейлик/ }));
  expect(titles()).toEqual(["Утренний дейлик"]);
});

test("фильтр запоминается, метка «✕» снимает его; ничего не найдено — «Показать все»", async () => {
  const first = setup();
  await userEvent.click(screen.getByRole("button", { name: "Категории" }));
  await userEvent.click(screen.getByRole("checkbox", { name: /^Ретроспектива/ }));
  expect(screen.queryByRole("list", { name: "Записи" })?.querySelectorAll("li")).toHaveLength(0);
  expect(screen.getByText("Нет записей в выбранных категориях")).toBeInTheDocument();
  first.unmount();

  setup();
  expect(screen.getByText("Категории: Ретроспектива")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Показать все" }));
  expect(titles()).toHaveLength(6);
  expect(window.localStorage.getItem("meet.categoryFilter")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Категории" }));
  await userEvent.click(screen.getByRole("checkbox", { name: /^Дейлик/ }));
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("button", { name: "Снять фильтр по категориям" }));
  expect(titles()).toHaveLength(6);
  expect(screen.queryByText(/^Категории:/)).toBeNull();
});

test("хранилище недоступно — фильтр работает, просто не запоминается", async () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("запрещено"); });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("запрещено"); });
  try {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Категории" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /^Дейлик/ }));
    expect(titles()).toEqual(["Утренний дейлик", "Второй дейлик"]);
  } finally {
    get.mockRestore();
    set.mockRestore();
  }
});

test("меню «⋯» → «Категория»: список с клавиатуры, выбор уходит резиденту", async () => {
  setup();
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

test("меню категории: текущая отмечена, «Назад» и «Настроить категории…»", async () => {
  const { onOpenSettings } = setup();
  const more = within(item("Утренний дейлик")).getByRole("button", { name: /^Действия с записью/ });
  await userEvent.click(more);
  await userEvent.click(screen.getByRole("menuitem", { name: "Категория" }));
  expect(screen.getByRole("menuitemradio", { name: "Дейлик" })).toHaveFocus();
  await userEvent.click(screen.getByRole("menuitem", { name: "Назад" }));
  expect(screen.getByRole("menuitem", { name: "Переименовать" })).toHaveFocus();
  await userEvent.click(screen.getByRole("menuitem", { name: "Категория" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Без категории" }));
  expect(api.setRecordingCategory).toHaveBeenCalledWith(ep, "a", null);
  await userEvent.click(more);
  await userEvent.click(screen.getByRole("menuitem", { name: "Категория" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Настроить категории…" }));
  expect(onOpenSettings).toHaveBeenCalledWith("categories");
});

test("без категорий в настройках фильтра нет", () => {
  setup({ categories: [] });
  expect(screen.queryByRole("button", { name: "Категории" })).toBeNull();
});
