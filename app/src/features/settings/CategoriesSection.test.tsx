import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import { categoriesToSave } from "./CategoriesSection";
import * as api from "../../lib/api";
import type { Category } from "../../lib/types";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
  getCategoriesInfo: vi.fn(),
}));

const ep = { base: "/api", token: null };
const defaults: Category[] = [
  { id: "daily", name: "Дейлик", color: "#4c8bf5", description: "Короткая встреча команды" },
  { id: "planning", name: "Планирование", color: "#2fa36b", description: "Планирование работ" },
  { id: "retro", name: "Ретроспектива", color: "#a0703c", description: "Что изменить" },
];
const saved: Category[] = [defaults[0]!, defaults[2]!];
const settings = { analysis: { category: true }, categories: saved };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...structuredClone(settings), ...(u as object) } as Record<string, unknown>, restart_required: [],
  }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
  vi.mocked(api.getCategoriesInfo).mockResolvedValue({
    categories: saved, defaults, counts: { daily: 4, retro: 1 }, none: 2,
  });
});

const open = async () => {
  render(<SettingsPane endpoint={ep} recordingsDir={null} initial="categories" />);
  return screen.findByRole("list", { name: "Категории встреч" });
};
const names = () => screen.getAllByRole("textbox", { name: "Название категории" }).map((i) => (i as HTMLInputElement).value);
const save = () => userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
const sent = () => (vi.mocked(api.patchSettings).mock.calls.at(-1)![1] as { categories: Category[] }).categories;

