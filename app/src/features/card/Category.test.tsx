import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CardHeader } from "./CardHeader";
import type { Category, Recording } from "../../lib/types";

const ep = { base: "/api", token: null };
const categories: Category[] = [
  { id: "daily", name: "Дейлик", color: "#4c8bf5", description: "Короткая встреча команды" },
  { id: "retro", name: "Ретроспектива", color: "#a0703c", description: "" },
];
const rec = (category: Recording["category"] = null): Recording => ({
  id: "r1", path: "C:/rec/r1", started_at: "2026-09-30T10:00:00", duration_s: 1800, tracks: { sys: "s.wav" },
  has_transcript: true, has_voices: false, title: "Планёрка", source: "record", category,
});

function setup(category: Recording["category"] = null) {
  const onCategory = vi.fn();
  const onOpenCategories = vi.fn();
  render(
    <CardHeader rec={rec(category)} speakers={[]} people={[]} endpoint={ep} onRename={vi.fn()}
      categories={categories} onCategory={onCategory} onOpenCategories={onOpenCategories} />,
  );
  return { onCategory, onOpenCategories };
}

test("метка категории под названием; от ИИ — подсказка об этом", () => {
  setup({ id: "daily", source: "ai" });
  const chip = screen.getByRole("button", { name: "Категория: Дейлик. Изменить" });
  expect(chip).toHaveAttribute("title", "Категорию определил ИИ — нажмите, чтобы выбрать другую");
});

test("неизвестная категория показывается как «Без категории»", () => {
  setup({ id: "gone", source: "user" });
  expect(screen.getByRole("button", { name: "Категория: Без категории. Изменить" })).toBeInTheDocument();
  expect(document.querySelector(".cat-chip--none")).not.toBeNull();
});

test("выбор с клавиатуры: фокус на текущей, стрелки, Enter; фокус возвращается на метку", async () => {
  const { onCategory } = setup({ id: "daily", source: "ai" });
  const chip = screen.getByRole("button", { name: /^Категория:/ });
  chip.focus();
  await userEvent.keyboard("{Enter}");
  expect(chip).toHaveAttribute("aria-expanded", "true");
  const menu = screen.getByRole("menu", { name: "Категория встречи" });
  expect(within(menu).getByRole("menuitemradio", { name: "Дейлик" })).toHaveFocus();
  expect(within(menu).getByRole("menuitemradio", { name: "Дейлик" })).toHaveAttribute("aria-checked", "true");
  await userEvent.keyboard("{ArrowDown}");
  expect(within(menu).getByRole("menuitemradio", { name: "Ретроспектива" })).toHaveFocus();
  await userEvent.keyboard("{End}");
  expect(within(menu).getByRole("menuitem", { name: "Настроить категории…" })).toHaveFocus();
  await userEvent.keyboard("{Home}{Enter}");
  expect(onCategory).toHaveBeenCalledWith(null);
  expect(screen.queryByRole("menu")).toBeNull();
  expect(chip).toHaveFocus();
});

test("та же категория от ИИ — выбор её закрепляет как выбор человека", async () => {
  const ai = setup({ id: "daily", source: "ai" });
  await userEvent.click(screen.getByRole("button", { name: /^Категория:/ }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Дейлик" }));
  expect(ai.onCategory).toHaveBeenCalledWith("daily");
});

test("уже выбранная вручную — повторный выбор ничего не шлёт; Esc закрывает", async () => {
  const { onCategory } = setup({ id: "retro", source: "user" });
  await userEvent.click(screen.getByRole("button", { name: /^Категория:/ }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Ретроспектива" }));
  expect(onCategory).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /^Категория:/ }));
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("«Настроить категории…» ведёт в настройки", async () => {
  const { onOpenCategories } = setup();
  await userEvent.click(screen.getByRole("button", { name: /^Категория:/ }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Настроить категории…" }));
  expect(onOpenCategories).toHaveBeenCalled();
});

test("без обработчика выбора метки нет", () => {
  render(<CardHeader rec={rec({ id: "daily", source: "user" })} speakers={[]} people={[]} endpoint={ep} onRename={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /^Категория:/ })).toBeNull();
});

test("повторный щелчок по метке закрывает меню", async () => {
  setup({ id: "daily", source: "user" });
  const chip = screen.getByRole("button", { name: /^Категория:/ });
  await userEvent.click(chip);
  expect(screen.getByRole("menu")).toBeInTheDocument();
  await userEvent.click(chip);
  expect(screen.queryByRole("menu")).toBeNull();
  expect(chip).toHaveAttribute("aria-expanded", "false");
});

test("категории ещё читаются — заготовка на месте метки, а не пустота", () => {
  const { container } = render(<CardHeader rec={rec({ id: "daily", source: "user" })} speakers={[]} people={[]}
    endpoint={ep} onRename={vi.fn()} onCategory={vi.fn()} />);
  expect(container.querySelector(".card__cat-skel")).not.toBeNull();
  expect(screen.queryByRole("button", { name: /^Категория:/ })).toBeNull();
});
