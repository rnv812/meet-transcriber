import { SETTINGS_INDEX, matchParts } from "./settingsIndex";

test("matchParts: совпавшие слова — отдельными кусками, регистр и «ё» не важны", () => {
  expect(matchParts("Пройти мастер заново", "МАСТЕР")).toEqual([
    { text: "Пройти ", hit: false }, { text: "мастер", hit: true }, { text: " заново", hit: false },
  ]);
  expect(matchParts("Всё ещё", "все")).toEqual([{ text: "Всё", hit: true }, { text: " ещё", hit: false }]);
  expect(matchParts("главы", "гла лав")).toEqual([{ text: "глав", hit: true }, { text: "ы", hit: false }]);
  expect(matchParts("Модель", "")).toEqual([{ text: "Модель", hit: false }]);
});

test("requires: каждое условие — подпись строки того же раздела, а цепочка не называет саму строку", () => {
  const withRequires = SETTINGS_INDEX.filter((e) => e.requires);
  expect(withRequires.length).toBeGreaterThan(0);
  for (const e of withRequires) {
    for (const label of e.requires!) {
      expect(label).not.toBe(e.label);
      expect(SETTINGS_INDEX.some((o) => o.section === e.section && o.label === label)).toBe(true);
    }
  }
});
