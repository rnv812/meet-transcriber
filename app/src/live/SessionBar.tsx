/**
 * Шапка сессии агента-участника: что с ним (слушает / думает / пишет /
 * ошибка), какая модель (у Claude Code — та, что запустил CLI, из
 * `system/init`; не та, что в настройках, — предупреждение), что она видит
 * и что может (0.3.7: файлы, MCP, веб — по вашему согласию), пометки (не
 * видит картинок;
 * исключённые папки — только просьба), профиль сессии (0.3.7: чип и
 * переключатель «Профиль» — «Рабочая встреча» / «Личный», только на эту
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
import "./live.css";

export const FREQUENCIES: AgentFrequencyLabel[] = ["реже", "обычно", "чаще"];
/** Пометка, когда модель не принимает картинки (как `llm.NO_VISION_NOTE` у резидента). */
export const NO_VISION = "Модель не видит изображения — уходит только текст";
export const DENY_NOTE = "Исключённые папки — только просьба";
const DENY_TITLE = "Эта модель не умеет запрещать чтение папок: исключённые папки базы знаний указаны ей только просьбой в инструкции";
const NO_FRESH = new Set<string>();

/**
 * «разговор, структура базы знаний, 3 материала»; карта только из прошлых встреч группы — «карта».
 * Профиль «Личный» базу знаний не видит вовсе: «только разговор», а с вложениями —
 * «разговор и ваши материалы (2 материала)».
 */
export function seesText(agent: AgentInfo): string {
  const sees = agent.sees ?? { conversation: true, kb: false, materials: 0, images: 0 };
  const own: string[] = [];
  if (sees.materials > 0) own.push(`${sees.materials} ${plural(sees.materials, "материал", "материала", "материалов")}`);
  if (sees.images > 0) own.push(`${sees.images} ${plural(sees.images, "изображение", "изображения", "изображений")}`);
  if (profileOf(agent.profile) === "personal") {
    return own.length ? `разговор и ваши материалы (${own.join(", ")})` : "только разговор";
  }
  const parts: string[] = [];
  if (sees.conversation !== false) parts.push("разговор");
  if (sees.kb) parts.push(sees.kb_docs === false ? "карта" : "структура базы знаний");
  return [...parts, ...own].join(", ") || "ничего";
}

const MODEL_TITLE = "Claude Code запустил не ту модель, что указана в настройках («Модель Claude Code»). "
  + "Проверьте переменные окружения ANTHROPIC_DEFAULT_*_MODEL, управляемые настройки Claude Code "
  + "(model, availableModels) и ~/.claude/settings.json — или модель недоступна вашей подписке";

/** «Запущена claude-fable-5-1, в настройках — opus»; модель та же или неизвестна — null. */
export function modelWarning(agent: AgentInfo): string | null {
  if (!agent.model_mismatch || !agent.model) return null;
  return `Запущена ${agent.model}, в настройках — ${agent.model_configured || "другая модель"}`;
}

export const CAN_CONSENT_TITLE = "Без вашей просьбы ассистент читает только эту встречу и ваши вложения; по просьбе — "
  + "читает файлы, ищет в вебе и смотрит через MCP; каждое действие (команда, запись, правка задачи, открытие страницы) "
  + "Meet покажет карточкой и выполнит только после «Разрешить один раз»";
export const CAN_PERSONAL_TITLE = "Без вашей просьбы ассистент читает только эту запись и ваши вложения; по просьбе — "
  + "читает файлы и ищет в вебе; каждое действие Meet покажет карточкой. База знаний, другие записи и MCP-серверы "
  + "в профиле «Личный» закрыты";
export const CAN_FILES_TITLE ="Codex/OpenCode: только чтение файлов по вашей просьбе; MCP, веб и действия — только с Claude Code";
/** Сколько имён MCP-серверов показать в «может: …», дальше — «…». */
const MCP_SHOWN = 3;

/**
 * «файлы, MCP (Jira, GitLab…), веб — по вашему согласию» при расширенных возможностях;
 * «читать встречу и базу знаний» без них; пусто — модель без своих инструментов.
 */
