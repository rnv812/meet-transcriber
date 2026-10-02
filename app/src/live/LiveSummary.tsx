/**
 * «Сводка»: тема, главное, решения, задачи, открытые вопросы — как их ведёт
 * ассистент. Пункты обновляются на месте (ключ — их id), новые встают в
 * конец своего раздела; изменённое ненадолго подсвечено (`fresh`).
 */

import type { LiveSummary as Summary } from "../lib/types";
import { summaryIsEmpty } from "./liveModel";
import "./live.css";

const SECTIONS = [
  ["points", "Главное"],
  ["decisions", "Решения"],
  ["open_questions", "Открытые вопросы"],
] as const;

export function LiveSummary({ summary, fresh }: { summary: Summary; fresh: Set<string> }) {
  if (summaryIsEmpty(summary)) {
    return <p className="live-empty muted">Сводка появится через минуту-другую разговора.</p>;
  }
  const mark = (id: string) => (fresh.has(id) ? " is-fresh" : "");
  return (
    <div className="live-summary">
      {summary.topic && (
        <p className={`live-summary__topic${mark("topic")}`}><span className="muted">Тема:</span> {summary.topic}</p>
      )}
      {SECTIONS.slice(0, 2).map(([key, title]) => <Section key={key} title={title} items={summary[key]} mark={mark} />)}
      {summary.tasks.length > 0 && (
        <section className="live-summary__section" aria-label="Задачи">
          <h4 className="live-summary__title">Задачи</h4>
          <ul className="live-summary__list">
            {summary.tasks.map((t) => (
              <li key={t.id} className={`live-summary__item${mark(t.id)}`}>
                <span className="live-summary__who">{t.who || "—"}</span> {t.what}
                {t.due && <span className="muted"> · срок: {t.due}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <Section title={SECTIONS[2][1]} items={summary.open_questions} mark={mark} />
    </div>
  );
}

function Section({ title, items, mark }: { title: string; items: { id: string; text: string }[]; mark: (id: string) => string }) {
  if (items.length === 0) return null;
  return (
    <section className="live-summary__section" aria-label={title}>
      <h4 className="live-summary__title">{title}</h4>
      <ul className="live-summary__list">
        {items.map((it) => <li key={it.id} className={`live-summary__item${mark(it.id)}`}>{it.text}</li>)}
      </ul>
    </section>
  );
}
