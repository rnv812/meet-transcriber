import { attMsg } from "../test/chatFixtures";
import { MAX_SOURCES, findSources, kbIndex, mayMentionDocs } from "./sources";

const kb = kbIndex({
  root: "D:\\KB",
  docs: ["Проекты/Альфа/Биллинг.md", "Проекты/Альфа/План.md", "Проекты/Бета/План.md", "Заметки/Ёлка.txt", "a.md"],
  more: false,
});

test("вложение — по названию целиком или ссылке mat:, открывается исходник, потом копия во встрече", () => {
  const plan = attMsg("a1", { type: "doc", name: "План запуска.pptx", source: "C:\\docs\\План запуска.pptx", path: "C:\\rec\\a1.txt" });
  const shot = attMsg("a2", { type: "image", name: "скрин.png", path: "C:\\rec\\files\\a2.png" });
  const got = findSources("Глянул план запуска.pptx и [скрин](mat:a2) — срок 15.11.", [plan, shot], null);
  expect(got.map((s) => [s.label, s.kind, s.paths])).toEqual([
    ["План запуска.pptx", "doc", ["C:\\docs\\План запуска.pptx", "C:\\rec\\a1.txt"]],
    ["скрин.png", "image", ["C:\\rec\\files\\a2.png"]],
  ]);
});

test("вложение не узнаётся посреди слова, неразобранное и убранное — без чипа", () => {
  const plan = attMsg("a1", { type: "doc", name: "План.md", path: "C:\\rec\\a1.txt" });
  expect(findSources("Смотри СтарыйПлан.md", [plan], null)).toEqual([]);
  expect(findSources("План.md", [{ ...plan, status: "failed" }], null)).toEqual([]);
  expect(findSources("План.md", [{ ...plan, status: "removed" }], null)).toEqual([]);
});

test("база знаний: полный путь (с расширением и без), ссылка kb:, уникальное имя файла", () => {
  const labels = (text: string) => findSources(text, [], kb).map((s) => s.paths[0]);
  expect(labels("См. Проекты\\Альфа\\Биллинг.md")).toEqual(["D:\\KB\\Проекты\\Альфа\\Биллинг.md"]);
  expect(labels("В [заметке](kb:Проекты/Бета/План.md#Сроки) есть срок")).toEqual(["D:\\KB\\Проекты\\Бета\\План.md"]);
  expect(labels("По проекты/альфа/план.md и Заметки/Елка.txt")).toEqual([
    "D:\\KB\\Проекты\\Альфа\\План.md", "D:\\KB\\Заметки\\Ёлка.txt"]);
  expect(labels("Биллинг.md говорит про таймаут")).toEqual(["D:\\KB\\Проекты\\Альфа\\Биллинг.md"]);
  // Имя «План.md» в базе дважды — без папки не угадываем; короткое имя «a.md» — тоже.
  expect(labels("Открой План.md и a.md")).toEqual([]);
  // Без папки и без расширения — не упоминание.
  expect(labels("Биллинг тормозит")).toEqual([]);
});

test("не больше MAX_SOURCES чипов, повторов нет", () => {
  const many = Array.from({ length: 6 }, (_, i) => attMsg(`a${i}`, { type: "doc", name: `Док${i}.md`, path: `C:\\r\\a${i}.txt` }));
  const text = many.map((a) => a.name).join(", ") + ", Док0.md";
  expect(findSources(text, many, null)).toHaveLength(MAX_SOURCES);
});

test("базу знаний спрашиваем, только если сообщение похоже на упоминание файла", () => {
  expect(mayMentionDocs("Срок — 15 ноября")).toBe(false);
  expect(mayMentionDocs("см. план.pptx")).toBe(true);
  expect(mayMentionDocs("[заметка](kb:Проекты/План)")).toBe(true);
  expect(mayMentionDocs("см. kb:Проекты/План")).toBe(true);
  expect(mayMentionDocs("версия 1.mdx")).toBe(false);
});

test("ложные совпадения (ревью M4): mat:a1 ≠ mat:a12, путь без расширения ≠ другой файл, хвост пути, папка, повторы", () => {
  const a1 = attMsg("a1", { type: "doc", name: "Отчёт.md", path: "C:\rec\a1.txt" });
  const a12 = attMsg("a12", { type: "doc", name: "Другое.md", path: "C:\rec\a12.txt" });
  expect(findSources("см. [это](mat:a12)", [a1, a12], null).map((s) => s.key)).toEqual(["a12"]);
  const both = kbIndex({ root: "D:\KB", docs: ["Проекты/Альфа/План.docx", "Альфа/План.md", "Проекты/Альфа/План.md"], more: false });
  // «План.pdf» — экспорт рядом: не тот же «План.docx» по пути без расширения.
  expect(findSources("Смотри Проекты/Альфа/План.pdf", [], both)).toEqual([]);
  // «Проекты/Альфа/План.md» — не «Альфа/План.md» по хвосту.
  expect(findSources("Смотри Проекты/Альфа/План.md", [], both).map((s) => s.key)).toEqual(["kb:Проекты/Альфа/План.md"]);
  // Путь без расширения узнаётся и без расширения где-то ещё в сообщении.
  expect(findSources("Это в Проекты/Альфа/План, раздел сроков", [], kbIndex({
    root: "D:\KB", docs: ["Проекты/Альфа/План.md"], more: false,
  })).map((s) => s.key)).toEqual(["kb:Проекты/Альфа/План.md"]);
  // Папка (без расширения) по слову не узнаётся; по ссылке — да.
  const dir = attMsg("a3", { type: "doc", name: "Документы", path: "C:\rec\a3.txt" });
  expect(findSources("Документы я посмотрел.", [dir], null)).toEqual([]);
  expect(findSources("[Документы](mat:a3)", [dir], null).map((s) => s.key)).toEqual(["a3"]);
  // Несколько вставленных «image.png» — один чип.
  const shots = [1, 2, 3].map((k) => attMsg(`a${k + 10}`, { name: "image.png", path: `C:\rec\files\a${k + 10}.png` }));
  expect(findSources("на image.png видно ошибку", shots, null)).toHaveLength(1);
});
