/**
 * Куда деть фокус, когда элемент с ним исчез (удалили группу из её меню, встреча
 * ушла из открытой группы) или когда действие вернуло что-то на место («Отменить»).
 * Список перерисовывается не сразу после ответа резидента, поэтому — несколько
 * попыток с паузами.
 */

const TRIES_MS = [0, 60, 200, 500];

/** Фокус потерян: на `<body>`, нигде или на элементе, которого уже нет в документе. */
export const focusLost = () => {
  const at = document.activeElement;
  return !at || at === document.body || !at.isConnected;
};

/**
 * Если фокус потерян (или стоит там, где ему больше не место, — `stale`), поставить его на
 * `target` (как только он есть).
 */
export function rescueFocus(target: () => HTMLElement | null, stale?: () => boolean): void {
  // Один раз: дальше фокусом распоряжается человек (и «Отменить», вернувшее группу).
  let done = false;
  for (const ms of TRIES_MS) {
    setTimeout(() => {
      if (done || (!focusLost() && !stale?.())) return;
      const el = target();
      if (!el) return;
      done = true;
      el.focus();
    }, ms);
  }
}

/** Поставить фокус на `target`, как только он появится (вернувшаяся группа или встреча). */
export function focusWhenReady(target: () => HTMLElement | null): void {
  let done = false;
  for (const ms of TRIES_MS) {
    setTimeout(() => {
      if (done) return;
      const el = target();
      if (!el) return;
      done = true;
      el.focus();
    }, ms);
  }
}

/** Фокус — в строке панели с ключом `key` (у удалённой группы со встречами строка остаётся «без названия»). */
export const focusInRow = (key: string) => Boolean(document.activeElement?.closest(`[data-row-key="${key}"]`));
/** «Все записи» в левой панели (есть и в полосе значков). */
export const allRow = () => document.querySelector<HTMLElement>('[data-scope-key="all"]');
/** Строка группы в левой панели. */
export const groupRow = (id: string) => document.querySelector<HTMLElement>(`[data-scope-key="${id}"]`);
/** Строка встречи в списке. */
export const meetingRow = (id: string) =>
  document.querySelector<HTMLElement>(`[data-rec-id="${id}"] > .rec-item__main`);
/** Поле поиска списка (после объединения с search-ui — combobox). */
export const listSearch = () =>
  document.querySelector<HTMLElement>(".rec-list input[type=search], .rec-list [role=combobox]");
