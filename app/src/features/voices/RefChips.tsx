import { refLabel } from "../../lib/profileAgent";
import type { Profile, ProfileRef } from "../../lib/types";

/** Ссылки утверждения профиля на реплики: «Встреча · мм:сс»; цитата — в подсказке. */
export function RefChips({ profile, refs, onOpenAt }: {
  profile: Profile; refs: ProfileRef[] | undefined; onOpenAt: (id: string, segment: number) => void;
}) {
  if (!refs?.length) return null;
  return (
    <div className="profile__refs">
      {refs.slice(0, 3).map((r) => {
        const label = refLabel(profile, r);
        return (
          <button key={`${r.m}#${r.i}`} type="button" className="pref" title={r.q ? `«${r.q}»` : undefined}
            aria-label={`Открыть реплику: ${label}`} onClick={() => onOpenAt(r.m, r.i)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}
