import { asrNoteText, systemAudioText } from "./asrNote";

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

test("пометка о распознавании: видеокарта без библиотек CUDA — процессор", () => {
  expect(asrNoteText("cuda_failed")).toBe("Видеокарта недоступна — распознано на процессоре");
  expect(asrNoteText("gigaam_failed")).toBe("GigaAM недоступна — использован Whisper");
  expect(asrNoteText("неизвестно")).toBeNull();
  expect(asrNoteText(null)).toBeNull();
});

test("пометка о распознавании: движок с видеокартой, а распознал процессор — с причиной", () => {
  expect(asrNoteText("no_gpu")).toBe("Распознано на процессоре: видеокарта NVIDIA не найдена");
  expect(asrNoteText("no_cuda_libs")).toBe("Распознано на процессоре: не найдены библиотеки CUDA");
});
