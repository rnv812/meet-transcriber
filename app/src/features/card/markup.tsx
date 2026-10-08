/**
 * Разметка встречи в «Расшифровке» (M3, Atlas Aurora): бейдж типа реплики,
 * фильтры по типам и «Задачи Jira» в одной строке, карточка «Наблюдения» под
 * поиском. Сама разметка — lib/analysisView.ts.
 */

import { useContext, useState } from "react";
import {
  ChevronDown, ChevronRight, CircleAlert, CircleArrowRight, CircleQuestionMark, Eye, Gavel, Hand, Lightbulb, ListTodo,
  Split, ThumbsUp, TriangleAlert, type LucideIcon,
} from "lucide-react";
import { INSIGHT_LABEL, TYPE_FILTERS, TYPE_LABEL, type InsightView } from "../../lib/analysisView";
import { clock } from "../../lib/format";
import { JiraLinks, type JiraTask } from "../../lib/jira";
import type { Turn } from "../../lib/speakers";
import type { InsightKind, PhraseType } from "../../lib/types";
import { AskAgentButton } from "../../ui/AskAgent";
import { JiraLink, LinkedText } from "../../ui/LinkedText";
import { Tip } from "../../ui/Tip";
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

/** Бейдж типа реплики — после имени: значок и подпись (у утверждения бейджа нет). */
export function TypeIcon({ type }: { type: PhraseType }) {
  const Icon = TYPE_ICON[type];
  if (!Icon) return null;
  return (
    // Подпись видна в самом бейдже: отдельной подсказки не нужно.
    <span className={`badge badge--plain turn__type turn__type--${type}`} role="img" aria-label={TYPE_LABEL[type]}>
      <Icon size={12} strokeWidth={1.75} aria-hidden="true" />{TYPE_LABEL[type]}
    </span>
  );
}

/** Есть ли что фильтровать: хоть одна реплика одного из типов фильтра. */
export const hasTypeFilters = (counts: Map<PhraseType, number>) =>
  TYPE_FILTERS.some((f) => (counts.get(f.type) ?? 0) > 0);

/** Фильтры «Вопросы · Решения · Задачи · Риски · Идеи» (Aurora .filter): можно выбрать несколько. */
export function TypeFilters({ counts, value, onChange }: {
  counts: Map<PhraseType, number>;
  value: ReadonlySet<PhraseType>;
  onChange: (next: Set<PhraseType>) => void;
}) {
  if (!hasTypeFilters(counts)) return null;
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
        const on = value.has(f.type);
        return (
          <button key={f.type} type="button" className="filter tfilter" aria-pressed={on} disabled={!n && !on}
            onClick={() => toggle(f.type)}>
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
/** Свёрнуто ли «Наблюдения»: как человек оставил; не выбирал — свёрнуто в узком окне (до 1000 px). */
function readCollapsed(): boolean {
  let stored: string | null = null;
  try { stored = window.localStorage?.getItem(COLLAPSED_KEY) ?? null; } catch { /* хранилище недоступно */ }
  if (stored === "1" || stored === "0") return stored === "1";
  return typeof window !== "undefined" && window.innerWidth > 0 && window.innerWidth <= 1000;
}
function writeCollapsed(v: boolean) {
  try { window.localStorage?.setItem(COLLAPSED_KEY, v ? "1" : "0"); } catch { /* хранилище недоступно */ }
}

/** Сколько реплик показывать у задачи в «Задачах» (остальные — «ещё N»). */
const TASK_TURNS = 4;

/**
 * «Задачи Jira:» в строке фильтров: задачи, названные во встрече, — ключ (ссылка) и
 * время реплик, где о ней говорили (спикер — в имени и подсказке кнопки).
 */
export function JiraTasks({ tasks, turns, onJump }: { tasks: JiraTask[]; turns: Turn[]; onJump: (turn: number) => void }) {
  const jira = useContext(JiraLinks);
  if (!jira || !tasks.length) return null;
  return (
    <div className="jtasks" role="group" aria-label="Задачи Jira, названные во встрече">
      <span className="jtasks__head">Задачи Jira:</span>
      <ul className="jtasks__list">
        {tasks.map((task) => (
          <li key={task.key} className="badge badge--plain jtask">
            <JiraLink linker={jira} keyText={task.key}>{task.key}</JiraLink>
            {task.turns.slice(0, TASK_TURNS).map((r) => {
              const t = turns[r];
              if (!t) return null;
              const at = `${clock(t.start)} · ${t.speaker}`;
              return (
                <Tip key={r} content={`${at} — перейти к реплике`} describe={false}>
                  <button type="button" className="jtask__ref num" onClick={() => onJump(r)} aria-label={at}>
                    {clock(t.start)}
                  </button>
                </Tip>
              );
            })}
            {task.turns.length > TASK_TURNS && (
              <span className="jtask__more">ещё {task.turns.length - TASK_TURNS}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Карточка «Наблюдения» под поиском (aurora-wash — вывод ИИ): вид, текст, «почему», ссылки на реплики, ✦. */
export function InsightsBlock({ insights, turns, onJump, onAsk }: {
  insights: InsightView[];
  turns: Turn[];
  /** Перейти к реплике (номер реплики карточки). */
  onJump: (turn: number) => void;
  onAsk?: (insight: InsightView) => void;
}) {
  const jira = useContext(JiraLinks);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [why, setWhy] = useState<ReadonlySet<string>>(() => new Set());
  if (!insights.length) return null;
  const toggle = () => { setCollapsed((c) => { writeCollapsed(!c); return !c; }); };
  const Chevron = collapsed ? ChevronRight : ChevronDown;
  return (
    <section className="card aurora-wash insights" aria-label="Наблюдения анализа встречи">
      <button type="button" className="insights__head" aria-expanded={!collapsed} onClick={toggle}>
        <Chevron size={14} strokeWidth={1.75} aria-hidden="true" />
        <span className="insights__title">Наблюдения</span>
        <span className="badge badge--plain insights__n num">{insights.length}</span>
      </button>
      {!collapsed && (
        <ul className="insights__list">
          {insights.map((x) => {
            const Icon = INSIGHT_ICON[x.kind] ?? Eye;
            const open = why.has(x.id);
            return (
              <li key={x.id} className={`insight insight--${x.kind}`}>
                <Tip content={INSIGHT_LABEL[x.kind]} describe={false}>
                  <span className="insight__icon" role="img" aria-label={INSIGHT_LABEL[x.kind]}>
                    <Icon size={14} strokeWidth={1.75} aria-hidden="true" />
                  </span>
                </Tip>
                <div className="insight__body">
                  <p className="insight__text"><LinkedText text={x.text} linker={jira} /></p>
                  {open && x.why && <p className="insight__why muted"><LinkedText text={x.why} linker={jira} /></p>}
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
                        <Tip key={r} content="Перейти к реплике">
                          <button type="button" className="insight__ref num" onClick={() => onJump(r)}>
                            {clock(t.start)} · {t.speaker}
                          </button>
                        </Tip>
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
