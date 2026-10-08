/**
 * «Словарь» (0.4, прежде в «Распознавании»): термины распознавания (свой файл
 * резидента, «Сохранить термины» — сразу) и исправления для будущих
 * расшифровок (черновик `asr.replacements`).
 */

import type { Endpoint } from "../../lib/api";
import { HotwordsEditor } from "./HotwordsEditor";
import { ReplacementsEditor } from "./ReplacementsEditor";
import { SettingsCard, type Raw, type SetFn } from "./Section";

export function DictionarySection({ draft, set, endpoint }: { draft: Raw; set: SetFn; endpoint: Endpoint }) {
  return (
    <>
      <SettingsCard title="Термины">
        <HotwordsEditor endpoint={endpoint} />
      </SettingsCard>
      <SettingsCard title="Исправления">
        <ReplacementsEditor value={draft.asr?.replacements} onChange={(x) => set("asr", "replacements", x)} />
      </SettingsCard>
    </>
  );
}
