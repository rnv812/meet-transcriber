import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useState } from "react";

import { Segmented, type SegmentedOption } from "./Segmented";

type Theme = "system" | "dark" | "light";
const OPTIONS: SegmentedOption<Theme>[] = [
  { value: "system", label: "Системная" }, { value: "dark", label: "Тёмная" }, { value: "light", label: "Светлая" },
];

function Host({ initial = "dark", onChange }: { initial?: Theme; onChange?: (v: Theme) => void }) {
  const [value, setValue] = useState<Theme>(initial);
  return <Segmented label="Тема" value={value} options={OPTIONS}
    onChange={(v) => { setValue(v); onChange?.(v); }} />;
}

test("radiogroup с radio, выбранный — aria-checked; в обходе только он", () => {
  render(<Host />);
  const group = screen.getByRole("radiogroup", { name: "Тема" });
  expect(group).toHaveClass("tabs", "tabs--sm", "segmented");
  const radios = screen.getAllByRole("radio");
  expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
  expect(radios.map((r) => r.getAttribute("tabindex"))).toEqual(["-1", "0", "-1"]);
});

test("щелчок выбирает; выбранный ещё раз — не изменение", async () => {
  const onChange = vi.fn();
  render(<Host onChange={onChange} />);
  await userEvent.click(screen.getByRole("radio", { name: "Тёмная" }));
  expect(onChange).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  expect(onChange).toHaveBeenCalledWith("light");
  expect(screen.getByRole("radio", { name: "Светлая" })).toHaveAttribute("aria-checked", "true");
});

test("стрелки двигают выбор и фокус, Home/End — к крайним, за край не уходят", async () => {
  const onChange = vi.fn();
  render(<Host onChange={onChange} />);
  screen.getByRole("radio", { name: "Тёмная" }).focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(onChange).toHaveBeenLastCalledWith("light");
  expect(screen.getByRole("radio", { name: "Светлая" })).toHaveFocus();
  onChange.mockClear();
  await userEvent.keyboard("{ArrowDown}");
  expect(onChange).not.toHaveBeenCalled();
  await userEvent.keyboard("{Home}");
  expect(onChange).toHaveBeenLastCalledWith("system");
  expect(screen.getByRole("radio", { name: "Системная" })).toHaveFocus();
  await userEvent.keyboard("{End}");
  expect(onChange).toHaveBeenLastCalledWith("light");
  await userEvent.keyboard("{ArrowLeft}{ArrowUp}");
  expect(onChange).toHaveBeenLastCalledWith("system");
});

test("размеры: sm — .tabs--sm, md — обычные .tabs", () => {
  const { rerender } = render(<Segmented label="Тема" value="dark" options={OPTIONS} onChange={() => {}} />);
  expect(screen.getByRole("radiogroup")).toHaveClass("tabs--sm");
  rerender(<Segmented label="Тема" size="md" value="dark" options={OPTIONS} onChange={() => {}} />);
  const group = screen.getByRole("radiogroup");
  expect(group).toHaveClass("tabs", "segmented");
  expect(group).not.toHaveClass("tabs--sm");
});

test("disabled: варианты недоступны, группа aria-disabled, клавиши ничего не меняют", () => {
  const onChange = vi.fn();
  render(<Segmented label="Тема" value="dark" options={OPTIONS} onChange={onChange} disabled />);
  expect(screen.getByRole("radiogroup")).toHaveAttribute("aria-disabled", "true");
  for (const r of screen.getAllByRole("radio")) expect(r).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("radiogroup"), { key: "ArrowRight" });
  expect(onChange).not.toHaveBeenCalled();
});

test("имя группы — по видимой подписи; значения нет среди вариантов — в обходе первый", () => {
  render(
    <>
      <span id="freq">Как часто писать</span>
      <Segmented labelledBy="freq" value={"нет" as Theme} options={OPTIONS} onChange={() => {}} />
    </>,
  );
  expect(screen.getByRole("radiogroup", { name: "Как часто писать" })).not.toHaveAttribute("aria-label");
  expect(screen.getByRole("radio", { name: "Системная" })).toHaveAttribute("tabindex", "0");
});

test("пояснение варианта — диктору; подсказка — Tip (описание без title)", () => {
  render(<Segmented label="Профиль" value="work" onChange={() => {}} options={[
    { value: "work", label: "рабочая", description: "Рабочая встреча" },
    { value: "personal", label: "личный", tip: "Свой промпт без базы знаний" },
  ]} />);
  expect(screen.getByRole("radio", { name: "рабочая" })).toHaveAccessibleDescription("Рабочая встреча");
  const personal = screen.getByRole("radio", { name: "личный" });
  expect(personal).toHaveAccessibleDescription("Свой промпт без базы знаний");
  expect(personal).not.toHaveAttribute("title");
});

test("стили — только токены Aurora; выбранный по aria-checked как выбранная вкладка", () => {
  const css = readFileSync(join(process.cwd(), "src", "ui", "segmented.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(/i);
  expect(css).toMatch(/\.segmented button\[aria-checked='true'\]\s*\{[^}]*background: var\(--deep\)/);
});
