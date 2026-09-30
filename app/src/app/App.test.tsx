import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";

vi.mock("../state/useResident", () => ({ useResident: () => ({ status: "offline" }) }));
const useLibrarySpy = vi.hoisted(() => vi.fn());
vi.mock("../state/useLibrary", () => ({ useLibrary: useLibrarySpy }));
beforeEach(() => {
  useLibrarySpy.mockReset();
  useLibrarySpy.mockReturnValue({ items: [], jobs: [], loading: false, error: null, refresh: async () => {} });
});

test("три раздела; у записей есть список, у голосов — нет", async () => {
  const { container } = render(<App />);
  expect(screen.getByRole("navigation")).toHaveTextContent(/Записи.*Голоса.*Настройки/);
  expect(container.querySelector('[data-pane="list"]')).not.toBeNull();
  await userEvent.click(screen.getByText("Голоса"));
  expect(container.querySelector('[data-pane="list"]')).toBeNull();
});

test("строка поиска из списка уходит в useLibrary", async () => {
  render(<App />);
  await userEvent.type(screen.getByRole("searchbox"), "план");
  expect(useLibrarySpy).toHaveBeenLastCalledWith(null, "план", undefined);
});
