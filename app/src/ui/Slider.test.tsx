import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { Slider } from "./Slider";

function Harness({ onChange, ...rest }: { onChange?: (v: number) => void } & Partial<Parameters<typeof Slider>[0]>) {
  const [value, setValue] = useState(70);
  return <Slider aria-label="Порог узнавания голоса" min={50} max={95} value={value}
    format={(v) => `${v}%`} onChange={(v) => { setValue(v); onChange?.(v); }} {...rest} />;
}

const slider = () => screen.getByRole("slider", { name: "Порог узнавания голоса" });

test("родной бегунок без системного вида: роль, границы, значение и текст значения", () => {
  render(<Harness />);
  const s = slider();
  expect(s).toHaveAttribute("type", "range");
  expect(s).toHaveClass("slider__input");
  expect(s).toHaveAttribute("min", "50");
  expect(s).toHaveAttribute("max", "95");
  expect(s).toHaveValue("70");
  expect(s).toHaveAttribute("aria-valuetext", "70%");
});

test("значение справа — моноширинным, для диктора скрыто (оно уже в aria-valuetext)", () => {
  const { container } = render(<Harness />);
  const out = container.querySelector(".slider__value")!;
  expect(out).toHaveTextContent("70%");
  expect(out).toHaveAttribute("aria-hidden", "true");
  // Место под самое длинное значение — число не толкает строку.
  expect((out as HTMLElement).style.minWidth).toBe("3ch");
});

test("showValue={false} — без подписи значения", () => {
  const { container } = render(<Harness showValue={false} />);
  expect(container.querySelector(".slider__value")).toBeNull();
});

test("заливка до значения — переменной --slider-fill", () => {
  const { container } = render(<Harness />);
  const root = container.querySelector(".slider") as HTMLElement;
  expect(root.style.getPropertyValue("--slider-fill")).toBe(`${((70 - 50) / 45) * 100}%`);
});

test("изменение — числом", () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  fireEvent.change(slider(), { target: { value: "80" } });
  expect(onChange).toHaveBeenLastCalledWith(80);
  expect(slider()).toHaveAttribute("aria-valuetext", "80%");
});

test("размер sm и недоступность", () => {
  const { container } = render(<Harness size="sm" disabled />);
  expect(container.querySelector(".slider")).toHaveClass("slider--sm");
  expect(slider()).toBeDisabled();
});

test("подпись через <label htmlFor> и id", () => {
  render(<><label htmlFor="redia-sens">Чувствительность</label>
    <Slider id="redia-sens" min={0} max={100} step={5} value={50} onChange={() => {}} /></>);
  expect(screen.getByRole("slider", { name: "Чувствительность" })).toHaveValue("50");
});

test("значение за границами показывается прижатым к ним", () => {
  render(<Harness value={120} />);
  expect(slider()).toHaveValue("95");
  expect(slider()).toHaveAttribute("aria-valuetext", "95%");
});
