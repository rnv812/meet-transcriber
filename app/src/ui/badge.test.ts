import { BADGE_CLASS } from "./badge";

test("тоны бейджей окна — варианты Aurora со словом рядом", () => {
  expect(BADGE_CLASS.ok).toBe("badge badge--fresh");
  expect(BADGE_CLASS.run).toBe("badge badge--info");
  expect(BADGE_CLASS.err).toBe("badge badge--error");
  expect(BADGE_CLASS.temp).toBe("badge badge--stale");
  expect(BADGE_CLASS.plain).toBe("badge badge--plain");
});
