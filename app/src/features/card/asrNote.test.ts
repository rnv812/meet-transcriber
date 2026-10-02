import { systemAudioText } from "./asrNote";

test("про разрешение — только когда дело было в нём", () => {
  expect(systemAudioText("missing", "permission"))
    .toBe("Звук собеседников не записан: у Meet не было разрешения «Запись экрана» — записан только микрофон");
  expect(systemAudioText("missing", "failed"))
    .toBe("Звук собеседников не записан: помощник записи системного звука не запустился — записан только микрофон");
  expect(systemAudioText("missing", "helper")).toContain("переустановите приложение");
  expect(systemAudioText("missing", "unsupported")).toContain("macOS 13");
  expect(systemAudioText("missing", null)).toBe("Звук собеседников не записан — записан только микрофон");
  expect(systemAudioText("partial", "permission"))
    .toBe("Звук собеседников записан не всю встречу: у Meet не было разрешения «Запись экрана»");
  expect(systemAudioText("partial", null)).toBe("Звук собеседников записан не всю встречу");
  for (const text of [systemAudioText("missing", "failed"), systemAudioText("missing", "helper")]) {
    expect(text).not.toContain("разрешения");
  }
  expect(systemAudioText(null, "permission")).toBeNull();
  expect(systemAudioText(undefined)).toBeNull();
});
