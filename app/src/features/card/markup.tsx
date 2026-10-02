/**
 * Разметка встречи в «Расшифровке» (M3): значок типа реплики, чипы фильтра по
 * типам, блок «Наблюдения» под поиском. Сама разметка — lib/analysisView.ts.
 */

import { useState, type ReactNode } from "react";
import {
  ChevronDown, ChevronRight, CircleAlert, CircleArrowRight, CircleQuestionMark, Eye, Gavel, Hand, Lightbulb, ListTodo,
  Split, ThumbsUp, TriangleAlert, type LucideIcon,
} from "lucide-react";
import { INSIGHT_LABEL, TYPE_FILTERS, TYPE_LABEL, type InsightView } from "../../lib/analysisView";
import { clock } from "../../lib/format";
import type { Turn } from "../../lib/speakers";
import type { InsightKind, PhraseType } from "../../lib/types";
import { AskAgentButton } from "../../ui/AskAgent";
import "./markup.css";

const TYPE_ICON: Record<PhraseType, LucideIcon | null> = {
  statement: null,
  question: CircleQuestionMark,
  decision: Gavel,
  task: ListTodo,
  risk: TriangleAlert,
  idea: Lightbulb,
  agreement: ThumbsUp,
  objection: Hand,
};

const INSIGHT_ICON: Record<InsightKind, LucideIcon> = {
  insight: Eye,
  contradiction: Split,
  attention: CircleAlert,
  followup: CircleArrowRight,
};

/** Значок типа реплики в начале строки: приглушённый, подпись — в подсказке. */
export function TypeIcon({ type }: { type: PhraseType }) {
  const Icon = TYPE_ICON[type];
  if (!Icon) return null;
  return (
    <span className={`turn__type turn__type--${type}`} role="img" aria-label={TYPE_LABEL[type]} title={TYPE_LABEL[type]}>
      <Icon size={13} strokeWidth={1.9} aria-hidden="true" />
    </span>
  );
}

/** Чипы «Вопросы · Решения · Задачи · Риски · Идеи»: можно выбрать несколько. */
export function TypeFilters({ counts, value, onChange }: {
  counts: Map<PhraseType, number>;
  value: ReadonlySet<PhraseType>;
  onChange: (next: Set<PhraseType>) => void;
}) {
  const any = TYPE_FILTERS.some((f) => (counts.get(f.type) ?? 0) > 0);
  if (!any) return null;
  const toggle = (type: PhraseType) => {
    const next = new Set(value);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    onChange(next);
  };
  return (
    <div className="tfilters" role="group" aria-label="Показать только реплики этих типов">
      {TYPE_FILTERS.map((f) => {
        const n = counts.get(f.type) ?? 0;
        const Icon = TYPE_ICON[f.type]!;
        const on = value.has(f.type);
        return (
          <button key={f.type} type="button" className="tfilter" aria-pressed={on} disabled={!n && !on}
            onClick={() => toggle(f.type)}>
            <Icon size={13} strokeWidth={1.9} aria-hidden="true" />
            {f.label}
            <span className="tfilter__n num">{n}</span>
          </button>
        );
      })}
      {value.size > 0 && (
        <button type="button" className="link-btn tfilters__reset" onClick={() => onChange(new Set())}>
          Показать все
        </button>
      )}
    </div>
  );
}

const COLLAPSED_KEY = "meet.insights.collapsed";
function readCollapsed(): boolean {
  try { return window.localStorage?.getItem(COLLAPSED_KEY) === "1"; } catch { return false; }
}
function writeCollapsed(v: boolean) {
  try { window.localStorage?.setItem(COLLAPSED_KEY, v ? "1" : "0"); } catch { /* хранилище недоступно */ }
}

/** Блок «Наблюдения» под поиском: вид, текст, «почему», ссылки на реплики, ✦. */
export function InsightsBlock({ insights, turns, onJump, onAsk, renderText }: {
  insights: InsightView[];
  turns: Turn[];
  /** Перейти к реплике (номер реплики карточки). */
  onJump: (turn: number) => void;
  onAsk?: (insight: InsightView) => void;
  /** Текст со ссылками (Jira); нет — как есть. */
  renderText?: (text: string) => ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [why, setWhy] = useState<ReadonlySet<string>>(() => new Set());
  if (!insights.length) return null;
  const toggle = () => { setCollapsed((c) => { writeCollapsed(!c); return !c; }); };
  const Chevron = collapsed ? ChevronRight : ChevronDown;
  return (
    <section className="insights" aria-label="Наблюдения анализа встречи">
      <button type="button" className="insights__head" aria-expanded={!collapsed} onClick={toggle}>
        <Chevron size={14} strokeWidth={1.9} aria-hidden="true" />
        <span>Наблюдения</span>
        <span className="insights__n num">{insights.length}</span>
      </button>
      {!collapsed && (
        <ul className="insights__list">
          {insights.map((x) => {
            const Icon = INSIGHT_ICON[x.kind] ?? Eye;
            const open = why.has(x.id);
            return (
              <li key={x.id} className={`insight insight--${x.kind}`}>
                <span className="insight__icon" role="img" aria-label={INSIGHT_LABEL[x.kind]} title={INSIGHT_LABEL[x.kind]}>
                  <Icon size={14} strokeWidth={1.9} aria-hidden="true" />
                </span>
                <div className="insight__body">
                  <p className="insight__text">{renderText ? renderText(x.text) : x.text}</p>
                  {open && x.why && <p className="insight__why muted">{renderText ? renderText(x.why) : x.why}</p>}
                  <div className="insight__row">
                    {x.why && (
                      <button type="button" className="link-btn insight__why-btn" aria-expanded={open}
                        onClick={() => setWhy((cur) => {
                          const next = new Set(cur);
                          if (next.has(x.id)) next.delete(x.id);
                          else next.add(x.id);
                          return next;
                        })}>
                        {open ? "Скрыть" : "Почему"}
                      </button>
                    )}
                    {x.refs.map((r) => {
                      const t = turns[r];
                      if (!t) return null;
                      return (
                        <button key={r} type="button" className="insight__ref num" onClick={() => onJump(r)}
                          title="Перейти к реплике">
                          {clock(t.start)} · {t.speaker}
                        </button>
                      );
                    })}
                    {onAsk && (
                      <AskAgentButton className="insight__ask" label="Обсудить с агентом"
                        onClick={() => onAsk(x)} />
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
