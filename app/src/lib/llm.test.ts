import { canChooseModel, choiceHint, llmLabel, originOf, provenance, PRIVACY_CLOUD, PRIVACY_LOCAL } from "./llm";
import type { AssistantInfo, ModelChoice } from "./types";

test("подпись модели: имя и модель из настроек", () => {
  expect(llmLabel({ provider: "claude-code", model: "sonnet" })).toBe("Claude Code (sonnet)");
  expect(llmLabel({ provider: "codex", model: null })).toBe("Codex");
  expect(llmLabel({ provider: "openai-compatible", model: "qwen3" })).toBe("Локальная модель (qwen3)");
  expect(llmLabel(null)).toBeNull();
});

test("происхождение прежнего анализа — из подписи model", () => {
  expect(originOf({ model: "claude-code:sonnet" })).toEqual({ provider: "claude-code", model: "sonnet" });
  expect(originOf({ model: "codex" })).toEqual({ provider: "codex", model: null });
  expect(originOf({ model: "openai-compatible:default" })).toEqual({ provider: "openai-compatible", model: null });
  expect(originOf({ model: "opencode:anthropic/claude-sonnet-4-5" }))
    .toEqual({ provider: "opencode", model: "anthropic/claude-sonnet-4-5" });
  expect(originOf({ model: "fake" })).toBeNull();
  // Новое поле важнее подписи.
  expect(originOf({ model: "codex", llm: { provider: "claude-code", model: "opus" } }))
    .toEqual({ provider: "claude-code", model: "opus" });
});

test("строка происхождения: что, какая модель и день", () => {
  const at = new Date(2026, 9, 6, 12, 0).getTime() / 1000;
  expect(provenance("Анализ", { provider: "claude-code", model: "sonnet" }, at)).toBe("Анализ: Claude Code (sonnet) · 06.10");
  expect(provenance("Итоги", { provider: "codex", model: null })).toBe("Итоги: Codex");
  expect(provenance("Анализ", null, at)).toBeNull();
});

const choice = (over: Partial<ModelChoice>): ModelChoice => ({
  provider: "claude-code", model: "sonnet", label: "Claude Code (sonnet)", default: false, local: false,
  available: true, reason: null, ...over,
});

test("подсказка пункта: куда уходит текст, по умолчанию, почему недоступна", () => {
  expect(choiceHint(choice({}))).toBe(PRIVACY_CLOUD);
  expect(choiceHint(choice({ local: true, default: true }))).toBe(`По умолчанию · ${PRIVACY_LOCAL}`);
  expect(choiceHint(choice({ available: false, reason: "не найден Codex CLI (codex)" })))
    .toBe("Недоступна: не найден Codex CLI (codex)");
});

test("выбирать есть из чего — только при двух включённых и больше", () => {
  const info = (models?: ModelChoice[]) => ({ models } as unknown as AssistantInfo);
  expect(canChooseModel(null)).toBe(false);
  expect(canChooseModel(info(undefined))).toBe(false);
  expect(canChooseModel(info([choice({})]))).toBe(false);
  expect(canChooseModel(info([choice({}), choice({ provider: "codex" })]))).toBe(true);
});
