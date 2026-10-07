/**
 * Шапка сессии агента-участника: что с ним (слушает / думает / пишет /
 * ошибка), какая модель (у Claude Code — та, что запустил CLI, из
 * `system/init`; не та, что в настройках, — предупреждение), что она видит,
 * пометки (не видит картинок;
 * исключённые папки — только просьба), профиль сессии (0.3.7: чип и
 * переключатель «Профиль» — «Рабочая встреча» / «Нейтральный», только на эту
 * сессию), «Как часто писать» и поповер «Что я знаю» со сводкой на сейчас
 * (сводка по-прежнему ведётся в фоне — на ней держатся итоги и название встречи).
 *
 * В компактной панели — состояние, модель и чип профиля, остальное — в поповере.
 */

import { BookOpen } from "lucide-react";
import { type KeyboardEvent, useId, useState } from "react";

import { plural } from "../lib/format";
import type { AgentFrequencyLabel, AgentInfo, AgentProfile, LiveSummary as Summary } from "../lib/types";
import { Icon } from "../ui/Icon";
import { Popover } from "../ui/Popover";
import { LiveSummary } from "./LiveSummary";
import { PROFILES, PROFILE_LABELS, PROFILE_NOTES, profileOf } from "./profiles";
import "./chat.css";

export const FREQUENCIES: AgentFrequencyLabel[] = ["реже", "обычно", "чаще"];
/** Пометка, когда модель не принимает картинки (как `llm.NO_VISION_NOTE` у резидента). */
export const NO_VISION = "Модель не видит изображения — уходит только текст";
export const DENY_NOTE = "Исключённые папки — только просьба";
const DENY_TITLE = "Эта модель не умеет запрещать чтение папок: исключённые папки базы знаний указаны ей только просьбой в инструкции";
const NO_FRESH = new Set<string>();

/**
 * «разговор, структура базы знаний, 3 материала»; карта только из прошлых встреч группы — «карта».
 * Профиль «Нейтральный» базу знаний не видит вовсе: «только разговор» и то, что приложил человек.
 */
export function seesText(agent: AgentInfo): string {
  const parts: string[] = [];
  const sees = agent.sees ?? { conversation: true, kb: false, materials: 0, images: 0 };
  const neutral = profileOf(agent.profile) === "neutral";
  if (neutral) parts.push("только разговор");
  else if (sees.conversation !== false) parts.push("разговор");
  if (sees.kb && !neutral) parts.push(sees.kb_docs === false ? "карта" : "структура базы знаний");
  if (sees.materials > 0) parts.push(`${sees.materials} ${plural(sees.materials, "материал", "материала", "материалов")}`);
  if (sees.images > 0) parts.push(`${sees.images} ${plural(sees.images, "изображение", "изображения", "изображений")}`);
  return parts.join(", ") || "ничего";
}

const MODEL_TITLE = "Claude Code запустил не ту модель, что указана в настройках («Модель Claude Code»). "
  + "Проверьте переменные окружения ANTHROPIC_DEFAULT_*_MODEL, управляемые настройки Claude Code "
  + "(model, availableModels) и ~/.claude/settings.json — или модель недоступна вашей подписке";

/** «Запущена claude-fable-5-1, в настройках — opus»; модель та же или неизвестна — null. */
export function modelWarning(agent: AgentInfo): string | null {
  if (!agent.model_mismatch || !agent.model) return null;
  return `Запущена ${agent.model}, в настройках — ${agent.model_configured || "другая модель"}`;
}

export function agentNotes(agent: AgentInfo): { text: string; title?: string; warn?: boolean }[] {
  const notes: { text: string; title?: string; warn?: boolean }[] = [];
  const warning = modelWarning(agent);
  if (warning) notes.push({ text: warning, title: MODEL_TITLE, warn: true });
  if (!agent.vision) notes.push({ text: NO_VISION });
  if (!agent.deny_enforced) notes.push({ text: DENY_NOTE, title: DENY_TITLE });
  return notes;
}

/** Состояние словом: «пишет» — только когда пузырь ответа виден (молчаливый ход — «думает»). */
export function stateOf(agent: AgentInfo, writingShown: boolean): { key: string; text: string } {
  if (agent.state === "error") return { key: "error", text: "ошибка" };
  if (agent.state === "writing") return writingShown ? { key: "writing", text: "пишет…" } : { key: "thinking", text: "думает…" };
  return { key: "listening", text: "слушает" };
}

