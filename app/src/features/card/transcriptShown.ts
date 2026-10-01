import { createContext } from "react";

/**
 * Растёт каждый раз, когда вкладка «Расшифровка» становится видимой (CardTabs).
 * Скрытая панель (`hidden`) прокручивать бесполезно: TranscriptView откладывает
 * прокрутку к совпадению до следующего показа. Вне вкладок — 0.
 */
export const TranscriptShown = createContext(0);
