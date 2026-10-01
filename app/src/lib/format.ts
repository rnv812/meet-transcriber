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

export function dayLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86_400_000);
  if (diff === 0) return `Сегодня ${time}`;
  if (diff === 1) return `Вчера ${time}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${time}`;
}

/** Текст ошибки для человека: у Error — сообщение без «Error: », иначе — как есть. */
/** «2 записи», «5 записей», «21 запись»: форма слова по числу. */
export function plural(n: number, one: string, few: string, many: string): string {
  const d = Math.abs(n) % 100;
  const u = d % 10;
  if (d >= 11 && d <= 14) return many;
  if (u === 1) return one;
  if (u >= 2 && u <= 4) return few;
  return many;
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
