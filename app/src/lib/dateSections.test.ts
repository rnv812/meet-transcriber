import {
  defaultOpen, groupBySection, loadSectionPrefs, parseLocal, saveSectionPrefs, sectionOf, SECTIONS_KEY,
  SECTIONS_MAX, withPref,
} from "./dateSections";

const at = (s: string) => new Date(s);
const of = (startedAt: string | null, now: string) => {
  const s = sectionOf(startedAt, at(now));
  return `${s.key} | ${s.label}`;
};

test("вторник 6 октября: сегодня, вчера, дни недели, месяцы, «Ранее в 2025», годы, без даты", () => {
  const now = "2026-10-06T12:00:00";
  expect(of("2026-10-06T09:30:00", now)).toBe("today | Сегодня");
  expect(of("2026-10-05T18:00:00", now)).toBe("yesterday | Вчера");
  expect(of("2026-10-04T10:00:00", now)).toBe("day:2026-10-04 | Воскресенье, 4 октября");
  expect(of("2026-09-30T10:00:00", now)).toBe("day:2026-09-30 | Среда, 30 сентября");
  expect(of("2026-09-29T23:59:00", now)).toBe("m:2026-09 | Сентябрь");
  expect(of("2026-01-05T10:00:00", now)).toBe("m:2026-01 | Январь");
  expect(of("2025-12-15T10:00:00", now)).toBe("m:2025-12 | Декабрь 2025");
  // Двенадцать месяцев назад — ещё месяц, дальше — остаток того года.
  expect(of("2025-10-01T10:00:00", now)).toBe("m:2025-10 | Октябрь 2025");
  expect(of("2025-09-30T10:00:00", now)).toBe("rest-y:2025 | Ранее в 2025");
  expect(of("2025-01-01T00:00:00", now)).toBe("rest-y:2025 | Ранее в 2025");
  expect(of("2024-05-01T10:00:00", now)).toBe("y:2024 | 2024");
  expect(of("2019-12-31T10:00:00", now)).toBe("y:2019 | 2019");
});

test("«Ранее в <месяце>»: этот месяц старше недели", () => {
  const now = "2026-10-20T12:00:00";
  expect(of("2026-10-14T10:00:00", now)).toBe("day:2026-10-14 | Среда, 14 октября");
  expect(of("2026-10-13T10:00:00", now)).toBe("rest:2026-10 | Ранее в октябре");
  expect(of("2026-10-01T00:00:00", now)).toBe("rest:2026-10 | Ранее в октябре");
  expect(of("2026-05-10T10:00:00", "2026-05-31T10:00:00")).toBe("rest:2026-05 | Ранее в мае");
});

test("полночь: граница суток по местному времени", () => {
  expect(of("2026-10-05T23:59:59", "2026-10-06T00:00:30")).toBe("yesterday | Вчера");
  expect(of("2026-10-06T00:00:00", "2026-10-06T00:00:30")).toBe("today | Сегодня");
  expect(of("2026-10-05T00:00:00", "2026-10-05T23:59:59")).toBe("today | Сегодня");
  // Запись «из будущего» (часы сбиты) — в «Сегодня», а не теряется.
  expect(of("2026-10-07T10:00:00", "2026-10-06T12:00:00")).toBe("today | Сегодня");
});

test("понедельник: прошлая неделя — по дням, неделей раньше — месяц", () => {
  const now = "2026-10-05T09:00:00";
  expect(of("2026-10-04T10:00:00", now)).toBe("yesterday | Вчера");
  expect(of("2026-10-03T10:00:00", now)).toBe("day:2026-10-03 | Суббота, 3 октября");
  expect(of("2026-09-29T10:00:00", now)).toBe("day:2026-09-29 | Вторник, 29 сентября");
  expect(of("2026-09-28T10:00:00", now)).toBe("m:2026-09 | Сентябрь");
  expect(of("2026-10-01T10:00:00", now)).toBe("day:2026-10-01 | Четверг, 1 октября");
});

test("первое число месяца: вчера — прошлый месяц, «Ранее в …» этого месяца нет", () => {
  const now = "2026-10-01T10:00:00";
  expect(of("2026-09-30T22:00:00", now)).toBe("yesterday | Вчера");
  expect(of("2026-09-25T10:00:00", now)).toBe("day:2026-09-25 | Пятница, 25 сентября");
  expect(of("2026-09-24T10:00:00", now)).toBe("m:2026-09 | Сентябрь");
  expect(of("2026-10-01T00:00:00", now)).toBe("today | Сегодня");
});

