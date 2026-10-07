import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { LiveSummary } from "../lib/types";
import { agentInfo } from "../test/chatFixtures";
import { EMPTY_SUMMARY } from "./liveModel";
import { DENY_NOTE, NO_VISION, SessionBar, seesText } from "./SessionBar";

const summary: LiveSummary = { ...EMPTY_SUMMARY, topic: "Запуск биллинга", decisions: [{ id: "d1", text: "Стенд к пятнице" }] };
const bar = () => screen.getByRole("group", { name: "Сессия ассистента" });
const stateEl = () => bar().querySelector(".session-bar__state")!;

test("модель, что видит, состояние", () => {
  render(<SessionBar agent={agentInfo({ sees: { conversation: true, kb: true, materials: 3, images: 0 } })} summary={summary}
    onFrequency={() => {}} />);
  expect(bar()).toHaveTextContent("Claude Code (sonnet)");
  expect(bar()).toHaveTextContent("видит: разговор, структура базы знаний, 3 материала");
  expect(stateEl()).toHaveTextContent("слушает");
  expect(bar()).not.toHaveTextContent(NO_VISION);
  expect(bar()).not.toHaveTextContent(DENY_NOTE);
});

test("пометки: модель без зрения и Codex — исключённые папки только просьбой", () => {
  render(<SessionBar agent={agentInfo({ provider: "codex", label: "Codex", vision: true, deny_enforced: false })}
    summary={summary} onFrequency={() => {}} />);
  expect(bar()).toHaveTextContent("Исключённые папки — только просьба");
  render(<SessionBar agent={agentInfo({ provider: "opencode", label: "OpenCode", vision: false })} summary={summary}
    onFrequency={() => {}} />);
  expect(screen.getAllByRole("group", { name: "Сессия ассистента" })[1]).toHaveTextContent("Модель не видит изображения");
});

test("состояние: молчаливый ход — «думает», видимый ответ — «пишет», ошибка — с текстом в подсказке", () => {
  const { rerender } = render(<SessionBar agent={agentInfo({ state: "writing" })} summary={summary} onFrequency={() => {}} />);
  expect(stateEl()).toHaveTextContent("думает…");
  rerender(<SessionBar agent={agentInfo({ state: "writing" })} summary={summary} writing onFrequency={() => {}} />);
  expect(stateEl()).toHaveTextContent("пишет…");
  rerender(<SessionBar agent={agentInfo({ state: "error", error: "лимит запросов" })} summary={summary} onFrequency={() => {}} />);
  expect(stateEl()).toHaveTextContent("ошибка");
  expect(stateEl()).toHaveAttribute("title", "лимит запросов");
});

test("живая область объявляет только ошибку и молчит в «Не отвлекать»", () => {
  const { rerender } = render(<SessionBar agent={agentInfo({ state: "writing" })} summary={summary} onFrequency={() => {}} />);
  expect(stateEl()).not.toHaveAttribute("role");
  expect(within(bar()).getByRole("status")).toBeEmptyDOMElement();
  const error = agentInfo({ state: "error", error: "лимит запросов" });
  rerender(<SessionBar agent={error} summary={summary} onFrequency={() => {}} />);
  expect(within(bar()).getByRole("status")).toHaveTextContent("Ошибка ассистента: лимит запросов");
  rerender(<SessionBar agent={error} summary={summary} quiet onFrequency={() => {}} />);
  expect(within(bar()).getByRole("status")).toBeEmptyDOMElement();
});

test("«Как часто писать»: реже / обычно / чаще, щелчок и стрелки", async () => {
  const onFrequency = vi.fn();
  render(<SessionBar agent={agentInfo({ frequency: "обычно" })} summary={summary} onFrequency={onFrequency} />);
  const group = screen.getByRole("radiogroup", { name: "Как часто писать" });
  expect(within(group).getAllByRole("radio").map((r) => r.textContent)).toEqual(["реже", "обычно", "чаще"]);
  expect(within(group).getByRole("radio", { name: "обычно" })).toHaveAttribute("aria-checked", "true");
  await userEvent.click(within(group).getByRole("radio", { name: "реже" }));
  expect(onFrequency).toHaveBeenLastCalledWith("реже");
  within(group).getByRole("radio", { name: "обычно" }).focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(onFrequency).toHaveBeenLastCalledWith("чаще");
});

test("«Что я знаю» — поповер со сводкой на сейчас", async () => {
  render(<SessionBar agent={agentInfo()} summary={summary} onFrequency={() => {}} />);
  await userEvent.click(screen.getByRole("button", { name: "Что я знаю" }));
  const pop = screen.getByRole("dialog", { name: "Что я знаю" });
  expect(pop).toHaveTextContent("Запуск биллинга");
  expect(pop).toHaveTextContent("Стенд к пятнице");
});

test("компактная: без «видит» и частоты в строке — они в поповере", async () => {
  render(<SessionBar agent={agentInfo({ vision: false })} summary={summary} compact onFrequency={() => {}} />);
  expect(bar()).not.toHaveTextContent("видит:");
  expect(screen.queryByRole("radiogroup")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Что я знаю" }));
  const pop = screen.getByRole("dialog", { name: "Что я знаю" });
  expect(within(pop).getByRole("radiogroup", { name: "Как часто писать" })).toBeInTheDocument();
  expect(pop).toHaveTextContent("Модель не видит изображения");
});

test("что видит: картинки и пусто", () => {
  expect(seesText(agentInfo({ sees: { conversation: true, kb: false, materials: 1, images: 2 } })))
    .toBe("разговор, 1 материал, 2 изображения");
});
