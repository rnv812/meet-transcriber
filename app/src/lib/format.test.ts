import { clock, duration, dayLabel, errorText, plural } from "./format";

test("форматы", () => {
  expect(clock(65)).toBe("01:05");
  expect(clock(3725)).toBe("1:02:05");
  expect(duration(2264)).toBe("38 мин");
  expect(duration(3900)).toBe("1 ч 05 мин");
  const now = new Date("2026-09-30T20:00:00");
  expect(dayLabel("2026-09-30T16:04:00", now)).toBe("Сегодня 16:04");
  expect(dayLabel("2026-09-29T11:00:00", now)).toBe("Вчера 11:00");
  expect(dayLabel("2026-09-28T16:04:00", now)).toBe("28 сен 16:04");
});

test("dayLabel(short): под разделом по дням — только время, в месяцах и годах — «5 сен, 10:00»", () => {
  const now = new Date("2026-10-06T12:00:00");
  expect(dayLabel("2026-10-06T09:05:00", now, true)).toBe("09:05");
  expect(dayLabel("2026-10-05T23:00:00", now, true)).toBe("23:00");
  expect(dayLabel("2026-09-30T10:00:00", now, true)).toBe("10:00");
  expect(dayLabel("2026-09-29T10:00:00", now, true)).toBe("29 сен, 10:00");
  expect(dayLabel("2024-09-05T10:00:00", now, true)).toBe("5 сен, 10:00");
});

test("errorText: у Error — только сообщение", () => {
  expect(errorText(new Error("сломалось"))).toBe("сломалось");
  expect(errorText("строка")).toBe("строка");
  expect(errorText(42)).toBe("42");
});

test("plural: форма слова по числу", () => {
  const w = (n: number) => `${n} ${plural(n, "запись", "записи", "записей")}`;
  expect([1, 2, 5, 11, 12, 21, 22, 25, 111].map(w)).toEqual([
    "1 запись", "2 записи", "5 записей", "11 записей", "12 записей", "21 запись", "22 записи", "25 записей", "111 записей"]);
});
