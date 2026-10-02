import { DEFAULT_JIRA_KEYS, findJira, jiraBaseError, jiraKeysError, jiraLinker, jiraUrl, keysPattern } from "./jira";

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

test("список проектов через запятую — только эти проекты; адрес задачи — /browse/ключ", () => {
  const l = on({ jira_base_url: "https://jira.example.com/", jira_keys: "SPR, OPS" })!;
  expect(keysPattern("SPR, OPS")).toBe("(?:SPR|OPS)-\\d+");
  expect(keys("SPR-1, OPS-2, DEV-3", l)).toEqual(["SPR-1", "OPS-2"]);
  expect(jiraUrl(l, "SPR-1")).toBe("https://jira.example.com/browse/SPR-1");
  expect(findJira("про SPR-1", l)).toEqual([{ start: 4, end: 9, key: "SPR-1" }]);
});

test("негодный шаблон из файла — шаблон по умолчанию", () => {
  expect(keys("SPR-1", on({ jira_base_url: "https://jira.example.com", jira_keys: "(" }))).toEqual(["SPR-1"]);
  expect(keysPattern("")).toBe(DEFAULT_JIRA_KEYS);
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
