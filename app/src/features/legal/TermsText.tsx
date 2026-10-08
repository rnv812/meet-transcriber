/**
 * Текст условий использования (lib/terms.ts) — разделы с заголовками. Общий
 * для окна-заслонки (TermsGate) и шага мастера первого запуска.
 */

import { TERMS_SECTIONS } from "../../lib/terms";
import "./terms.css";

export function TermsText({ headingLevel = 3, className = "" }: {
  /** Уровень заголовков разделов: у заслонки — 3 (заголовок окна — 2). */
  headingLevel?: 2 | 3 | 4;
  className?: string;
}) {
  const Heading = `h${headingLevel}` as "h3";
  return (
    <div className={`terms-text ${className}`.trim()}>
      {TERMS_SECTIONS.map((s) => (
        <section key={s.title} className="terms-text__section">
          <Heading className="terms-text__title">{s.title}</Heading>
          {s.paragraphs.map((p) => <p key={p} className="terms-text__p">{p}</p>)}
        </section>
      ))}
    </div>
  );
}
