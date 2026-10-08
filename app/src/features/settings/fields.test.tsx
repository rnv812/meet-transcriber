import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MinutesSlider, NumberField, SecondsRow, TextRow } from "./fields";

function Seconds({ initial = 60, onChange = () => {} }: { initial?: number; onChange?: (v: number) => void }) {
  const [v, setV] = useState(initial);
  return <SecondsRow id="min-call" label="Минимальная длительность звонка" hint="Короче — не расшифровывается"
    value={v} onChange={(x) => { setV(x); onChange(x); }} />;
}

test("число — поле Aurora без системных стрелок, «−»/«+» и единица; не меньше нуля", async () => {
  const onChange = vi.fn();
  render(<Seconds initial={5} onChange={onChange} />);
  const field = screen.getByLabelText("Минимальная длительность звонка");
  expect(field).toHaveClass("field", "field--sm", "stepper__input");
  expect(field).toHaveValue(5);
  expect(screen.getByText("секунд")).toHaveClass("unit");
  await userEvent.click(screen.getByRole("button", { name: "Больше: Минимальная длительность звонка" }));
  expect(onChange).toHaveBeenLastCalledWith(10);
  await userEvent.click(screen.getByRole("button", { name: "Меньше: Минимальная длительность звонка" }));
  await userEvent.click(screen.getByRole("button", { name: "Меньше: Минимальная длительность звонка" }));
  expect(onChange).toHaveBeenLastCalledWith(0);
  expect(screen.getByRole("button", { name: "Меньше: Минимальная длительность звонка" })).toBeDisabled();
  await userEvent.clear(field);
  await userEvent.type(field, "42");
  expect(onChange).toHaveBeenLastCalledWith(42);
});

test("пустое число — null (решает вызывающий), «+» от минимума; граница — кнопка недоступна", async () => {
  const onChange = vi.fn();
  const { rerender } = render(<NumberField id="w" label="Окно" min={5} max={120} step={5} value={null} onChange={onChange} />);
  await userEvent.click(screen.getByRole("button", { name: "Больше: Окно" }));
  expect(onChange).toHaveBeenLastCalledWith(10);
  rerender(<NumberField id="w" label="Окно" min={5} max={120} step={5} value={120} invalid onChange={onChange} />);
  expect(screen.getByRole("button", { name: "Больше: Окно" })).toBeDisabled();
  expect(screen.getByRole("spinbutton")).toHaveAttribute("aria-invalid", "true");
});

test("минуты — бегунок Aurora (ui/Slider) с подписью значения", () => {
  render(<MinutesSlider id="grace" label="Ждать повторного подключения" min={1} max={60} value={10} onChange={() => {}} />);
  const slider = screen.getByRole("slider", { name: "Ждать повторного подключения" });
  expect(slider).toHaveClass("slider__input");
  expect(slider).toHaveAttribute("aria-valuetext", "10 мин");
});

test("текстовые поля — Aurora field--sm: короткое, среднее и во всю ширину моноширинным", () => {
  render(<>
    <TextRow id="a" label="Имя" short value="Вы" onChange={() => {}} />
    <TextRow id="b" label="Модель" value="sonnet" onChange={() => {}} />
    <TextRow id="c" label="Команда" wide value="" onChange={() => {}} />
  </>);
  expect(screen.getByLabelText("Имя")).toHaveClass("field", "field--sm", "input--short");
  expect(screen.getByLabelText("Модель")).toHaveClass("field", "field--sm", "input--mid");
  expect(screen.getByLabelText("Команда")).toHaveClass("field", "field--sm", "input--wide", "input--mono");
});
