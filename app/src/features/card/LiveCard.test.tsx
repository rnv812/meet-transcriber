import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { LiveStatus } from "../../lib/types";
import { FakeEventSource } from "../../test/setup";
import { LiveCard } from "./LiveCard";

const ep = { base: "http://h", token: null };
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: true, starting: false, stopping: false, folder: "C:/rec/r1", error: null, started_at: 1, ...o,
});
const streams = () => FakeEventSource.instances.filter((s) => s.url.startsWith("http://h/live/events"));

test("идёт: поток ассистента открыт, те же вкладки, вопросы доступны", async () => {
  render(<LiveCard endpoint={ep} live={live()} />);
  act(() => streams()[0]!.emit("state", { digest: "", hints: [], status: null }));
  expect(screen.getByText("Идёт запись с ассистентом")).toBeInTheDocument();
  expect(streams()).toHaveLength(1);
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Лента", "Сводка", "Подсказки", "Спросить"]);
  await userEvent.click(screen.getByRole("tab", { name: "Спросить" }));
  expect(screen.getByRole("button", { name: "Что я пропустил?" })).toBeEnabled();
});

test("ассистент дописывает запись: лента остаётся, поток закрыт, вопросы неактивны", async () => {
  const { rerender } = render(<LiveCard endpoint={ep} live={live()} />);
  act(() => streams()[0]!.emit("state", { digest: "", hints: [], status: null }));
  act(() => streams()[0]!.emit("line", { t: 1, speaker: "Демьян", text: "итог" }, 0));
  rerender(<LiveCard endpoint={ep} live={live({ active: false, stopping: true })} />);
  expect(screen.getByText("Останавливаю…")).toBeInTheDocument();
  expect(screen.getByRole("log")).toHaveTextContent("итог");
  expect(streams()[0]!.closed).toBe(true);
  await userEvent.click(screen.getByRole("tab", { name: "Спросить" }));
  expect(screen.getByRole("button", { name: "Что я пропустил?" })).toBeDisabled();
});

test("резидент дописывает запись (active и stopping) — поток закрыт, «нет связи» не пишем", () => {
  const { rerender } = render(<LiveCard endpoint={ep} live={live()} />);
  rerender(<LiveCard endpoint={ep} live={live({ active: true, stopping: true })} />);
  expect(streams()[0]!.closed).toBe(true);
  expect(streams()).toHaveLength(1); // и не переподключаемся
  expect(screen.queryByText(/Нет связи с ассистентом/)).toBeNull();
  expect(screen.getByText("Останавливаю…")).toBeInTheDocument();
});

test("до первого состояния ассистента — «Подключаюсь…», а не прежние вкладки", () => {
  render(<LiveCard endpoint={ep} live={live()} />);
  expect(screen.getByText("Подключаюсь к ассистенту…")).toBeInTheDocument();
  expect(screen.queryByRole("tablist")).toBeNull();
  act(() => streams()[0]!.emit("state", { digest: "", hints: [], status: null }));
  expect(screen.getByRole("tablist")).toBeInTheDocument();
});
