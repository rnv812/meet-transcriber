import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReplacementsEditor, withRule } from "./ReplacementsEditor";

const rules = [{ from: "кубер нетис", to: "Kubernetes" }, { from: "дев опс", to: "DevOps" }];

test("список исправлений: удалить одно", async () => {
  const onChange = vi.fn();
  render(<ReplacementsEditor value={rules} onChange={onChange} />);
  const list = screen.getByRole("list", { name: "Исправления для будущих расшифровок" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  await userEvent.click(screen.getByRole("button", { name: "Удалить исправление «дев опс → DevOps»" }));
  expect(onChange).toHaveBeenCalledWith([rules[0]]);
});

test("добавить исправление; то же «как распознаётся» заменяет прежнее", async () => {
  const onChange = vi.fn();
  render(<ReplacementsEditor value={rules} onChange={onChange} />);
  const add = screen.getByRole("button", { name: "Добавить" });
  expect(add).toBeDisabled();
  await userEvent.type(screen.getByRole("textbox", { name: "Как распознаётся" }), "Кубер  Нетис");
  await userEvent.type(screen.getByRole("textbox", { name: "Как правильно" }), "K8s{Enter}");
  expect(onChange).toHaveBeenCalledWith([rules[1], { from: "Кубер Нетис", to: "K8s" }]);
  expect(withRule([], { from: "ёлка", to: "Ёлка" })).toEqual([{ from: "ёлка", to: "Ёлка" }]);
});

test("пусто — подсказка, откуда берутся исправления", () => {
  render(<ReplacementsEditor value={undefined} onChange={() => {}} />);
  expect(screen.getByText(/Исправлений пока нет/)).toBeInTheDocument();
});
