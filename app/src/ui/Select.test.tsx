import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Select, type SelectOption } from "./Select";
import { TIP_DELAY_MS } from "./Tip";
import { Popover } from "./Popover";

const OPTIONS: SelectOption[] = [
  { value: "claude", label: "Claude Code" },
  { value: "codex", label: "Codex", detail: "сейчас" },
  { value: "opencode", label: "OpenCode", disabled: true },
  { value: "local", label: "Локальная модель" },
];

function Harness({ initial = "claude", onChange, ...rest }: {
  initial?: string; onChange?: (v: string) => void;
} & Partial<Parameters<typeof Select>[0]>) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <Select aria-label="Агент" options={OPTIONS} value={value}
        onChange={(v) => { setValue(v); onChange?.(v); }} {...rest} />
      <button type="button">Снаружи</button>
    </>
  );
}

const combo = () => screen.getByRole("combobox", { name: "Агент" });
const list = () => screen.queryByRole("listbox");

test("закрыт: кнопка-список Aurora с выбранным значением и стрелкой", () => {
  render(<Harness />);
  const c = combo();
  expect(c.tagName).toBe("BUTTON");
  expect(c).toHaveClass("select-btn", "select-btn--md");
  expect(c).toHaveAttribute("aria-expanded", "false");
  expect(c).toHaveAttribute("aria-haspopup", "listbox");
  expect(c).toHaveTextContent("Claude Code");
  expect(c.querySelector("svg.ic")).not.toBeNull();
  expect(list()).toBeNull();
  expect(document.querySelector("select")).toBeNull();
});

test("размер sm — 32 px класса Aurora; ширина и класс снаружи", () => {
  render(<Harness size="sm" width={170} className="agent__select" />);
  expect(combo()).toHaveClass("select-btn--sm", "agent__select");
  expect(combo()).toHaveStyle({ width: "170px" });
});

test("нажатие раскрывает список порталом: пункты, выбранный отмечен, фокус остаётся на кнопке", async () => {
  render(<div className="host"><Harness /></div>);
  await userEvent.click(combo());
  const lb = list()!;
  expect(lb).toHaveClass("menu", "open");
  expect(document.querySelector(".host")!.contains(lb)).toBe(false);
  expect(combo()).toHaveAttribute("aria-expanded", "true");
  expect(combo()).toHaveAttribute("aria-controls", lb.id);
  const opts = within(lb).getAllByRole("option");
  expect(opts.map((o) => o.textContent)).toEqual(["Claude Code", "Codexсейчас", "OpenCode", "Локальная модель"]);
  expect(opts[0]).toHaveAttribute("aria-selected", "true");
  expect(opts[1]).toHaveAttribute("aria-selected", "false");
  expect(opts[2]).toHaveAttribute("aria-disabled", "true");
  expect(within(opts[1]!).getByText("сейчас").tagName).toBe("SMALL");
  expect(opts[0]!.querySelector("svg.check")).not.toBeNull();
  expect(combo()).toHaveFocus();
  expect(combo()).toHaveAttribute("aria-activedescendant", opts[0]!.id);
});

test("выбор мышью: onChange, список закрыт, фокус на кнопке", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  await userEvent.click(combo());
  await userEvent.click(screen.getByRole("option", { name: /Codex/ }));
  expect(onChange).toHaveBeenCalledWith("codex");
  expect(list()).toBeNull();
  expect(combo()).toHaveTextContent("Codex");
  expect(combo()).toHaveFocus();
});

test("недоступный пункт не выбирается", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  await userEvent.click(combo());
  await userEvent.click(screen.getByRole("option", { name: "OpenCode" }));
  expect(onChange).not.toHaveBeenCalled();
  expect(list()).not.toBeNull();
});

test("тот же пункт — без onChange (как у родного списка)", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  await userEvent.click(combo());
  await userEvent.click(screen.getByRole("option", { name: "Claude Code" }));
  expect(onChange).not.toHaveBeenCalled();
  expect(list()).toBeNull();
});

test("клавиатура: ↓ раскрывает, ↓↑ ходят мимо недоступных, Enter выбирает", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  combo().focus();
  await userEvent.keyboard("{ArrowDown}");
  expect(list()).not.toBeNull();
  const active = () => document.getElementById(combo().getAttribute("aria-activedescendant") ?? "");
  expect(active()).toHaveTextContent("Claude Code");
  await userEvent.keyboard("{ArrowDown}");
  expect(active()).toHaveTextContent("Codex");
  expect(active()).toHaveClass("active");
  await userEvent.keyboard("{ArrowDown}");
  expect(active()).toHaveTextContent("Локальная модель"); // OpenCode недоступен — пропущен
  await userEvent.keyboard("{ArrowDown}");
  expect(active()).toHaveTextContent("Локальная модель"); // конец — без перехода по кругу
  await userEvent.keyboard("{ArrowUp}");
  expect(active()).toHaveTextContent("Codex");
  await userEvent.keyboard("{Enter}");
  expect(onChange).toHaveBeenCalledWith("codex");
  expect(list()).toBeNull();
  expect(combo()).toHaveFocus();
});

