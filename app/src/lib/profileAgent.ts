/**
 * Профиль человека — во вкладку «Агент»: ✦ «Подготовиться к разговору» (профиль,
 * главные советы и заготовка просьбы) и ✦ «Обсудить с агентом» (профиль со
 * ссылками на реплики).
 *
 * Текст уходит в поле ввода агента той же вставкой, что и ссылки на реплики
 * (lib/agentRef, AgentTab): одна строка без управляющих символов и символов
 * смены направления письма, не длиннее PROFILE_TEXT_MAX — Enter он не нажимает.
 */

import { cleanRefText } from "./agentRef";
import { clock } from "./format";
import { pcmLabel } from "./pcm";
import type { Profile, ProfileRef, ProfileSectionKey } from "./types";

/** Длина вставки целиком (символов). */
export const PROFILE_TEXT_MAX = 2000;
/** Сколько утверждений раздела брать в «Подготовиться к разговору». */
const PER_SECTION = 3;

export const SECTION_TITLES: Record<ProfileSectionKey, string> = {
  style: "Стиль общения",
  values: "Что для человека важно",
  how_to_talk: "Как лучше строить разговор",
  avoid: "Чего избегать",
  topics: "Типичные темы",
};
export const SECTION_ORDER: ProfileSectionKey[] = ["style", "values", "how_to_talk", "avoid", "topics"];

/** Обрезка по кодовым точкам (эмодзи на границе не режется пополам). */
function clip(text: string, max: number): string {
  const chars = Array.from(text);
  return chars.length <= max ? text : `${chars.slice(0, max - 1).join("").trimEnd()}…`;
}

const sentence = (text: string) => {
  const t = cleanRefText(text);
  return t && !/[.?!…]$/.test(t) ? `${t}.` : t;
};

/** «Планирование · 05:31» — подпись ссылки (и для чипа в окне). */
export function refLabel(profile: Profile, ref: ProfileRef, titleMax = 28): string {
  const title = cleanRefText(profile.sources?.[ref.m]?.title ?? ref.m);
  return `${clip(title, titleMax)} · ${clock(ref.t ?? 0)}`;
}

function items(profile: Profile, key: ProfileSectionKey, n = PER_SECTION): string[] {
  return (profile.sections[key] ?? []).slice(0, n).map((s) => sentence(s.text)).filter(Boolean);
}

function part(title: string, list: string[]): string {
  return list.length ? `${title}: ${list.join(" ")}` : "";
}

/**
 * Одна строка, без управляющих символов, не длиннее PROFILE_TEXT_MAX: длинный
 * профиль обрезается посередине, начало и просьба в конце остаются целыми.
 */
function finish(head: string, body: string[], tail = ""): string {
  const h = cleanRefText(head);
  const t = cleanRefText(tail);
  const room = Math.max(0, PROFILE_TEXT_MAX - Array.from(h).length - Array.from(t).length - 2);
  const b = clip(cleanRefText(body.filter(Boolean).join(" ")), room);
  return [h, b, t].filter(Boolean).join(" ");
}

/** Гипотеза PCM одной фразой: база, фаза, канал, как давать признание. */
function pcmPart(profile: Profile): string {
  const pcm = profile.pcm;
  if (!pcm) return "";
  const bits = [`база — ${pcmLabel(pcm.base.type)}`];
  if (pcm.phase && pcm.phase.type !== pcm.base.type) bits.push(`фаза — ${pcmLabel(pcm.phase.type)}`);
  if (pcm.channel) bits.push(`канал общения — ${pcm.channel.value}`);
  const recognize = pcm.needs?.how_to_recognize || pcm.needs?.value;
  return `Гипотеза по модели PCM (не оценка): ${bits.join(", ")}.${recognize ? ` Как давать признание: ${sentence(recognize)}` : ""}`;
}

/**
 * Есть ли у «Коротко» своя опора: хоть одна ссылка на реплику, которая не
 * изменилась. Без неё «Коротко» не показывается и агенту не уходит.
 */
export function grounded(profile: Profile): boolean {
  return (profile.summary_refs ?? []).some((r) => !r.stale);
}

const summaryPart = (profile: Profile) =>
  (profile.summary && grounded(profile) ? `Коротко: ${sentence(profile.summary)}` : "");

/**
 * ✦ «Подготовиться к разговору»: профиль коротко, главные советы и заготовка
 * просьбы; кончается «Тема разговора:» — человек дописывает тему сам.
 * `shared` — текст уходит в общую с человеком встречу (иначе — в последнюю,
 * и просьба не ссылается на «материалы этой встречи»).
 */
export function prepareText(name: string, profile: Profile, withPcm = true, shared = true): string {
  const who = clip(cleanRefText(name), 60);
  return finish(
    `Помоги подготовиться к разговору с человеком «${who}». Его профиль общения (гипотеза по репликам во встречах, `
      + "не оценка личности):",
    [
      summaryPart(profile),
      part("Как лучше строить разговор", items(profile, "how_to_talk")),
      part("Что для человека важно", items(profile, "values")),
      part("Чего избегать", items(profile, "avoid")),
      withPcm ? pcmPart(profile) : "",
    ],
    "Предложи план разговора: как начать, как аргументировать, как попросить о решении и как дать обратную связь"
      + (shared ? "; учитывай материалы этой встречи." : ".") + " Тема разговора:",
  );
}

/** ✦ «Обсудить с агентом»: профиль целиком со ссылками на реплики. */
export function discussText(name: string, profile: Profile, withPcm = true): string {
  const who = clip(cleanRefText(name), 60);
  const sections = SECTION_ORDER.flatMap((key) => {
    const list = (profile.sections[key] ?? []).map((s) => {
      const refs = s.refs.filter((r) => !r.stale).slice(0, 2).map((r) => refLabel(profile, r, 24)).join("; ");
      return `${sentence(s.text)}${refs ? ` [${refs}]` : ""}`;
    });
    return list.length ? [`${SECTION_TITLES[key]}: ${list.join(" ")}`] : [];
  });
  return finish(
    `Про профиль общения человека «${who}» (по ${profile.meetings} встречам; гипотеза по репликам, не оценка личности):`,
    [summaryPart(profile), ...sections, withPcm ? pcmPart(profile) : ""],
  );
}
