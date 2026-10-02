import { fastMeaning } from "./LiveHintsRows";

test("«Быстрее» у каждого провайдера своё", () => {
  expect(fastMeaning("claude-code")).toBe("Claude Code: модель Haiku");
  expect(fastMeaning("codex")).toBe("Codex: низкое усилие рассуждения");
  expect(fastMeaning("openai-compatible")).toMatch(/без изменений/);
  expect(fastMeaning(null)).toMatch(/Haiku.*Codex/);
});
