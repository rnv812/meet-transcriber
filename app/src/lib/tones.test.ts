import { NO_SPEAKER } from "./speakers";
import { SPEAKER_RINGS, speakerTone, speakerTones } from "./tones";

test("цвет спикера: свой цвет человека из базы голосов важнее места в шапке", () => {
  expect(speakerTone("Анна", 3, { name: "Анна", color: "#4b6bd6" })).toBe("#4b6bd6");
});

test("цвет спикера: названного, но не из базы — по месту в шапке (палитра данных Aurora, по кругу)", () => {
  expect(speakerTone("Анна", 0)).toBe(SPEAKER_RINGS[0]);
  expect(speakerTone("Борис", 1, { name: "Борис", color: "" })).toBe(SPEAKER_RINGS[1]);
  expect(speakerTone("Вера", SPEAKER_RINGS.length)).toBe(SPEAKER_RINGS[0]);
  expect(SPEAKER_RINGS.every((c) => /^var\(--data-\d\)$/.test(c))).toBe(true);
});

test("цвет спикера: «Спикер N» и «Неизвестный» — без цвета (серые везде)", () => {
  expect(speakerTone("Спикер 2", 1)).toBeUndefined();
  expect(speakerTone("SPEAKER_01", 0)).toBeUndefined();
  expect(speakerTone(NO_SPEAKER, 0)).toBeUndefined();
});

test("цвета спикеров встречи: одна карта на шапку, ленту и панель — по порядку в шапке", () => {
  const tones = speakerTones(["Анна", "Спикер 2", "Борис"], [{ name: "Борис", color: "#c0793a" }]);
  expect([...tones]).toEqual([["Анна", SPEAKER_RINGS[0]], ["Борис", "#c0793a"]]);
});
