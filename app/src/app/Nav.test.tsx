import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Nav } from "./Nav";

const css = (...parts: string[]) => readFileSync(join(process.cwd(), "src", ...parts), "utf8");

test("рейка «Разделы»: знак Meet сверху, разделы — кнопки-значки с именами и aria-current", async () => {
  const onSelect = vi.fn();
  render(<Nav section="recordings" onSelect={onSelect} />);
  const nav = screen.getByRole("navigation", { name: "Разделы" });
  // Знак — первым, только для глаз (имя «Meet» — во всплывающей подсказке).
  const mark = nav.firstElementChild as HTMLElement;
  expect(mark).toHaveAttribute("title", "Meet");
  expect(mark.querySelector("svg.meet-mark")).toHaveAttribute("aria-hidden", "true");
  const buttons = within(nav).getAllByRole("button");
  expect(buttons.map((b) => b.getAttribute("aria-label"))).toEqual(["Записи", "Голоса", "Настройки"]);
  expect(screen.getByRole("button", { name: "Записи" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("button", { name: "Голоса" })).not.toHaveAttribute("aria-current");
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  expect(onSelect).toHaveBeenCalledWith("voices");
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  expect(onSelect).toHaveBeenCalledWith("settings");
});

test("рейка без подписей: в кнопке только значок, имя — aria-label и подсказка справа", () => {
  render(<Nav section="voices" onSelect={() => {}} />);
  expect(screen.getByRole("button", { name: "Голоса" })).toHaveAttribute("aria-current", "page");
  for (const name of ["Записи", "Голоса", "Настройки"]) {
    const button = screen.getByRole("button", { name });
    expect(button).toHaveTextContent("");
    expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // Подсказка повторяет имя — для экранного диктора её нет (имя уже прочитано).
    const tip = button.parentElement?.querySelector(".tooltip");
    expect(tip).toHaveTextContent(name);
    expect(tip).toHaveAttribute("aria-hidden", "true");
  }
});

test("кнопка записи — под знаком, предупреждения — внизу над «Настройками»", () => {
  render(<Nav section="recordings" onSelect={() => {}}
    record={<button type="button">Начать запись</button>}
    alerts={<button type="button">Мало места: 3 ГБ</button>} />);
  const names = within(screen.getByRole("navigation", { name: "Разделы" })).getAllByRole("button")
    .map((b) => b.getAttribute("aria-label") ?? b.textContent);
  expect(names).toEqual(["Начать запись", "Записи", "Голоса", "Мало места: 3 ГБ", "Настройки"]);
});

test("рейка — 60 px на токенах Aurora; прежние правила навигации и шапки убраны", () => {
  const rail = css("app", "rail.css");
  expect(rail).toMatch(/\.rail \{[^}]*width: 60px/);
  expect(rail).toMatch(/\.rail \{[^}]*border-right: 1px solid var\(--hairline\)/);
  // «Настройки» прижаты книзу.
  expect(rail).toMatch(/\.rail__foot \{[^}]*margin-top: auto/);
  const tokens = css("theme", "tokens.css");
  expect(tokens).not.toMatch(/\.nav\b|data-nav-rail|\.content \{|--nav-w/);
  expect(css("features", "recordings", "recordings.css")).not.toMatch(/\.topbar|\.rec-badge|\.split\b/);
});
