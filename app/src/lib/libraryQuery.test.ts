import {
  addChip, chipText, commit, effectiveQuery, intersectDates, parseDuration, parseText, personQuery, prefixHint,
  refreshDates, scan, suggestions, tail, textOf,
  toFilter, toggleChip, whoRanges, whoWords, withGroupScope, NO_GROUP_MATCH, type Chip, type QueryContext,
} from "./libraryQuery";
import type { Category } from "./types";

const NOW = new Date(2026, 9, 6, 15, 30);
const categories: Category[] = [
  { id: "daily", name: "Дейлик", color: "#4c8bf5", description: "" },
  { id: "client", name: "Встреча с клиентом", color: "#e08a2e", description: "" },
  { id: "retro", name: "Ретроспектива", color: "#a0703c", description: "" },
];
const groups = [
  { id: "g-alpha001", name: "Проект Альфа", color: "#2fa36b" },
  { id: "g-beta0002", name: "Бета", color: "#8e6cd8" },
  { id: "g-bet00003", name: "Бетон", color: "#9aa0a6" },
];
const ctx: QueryContext = { categories, groups, now: NOW };
const parse = (text: string) => parseText(text, ctx);

test("scan: префиксы, кавычки, незакрытая кавычка — до конца", () => {
  const toks = scan(`участник:"Анна П" бюджет «план работ» Спикер:Борис http://x категория:«Встреча с`);
  expect(toks.map((t) => [t.prefix, t.value, t.quoted])).toEqual([
    ["участник", "Анна П", true], [null, "бюджет", false], [null, "план работ", true],
    ["спикер", "Борис", false], [null, "http://x", false], ["категория", "Встреча с", true],
  ]);
});

test("каждый префикс — своя метка, текст — отдельно", () => {
  const got = parse(`название:бюджет спикер:Анна участник:"Борис П" группа:альфа категория:дейлик дата:вчера `
    + "есть:итоги нет:анализ дольше:30м короче:1ч релиз");
  expect(got.chips).toEqual([
    { kind: "title", value: "бюджет" },
    { kind: "person", value: "Борис П" },
    { kind: "group", value: "g-alpha001" },
    { kind: "category", value: "daily" },
    { kind: "date", value: "2026-10-05..2026-10-05", label: "Вчера", expr: "дата:вчера" },
    { kind: "has", value: "summary" },
    { kind: "lacks", value: "analysis" },
    { kind: "longer", value: "1800" },
    { kind: "shorter", value: "3600" },
  ]);
  expect(got.rest).toBe("спикер:Анна релиз");
});

test("после: и до: — края диапазона, подписи днём", () => {
  expect(parse("после:5.10").chips).toEqual([{ kind: "date", value: "2026-10-05..", label: "с 5 октября" }]);
  expect(parse("до:сентябрь").chips).toEqual([{ kind: "date", value: "..2026-09-30", label: "по 30 сентября" }]);
  expect(toFilter(parse("после:1.09 до:15.09").chips)).toEqual({ from: "2026-09-01", to: "2026-09-15" });
});

test("дата без кавычек — самый длинный разбираемый кусок; дальше — текст", () => {
  expect(parse("дата:5 окт бюджет")).toMatchObject({
    chips: [{ kind: "date", value: "2026-10-05..2026-10-05" }], rest: "бюджет" });
  expect(parse("дата:с 1.09 по 15.09 релиз")).toMatchObject({
    chips: [{ kind: "date", value: "2026-09-01..2026-09-15" }], rest: "релиз" });
  expect(parse("дата:5 октября 2025").chips[0]!.value).toBe("2025-10-05..2025-10-05");
  expect(parse(`дата:"прошлая неделя"`).chips[0]!.value).toBe("2026-09-28..2026-10-04");
});

test("негодные и недописанные префиксы в поиск не уходят и метками не становятся", () => {
  const got = parse("дата:абв группа:нет-такой есть:что-то дольше: участник: бюджет");
  expect(got.chips).toEqual([]);
  expect(got.rest).toBe("бюджет");
  expect(got.prefixes.map((p) => [p.prefix, p.chip])).toEqual([
    ["дата", null], ["группа", null], ["есть", null], ["дольше", null], ["участник", null]]);
});

