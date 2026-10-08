import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Lightbox } from "./Lightbox";

/** Просмотр картинки поверх окна (0.5): Esc и щелчок мимо — закрыть; «Открыть файл», «Показать в папке». */

test("картинка во весь экран: имя для диктора, фокус — на «Закрыть»; Esc закрывает", async () => {
  const onClose = vi.fn();
  render(<Lightbox src="blob:x" name="скрин.png" onClose={onClose} />);
  const dialog = screen.getByRole("dialog", { name: "скрин.png" });
  expect(dialog).toHaveAttribute("aria-modal", "true");
  expect(screen.getByRole("img", { name: "скрин.png" })).toHaveAttribute("src", "blob:x");
  expect(screen.getByRole("button", { name: "Закрыть" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("щелчок мимо картинки закрывает, по самой картинке — нет", () => {
  const onClose = vi.fn();
  render(<Lightbox src="blob:x" name="скрин.png" onClose={onClose} />);
  fireEvent.click(screen.getByRole("img", { name: "скрин.png" }));
  expect(onClose).not.toHaveBeenCalled();
  fireEvent.click(document.querySelector(".lightbox")!);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("«Открыть файл» и «Показать в папке» — только когда переданы; ошибка видна", async () => {
  const onOpen = vi.fn(async () => { throw new Error("этот файл приложение не открывает"); });
  const onReveal = vi.fn(async () => {});
  const { rerender } = render(<Lightbox src="blob:x" name="a.png" onClose={() => {}} />);
  expect(screen.queryByRole("button", { name: "Открыть файл" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Показать в папке" })).toBeNull();
  rerender(<Lightbox src="blob:x" name="a.png" onClose={() => {}} onOpen={onOpen} onReveal={onReveal} />);
  await userEvent.click(screen.getByRole("button", { name: "Показать в папке" }));
  expect(onReveal).toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Открыть файл" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("этот файл приложение не открывает");
});
