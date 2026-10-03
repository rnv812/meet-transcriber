import {
  aliasError, DEFAULT_JIRA_KEYS, findJira, jiraBaseError, jiraKeysError, jiraLinker, jiraProjects, jiraUrl, keysPattern,
  literalPattern, projectKeyError, projectsError,
} from "./jira";

const on = (integrations: Record<string, unknown>, view: Record<string, unknown> = {}) =>
  jiraLinker({ integrations, transcript_view: view });
const keys = (text: string, l = on({ jira_base_url: "https://jira.example.com" })) => findJira(text, l).map((m) => m.key);

test("ключи по шаблону по умолчанию; не внутри слова и не с хвостом", () => {
  expect(keys("Заведу SPR-131 и OPS-7, а fooSPR-1, SPR-12a и spr-5 — нет.")).toEqual(["SPR-131", "OPS-7"]);
  expect(keys("(SPR-131); «SPR-9»")).toEqual(["SPR-131", "SPR-9"]);
  expect(keys("ВЖ-12 — кириллица не ключ")).toEqual([]);
});

test("адрес не задан или ссылки выключены — ссылок нет", () => {
  expect(on({})).toBeNull();
  expect(on({ jira_base_url: "" })).toBeNull();
  expect(on({ jira_base_url: "https://jira.example.com" }, { jira: false })).toBeNull();
  expect(on({ jira_base_url: "http://jira.example.com" })).toBeNull();
  expect(findJira("SPR-1", null)).toEqual([]);
});

test("проекты из настроек — только их ключи; адрес задачи — /browse/ключ", () => {
  const l = on({ jira_base_url: "https://jira.example.com/", jira_projects: [{ key: "SPR", aliases: [] }, { key: "OPS" }] })!;
  expect(keysPattern("SPR, OPS")).toBe("(?:SPR|OPS)-\\d+");
  expect(keys("SPR-1, OPS-2, DEV-3", l)).toEqual(["SPR-1", "OPS-2"]);
  expect(jiraUrl(l, "SPR-1")).toBe("https://jira.example.com/browse/SPR-1");
  expect(findJira("про SPR-1", l)).toEqual([{ start: 4, end: 9, key: "SPR-1" }]);
});

test("негодный шаблон из файла — шаблон по умолчанию", () => {
  expect(keys("SPR-1", on({ jira_base_url: "https://jira.example.com", jira_pattern: "(" }))).toEqual(["SPR-1"]);
  expect(keysPattern("")).toBe(DEFAULT_JIRA_KEYS);
});

test("шаблон ключа для текста: свой, иначе ключи проектов, иначе любой ключ", () => {
  expect(literalPattern({})).toBe(DEFAULT_JIRA_KEYS);
  expect(literalPattern({ jira_projects: [{ key: "ORION", aliases: ["смдэв"] }, { key: "SPR" }] }))
    .toBe("(?:ORION|SPR)-\\d+");
  expect(literalPattern({ jira_projects: [{ key: "ORION" }], jira_pattern: "OPS, DEMO" })).toBe("(?:OPS|DEMO)-\\d+");
  expect(literalPattern({ jira_projects: [{ key: "ORION" }], jira_pattern: "(" })).toBe("(?:ORION)-\\d+");
  const l = on({ jira_base_url: "https://jira.example.com", jira_pattern: "DEMO-\\d{2}" })!;
  expect(keys("DEMO-12, SPR-1", l)).toEqual(["DEMO-12"]);
});

test("проекты из файла: битые ключи и повторы пропускаются", () => {
  expect(jiraProjects({ jira_projects: [{ key: "SPR", aliases: ["эс пи ар", 5] }, { key: "spr" }, { key: "SPR" }, "OPS", null] }))
    .toEqual([{ key: "SPR", aliases: ["эс пи ар"] }]);
  expect(jiraProjects({ jira_projects: "SPR" })).toEqual([]);
});

test("проверка ключа проекта и вариантов названия", () => {
  expect(projectKeyError("")).toBeNull();
  expect(projectKeyError("orion")).toBeNull();
  expect(projectKeyError("ORION", ["ORION"])).toBe("Проект ORION уже в списке");
  for (const bad of ["S", "1AB", "SM-DEV", "ОРИОН", "A".repeat(21)]) expect(projectKeyError(bad), bad).not.toBeNull();
  expect(aliasError("смдэв")).toBeNull();
  expect(aliasError("эс эм дэв")).toBeNull();
  expect(aliasError("орион2")).toMatch(/цифр/);
  expect(aliasError("ab")).toMatch(/три буквы/);
  expect(aliasError("см/дев")).toMatch(/Только буквы/);
  expect(projectsError([{ key: "ORION", aliases: ["смдэв"] }, { key: "SPR", aliases: [] }])).toBeNull();
  expect(projectsError([{ key: "ORION", aliases: [] }, { key: "ORION", aliases: [] }])).toMatch(/дважды/);
  expect(projectsError([{ key: "ORION", aliases: ["x1"] }])).toMatch(/^ORION: /);
});

test("проверка адреса и шаблона в настройках", () => {
  expect(jiraBaseError("")).toBeNull();
  expect(jiraBaseError("https://jira.example.com")).toBeNull();
  expect(jiraBaseError("https://example.com:8443/jira")).toBeNull();
  for (const bad of ["http://jira.example.com", "https://user:pass@jira.example.com", "https://jira.example.com?x",
    "https://jira..example.com", "https://.example.com", "javascript:alert(1)", "https://jira.example.com/#a"]) {
    expect(jiraBaseError(bad), bad).not.toBeNull();
  }
  expect(jiraKeysError("")).toBeNull();
  expect(jiraKeysError("SPR, OPS")).toBeNull();
  expect(jiraKeysError("(?:SPR|OPS)-\\d+")).toBeNull();
  for (const bad of ["(", "x*", "(?i)spr-\\d+", "(?P<k>A)-\\d+", "A".repeat(201)]) {
    expect(jiraKeysError(bad), bad).not.toBeNull();
  }
});
