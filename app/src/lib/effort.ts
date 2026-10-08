/**
 * Уровень рассуждений модели (0.5): Claude Code — `llm.effort` (`--effort`), Codex —
 * `llm.codex_effort` (`model_reasoning_effort`); "" — по умолчанию CLI. Значения —
 * те же, что принимает резидент (`settings.CLAUDE_EFFORTS`, `CODEX_EFFORTS`).
 */

export const CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
export const CODEX_EFFORTS = ["minimal", "low", "medium", "high", "xhigh"] as const;

export const EFFORT_LABELS: Record<string, string> = {
  minimal: "минимальный", low: "низкий", medium: "средний", high: "высокий", xhigh: "очень высокий",
  max: "максимальный",
};

/** «По умолчанию» в списке: у Select пустое значение — «не выбрано», поэтому своё слово. */
export const EFFORT_DEFAULT = "default";

/** Пункты списка: «по умолчанию» и уровни. */
export const effortOptions = (levels: readonly string[]) => [
  { value: EFFORT_DEFAULT, label: "По умолчанию", detail: "как настроено в самом CLI" },
  ...levels.map((v) => ({ value: v, label: EFFORT_LABELS[v] ?? v })),
];

/** Подпись уровня для шапки: «высокий»; нет — пусто. */
export const effortText = (value: unknown) => (typeof value === "string" && EFFORT_LABELS[value]) || "";
