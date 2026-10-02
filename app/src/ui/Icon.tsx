import type { LucideIcon, LucideProps } from "lucide-react";

/**
 * Значки окна — только lucide, три размера и один штрих: 14 — в строках и
 * мелких кнопках, 16 — в обычных кнопках и меню, 20 — в заголовках и пустых
 * состояниях. Свои SVG и символы Юникода (× ▾ ▸ ⋯ ✓) вместо значков не
 * используются: у них другая толщина и базовая линия.
 */
export const ICON = { sm: 14, md: 16, lg: 20 } as const;
export const ICON_STROKE = 1.75;
export type IconSize = keyof typeof ICON;

export function Icon({ as: Glyph, size = "md", ...rest }: { as: LucideIcon; size?: IconSize } & Omit<LucideProps, "size" | "ref">) {
  return <Glyph size={ICON[size]} strokeWidth={ICON_STROKE} aria-hidden="true" focusable="false" {...rest} />;
}
