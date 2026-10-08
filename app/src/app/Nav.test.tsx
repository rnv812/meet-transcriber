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
  // Знак — первым, только для глаз; 24 px — не теряется над кнопкой записи 48 (системной подсказки нет).
  const mark = nav.firstElementChild as HTMLElement;
  expect(mark).not.toHaveAttribute("title");
  expect(mark).toHaveAttribute("aria-hidden", "true");
  expect(mark.querySelector("svg.meet-mark")).toHaveAttribute("width", "24");
  const buttons = within(nav).getAllByRole("button");
  expect(buttons.map((b) => b.getAttribute("aria-label"))).toEqual(["Записи", "Голоса", "Настройки"]);
  expect(screen.getByRole("button", { name: "Записи" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("button", { name: "Голоса" })).not.toHaveAttribute("aria-current");
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  expect(onSelect).toHaveBeenCalledWith("voices");
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  expect(onSelect).toHaveBeenCalledWith("settings");
});

test("рейка без подписей: в кнопке только значок, имя — aria-label и подсказка справа (ui/Tip)", async () => {
  render(<Nav section="voices" onSelect={() => {}} />);
  expect(screen.getByRole("button", { name: "Голоса" })).toHaveAttribute("aria-current", "page");
  for (const name of ["Записи", "Голоса", "Настройки"]) {
    const button = screen.getByRole("button", { name });
    expect(button).toHaveTextContent("");
    expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // Подсказка повторяет имя — для экранного диктора её нет (имя уже прочитано).
    expect(button).not.toHaveAccessibleDescription();
    expect(button).not.toHaveAttribute("title");
  }
  // С клавиатуры облачко — сразу, справа от кнопки.
  await userEvent.tab();
  const tip = document.body.querySelector(".tooltip.tip");
  expect(tip).toHaveTextContent("Записи");
  expect(tip).toHaveAttribute("aria-hidden", "true");
  expect(tip).toHaveAttribute("data-side", "right");
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

test("меню кнопки записи — меню Aurora: узкой «ещё» нет, пункты 44 px со значком, наведение и фокус — --surface-3", () => {
  const rail = css("app", "rail.css");
  expect(rail).not.toMatch(/rail-rec__more/);
  expect(rail).toMatch(/\.rec-menu \{[^}]*border-radius: var\(--r-lg\)/);
  expect(rail).toMatch(/\.rec-menu__item \{[^}]*min-height: 44px/);
  expect(rail).toMatch(/\.rec-menu__item:hover:not\(:disabled\), \.rec-menu__item:focus-visible \{[^}]*background: var\(--surface-3\)/);
  expect(rail).toMatch(/\.rec-menu__icon \{/);
  // Знак 24 с отступом 8, черта — 32.
  expect(rail).toMatch(/\.rail__mark \{[^}]*margin-bottom: 8px/);
  expect(rail).toMatch(/\.rail__sep \{[^}]*width: 32px/);
});
