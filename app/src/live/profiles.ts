/**
 * Профили сессии ассистента (`assist.profile`, 0.3.7): подписи и пояснения —
 * одни для меню старта, шапки сессии, настроек и вкладки «Ассистент».
 *
 * - «Рабочая встреча» (`work`) — база знаний и прошлые встречи, подсказки по
 *   делу, закреплённые вопросы к пользователю;
 * - «Личный» (`personal`) — созвон, стрим, видео, подкаст: без базы знаний и
 *   рабочей рамки; ассистент определяет, что это, отвечает и даёт краткое содержание.
 */

import type { AgentProfile } from "../lib/types";

export const PROFILES: AgentProfile[] = ["work", "personal"];
export const DEFAULT_PROFILE: AgentProfile = "work";

export const PROFILE_LABELS: Record<AgentProfile, string> = {
  work: "Рабочая встреча",
  personal: "Личный",
};

/** Коротко, что меняет профиль (заметка в меню, подсказка чипа). */
export const PROFILE_NOTES: Record<AgentProfile, string> = {
  work: "база знаний и прошлые встречи, подсказки по делу",
  personal: "созвон, стрим, видео: ответы и краткое содержание, без базы знаний",
};

/** Быстрые вопросы живой панели в профиле «Личный» (без «Что ответить?» — это не встреча). */
export const PERSONAL_LIVE_QUESTIONS = ["Что я пропустил?", "О чём это?", "Краткое содержание"];

/** Профиль из ответа резидента; неизвестное или нет поля (старый резидент) — «Рабочая встреча». */
export function profileOf(value: unknown): AgentProfile {
  // `neutral` — прежнее имя «Личного» до выпуска (старый резидент).
  return value === "personal" || value === "neutral" ? "personal" : DEFAULT_PROFILE;
}

export const profileLabel = (value: unknown): string => PROFILE_LABELS[profileOf(value)];
