import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../lib/api";
import type { AssistantInfo } from "../../lib/types";
import { SummaryTab, tasksAsTable } from "./SummaryTab";

vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSummary: vi.fn(),
  getLiveDraft: vi.fn(async () => null),
  makeSummary: vi.fn(),
}));

/** «Итоги» по макету: «Задачи» — таблица Кто / Что / Срок в карточке, время у пунктов — чипом. */

const ep = { base: "/api", token: null };
const assistant: AssistantInfo = { provider: "claude-code", setting: "auto", available: {}, knowledge_dir: null, checking: false };

test("«Задачи» списком «Кто: что» становятся таблицей Кто · Что · Срок; срок — из «к среде», «(до 15.10)»", () => {
  const md = "## Решения\n\n- Бюджет утверждён\n\n## Задачи\n\n- Анна: план к среде\n- **Борис** — эндпоинт подписок (до 15.10)\n"
    + "- Обновить библиотеку графиков\n\n## Открытые вопросы\n\n- Кто ведёт демо?";
  expect(tasksAsTable(md)).toBe("## Решения\n\n- Бюджет утверждён\n\n## Задачи\n\n"
    + "| Кто | Что | Срок |\n|---|---|---|\n| Анна | план | к среде |\n| Борис | эндпоинт подписок | до 15.10 |\n"
    + "| — | Обновить библиотеку графиков | — |\n\n## Открытые вопросы\n\n- Кто ведёт демо?");
});

test("«Задачи» уже таблицей или не списком — как есть; без раздела — как есть", () => {
  const table = "## Задачи\n\n| Кто | Что | Срок |\n|---|---|---|\n| Анна | план | — |";
  expect(tasksAsTable(table)).toBe(table);
  const prose = "## Задачи\n\nЗадач не поставили.";
  expect(tasksAsTable(prose)).toBe(prose);
  expect(tasksAsTable("## Итоги\n\n- всё")).toBe("## Итоги\n\n- всё");
});

test("в итогах «Задачи» — карточка с таблицей Aurora; время пунктов — чипом, нажатие ведёт к реплике", async () => {
  vi.mocked(api.getSummary).mockResolvedValue({
    markdown: "## Задачи\n\n- Анна: план к среде\n\n## Цитаты\n\n- [01:05] Борис: Согласен.", created_at: 1000,
  });
  const onTime = vi.fn();
  const { container } = render(<SummaryTab endpoint={ep} id="r1" folder="C:/rec/r1" jobs={[]} assistant={assistant}
    onTime={onTime} />);
  const table = await screen.findByRole("table");
  expect(table.closest(".md-table")).toHaveClass("card");
  expect(table).toHaveClass("tbl");
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Кто", "Что", "Срок"]);
  expect(within(table).getByRole("cell", { name: "к среде" })).toBeInTheDocument();
  const chip = container.querySelector<HTMLButtonElement>("button.md-time")!;
  expect(chip).toHaveTextContent("01:05");
  await userEvent.click(chip);
  expect(onTime).toHaveBeenCalledWith(65);
});
