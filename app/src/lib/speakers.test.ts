import { mergeTurns, initials, initialsFontSize, speakersOf, isUnnamed } from "./speakers";

test("кегль инициалов: одна буква ≈ 42 % диаметра, две — ≈ 34 %, не меньше 9 px (0.5)", () => {
  expect(initialsFontSize(24, "А")).toBe(10);
  expect(initialsFontSize(24, "АС")).toBe(9);
  expect(initialsFontSize(48, "А")).toBe(20);
  expect(initialsFontSize(48, "АС")).toBe(16);
  expect(initialsFontSize(16, "АС")).toBe(9);
});

test("склейка подряд идущих реплик одного спикера", () => {
  const t = mergeTurns([
    { start: 0, end: 5, speaker: "Демьян", text: "а", uncertain: false },
    { start: 5.5, end: 9, speaker: "Демьян", text: "б", uncertain: true },
    { start: 12, end: 14, speaker: "Демьян", text: "в", uncertain: false },
    { start: 14, end: 15, speaker: "Матвей", text: "г", uncertain: false }]);
  expect(t.map(x => [x.speaker, x.texts.join(" "), x.uncertain]))
    .toEqual([["Демьян", "а б", true], ["Демьян", "в", false], ["Матвей", "г", false]]);
});
test("3000 сегментов склеиваются быстро", () => {
  const segs = Array.from({ length: 3000 }, (_, i) => ({ start: i, end: i + 0.9,
    speaker: i % 7 ? "Демьян" : "Матвей", text: "слово", uncertain: false }));
  const t0 = performance.now(); mergeTurns(segs);
  expect(performance.now() - t0).toBeLessThan(50);
});
test("инициалы", () => {
  expect(initials("Демьян Петров")).toBe("ДП");
  expect(initials("Демьян")).toBe("Д");
  expect(initials("Спикер 2")).toBe("2");
  expect(speakersOf([{ speaker: "Б" }, { speaker: "А" }, { speaker: "Б" }] as any)).toEqual(["Б", "А"]);
});
test("isUnnamed", () => {
  expect(isUnnamed("Спикер 3")).toBe(true);
  expect(isUnnamed("SPEAKER_01")).toBe(true);
  expect(isUnnamed("Демьян")).toBe(false);
});
test("отметка перерыва — отдельный элемент, не склеивает соседей", () => {
  const t = mergeTurns([
    { start: 0, end: 5, speaker: "Демьян", text: "а", uncertain: false },
    { start: 5, end: 5, speaker: null, text: "— перерыв 5 мин —", uncertain: false, kind: "break" },
    { start: 5.5, end: 9, speaker: "Демьян", text: "б", uncertain: false }]);
  expect(t.map((x) => [x.kind ?? null, x.texts.join(" ")]))
    .toEqual([[null, "а"], ["break", "— перерыв 5 мин —"], [null, "б"]]);
  expect(speakersOf([{ speaker: null, kind: "break" }, { speaker: "Демьян" }] as any)).toEqual(["Демьян"]);
});