test("группа и категория — по имени, началу слова, id; неоднозначное — нет", () => {
  expect(parse("группа:бета").chips).toEqual([{ kind: "group", value: "g-beta0002" }]);
  expect(parse("группа:бет").chips).toEqual([]);  // Бета и Бетон
  expect(parse("группа:бето").chips).toEqual([{ kind: "group", value: "g-bet00003" }]);
  expect(parse("группа:альф").chips).toEqual([{ kind: "group", value: "g-alpha001" }]);
  expect(parse("группа:g-beta0002").chips).toEqual([{ kind: "group", value: "g-beta0002" }]);
  expect(parse("группа:без").chips).toEqual([{ kind: "group", value: "_none" }]);
  expect(parse(`категория:"встреча с клиентом"`).chips).toEqual([{ kind: "category", value: "client" }]);
  expect(parse("категория:клиент").chips).toEqual([{ kind: "category", value: "client" }]);
  expect(parse("Категория:РЕТРО").chips).toEqual([{ kind: "category", value: "retro" }]);
  expect(parse("категория:без").chips).toEqual([{ kind: "category", value: "_none" }]);
});

test("есть/нет — по началу слова и формам", () => {
  for (const [w, v] of [["итоги", "summary"], ["итогов", "summary"], ["ит", "summary"], ["анализа", "analysis"],
    ["ассистент", "assistant"], ["асс", "assistant"], ["расшифровки", "transcript"], ["расш", "transcript"]]) {
    expect(parse(`есть:${w}`).chips).toEqual([{ kind: "has", value: v }]);
  }
  expect(parse("есть:а").chips).toEqual([]);
});

test("длительность", () => {
  expect(parseDuration("30")).toBe(1800);
  expect(parseDuration("30м")).toBe(1800);
  expect(parseDuration("30 мин")).toBe(1800);
  expect(parseDuration("1ч")).toBe(3600);
  expect(parseDuration("1,5ч")).toBe(5400);
  expect(parseDuration("1ч30м")).toBe(5400);
  expect(parseDuration("90с")).toBe(90);
  expect(parseDuration("час")).toBeNull();
  expect(parseDuration("30x")).toBeNull();
  expect(parseDuration("")).toBeNull();
  expect(chipText({ kind: "longer", value: "5400" }, ctx)).toBe("Дольше 1 ч 30 мин");
  expect(chipText({ kind: "shorter", value: "900" }, ctx)).toBe("Короче 15 мин");
});

test("числовая дата целиком — метка; с другими словами — текст", () => {
  expect(parse("05.10")).toMatchObject({ numericDate: true, rest: "",
    chips: [{ kind: "date", value: "2026-10-05..2026-10-05", label: "5 октября" }] });
  expect(parse("05.10 релиз")).toMatchObject({ numericDate: false, rest: "05.10 релиз", chips: [] });
  // Словесная дата без префикса — только подсказка.
  expect(parse("вчера")).toMatchObject({ chips: [], rest: "вчера" });
  expect(parse("2025")).toMatchObject({ chips: [], rest: "2025" });
  // В кавычках — текст.
  expect(parse(`"05.10"`)).toMatchObject({ chips: [], rest: `"05.10"` });
});

test("textOf — только текстовая часть: префиксы карточке не нужны", () => {
  expect(textOf("группа:альфа бюджет спикер:Анна \"план работ\" дата:5 окт", NOW)).toBe(`бюджет спикер:Анна "план работ"`);
  expect(textOf("участник:Анна", NOW)).toBe("");
  expect(textOf("05.10", NOW)).toBe("");
  expect(textOf("бюджет", NOW)).toBe("бюджет");
  expect(textOf("название:релиз план", NOW)).toBe("план");
});

test("toFilter: категории и группы — любая из, участники — все, даты — пересечение, длительность — строже", () => {
  const chips: Chip[] = [
    { kind: "category", value: "daily" }, { kind: "category", value: "_none" },
    { kind: "group", value: "g-alpha001" }, { kind: "person", value: "Анна" }, { kind: "person", value: "Борис" },
    { kind: "date", value: "2026-09-01..2026-09-30" }, { kind: "date", value: "2026-09-15.." },
    { kind: "has", value: "summary" }, { kind: "lacks", value: "analysis" },
    { kind: "longer", value: "600" }, { kind: "longer", value: "900" }, { kind: "shorter", value: "3600" },
    { kind: "title", value: "релиз" }, { kind: "title", value: "план" },
  ];
  expect(toFilter(chips)).toEqual({
    categories: ["daily", "_none"], groups: ["g-alpha001"], people: ["Анна", "Борис"],
    from: "2026-09-15", to: "2026-09-30", has: ["summary"], lacks: ["analysis"], min_s: 900, max_s: 3600,
    title: "релиз план",
  });
  expect(toFilter([])).toEqual({});
});

