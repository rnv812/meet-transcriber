import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import { DEFAULT_APPEARANCE } from "../../theme/appearance";
import { AppearanceSection } from "./AppearanceSection";

vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), patchSettings: vi.fn() }));

const ep = { base: "/api", token: null };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patchSettings).mockResolvedValue({ settings: {}, restart_required: [] });
});

test("тема: выбор применяется сразу и пишется в настройки", async () => {
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={DEFAULT_APPEARANCE} onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  expect(onPreview).toHaveBeenCalledWith({ ...DEFAULT_APPEARANCE, theme: "light" });
  expect(api.patchSettings).toHaveBeenCalledWith(ep, { ui: { theme: "light", aurora: "violet", aurora_style: "glow", motion: true } });
});

test("палитра — пять образцов с подписями, выбранный отмечен", async () => {
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={{ ...DEFAULT_APPEARANCE, aurora: "blue" }} onPreview={onPreview} />);
  const group = screen.getByRole("radiogroup", { name: "Палитра сияния" });
  const swatches = screen.getAllByRole("radio", { name: /Фиолетовая|Зелёная|Синяя|Красная|Янтарная/ });
  expect(swatches).toHaveLength(5);
  expect(group).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Синяя" })).toBeChecked();
  await userEvent.click(screen.getByRole("radio", { name: "Янтарная" }));
  expect(onPreview).toHaveBeenCalledWith({ ...DEFAULT_APPEARANCE, aurora: "amber" });
});

test("вид сияния и живое сияние", async () => {
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={DEFAULT_APPEARANCE} onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Волны" }));
  expect(onPreview).toHaveBeenLastCalledWith({ ...DEFAULT_APPEARANCE, auroraStyle: "waves" });
  await userEvent.click(screen.getByRole("switch", { name: "Живое сияние" }));
  expect(onPreview).toHaveBeenLastCalledWith({ ...DEFAULT_APPEARANCE, motion: false });
});

test("резидент не сохранил — выбор откатывается и видна ошибка", async () => {
  vi.mocked(api.patchSettings).mockRejectedValue(new Error("Служба записи не отвечает"));
  const onPreview = vi.fn();
  render(<AppearanceSection endpoint={ep} value={DEFAULT_APPEARANCE} onPreview={onPreview} />);
  await userEvent.click(screen.getByRole("radio", { name: "Светлая" }));
  await waitFor(() => expect(onPreview).toHaveBeenLastCalledWith(DEFAULT_APPEARANCE));
  expect(screen.getByRole("alert")).toHaveTextContent("Служба записи не отвечает");
});
