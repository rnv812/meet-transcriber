import { noModelText } from "./assistant";
import type { AssistantInfo, ModelChoice } from "../../lib/types";

const model = (o: Partial<ModelChoice>): ModelChoice => ({
  provider: "claude-code", model: "sonnet", label: "Claude Code (sonnet)", default: false, local: false,
  available: true, reason: null, ...o,
});
const info = (o: Partial<AssistantInfo>): AssistantInfo => ({
  provider: null, setting: "auto", available: {}, knowledge_dir: null, checking: false, ...o,
});

test("без списка моделей (старый резидент) — прежний текст", () => {
  expect(noModelText(info({}))).toBe("Подключите Claude Code, Codex или OpenCode в настройках");
});

test("«Авто» без готовой модели и недоступная модель по умолчанию — свой текст", () => {
  expect(noModelText(info({ models: [model({ available: false })] })))
    .toBe("«Авто» не нашло готовой модели среди включённых — подключите её в настройках");
  expect(noModelText(info({ setting: "openai-compatible", models: [
    model({}), model({ provider: "openai-compatible", label: "Локальная модель", available: false })] })))
    .toBe("Модель по умолчанию (Локальная модель) недоступна — проверьте её в настройках или выберите другую модель стрелкой у действия");
});
