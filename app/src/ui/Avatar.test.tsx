import { render } from "@testing-library/react";
import { Avatar } from "./Avatar";

test("цвет человека — кольцом (--person) на поверхности Aurora, без своего цвета текста", () => {
  const { container } = render(<Avatar name="Анна Смирнова" color="#a33" />);
  const el = container.querySelector(".avatar") as HTMLElement;
  expect(el).toHaveClass("avatar--person");
  expect(el.style.getPropertyValue("--person")).toBe("#a33");
  // Инициалы — цветом текста темы: литерал #fff не читался на светлых цветах людей.
  expect(el.style.color).toBe("");
  expect(el.style.background).toBe("");
  expect(el).toHaveTextContent("АС");
});

test("неназванный спикер и человек без цвета — без кольца", () => {
  const { container } = render(<><Avatar name="Спикер 1" color="#a33" /><Avatar name="Борис" /></>);
  const [unnamed, plain] = [...container.querySelectorAll<HTMLElement>(".avatar")];
  expect(unnamed).toHaveClass("avatar--unnamed");
  expect(unnamed).not.toHaveClass("avatar--person");
  expect(plain).not.toHaveClass("avatar--person");
});
