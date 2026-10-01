import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LiveAsk, MISSED_QUESTION } from "./LiveAsk";
import type { LiveReply } from "./useLive";

const idle: LiveReply = { pending: false, question: null, answer: null, error: null };

test("«Что я пропустил?» задаёт фиксированный вопрос", async () => {
  const onAsk = vi.fn(async () => {});
  render(<LiveAsk reply={idle} onAsk={onAsk} />);
  await userEvent.click(screen.getByRole("button", { name: "Что я пропустил?" }));
  expect(MISSED_QUESTION).toBe("Что я пропустил за последние минуты?");
  expect(onAsk).toHaveBeenCalledWith("Что я пропустил за последние минуты?");
});

test("свой вопрос: Enter отправляет, поле очищается", async () => {
  const onAsk = vi.fn(async () => {});
  render(<LiveAsk reply={idle} onAsk={onAsk} />);
  const input = screen.getByRole("textbox", { name: "Вопрос ассистенту" });
  await userEvent.type(input, "  кто за релиз?  {Enter}");
  expect(onAsk).toHaveBeenCalledWith("кто за релиз?");
  expect(input).toHaveValue("");
});

test("поле не берёт фокус само — только по клику", async () => {
  render(<LiveAsk reply={idle} onAsk={vi.fn()} />);
  const input = screen.getByRole("textbox", { name: "Вопрос ассистенту" });
  expect(input).not.toHaveFocus();
  expect(document.activeElement).toBe(document.body);
  await userEvent.click(input);
  expect(input).toHaveFocus();
});

test("пока модель думает — кнопки неактивны, видно ожидание", () => {
  render(<LiveAsk reply={{ pending: true, question: "кто?", answer: null, error: null }} onAsk={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Что я пропустил?" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Спросить" })).toBeDisabled();
  expect(screen.getByText("Модель думает…")).toBeInTheDocument();
  expect(screen.getByText("кто?")).toBeInTheDocument();
});

test("ответ и ошибка — под полем", () => {
  const { rerender } = render(
    <LiveAsk reply={{ pending: false, question: "кто?", answer: "**Демьян**", error: null }} onAsk={vi.fn()} />);
  const answer = screen.getByText("Демьян");
  expect(answer.tagName).toBe("STRONG");
  const input = screen.getByRole("textbox", { name: "Вопрос ассистенту" });
  expect(input.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  rerender(<LiveAsk reply={{ pending: false, question: "кто?", answer: null, error: "модель не ответила" }} onAsk={vi.fn()} />);
  expect(screen.getByRole("alert")).toHaveTextContent("модель не ответила");
});
