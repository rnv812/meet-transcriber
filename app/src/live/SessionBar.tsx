/**
 * Шапка сессии агента-участника — одна строка (макет MeetLive, доводка 0.4):
 * что с ним (слушает / думает / пишет / ошибка) · какая модель (у Claude Code —
 * та, что запустил CLI, из `system/init`) · бейдж профиля сессии · пометка-
 * предупреждение, если запущена не та модель, что в настройках; справа — «Что я знаю».
 *
 * Поповер «Что я знаю» — всё остальное: модель и профиль с переключателем
 * («Рабочая встреча» / «Личный», только на эту сессию), что модель видит и что
 * может (0.3.7: файлы, MCP, веб — по вашему согласию), разрешённое до конца
 * встречи с «Отозвать», пометки (не видит картинок; исключённые папки — только
 * просьба), «Как часто писать» и сводка на сейчас (сводка по-прежнему ведётся в
 * фоне — на ней держатся итоги и название встречи).
 *
 * Компактная (узкая область): «Что я знаю» — значком, предупреждение — цветом
 * модели и текстом для экранного диктора.
 */

import { BookOpen, X } from "lucide-react";
import { useId, useState } from "react";

import { plural } from "../lib/format";
import type { AgentFrequencyLabel, AgentInfo, AgentProfile, LiveSummary as Summary } from "../lib/types";
import { BADGE_CLASS } from "../ui/badge";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { Popover } from "../ui/Popover";
import { Segmented } from "../ui/Segmented";
import { Tip } from "../ui/Tip";
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
  if (can.mode === "consent" && can.agent_mode) {
    // 0.4: «Личный» отличается только промптом — подпись одна для обоих профилей.
    const names = (can.mcp ?? []).filter(Boolean);
    const mcp = names.length ? `MCP (${names.slice(0, MCP_SHOWN).join(", ")}${names.length > MCP_SHOWN ? "…" : ""})` : "MCP";
    return isAuto(agent)
      ? `файлы, команды, ${mcp}, веб — сам; рискованное — с вашего согласия`
      : `файлы, команды, ${mcp}, веб — с вашего согласия на каждое действие`;
  }
  if (can.mode === "act") return "файлы и команды в рабочих папках — сам; рискованное — нет";
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

export const CAN_AUTO_TITLE = "Обычные действия ассистент выполняет сам, рискованные — после вашего разрешения. По вашей просьбе он читает, правит файлы, "
  + "выполняет команды, зовёт MCP. Рискованное — удаление, запись вне рабочих папок, отправка наружу. "
  + "Ход только по репликам встречи ничего не меняет и не отправляет";
export const CAN_CONFIRM_TITLE = "Каждое действие (команда, правка, запись в задаче, открытие страницы) Meet покажет "
  + "карточкой и выполнит только после «Разрешить». Вернуть «Действует сам» — Настройки → Ассистент";
export const CAN_ACT_TITLE = "Codex/OpenCode по вашей просьбе правят рабочие папки и выполняют команды сами; удаление, "
  + "запись вне рабочих папок, отправку наружу и MCP — может только ассистент на Claude Code";

/** Автомод работает (или просили его, а CLI ещё не сообщил режим). */
function isAuto(agent: AgentInfo): boolean {
  return agent.can?.agent_mode === "auto" && agent.can.auto !== false;
}

/**
 * Как действует ассистент по просьбе (0.4; слова — 0.5): «Действует сам», «Спрашивает каждое» или —
 * просили автомод CLI, а он в другом режиме — «Сам действовать не может — спрашивает каждое»
 * (`warn`). Без расширенных возможностей или у старого ребёнка — null.
 */
