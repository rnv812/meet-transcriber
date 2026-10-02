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