test("addChip/toggleChip: дата одна, «есть» и «нет» одного — не вместе, повторов нет", () => {
  let chips: Chip[] = [{ kind: "date", value: "2026-10-05..2026-10-05" }, { kind: "has", value: "summary" }];
  chips = addChip(chips, { kind: "date", value: "2026-09-01..2026-09-30" });
  chips = addChip(chips, { kind: "lacks", value: "summary" });
  chips = addChip(chips, { kind: "person", value: "Анна" });
  chips = addChip(chips, { kind: "person", value: "Анна" });
  expect(chips).toEqual([
    { kind: "date", value: "2026-09-01..2026-09-30" }, { kind: "lacks", value: "summary" }, { kind: "person", value: "Анна" }]);
  expect(toggleChip(chips, { kind: "person", value: "Анна" })).toHaveLength(2);
});

test("commit (Enter): годные префиксы — в метки, категории — в запоминаемые, негодное остаётся в поле", () => {
  const got = commit("бюджет группа:альфа категория:дейлик дата:абв участник:Анна", [], ["retro"], ctx);
  expect(got).toEqual({
    text: "бюджет дата:абв ",
    chips: [{ kind: "group", value: "g-alpha001" }, { kind: "person", value: "Анна" }],
    categories: ["retro", "daily"],
  });
  expect(commit("05.10", [], [], ctx)).toEqual({
    text: "", chips: [{ kind: "date", value: "2026-10-05..2026-10-05", label: "5 октября" }], categories: [] });
  expect(commit("просто текст", [], [], ctx)).toEqual({ text: "просто текст", chips: [], categories: [] });
  expect(commit("дата:5 окт бюджет", [], [], ctx).text).toBe("бюджет ");
});

test("effectiveQuery: метки сеанса + запомненные категории + текст; active — не от одних категорий", () => {
  const chips: Chip[] = [{ kind: "person", value: "Анна" }];
  // «название:релиз бюджет»: «релиз» — только в названии (title=), «бюджет» — как обычно.
  const got = effectiveQuery("название:релиз бюджет группа:бета", chips, ["daily"], ctx);
  expect(got.q).toBe("бюджет");
  expect(got.find).toBe("бюджет");
  expect(got.filter).toEqual({ categories: ["daily"], people: ["Анна"], groups: ["g-beta0002"], title: "релиз" });
  expect(got.chips.map((c) => c.kind)).toEqual(["category", "person", "title", "group"]);
  expect(got.active).toBe(true);
  expect(effectiveQuery("", [], ["daily"], ctx).active).toBe(false);
  expect(effectiveQuery("б", [], [], ctx).active).toBe(false);
  expect(effectiveQuery("бю", [], [], ctx).active).toBe(true);
  expect(effectiveQuery("группа:бета", [], [], ctx).active).toBe(true);
  // Повтор метки из текста и сеанса — одна.
  expect(effectiveQuery("участник:Анна", chips, [], ctx).filter.people).toEqual(["Анна"]);
});

test("область группы и метка «группа:» не спорят: метка сужает внутри области", () => {
  expect(withGroupScope({ people: ["Анна"] }, null)).toEqual({ people: ["Анна"] });
  expect(withGroupScope({}, "g-alpha001")).toEqual({ groups: ["g-alpha001"] });
  expect(withGroupScope({ groups: ["g-alpha001", "g-beta0002"] }, "g-alpha001")).toEqual({ groups: ["g-alpha001"] });
  expect(withGroupScope({ groups: ["g-beta0002"] }, "g-alpha001")).toEqual({ groups: [NO_GROUP_MATCH] });
  expect(withGroupScope({ groups: ["_none"] }, "_none")).toEqual({ groups: ["_none"] });
  expect(withGroupScope({ groups: ["g-beta0002"] }, undefined)).toEqual({ groups: ["g-beta0002"] });
});

