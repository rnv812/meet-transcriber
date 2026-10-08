/** Выбор модели у действий карточки и подписи «какая модель это сделала» (0.3.4). */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnalysisStatus } from "./analysis";
import { CardActions } from "./CardActions";
import { ImproveStatus } from "./improve";
import { SummaryTab } from "./SummaryTab";
import * as api from "../../lib/api";
import type { AssistantInfo, ModelChoice } from "../../lib/types";
import { AiBadge } from "../../ui/AiBadge";

vi.mock("../../lib/shell", async (orig) => ({ ...(await orig<typeof import("../../lib/shell")>()), inTauri: () => true }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  getSummary: vi.fn(),
  getLiveDraft: vi.fn(),
  makeSummary: vi.fn(),
}));

const ep = { base: "/api", token: null };

const LOCAL: ModelChoice = {
  provider: "openai-compatible", model: "qwen3", label: "Локальная модель (qwen3)", default: true, local: true,
  available: true, reason: null,
};
const CLAUDE: ModelChoice = {
  provider: "claude-code", model: "sonnet", label: "Claude Code (sonnet)", default: false, local: false,
  available: true, reason: null,
};
const CODEX: ModelChoice = {
  provider: "codex", model: null, label: "Codex", default: false, local: false, available: false,
  reason: "не найден Codex CLI (codex)",
};
const MODELS = [CLAUDE, CODEX, LOCAL];

function actions(more: Partial<Parameters<typeof CardActions>[0]> = {}) {
  const props = {
    canExport: true, canRetranscribe: true, busy: false,
    onExport: vi.fn(), onKbExport: vi.fn(), onOpenFolder: vi.fn(), onRetranscribe: vi.fn(),
    onRediarize: vi.fn(), onDelete: vi.fn(), onReanalyze: vi.fn(), onSuggestTitle: vi.fn(), onImprove: vi.fn(),
    ...more,
  };
  render(<CardActions {...props} />);
  return props;
}

const openMore = () => userEvent.click(screen.getByRole("button", { name: "Ещё действия" }));

test("одна модель — у действий нет стрелки выбора", async () => {
  actions({ models: [LOCAL] });
  await openMore();
  expect(screen.queryByRole("menuitem", { name: /выбрать модель/ })).toBeNull();
});

test("основное нажатие — модель по умолчанию", async () => {
  const props = actions({ models: MODELS, reanalyzeLabel: "Анализировать" });
  await openMore();
  await userEvent.click(screen.getByRole("menuitem", { name: "Анализировать" }));
  expect(props.onReanalyze).toHaveBeenCalledWith();
});