test("клавиатура: Home/End, пробел выбирает, Esc закрывает без изменений", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} initial="codex" />);
  combo().focus();
  await userEvent.keyboard("{Enter}");
  const active = () => document.getElementById(combo().getAttribute("aria-activedescendant") ?? "");
  expect(active()).toHaveTextContent("Codex"); // открылся на выбранном
  await userEvent.keyboard("{End}");
  expect(active()).toHaveTextContent("Локальная модель");
  await userEvent.keyboard("{Home}");
  expect(active()).toHaveTextContent("Claude Code");
  await userEvent.keyboard("{Escape}");
  expect(list()).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
  expect(combo()).toHaveFocus();
  await userEvent.keyboard(" ");
  expect(list()).not.toBeNull();
  await userEvent.keyboard("{End} ");
  expect(onChange).toHaveBeenCalledWith("local");
});

test("Esc закрывает только список, не окно вокруг", async () => {
  const onClose = vi.fn();
  function InPopover() {
    const [anchor, setAnchor] = useState<HTMLElement | null>(null);
    return (
      <>
        <button type="button" ref={setAnchor}>Якорь</button>
        {anchor && (
          <Popover anchor={anchor} onClose={onClose} label="Окно">
            <Harness />
          </Popover>
        )}
      </>
    );
  }
  render(<InPopover />);
  await userEvent.click(combo());
  expect(list()).not.toBeNull();
  await userEvent.keyboard("{Escape}");
  expect(list()).toBeNull();
  expect(onClose).not.toHaveBeenCalled();
});

test("выбор в списке внутри всплывающего окна не закрывает окно", async () => {
  const onClose = vi.fn();
  const onChange = vi.fn();
  function InPopover() {
    const [anchor, setAnchor] = useState<HTMLElement | null>(null);
    return (
      <>
        <button type="button" ref={setAnchor}>Якорь</button>
        {anchor && <Popover anchor={anchor} onClose={onClose} label="Окно"><Harness onChange={onChange} /></Popover>}
      </>
    );
  }
  render(<InPopover />);
  await userEvent.click(combo());
  await userEvent.click(screen.getByRole("option", { name: "Локальная модель" }));
  expect(onChange).toHaveBeenCalledWith("local");
  expect(onClose).not.toHaveBeenCalled();
});

test("ввод букв: открытый — к пункту на эти буквы; закрытый — раскрывает на нём", async () => {
  render(<Harness />);
  combo().focus();
  await userEvent.keyboard("л");
  expect(list()).not.toBeNull();
  const active = () => document.getElementById(combo().getAttribute("aria-activedescendant") ?? "");
  expect(active()).toHaveTextContent("Локальная модель");
  await userEvent.keyboard("{Escape}");
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    await userEvent.keyboard("{ArrowDown}");
    act(() => { vi.advanceTimersByTime(1000); });
    await userEvent.keyboard("co");
    expect(active()).toHaveTextContent("Codex");
    act(() => { vi.advanceTimersByTime(1000); });
    await userEvent.keyboard("c");
    expect(active()).toHaveTextContent("Claude Code");
  } finally {
    vi.useRealTimers();
  }
});

test("клик снаружи и Tab закрывают без изменений", async () => {
  const onChange = vi.fn();
  render(<Harness onChange={onChange} />);
  await userEvent.click(combo());
  await userEvent.click(screen.getByRole("button", { name: "Снаружи" }));
  expect(list()).toBeNull();
  await userEvent.click(combo());
  await userEvent.keyboard("{ArrowDown}");
  await userEvent.tab();
  expect(list()).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});

test("повторное нажатие на кнопку закрывает список", async () => {
  render(<Harness />);
  await userEvent.click(combo());
  await userEvent.click(combo());
  expect(list()).toBeNull();
});

test("значение вне списка — заглушка (действие «Объединить с…»)", async () => {
  const onChange = vi.fn();
  render(<Select aria-label="Объединить с…" placeholder="Объединить с…" value=""
    options={[{ value: "Анна", label: "Анна" }, { value: "Борис", label: "Борис" }]} onChange={onChange} />);
  const c = screen.getByRole("combobox", { name: "Объединить с…" });
  expect(c).toHaveTextContent("Объединить с…");
  expect(c.querySelector(".selectbox__placeholder")).not.toBeNull();
  await userEvent.click(c);
  expect(screen.getAllByRole("option").every((o) => o.getAttribute("aria-selected") === "false")).toBe(true);
  // Ничего не выбрано — открыт на первом пункте.
  const active = document.getElementById(c.getAttribute("aria-activedescendant") ?? "");
  expect(active).toHaveTextContent("Анна");
  await userEvent.keyboard("{Enter}");
  expect(onChange).toHaveBeenCalledWith("Анна");
});

test("подпись через <label htmlFor>: id на кнопке", () => {
  render(<><label htmlFor="asr-device">Устройство</label>
    <Select id="asr-device" value="cpu" options={[{ value: "cpu", label: "Процессор" }, { value: "cuda", label: "Видеокарта" }]}
      onChange={() => {}} /></>);
  expect(screen.getByRole("combobox", { name: "Устройство" })).toHaveTextContent("Процессор");
});

test("недоступный: не раскрывается; причина — подсказкой и описанием", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    render(<Harness disabled disabledReason="Сначала остановите агента" />);
    const c = combo();
    expect(c).toBeDisabled();
    expect(c).toHaveAccessibleDescription("Сначала остановите агента");
    fireEvent.click(c);
    expect(list()).toBeNull();
    fireEvent.mouseEnter(c);
    act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
    expect(document.body.querySelector(".tooltip.tip")).toHaveTextContent("Сначала остановите агента");
  } finally {
    vi.useRealTimers();
  }
});

test("размер sm — плотные пункты в списке", async () => {
  render(<Harness size="sm" />);
  await userEvent.click(combo());
  expect(list()).toHaveClass("selectbox__menu--sm");
});
