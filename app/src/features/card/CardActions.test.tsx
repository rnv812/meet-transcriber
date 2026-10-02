import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CardActions, COMPACT_PX } from "./CardActions";

vi.mock("../../lib/shell", () => ({ inTauri: () => true }));

type Props = Parameters<typeof CardActions>[0];

function setup(more: Partial<Props> = {}) {
  const props: Props = {
    canExport: true, canRetranscribe: true, busy: false,
    onExport: vi.fn(), onKbExport: vi.fn(), onOpenFolder: vi.fn(), onRetranscribe: vi.fn(),
    onRediarize: vi.fn(), onDelete: vi.fn(), ...more,
  };
  render(<CardActions {...props} />);
  return props;
}

/** Ширина полосы действий, как её померит ResizeObserver / getBoundingClientRect. */
function withWidth(width: number) {
  return vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { width, height: 32, left: 0, top: 0, right: width, bottom: 32, x: 0, y: 0, toJSON() {} } as DOMRect);
}

afterEach(() => vi.restoreAllMocks());

test("две группы: главные действия с подписями и значками, справа — папка и «Ещё действия»", () => {
  withWidth(COMPACT_PX + 200);
  setup();
  const main = screen.getByRole("group", { name: "Главные действия" });
  const side = screen.getByRole("group", { name: "Другие действия" });
  expect(within(main).getAllByRole("button").map((b) => b.textContent)).toEqual(["Экспорт", "В базу знаний"]);
  for (const b of within(main).getAllByRole("button")) expect(b.querySelector("svg")).not.toBeNull();
  expect(within(side).getByRole("button", { name: "Открыть папку" })).toHaveAttribute("title");
  expect(within(side).getByRole("button", { name: "Ещё действия" })).toHaveAttribute("aria-haspopup", "menu");
  // Редкие и опасные действия — только в меню.
  expect(screen.queryByRole("button", { name: /Удалить|Перерасшифровать|Переразделить/ })).toBeNull();
});

test("узкая карточка: подписи свёрнуты в значки, имя кнопки и подсказка остаются", () => {
  withWidth(COMPACT_PX - 100);
  setup();
  const exp = screen.getByRole("button", { name: "Экспорт" });
  expect(exp.querySelector(".sr-only")).toHaveTextContent("Экспорт");
  expect(exp).toHaveAttribute("title");
  expect(screen.getByRole("button", { name: "В базу знаний" }).querySelector(".sr-only")).not.toBeNull();
});

test("широкая карточка: подписи видны", () => {
  withWidth(COMPACT_PX + 1);
  setup();
  expect(screen.getByRole("button", { name: "Экспорт" }).querySelector(".sr-only")).toBeNull();
});

test("«Ещё действия»: меню с клавиатуры — фокус на первом, стрелки, Esc возвращает фокус", async () => {
  setup({ onReanalyze: vi.fn(), onSuggestTitle: vi.fn() });
  const more = screen.getByRole("button", { name: "Ещё действия" });
  more.focus();
  await userEvent.keyboard("{Enter}");
  const menu = screen.getByRole("menu", { name: "Ещё действия с записью" });
  expect(within(menu).getAllByRole("menuitem").map((i) => i.textContent)).toEqual([
    "Переразделить на спикеров…", "Перерасшифровать…", "Переанализировать", "Предложить название", "Удалить…",
  ]);
  // Удаление отделено чертой и стоит последним.
  expect(within(menu).getByRole("separator")).toBeInTheDocument();
  expect(within(menu).getAllByRole("menuitem")[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowDown}");
  expect(within(menu).getByRole("menuitem", { name: "Перерасшифровать…" })).toHaveFocus();
  await userEvent.keyboard("{End}");
  expect(within(menu).getByRole("menuitem", { name: "Удалить…" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(more).toHaveFocus();
});

test("пунктов нет, если действие недоступно", async () => {
  setup({ onRediarize: undefined, canRetranscribe: false });
  await userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));
  expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual(["Удалить…"]);
});

test("«Удалить…» спрашивает подтверждение в том же меню", async () => {
  const p = setup();
  await userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить…" }));
  expect(screen.getByRole("menu")).toHaveTextContent("Удалить запись и расшифровку? Это действие нельзя отменить.");
  expect(p.onDelete).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("menuitem", { name: "Отмена" }));
  expect(screen.getByRole("menuitem", { name: "Удалить…" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить…" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Удалить" }));
  expect(p.onDelete).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("menu")).toBeNull();
});

test("«Перерасшифровать…» предупреждает о потере правок", async () => {
  const p = setup();
  await userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Перерасшифровать…" }));
  expect(screen.getByRole("menu")).toHaveTextContent(/ручные правки и имена/);
  await userEvent.click(screen.getByRole("menuitem", { name: "Перерасшифровать" }));
  expect(p.onRetranscribe).toHaveBeenCalledTimes(1);
});

test("«Переразделить на спикеров…» и папка", async () => {
  const p = setup();
  await userEvent.click(screen.getByRole("button", { name: "Открыть папку" }));
  expect(p.onOpenFolder).toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Переразделить на спикеров…" }));
  expect(p.onRediarize).toHaveBeenCalled();
});

test("экспорт: меню форматов с клавиатуры", async () => {
  const p = setup();
  await userEvent.click(screen.getByRole("button", { name: "Экспорт" }));
  const menu = screen.getByRole("menu", { name: "Формат экспорта" });
  expect(within(menu).getAllByRole("menuitem").map((i) => i.textContent)).toEqual(["md", "txt", "srt"]);
  expect(within(menu).getAllByRole("menuitem")[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowUp}{Enter}");
  expect(p.onExport).toHaveBeenCalledWith("srt");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("пока идёт действие, «В базу знаний» и пункты меню недоступны, «Удалить…» — в фокусе", async () => {
  setup({ busy: true });
  expect(screen.getByRole("button", { name: "В базу знаний" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));
  expect(screen.getByRole("menuitem", { name: "Перерасшифровать…" })).toBeDisabled();
  expect(screen.getByRole("menuitem", { name: "Переразделить на спикеров…" })).toBeDisabled();
  expect(screen.getByRole("menuitem", { name: "Удалить…" })).toHaveFocus();
});
