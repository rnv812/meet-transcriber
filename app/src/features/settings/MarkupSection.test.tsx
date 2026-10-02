import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import { DEFAULT_PREFS, markupPrefs } from "../../lib/markupPrefs";
import { SettingsPane } from "./SettingsPane";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  getDevices: vi.fn(),
  getProcesses: vi.fn(),
}));

const ep = { base: "/api", token: null };
const settings = {
  transcript_view: {
    types: true, importance: true, chapters: true, insights: true, curve: "hover", bar_labels: true, jira: true,
  },
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

const open = () => render(<SettingsPane endpoint={ep} recordingsDir={null} initial="markup" />);
const toggle = (name: string) => screen.findByRole("switch", { name });

test("раздел «Расшифровка: подсветка и разметка»: всё включено, кривая — при наведении, подсказки", async () => {
  open();
  expect(screen.getByRole("button", { name: "Подсветка расшифровки" })).toHaveAttribute("aria-current", "page");
  for (const name of ["Значки типов реплик", "Полоса у важных реплик", "Заголовки глав", "Блок «Наблюдения»",
    "Подписи глав на полосе плеера"]) {
    expect(await toggle(name)).toHaveAttribute("aria-checked", "true");
  }
  expect(screen.getByRole("radio", { name: "При наведении" })).toBeChecked();
  for (const tip of ["Что такое подсветка и разметка", "Что такое кривая важности", "Клавиши плеера"]) {
    expect(screen.getByRole("button", { name: tip })).toBeInTheDocument();
  }
});

test("переключатели и кривая уходят в transcript_view", async () => {
  open();
  await userEvent.click(await toggle("Значки типов реплик"));
  await userEvent.click(await toggle("Подписи глав на полосе плеера"));
  await userEvent.click(screen.getByRole("radio", { name: "Всегда" }));
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    transcript_view: { types: false, bar_labels: false, curve: "always" },
  }));
});

test("что показывать: «Подсветка и разметка» вместе с «Анализом встречи»; нет разделов — всё включено", () => {
  expect(markupPrefs({})).toEqual(DEFAULT_PREFS);
  const p = markupPrefs({
    transcript_view: { types: false, importance: true, chapters: false, insights: true, curve: "off", bar_labels: false },
    analysis: { importance: false, insights: false },
  });
  expect(p.parts).toEqual({ types: true, importance: false, chapters: true, insights: false });
  expect([p.typeIcons, p.keyBorder, p.chapterHeads, p.insights, p.curve, p.barLabels])
    .toEqual([false, true, false, true, "off", false]);
  expect(markupPrefs({ transcript_view: { curve: "сбоку" } }).curve).toBe("hover");
});

test("Jira: переключатель, адрес и ключи уходят в настройки; негодный адрес не сохраняется", async () => {
  open();
  expect(await toggle("Ссылки на задачи Jira")).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("button", { name: "Как работают ссылки на Jira" })).toBeInTheDocument();
  const base = screen.getByRole("textbox", { name: "Адрес Jira" });
  await userEvent.type(base, "http://jira.example.com");
  expect(screen.getByText("Адрес должен начинаться с https://")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.clear(base);
  await userEvent.type(base, "https://jira.example.com");
  await userEvent.type(screen.getByRole("textbox", { name: "Ключи задач" }), "SPR, OPS");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    integrations: { jira_base_url: "https://jira.example.com", jira_keys: "SPR, OPS" },
  }));
});

test("Jira выключили с негодным адресом — «Сохранить» доступно, адрес не уходит", async () => {
  open();
  const base = await screen.findByRole("textbox", { name: "Адрес Jira" });
  await userEvent.type(base, "http://jira.example.com");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.click(await toggle("Ссылки на задачи Jira"));
  expect(screen.getByRole("textbox", { name: "Адрес Jira" })).toBeDisabled();
  const save = screen.getByRole("button", { name: "Сохранить" });
  expect(save).toBeEnabled();
  await userEvent.click(save);
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, { transcript_view: { jira: false } }));
});
