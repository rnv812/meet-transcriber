import { discussText, prepareText, PROFILE_TEXT_MAX, refLabel } from "./profileAgent";
import type { Profile } from "./types";

const base: Profile = {
  version: 1, person_id: "0123456789abcdef", name: "Вера", updated_at: 0, meetings: 3, turns: 40,
  summary: "Говорит по делу", sections: {
    how_to_talk: [{ text: "Приходить с вариантами", refs: [{ m: "a", i: 1, t: 61 }] }],
    avoid: [{ text: "Длинных вступлений.", refs: [{ m: "a", i: 2, t: 70 }] }],
  },
  sources: { a: { title: "Очень длинное название встречи про планирование квартала", date: "2026-09-30" } },
};

test("подпись ссылки: короткое название встречи и время", () => {
  expect(refLabel(base, { m: "a", i: 1, t: 61 })).toBe("Очень длинное название встр… · 01:01");
  expect(refLabel(base, { m: "нет", i: 1 })).toBe("нет · 00:00");
});

test("заготовка: одна строка, точки, просьба в конце", () => {
  const text = prepareText("Вера", base);
  expect(text).toBe(
    "Помоги подготовиться к разговору с человеком «Вера». Его профиль общения (гипотеза по репликам во встречах, "
    + "не оценка личности): Коротко: Говорит по делу. Как лучше строить разговор: Приходить с вариантами. "
    + "Чего избегать: Длинных вступлений. Предложи план разговора: как начать, как аргументировать, как попросить о "
    + "решении и как дать обратную связь; учитывай материалы этой встречи. Тема разговора:");
});

test("управляющие символы и переводы строк вычищаются, длина ограничена, конец сохраняется", () => {
  const noisy: Profile = {
    ...base,
    summary: "Строка\r\nвторая \u001b[201~ конец‮",
    sections: { how_to_talk: Array.from({ length: 50 }, (_, k) => ({
      text: `Совет ${k} `.repeat(20), refs: [{ m: "a", i: k, t: k }] })) },
  };
  const text = prepareText("Ве\nра\u0007", noisy);
  expect(text).not.toMatch(/[\u0000-\u001f\u007f-\u009f‪-‮]/);
  expect(text).toContain("«Ве ра»");
  expect(text).toContain("Коротко: Строка вторая конец.");
  expect(Array.from(text).length).toBeLessThanOrEqual(PROFILE_TEXT_MAX);
  expect(text.endsWith("Тема разговора:")).toBe(true);
  expect(Array.from(discussText("Вера", noisy)).length).toBeLessThanOrEqual(PROFILE_TEXT_MAX);
});

test("«Коротко» без опоры на реплики агенту не уходит; без общей встречи — без «материалов этой встречи»", () => {
  const ungrounded: Profile = { ...base, sections: { how_to_talk: [{ text: "Совет", refs: [] }] } };
  expect(prepareText("Вера", ungrounded)).not.toContain("Коротко");
  expect(discussText("Вера", ungrounded)).not.toContain("Коротко");
  const text = prepareText("Вера", base, true, false);
  expect(text).not.toContain("материалы этой встречи");
  expect(text.endsWith("обратную связь. Тема разговора:")).toBe(true);
});

test("ссылки «реплика изменилась» в текст для агента не попадают", () => {
  const p: Profile = { ...base, sections: { style: [{ text: "Коротко", refs: [
    { m: "a", i: 1, t: 61, stale: true }, { m: "a", i: 2, t: 70 }] }] } };
  expect(discussText("Вера", p)).toContain("Стиль общения: Коротко. [Очень длинное название… · 01:10]");
});
