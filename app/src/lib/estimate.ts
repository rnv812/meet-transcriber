/**
 * Оценка времени расшифровки — копия `meet.engine.estimate_text`: мастер
 * показывает её до того, как появится резидент (движка ещё нет).
 *
 * IMPORTANT: коэффициенты и округление — как в Python (`SPEED_FACTOR`,
 * банковский `round()`); тест сверяет строки с выводом Python.
 */

export type Profile = "cuda" | "cpu";

/** Время расшифровки / длительность записи (замер 30.09.2026). */
export const SPEED_FACTOR: Record<Profile, number> = { cuda: 0.22, cpu: 1.26 };

/** `round()` Python: ровно половина — к чётному. */
export function pyRound(x: number): number {
  const floor = Math.floor(x);
  const frac = x - floor;
  if (frac > 0.5) return floor + 1;
  if (frac < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

const minutes = (seconds: number) => Math.max(1, pyRound(seconds / 60));

/** «60 мин встречи ≈ 13 мин обработки». */
export function estimateText(durationS: number, profile: Profile): string {
  const processing = durationS * (SPEED_FACTOR[profile] ?? SPEED_FACTOR.cpu);
  return `${minutes(durationS)} мин встречи ≈ ${minutes(processing)} мин обработки`;
}
