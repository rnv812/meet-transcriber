import { render } from "@testing-library/react";
import { Skeleton } from "./Loading";

test("заготовка — мерцание Aurora, размеры окна", () => {
  const { container } = render(<Skeleton width={120} height={12} />);
  const s = container.querySelector(".skeleton")!;
  expect(s).toHaveClass("sk");
  expect(s).toHaveAttribute("aria-hidden", "true");
});
