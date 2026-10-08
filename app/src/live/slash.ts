/**
 * Слэш-команды в строке ввода чата (0.4, спец. §6): окно только подсказывает, выполняет
 * ассистент (`meet/assist/slash.py`) — одинаково во время встречи и после неё.
 *
 * Список — `agent.commands` ребёнка (команды Meet этого провайдера и команды CLI из
 * `initialize`); без него (после встречи, старый ребёнок) — команды Meet.
 */

import type { AgentCommand, AgentMcpServer, AgentModel } from "../lib/types";

/** Команды Meet (как `MEET_COMMANDS` у ассистента). */
export const MEET_COMMANDS: AgentCommand[] = [
  { name: "help", hint: "", description: "Команды чата", source: "meet" },
  { name: "mcp", hint: "[reconnect|enable|disable <сервер>]", description: "MCP-серверы и их состояние", source: "meet" },
  { name: "clear", hint: "", description: "Новый разговор с агентом (лента остаётся)", source: "meet" },
  { name: "model", hint: "[модель]", description: "Какая модель отвечает, сменить модель", source: "meet" },
  { name: "compact", hint: "[указание]", description: "Сжать контекст разговора", source: "meet" },
];

/** Сколько команд показать в списке (дальше — сужайте вводом). */
export const SLASH_SHOWN = 50;

/**
 * Что ищем: поле — «/» и имя без пробела (`/mc` → «mc»); иначе null (текст, «//…», аргументы).
 */
export function slashQuery(text: string): string | null {
  const m = /^\/([A-Za-z][\w:.-]*)?$/.exec(text);
  return m ? (m[1] ?? "") : null;
}

/** Команды по набранному: сначала начинающиеся с него, потом содержащие; без повторов имён. */
export function matchCommands(commands: AgentCommand[], query: string): AgentCommand[] {
  const q = query.toLowerCase();
  const seen = new Set<string>();
  const unique = commands.filter((c) => c.name && !seen.has(c.name) && (seen.add(c.name), true));
  const starts = unique.filter((c) => c.name.toLowerCase().startsWith(q));
  const contains = q ? unique.filter((c) => !c.name.toLowerCase().startsWith(q) && c.name.toLowerCase().includes(q)) : [];
  return [...starts, ...contains].slice(0, SLASH_SHOWN);
}

/** Текст поля после выбора команды: с аргументами — с пробелом (дальше — аргументы). */
export function completion(c: AgentCommand): string {
  return `/${c.name}${c.hint ? " " : ""}`;
}

/**
 * Пункт подсказки: команда или аргумент. `text` — поле после выбора; `send` — Enter сразу
 * отправляет его (команда без аргументов, выбран сервер или модель), иначе дописывает.
 */
export type Suggestion = {
  key: string;
  /** Что видно слева (моноширинно): «/review», «team-jira», «sonnet». */
  name: string;
  hint?: string;
  /** Справа, мельче: описание, состояние сервера, название модели. */
  desc?: string;
  /** Пометка: «навык», «CLI», «ошибка»… */
  tag?: string;
  warn?: boolean;
  text: string;
  send: boolean;
};

/** Что нужно дополнению аргументов: серверы и модели от ассистента (`agent.mcp_servers`, `agent.models`). */
export type SlashContext = { commands: AgentCommand[]; servers?: AgentMcpServer[]; models?: AgentModel[] };

/** Состояние MCP-сервера словом (как у ассистента, `/mcp`). */
export const MCP_STATUS: Record<string, string> = {
  connected: "подключён", failed: "ошибка", "needs-auth": "нужен вход", pending: "ожидает", disabled: "выключен",
};
const MCP_ACTIONS: { name: string; desc: string }[] = [
  { name: "reconnect", desc: "переподключить сервер (без имени — все сбойные)" },
  { name: "enable", desc: "включить сервер до конца сессии" },
  { name: "disable", desc: "выключить сервер до конца сессии" },
];

const TAG: Record<AgentCommand["source"], string | undefined> = { meet: undefined, cli: "CLI", skill: "навык" };

const startsWith = (value: string, typed: string) => value.toLowerCase().startsWith(typed.toLowerCase());

/**
 * Подсказки для поля: «/» и имя — команды; `/mcp ` — действие, `/mcp reconnect|enable|disable ` —
 * серверы с состоянием (сбойные первыми у reconnect); `/model ` — модели. Иначе — пусто.
 */
export function suggestions(text: string, ctx: SlashContext): Suggestion[] {
  const query = slashQuery(text);
  if (query !== null) {
    return matchCommands(ctx.commands, query).map((c) => ({
      key: `${c.source}:${c.name}`, name: `/${c.name}`, hint: c.hint || undefined,
      desc: c.description || (c.source === "skill" ? "навык" : c.source === "cli" ? "команда CLI" : ""),
      tag: TAG[c.source], text: completion(c), send: !c.hint,
    }));
  }
  const m = /^\/(mcp|model)\s+(.*)$/s.exec(text);
  if (!m) return [];
  const rest = m[2]!;
  if (m[1] === "model") {
    if (/\s/.test(rest)) return [];
    return (ctx.models ?? []).filter((x) => startsWith(x.value, rest)).map((x) => ({
      key: `model:${x.value}`, name: x.value, desc: x.label || undefined, text: `/model ${x.value}`, send: true,
    }));
  }
  const words = rest.split(/\s+/);
  if (words.length === 1) {
    return MCP_ACTIONS.filter((a) => startsWith(a.name, words[0]!)).map((a) => ({
      key: `mcp:${a.name}`, name: a.name, desc: a.desc, text: `/mcp ${a.name} `, send: false,
    }));
  }
  const action = words[0]!.toLowerCase();
  if (words.length !== 2 || !MCP_ACTIONS.some((a) => a.name === action)) return [];
  const typed = words[1]!;
  let servers = (ctx.servers ?? []).filter((s) => startsWith(s.name, typed));
  if (action === "reconnect") servers = [...servers].sort((a, b) => Number(b.status === "failed") - Number(a.status === "failed"));
  if (action === "enable") servers = servers.filter((s) => s.status === "disabled");
  if (action === "disable") servers = servers.filter((s) => s.status !== "disabled");
  return servers.map((s) => ({
    key: `server:${s.name}`, name: s.name, desc: MCP_STATUS[s.status] ?? s.status,
    warn: s.status === "failed" || s.status === "needs-auth", text: `/mcp ${action} ${s.name}`, send: true,
  }));
}

/** Призрак аргументов: набрана команда с аргументами («/review ») — её `argumentHint`; иначе null. */
export function argHint(text: string, commands: AgentCommand[]): string | null {
  const m = /^\/([A-Za-z][\w:.-]*)\s/.exec(text);
  if (!m) return null;
  const c = commands.find((x) => x.name === m[1]);
  return c?.hint ? `/${c.name} ${c.hint}` : null;
}