/** Переключатель-радиогруппа шапки сессии (частота, профиль): стрелки двигают выбор. */
function Segmented<T extends string>({ label, options, text, titles, value, onChange, disabled = false }: {
  label: string; options: readonly T[]; text?: (v: T) => string; titles?: (v: T) => string;
  value: T; onChange: (v: T) => void; disabled?: boolean;
}) {
  const labelId = useId();
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = options.indexOf(value);
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? Math.min(options.length - 1, at + 1)
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? Math.max(0, at - 1) : -1;
    if (next < 0 || next === at) return;
    e.preventDefault();
    onChange(options[next]!);
    (e.currentTarget.querySelectorAll("button")[next] as HTMLButtonElement | undefined)?.focus();
  };
  return (
    <span className="session-freq">
      <span className="session-freq__label" id={labelId}>{label}</span>
      <div className="session-freq__group" role="radiogroup" aria-labelledby={labelId} onKeyDown={onKey}>
        {options.map((f) => (
          <button key={f} type="button" role="radio" aria-checked={value === f} tabIndex={value === f ? 0 : -1}
            className="session-freq__opt" disabled={disabled} title={titles?.(f)}
            onClick={() => { if (f !== value) onChange(f); }}>
            {text ? text(f) : f}
          </button>
        ))}
      </div>
    </span>
  );
}

export function FrequencySelect({ value, onChange, disabled = false }: {
  value: AgentFrequencyLabel; onChange: (v: AgentFrequencyLabel) => void; disabled?: boolean;
}) {
  return <Segmented label="Как часто писать" options={FREQUENCIES} value={value} onChange={onChange} disabled={disabled} />;
}

/** «Профиль»: роль ассистента на эту сессию (настройка по умолчанию не меняется). */
export function ProfileSelect({ value, onChange, disabled = false }: {
  value: AgentProfile; onChange: (v: AgentProfile) => void; disabled?: boolean;
}) {
  return (
    <Segmented label="Профиль" options={PROFILES} value={value} onChange={onChange} disabled={disabled}
      text={(p) => PROFILE_LABELS[p].toLowerCase()} titles={(p) => PROFILE_NOTES[p]} />
  );
}

/** Подсказка чипа профиля. */
export function profileTitle(profile: AgentProfile): string {
  return `Профиль сессии «${PROFILE_LABELS[profile]}»: ${PROFILE_NOTES[profile]}. Меняется по ходу встречи`;
}

export function SessionBar({ agent, summary, writing = false, compact = false, quiet = false, onFrequency, onProfile,
  disabled = false }: {
  agent: AgentInfo;
  summary: Summary;
  /** Ответ пишется и виден в ленте. */
  writing?: boolean;
  compact?: boolean;
  /** «Не отвлекать»: и ошибку не объявлять. */
  quiet?: boolean;
  onFrequency: (v: AgentFrequencyLabel) => void;
  /** Сменить профиль идущей сессии; нет — только чип, без переключателя. */
  onProfile?: (v: AgentProfile) => void;
  disabled?: boolean;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const state = stateOf(agent, writing);
  const notes = agentNotes(agent);
  const warning = modelWarning(agent);
  const sees = seesText(agent);
  const frequency = FREQUENCIES.includes(agent.frequency) ? agent.frequency : "чаще";
  const profile = profileOf(agent.profile);
  return (
    <div className={`session-bar${compact ? " session-bar--compact" : ""}`} role="group" aria-label="Сессия ассистента">
      {/* Состояние меняется на каждом ходе агента — не живая область, иначе
          экранный диктор говорил бы всю встречу. Объявляется только ошибка.
          Без точки: точка одна — в шапке панели, и она тоже про агента
          (ревью live-chat, M6). */}
      <span className={`session-bar__state session-bar__state--${state.key}`}
        title={agent.state === "error" && agent.error ? agent.error : undefined}>
        {state.text}
      </span>
      <span className="sr-only" role="status">
        {!quiet && agent.state === "error" ? `Ошибка ассистента${agent.error ? `: ${agent.error}` : ""}` : ""}
      </span>
      <span className={`session-bar__model${warning ? " session-bar__model--warn" : ""}`}
        title={warning ? `${warning}. ${MODEL_TITLE}` : agent.provider}>
        {agent.label || agent.provider}
        {compact && warning && <span className="sr-only"> ({warning})</span>}
      </span>
      <span className={`session-bar__profile session-bar__profile--${profile}`} title={profileTitle(profile)}>
        {PROFILE_LABELS[profile]}
      </span>
      {!compact && <span className="session-bar__sees">видит: {sees}</span>}
      {!compact && notes.map((n) => (
        <span key={n.text} className={`session-bar__note${n.warn ? " session-bar__note--warn" : ""}`}
          title={n.title ?? n.text}>{n.text}</span>
      ))}
      <span className="session-bar__end">
        {!compact && onProfile && <ProfileSelect value={profile} onChange={onProfile} disabled={disabled} />}
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
            <p className="session-know__line"><span className="muted">Профиль:</span> {PROFILE_LABELS[profile]}</p>
            <p className="session-know__line"><span className="muted">Видит:</span> {sees}</p>
            {notes.map((n) => <p key={n.text} className="session-know__note" title={n.title}>{n.text}</p>)}
            {compact && onProfile && <ProfileSelect value={profile} onChange={onProfile} disabled={disabled} />}
            {compact && <FrequencySelect value={frequency} onChange={onFrequency} disabled={disabled} />}
            <h4 className="session-know__title">Сводка на сейчас</h4>
            <div className="session-know__summary"><LiveSummary summary={summary} fresh={NO_FRESH} /></div>
          </div>
        </Popover>
      )}
    </div>
  );
}
