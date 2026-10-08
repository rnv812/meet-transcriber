/** Тоны бейджей окна → варианты Atlas Aurora; цвет всегда вместе со словом. */
export type BadgeTone = "ok" | "run" | "err" | "temp" | "plain";

export const BADGE_CLASS: Record<BadgeTone, string> = {
  ok: "badge badge--fresh",
  run: "badge badge--info",
  err: "badge badge--error",
  temp: "badge badge--stale",
  plain: "badge badge--plain",
};
