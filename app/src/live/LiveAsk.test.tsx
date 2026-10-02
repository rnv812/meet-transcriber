import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { LiveQa } from "../lib/types";
import { LiveAsk, QUICK_ACTIONS } from "./LiveAsk";

const item = (o: Partial<LiveQa> = {}): LiveQa => ({
  id: 1, q: "кто за релиз?", a: null, error: null, pending: false, at: 1, quick: null, ...o,
});

test("быстрые действия: четыре кнопки, каждая уходит со своим id", async () => {
  const onAsk = vi.fn(async () => {});
  render(<LiveAsk qa={[]} onAsk={onAsk} />);
  const group = screen.getByRole("group", { name: "Быстрые вопросы" });
  expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(
    ["Что я пропустил?", "Какие решения уже приняты?", "Что мне ответить?", "Кратко за 1 минуту"]);
  for (const q of QUICK_ACTIONS) {
    await userEvent.click(within(group).getByRole("button", { name: q.label }));
    expect(onAsk).toHaveBeenLastCalledWith(q.label, q.id);
  }
});

test("свой вопрос: Enter отправляет, поле очищается", async () => {
  const onAsk = vi.fn(async () => {});
  render(<LiveAsk qa={[]} onAsk={onAsk} />);
  const input = screen.getByRole("textbox", { name: "Вопрос ассистенту" });
  await userEvent.type(input, "  кто за релиз?  {Enter}");
  expect(onAsk).toHaveBeenCalledWith("кто за релиз?");
  expect(input).toHaveValue("");
});

test("поле не берёт фокус само — только по клику", async () => {
  render(<LiveAsk qa={[]} onAsk={vi.fn()} />);
  const input = screen.getByRole("textbox", { name: "Вопрос ассистенту" });
  expect(input).not.toHaveFocus();
  expect(document.activeElement).toBe(document.body);
  await userEvent.click(input);
  expect(input).toHaveFocus();
});

test("история целиком: вопросы, ответы Markdown'ом, ошибки; ждём — «Модель думает…»", () => {
  render(<LiveAsk onAsk={vi.fn()} qa={[
    item({ id: 1, q: "кто за релиз?", a: "**Демьян**" }),
    item({ id: 2, q: "Какие решения уже приняты?", error: "модель не ответила", quick: "decisions" }),
    item({ id: 3, q: "а сроки?", pending: true }),
  ]} />);
  const history = screen.getByRole("list", { name: "Вопросы и ответы" });
  expect(within(history).getAllByRole("listitem")).toHaveLength(3);
  expect(screen.getByText("Демьян").tagName).toBe("STRONG");
  expect(screen.getByText("модель не ответила")).toBeInTheDocument();
  expect(screen.getByText("Модель думает…")).toBeInTheDocument();
  // Пока ждём ответа, новые вопросы не уходят.
  expect(screen.getByRole("button", { name: "Спросить" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Что я пропустил?" })).toBeDisabled();
});

test("запрос ушёл, история его ещё не показала — ожидание видно сразу", () => {
  render(<LiveAsk qa={[]} asking onAsk={vi.fn()} />);
  expect(screen.getByText("Модель думает…")).toBeInTheDocument();
});

test("вопрос не дошёл — ошибка под историей", () => {
  render(<LiveAsk qa={[]} error="Ассистент не запущен" onAsk={vi.fn()} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Ассистент не запущен");
});

test("таймкоды в ответе кликабельны", async () => {
  const onTime = vi.fn();
  render(<LiveAsk qa={[item({ a: "Решили в [00:12:34]." })]} onAsk={vi.fn()} onTime={onTime} />);
  await userEvent.click(screen.getByRole("button", { name: "00:12:34" }));
  expect(onTime).toHaveBeenCalledWith(754);
});

test("текст поля можно задать снаружи (Спросить об этом)", async () => {
  const onAsk = vi.fn();
  function Host() {
    const [draft, setDraft] = useState("Расскажите подробнее: «нет владельца»");
    return <LiveAsk qa={[]} onAsk={onAsk} draft={draft} onDraft={setDraft} />;
  }
  render(<Host />);
  const input = screen.getByRole("textbox", { name: "Вопрос ассистенту" });
  expect(input).toHaveValue("Расскажите подробнее: «нет владельца»");
  await userEvent.type(input, "{Enter}");
  expect(onAsk).toHaveBeenCalledWith("Расскажите подробнее: «нет владельца»");
  expect(input).toHaveValue("");
});

test("ответ, который пишется, виден по мере генерации; готовый — Markdown'ом на том же месте", () => {
  const { rerender } = render(<LiveAsk onAsk={vi.fn()} qa={[
    item({ pending: true, partial: "Предлагаю **перенести" }),
  ]} />);
  expect(screen.queryByText("Модель думает…")).toBeNull();
  const streaming = screen.getByText("Предлагаю **перенести");
  expect(streaming).toHaveClass("live-ask__a--streaming");
  expect(streaming).toHaveAttribute("aria-busy", "true");
  rerender(<LiveAsk onAsk={vi.fn()} qa={[item({ a: "Предлагаю **перенести** релиз." })]} />);
  expect(screen.getByText("перенести").tagName).toBe("STRONG");
  expect(document.querySelector(".live-ask__a--streaming")).toBeNull();
});
