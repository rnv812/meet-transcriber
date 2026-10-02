import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

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