test("стрелка — включённые модели: имя, куда уходит текст, недоступная с причиной", async () => {
  const props = actions({ models: MODELS });
  await openMore();
  await userEvent.click(screen.getByRole("menuitem", { name: "Улучшить расшифровку: выбрать модель" }));
  const menu = screen.getByRole("menu", { name: "Какой моделью" });
  expect(within(menu).getByText("«Улучшить расшифровку» — какой моделью")).toBeInTheDocument();
  const claude = within(menu).getByRole("menuitem", { name: /Claude Code \(sonnet\)/ });
  expect(claude).toHaveTextContent("облачная — текст встречи уходит провайдеру");
  const local = within(menu).getByRole("menuitem", { name: /Локальная модель \(qwen3\) — по умолчанию/ });
  expect(local).toHaveTextContent("локальная — данные не покидают компьютер");
  const codex = within(menu).getByRole("menuitem", { name: /Codex/ });
  // Недоступная видна диктору и клавиатуре (aria-disabled), но не выбирается.
  expect(codex).toHaveAttribute("aria-disabled", "true");
  expect(codex).not.toBeDisabled();
  expect(codex).toHaveTextContent("Недоступна: не найден Codex CLI (codex)");
  await userEvent.click(codex);
  expect(props.onImprove).not.toHaveBeenCalled();
  await userEvent.click(claude);
  expect(props.onImprove).toHaveBeenCalledWith("claude-code");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("«Переанализировать…» другой моделью — после подтверждения и только ею", async () => {
  const props = actions({ models: MODELS });
  await openMore();
  await userEvent.click(screen.getByRole("menuitem", { name: "Переанализировать: выбрать модель" }));
  await userEvent.click(screen.getByRole("menuitem", { name: /Claude Code/ }));
  expect(props.onReanalyze).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Переанализировать" }));
  expect(props.onReanalyze).toHaveBeenCalledWith("claude-code");
});

test("«Предложить название» выбранной моделью; «Назад» возвращает к действиям", async () => {
  const props = actions({ models: MODELS });
  await openMore();
  await userEvent.click(screen.getByRole("menuitem", { name: "Предложить название: выбрать модель" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Назад" }));
  expect(screen.getByRole("menu", { name: "Ещё действия с записью" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("menuitem", { name: "Предложить название: выбрать модель" }));
  await userEvent.click(screen.getByRole("menuitem", { name: /Локальная/ }));
  expect(props.onSuggestTitle).toHaveBeenCalledWith("openai-compatible");
});

// --- «Итоги» ---------------------------------------------------------------------

const info = (models?: ModelChoice[]): AssistantInfo => ({
  provider: "openai-compatible", setting: "openai-compatible", available: {}, knowledge_dir: null, checking: false,
  models,
});

test("«Сделать итоги»: основное — по умолчанию, стрелка — выбранной моделью", async () => {
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.makeSummary).mockResolvedValue({
    id: "s1", kind: "summary", folder: "C:/rec/r1", state: "queued", stage: null, label: null, done: null,
    total: null, note: null, result: null, error: null, provider: "claude-code",
  });
  render(<SummaryTab endpoint={ep} id="r1" folder="C:\\rec\\r1" jobs={[]} assistant={info(MODELS)} />);
  await userEvent.click(await screen.findByRole("button", { name: "Сделать итоги: выбрать модель" }));
  await userEvent.click(screen.getByRole("menuitem", { name: /Claude Code/ }));
  expect(api.makeSummary).toHaveBeenCalledWith(ep, "r1", "claude-code");
});

test("готовые итоги подписаны моделью", async () => {
  vi.mocked(api.getSummary).mockResolvedValue({
    markdown: "# Итоги", created_at: 1000, llm: { provider: "claude-code", model: "sonnet" },
  });
  render(<SummaryTab endpoint={ep} id="r1" folder="C:\\rec\\r1" jobs={[]} assistant={info([LOCAL])} />);
  expect(await screen.findByText(/^Итоги собрал Claude Code \(sonnet\) · /)).toBeInTheDocument();
  // Включена одна модель — обычная кнопка, без стрелки.
  expect(screen.queryByRole("button", { name: /выбрать модель/ })).toBeNull();
});

// --- подписи --------------------------------------------------------------------------

test("анализ подписан моделью и днём; прежний — по подписи model", () => {
  const at = new Date(2026, 9, 6, 10, 0).getTime() / 1000;
  const doc = { version: 1 as const, model: "claude-code:sonnet", created_at: at, fingerprint: "x", features: [] };
  const { rerender } = render(<AnalysisStatus state={{ state: "ready", analysis: doc }} busy={false} />);
  expect(screen.getByText("Анализ: Claude Code (sonnet) · 06.10")).toBeInTheDocument();
  rerender(<AnalysisStatus state={{ state: "ready", analysis: { ...doc, llm: { provider: "openai-compatible", model: "qwen3" } } }}
    busy={false} />);
  expect(screen.getByText("Анализ: Локальная модель (qwen3) · 06.10")).toBeInTheDocument();
});

test("предложение улучшения подписано моделью", () => {
  render(<ImproveStatus busy={false} onOpen={vi.fn()} onDismiss={vi.fn()} state={{
    state: "ready", proposal: {
      version: 1, model: "codex", llm: { provider: "codex", model: null }, created_at: 1, fingerprint: "x",
      segments: 1, groups: [{ id: "g1", find: "апи", replace: "API", kind: "term", confidence: 0.9, count: 1, samples: [] }],
    },
  }} />);
  expect(screen.getByText(/· Codex/)).toBeInTheDocument();
});

test("бейдж «ИИ» называет модель, придумавшую название", () => {
  render(<AiBadge by="Claude Code (sonnet)" />);
  expect(screen.getByText("Название предложено ИИ (Claude Code (sonnet)) — нажмите, чтобы изменить")).toBeInTheDocument();
});

// --- fix round 1 ---------------------------------------------------------------------

const failedSummary = {
  id: "s1", kind: "summary", folder: "C:/rec/r1", state: "failed" as const, stage: null, label: null, done: null,
  total: null, note: null, result: null, error: "локальная модель не отвечает", provider: "openai-compatible",
};

test("«Повторить» итогов после сбоя выбранной модели — ею же, и это видно на кнопке", async () => {
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.makeSummary).mockResolvedValue({ ...failedSummary, state: "queued" });
  const claudeDefault = info([{ ...CLAUDE, default: true }, { ...LOCAL, default: false }]);
  render(<SummaryTab endpoint={ep} id="r1" folder="C:\rec\r1" jobs={[failedSummary]} assistant={claudeDefault} />);
  await userEvent.click(await screen.findByRole("button", { name: "Повторить (Локальная модель)" }));
  expect(api.makeSummary).toHaveBeenCalledWith(ep, "r1", "openai-compatible");
});

test("модель по умолчанию недоступна: основное нажатие заперто с причиной, стрелка — нет", async () => {
  vi.mocked(api.getLiveDraft).mockResolvedValue(null);
  vi.mocked(api.getSummary).mockRejectedValue(new api.ApiError(404, "итогов нет"));
  vi.mocked(api.makeSummary).mockResolvedValue({ ...failedSummary, state: "queued", provider: "claude-code" });
  const down = { ...info([CLAUDE, { ...LOCAL, available: false, reason: "локальная модель не отвечает" }]), provider: null };
  render(<SummaryTab endpoint={ep} id="r1" folder="C:\rec\r1" jobs={[]} assistant={down} />);
  const main = await screen.findByRole("button", { name: "Сделать итоги" });
  expect(main).toBeDisabled();
  expect(main).toHaveAccessibleDescription(/Модель по умолчанию \(Локальная модель \(qwen3\)\) недоступна/);
  expect(screen.getByText(/выберите другую модель стрелкой/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Сделать итоги: выбрать модель" }));
  await userEvent.click(screen.getByRole("menuitem", { name: /Claude Code/ }));
  expect(api.makeSummary).toHaveBeenCalledWith(ep, "r1", "claude-code");
});

test("меню карточки: недоступная модель по умолчанию запирает только основное нажатие", async () => {
  const props = actions({
    models: [CLAUDE, { ...LOCAL, available: false, reason: "не отвечает" }],
    improveBlocked: "Модель по умолчанию недоступна", reanalyzeBlocked: "Модель по умолчанию недоступна",
    reanalyzeLabel: "Анализировать",
  });
  await openMore();
  expect(screen.getByRole("menuitem", { name: "Улучшить расшифровку" })).toBeDisabled();
  expect(screen.getByRole("menuitem", { name: "Анализировать" })).toBeDisabled();
  await userEvent.click(screen.getByRole("menuitem", { name: "Анализировать: выбрать модель" }));
  await userEvent.click(screen.getByRole("menuitem", { name: /Claude Code/ }));
  expect(props.onReanalyze).toHaveBeenCalledWith("claude-code");
});

test("анализ уже в очереди — стрелка тоже заперта", async () => {
  actions({ models: MODELS, reanalyzeBlocked: "Анализ уже в очереди", reanalyzePickBlocked: "Анализ уже в очереди" });
  await openMore();
  expect(screen.getByRole("menuitem", { name: "Переанализировать: выбрать модель" })).toBeDisabled();
});

test("«Повторить» анализа и улучшения после сбоя — моделью упавшей задачи", async () => {
  const onRun = vi.fn();
  render(<AnalysisStatus state={{ state: "failed", error: "нет", provider: "claude-code" }} busy={false} onRun={onRun} />);
  await userEvent.click(screen.getByRole("button", { name: "Повторить (Claude Code)" }));
  expect(onRun).toHaveBeenCalledWith("claude-code");
  const onRetry = vi.fn();
  render(<ImproveStatus busy={false} onOpen={vi.fn()} onDismiss={vi.fn()} onRetry={onRetry}
    state={{ state: "failed", error: "нет", provider: "openai-compatible" }} />);
  await userEvent.click(screen.getByRole("button", { name: "Повторить (Локальная модель)" }));
  expect(onRetry).toHaveBeenCalledWith("openai-compatible");
});

test("устаревший анализ показывает, какая модель его делала", () => {
  const doc = { version: 1 as const, model: "codex", created_at: 1, fingerprint: "x", features: [] };
  render(<AnalysisStatus state={{ state: "stale", analysis: doc }} busy={false} />);
  expect(screen.getByText(/Анализ устарел/)).toHaveAccessibleDescription(/Анализ: Codex/);
});
