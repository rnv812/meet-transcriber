import { SETTINGS_INDEX, matchParts, sectionKeys } from "./settingsIndex";

test("matchParts: совпавшие слова — отдельными кусками, регистр и «ё» не важны", () => {
  expect(matchParts("Пройти мастер заново", "МАСТЕР")).toEqual([
    { text: "Пройти ", hit: false }, { text: "мастер", hit: true }, { text: " заново", hit: false },
  ]);
  expect(matchParts("Всё ещё", "все")).toEqual([{ text: "Всё", hit: true }, { text: " ещё", hit: false }]);
  expect(matchParts("главы", "гла лав")).toEqual([{ text: "глав", hit: true }, { text: "ы", hit: false }]);
  expect(matchParts("Модель", "")).toEqual([{ text: "Модель", hit: false }]);
});

test("requires: каждое условие — подпись строки того же раздела, а цепочка не называет саму строку", () => {
  const withRequires = SETTINGS_INDEX.filter((e) => e.requires);
  expect(withRequires.length).toBeGreaterThan(0);
  for (const e of withRequires) {
    for (const label of e.requires!) {
      expect(label).not.toBe(e.label);
      expect(SETTINGS_INDEX.some((o) => o.section === e.section && o.label === label)).toBe(true);
    }
  }
});

test("keys: согласия, история, пути, категории и секреты в сбрасываемые ключи не входят никогда", () => {
  const all = SETTINGS_INDEX.flatMap((e) => e.keys ?? []);
  expect(all.length).toBeGreaterThan(0);
  for (const key of [
    "analysis.consent", "analysis.auto", "ui.terms_accepted", "ui.wizard_done", "llm.provider", "ui.theme",
    "assist.kb_exclude", "assist.agent_mode", "assist.agent_freedom", "agent.launch", "integrations.hf_token",
    "recording.out_dir", "export.meetings_dir", "assistant.knowledge_dir", "integrations.gpu_marker_path",
    "auto_record.enabled",
  ]) expect(all).not.toContain(key);
  expect(all.every((k) => /^[a-z_]+\.[a-z_]+$/.test(k))).toBe(true);
  expect(sectionKeys("categories")).toEqual([]);
  expect(sectionKeys("models")).toEqual(expect.arrayContaining(["analysis.types", "transcript_view.types", "llm.model"]));
  expect(new Set(sectionKeys("asr")).size).toBe(sectionKeys("asr").length);
});
