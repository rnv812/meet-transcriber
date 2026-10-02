/** Живая сводка и подсказки: подписи, ценность, ключи «нового». */

import type { LiveHint, LiveHintKind, LiveSummary } from "../lib/types";

export const KIND_LABEL: Record<LiveHintKind, string> = {
  question: "Стоит спросить",
  risk: "Риск или неясность",
  unanswered: "Без ответа",
  term: "Термин",
  followup: "Следующий шаг",
};

/** Ценность вида (как у ассистента): для строки свёрнутой панели. */
const KIND_VALUE: Record<LiveHintKind, number> = { unanswered: 5, risk: 4, question: 3, followup: 2, term: 1 };

export const EMPTY_SUMMARY: LiveSummary = { topic: "", points: [], decisions: [], tasks: [], open_questions: [] };

/**
 * Самая важная подсказка: закреплённая, затем по ценности вида, затем
 * свежая. Нет подсказок — null.
 */
export function topHint(hints: LiveHint[]): LiveHint | null {
  let best: LiveHint | null = null;
  const rank = (h: LiveHint) => [h.pinned ? 1 : 0, KIND_VALUE[h.kind] ?? 0, h.updated_at] as const;
  for (const h of hints) {
    if (!best) { best = h; continue; }
    const a = rank(h), b = rank(best);
    if (a[0] !== b[0] ? a[0] > b[0] : a[1] !== b[1] ? a[1] > b[1] : a[2] > b[2]) best = h;
  }
  return best;
}

/** Ключ «что именно видел человек»: id и текст — правка текста снова «новая». */
export const hintKey = (h: LiveHint) => `${h.id}:${h.text}`;

/** Пункты сводки ключами (`id` → подпись содержимого): по ним считаем новое. */
export function summaryEntries(s: LiveSummary): [string, string][] {
  const out: [string, string][] = [];
  if (s.topic) out.push(["topic", s.topic]);
  for (const it of [...s.points, ...s.decisions, ...s.open_questions]) out.push([it.id, it.text]);
  for (const t of s.tasks) out.push([t.id, `${t.who ?? ""}|${t.what}|${t.due ?? ""}`]);
  return out;
}

export const summaryIsEmpty = (s: LiveSummary) => summaryEntries(s).length === 0;
