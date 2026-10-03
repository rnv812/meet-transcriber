import { render, screen, waitFor, within } from "@testing-library/react";
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

test("Jira: переключатель и адрес уходят в настройки; негодный адрес не сохраняется", async () => {
  open();
  expect(await toggle("Ссылки на задачи Jira")).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("button", { name: "Как работают ссылки на Jira" })).toBeInTheDocument();
  const base = screen.getByRole("textbox", { name: "Адрес Jira" });
  await userEvent.type(base, "http://jira.example.com");
  expect(screen.getByText("Адрес должен начинаться с https://")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.clear(base);
  await userEvent.type(base, "https://jira.example.com");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    integrations: { jira_base_url: "https://jira.example.com" },
  }));
});

test("Jira: проекты — чипы ключей с вариантами названия, проект по умолчанию", async () => {
  open();
  const key = await screen.findByRole("textbox", { name: "Ключ проекта" });
  expect(screen.getByText(/Проектов нет/)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Проект по умолчанию" })).toBeDisabled();
  // Ключ — заглавными, как в Jira; негодный не добавляется.
  await userEvent.type(key, "1ab");
  expect(screen.getByText(/Ключ проекта — латинские буквы/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Добавить проект" })).toBeDisabled();
  await userEvent.clear(key);
  await userEvent.type(key, "orion{Enter}");
  await userEvent.type(key, "spr");
  await userEvent.click(screen.getByRole("button", { name: "Добавить проект" }));
  const chips = screen.getByRole("list", { name: "Ключи проектов" });
  expect(within(chips).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["ORION", "SPR"]);
  await userEvent.type(key, "SPR");
  expect(screen.getByText("Проект SPR уже в списке")).toBeInTheDocument();
  await userEvent.clear(key);
  // Варианты названия — у раскрытого ключа.
  const orion = within(chips).getByRole("button", { name: /^ORION/ });
  await userEvent.click(orion);
  expect(orion).toHaveAttribute("aria-expanded", "true");
  const alias = screen.getByRole("textbox", { name: "Новый вариант названия ORION" });
  await userEvent.type(alias, "орайон2");
  expect(screen.getByText(/Без цифр/)).toBeInTheDocument();
  await userEvent.clear(alias);
  await userEvent.type(alias, "орайон{Enter}");
  expect(within(screen.getByRole("list", { name: "Варианты названия ORION" })).getByText("орайон")).toBeInTheDocument();
  expect(orion).toHaveTextContent("+1 вариант");
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Проект по умолчанию" }), "ORION");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    integrations: {
      jira_projects: [{ key: "ORION", aliases: ["орайон"] }, { key: "SPR", aliases: [] }],
      jira_default_project: "ORION",
    },
  }));
});

test("Jira: убрали проект по умолчанию — он сбрасывается; шаблон ключа — в «Дополнительно»", async () => {
  vi.mocked(api.getSettings).mockResolvedValue({
    ...structuredClone(settings),
    integrations: {
      jira_base_url: "https://jira.example.com", jira_projects: [{ key: "SPR", aliases: [] }, { key: "OPS", aliases: [] }],
      jira_default_project: "OPS", jira_pattern: "",
    },
  });
  open();
  expect(await screen.findByRole("combobox", { name: "Проект по умолчанию" })).toHaveValue("OPS");
  await userEvent.click(screen.getByRole("button", { name: "Убрать проект OPS" }));
  expect(screen.getByRole("combobox", { name: "Проект по умолчанию" })).toHaveValue("");
  // Шаблон скрыт под «Дополнительно», подсказка в поле — то, что действует без него.
  await userEvent.click(screen.getByText("Дополнительно: шаблон ключа для текста"));
  const pattern = screen.getByRole("textbox", { name: "Шаблон ключа для текста" });
  expect(pattern).toHaveAttribute("placeholder", "(?:SPR)-\\d+");
  await userEvent.type(pattern, "(");
  expect(screen.getByText(/Шаблон не разобрался/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  await userEvent.clear(pattern);
  await userEvent.type(pattern, "DEMO-\\d+");
  await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(api.patchSettings).toHaveBeenCalledWith(ep, {
    integrations: { jira_projects: [{ key: "SPR", aliases: [] }], jira_default_project: "", jira_pattern: "DEMO-\\d+" },
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
