/**
 * Шапка сессии агента-участника: что с ним (слушает / думает / пишет /
 * ошибка), какая модель, что она видит, пометки (не видит картинок;
 * исключённые папки — только просьба), «Как часто писать» и поповер «Что я
 * знаю» со сводкой на сейчас (сводка по-прежнему ведётся в фоне — на ней
 * держатся итоги и название встречи).
 *
 * В компактной панели — только состояние и модель, остальное — в поповере.
 */

import { BookOpen } from "lucide-react";
import { type KeyboardEvent, useId, useState } from "react";

import { plural } from "../lib/format";
import type { AgentFrequencyLabel, AgentInfo, LiveSummary as Summary } from "../lib/types";
import { Icon } from "../ui/Icon";
import { Popover } from "../ui/Popover";
import { LiveSummary } from "./LiveSummary";
import "./chat.css";

export const FREQUENCIES: AgentFrequencyLabel[] = ["реже", "обычно", "чаще"];
/** Пометка, когда модель не принимает картинки (как `llm.NO_VISION_NOTE` у резидента). */
export const NO_VISION = "Модель не видит изображения — уходит только текст";
export const DENY_NOTE = "Исключённые папки — только просьба";
const DENY_TITLE = "Эта модель не умеет запрещать чтение папок: исключённые папки базы знаний указаны ей только просьбой в инструкции";
const NO_FRESH = new Set<string>();

/** «разговор, структура базы знаний, 3 материала». */
export function seesText(agent: AgentInfo): string {
  const parts: string[] = [];
  const sees = agent.sees ?? { conversation: true, kb: false, materials: 0, images: 0 };
  if (sees.conversation !== false) parts.push("разговор");
  if (sees.kb) parts.push("структура базы знаний");
  if (sees.materials > 0) parts.push(`${sees.materials} ${plural(sees.materials, "материал", "материала", "материалов")}`);
  if (sees.images > 0) parts.push(`${sees.images} ${plural(sees.images, "изображение", "изображения", "изображений")}`);
  return parts.join(", ") || "ничего";
}

export function agentNotes(agent: AgentInfo): { text: string; title?: string }[] {
  const notes: { text: string; title?: string }[] = [];
  if (!agent.vision) notes.push({ text: NO_VISION });
  if (!agent.deny_enforced) notes.push({ text: DENY_NOTE, title: DENY_TITLE });
  return notes;
}

/** Состояние словом: «пишет» — только когда пузырь ответа виден (молчаливый ход — «думает»). */
function stateOf(agent: AgentInfo, writingShown: boolean): { key: string; text: string } {
  if (agent.state === "error") return { key: "error", text: "ошибка" };
  if (agent.state === "writing") return writingShown ? { key: "writing", text: "пишет…" } : { key: "thinking", text: "думает…" };
  return { key: "listening", text: "слушает" };
}

export function FrequencySelect({ value, onChange, disabled = false }: {
  value: AgentFrequencyLabel; onChange: (v: AgentFrequencyLabel) => void; disabled?: boolean;
}) {
  const labelId = useId();
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = FREQUENCIES.indexOf(value);
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? Math.min(FREQUENCIES.length - 1, at + 1)
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? Math.max(0, at - 1) : -1;
    if (next < 0 || next === at) return;
    e.preventDefault();
    onChange(FREQUENCIES[next]!);
    (e.currentTarget.querySelectorAll("button")[next] as HTMLButtonElement | undefined)?.focus();
  };
  return (
    <span className="session-freq">
      <span className="session-freq__label" id={labelId}>Как часто писать</span>
      <div className="session-freq__group" role="radiogroup" aria-labelledby={labelId} onKeyDown={onKey}>
        {FREQUENCIES.map((f) => (
          <button key={f} type="button" role="radio" aria-checked={value === f} tabIndex={value === f ? 0 : -1}
            className="session-freq__opt" disabled={disabled} onClick={() => { if (f !== value) onChange(f); }}>
            {f}
          </button>
        ))}
      </div>
    </span>
  );
}

export function SessionBar({ agent, summary, writing = false, compact = false, quiet = false, onFrequency, disabled = false }: {
  agent: AgentInfo;
  summary: Summary;
  /** Ответ пишется и виден в ленте. */
  writing?: boolean;
  compact?: boolean;
  /** «Не отвлекать»: и ошибку не объявлять. */
  quiet?: boolean;
  onFrequency: (v: AgentFrequencyLabel) => void;
  disabled?: boolean;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const state = stateOf(agent, writing);
  const notes = agentNotes(agent);
  const sees = seesText(agent);
  const frequency = FREQUENCIES.includes(agent.frequency) ? agent.frequency : "чаще";
  return (
    <div className={`session-bar${compact ? " session-bar--compact" : ""}`} role="group" aria-label="Сессия ассистента">
      {/* Состояние меняется на каждом ходе агента — не живая область, иначе
          экранный диктор говорил бы всю встречу. Объявляется только ошибка. */}
      <span className={`session-bar__state session-bar__state--${state.key}`}
        title={agent.state === "error" && agent.error ? agent.error : undefined}>
        <span className="session-bar__dot" aria-hidden="true" />{state.text}
      </span>
      <span className="sr-only" role="status">
        {!quiet && agent.state === "error" ? `Ошибка ассистента${agent.error ? `: ${agent.error}` : ""}` : ""}
      </span>
      <span className="session-bar__model" title={agent.provider}>{agent.label || agent.provider}</span>
      {!compact && <span className="session-bar__sees">видит: {sees}</span>}
      {!compact && notes.map((n) => (
        <span key={n.text} className="session-bar__note" title={n.title ?? n.text}>{n.text}</span>
      ))}
      <span className="session-bar__end">
        {!compact && <FrequencySelect value={frequency} onChange={onFrequency} disabled={disabled} />}
        <button type="button" className="session-bar__know" aria-expanded={!!anchor}
          onClick={(e) => setAnchor(anchor ? null : e.currentTarget)}>
          <Icon as={BookOpen} size="sm" />{compact ? <span className="sr-only">Что я знаю</span> : "Что я знаю"}
        </button>
      </span>
      {anchor && (
        <Popover anchor={anchor} onClose={() => setAnchor(null)} label="Что я знаю" width={320} align="end" anchorToggles>
          <div className="session-know">
            <p className="session-know__line"><span className="muted">Модель:</span> {agent.label || agent.provider}</p>
            <p className="session-know__line"><span className="muted">Видит:</span> {sees}</p>
            {notes.map((n) => <p key={n.text} className="session-know__note" title={n.title}>{n.text}</p>)}
            {compact && <FrequencySelect value={frequency} onChange={onFrequency} disabled={disabled} />}
            <h4 className="session-know__title">Сводка на сейчас</h4>
            <div className="session-know__summary"><LiveSummary summary={summary} fresh={NO_FRESH} /></div>
          </div>
        </Popover>
      )}
    </div>
  );
}