export function canText(agent: AgentInfo): string {
  const can = agent.can ?? (agent.tools ? { mode: "read" as const, mcp: null } : { mode: "meet" as const, mcp: null });
  const personal = profileOf(agent.profile) === "personal";
  if (can.mode === "consent") {
    // «Личный»: MCP пользователя не подключаются (база знаний другим путём).
    if (personal) return "файлы, веб — по вашему согласию";
    const names = (can.mcp ?? []).filter(Boolean);
    const mcp = names.length
      ? `MCP (${names.slice(0, MCP_SHOWN).join(", ")}${names.length > MCP_SHOWN ? "…" : ""})`
      : "MCP";
    return `файлы, ${mcp}, веб — по вашему согласию`;
  }
  if (can.mode === "files") return "читать файлы по вашей просьбе";
  if (can.mode === "read") return personal ? "читать эту запись и ваши вложения" : "читать встречу и базу знаний";
  return "";
}

/** «Личный» у Codex: база знаний и другие записи закрыты только просьбой в инструкции. */
export const PERSONAL_DENY_NOTE = "База знаний закрыта только просьбой";
const PERSONAL_DENY_TITLE = "Эта модель не умеет запрещать чтение папок: база знаний и другие записи закрыты ей "
  + "только просьбой в инструкции. Надёжно закрывает их Claude Code";

export function agentNotes(agent: AgentInfo): { text: string; title?: string; warn?: boolean }[] {
  const notes: { text: string; title?: string; warn?: boolean }[] = [];
  const warning = modelWarning(agent);
  if (warning) notes.push({ text: warning, title: MODEL_TITLE, warn: true });
  if (!agent.vision) notes.push({ text: NO_VISION });
  // Исключённые папки базы знаний — про «Рабочую встречу»; в «Личном» — своя пометка:
  // базу и другие записи эта модель не запрещает, только просит (ревью I2, п. 8).
  if (!agent.deny_enforced) {
    notes.push(profileOf(agent.profile) === "personal"
      ? { text: PERSONAL_DENY_NOTE, title: PERSONAL_DENY_TITLE }
      : { text: DENY_NOTE, title: DENY_TITLE });
  }
  return notes;
}

/** Состояние словом: «пишет» — только когда пузырь ответа виден (молчаливый ход — «думает»). */
export function stateOf(agent: AgentInfo, writingShown: boolean): { key: string; text: string } {
  if (agent.state === "error") return { key: "error", text: "ошибка" };
  if (agent.state === "writing") return writingShown ? { key: "writing", text: "пишет…" } : { key: "thinking", text: "думает…" };
  return { key: "listening", text: "слушает" };
}

/**
 * Переключатель-радиогруппа шапки сессии (частота, профиль): сегменты Aurora
 * `.tabs.tabs--sm` с ролями радио (`live-seg` — выбранный по `aria-checked`,
 * live.css); стрелки двигают выбор. Группа не обрезает содержимое — рамка
 * фокуса видна целиком.
 */
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
      <div className="tabs tabs--sm live-seg" role="radiogroup" aria-labelledby={labelId} onKeyDown={onKey}>
        {options.map((f) => (
          <button key={f} type="button" role="radio" aria-checked={value === f} tabIndex={value === f ? 0 : -1}
            disabled={disabled} title={titles?.(f)}
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
  disabled = false, onRevokeGrant }: {
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
  /** Отозвать «Разрешать такое до конца встречи». */
  onRevokeGrant?: (id: string) => void;
}) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const state = stateOf(agent, writing);
  const notes = agentNotes(agent);
  const warning = modelWarning(agent);
  const sees = seesText(agent);
  const can = canText(agent);
  const canTitle = agent.can?.mode === "consent"
    ? (profileOf(agent.profile) === "personal" ? CAN_PERSONAL_TITLE : CAN_CONSENT_TITLE)
    : agent.can?.mode === "files" ? CAN_FILES_TITLE : undefined;
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
      {!compact && can && <span className="session-bar__sees session-bar__can" title={canTitle}>может: {can}</span>}
      {!compact && (agent.grants?.length ?? 0) > 0 && (
        <span className="session-bar__sees session-bar__grants" title="Разрешено до конца встречи — без карточки">
          разрешено:{" "}
          {agent.grants!.map((g, k) => (
            <span key={g.id} className="session-bar__grant">
              {k > 0 && ", "}{g.label}
              {onRevokeGrant && (
                <button type="button" className="session-bar__revoke" aria-label={`Отозвать: ${g.label}`}
                  onClick={() => onRevokeGrant(g.id)}>×</button>
              )}
            </span>
          ))}
        </span>
      )}
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
            {can && <p className="session-know__line" title={canTitle}><span className="muted">Может:</span> {can}</p>}
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