test("1 января: прошлый год — месяцы с годом, дни недели через Новый год", () => {
  const now = "2027-01-01T08:00:00";
  expect(of("2026-12-31T23:00:00", now)).toBe("yesterday | Вчера");
  expect(of("2026-12-26T10:00:00", now)).toBe("day:2026-12-26 | Суббота, 26 декабря");
  expect(of("2026-12-25T10:00:00", now)).toBe("m:2026-12 | Декабрь 2026");
  expect(of("2026-01-15T10:00:00", now)).toBe("m:2026-01 | Январь 2026");
  // Весь 2026 — в месяцах: «Ранее в 2026» пуст, дальше сразу годы.
  expect(of("2025-12-31T10:00:00", now)).toBe("y:2025 | 2025");
});

test("без даты: нет, пусто, не дата", () => {
  const now = "2026-10-06T12:00:00";
  expect(of(null, now)).toBe("none | Без даты");
  expect(of("", now)).toBe("none | Без даты");
  expect(of("вчера", now)).toBe("none | Без даты");
  expect(of("2026-13-45T10:00:00", now)).toBe("none | Без даты");
});

test("parseLocal: время без зоны — местное; только дата — полночь", () => {
  expect(parseLocal("2026-10-06T09:05:00")?.getHours()).toBe(9);
  expect(parseLocal("2026-10-06 09:05")?.getMinutes()).toBe(5);
  expect(parseLocal("2026-10-06")?.getDate()).toBe(6);
  expect(parseLocal("2026-02-31T10:00:00")).toBeNull();
});

test("groupBySection: разделы по порядку от новых к старым, «Без даты» последним; порядок внутри — как пришёл", () => {
  const now = at("2026-10-06T12:00:00");
  const rec = (id: string, started_at: string | null) => ({ id, started_at });
  const groups = groupBySection([
    rec("n", null),
    rec("y24", "2024-03-01T10:00:00"),
    rec("t2", "2026-10-06T08:00:00"),
    rec("t1", "2026-10-06T10:00:00"),
    rec("sep", "2026-09-10T10:00:00"),
    rec("r25", "2025-02-01T10:00:00"),
    rec("y", "2026-10-05T10:00:00"),
  ], now);
  expect(groups.map((g) => [g.section.key, g.items.map((r) => r.id)])).toEqual([
    ["today", ["t2", "t1"]],
    ["yesterday", ["y"]],
    ["m:2026-09", ["sep"]],
    ["rest-y:2025", ["r25"]],
    ["y:2024", ["y24"]],
    ["none", ["n"]],
  ]);
});

test("по умолчанию свёрнуты только годовые разделы", () => {
  const now = at("2026-10-06T12:00:00");
  const open = (s: string | null) => defaultOpen(sectionOf(s, now));
  expect(open("2026-10-06T10:00:00")).toBe(true);
  expect(open("2026-10-02T10:00:00")).toBe(true);
  expect(open("2025-11-02T10:00:00")).toBe(true);
  expect(open("2025-02-02T10:00:00")).toBe(false);
  expect(open("2023-02-02T10:00:00")).toBe(false);
  expect(open(null)).toBe(true);
});

describe("запоминание разделов", () => {
  beforeEach(() => window.localStorage.clear());

  test("хранятся только отклонения от умолчания", () => {
    let prefs = withPref({}, "m:2026-09", false, true);
    expect(prefs).toEqual({ "m:2026-09": false });
    prefs = withPref(prefs, "y:2024", true, false);
    expect(prefs).toEqual({ "m:2026-09": false, "y:2024": true });
    // Вернули как по умолчанию — ключ уходит.
    prefs = withPref(prefs, "m:2026-09", true, true);
    expect(prefs).toEqual({ "y:2024": true });
    saveSectionPrefs(prefs);
    expect(JSON.parse(window.localStorage.getItem(SECTIONS_KEY)!)).toEqual({ "y:2024": true });
    expect(loadSectionPrefs()).toEqual({ "y:2024": true });
    saveSectionPrefs({});
    expect(window.localStorage.getItem(SECTIONS_KEY)).toBeNull();
  });

  test("не больше 100 ключей: старые вытесняются, тронутый — снова свежий", () => {
    let prefs: Record<string, boolean> = {};
    for (let i = 0; i < SECTIONS_MAX; i++) prefs = withPref(prefs, `k${i}`, false, true);
    prefs = withPref(prefs, "k0", false, true);
    prefs = withPref(prefs, "new", false, true);
    expect(Object.keys(prefs)).toHaveLength(SECTIONS_MAX);
    expect("k1" in prefs).toBe(false);
    expect("k0" in prefs).toBe(true);
    expect(Object.keys(prefs).at(-1)).toBe("new");
  });

  test("битое хранилище — пусто", () => {
    window.localStorage.setItem(SECTIONS_KEY, "{не json");
    expect(loadSectionPrefs()).toEqual({});
    window.localStorage.setItem(SECTIONS_KEY, JSON.stringify({ a: true, b: "да", c: false }));
    expect(loadSectionPrefs()).toEqual({ a: true, c: false });
  });
});