test("подписи меток", () => {
  expect(chipText({ kind: "category", value: "daily" }, ctx)).toBe("Дейлик");
  expect(chipText({ kind: "category", value: "_none" }, ctx)).toBe("Без категории");
  expect(chipText({ kind: "group", value: "_none" }, ctx)).toBe("Без группы");
  expect(chipText({ kind: "group", value: "g-gone0000" }, ctx)).toBe("Группа без названия");
  expect(chipText({ kind: "has", value: "summary" }, ctx)).toBe("Есть итоги");
  expect(chipText({ kind: "lacks", value: "assistant" }, ctx)).toBe("Без ассистента");
  expect(chipText({ kind: "title", value: "релиз" }, ctx)).toBe("В названии: релиз");
});

test("подсветка участника в имени спикера", () => {
  const who = whoWords("бюджет спикер:\"Борис П\"", { people: ["Анна"] });
  expect(who).toEqual([["анна"], ["борис", "п"]]);
  expect(whoRanges("Анна Петрова", who)).toEqual([[0, 4]]);
  expect(whoRanges("Борис Петров", who)).toEqual([[0, 5], [6, 12]]);
  expect(whoRanges("Борис Сидоров", who)).toEqual([]);  // «П» не нашлась — не тот Борис
  expect(whoRanges("Ёлка", [["елк"]])).toEqual([[0, 4]]);
  expect(whoRanges("Анна", [])).toEqual([]);
});

// --- подсказки ------------------------------------------------------------------------

const labels = (text: string, people = [] as { name: string; meetings: number }[]) =>
  suggestions(text, ctx, people).map((s) => s.label);

test("первая подсказка — «Искать «…» в тексте»; дописать префикс", () => {
  expect(labels("")).toEqual([]);
  const s = suggestions("кат", ctx);
  expect(s[0]).toMatchObject({ label: "Искать «кат» в тексте", action: { type: "commit" } });
  expect(s[1]).toMatchObject({ label: "категория:", action: { type: "replace", text: "категория:" } });
  expect(labels("бюджет д")).toEqual(["Искать «бюджет д» в тексте", "дата:", "до:", "дольше:"]);
});

test("подсказки значений префикса: группы, категории, даты, есть, длительность, участники", () => {
  expect(labels("группа:")).toEqual(["Применить условия", "Без группы", "Проект Альфа", "Бета", "Бетон"]);
  const g = suggestions("бюджет группа:бе", ctx);
  expect(g.map((x) => x.label)).toEqual(["Искать «бюджет» в тексте", "Без группы", "Бета", "Бетон"]);
  expect(g.find((x) => x.label === "Бета")!.action).toEqual({ type: "replace", text: "бюджет ",
    chip: { kind: "group", value: "g-beta0002" } });
  expect(labels("категория:")).toEqual(["Применить условия", "Без категории", "Дейлик", "Встреча с клиентом", "Ретроспектива"]);
  expect(labels("дата:")).toEqual(["Применить условия", "Сегодня", "Вчера", "Эта неделя", "Прошлая неделя", "Этот месяц", "Прошлый месяц"]);
  expect(labels("дата:5 окт")).toEqual(["Применить условия", "5 октября"]);
  expect(labels("дата:про")).toEqual(["Применить условия", "Прошлая неделя", "Прошлый месяц"]);
  expect(labels("после:5.10")).toEqual(["Применить условия", "с 5 октября"]);
  expect(labels("есть:")).toEqual(["Применить условия", "Есть итоги", "Есть анализ", "Был ассистент", "Есть расшифровка"]);
  expect(labels("нет:ан")).toEqual(["Применить условия", "Нет анализа"]);
  expect(labels("дольше:")).toEqual(["Применить условия", "Дольше 15 мин", "Дольше 30 мин", "Дольше 1 ч", "Дольше 2 ч"]);
  expect(labels("короче:45")).toEqual(["Применить условия", "Короче 45 мин"]);
  const people = [{ name: "Анна Петрова", meetings: 12 }, { name: "Анатолий", meetings: 1 }];
  const s = suggestions("участник:Ан", ctx, people);
  expect(s.map((x) => [x.label, x.detail])).toEqual([
    ["Применить условия", "Enter"], ["Анна Петрова", "12 встреч"], ["Анатолий", "1 встреча"]]);
  expect(s[1]!.action).toEqual({ type: "replace", text: "", chip: { kind: "person", value: "Анна Петрова" } });
});

