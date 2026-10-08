/**
 * Шаг «Условия» — перед «Готово»: тот же текст, что у заслонки условий
 * (features/legal/TermsGate), но внутри мастера, чтобы новый человек увидел его
 * один раз. Шаг идёт после шагов с резидентом, поэтому согласие сохраняется
 * сразу (`acceptTerms`); заслонка, если открыта, уходит по его событию. Уже
 * принятые условия мастер этот шаг пропускает (Wizard).
 */

import { useState } from "react";
import type { Endpoint } from "../../lib/api";
import { errorText } from "../../lib/format";
import { acceptTerms, TERMS_CHECKBOX, TERMS_INTRO } from "../../lib/terms";
import { Button } from "../../ui/Button";
import { TermsText } from "../legal/TermsText";

export function StepTerms({ endpoint, onNext }: { endpoint: Endpoint; onNext: () => void }) {
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = async () => {
    if (!agreed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await acceptTerms(endpoint);
    } catch (cause) {
      setError(`Не удалось сохранить согласие: ${errorText(cause)}`);
      setBusy(false);
      return;
    }
    onNext();
  };

  return (
    <>
      <p className="wizard__lead">{TERMS_INTRO}</p>
      {/* Заголовок шага — h1, разделы условий — h2. */}
      <TermsText headingLevel={2} />
      <label className="check-row">
        <input type="checkbox" className="cb" checked={agreed}
          onChange={(e) => { setAgreed(e.target.checked); setError(null); }} />
        <span>{TERMS_CHECKBOX}</span>
      </label>
      {error && <p className="wizard__error" role="alert">{error}</p>}
      <div className="wizard__bar">
        <Button variant="primary" disabled={!agreed} busy={busy} onClick={() => void accept()}>Далее</Button>
      </div>
    </>
  );
}
