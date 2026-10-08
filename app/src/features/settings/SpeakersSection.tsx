/**
 * «Спикеры» (0.4): всё про разделение на спикеров и узнавание голосов —
 * токен Hugging Face (прежде в «Движке и моделях»), «Мой голос» (прежде в
 * «Звуке»), порог узнавания и одновременная речь (прежде в «Распознавании»).
 *
 * Токен и образец голоса — мгновенные действия (свои запросы резиденту), порог
 * и одновременная речь — черновик `asr`. Образец пишется с микрофона из
 * черновика «Звука».
 */

import type { Endpoint } from "../../lib/api";
import { Slider } from "../../ui/Slider";
import { SLIDER_WIDTH } from "./fields";
import { HfTokenRow } from "./HfTokenRow";
import { OwnerVoiceRow } from "./OwnerVoice";
import { Row, SettingsCard, Switch, type Raw, type SetFn } from "./Section";
import { pickedName } from "./SoundSection";
import { VoiceThresholdTip } from "./tips";

export function SpeakersSection({ draft, set, endpoint }: { draft: Raw; set: SetFn; endpoint: Endpoint }) {
  const v = (k: string) => draft.asr?.[k];
  const threshold = Math.round(Number(v("voice_threshold") ?? 0.75) * 100);
  return (
    <>
      <SettingsCard title="Разделение на спикеров">
        <HfTokenRow endpoint={endpoint} />
        <Switch label="Отмечать одновременную речь" hint="Реплики, где говорят одновременно, помечаются «нахлёст»: спикер в них может быть определён неточно"
          value={Boolean(v("overlap"))} onChange={(x) => set("asr", "overlap", x)} />
      </SettingsCard>
      <SettingsCard title="Узнавание голосов">
        <OwnerVoiceRow endpoint={endpoint} device={pickedName(draft.recording?.mic_device)} />
        <Row label="Порог узнавания голоса" htmlFor="asr-voice-threshold" help={<VoiceThresholdTip />}
          hint="Насколько голос должен быть похож на образец из базы голосов, чтобы спикер получил имя">
          <Slider id="asr-voice-threshold" min={50} max={95} value={threshold} width={SLIDER_WIDTH}
            format={(x) => `${x}%`} onChange={(x) => set("asr", "voice_threshold", x / 100)} />
        </Row>
      </SettingsCard>
    </>
  );
}
