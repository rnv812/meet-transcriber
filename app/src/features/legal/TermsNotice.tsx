/**
 * Компактная пометка «Примите условия, чтобы продолжить» для панелей без
 * заслонки (трей, панель ассистента): кнопка открывает окно Meet, где
 * `TermsGate` и спросит. Выноска Aurora `callout--warn` в компактном виде.
 * `id` — для `aria-describedby` закрытых ею кнопок.
 */

import { ShieldAlert } from "lucide-react";

import { TERMS_NEEDED } from "../../lib/terms";
import { Button } from "../../ui/Button";
import { Icon } from "../../ui/Icon";
import "./terms.css";

export function TermsNotice({ id, className, onOpen }: {
  id?: string;
  className?: string;
  /** Открыть окно Meet (та же команда оболочки, что у «Открыть Meet»). */
  onOpen: () => void;
}) {
  return (
    <div id={id} className={`callout callout--warn terms-notice${className ? ` ${className}` : ""}`}
      role="status">
      <Icon as={ShieldAlert} size="sm" className="ic" />
      <div className="terms-notice__body">
        <span className="terms-notice__text">{TERMS_NEEDED}</span>
        <Button size="xs" variant="secondary" onClick={onOpen}>Открыть Meet</Button>
      </div>
    </div>
  );
}
