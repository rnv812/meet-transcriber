import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelpTip } from "../../ui/HelpTip";
import { FolderRow, Radio, Row, SeeAlso, SeeAlsoSlot, Segmented, Switch } from "./Section";

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

test("переключатель — role=switch и aria-checked, без своего бегунка", async () => {
  const onChange = vi.fn();
  render(<Switch label="Расшифровывать сразу" value={false} onChange={onChange} />);
  const s = screen.getByRole("switch", { name: "Расшифровывать сразу" });
  expect(s).toHaveClass("switch");
  expect(s).toHaveAttribute("aria-checked", "false");
  expect(s.querySelector(".switch__knob")).toBeNull();
  await userEvent.click(s);
  expect(onChange).toHaveBeenCalledWith(true);
});

test("короткое перечисление — сегменты Aurora (.tabs--sm) с ролями радио; стрелки двигают выбор", async () => {
  const onChange = vi.fn();
  const { rerender } = render(<Segmented label="Тема" value="dark" onChange={onChange}
    options={[{ value: "system", label: "Системная" }, { value: "dark", label: "Тёмная" }, { value: "light", label: "Светлая" }]} />);
  const group = screen.getByRole("radiogroup", { name: "Тема" });
  expect(group).toHaveClass("tabs", "tabs--sm", "segmented");
  expect(group.closest(".srow")).not.toBeNull();
  const dark = screen.getByRole("radio", { name: "Тёмная" });
  expect(dark).toBeChecked();
  expect(dark).toHaveAttribute("tabindex", "0");
  expect(screen.getByRole("radio", { name: "Светлая" })).toHaveAttribute("tabindex", "-1");
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  expect(onChange).toHaveBeenLastCalledWith("light");
  // Выбранный ещё раз — не изменение.
  onChange.mockClear();
  await userEvent.click(dark);
  expect(onChange).not.toHaveBeenCalled();
  dark.focus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(onChange).toHaveBeenLastCalledWith("system");
  await userEvent.keyboard("{ArrowRight}");
  expect(onChange).toHaveBeenLastCalledWith("light");
  rerender(<Segmented label="Тема" value="dark" onChange={onChange} disabled
    options={[{ value: "system", label: "Системная" }, { value: "dark", label: "Тёмная" }]} />);
  expect(screen.getByRole("radio", { name: "Системная" })).toBeDisabled();
});

test("радио в столбик — Aurora .rd в .check-row", () => {
  render(<Radio label="Прокси" stack value="a" onChange={() => {}}
    options={[{ value: "a", label: "Как в системе" }, { value: "b", label: "Без прокси" }]} />);
  const a = screen.getByRole("radio", { name: "Как в системе" });
  expect(a).toHaveClass("rd");
  expect(a.closest("label")).toHaveClass("check-row");
  expect(screen.getByRole("radiogroup", { name: "Прокси" })).toHaveClass("radios--column");
});

test("«см. также» раздела — в шапку (место SeeAlsoSlot), без места — на месте", () => {
  const slot = document.createElement("p");
  document.body.append(slot);
  const { rerender } = render(<SeeAlso head>Модель — в разделе «Модели ИИ».</SeeAlso>);
  // Без места в шапке (раздел отдельно) — строкой на месте.
  expect(screen.getByText("Модель — в разделе «Модели ИИ».").closest("p")).toHaveClass("see-also");
  rerender(<SeeAlsoSlot.Provider value={slot}><SeeAlso head>Модель — в разделе «Модели ИИ».</SeeAlso></SeeAlsoSlot.Provider>);
  expect(slot).toHaveTextContent("Модель — в разделе «Модели ИИ».");
  // Без head — всегда на месте (ссылка внутри карточки).
  rerender(<SeeAlsoSlot.Provider value={slot}><SeeAlso>Адрес — в разделе «Jira».</SeeAlso></SeeAlsoSlot.Provider>);
  expect(slot).not.toHaveTextContent("Адрес");
  expect(screen.getByText("Адрес — в разделе «Jira».")).toBeInTheDocument();
  slot.remove();
});

test("переключатель может быть недоступен: строка приглушена", () => {
  const { container } = render(<Switch label="Через прокси" value={false} disabled onChange={() => {}} />);
  expect(screen.getByRole("switch", { name: "Через прокси" })).toBeDisabled();
  expect(container.querySelector(".srow")).toHaveClass("srow--disabled");
});
