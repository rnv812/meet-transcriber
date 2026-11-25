/**
 * Уровень рассуждений модели (0.5): Claude Code — `llm.effort` (`--effort`), Codex —
 * `llm.codex_effort` (`model_reasoning_effort`), Antigravity CLI — `llm.agy_effort`
 * (0.6.2, опция сеанса `thought_level`); "" — по умолчанию CLI. У Claude Code
 * уровни постоянные (`settings.CLAUDE_EFFORTS`); у Codex и agy (0.6.2) — свои у
 * каждой модели, из самого CLI (`GET /assistant/models`), а здесь — запасной
 * список на случай, когда CLI не ответил.
 */

export const CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
/** Запасной список Codex (CLI не ответил): как `settings.CODEX_EFFORTS`. */
export const CODEX_EFFORTS = ["minimal", "low", "medium", "high", "xhigh"] as const;
/** Запасной список Antigravity CLI: `agy --effort` (1.5.11). */
export const AGY_EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;

export const EFFORT_LABELS: Record<string, string> = {
  none: "без рассуждений", minimal: "минимальный", low: "низкий", medium: "средний", high: "высокий",
  xhigh: "очень высокий", max: "максимальный", ultra: "ультра",
};

/** «По умолчанию» в списке: у Select пустое значение — «не выбрано», поэтому своё слово. */
export const EFFORT_DEFAULT = "default";

/** Подпись уровня: русское слово, иначе подпись CLI, иначе само имя. */
export const effortName = (id: string, label?: string) => EFFORT_LABELS[id] ?? (label || id);

/** Пункты списка: «по умолчанию» (`detail` — что это значит сейчас) и уровни. */
export const effortOptions = (levels: readonly (string | { id: string; label?: string })[], detail = "как настроено в самом CLI") => [
  { value: EFFORT_DEFAULT, label: "По умолчанию", detail },
  ...levels.map((v) => (typeof v === "string" ? { value: v, label: effortName(v) } : { value: v.id, label: effortName(v.id, v.label) })),
];

/** Подпись уровня для шапки: «высокий»; нет — пусто. */
export const effortText = (value: unknown) => (typeof value === "string" && EFFORT_LABELS[value]) || "";
