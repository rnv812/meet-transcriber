import { daysBefore, parseLocal } from "./dateSections";

const pad = (n: number) => String(n).padStart(2, "0");

export function clock(s: number): string {
  const t = Math.max(0, Math.floor(s));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

export function duration(s: number): string {
  const mins = Math.round(Math.max(0, s) / 60);
  const h = Math.floor(mins / 60);
  return h > 0 ? `${h} ч ${pad(mins % 60)} мин` : `${mins} мин`;
}

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

/** ISO-время со сдвигом зоны («…Z», «…+03:00»); иначе и при ошибке — null. */
function parseZoned(iso: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}T.*(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(iso)) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * «Сегодня 10:00», «Вчера 10:00», «5 сен 10:00». `short` — строка под разделом
 * по дате (lib/dateSections): в разделах по дням (до 6 дней назад) — только
 * время, старше — «5 сен, 10:00» (месяц или год уже в заголовке раздела).
 */
export function dayLabel(iso: string, now: Date = new Date(), short = false): string {
  // Время без зоны (started_at, голая дата) — местное, как в разделах; ISO с зоной
  // (время ответа ассистента) — как есть. Не дата — пусто, а не «NaN undefined».
  const d = parseLocal(iso) ?? parseZoned(iso);
  if (!d) return "";
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const diff = daysBefore(d, now);
  if (short) return diff <= 6 ? time : `${d.getDate()} ${MONTHS[d.getMonth()]}, ${time}`;
  if (diff === 0) return `Сегодня ${time}`;
  if (diff === 1) return `Вчера ${time}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${time}`;
}

/**
 * Когда кончилась встреча: начало (`started_at`, местное без зоны) плюс
 * длительность. Нет одного из них или не дата — null.
 */
export function meetingEndOf(startedAt: string | null | undefined, durationS: number | null | undefined): Date | null {
  if (!startedAt || durationS == null || !Number.isFinite(durationS)) return null;
  const start = parseLocal(startedAt) ?? parseZoned(startedAt);
  return start ? new Date(start.getTime() + durationS * 1000) : null;
}

/** «2 записи», «5 записей», «21 запись»: форма слова по числу. */
export function plural(n: number, one: string, few: string, many: string): string {
  const d = Math.abs(n) % 100;
  const u = d % 10;
  if (d >= 11 && d <= 14) return many;
  if (u === 1) return one;
  if (u >= 2 && u <= 4) return few;
  return many;
}

/** Текст ошибки для человека: у Error — сообщение без «Error: », иначе — как есть. */
export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
