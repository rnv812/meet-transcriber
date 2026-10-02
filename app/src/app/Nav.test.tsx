import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Nav } from "./Nav";

test("над разделами — знак и имя «Meet», разделы — кнопки", async () => {
  const onSelect = vi.fn();
  render(<Nav section="recordings" onSelect={onSelect} />);
  const nav = screen.getByRole("navigation");
  expect(nav.querySelector(".nav__brand")?.textContent).toBe("Meet");
  expect(nav.querySelector(".nav__mark")?.getAttribute("aria-hidden")).toBe("true");
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Записи", "Голоса", "Настройки"]);
  expect(screen.getByRole("button", { name: "Записи" })).toHaveAttribute("aria-current", "page");
  await userEvent.click(screen.getByRole("button", { name: "Голоса" }));
  expect(onSelect).toHaveBeenCalledWith("voices");
});

test("у раздела значок и подсказка: в узком окне остаются только значки", () => {
  render(<Nav section="voices" onSelect={() => {}} />);
  for (const name of ["Записи", "Голоса", "Настройки"]) {
    const button = screen.getByRole("button", { name });
    expect(button).toHaveAttribute("title", name);
    expect(button.querySelector("svg")).toHaveAttribute("width", "16");
  }
});

test("до 1000 px навигация — полоса 56 px, список — 260 px (карточке остаётся не меньше 520 px в 900 px)", () => {
  const css = readFileSync(join(process.cwd(), "src", "theme", "tokens.css"), "utf8");
  const narrow = /@media \(max-width: 1000px\) \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
  expect(narrow).toMatch(/--nav-w: 56px/);
  expect(narrow).toMatch(/--list-w: 260px/);
  expect(narrow).toMatch(/\.nav \.nav__label \{[^}]*clip/);
  expect(900 - 56 - 260).toBeGreaterThanOrEqual(520);
});
