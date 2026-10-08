import { render } from "@testing-library/react";
import { AgentMark } from "./AgentMark";

test("в покое — монохромная звезда, без своего градиента в стиле", () => {
  const { container } = render(<AgentMark />);
  const mark = container.querySelector(".agent-mark")!;
  expect(mark).toHaveAttribute("data-state", "rest");
  expect(mark).toHaveAttribute("aria-hidden", "true");
  expect(mark.querySelector("svg")!.getAttribute("style") ?? "").not.toContain("url(");
});

test("ищет и пишет — градиент свой у каждого экземпляра", () => {
  const { container } = render(<><AgentMark state="search" /><AgentMark state="write" /></>);
  const [a, b] = Array.from(container.querySelectorAll(".agent-mark svg"));
  const idA = a!.querySelector("linearGradient")!.id;
  const idB = b!.querySelector("linearGradient")!.id;
  expect(idA).not.toBe(idB);
  // jsdom сериализует url(#id) с кавычками — допускаем оба вида.
  expect(a!.getAttribute("style")).toMatch(new RegExp(`url\\(["']?#${idA}["']?\\)`));
  expect(b!.getAttribute("style")).toMatch(new RegExp(`fill: url\\(["']?#${idB}["']?\\)`));
});

test("с подписью — изображение с именем", () => {
  const { getByRole } = render(<AgentMark state="listen" label="Агент слушает" />);
  expect(getByRole("img", { name: "Агент слушает" })).toHaveAttribute("data-state", "listen");
});
