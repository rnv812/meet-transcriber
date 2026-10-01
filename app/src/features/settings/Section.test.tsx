import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelpTip } from "../../ui/HelpTip";
import { FolderRow, Row, Switch } from "./Section";

vi.mock("../../lib/shell", () => ({ pickFolder: vi.fn(async () => "D:/Notes/Встречи") }));

test("строка: подпись связана с полем, «?» — рядом с подписью, элемент — в своей колонке", () => {
  const { container } = render(
    <Row label="Язык речи" hint="Код языка" htmlFor="lang" help={<HelpTip label="Про язык">Пояснение</HelpTip>}>
      <input id="lang" type="text" />
    </Row>,
  );
  expect(screen.getByRole("textbox", { name: "Язык речи" })).toBeInTheDocument();
  const head = container.querySelector(".srow__head")!;
  expect(within(head as HTMLElement).getByRole("button", { name: "Про язык" })).toBeInTheDocument();
  expect(container.querySelector(".srow__text")).toHaveTextContent("Код языка");
  expect(container.querySelector(".srow__control input")).not.toBeNull();
  expect(container.querySelector(".srow")).not.toHaveClass("srow--stack");
});

test("stack — элемент под подписью во всю ширину", () => {
  const { container } = render(<Row label="Провайдер" stack><span>варианты</span></Row>);
  expect(container.querySelector(".srow")).toHaveClass("srow--stack");
});

test("переключатель с «?»: доступное имя — подпись", () => {
  render(<Switch label="Отмечать одновременную речь" value={false} onChange={() => {}}
    help={<HelpTip label="Про нахлёст">Пояснение</HelpTip>} />);
  expect(screen.getByRole("switch", { name: "Отмечать одновременную речь" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Про нахлёст" })).toBeInTheDocument();
});

test("папка: путь в одну строку с полным путём во всплывающей подсказке, кнопки рядом", async () => {
  const onChange = vi.fn();
  const long = "D:/Очень/длинный/путь/к/хранилищу/заметок/и/папке/для/встреч";
  render(<FolderRow label="Папка для встреч" hint="Куда выгружать" value={long} onChange={onChange} />);
  const group = screen.getByRole("group", { name: "Папка для встреч" });
  const path = within(group).getByText(long);
  const code = path.closest("code")!;
  expect(code).toHaveClass("path--clip");
  expect(code).toHaveAttribute("title", long);
  const folder = code.parentElement!;
  expect(folder).toHaveClass("folder");
  expect(within(folder).getByRole("button", { name: "Выбрать папку…" })).toBeInTheDocument();
  await userEvent.click(within(folder).getByRole("button", { name: "Очистить" }));
  expect(onChange).toHaveBeenCalledWith(null);
  await userEvent.click(within(folder).getByRole("button", { name: "Выбрать папку…" }));
  expect(onChange).toHaveBeenLastCalledWith("D:/Notes/Встречи");
});

test("папка не задана — «Не задана», «Очистить» недоступна", () => {
  render(<FolderRow label="База знаний" hint="Папка" value={null} onChange={() => {}} />);
  const group = screen.getByRole("group", { name: "База знаний" });
  expect(within(group).getByText("Не задана")).toBeInTheDocument();
  expect(within(group).getByRole("button", { name: "Очистить" })).toBeDisabled();
});
