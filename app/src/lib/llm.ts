/**
 * Несколько моделей сразу (0.3.4): подписи «какая модель это сделала» и выбор
 * модели у действий карточки.
 *
 * Резидент отдаёт происхождение результата `{provider, model}` (`llm` у анализа,
 * итогов, улучшения и названия «ИИ»); у анализа и улучшения прежних версий
 * есть только подпись `model` вида «claude-code:sonnet» — её разбирает
 * `originOf`. Включённые модели для выбора — `GET /assistant` → `models`.
 */

import type { AssistantInfo, LlmOrigin, ModelChoice } from "./types";

export const PROVIDER_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  opencode: "OpenCode",
  "openai-compatible": "Локальная модель",
};

/** Подсказки о том, куда уходит текст встречи. */
export const PRIVACY_LOCAL = "локальная — данные не покидают компьютер";
export const PRIVACY_CLOUD = "облачная — текст встречи уходит провайдеру";

export const privacyOf = (local: boolean): string => (local ? PRIVACY_LOCAL : PRIVACY_CLOUD);

/** «Claude Code (sonnet)», «Codex»; неизвестно — null. */
export function llmLabel(origin: LlmOrigin | null | undefined): string | null {
  if (!origin?.provider) return null;
  const name = PROVIDER_LABELS[origin.provider] ?? origin.provider;
  return origin.model ? `${name} (${origin.model})` : name;
}

/**
 * Происхождение документа: `llm`, а у прежних — подпись `model`
 * («claude-code:sonnet», «codex», «openai-compatible:default»).
 */
export function originOf(doc: { llm?: LlmOrigin | null; model?: string } | null | undefined): LlmOrigin | null {
  if (!doc) return null;
  if (doc.llm?.provider) return doc.llm;
  const raw = doc.model ?? "";
  const cut = raw.indexOf(":");
  const provider = cut < 0 ? raw : raw.slice(0, cut);
  if (!(provider in PROVIDER_LABELS)) return null;
  const model = cut < 0 ? null : raw.slice(cut + 1);
  return { provider, model: model && model !== "default" ? model : null };
}

const pad = (n: number) => String(n).padStart(2, "0");

/** «Анализ: Claude Code (sonnet) · 06.10»; модель неизвестна — null. `at` — секунды эпохи. */
export function provenance(what: string, origin: LlmOrigin | null | undefined, at?: number | null): string | null {
  const label = llmLabel(origin);
  if (!label) return null;
  if (typeof at !== "number" || !Number.isFinite(at) || at <= 0) return `${what}: ${label}`;
  const d = new Date(at * 1000);
  return `${what}: ${label} · ${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
}

/** Включённые модели для выбора; у старого резидента — ни одной. */
export const modelChoices = (info: AssistantInfo | null | undefined): ModelChoice[] => info?.models ?? [];

/** Выбирать есть из чего: включено больше одной модели. */
export const canChooseModel = (info: AssistantInfo | null | undefined): boolean => modelChoices(info).length > 1;

/** Модель `provider` включена и найдена на машине. */
export const modelReady = (choices: ModelChoice[], provider: string): boolean =>
  choices.some((c) => c.provider === provider && c.available);

/** Хоть одну включённую модель можно выбрать (стрелка выбора не бесполезна). */
export const anyModelReady = (choices: ModelChoice[]): boolean => choices.some((c) => c.available);

/** «Повторить» после сбоя: с моделью упавшей задачи, если её выбирали. */
export const retryText = (provider?: string | null): string =>
  (provider ? `Повторить (${PROVIDER_LABELS[provider] ?? provider})` : "Повторить");

/** Пункт выбора модели: имя, «по умолчанию», куда уходит текст или почему недоступна. */
export function choiceHint(choice: ModelChoice): string {
  if (!choice.available) return `Недоступна: ${choice.reason ?? "не найдена"}`;
  return `${choice.default ? "По умолчанию · " : ""}${privacyOf(choice.local)}`;
}
