/**
 * Мини-рендер Markdown в React-элементы — для итогов и ответов модели.
 *
 * IMPORTANT: из текста не делается ни строчки HTML — только React-узлы, поэтому
 * `<script>` и прочая разметка в ответе модели остаются текстом. Поддержано то,
 * что модели пишут в итогах: заголовки, абзацы, списки (с вложенностью),
 * GFM-таблицы, цитаты, блоки кода, черта; внутри строки — жирный, курсив,
 * зачёркнутый, код и ссылки. Ссылка — текст с адресом в подсказке: открывать
 * произвольные адреса оболочка не умеет, и окну это не нужно.
 */

import { Fragment, useMemo, type ReactNode } from "react";

type Align = "left" | "center" | "right" | undefined;
type Item = { text: string; children: Block[] };
type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "para"; lines: string[] }
  | { kind: "list"; ordered: boolean; start: number; items: Item[] }
  | { kind: "table"; head: string[]; align: Align[]; rows: string[][] }
  | { kind: "code"; text: string }
  | { kind: "quote"; blocks: Block[] }
  | { kind: "hr" };

const HEADING = /^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$/;
const FENCE = /^ {0,3}(```|~~~)/;
const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;

const indentOf = (s: string) => s.replace(/\t/g, "    ").length - s.replace(/\t/g, "    ").trimStart().length;
const isOrdered = (marker: string) => /\d/.test(marker);
const itemOf = (line: string) => (HR.test(line) ? null : ITEM.exec(line));

const isTableStart = (lines: string[], i: number) =>
  i + 1 < lines.length && lines[i]!.includes("|") && lines[i + 1]!.includes("|") && TABLE_SEP.test(lines[i + 1]!);

const startsBlock = (lines: string[], i: number) => {
  const line = lines[i]!;
  return FENCE.test(line) || HEADING.test(line) || HR.test(line) || QUOTE.test(line) || ITEM.test(line)
    || isTableStart(lines, i);
};

/** Ячейки строки таблицы; `\|` — черта внутри ячейки. */
function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells: string[] = [];
  let cur = "";
  for (let k = 0; k < s.length; k++) {
    const c = s[k]!;
    if (c === "\\" && s[k + 1] === "|") { cur += "|"; k++; continue; }
    if (c === "|") { cells.push(cur.trim()); cur = ""; continue; }
    cur += c;
  }
  cells.push(cur.trim());
  return cells;
}

function alignOf(sep: string): Align {
  const left = sep.startsWith(":");
  const right = sep.endsWith(":");
  return left && right ? "center" : right ? "right" : left ? "left" : undefined;
}

function parseTable(lines: string[], i: number): [Block, number] {
  const head = splitRow(lines[i]!);
  const align = splitRow(lines[i + 1]!).map(alignOf);
  const rows: string[][] = [];
  let k = i + 2;
  for (; k < lines.length && lines[k]!.trim() && lines[k]!.includes("|"); k++) {
    const cells = splitRow(lines[k]!);
    rows.push(head.map((_, c) => cells[c] ?? ""));
  }
  return [{ kind: "table", head, align, rows }, k];
}

function parseList(lines: string[], i: number, indent: number): [Block, number] {
  const marker = ITEM.exec(lines[i]!)![2]!;
  const ordered = isOrdered(marker);
  const start = ordered ? parseInt(marker, 10) : 1;
  const items: Item[] = [];
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) {
      // Пустая строка внутри списка: дальше снова пункт — список продолжается.
      let j = i + 1;
      while (j < lines.length && !lines[j]!.trim()) j++;
      const next = j < lines.length ? itemOf(lines[j]!) : null;
      if (next && indentOf(next[1]!) >= indent) { i = j; continue; }
      break;
    }
    const m = itemOf(line);
    if (m) {
      const ind = indentOf(m[1]!);
      if (ind < indent) break;
      if (ind >= indent + 2 && items.length) {
        const [sub, next] = parseList(lines, i, ind);
        items.at(-1)!.children.push(sub);
        i = next;
        continue;
      }
      if (isOrdered(m[2]!) !== ordered) break;
      items.push({ text: m[3]!, children: [] });
      i++;
      continue;
    }
    // Строка с отступом — продолжение пункта.
    if (items.length && indentOf(line) > indent && !startsBlock(lines, i)) {
      items.at(-1)!.text += ` ${line.trim()}`;
      i++;
      continue;
    }
    break;
  }
  return [{ kind: "list", ordered, start, items }, i];
}

function parseBlocks(lines: string[]): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (!line.trim()) { i++; continue; }
    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i]!.trimStart().startsWith(fence[1]!); i++) body.push(lines[i]!);
      i++; // закрывающая ограда (или конец текста)
      out.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ kind: "heading", level: heading[1]!.length, text: heading[2]! });
      i++;
      continue;
    }
    if (HR.test(line)) { out.push({ kind: "hr" }); i++; continue; }
    if (isTableStart(lines, i)) {
      const [table, next] = parseTable(lines, i);
      out.push(table);
      i = next;
      continue;
    }
    if (QUOTE.test(line)) {
      const inner: string[] = [];
      for (; i < lines.length && QUOTE.test(lines[i]!); i++) inner.push(QUOTE.exec(lines[i]!)![1]!);
      out.push({ kind: "quote", blocks: parseBlocks(inner) });
      continue;
    }
    const item = ITEM.exec(line);
    if (item) {
      const [list, next] = parseList(lines, i, indentOf(item[1]!));
      out.push(list);
      i = next;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && (para.length === 0 || !startsBlock(lines, i))) {
      para.push(lines[i]!.trim());
      i++;
    }
    out.push({ kind: "para", lines: para });
  }
  return out;
}

// --- строка -----------------------------------------------------------------------

const ESCAPE = /^\\([\\`*_{}[\]()#+\-.!|>~])/;
const CODE = /^(`+)(.+?)\1(?!`)/;
const LINK = /^\[([^\]]+)\]\(([^)\s]*)(?:\s+"[^"]*")?\)/;
const STRONG_STAR = /^\*\*(?=\S)(.*?\S)\*\*/;
const STRONG_UNDER = /^__(?=\S)(.*?\S)__/;
const EM_STAR = /^\*(?=[^\s*])([^*]*?[^\s*])\*(?!\*)/;
const EM_UNDER = /^_(?=[^\s_])([^_]*?[^\s_])_/;
const DEL = /^~~(?=\S)(.*?\S)~~/;
const WORD = /[\p{L}\p{N}_]/u;
const isWord = (c: string | undefined) => c !== undefined && WORD.test(c);

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let buf = "";
  const push = (node: ReactNode) => {
    if (buf) { out.push(buf); buf = ""; }
    out.push(node);
  };
  let i = 0;
  while (i < text.length) {
    const rest = text.slice(i);
    const c = text[i]!;
    let m: RegExpExecArray | null = null;
    if (c === "\\" && (m = ESCAPE.exec(rest))) {
      buf += m[1];
    } else if (c === "`" && (m = CODE.exec(rest))) {
      push(<code key={out.length}>{m[2]!.trim()}</code>);
    } else if (c === "[" && (m = LINK.exec(rest))) {
      push(<span key={out.length} className="md-link" title={m[2]}>{inline(m[1]!)}</span>);
    } else if (c === "*" && (m = STRONG_STAR.exec(rest) ?? EM_STAR.exec(rest))) {
      const Tag = m[0].startsWith("**") ? "strong" : "em";
      push(<Tag key={out.length}>{inline(m[1]!)}</Tag>);
    } else if (c === "_" && !isWord(text[i - 1]) && (m = STRONG_UNDER.exec(rest) ?? EM_UNDER.exec(rest))
      && !isWord(text[i + m[0].length])) {
      const Tag = m[0].startsWith("__") ? "strong" : "em";
      push(<Tag key={out.length}>{inline(m[1]!)}</Tag>);
    } else if (c === "~" && (m = DEL.exec(rest))) {
      push(<del key={out.length}>{inline(m[1]!)}</del>);
    } else {
      m = null;
    }
    if (m) { i += m[0].length; continue; }
    buf += c;
    i++;
  }
  if (buf) out.push(buf);
  return out;
}

