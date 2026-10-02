/** Живая сводка и подсказки: подписи, ценность, ключи «нового». */

import type { LiveHint, LiveHintKind, LiveSummary } from "../lib/types";

export const KIND_LABEL: Record<LiveHintKind, string> = {
  ask_you: "Вам вопрос",
  question: "Стоит спросить",
  risk: "Риск или неясность",
  unanswered: "Без ответа",
  term: "Термин",
  followup: "Следующий шаг",
};

/** Ценность вида (как у ассистента): для строки свёрнутой панели. */
const KIND_VALUE: Record<LiveHintKind, number> = { ask_you: 6, unanswered: 5, risk: 4, question: 3, followup: 2, term: 1 };

/** «Вам вопрос»: к владельцу обратились и ждут ответа. */
export const isUrgent = (h: LiveHint) => h.kind === "ask_you";

/**
 * Порядок в «Подсказках»: «Вам вопрос» — наверху (свежий первым), остальные
 * — как у ассистента (новые в конце, ничего не прыгает).
 */
export function orderHints(hints: LiveHint[]): LiveHint[] {
  const urgent = hints.filter(isUrgent).sort((a, b) => b.created_at - a.created_at);
  return urgent.length ? [...urgent, ...hints.filter((h) => !isUrgent(h))] : hints;
}

export const EMPTY_SUMMARY: LiveSummary = { topic: "", points: [], decisions: [], tasks: [], open_questions: [] };

/**
 * Самая важная подсказка: «Вам вопрос», затем закреплённая, затем по
 * ценности вида, затем свежая. Нет подсказок — null.
 */
export function topHint(hints: LiveHint[]): LiveHint | null {
  let best: LiveHint | null = null;
  const rank = (h: LiveHint) =>
    [isUrgent(h) ? 1 : 0, h.pinned ? 1 : 0, KIND_VALUE[h.kind] ?? 0, h.updated_at] as const;
  const better = (a: readonly number[], b: readonly number[]) => {
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i]! > b[i]!;
    return false;
  };
  for (const h of hints) {
    if (!best || better(rank(h), rank(best))) best = h;
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
