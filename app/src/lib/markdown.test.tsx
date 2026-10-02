import { render, screen, within } from "@testing-library/react";
import { Markdown } from "./markdown";

test("заголовки: уровень markdown + 2, текст без решёток", () => {
  render(<Markdown source={"# Итоги встречи\n\n## Решения ##\n### Детали"} />);
  expect(screen.getByRole("heading", { level: 3, name: "Итоги встречи" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 4, name: "Решения" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 5, name: "Детали" })).toBeInTheDocument();
});

test("таблица «Кто · Что · Срок» с выравниванием и разметкой в ячейках", () => {
  const md = [
    "## Задачи",
    "",
    "| Кто | Что | Срок |",
    "|-----|:---:|-----:|",
    "| Демьян | **Отчёт** по `Q3` | пятница |",
    "| Мария | Созвон с a\\|b |",
  ].join("\n");
  render(<Markdown source={md} />);
  const table = screen.getByRole("table");
  expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Кто", "Что", "Срок"]);
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(3);
  const cells = within(rows[1]!).getAllByRole("cell");
  expect(cells.map((c) => c.textContent)).toEqual(["Демьян", "Отчёт по Q3", "пятница"]);
  expect(within(cells[1]!).getByText("Отчёт").tagName).toBe("STRONG");
  expect(within(cells[1]!).getByText("Q3").tagName).toBe("CODE");
  expect(cells[1]).toHaveStyle({ textAlign: "center" });
  expect(cells[2]).toHaveStyle({ textAlign: "right" });
  // Короткая строка дополняется пустыми ячейками, экранированная черта — текст.
  expect(within(rows[2]!).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Мария", "Созвон с a|b", ""]);
});

test("жирный, курсив, код, зачёркнутый", () => {
  const { container } = render(<Markdown source={"Это **важно**, *срочно*, __тоже__ и `config.json`, ~~старое~~"} />);
  expect(screen.getByText("важно").tagName).toBe("STRONG");
  expect(screen.getByText("тоже").tagName).toBe("STRONG");
  expect(screen.getByText("срочно").tagName).toBe("EM");
  expect(screen.getByText("config.json").tagName).toBe("CODE");
  expect(screen.getByText("старое").tagName).toBe("DEL");
  expect(container.querySelector("p")).toHaveTextContent("Это важно, срочно, тоже и config.json, старое");
});

test("snake_case и одиночные звёздочки не становятся курсивом", () => {
  const { container } = render(<Markdown source={"файл my_file_name.txt, 2 * 3 * 4"} />);
  expect(container.querySelector("em")).toBeNull();
  expect(container.querySelector("p")).toHaveTextContent("файл my_file_name.txt, 2 * 3 * 4");
});

test("<script> и прочий HTML — просто текст", () => {
  const { container } = render(
    <Markdown source={"<script>alert(1)</script> **x**\n\n| a |\n|---|\n| <img src=x onerror=alert(1)> |"} />);
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(container).toHaveTextContent("<script>alert(1)</script> x");
  expect(screen.getByRole("cell")).toHaveTextContent("<img src=x onerror=alert(1)>");
});

test("ссылки — текст с адресом в подсказке, без <a>", () => {
  const { container } = render(
    <Markdown source={"См. [протокол](https://wiki/x) и [клик](javascript:alert(1))"} />);
  expect(container.querySelector("a")).toBeNull();
  expect(screen.getByText("протокол")).toHaveAttribute("title", "https://wiki/x");
  expect(container.querySelector("p")).toHaveTextContent("См. протокол и клик");
});

test("маркированный, нумерованный и вложенный списки", () => {
  const md = [
    "- Первое",
    "- Второе",
    "  - вложенное **а**",
    "  - вложенное б",
    "- Третье",
    "",
    "3. три",
    "4. четыре",
  ].join("\n");
  const { container } = render(<Markdown source={md} />);
  const ul = container.querySelector("ul")!;
  expect(ul.querySelectorAll(":scope > li")).toHaveLength(3);
  const nested = ul.querySelector("li ul")!;
  expect(nested.querySelectorAll("li")).toHaveLength(2);
  expect(within(nested as HTMLElement).getByText("а").tagName).toBe("STRONG");
  const ol = container.querySelector("ol")!;
  expect(ol).toHaveAttribute("start", "3");
  expect(ol.querySelectorAll("li")).toHaveLength(2);
});

test("абзацы, переносы, цитата, код-блок и черта", () => {
  const md = [
    "Первая строка",
    "вторая строка",
    "",
    "> цитата **жирно**",
    "",
    "```",
    "**не жирный** <b>",
    "```",
    "",
    "---",
    "",
    "Последний",
  ].join("\n");
  const { container } = render(<Markdown source={md} />);
  const ps = container.querySelectorAll(":scope > div > p");
  expect(ps[0]!.querySelector("br")).not.toBeNull();
  expect(container.querySelector("blockquote strong")).toHaveTextContent("жирно");
  expect(container.querySelector("pre code")).toHaveTextContent("**не жирный** <b>");
  expect(container.querySelector("pre strong")).toBeNull();
  expect(container.querySelector("hr")).not.toBeNull();
  expect(screen.getByText("Последний")).toBeInTheDocument();
});

test("Windows-переводы строк и пустой текст", () => {
  const { container, rerender } = render(<Markdown source={"# А\r\n\r\n- б\r\n"} />);
  expect(screen.getByRole("heading", { name: "А" })).toBeInTheDocument();
  expect(container.querySelector("li")).toHaveTextContent(/^б$/);
  rerender(<Markdown source="" />);
  expect(container.querySelector(".md")?.childElementCount).toBe(0);
});

test("таймкоды в скобках — кнопки, если есть onTime; без него — текст", async () => {
  const onTime = vi.fn();
  const { rerender } = render(<Markdown source={"Решили в [12:34], подтвердили в [01:02:03].\n\n- пункт [00:05]"} onTime={onTime} />);
  const buttons = screen.getAllByRole("button");
  expect(buttons.map((b) => b.textContent)).toEqual(["12:34", "01:02:03", "00:05"]);
  buttons[0]!.click();
  buttons[1]!.click();
  buttons[2]!.click();
  expect(onTime.mock.calls).toEqual([[754], [3723], [5]]);
  rerender(<Markdown source="Решили в [12:34]." />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText("Решили в [12:34].")).toBeInTheDocument();
});

test("ссылка рядом с таймкодом остаётся ссылкой", () => {
  render(<Markdown source="[док](http://x) и [3:07]" onTime={vi.fn()} />);
  expect(screen.getByText("док")).toHaveClass("md-link");
  expect(screen.getByRole("button", { name: "3:07" })).toBeInTheDocument();
});

test("itemAction: действие у каждого пункта списка и строки таблицы — с текстом без разметки и разделом", () => {
  const md = [
    "## Решения",
    "",
    "- выпускаем **в пятницу**",
    "  - вложенный пункт",
    "",
    "## Задачи",
    "",
    "| Кто | Что | Срок |",
    "|---|---|---|",
    "| Демьян | `отчёт` | — |",
  ].join("\n");
  const seen: Array<[string, string | null]> = [];
  render(<Markdown source={md} itemAction={(text, section) => {
    seen.push([text, section]);
    return <button type="button">✦ {text}</button>;
  }} />);
  expect(seen).toEqual([
    ["выпускаем в пятницу", "Решения"],
    ["вложенный пункт", "Решения"],
    ["Кто: Демьян; Что: отчёт; Срок: —", "Задачи"],
  ]);
  expect(screen.getByRole("button", { name: "✦ выпускаем в пятницу" }).closest("li")).toHaveTextContent("выпускаем");
  // У таблицы — своя колонка для действий, заголовок которой видит только экранный диктор.
  expect(screen.getAllByRole("columnheader")).toHaveLength(4);
  expect(screen.getByRole("button", { name: "✦ Кто: Демьян; Что: отчёт; Срок: —" }).closest("td")).not.toBeNull();
});

test("без itemAction — ни кнопок, ни лишней колонки", () => {
  render(<Markdown source={"- пункт\n\n| a | b |\n|---|---|\n| 1 | 2 |"} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getAllByRole("columnheader")).toHaveLength(2);
});
