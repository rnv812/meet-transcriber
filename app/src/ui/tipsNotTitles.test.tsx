// Последние системные `title` в ui/ (категория, бейдж «ИИ», ссылка Jira, разделитель) —
// облачка Aurora (ui/Tip). У ui/Truncate `title` остаётся намеренно: это полный текст обрезанной строки.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import ts from "typescript";
import { AiBadge } from "./AiBadge";
import { CategoryMark, CategoryMenu } from "./Category";
import { JiraLink } from "./LinkedText";
import { Splitter } from "./Splitter";
import { TIP_DELAY_MS } from "./Tip";

afterEach(() => { vi.useRealTimers(); });

const tipEl = () => document.body.querySelector<HTMLElement>(".tooltip.tip");
const hover = (el: Element) => {
  fireEvent.mouseEnter(el);
  act(() => { vi.advanceTimersByTime(TIP_DELAY_MS); });
};

const daily = { id: "daily", name: "Дейлик", color: "#7c5cff", description: "Короткая встреча команды" };
const linker = { re: /\b[A-Z][A-Z0-9]+-\d+\b/g, base: "https://jira.example.com" };

test("метка категории: описание и облачко вместо title", () => {
  vi.useFakeTimers();
  const { container } = render(<CategoryMark category={daily} />);
  const mark = container.querySelector<HTMLElement>(".cat-mark")!;
  expect(mark).not.toHaveAttribute("title");
  expect(mark).toHaveAccessibleDescription("Дейлик — Короткая встреча команды");
  hover(mark);
  expect(tipEl()).toHaveTextContent("Дейлик — Короткая встреча команды");
});

test("меню категорий: описание пункта — облачком сбоку; без описания — подсказки нет", () => {
  vi.useFakeTimers();
  render(<CategoryMenu list={[daily, { ...daily, id: "x", name: "Прочее", description: "" }]} current={null}
    onPick={() => {}} />);
  const item = screen.getByRole("menuitemradio", { name: "Дейлик" });
  expect(item).not.toHaveAttribute("title");
  expect(item).toHaveAccessibleDescription("Короткая встреча команды");
  hover(item);
  expect(tipEl()).toHaveAttribute("data-side", "right");
  const plain = screen.getByRole("menuitemradio", { name: "Прочее" });
  expect(plain).not.toHaveAccessibleDescription();
});

test("бейдж «ИИ»: облачко с моделью; текст для диктора — внутри, описанием не дублируется", () => {
  vi.useFakeTimers();
  const { container } = render(<AiBadge by="Claude Code (sonnet)" />);
  const badge = container.querySelector<HTMLElement>(".ai-badge")!;
  expect(badge).not.toHaveAttribute("title");
  expect(badge).not.toHaveAttribute("aria-description");
  hover(badge);
  expect(tipEl()).toHaveTextContent("Название предложено ИИ (Claude Code (sonnet)) — нажмите, чтобы изменить");
});

test("ссылка Jira: «открыть в Jira» — описанием и облачком, без title", () => {
  vi.useFakeTimers();
  render(<JiraLink linker={linker} keyText="SPR-131">SPR-131</JiraLink>);
  const link = screen.getByRole("link", { name: "SPR-131" });
  expect(link).not.toHaveAttribute("title");
  expect(link).toHaveAccessibleDescription("Открыть SPR-131 в Jira");
  hover(link);
  expect(tipEl()).toHaveTextContent("Открыть SPR-131 в Jira");
});

test("разделитель: подсказка облачком (у вертикального — сбоку), нажатие её прячет", () => {
  vi.useFakeTimers();
  render(<Splitter label="Ширина списка" value={300} min={260} max={560} panel="before"
    onPreview={() => {}} onCommit={() => {}} onReset={() => {}} />);
  const bar = screen.getByRole("separator", { name: "Ширина списка" });
  expect(bar).not.toHaveAttribute("title");
  expect(bar).toHaveAccessibleDescription("Потяните, чтобы изменить ширину. Двойной щелчок или Enter — как было");
  hover(bar);
  expect(tipEl()).toHaveAttribute("data-side", "right");
  fireEvent.pointerDown(bar, { button: 0, clientX: 300, pointerId: 1 });
  expect(tipEl()).toBeNull();
});

/**
 * Страж всего окна: системный `title` у элемента разметки (`<span title=…>`) и через DOM
 * (`el.title = …`, `setAttribute("title", …)`) — только в списке ниже, с причиной. Подсказка —
 * `ui/Tip` (`useTip`), у обрезанного текста — `ui/Truncate`. `title` компонентов (`EmptyState`,
 * `HelpTip`, `ConfirmDialog`…) — их заголовок, не подсказка, и не в счёт.
 */
const TITLE_ALLOWED: Record<string, string> = {
  "ui/Truncate.tsx": "полный текст обрезанной строки — системная подсказка, только когда текст обрезан",
};

function tsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? tsxFiles(join(dir, e.name))
      : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [join(dir, e.name)] : []);
}

/** Места с системным `title` в файле: «строка: что». */
function nativeTitles(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const found: string[] = [];
  const at = (node: ts.Node) => source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(source);
      // Элемент разметки — со строчной буквы (span, li, code); компонент — с заглавной.
      if (/^[a-z]/.test(tag)) {
        for (const a of node.attributes.properties) {
          if (ts.isJsxAttribute(a) && a.name.getText(source) === "title") found.push(`${at(a)}: <${tag} title>`);
        }
      }
    }
    // `x.title = …` — только в компонентах (.tsx): в .ts это поле данных (фильтр поиска и т. п.).
    if (file.endsWith(".tsx") && ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken
      && ts.isPropertyAccessExpression(node.left) && node.left.name.text === "title") {
      found.push(`${at(node)}: .title =`);
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.name.text === "setAttribute" && node.arguments[0]
      && ts.isStringLiteral(node.arguments[0]) && node.arguments[0].text === "title") {
      found.push(`${at(node)}: setAttribute("title")`);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

test("системный title в окне — только в списке исключений (ui/Truncate)", () => {
  const src = join(process.cwd(), "src");
  const bad: string[] = [];
  const used = new Set<string>();
  for (const file of tsxFiles(src)) {
    const rel = relative(src, file).split(sep).join("/");
    if (rel.startsWith("theme/aurora/")) continue; // дизайн-система, генерирует scripts/vendor_aurora.py
    const found = nativeTitles(file);
    if (!found.length) continue;
    if (rel in TITLE_ALLOWED) used.add(rel);
    else bad.push(...found.map((f) => `${rel}:${f}`));
  }
  expect(bad).toEqual([]);
  // Устаревшая строка исключений роняет тест: место перевели — уберите строку.
  expect([...used].sort()).toEqual(Object.keys(TITLE_ALLOWED).sort());
});

test("в этих примитивах системного title больше нет", () => {
  for (const file of ["Category.tsx", "AiBadge.tsx", "LinkedText.tsx", "Splitter.tsx"]) {
    const src = readFileSync(join(process.cwd(), "src", "ui", file), "utf8");
    expect(src, file).not.toMatch(/\stitle=\{/);
  }
});
