/**
 * Точная остановка фрагмента записи на секунде `until`.
 *
 * `timeupdate` приходит лишь ~4 раза в секунду: остановка по нему опаздывает
 * до 0,25 с — в образец спикера попадает начало чужой фразы. Поэтому —
 * таймер на `(until − currentTime) / playbackRate`:
 *
 * - ставится, пока звук идёт (`playing`), и переставляется при смене скорости
 *   (`ratechange`) и после перемотки (`seeked`);
 * - снимается на паузе и пока ждём данных (`pause`, `waiting`): пока звука
 *   нет, время записи не идёт;
 * - `timeupdate` перепроверяет: конец пройден — стоп сразу, иначе таймер
 *   заново по фактической позиции (поправка на дрейф).
 *
 * Отменять перемотку человеком — дело вызывающего (вернуть отмену и позвать
 * её): событие `seeked` от собственной перемотки к началу фрагмента от
 * чужой не отличить. `onStop` зовётся один раз; после него и после отмены
 * обработчики сняты.
 */
export function stopAt(media: HTMLMediaElement, until: number, onStop: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let done = false;
  const clear = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  const arm = () => {
    clear();
    if (done || media.paused) return;
    const rate = media.playbackRate > 0 ? media.playbackRate : 1;
    const left = (until - media.currentTime) / rate;
    if (left <= 0) { finish(); return; }
    // Вверх до целой миллисекунды: 16,2 − 12 = 4,1999… — не остановиться на долю раньше конца.
    timer = setTimeout(finish, Math.ceil(left * 1000));
  };
  const check = () => {
    if (media.currentTime >= until) finish();
    else arm();
  };
  const ARM = ["playing", "seeked", "ratechange"] as const;
  const HALT = ["pause", "waiting"] as const;
  function cancel() {
    if (done) return;
    done = true;
    clear();
    for (const e of ARM) media.removeEventListener(e, arm);
    for (const e of HALT) media.removeEventListener(e, clear);
    media.removeEventListener("timeupdate", check);
  }
  function finish() {
    if (done) return;
    cancel();
    onStop();
  }
  for (const e of ARM) media.addEventListener(e, arm);
  for (const e of HALT) media.addEventListener(e, clear);
  media.addEventListener("timeupdate", check);
  arm();
  return cancel;
}