test("без префикса: участники, группы, категории, словесная дата — только подсказкой", () => {
  const people = [{ name: "Анна Петрова", meetings: 3 }];
  const s = suggestions("бюджет ан", ctx, people);
  expect(s.map((x) => x.label)).toEqual(["Искать «бюджет ан» в тексте", "Участник: Анна Петрова"]);
  expect(s[1]!.action).toEqual({ type: "replace", text: "бюджет ", chip: { kind: "person", value: "Анна Петрова" } });
  expect(labels("альф")).toEqual(["Искать «альф» в тексте", "Группа: Проект Альфа"]);
  expect(labels("ретро")).toEqual(["Искать «ретро» в тексте", "Категория: Ретроспектива"]);
  const d = suggestions("бюджет 5 окт", ctx);
  expect(d.map((x) => x.label)).toEqual(["Искать «бюджет 5 окт» в тексте", "Дата: 5 октября"]);
  expect(d[1]!.action).toEqual({ type: "replace", text: "бюджет ",
    chip: { kind: "date", value: "2026-10-05..2026-10-05", label: "5 октября" } });
  expect(labels("вчера")).toEqual(["Искать «вчера» в тексте", "Дата: Вчера"]);
  expect(labels("2025")).toEqual(["Искать «2025» в тексте", "Дата: 2025"]);
  // Слово кончилось (пробел) — подсказок значений нет.
  expect(labels("ретро ")).toEqual(["Искать «ретро» в тексте"]);
});

test("числовая дата: по Enter — метка, «Искать «05.10» как текст» — фразой", () => {
  const s = suggestions("05.10", ctx);
  expect(s.map((x) => x.label)).toEqual(["Дата: 5 октября", "Искать «05.10» как текст"]);
  expect(s[0]!.action).toEqual({ type: "commit" });
  expect(s[1]!.action).toEqual({ type: "replace", text: `"05.10"` });
});

test("tail и personQuery: что под курсором", () => {
  expect(tail("бюджет дата:5 ок").tok?.value).toBe("5 ок");
  expect(tail("дата:вчера бюджет").tok?.value).toBe("бюджет");
  expect(tail(`участник:"Анна П`).tok?.value).toBe("Анна П");
  expect(tail(`участник:"Анна П" `).tok).toBeNull();
  expect(personQuery("участник:Ан")).toBe("Ан");
  expect(personQuery("участник:")).toBe("");
  expect(personQuery("бюджет ан")).toBe("ан");
  expect(personQuery("бюджет а")).toBeNull();
  expect(personQuery("бюджет 12")).toBeNull();
  expect(personQuery("группа:ан")).toBeNull();
  expect(personQuery("бюджет ")).toBeNull();
});

// --- fix round 1 ------------------------------------------------------------------------

test("I2: Enter не расширяет фильтр — «после:» и «до:» становятся одним диапазоном", () => {
  const typed = "после:1.09 до:15.09 бюджет";
  expect(toFilter(parse(typed).chips)).toEqual({ from: "2026-09-01", to: "2026-09-15" });
  const done = commit(typed, [], [], ctx);
  expect(done.chips).toEqual([{ kind: "date", value: "2026-09-01..2026-09-15", label: "1 сен – 15 сен" }]);
  expect(toFilter(done.chips)).toEqual({ from: "2026-09-01", to: "2026-09-15" });
  expect(done.text).toBe("бюджет ");
  // С уже стоящей меткой даты — тоже пересечение, как действовало до Enter.
  const before: Chip[] = [{ kind: "date", value: "2026-09-01..2026-09-30", label: "Сентябрь" }];
  const more = commit("после:10.09", before, [], ctx);
  expect(more.chips).toEqual([{ kind: "date", value: "2026-09-10..2026-09-30", label: "10 сен – 30 сен" }]);
  // Длительность — строжайшая граница из всех.
  expect(commit("дольше:30м дольше:10м короче:2ч", [{ kind: "shorter", value: "3600" }], [], ctx).chips).toEqual([
    { kind: "longer", value: "1800" }, { kind: "shorter", value: "3600" }]);
  // Пересечение пусто — так и подписано, а не «шире».
  expect(intersectDates([{ kind: "date", value: "2026-09-20.." }, { kind: "date", value: "..2026-09-10" }], NOW))
    .toEqual({ kind: "date", value: "2026-09-20..2026-09-10", label: "20 сентября – 10 сентября (нет общих дней)" });
});

