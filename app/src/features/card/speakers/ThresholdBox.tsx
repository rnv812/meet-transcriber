/**
 * «Порог узнавания голоса» этой встречи: насколько голос спикера должен быть
 * похож на образец человека из базы, чтобы получить его имя. Сдвиг ползунка
 * сразу показывает, чьи имена изменятся (по уже посчитанным голосам — без
 * повторной обработки); «Применить» — шаг истории встречи. Названных вручную
 * спикеров порог не трогает.
 */

import { useEffect, useState } from "react";
import { applyThreshold, thresholdPlan, type Endpoint } from "../../../lib/api";
import { errorText } from "../../../lib/format";
import type { SpeakersView, ThresholdPlan } from "../../../lib/types";
import { Button } from "../../../ui/Button";
import { HelpTip, TipLine } from "../../../ui/HelpTip";

const PLAN_DELAY_MS = 250;
const pct = (x: number) => `${Math.round(x * 100)}%`;

export function ThresholdBox({ endpoint, recordingId, own, fallback, busy, onApplied }: {
  endpoint: Endpoint;
  recordingId: string;
  /** Порог этой встречи; null — общий. */
  own: number | null;
  /** Общий порог из настроек. */
  fallback: number;
  busy: boolean;
  onApplied: (view: SpeakersView) => void;
}) {
  const current = own ?? fallback;
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(current);
  const [plan, setPlan] = useState<ThresholdPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => { setValue(current); }, [current]);
  useEffect(() => {
    if (!open) return;
    let live = true;
    const timer = setTimeout(() => {
      thresholdPlan(endpoint, recordingId, value)
        .then((p) => { if (live) { setPlan(p); setError(null); } })
        .catch((e) => { if (live) setError(errorText(e)); });
    }, PLAN_DELAY_MS);
    return () => { live = false; clearTimeout(timer); };
  }, [open, value, endpoint, recordingId, current]);

  const apply = async () => {
    setWorking(true);
    setError(null);
    try {
      onApplied(await applyThreshold(endpoint, recordingId, value));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setWorking(false);
    }
  };

  const changes = plan && plan.value === value ? plan.changes : null;
  return (
    <section className="spk-thr" aria-label="Порог узнавания голоса">
      <button type="button" className="spk-hist__toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "▾" : "▸"} Порог узнавания голоса: {pct(current)} {own === null ? "(общий)" : "(для этой встречи)"}
      </button>
      {open && (
        <div className="spk-thr__body">
          <div className="spk-thr__row">
            <input type="range" min={50} max={95} step={1} value={Math.round(value * 100)}
              aria-label="Порог узнавания голоса, %" aria-valuetext={pct(value)}
              onChange={(e) => setValue(Number(e.target.value) / 100)} />
            <span className="num spk-thr__value">{pct(value)}</span>
            <HelpTip label="Что такое порог узнавания" title="Порог узнавания голоса">
              <TipLine>Насколько голос спикера должен быть похож на образец человека из базы голосов, чтобы спикер получил его имя.</TipLine>
              <TipLine>Ниже порог — узнаётся больше людей, но чаще бывают ошибки. Выше — имена надёжнее, но больше спикеров остаётся без имени.</TipLine>
              <TipLine>Здесь порог меняется только для этой встречи; общий порог — в настройках, раздел «Распознавание». Спикеров, названных вручную, он не меняет.</TipLine>
            </HelpTip>
          </div>
          {error && <div className="card__error" role="alert">{error}</div>}
          {changes && (
            <div className="spk-thr__plan" aria-live="polite">
              {changes.length === 0 ? (
                <span className="muted">Имена спикеров не изменятся.</span>
              ) : (
                <ul>
                  {changes.map((c) => (
                    <li key={c.label}>
                      {c.label} → {c.to ?? "без имени"}
                      {c.best && c.score !== null && <span className="muted num"> (похож на {c.best}: {pct(c.score)})</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="spk__actions">
            {value !== fallback && (
              <Button onClick={() => setValue(fallback)} disabled={busy || working}>Как в настройках</Button>
            )}
            <Button variant="primary" onClick={() => void apply()} disabled={busy || working || value === current && !changes?.length}>
              Применить
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
