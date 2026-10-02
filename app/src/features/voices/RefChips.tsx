import { EyeOff } from "lucide-react";
import { refLabel } from "../../lib/profileAgent";
import type { Profile, ProfileRef } from "../../lib/types";

/**
 * Открыть встречу на реплике: номер сегмента, начало и спикер — карточка
 * сверяет их и не переходит к реплике, если она уже не та.
 */
export type OpenAt = (recording: string, segment: number, t?: number, speaker?: string) => void;

/** Подсказка у ссылки, чья реплика изменилась после профиля. */
export const STALE_TITLE = "Реплика изменилась после правки расшифровки — ссылка никуда не ведёт";

/**
 * Ссылки утверждения профиля на реплики: «Встреча · мм:сс»; цитата — в
 * подсказке. Реплику с тех пор изменили (разделили, перерасшифровали,
 * отдали другому спикеру) — ссылка неактивна: лучше никуда, чем на чужую
 * реплику.
 */
export function RefChips({ profile, refs, onOpenAt }: {
  profile: Profile; refs: ProfileRef[] | undefined; onOpenAt: OpenAt;
}) {
  if (!refs?.length) return null;
  return (
    <div className="profile__refs">
      {refs.slice(0, 3).map((r) => {
        const label = refLabel(profile, r);
        return r.stale ? (
          <button key={`${r.m}#${r.i}`} type="button" className="pref pref--stale" aria-disabled="true"
            title={`${STALE_TITLE}${r.q ? `. Было: «${r.q}»` : ""}`} aria-label={`Реплика изменилась: ${label}`}>
            {label} · реплика изменилась
          </button>
        ) : (
          <button key={`${r.m}#${r.i}`} type="button" className="pref" title={r.q ? `«${r.q}»` : undefined}
            aria-label={`Открыть реплику: ${label}`} onClick={() => onOpenAt(r.m, r.i, r.t)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

/** «Скрыть» у утверждения: не показывать и после обновления профиля. */
export function HideButton({ onHide }: { onHide: () => void }) {
  return (
    <button type="button" className="profile__hide" onClick={onHide} aria-label="Скрыть утверждение"
      title="Скрыть — не показывать это утверждение и после обновления профиля">
      <EyeOff size={13} strokeWidth={1.75} aria-hidden="true" />
    </button>
  );
}