test("I2: addChip — «после» к «до» (подсказка, панель) дополняет; полный диапазон заменяет", () => {
  let chips: Chip[] = addChip([], { kind: "date", value: "..2026-09-15", label: "по 15 сентября" }, NOW);
  chips = addChip(chips, { kind: "date", value: "2026-09-01..", label: "с 1 сентября" }, NOW);
  expect(chips).toEqual([{ kind: "date", value: "2026-09-01..2026-09-15", label: "1 сен – 15 сен" }]);
  chips = addChip(chips, { kind: "date", value: "2026-10-01..2026-10-31", label: "Октябрь" }, NOW);
  expect(chips).toEqual([{ kind: "date", value: "2026-10-01..2026-10-31", label: "Октябрь" }]);
  // Два «после» — новое заменяет.
  chips = addChip([{ kind: "date", value: "2026-09-01.." }], { kind: "date", value: "2026-09-05.." }, NOW);
  expect(chips).toEqual([{ kind: "date", value: "2026-09-05.." }]);
});

test("I1: «до:» без года — этот год; «после:» — последнее прошедшее", () => {
  expect(parse("до:20.10").chips).toEqual([{ kind: "date", value: "..2026-10-20", label: "по 20 октября" }]);
  expect(parse("до:31.12").chips).toEqual([{ kind: "date", value: "..2026-12-31", label: "по 31 декабря" }]);
  expect(parse("после:20.10").chips).toEqual([{ kind: "date", value: "2025-10-20..", label: "с 20 октября 2025" }]);
  expect(parse("дата:с 1.10 по 31.10").chips).toEqual([{ kind: "date", value: "2026-10-01..2026-10-31", label: "Октябрь" }]);
});

test("I3: название: — только его слова в названии, прочие ищутся как обычно; в карточку — прочие", () => {
  const got = effectiveQuery(`бюджет название:релиз спикер:Анна название:"план работ"`, [], [], ctx);
  expect(got.q).toBe("бюджет спикер:Анна");
  expect(got.filter).toEqual({ title: "релиз план работ" });
  expect(got.find).toBe("бюджет спикер:Анна");
  expect(textOf("название:релиз бюджет", NOW)).toBe("бюджет");
  // Пустое «название:» — не метка.
  expect(parse("название: бюджет").chips).toEqual([]);
  const committed = commit("название:релиз бюджет", [], [], ctx);
  expect(committed).toMatchObject({ text: "бюджет ", chips: [{ kind: "title", value: "релиз" }] });
  expect(effectiveQuery(committed.text, committed.chips, [], ctx)).toMatchObject({ q: "бюджет", filter: { title: "релиз" } });
});

test("M4: «дата:с сентября», «дата:до 5 окт» — края, а не весь месяц", () => {
  expect(parse("дата:с сентября").chips).toEqual([{ kind: "date", value: "2026-09-01..", label: "с 1 сентября" }]);
  expect(parse("дата:после 5 окт").chips).toEqual([{ kind: "date", value: "2026-10-05..", label: "с 5 октября" }]);
  expect(parse("дата:до сентября").chips).toEqual([{ kind: "date", value: "..2026-09-30", label: "по 30 сентября" }]);
  expect(parse("дата:по 5 окт").chips).toEqual([{ kind: "date", value: "..2026-10-05", label: "по 5 октября" }]);
  expect(parse("дата:с утра").chips).toEqual([]);
});

test("M3: относительные даты помнят выражение и пересчитываются после полуночи", () => {
  const chips: Chip[] = [
    ...parse("дата:сегодня").chips, ...parse("после:вчера").chips,
    { kind: "date", value: "2026-09-01..2026-09-30", label: "Сентябрь" },
  ];
  expect(chips[0]).toEqual({ kind: "date", value: "2026-10-06..2026-10-06", label: "Сегодня", expr: "дата:сегодня" });
  expect(chips[1]).toMatchObject({ value: "2026-10-05..", expr: "после:вчера" });
  const tomorrow = new Date(2026, 9, 7, 0, 1);
  const next = refreshDates(chips, tomorrow);
  expect(next[0]).toEqual({ kind: "date", value: "2026-10-07..2026-10-07", label: "Сегодня", expr: "дата:сегодня" });
  expect(next[1]).toEqual({ kind: "date", value: "2026-10-06..", label: "с 6 октября", expr: "после:вчера" });
  expect(next[2]).toBe(chips[2]);
  // Тот же день — тот же массив (без лишних перерисовок).
  expect(refreshDates(chips, NOW)).toBe(chips);
  // Эта неделя через воскресенье.
  const week = parse("дата:эта неделя").chips;
  expect(refreshDates(week, new Date(2026, 9, 12))[0]).toMatchObject({ value: "2026-10-12..2026-10-18", label: "Эта неделя" });
});

