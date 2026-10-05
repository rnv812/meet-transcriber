/**
 * Шаг «Ваш голос» (необязательный, после «Запись»): прочитать вслух текст
 * ~25 с — по отпечатку голоса расшифровка отличит вас от людей рядом, которых
 * слышит ваш микрофон. Без модели разделения на спикеров или токена Hugging
 * Face записать нечем — «Позже, в настройках».
 */

import type { Endpoint } from "../../lib/api";
import { Button } from "../../ui/Button";
import { OwnerVoiceRecorder, useOwnerVoice } from "../settings/OwnerVoice";

export function StepVoice({ endpoint, onNext, pollMs }: { endpoint: Endpoint; onNext: () => void; pollMs?: number }) {
  const voice = useOwnerVoice(endpoint, pollMs);
  const { status } = voice;
  const done = status?.take?.state === "done";
  const later = <Button onClick={onNext} disabled={voice.busy}>Позже, в настройках</Button>;

  if (!status) {
    return voice.error ? (
      <>
        <p className="error">{voice.error}</p>
        <div className="wizard__bar">{later}</div>
      </>
    ) : <p className="muted wizard__wait">Загрузка…</p>;
  }
  if (!status.ready) {
    return (
      <>
        <p className="muted">
          Образец голоса помогает отличать вас от людей, которые сидят рядом и попадают в ваш микрофон.
          Сейчас его не записать: {status.reason ?? "нет модели разделения на спикеров"}.
        </p>
        <p className="wizard__hint">Записать его можно позже: Настройки → Звук → «Мой голос».</p>
        <div className="wizard__bar">{later}</div>
      </>
    );
  }
  return (
    <>
      <p className="muted">
        По отпечатку голоса расшифровка отличит вас от людей, которые сидят рядом и попадают в ваш микрофон.
        Шаг можно пропустить и записать голос позже в настройках.
      </p>
      <OwnerVoiceRecorder voice={voice} device={null} />
      <div className="wizard__bar">
        {done ? <Button variant="primary" onClick={onNext}>Далее</Button> : later}
      </div>
    </>
  );
}
