import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPane } from "./SettingsPane";
import * as api from "../../lib/api";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  assistant: { knowledge_dir: null, auto_title: false },
  analysis: { auto: true, types: true, importance: true, chapters: true, insights: true, category: true, title: true },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettings).mockResolvedValue(structuredClone(settings));
  vi.mocked(api.patchSettings).mockImplementation(async (_e, u) => ({
    settings: { ...structuredClone(settings), ...(u as object) } as Record<string, unknown>, restart_required: [],
  }));
  vi.mocked(api.getDevices).mockResolvedValue({ available: false, pinning: false });
  vi.mocked(api.getProcesses).mockResolvedValue({ available: false });
});

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="analysis" />);
const toggle = (name: string) => screen.findByRole("switch", { name });

test("раздел «Анализ встречи» есть в меню и открывается по initial", async () => {
  open();
  expect(screen.getByRole("button", { name: "Анализ встречи" })).toHaveAttribute("aria-current", "page");
  expect(await toggle("Анализировать встречу после расшифровки")).toHaveAttribute("aria-checked", "true");
  for (const name of ["Типы реплик", "Важность реплик", "Главы", "Наблюдения", "Категория встречи", "Название встречи"]) {
    expect(await toggle(name)).toHaveAttribute("aria-checked", "true");
  }
  expect(screen.getByRole("button", { name: "Что такое анализ встречи" })).toBeInTheDocument();
});

test("переключатели уходят в analysis", async () => {
  open();
  await userEvent.click(await toggle("Анализировать встречу после расшифровки"));
  await userEvent.click(await toggle("Наблюдения"));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    analysis: { auto: false, insights: false },
  }));
});

test("старый резидент без секции analysis: всё включено по умолчанию", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({ assistant: { knowledge_dir: null } });
  open();
  expect(await toggle("Главы")).toHaveAttribute("aria-checked", "true");
});
