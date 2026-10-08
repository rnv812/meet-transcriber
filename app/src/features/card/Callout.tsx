/**
 * Выноска карточки записи — Aurora `.callout` (значок, текст, кнопки справа):
 * выгрузка в базу знаний, сбой перерасшифровки, новое разделение на спикеров,
 * нет токена Hugging Face, заметки распознавания, предложения ИИ.
 *
 * Тон: `note` — сведения, `tip` — совет, `ok` — сделано, `warn` — стоит
 * поправить, `err` — не удалось; `ai` — предложение модели (плоско,
 * тонкий акцент слева, значок ✦ — 0.5). Роль и подпись — как у прежних строк карточки:
 * тесты и экранный диктор находят выноску по ним.
 */

import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, Info, Lightbulb, Sparkles, TriangleAlert, type LucideIcon } from "lucide-react";
import { Icon } from "../../ui/Icon";

export type CalloutTone = "note" | "tip" | "ok" | "warn" | "err" | "ai";

const ICON: Record<CalloutTone, LucideIcon> = {
  note: Info, tip: Lightbulb, ok: CircleCheck, warn: TriangleAlert, err: CircleAlert, ai: Sparkles,
};

export function Callout({ tone, role = "status", label, actions, children, className = "" }: {
  tone: CalloutTone;
  /** `status` — по умолчанию; `note` — тихая пометка; `region` — блок с решением (предложение). */
  role?: "status" | "note" | "region";
  label?: string;
  /** Кнопки выноски — справа от текста (в узкой карточке — под ним). */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const look = tone === "ai" ? "card__callout--ai" : `callout--${tone}`;
  return (
    <div className={`callout ${look} card__callout ${className}`.trim()} role={role} aria-label={label}>
      <Icon as={ICON[tone]} size="md" className="ic" />
      <div className="card__callout-body">
        <div className="card__callout-text">{children}</div>
        {actions && <div className="card__callout-actions">{actions}</div>}
      </div>
    </div>
  );
}
