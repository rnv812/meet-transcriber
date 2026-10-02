import { expandToWords, hasTerm, newTerms, removeTerm, segmentSpan, wordAt } from "./textfix";

test("выделение дополняется до целых слов, края без знаков препинания", () => {
  const text = "Поднимем кубер нетис, на стенде.";
  // «бер нет» — недовыделенные слова целиком.
  expect(expandToWords(text, 11, 18)).toEqual({ start: 9, end: 20 });
  // С пробелом и запятой по краям — только слова.
  expect(expandToWords(text, 8, 21)).toEqual({ start: 9, end: 20 });
  expect(text.slice(9, 20)).toBe("кубер нетис");
  expect(expandToWords(text, 20, 22)).toBeNull();
  expect(expandToWords("ёлка", 1, 2)).toEqual({ start: 0, end: 4 });
});

test("слово под местом щелчка", () => {
  const text = "Кубер нетис готов.";
  expect(wordAt(text, 2)).toEqual({ start: 0, end: 5 });
  expect(wordAt(text, 5)).toEqual({ start: 0, end: 5 }); // сразу после слова
  expect(wordAt(text, 17)).toEqual({ start: 12, end: 17 });
  expect(wordAt(" , ", 1)).toBeNull();
});

test("место в реплике → сегмент и начало в нём; через границу сегментов — нельзя", () => {
  const texts = ["Начнём.", "Кубер нетис готов."];
  expect(segmentSpan(texts, 8, 19)).toEqual({ k: 1, offset: 0 });
  expect(segmentSpan(texts, 0, 6)).toEqual({ k: 0, offset: 0 });
  expect(segmentSpan(texts, 0, 13)).toBeNull();
});

test("термины: убрать только добавленную строку; есть ли уже", () => {
  const text = "SIEM\nKubernetes\n# Kubernetes в комментарии\nSOC\n";
  expect(removeTerm(text, "Kubernetes")).toBe("SIEM\n# Kubernetes в комментарии\nSOC\n");
  expect(removeTerm(text, "нет такого")).toBe(text);
  expect(hasTerm(text, "kubernetes")).toBe(true);
  expect(hasTerm("Ёлка", "елка")).toBe(true);
  expect(hasTerm(text, "Docker")).toBe(false);
});

test("термины из исправления — только новые или изменённые значимые слова (как у резидента)", () => {
  expect(newTerms("кубер нетис", "Kubernetes")).toBe("Kubernetes");
  expect(newTerms("ломается", "ломается подпись")).toBe("подпись");
  expect(newTerms("Демьян Петров", "Демьян Петрова")).toBe("Петрова");
  expect(newTerms("кубер", "Кубер")).toBe("");
  expect(newTerms("ломается", "не ломается")).toBe("");
  expect(newTerms("релиз", "релиз v2,")).toBe("v2");
});
