import {
  cleanGroupName, groupNameError, loadGroupScope, meetingsText, NO_GROUP, reorderIds, saveGroupScope, sentence, shiftId,
} from "./groups";

beforeEach(() => window.localStorage.clear());

test("область списка запоминается в meet.groupScope; все записи — ключа нет", () => {
  expect(loadGroupScope()).toBeNull();
  saveGroupScope("g-1a2b3c4d");
  expect(window.localStorage.getItem("meet.groupScope")).toBe('"g-1a2b3c4d"');
  expect(loadGroupScope()).toBe("g-1a2b3c4d");
  saveGroupScope(NO_GROUP);
  expect(loadGroupScope()).toBe(NO_GROUP);
  saveGroupScope(null);
  expect(window.localStorage.getItem("meet.groupScope")).toBeNull();
});

test("мусор в хранилище и недоступное хранилище — все записи, без исключений", () => {
  window.localStorage.setItem("meet.groupScope", "{не json");
  expect(loadGroupScope()).toBeNull();
  window.localStorage.setItem("meet.groupScope", JSON.stringify("../../etc"));
  expect(loadGroupScope()).toBeNull();
  window.localStorage.setItem("meet.groupScope", JSON.stringify(42));
  expect(loadGroupScope()).toBeNull();
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceeded"); });
  try {
    expect(loadGroupScope()).toBeNull();
    expect(() => saveGroupScope("g-1")).not.toThrow();
  } finally {
    get.mockRestore();
    set.mockRestore();
  }
});

test("название группы — те же правила и слова, что у резидента", () => {
  const groups = [{ id: "g-1", name: "Проект Альфа" }, { id: "g-2", name: "Ёлка" }];
  expect(groupNameError("  ", groups)).toBe("Нужно название группы");
  expect(groupNameError("а".repeat(61), groups)).toBe("Название группы — не длиннее 60 символов");
  expect(groupNameError("а".repeat(60), groups)).toBeNull();
  expect(groupNameError("все  ЗАПИСИ", groups)).toBe("«Все записи» — не название группы");
  expect(groupNameError("проект   альфа", groups)).toBe("Группа «проект альфа» уже есть");
  expect(groupNameError("елка", groups)).toBe("Группа «елка» уже есть");
  // Своё же имя при переименовании — не повтор.
  expect(groupNameError("Проект Альфа", groups, "g-1")).toBeNull();
  expect(groupNameError("Проект Бета", groups)).toBeNull();
  expect(cleanGroupName("  Проект \t Альфа ")).toBe("Проект Альфа");
  expect(sentence("группа «X» уже есть")).toBe("Группа «X» уже есть");
});

test("порядок: вставка перед местом и шаг выше/ниже; без изменений — тот же массив", () => {
  const ids = ["a", "b", "c", "d"];
  expect(reorderIds(ids, "a", 3)).toEqual(["b", "c", "a", "d"]);
  expect(reorderIds(ids, "d", 0)).toEqual(["d", "a", "b", "c"]);
  expect(reorderIds(ids, "b", 4)).toEqual(["a", "c", "d", "b"]);
  expect(reorderIds(ids, "b", 1)).toBe(ids);
  expect(reorderIds(ids, "b", 2)).toBe(ids);
  expect(reorderIds(ids, "x", 0)).toBe(ids);
  expect(shiftId(ids, "c", -1)).toEqual(["a", "c", "b", "d"]);
  expect(shiftId(ids, "c", 1)).toEqual(["a", "b", "d", "c"]);
  expect(shiftId(ids, "a", -1)).toBe(ids);
  expect(shiftId(ids, "d", 1)).toBe(ids);
});

test("число встреч по-русски", () => {
  expect(meetingsText(1)).toBe("1 встреча");
  expect(meetingsText(3)).toBe("3 встречи");
  expect(meetingsText(12)).toBe("12 встреч");
  expect(meetingsText(21)).toBe("21 встреча");
});