// --- отрисовка ----------------------------------------------------------------------

function render(blocks: Block[]): ReactNode[] {
  return blocks.map((b, k) => {
    switch (b.kind) {
      case "heading": {
        // Уровни сдвинуты: над итогами уже есть заголовок карточки.
        const Tag = `h${Math.min(6, b.level + 2)}` as "h3";
        return <Tag key={k} className={`md-h md-h${b.level}`}>{inline(b.text)}</Tag>;
      }
      case "para":
        return (
          <p key={k}>
            {b.lines.map((line, j) => <Fragment key={j}>{j > 0 && <br />}{inline(line)}</Fragment>)}
          </p>
        );
      case "list": {
        const items = b.items.map((it, j) => <li key={j}>{inline(it.text)}{render(it.children)}</li>);
        return b.ordered
          ? <ol key={k} start={b.start !== 1 ? b.start : undefined}>{items}</ol>
          : <ul key={k}>{items}</ul>;
      }
      case "table":
        return (
          <div key={k} className="md-table">
            <table>
              <thead>
                <tr>{b.head.map((c, j) => <th key={j} style={{ textAlign: b.align[j] }}>{inline(c)}</th>)}</tr>
              </thead>
              <tbody>
                {b.rows.map((row, r) => (
                  <tr key={r}>{row.map((c, j) => <td key={j} style={{ textAlign: b.align[j] }}>{inline(c)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      case "code":
        return <pre key={k}><code>{b.text}</code></pre>;
      case "quote":
        return <blockquote key={k}>{render(b.blocks)}</blockquote>;
      case "hr":
        return <hr key={k} />;
    }
  });
}

export function Markdown({ source, className }: { source: string; className?: string }) {
  const nodes = useMemo(() => render(parseBlocks(source.replace(/\r\n?/g, "\n").split("\n"))), [source]);
  return <div className={className ? `md ${className}` : "md"}>{nodes}</div>;
}
