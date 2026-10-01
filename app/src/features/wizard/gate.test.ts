import { LOCAL_DONE_KEY, freeSpaceShortfall, readLocalDone, shouldAutoShow, writeLocalDone } from "./gate";

test("мастер сам — только без движка, без резидента и без «Пропустить»", () => {
  expect(shouldAutoShow({ installed: false, reachable: false, wizardDone: false })).toBe(true);
  expect(shouldAutoShow({ installed: true, reachable: false, wizardDone: false })).toBe(false);
  // dev: резидент отвечает — мастер не мешает.
  expect(shouldAutoShow({ installed: false, reachable: true, wizardDone: false })).toBe(false);
  expect(shouldAutoShow({ installed: false, reachable: false, wizardDone: true })).toBe(false);
});

test("флаг «мастер пройден» живёт в localStorage; сбой хранилища — не пройден", () => {
  localStorage.removeItem(LOCAL_DONE_KEY);
  expect(readLocalDone()).toBe(false);
  writeLocalDone();
  expect(localStorage.getItem(LOCAL_DONE_KEY)).toBe("1");
  expect(readLocalDone()).toBe(true);
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  try {
    expect(readLocalDone()).toBe(false);
    expect(() => writeLocalDone()).not.toThrow();
  } finally {
    get.mockRestore();
    set.mockRestore();
    localStorage.removeItem(LOCAL_DONE_KEY);
  }
});

test("нехватка места — в десятых ГБ, без хвостов плавающей точки; не узнать — не блокируем", () => {
  expect(freeSpaceShortfall(5, 2.3)).toBe(2.7);
  expect(freeSpaceShortfall(5, 5)).toBeNull();
  expect(freeSpaceShortfall(5, 12.4)).toBeNull();
  expect(freeSpaceShortfall(5, null)).toBeNull();
});
