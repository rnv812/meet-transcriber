import { render, screen } from "@testing-library/react";
import { LiveApp } from "./LiveApp";

test("заглушка панели: ассистент слушает", () => {
  render(<LiveApp />);
  expect(screen.getByText("Ассистент слушает…")).toBeInTheDocument();
});
