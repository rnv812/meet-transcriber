/**
 * Условия использования: один раз и у новых, и у обновившихся пользователей.
 *
 * Принятая версия хранится в настройках резидента (`ui.terms_accepted`, по
 * умолчанию "" — не принимали). Сменился смысл текста — поднять
 * TERMS_VERSION: окно спросит снова. Правка опечатки версию не меняет.
 */

import { type Endpoint, patchSettings } from "./api";

export const TERMS_VERSION = "2026-10-08";

export const TERMS_TITLE = "Прежде чем продолжить";
export const TERMS_INTRO = "Коротко о том, куда уходят данные и за что отвечаете вы.";
export const TERMS_CHECKBOX = "Я прочитал(а) и принимаю эти условия";
/** Компактная пометка панелей трея и ассистента: условия не приняты, кнопки записи закрыты. */
export const TERMS_NEEDED = "Примите условия, чтобы продолжить";

export type TermsSection = { title: string; paragraphs: readonly string[] };

export const TERMS_SECTIONS: readonly TermsSection[] = [
  {
    title: "Данные и провайдеры моделей",
    paragraphs: [
      "Запись и расшифровка идут на этом компьютере. Итоги, анализ встречи, улучшение расшифровки, "
        + "названия, категории и ассистент отправляют текст встречи, вложения и ваши вопросы выбранному "
        + "провайдеру модели: Anthropic (через Claude Code), OpenAI (через Codex), OpenCode и его "
        + "провайдерам или OpenAI-совместимому серверу. Они обрабатывают данные по своим условиям — "
        + "Meet на это не влияет.",
      "Не отправляйте то, что нельзя передавать по договорам, NDA и правилам вашей организации. "
        + "Локальная модель на этом компьютере данные никуда не передаёт. Провайдера и то, какие "
        + "функции работают через модель, вы выбираете в настройках «ИИ».",
    ],
  },
  {
    title: "Запись встреч",
    paragraphs: [
      "Вы отвечаете за то, чтобы запись была законной: предупредите участников и получите их согласие, "
        + "где этого требуют закон или правила организации. Автозапись начинает запись звонка сама, "
        + "без нажатия кнопки. Записи, расшифровки и база голосов для узнавания спикеров хранятся "
        + "на этом компьютере — берегите их, как другие рабочие документы.",
    ],
  },
  {
    title: "Без гарантий",
    paragraphs: [
      "Расшифровка, итоги и ответы ИИ могут содержать ошибки — проверяйте важное. Программа "
        + "предоставляется «как есть», без каких-либо гарантий (лицензия Apache 2.0).",
    ],
  },
];

/** Приняты ли условия текущей версии — по ответу `GET /settings`. */
export function termsAccepted(settings: Record<string, unknown> | null | undefined): boolean {
  const ui = settings?.ui as { terms_accepted?: unknown } | undefined;
  return ui?.terms_accepted === TERMS_VERSION;
}

/** Событие окна: условия приняты (из окна-заслонки или шага мастера). */
export const TERMS_ACCEPTED_EVENT = "meet:terms-accepted";

/** Резидент ответил на PATCH, но отметки в ответе нет: его движок не знает `ui.terms_accepted`. */
export const TERMS_NOT_SAVED = "Отметка о принятии не сохранилась: работает движок Meet старой версии. "
  + "Закройте Meet из значка в трее и откройте снова — движок обновится";

/** Принять условия: `PATCH /settings {"ui": {"terms_accepted": TERMS_VERSION}}`.
 *  Ошибку резидента пробрасывает — показать её решает вызывающий. Ответ без
 *  отметки (0.5: движок старой версии молча отбрасывал ключ, и окно спрашивало
 *  при каждом запуске) — тоже ошибка. */
export async function acceptTerms(endpoint: Endpoint): Promise<void> {
  const reply = await patchSettings(endpoint, { ui: { terms_accepted: TERMS_VERSION } });
  if (reply && typeof reply === "object" && "settings" in reply && !termsAccepted(reply.settings)) {
    console.warn("ui.terms_accepted: резидент не сохранил отметку — движок старой версии?");
    throw new Error(TERMS_NOT_SAVED);
  }
  window.dispatchEvent(new Event(TERMS_ACCEPTED_EVENT));
}
