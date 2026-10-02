/**
 * Пометки транскрипта о распознавании (`asr_note`): встречу распознал не тот
 * движок, что выбран в настройках. Тихая строка в карточке, без действий.
 */
export const ASR_NOTES: Record<string, string> = {
  // GigaAM распознаёт только русский: такую запись расшифровал Whisper.
  not_russian: "Запись не на русском — использован Whisper",
  // GigaAM не скачалась или не загрузилась (нет сети, файл повреждён); причина — в журнале.
  gigaam_failed: "GigaAM недоступна — использован Whisper",
};

export const asrNoteText = (note: string | null | undefined): string | null =>
  (note && ASR_NOTES[note]) || null;

/** macOS: почему в записи нет голосов собеседников (`system_audio_reason` в meta.json). */
export const SYSTEM_AUDIO_REASONS: Record<string, string> = {
  permission: "у Meet не было разрешения «Запись экрана»",
  helper: "не найден помощник записи системного звука — переустановите приложение",
  unsupported: "запись системного звука требует macOS 13 или новее",
  failed: "помощник записи системного звука не запустился",
};

/**
 * Пометка карточки про звук собеседников (`system_audio`): «missing» — не
 * записан вовсе, «partial» — не всю встречу. Причину называем, только если она
 * известна: о разрешении — лишь когда дело правда было в нём.
 */
export function systemAudioText(
  state: string | null | undefined, reason?: string | null,
): string | null {
  const why = reason ? SYSTEM_AUDIO_REASONS[reason] : undefined;
  if (state === "missing") {
    return `Звук собеседников не записан${why ? `: ${why}` : ""} — записан только микрофон`;
  }
  if (state === "partial") {
    return `Звук собеседников записан не всю встречу${why ? `: ${why}` : ""}`;
  }
  return null;
}
