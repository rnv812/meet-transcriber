/**
 * Ссылки для агента: текст, который вставляется в поле ввода Claude Code или
 * Codex во вкладке «Агент» — «Про реплики:» и строки «[мм:сс] Спикер: «текст»».
 *
 * IMPORTANT: текст уходит в псевдоконсоль как ввод с клавиатуры. Из ссылок
 * убирается всё, что терминал понял бы как команду: escape-последовательности,
 * управляющие символы (C0, C1, DEL), символы смены направления письма; переводы
 * строк внутри ссылки — пробелы. Перевод строки остаётся только между строками
 * ссылки, и вставка идёт режимом bracketed paste (см. AgentTab) — Enter она не
 * нажимает.
 */

import { clock } from "./format";

/** Реплика, пункт итогов или подсказка: момент встречи, кто (или вид), текст. */
export type AgentRef = {
  t?: number | null;
  speaker?: string | null;
  text: string;
  /** Раздел итогов («Решения», «Задачи»…). */
  section?: string | null;
};

export type AgentRefKind = "turns" | "summary" | "hint";

export type AgentRequest = {
  refs: AgentRef[];
  kind?: AgentRefKind;
  /** Готовый вопрос перед ссылками («Объясни», «Сформулируй задачу»…). */
  intent?: string;
};

/** Быстрые вопросы к ссылке. */
export const AGENT_INTENTS = ["Объясни", "Что из этого следует?", "Сформулируй задачу", "Проверь по базе знаний"] as const;

/** Длина текста одной ссылки (с многоточием). */
export const REF_TEXT_MAX = 300;
/** Сколько ссылок вставлять; остальные — «…и ещё N». */
export const REF_CAP = 10;
const NAME_MAX = 60;

const HEADINGS: Record<AgentRefKind, [string, string]> = {
  turns: ["Про реплику:", "Про реплики:"],
  summary: ["Про пункт итогов:", "Про пункты итогов:"],
  hint: ["Про подсказку ассистента:", "Про подсказки ассистента:"],
};

/* eslint-disable no-control-regex */
/** CSI (ESC [ … финальный байт), OSC (ESC ] … BEL или ESC \), прочие ESC-последовательности. */
const ESCAPES = /\x1b\[[0-?]*[ -/]*[@-~]?|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]?/g;
/** Переводы строк и табуляция — пробелы. */
const BREAKS = /[\r\n\t\v\f\u0085\u2028\u2029]/g;
/** Остальные управляющие символы и символы направления письма — убрать. */
const CONTROLS = /[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;
/* eslint-enable no-control-regex */

/** Текст для ссылки: одна строка без управляющих символов, пробелы схлопнуты. */
export function cleanRefText(text: string): string {
  return text.replace(ESCAPES, "").replace(BREAKS, " ").replace(CONTROLS, "").replace(/\s+/g, " ").trim();
}

/** Обрезка по символам (кодовым точкам): эмодзи на границе не разрезается пополам. */
function clip(text: string, max: number): string {
  const chars = Array.from(text);
  if (chars.length <= max) return text;
  return `${chars.slice(0, max - 1).join("").trimEnd()}…`;
}

function refLine(ref: AgentRef): string | null {
  const text = clip(cleanRefText(ref.text), REF_TEXT_MAX);
  if (!text) return null;
  const who = clip(cleanRefText(ref.speaker ?? ""), NAME_MAX);
  const time = typeof ref.t === "number" && Number.isFinite(ref.t) ? `[${clock(ref.t)}] ` : "";
  const section = clip(cleanRefText(ref.section ?? ""), NAME_MAX);
  return `${time}${who ? `${who}: ` : ""}«${text}»${section ? ` (раздел «${section}»)` : ""}`;
}

/**
 * Текст для поля ввода агента. Без намерения кончается переводом строки —
 * человек сразу пишет вопрос; с намерением — готовый вопрос, остаётся Enter.
 */
export function agentPrompt({ refs, kind = "turns", intent }: AgentRequest): string {
  const lines = refs.map(refLine).filter((l): l is string => l !== null);
  if (!lines.length) return "";
  const shown = lines.slice(0, REF_CAP);
  const more = lines.length - shown.length;
  const [one, many] = HEADINGS[kind];
  const out = [lines.length === 1 ? one : many, ...shown];
  if (more > 0) out.push(`…и ещё ${more}`);
  const ask = cleanRefText(intent ?? "");
  if (ask) return [/[.?!…]$/.test(ask) ? ask : `${ask}.`, ...out].join("\n");
  return `${out.join("\n")}\n`;
}

const isRefLine = (line: string) => line.startsWith("[") || line.startsWith("«") || line.startsWith("…и ещё");

/** Тот же текст одной строкой: для агента, который не включил режим bracketed paste. */
export function flatPrompt(text: string): string {
  const lines = text.split("\n");
  let out = lines[0] ?? "";
  for (let i = 1; i < lines.length; i++) {
    out += (isRefLine(lines[i - 1]!) && isRefLine(lines[i]!) ? " · " : " ") + lines[i];
  }
  return out;
}

/**
 * Последний рубеж перед псевдоконсолью: одна строка, без единого управляющего
 * символа — ни \r, ни \n (Enter), ни ESC (конец вставки, команды терминала).
 * Что бы ни передали во вкладку «Агент», вставка не нажмёт Enter и не выйдет
 * из поля ввода.
 */
export function pasteLine(text: string): string {
  return flatPrompt(text).replace(ESCAPES, "").replace(BREAKS, " ").replace(CONTROLS, "");
}

/** Текст пункта Markdown без разметки: жирный, курсив, код, ссылки, экранирование. */
export function plainMarkdown(md: string): string {
  return md
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(^|[^\\\p{L}\p{N}])[*_](?=\S)([^*_]*?\S)[*_](?![\p{L}\p{N}])/gu, "$1$2")
    .replace(/\\([\\`*_{}[\]()#+\-.!|>~])/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}
