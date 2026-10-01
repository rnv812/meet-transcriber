import type { SpeakerRow } from "../../../lib/types";
import { changeText, describeStep, finalOf, preview, prune, rememberDefault, toOps, type Staged } from "./staging";

const row = (label: string, extra: Partial<SpeakerRow> = {}): SpeakerRow => ({
  label, name: null, seconds: 10, share: 0.5, turns: 2, samples: [], has_voice: true, suggestions: [], ...extra,
});

test("итоговая подпись: имя, объединение по цепочке, «без имени»", () => {
  const staged: Staged = {
    "Спикер 1": { kind: "rename", to: "Борис" },
    "Спикер 3": { kind: "merge", into: "Спикер 1" },
    "Анна": { kind: "reset" },
  };
  expect(finalOf("Спикер 3", staged)).toBe("Борис");
  expect(finalOf("Анна", staged)).toBeNull();
  expect(finalOf("Спикер 2", staged)).toBe("Спикер 2");
});

test("предпросмотр перечисляет правки в порядке строк", () => {
  const staged: Staged = {
    "Спикер 3": { kind: "merge", into: "Спикер 1" },
    "Спикер 2": { kind: "rename", to: "Анна" },
    "Спикер 1": { kind: "rename", to: "Борис" },
  };
  expect(preview(["Спикер 1", "Спикер 2", "Спикер 3"], staged)).toBe(
    "Будет изменено: Спикер 1 → Борис, Спикер 2 → Анна, Спикер 3 → объединён со спикером «Борис»");
  expect(preview(["Спикер 1"], {})).toBe("");
  expect(changeText("Анна", { "Анна": { kind: "reset" } })).toBe("Анна → без имени");
});

test("правки уходят резиденту в порядке строк", () => {
  const staged: Staged = { "Спикер 2": { kind: "reset" }, "Спикер 1": { kind: "merge", into: "Спикер 2" } };
  expect(toOps(["Спикер 1", "Спикер 2"], staged)).toEqual([
    { type: "merge", label: "Спикер 1", to: "Спикер 2" }, { type: "reset", label: "Спикер 2" }]);
});

test("«Запомнить голос» включён только для безымянного, получившего имя", () => {
  const named: Staged = { "Спикер 2": { kind: "rename", to: "Анна" } };
  expect(rememberDefault(row("Спикер 2"), named, "Вы")).toBe(true);
  expect(rememberDefault(row("Спикер 2", { has_voice: false }), named, "Вы")).toBe(false);
  expect(rememberDefault(row("Спикер 2"), { "Спикер 2": { kind: "rename", to: "Вы" } }, "Вы")).toBe(false);
  expect(rememberDefault(row("Анна"), { "Анна": { kind: "rename", to: "Анна Смирнова" } }, "Вы")).toBe(false);
  expect(rememberDefault(row("Спикер 2"), {}, "Вы")).toBe(false);
});

test("правки исчезнувших строк отбрасываются", () => {
  const staged: Staged = { "Спикер 1": { kind: "merge", into: "Анна" }, "Спикер 2": { kind: "reset" } };
  expect(prune(staged, ["Спикер 1", "Спикер 2"])).toEqual({ "Спикер 2": { kind: "reset" } });
});

test("шаг истории словами", () => {
  expect(describeStep({
    id: "a", at: "2026-09-30T17:00:00", created_people: [],
    ops: [{ type: "rename", label: "Спикер 2", from: "Спикер 2", to: "Анна" },
      { type: "merge", label: "Спикер 3", from: "Спикер 3", to: "Анна", into: "Спикер 2" },
      { type: "reset", label: "Борис", from: "Борис", to: "Спикер 1" }],
    enrolled: [{ person: "Анна", sample_id: "x", label: "SPEAKER_01", created: true },
      { person: "Анна", sample_id: "y", label: "SPEAKER_02", created: false }],
  })).toBe("Спикер 2 → Анна, Спикер 3 объединён со спикером «Анна», Борис → без имени (Спикер 1) · голос запомнен: Анна");
});

test("шаг истории словами: реплики другому спикеру", () => {
  expect(describeStep({
    id: "b", at: "2026-09-30T17:00:00", created_people: [], enrolled: [],
    ops: [{ type: "relabel", from: ["Спикер 2", "Вы"], to: "Анна", segments: 4, turns: 3 }],
  })).toBe("3 реплики (Спикер 2, Вы) → Анна");
});
