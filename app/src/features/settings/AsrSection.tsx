/**
 * «Распознавание»: устройство, движок и модель для видеокарты и процессора
 * (AsrChoice), язык речи; в «Тонкой настройке» — уточнение времени слов. Порог узнавания голоса и
 * одновременная речь — в «Спикерах», термины и исправления — в «Словаре» (0.4).
 */

import { type Endpoint, GIGAAM_PREFIX } from "../../lib/api";
import { AsrChoice, backendOf } from "./AsrChoice";
import { TextRow } from "./fields";
import { FineTuning, SettingsCard, Switch, type Raw, type SetFn } from "./Section";
import { AsrModelTip } from "./tips";

/**
 * Где используется модель по черновику «Распознавания» — для строки модели в
 * «Движке и моделях»: id → «видеокарта», «процессор — записи не на русском», …
 */
export function modelUsage(draft: Raw): Record<string, string[]> {
  const asr = draft.asr ?? {};
  const out: Record<string, string[]> = {};
  const add = (id: unknown, text: string) => {
    if (typeof id === "string" && id) (out[id] ??= []).push(text);
  };
  for (const [device, title, key] of [["cuda", "видеокарта", "model"], ["cpu", "процессор", "cpu_model"]] as const) {
    if (backendOf(asr, device) === "gigaam") {
      add(GIGAAM_PREFIX + String(asr.gigaam_model ?? "v3_e2e_rnnt"), title);
      add(asr[key], `${title} — записи не на русском`);
    } else {
      add(asr[key], title);
    }
  }
  return out;
}

export function AsrSection({ draft, saved, set, endpoint, onOpenEngine }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint; onOpenEngine: () => void;
}) {
  const v = (k: string) => draft.asr?.[k];
  return (
    <>
      <SettingsCard title="Устройство и модель">
        <AsrChoice draft={draft} saved={saved} set={set} endpoint={endpoint} help={<AsrModelTip />}
          onOpenEngine={onOpenEngine} />
      </SettingsCard>
      <SettingsCard title="Речь">
        <TextRow id="asr-language" label="Язык речи" short
          hint="Код языка, например ru или en; auto — определить по записи. GigaAM понимает только русский"
          value={String(v("language") ?? "ru")} onChange={(x) => set("asr", "language", x)} />
      </SettingsCard>
      <FineTuning>
        <Switch label="Уточнять время каждого слова" hint="Точнее границы реплик; расшифровка занимает немного больше времени. После GigaAM не нужно: время слов у него своё"
          value={Boolean(v("align"))} onChange={(x) => set("asr", "align", x)} />
      </FineTuning>
    </>
  );
}
