import { matchParts } from "./settingsIndex";

test("matchParts: совпавшие слова — отдельными кусками, регистр и «ё» не важны", () => {
  expect(matchParts("Пройти мастер заново", "МАСТЕР")).toEqual([
    { text: "Пройти ", hit: false }, { text: "мастер", hit: true }, { text: " заново", hit: false },
  ]);
  expect(matchParts("Всё ещё", "все")).toEqual([{ text: "Всё", hit: true }, { text: " ещё", hit: false }]);
  expect(matchParts("главы", "гла лав")).toEqual([{ text: "глав", hit: true }, { text: "ы", hit: false }]);
  expect(matchParts("Модель", "")).toEqual([{ text: "Модель", hit: false }]);
});
