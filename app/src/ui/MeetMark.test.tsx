import { render } from "@testing-library/react";
import { MeetMark } from "./MeetMark";

test("знак Meet — диск сияния из палитры, сердцевина вырезана", () => {
  const { container } = render(<><MeetMark size={22} /><MeetMark size={22} /></>);
  const [a, b] = Array.from(container.querySelectorAll("svg.meet-mark"));
  expect(a).toHaveAttribute("aria-hidden", "true");
  expect(a!.getAttribute("width")).toBe("22");
  const stops = Array.from(a!.querySelectorAll("stop")).map((s) => s.getAttribute("style"));
  expect(stops.join(" ")).toContain("var(--wave-");
  expect(a!.querySelector("mask")!.id).not.toBe(b!.querySelector("mask")!.id);
});

test("с подписью — изображение с именем", () => {
  const { getByRole } = render(<MeetMark label="Meet" />);
  expect(getByRole("img", { name: "Meet" })).toBeInTheDocument();
});
