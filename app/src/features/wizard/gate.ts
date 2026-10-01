/**
 * Когда мастер первого запуска открывается сам — правило «не мешать»: только
 * пока движка нет, резидента нет и «Пропустить» не нажимали. Отсутствие токена
 * Hugging Face мастер не вызывает (на это — подсказка в карточке записи).
 *
 * Флаг «пройден» живёт в настройках резидента (`ui.wizard_done`), но без
 * движка резидента нет — поэтому он дублируется в localStorage окна, а в
 * настройки дописывается, когда резидент появится.
 */

export const LOCAL_DONE_KEY = "meet.wizard_done";

export function shouldAutoShow({ installed, reachable, wizardDone }: {
  installed: boolean; reachable: boolean; wizardDone: boolean;
}): boolean {
  return !installed && !reachable && !wizardDone;
}

/** Хранилище недоступно (приватный режим, запрет) — считаем «не пройден». */
export function readLocalDone(): boolean {
  try {
    return localStorage.getItem(LOCAL_DONE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeLocalDone(): void {
  try {
    localStorage.setItem(LOCAL_DONE_KEY, "1");
  } catch {
    /* не сохранилось — флаг остаётся у оболочки и в настройках резидента */
  }
}

/**
 * Сколько ГБ не хватает (до десятых), хватает или не узнать — null. Считаем в
 * десятых целыми числами: 5 − 2,3 в плавающей точке — 2,7000000000000002.
 */
export function freeSpaceShortfall(needsGb: number, freeGb: number | null): number | null {
  if (freeGb === null) return null;
  const tenths = Math.ceil(needsGb * 10 - 1e-9) - Math.floor(freeGb * 10 + 1e-9);
  return tenths > 0 ? tenths / 10 : null;
}

/** «2,7» — гигабайты по-русски, до десятых. */
export function gb(value: number): string {
  return String(Math.round(value * 10) / 10).replace(".", ",");
}

/** Буква диска пути («C:»); не путь Windows — null. */
export function driveOf(path: string): string | null {
  return /^([A-Za-z]:)/.exec(path)?.[1]?.toUpperCase() ?? null;
}
