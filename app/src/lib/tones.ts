/**
 * Цвет спикера встречи — один на всю карточку: кольцо чипа в шапке, точка у
 * имени в ленте, кольцо аватара и полоса доли в панели «Спикеры».
 *
 * У человека из базы голосов — его цвет; у названного, но не из базы, — цвет
 * палитры данных Aurora по месту в шапке (по кругу); у «Спикер N» и
 * «Неизвестный» цвета нет — они серые везде (цвет — только у того, кого знают).
 */

import { NO_SPEAKER, isUnnamed } from "./speakers";

/** Кольца участников без своего цвета — палитра данных Aurora, по порядку в шапке. */
export const SPEAKER_RINGS = ["var(--data-1)", "var(--data-2)", "var(--data-3)", "var(--data-4)"] as const;

type Colored = { name: string; color?: string | null };

/** Цвет спикера `name`, стоящего `index`-м в шапке; undefined — без цвета. */
export function speakerTone(name: string, index: number, person?: Colored | null): string | undefined {
  if (isUnnamed(name) || name === NO_SPEAKER) return undefined;
  if (person?.color) return person.color;
  return SPEAKER_RINGS[((index % SPEAKER_RINGS.length) + SPEAKER_RINGS.length) % SPEAKER_RINGS.length];
}

/** Цвета спикеров встречи (по порядку в шапке): имя → цвет; без цвета — нет в карте. */
export function speakerTones(speakers: readonly string[], people: readonly Colored[]): Map<string, string> {
  const out = new Map<string, string>();
  speakers.forEach((name, i) => {
    const tone = speakerTone(name, i, people.find((p) => p.name === name));
    if (tone) out.set(name, tone);
  });
  return out;
}
