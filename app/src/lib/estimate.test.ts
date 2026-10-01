import { estimateText, pyRound } from "./estimate";

// Значения — из Python `meet.engine.estimate_text` (round() банковский).
test.each([
  [3600, "cuda", "60 мин встречи ≈ 13 мин обработки"],
  [3600, "cpu", "60 мин встречи ≈ 76 мин обработки"],
  [150, "cuda", "2 мин встречи ≈ 1 мин обработки"],
  [150, "cpu", "2 мин встречи ≈ 3 мин обработки"],
  [90, "cpu", "2 мин встречи ≈ 2 мин обработки"],
  [2280, "cpu", "38 мин встречи ≈ 48 мин обработки"],
  [1500, "cuda", "25 мин встречи ≈ 6 мин обработки"],
  [1500, "cpu", "25 мин встречи ≈ 32 мин обработки"],
  [4500, "cpu", "75 мин встречи ≈ 94 мин обработки"],
  [0, "cuda", "1 мин встречи ≈ 1 мин обработки"],
] as const)("estimateText(%d, %s) как в Python", (duration, profile, text) => {
  expect(estimateText(duration, profile)).toBe(text);
});

test("pyRound: половина — к чётному, как round() в Python", () => {
  expect(pyRound(2.5)).toBe(2);
  expect(pyRound(3.5)).toBe(4);
  expect(pyRound(31.5)).toBe(32);
  expect(pyRound(5.5)).toBe(6);
  expect(pyRound(2.4999)).toBe(2);
  expect(pyRound(2.5001)).toBe(3);
});
