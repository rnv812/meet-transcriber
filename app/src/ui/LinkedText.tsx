/**
 * Текст с подсветкой поиска и ссылками на задачи Jira одновременно.
 *
 * Совпадение поиска может задевать ключ задачи частично: тогда оно режется на
 * куски по границе ссылки, и у каждого куска — тот же номер `data-hit` (поиск
 * в карточке выделяет «текущее» по номеру, см. TranscriptView). Номер
 * совпадения — `firstHit` + его порядковый номер в реплике, как у Highlight.
 */

import type { MouseEvent, ReactNode } from "react";
import { findJira, jiraUrl, type JiraCard, type JiraLinker, type JiraMatch } from "../lib/jira";
import type { Range } from "../lib/search";
import { openUrl } from "../lib/shell";
import "./linked-text.css";

/** Кусок текста [from, to) с подсветкой совпадений `ranges`. */
function marked(text: string, from: number, to: number, ranges: Range[], firstHit: number | undefined, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let at = from;
  ranges.forEach(([s, e], r) => {
    const a = Math.max(s, from);
    const b = Math.min(e, to);
    if (a >= b) return;
    if (a > at) out.push(text.slice(at, a));
    out.push(
      <mark key={`${key}-${r}`} className="hit" data-hit={firstHit === undefined ? undefined : firstHit + r}>
        {text.slice(a, b)}
      </mark>,
    );
    at = b;
  });
  if (at < to) out.push(text.slice(at, to));
  return out;
}

export function JiraLink({ linker, keyText, spoken, children }: {
  linker: JiraLinker; keyText: string;
  /** Ключ сказан словами (эти слова — `children`): значок с ключом поверх них, слова — в подсказке. */
  spoken?: string;
  children: ReactNode;
}) {
  const url = jiraUrl(linker, keyText);
  const open = (e: MouseEvent) => {
    // Ctrl/Shift+щелчок по реплике — выбор реплик, а не переход.
    if (e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    e.stopPropagation();
    // Двойной щелчок — выделение слова (правка L3), а не второе окно браузера.
    if (e.detail > 1) return;
    void openUrl(url);
  };
  // Ключ — псевдоэлементом (data-key): текст реплики в DOM тот же, правка слов
  // и разделение реплики считают позиции по нему.
  return spoken ? (
    <a className="jira-link jira-ref" href={url} data-key={keyText} aria-label={keyText}
      title={`«${spoken}» → ${keyText} · открыть в Jira`} onClick={open} rel="noreferrer noopener" draggable={false}>
      {children}
    </a>
  ) : (
    <a className="jira-link" href={url} title={`Открыть ${keyText} в Jira`} onClick={open} rel="noreferrer noopener"
      draggable={false}>
      {children}
    </a>
  );
}

/**
 * Текст: ссылки Jira и подсветка `ranges` (если есть). Ссылки — `links`
 * (готовые, от резидента: реплика) или найденные в тексте по `linker`.
 */
export function LinkedText({ text, ranges = [], firstHit, linker, links: given }: {
  text: string;
  ranges?: Range[];
  firstHit?: number;
  linker: JiraCard | JiraLinker | null;
  links?: JiraMatch[];
}) {
  const links = linker ? given ?? findJira(text, linker) : [];
  if (!links.length) return <>{marked(text, 0, text.length, ranges, firstHit, "t")}</>;
  const out: ReactNode[] = [];
  let at = 0;
  links.forEach((m, k) => {
    if (m.start < at || m.end > text.length) return;
    if (m.start > at) out.push(...marked(text, at, m.start, ranges, firstHit, `p${k}`));
    out.push(
      <JiraLink key={`j${k}`} linker={linker!} keyText={m.key} spoken={m.source ? m.spoken ?? text.slice(m.start, m.end) : undefined}>
        {marked(text, m.start, m.end, ranges, firstHit, `j${k}`)}
      </JiraLink>,
    );
    at = m.end;
  });
  if (at < text.length) out.push(...marked(text, at, text.length, ranges, firstHit, "tail"));
  return <>{out}</>;
}
