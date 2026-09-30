import { clock, duration, dayLabel } from "./format";

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
