import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";

vi.mock("../state/useResident", () => ({ useResident: () => ({ status: "offline" }) }));
vi.mock("../state/useLibrary", () => ({ useLibrary: () => ({ items: [], jobs: [], loading: false }) }));

test("три раздела; у записей есть список, у голосов — нет", async () => {
  const { container } = render(<App />);
  expect(screen.getByRole("navigation")).toHaveTextContent(/Записи.*Голоса.*Настройки/);
  expect(container.querySelector('[data-pane="list"]')).not.toBeNull();
  await userEvent.click(screen.getByText("Голоса"));
  expect(container.querySelector('[data-pane="list"]')).toBeNull();
});