export function modeOf(agent: AgentInfo): { text: string; warn?: boolean } | null {
  const can = agent.can;
  if (!can?.agent_mode || (can.mode !== "consent" && can.mode !== "act")) return null;
  if (can.agent_mode === "confirm") return { text: "Спрашивает каждое" };
  if (can.mode === "consent" && can.auto === false) return { text: "Сам действовать не может — спрашивает каждое", warn: true };
  return { text: "Действует сам" };
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
 * Переключатель шапки сессии (частота, профиль): видимая подпись и общие
 * сегменты `ui/Segmented` (`sm`, роли радио, стрелки двигают выбор).
 */
function SessionChoice<T extends string>({ label, options, text, titles, value, onChange, disabled = false }: {
  label: string; options: readonly T[]; text?: (v: T) => string; titles?: (v: T) => string;
  value: T; onChange: (v: T) => void; disabled?: boolean;
}) {
  const labelId = useId();
  return (
    <span className="session-freq">
      <span className="session-freq__label" id={labelId}>{label}</span>
      <Segmented labelledBy={labelId} value={value} onChange={onChange} disabled={disabled}
        options={options.map((f) => ({ value: f, label: text ? text(f) : f, tip: titles?.(f) }))} />
    </span>
  );
}

export function FrequencySelect({ value, onChange, disabled = false }: {
  value: AgentFrequencyLabel; onChange: (v: AgentFrequencyLabel) => void; disabled?: boolean;
}) {
  return <SessionChoice label="Как часто писать" options={FREQUENCIES} value={value} onChange={onChange} disabled={disabled} />;
}

/** «Профиль»: роль ассистента на эту сессию (настройка по умолчанию не меняется). */
export function ProfileSelect({ value, onChange, disabled = false }: {
  value: AgentProfile; onChange: (v: AgentProfile) => void; disabled?: boolean;
}) {
  return (
    <SessionChoice label="Профиль" options={PROFILES} value={value} onChange={onChange} disabled={disabled}
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
  const mode = modeOf(agent);
  const canTitle = agent.can?.mode === "consent" && agent.can.agent_mode
    ? (isAuto(agent) ? CAN_AUTO_TITLE : CAN_CONFIRM_TITLE)
    : agent.can?.mode === "consent"
      ? (profileOf(agent.profile) === "personal" ? CAN_PERSONAL_TITLE : CAN_CONSENT_TITLE)
      : agent.can?.mode === "act" ? CAN_ACT_TITLE : agent.can?.mode === "files" ? CAN_FILES_TITLE : undefined;
  const frequency = FREQUENCIES.includes(agent.frequency) ? agent.frequency : "чаще";
  const profile = profileOf(agent.profile);
  const grants = agent.grants ?? [];
  const toggle = (e: { currentTarget: HTMLElement }) => setAnchor(anchor ? null : e.currentTarget);
  return (
    <div className={`session-bar${compact ? " session-bar--compact" : ""}`} role="group" aria-label="Сессия ассистента">
      {/* Состояние меняется на каждом ходе агента — не живая область, иначе
          экранный диктор говорил бы всю встречу. Объявляется только ошибка.
          Без точки: точка одна — в шапке панели, и она тоже про агента
          (ревью live-chat, M6). */}
      <Tip content={agent.state === "error" && agent.error ? agent.error : undefined}>
        <span className={`session-bar__state session-bar__state--${state.key}`}>{state.text}</span>
      </Tip>
      <span className="sr-only" role="status">
        {!quiet && agent.state === "error" ? `Ошибка ассистента${agent.error ? `: ${agent.error}` : ""}` : ""}
      </span>
      {/* Модель может быть срезана многоточием — полное имя (и предупреждение) в подсказке. */}
      <Tip content={warning ? `${warning}. ${MODEL_TITLE}` : agent.label || agent.provider}>
        <span className={`session-bar__model${warning ? " session-bar__model--warn" : ""}`}>
          {agent.label || agent.provider}
          {compact && warning && <span className="sr-only"> ({warning})</span>}
        </span>
      </Tip>
      <Tip content={profileTitle(profile)}>
        <span className={`${profile === "personal" ? BADGE_CLASS.run : "badge"} session-bar__profile session-bar__profile--${profile}`}>
          {PROFILE_LABELS[profile]}
        </span>
      </Tip>
      {/* Как действует по просьбе (0.4): в узкой — только «автомод недоступен». */}
      {mode && (!compact || mode.warn) && (
        <Tip content={canTitle}>
          <span className={`${mode.warn ? BADGE_CLASS.temp : `${BADGE_CLASS.plain}`} session-bar__mode${mode.warn ? " session-bar__mode--warn" : ""}`}>
            <span className="session-bar__note-text">{mode.text}</span>
          </span>
        </Tip>
      )}
      {/* В строке — только предупреждение (не та модель); остальные пометки — в «Что я знаю». */}
      {!compact && warning && (
        <Tip content={MODEL_TITLE}>
          <span className={`${BADGE_CLASS.temp} session-bar__note session-bar__note--warn`}>
            <span className="session-bar__note-text">{warning}</span>
          </span>
        </Tip>
      )}
      <span className="session-bar__end">
        {compact ? (
          <IconButton icon={BookOpen} label="Что я знаю" variant="secondary" aria-expanded={!!anchor} onClick={toggle} />
        ) : (
          <Button icon={BookOpen} aria-expanded={!!anchor} onClick={toggle}>Что я знаю</Button>
        )}
      </span>
      {anchor && (
        <Popover anchor={anchor} onClose={() => setAnchor(null)} label="Что я знаю" width={340} align="end" anchorToggles>
          <div className="session-know">
            <div className="session-know__facts">
              <p className="session-know__line"><span className="session-know__key">Модель:</span> {agent.label || agent.provider}</p>
              <p className="session-know__line"><span className="session-know__key">Профиль:</span> {PROFILE_LABELS[profile]}</p>
              <p className="session-know__line"><span className="session-know__key">Видит:</span> {sees}</p>
              {mode && (
                <p className="session-know__line"><span className="session-know__key">Действия:</span> {mode.text}</p>
              )}
              {can && (
                <div className="session-bar__can">
                  <p className="session-know__line"><span className="session-know__key">Может:</span> {can}</p>
                  {/* Пояснение — текстом, а не скрытой подсказкой: место в поповере есть. */}
                  {canTitle && <p className="session-know__hint">{canTitle}</p>}
                </div>
              )}
            </div>
            {notes.map((n) => (
              <Tip key={n.text} content={n.title && n.title !== n.text ? n.title : undefined}>
                <p className="session-know__note">{n.text}</p>
              </Tip>
            ))}
            {grants.length > 0 && (
              <section className="session-know__section session-bar__grants">
                <h4 className="session-know__title">Разрешено до конца встречи</h4>
                <p className="session-know__hint">Такие действия ассистент выполняет без карточки.</p>
                <ul className="session-know__grants">
                  {grants.map((g) => (
                    <li key={g.id} className="session-bar__grant">
                      <span className="session-know__grant-label">{g.label}</span>
                      {onRevokeGrant && (
                        <IconButton icon={X} size="xs" variant="danger" label={`Отозвать: ${g.label}`}
                          tooltip={`Отозвать: ${g.label} — дальше снова с карточкой`} onClick={() => onRevokeGrant(g.id)} />
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <div className="session-know__controls">
              {onProfile && <ProfileSelect value={profile} onChange={onProfile} disabled={disabled} />}
              <FrequencySelect value={frequency} onChange={onFrequency} disabled={disabled} />
            </div>
            <section className="session-know__section">
              <h4 className="session-know__title">Сводка на сейчас</h4>
              <div className="session-know__summary"><LiveSummary summary={summary} fresh={NO_FRESH} /></div>
            </section>
          </div>
        </Popover>
      )}
    </div>
  );
}