test("M1: «1.5», «3.11», «5.10» без префикса — текст и подсказка, а не метка", () => {
  for (const s of ["1.5", "3.11", "5.10", "2.10"]) {
    expect(parse(s)).toMatchObject({ numericDate: false, chips: [], rest: s });
  }
  expect(suggestions("5.10", ctx).map((x) => x.label)).toEqual(["Искать «5.10» в тексте", "Дата: 5 октября"]);
  expect(parse("05.10").numericDate).toBe(true);
});

test("M12: негодный префикс — пояснение в подсказках, не выбирается", () => {
  const hints = (text: string) => suggestions(text, ctx).filter((x) => x.kind === "hint").map((x) => [x.label, x.disabled]);
  expect(hints("дата:абв")).toEqual([["Не понял дату «абв»", true]]);
  expect(hints("дата:с 15.09 по 1.09")).toEqual([["Конец периода раньше начала — поменяйте местами или укажите год", true]]);
  expect(hints("группа:нет-такой")).toEqual([["Нет группы «нет-такой»", true]]);
  expect(hints("категория:ххх")).toEqual([["Нет категории «ххх»", true]]);
  expect(hints("есть:что")).toEqual([["Не понял «что»: итоги, анализ, ассистент или расшифровка", true]]);
  expect(hints("дольше:долго")).toEqual([["Не понял длительность «долго»: например 30м или 1ч", true]]);
  // Есть подходящие значения или ещё ничего не набрано — пояснения нет.
  expect(hints("группа:бе")).toEqual([]);
  expect(hints("дата:")).toEqual([]);
  // Негодный префикс раньше в строке — тоже поясняется.
  expect(hints("группа:нет-такой бюджет")).toEqual([["Нет группы «нет-такой»", true]]);
  expect(prefixHint("группа", "бет", ctx)).toBe("«бет» — подходит несколько групп, уточните");
});

test("M7: у даты подсказки — дни словами, если подпись их не называет", () => {
  const detail = (text: string) => suggestions(text, ctx).filter((x) => x.kind === "date").map((x) => [x.label, x.detail]);
  expect(detail("дата:эта")).toEqual([["Эта неделя", "5 окт – 11 окт"]]);
  expect(detail("дата:5 окт")).toEqual([["5 октября", undefined]]);
  expect(detail("дата:с 1.09 по 15.09")).toEqual([["1 сен – 15 сен", undefined]]);
  expect(detail("вчера")).toEqual([["Дата: Вчера", "5 октября"]]);
});

test("диапазон задом наперёд после «дата:» негоден целиком — не укорачивается до «с 15.09»", () => {
  const got = parse("бюджет дата:с 15.09 по 1.09 релиз");
  expect(got.chips).toEqual([]);
  expect(got.rest).toBe("бюджет релиз");
  expect(got.prefixes.map((x) => [x.value, x.chip])).toEqual([["с 15.09 по 1.09", null]]);
  expect(commit("бюджет дата:с 15.09 по 1.09", [], [], ctx).text).toBe("бюджет дата:с 15.09 по 1.09");
  expect(parse("после:15.09-1.09").chips).toEqual([]);
});

test("R1-I1: метка «до:» 3 января — по концу прошлого года", () => {
  const jan3: QueryContext = { ...ctx, now: new Date(2027, 0, 3, 10, 0) };
  expect(parseText("до:31.12", jan3).chips).toEqual([{ kind: "date", value: "..2026-12-31", label: "по 31 декабря 2026" }]);
  expect(parseText("до:декабрь", jan3).chips).toEqual([{ kind: "date", value: "..2026-12-31", label: "по 31 декабря 2026" }]);
});