test("раздел в меню; список с цветом, названием и описанием", async () => {
  const list = await open();
  expect(screen.getByRole("button", { name: "Категории встреч" })).toHaveAttribute("aria-current", "page");
  expect(names()).toEqual(["Дейлик", "Ретроспектива"]);
  expect(within(list).getByRole("textbox", { name: "Описание категории «Дейлик» для ИИ" }))
    .toHaveValue("Короткая встреча команды");
  expect(within(list).getByRole("button", { name: "Цвет категории «Дейлик»: Синий" })).toBeInTheDocument();
  expect(screen.getByText("Описание помогает ИИ отличать категории")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

test("переименование сохраняет id; цвет из палитры", async () => {
  await open();
  const name = screen.getAllByRole("textbox", { name: "Название категории" })[1]!;
  await userEvent.clear(name);
  await userEvent.type(name, "Разбор спринта");
  await userEvent.click(screen.getByRole("button", { name: /^Цвет категории «Разбор спринта»/ }));
  const palette = screen.getByRole("radiogroup", { name: "Цвет категории" });
  await userEvent.click(within(palette).getByRole("radio", { name: "Красный" }));
  expect(screen.getByRole("button", { name: "Цвет категории «Разбор спринта»: Красный" })).toHaveFocus();
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  expect(sent()).toEqual([saved[0], { id: "retro", name: "Разбор спринта", color: "#e5484d", description: "Что изменить" }]);
});

test("порядок кнопками ↑/↓ с клавиатуры", async () => {
  await open();
  const down = screen.getByRole("button", { name: "Опустить «Дейлик»" });
  expect(screen.getByRole("button", { name: "Поднять «Дейлик»" })).toBeDisabled();
  down.focus();
  await userEvent.keyboard("{Enter}");
  expect(names()).toEqual(["Ретроспектива", "Дейлик"]);
  // Внизу «Опустить» недоступна — фокус на «Поднять» той же строки.
  await waitFor(() => expect(screen.getByRole("button", { name: "Поднять «Дейлик»" })).toHaveFocus());
  await save();
  await waitFor(() => expect(sent().map((c) => c.id)).toEqual(["retro", "daily"]));
});

test("добавить: фокус в название, id появляется при сохранении; пустое название не сохраняется", async () => {
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Добавить категорию" }));
  const fresh = screen.getAllByRole("textbox", { name: "Название категории" })[2]!;
  expect(fresh).toHaveFocus();
  // Ошибка — не сразу после «Добавить», а когда из пустого поля ушли.
  expect(screen.queryByText("У каждой категории должно быть название")).toBeNull();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.tab();
  expect(screen.getByText("У каждой категории должно быть название")).toBeInTheDocument();
  await userEvent.type(fresh, "Без категории");
  expect(screen.getByText(/Название «Без категории» занято/)).toBeInTheDocument();
  await userEvent.clear(fresh);
  await userEvent.type(fresh, "Дейлик");
  expect(screen.getByText("Названия категорий не должны повторяться")).toBeInTheDocument();
  await userEvent.clear(fresh);
  await userEvent.type(fresh, "Встреча с инвестором");
  await save();
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalled());
  const added = sent()[2]!;
  expect(added.id).toMatch(/^vstrecha-s-investoro-[0-9a-z]{4}$/);
  expect(added).toMatchObject({ name: "Встреча с инвестором", color: "#3aa7b8", description: "" });
});

test("удаление — с подтверждением и числом встреч", async () => {
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Удалить «Дейлик»" }));
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("У 4 встреч эта категория будет снята. Удалить категорию «Дейлик»?");
  expect(within(alert).getByRole("button", { name: "Отмена" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(names()).toEqual(["Дейлик", "Ретроспектива"]);
  await userEvent.click(screen.getByRole("button", { name: "Удалить «Ретроспектива»" }));
  expect(screen.getByRole("alert")).toHaveTextContent("У 1 встречи эта категория будет снята.");
  await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Удалить" }));
  expect(names()).toEqual(["Дейлик"]);
  await save();
  await waitFor(() => expect(sent()).toEqual([saved[0]]));
});

test("«Сбросить к стандартным» возвращает стандартный список", async () => {
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Удалить «Дейлик»" }));
  await userEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Удалить" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Сбросить к стандартным" })).toBeEnabled());
  await userEvent.click(screen.getByRole("button", { name: "Сбросить к стандартным" }));
  expect(names()).toEqual(["Дейлик", "Планирование", "Ретроспектива"]);
  await save();
  await waitFor(() => expect(sent()).toEqual(defaults));
});

test("новые id не повторяются и годятся резиденту", () => {
  const random = vi.fn().mockReturnValueOnce(0).mockReturnValueOnce(0).mockReturnValueOnce(0.5);
  const got = categoriesToSave([
    { id: "", name: "  Q&A   сессия ", color: "#4c8bf5", description: " о продукте " },
    { id: "", name: "Q&A сессия 2", color: "#4c8bf5", description: "" },
  ], random);
  expect(got[0]).toEqual({ id: "q-a-sessiya-0000", name: "Q&A сессия", color: "#4c8bf5", description: "о продукте" });
  expect(got[1]!.id).toMatch(/^q-a-sessiya-2-[0-9a-z]{4}$/);
  for (const c of got) expect(c.id).toMatch(/^[a-z0-9][a-z0-9_-]{0,31}$/);
});

test("палитра: одна остановка Tab, стрелки ходят по цветам; повторный щелчок по кружку закрывает", async () => {
  await open();
  const swatch = screen.getByRole("button", { name: "Цвет категории «Дейлик»: Синий" });
  await userEvent.click(swatch);
  const palette = screen.getByRole("radiogroup", { name: "Цвет категории" });
  const radios = within(palette).getAllByRole("radio");
  expect(radios.filter((r) => r.tabIndex === 0)).toHaveLength(1);
  expect(within(palette).getByRole("radio", { name: "Синий" })).toHaveFocus();
  await userEvent.keyboard("{ArrowRight}");
  expect(within(palette).getByRole("radio", { name: "Бирюзовый" })).toHaveFocus();
  await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
  expect(within(palette).getByRole("radio", { name: "Серый" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("button", { name: "Цвет категории «Дейлик»: Серый" })).toHaveFocus();
  const again = screen.getByRole("button", { name: "Цвет категории «Дейлик»: Серый" });
  await userEvent.click(again);
  expect(screen.getByRole("radiogroup")).toBeInTheDocument();
  await userEvent.click(again);
  expect(screen.queryByRole("radiogroup")).toBeNull();
});

test("счётчики не пришли — подтверждение удаления без числа", async () => {
  vi.mocked(api.getCategoriesInfo).mockRejectedValue(new Error("нет связи"));
  await open();
  await userEvent.click(screen.getByRole("button", { name: "Удалить «Дейлик»" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Встречи с этой категорией будут показаны «Без категории».");
});
