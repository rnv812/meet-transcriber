import type { CSSProperties } from "react";
import type { Endpoint } from "../../lib/api";
import { initials, initialsFontSize, isUnnamed } from "../../lib/speakers";
import { Avatar } from "../../ui/Avatar";

/**
 * Знак участника встречи по макету: инициал (или фото) в кольце цвета спикера
 * (lib/tones) на --surface-2 — так инициал читается в обеих темах. Без цвета
 * («Спикер N») — кольцо цвета контура. Для диктора не виден: имя — рядом.
 */
export function PersonMark({ name, tone, hasAvatar = false, version, endpoint, size = 22 }: {
  name: string;
  tone?: string;
  hasAvatar?: boolean;
  version?: number;
  endpoint?: Endpoint | null;
  size?: number;
}) {
  const photo = hasAvatar && !isUnnamed(name) && !!endpoint;
  const style = {
    "--person": tone, width: size, height: size, fontSize: initialsFontSize(size, initials(name)),
  } as CSSProperties;
  return (
    <span className="person-mark" style={style} aria-hidden="true">
      {/* Фото — внутри кольца: на 4 px меньше знака. */}
      {photo ? <Avatar name={name} hasAvatar version={version} size={size - 4} endpoint={endpoint} /> : initials(name)}
    </span>
  );
}
