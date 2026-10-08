import { render, screen } from "@testing-library/react";
import type { Recording } from "../../lib/types";
import { RecordingItem, listDuration } from "./RecordingItem";

/** Строка записи (макет LIBRARY): бейдж — в строке названия справа, мета — время и длительность. */

const rec = (extra: Partial<Recording> = {}): Recording => ({
  id: "r1", path: "C:/rec/r1", started_at: "2026-10-06T11:00:00", duration_s: 3720, tracks: { sys: "s.ogg" },
  has_transcript: false, has_voices: false, title: "Планирование спринта", source: "record", ...extra,
});
const now = new Date("2026-10-06T18:00:00");
const row = (r: Recording, extra: Partial<Parameters<typeof RecordingItem>[0]> = {}) => render(
  <ul><RecordingItem rec={r} status={{ kind: "untranscribed" }} selected={false} onSelect={() => {}} now={now} {...extra} /></ul>,
);

test("длительность в списке: часы и минуты, короткая запись — «< 1 мин», неизвестная — нет", () => {
  expect(listDuration(3720)).toBe("1 ч 02 мин");
  expect(listDuration(600)).toBe("10 мин");
  expect(listDuration(12)).toBe("< 1 мин");
  expect(listDuration(0)).toBe("");
  expect(listDuration(null)).toBe("");
});

test("бейдж состояния — в строке названия справа (на месте «⋯»), а не посреди строки меты", () => {
  const { container } = row(rec());
  const badge = screen.getByText("Не расшифровано");
  expect(badge.closest(".rec-item__head")).not.toBeNull();
  expect(badge.closest(".rec-item__meta")).toBeNull();
  expect(container.querySelector(".rec-item__meta")).toHaveTextContent("11:00 · 1 ч 02 мин");
});

test("флажок выбора — флажок Aurora .cb (18 px), не системный", () => {
  row(rec(), { picking: true, onPick: () => {} });
  const box = screen.getByRole("checkbox", { name: "Выбрать «Планирование спринта»" });
  expect(box).toHaveClass("cb");
});
