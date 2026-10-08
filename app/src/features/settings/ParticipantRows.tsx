/**
 * Настройки агента-участника (раздел «Ассистент» → «Участник встречи» и «База
 * знаний», 0.3.6):
 *
 * - «Ассистент — участник встречи» (`assist.participant`) — во время встречи ассистент
 *   пишет в чат как участник; выключен — прежние подсказки и «Спросить»
 *   (запасной режим в 0.3.6), и тогда видны их настройки (`LiveHintsRows`);
 * - «Профиль по умолчанию» (`assist.profile`: work / personal, 0.3.7) — с каким
 *   профилем стартует сессия, если его не выбрали в меню записи; в панели
 *   встречи профиль меняется на ходу (только для этой сессии);
 * - «Как часто писать» (`assist.frequency`: less / normal / more);
 * - «Показывать ассистенту карту: базу знаний и прошлые встречи группы» (`assist.kb_map`);
 * - «Не показывать ассистенту» — исключённые папки базы (`assist.kb_exclude`,
 *   по умолчанию «Личное/», «.trash/»): список с выбором папки внутри базы.
 *   Claude Code запрещает их чтение правилами; Codex и OpenCode такого не
 *   умеют — для них исключения только просьба в инструкции (пометка);
 * - «Расширенные возможности ассистента (файлы вне встречи, MCP, веб) — по согласию»
 *   (`assist.agent_freedom`, 0.3.7, по умолчанию вкл.): всё, что умеет CLI модели, но без
 *   просьбы — только эта встреча и вложения, изменения — после отдельной кнопки;
 *   выключено — как в 0.3.6 (только чтение встречи и базы знаний);
 * - «Действия ассистента» (`assist.agent_mode`, 0.4, при расширенных возможностях):
 *   «Сам, рискованное — спрашивает» (`auto`, по умолчанию — автомод Claude Code) или
 *   «Спрашивать каждое» (`confirm`, как 0.3.7);
 * - какие модели видят картинки (пометка).
 *
 * «Только сводка» (`assist.activity: summary`) выключает и агента — если она
 * осталась от прежнего режима, видно предупреждение с «Вернуть чат».
 */

import { TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { kbExcludeEntry, kbRelative } from "../../lib/kb";
import { pickFolder } from "../../lib/shell";
import { fieldClass } from "./fields";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Icon } from "../../ui/Icon";
import { IconButton } from "../../ui/IconButton";
import { Tip } from "../../ui/Tip";
import type { AgentProfile } from "../../lib/types";
import { PROFILES, PROFILE_LABELS, profileOf } from "../../live/profiles";
import { Row, Segmented, Switch, type Raw, type SetFn } from "./Section";

type Frequency = "less" | "normal" | "more";
const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "less", label: "реже" },
  { value: "normal", label: "обычно" },
  { value: "more", label: "чаще" },
];
/** Профиль сессии по умолчанию (`assist.profile`). */
const PROFILE_OPTIONS: { value: AgentProfile; label: string }[] = PROFILES.map((p) => ({ value: p, label: PROFILE_LABELS[p] }));
export const PROFILE_DEFAULT_LABEL = "Профиль по умолчанию";
export const PROFILE_HINTS: Record<AgentProfile, string> = {
  work: "Рабочая встреча: база знаний, прошлые встречи, подсказки по встрече. Профиль можно выбрать при старте и сменить в панели встречи",
  personal: "Личный: созвон, стрим, видео — без базы знаний и рабочих советов. Профиль можно выбрать при старте и сменить в панели встречи",
};
/** Как у резидента (`settings.KB_EXCLUDE_DEFAULT`). */
export const KB_EXCLUDE_DEFAULT = ["Личное/", ".trash/"];
/** Модели, которые не умеют запрещать чтение папок: исключения — только просьба. */
const DENY_BY_PROMPT: Record<string, string> = { codex: "Codex", opencode: "OpenCode" };
/** Кто видит картинки (как `llm.VISION_PROVIDERS`). */
const VISION: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };
const LABELS: Record<string, string> = {
  "claude-code": "Claude Code", codex: "Codex", opencode: "OpenCode", "openai-compatible": "Локальная модель",
};
export const PARTICIPANT_LABEL = "Ассистент — участник встречи";
export const KB_MAP_LABEL = "Показывать ассистенту карту: базу знаний и прошлые встречи группы";
export const VISION_NOTE = "Картинки видят Claude Code и Codex; OpenCode и локальная модель получают только текст сообщения";
export const DENY_NOTE = "исключения — только просьба";
export const FREEDOM_LABEL = "Расширенные возможности ассистента (файлы вне встречи, MCP, веб) — по согласию";
/** Подсказка под переключателем — одна строка; подробности и оговорки — в «?» (FreedomTip). */
export const FREEDOM_ON_HINT =
  "По вашей просьбе — файлы, команды, веб и ваши MCP с вашими правами. "
  + "Без вашей просьбы — только чтение этой встречи и вложений";
/** Как ассистент действует по просьбе (`assist.agent_mode`, 0.4): сам, как автомод Claude Code, или спрашивает каждое действие. */
type AgentMode = "auto" | "confirm";
export const AGENT_MODE_LABEL = "Действия ассистента";
const AGENT_MODE_OPTIONS: { value: AgentMode; label: string }[] = [
  { value: "auto", label: "Действует сам" },
  { value: "confirm", label: "Спрашивает каждое" },
];
export const AGENT_MODE_HINTS: Record<AgentMode, string> = {
  auto: "Обычные действия ассистент выполняет сам, рискованные — после вашего разрешения (удаление, запись вне "
    + "рабочих папок, отправка наружу). Ход по одной речи встречи ничего не меняет",
  confirm: "Каждое действие — карточкой «Разрешить» в чате, как в 0.3.7. Ход по одной речи встречи ничего не меняет",
};
/** Codex/OpenCode по уровню «Действия ассистента»: в автомоде действуют по просьбе, с подтверждением — только читают. */
export const FREEDOM_FILES_NOTE = "Codex/OpenCode: только чтение файлов по вашей просьбе; MCP, веб и действия — только с Claude Code";
export const FREEDOM_ACT_NOTE = "Codex/OpenCode: по вашей просьбе правят рабочие папки и выполняют команды; MCP — только с Claude Code";
/** Закрытые папки и чувствительные пути у моделей без проверки каждого вызова. */
export const FREEDOM_CLOSED_NOTE: Record<string, string> = {
  codex: "У Codex закрытые папки и ключи с паролями закрыты только правилом в инструкции — песочница читает весь диск",
  opencode: "У OpenCode закрытые папки и ключи с паролями закрыты его правами (не проверено на живом OpenCode)",
};
export const FREEDOM_OFF_HINT = "Выключено: как в 0.3.6 — ассистент читает эту встречу, базу знаний и прошлые встречи";
/** Модели без проверки каждого вызова: со свободой им дают только чтение файлов. */
const FILES_ONLY: Record<string, string> = { codex: "Codex", opencode: "OpenCode" };

function ParticipantTip() {
  return (
    <HelpTip label="Что такое ассистент — участник встречи" title={PARTICIPANT_LABEL}>
      <TipLine>
        Во время встречи ассистент слушает разговор и сам пишет в чат панели — коротко, от первого лица, со
        своими кнопками. Ему можно написать, приложить файл или скриншот и отреагировать 👍 👎 ❓.
      </TipLine>
      <TipLine>
        После встречи разговор продолжается во вкладке «Ассистент» карточки записи.
      </TipLine>
      <TipLine>
        Выключен — как раньше: подсказки и «Спросить» в панели. Применяется со следующего запуска ассистента.
      </TipLine>
    </HelpTip>
  );
}

function FreedomTip() {
  return (
    <HelpTip label="Что дают расширенные возможности" title={FREEDOM_LABEL}>
      <TipLine>
        С Claude Code ассистент получает всё, что умеет модель на этом компьютере: файлы вне встречи (например,
        «Загрузки»), веб, ваши MCP-серверы с вашими доступами и команды. Он спрашивает вас кнопками: «Я тоже гляну
        этот файл?», «Проверить задачу ABC-123 в Jira?».
      </TipLine>
      <TipLine>
        Без вашей просьбы — только эта встреча и ваши вложения. Ваше сообщение, нажатая кнопка или ❓ — согласие
        на чтение по этой просьбе: файлы, поиск в вебе, чтение через MCP.
      </TipLine>
      <TipLine>
        По просьбе он действует сам: читает, правит файлы в рабочих папках, выполняет
        команды, зовёт MCP — каждый вызов виден строкой в чате. Рискованное — удаление, запись вне рабочих папок,
        отправку наружу, изменения через MCP — Meet показывает карточкой с точным вызовом: «Разрешить один раз»,
        «Разрешать такое до конца встречи» или «Отклонить». «Действия ассистента» → «Спрашивает каждое» —
        карточка на каждое действие. Без ответа за 2 минуты — не выполняется. Лишнее Meet блокирует — в чате
        появится строка «Ассистент хотел … — запрос заблокирован».
      </TipLine>
      <TipLine>
        MCP-инструменты, чьё имя не похоже на запись, выполняются без вопроса. «git commit» и «git pull»,
        разрешённые до конца встречи, запускают хуки самого репозитория.
      </TipLine>
      <TipLine>
        Закрыто всегда: папки «Не показывать ассистенту», ключи и пароли (SSH, облака, настройки Claude Code и
        Codex, профили браузеров, служебные файлы Meet, .env), подагенты и фоновые команды, локальные адреса.
        Собственные хуки Claude Code не запускаются; ваши CLAUDE.md, навыки и плагины ассистент видит.
      </TipLine>
      <TipLine>
        Codex и OpenCode остановить вызов до вашего решения не дают: по вашей просьбе они правят рабочие папки и
        выполняют команды сами (удаление и отправку наружу у Codex удерживают только инструкция и его проверка,
        у OpenCode — правила разрешений); без просьбы — только чтение. MCP — только с Claude Code.
        У Codex и закрытые папки, и ключи с паролями закрыты только правилом в инструкции: его песочница читает
        весь диск.
      </TipLine>
      <TipLine>
        Риск: текст файлов, страниц и задач может пытаться командовать ассистентом. Без вашей просьбы он ничего
        не прочтёт вне встречи, без карточки ничего не сделает; разрешённое выполняет с вашими правами.
        Выключено — как в 0.3.6: ассистент читает встречу, базу знаний и прошлые встречи; Codex и тогда загружает
        свои MCP-серверы, как в 0.3.6.
      </TipLine>
    </HelpTip>
  );
}

function KbExcludeEditor({ value, kbRoot, provider, onChange }: {
  value: unknown; kbRoot: string | null; provider: string | null; onChange: (v: string[]) => void;
}) {
  const list = Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : KB_EXCLUDE_DEFAULT;
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const add = (entry: string | null, bad: string) => {
    if (!entry) { setError(bad); return false; }
    setError(null);
    if (!list.some((x) => x.toLowerCase() === entry.toLowerCase())) onChange([...list, entry]);
    return true;
  };
  const pick = async () => {
    if (!kbRoot) return;
    const path = await pickFolder(kbRoot).catch(() => null);
    if (!path) return;
    const rel = kbRelative(kbRoot, path);
    if (rel === null) { setError("Эта папка вне базы знаний — выберите папку внутри неё"); return; }
    if (rel === "") { setError("Это сама база знаний — выберите папку внутри неё"); return; }
    add(kbExcludeEntry(rel), "Не получилось добавить эту папку");
  };
  const typed = () => {
    if (add(kbExcludeEntry(text), "Папка — путь внутри базы знаний, например «Личное/», без буквы диска и «..»")) setText("");
  };
  const deny = provider ? DENY_BY_PROMPT[provider] : undefined;
  const same = list.length === KB_EXCLUDE_DEFAULT.length && list.every((x, k) => x === KB_EXCLUDE_DEFAULT[k]);
  return (
    <Row label="Не показывать ассистенту" stack
      hint={kbRoot ? "Папки базы знаний, которых ассистент не видит и не читает (пути внутри базы)"
        : "Папки базы знаний, которых ассистент не видит и не читает. Сначала задайте базу знаний выше"}>
      <div className="kb-exclude" role="group" aria-label="Исключённые папки">
        {list.length ? (
          <ul className="kb-exclude__list" aria-label="Исключённые папки базы знаний">
            {list.map((p) => (
              <li key={p} className="kb-exclude__item">
                <code className="path">{p}</code>
                <IconButton icon={X} size="xs" label={`Убрать исключение ${p}`}
                  onClick={() => onChange(list.filter((x) => x !== p))} />
              </li>
            ))}
          </ul>
        ) : <span className="muted kb-exclude__empty">Исключений нет — ассистент видит всю базу знаний</span>}
        <div className="kb-exclude__add">
          <Button onClick={() => void pick()} disabled={!kbRoot}>Выбрать папку…</Button>
          <input type="text" className={fieldClass({ mono: true })} aria-label="Папка внутри базы знаний"
            placeholder="Папка/" value={text} spellCheck={false}
            disabled={!kbRoot}
            onChange={(e) => { setText(e.target.value); setError(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); typed(); } }} />
          <Button onClick={typed} disabled={!kbRoot || !text.trim()}>Добавить</Button>
          {!same && <Button variant="link" onClick={() => { setError(null); onChange([...KB_EXCLUDE_DEFAULT]); }}>По умолчанию</Button>}
        </div>
        {error && <span className="error" role="alert">{error}</span>}
        {deny && (
          <Tip content={`${deny} не умеет запрещать чтение папок: исключённые папки указаны ему только просьбой в инструкции`}>
            <span className="badge badge--stale kb-exclude__note">{deny}: {DENY_NOTE}</span>
          </Tip>
        )}
      </div>
    </Row>
  );
}

export function ParticipantRows({ draft, set, provider }: {
  draft: Raw; set: SetFn;
  /** Модель по умолчанию, которая ответит сейчас (null — неизвестно). */
  provider: string | null;
}) {
  const assist = draft.assist ?? {};
  const on = assist.participant !== false;
  const frequency = (FREQUENCIES.some((f) => f.value === assist.frequency) ? assist.frequency : "more") as Frequency;
  const profile = profileOf(assist.profile);
  const sees = provider ? provider in VISION : null;
  const freedom = assist.agent_freedom !== false;
  const agentMode: AgentMode = assist.agent_mode === "confirm" ? "confirm" : "auto";
  const filesOnly = provider ? FILES_ONLY[provider] : undefined;
  return (
    <>
      <Switch label={PARTICIPANT_LABEL} help={<ParticipantTip />} value={on} onChange={(v) => set("assist", "participant", v)}
        hint={on ? "Во время встречи ассистент пишет в чат как участник; после — вкладка «Ассистент» в карточке"
          : "Выключен: прежние подсказки и «Спросить» во время встречи"} />
      {on && (
        <>
          {assist.activity === "summary" && (
            <div className="callout callout--warn scallout" role="alert">
              <Icon as={TriangleAlert} className="ic" />
              <p>
                Выбрано «Только сводка» прежнего ассистента — во время встречи он молчит.{" "}
                <Button variant="link" onClick={() => set("assist", "activity", "calm")}>Вернуть чат</Button>
              </p>
            </div>
          )}
          <Segmented label={PROFILE_DEFAULT_LABEL} value={profile} options={PROFILE_OPTIONS}
            hint={PROFILE_HINTS[profile]} onChange={(v) => set("assist", "profile", v)} />
          <Segmented label="Как часто писать" value={frequency} options={FREQUENCIES}
            hint="Просьба к ассистенту в инструкции; меняется и в панели встречи"
            onChange={(v) => set("assist", "frequency", v)} />
          <Switch label={FREEDOM_LABEL} help={<FreedomTip />} value={freedom}
            onChange={(v) => set("assist", "agent_freedom", v)}
            hint={freedom
              ? (filesOnly && provider
                ? `${draft.assist?.agent_mode === "confirm" ? FREEDOM_FILES_NOTE : FREEDOM_ACT_NOTE}. ${FREEDOM_CLOSED_NOTE[provider]}`
                : FREEDOM_ON_HINT)
              : FREEDOM_OFF_HINT} />
          {/* Как действует по просьбе (`assist.agent_mode`, 0.4) — только с расширенными возможностями. */}
          {freedom && (
            <Segmented label={AGENT_MODE_LABEL} value={agentMode} options={AGENT_MODE_OPTIONS}
              hint={AGENT_MODE_HINTS[agentMode]} onChange={(v) => set("assist", "agent_mode", v)} />
          )}
          <p className="muted sdesc participant__vision">
            {VISION_NOTE}
            {sees === false && provider ? `. ${LABELS[provider] ?? provider} картинки не видит` : ""}
          </p>
        </>
      )}
    </>
  );
}

/**
 * База знаний агента-участника (карточка «База знаний» в «Ассистенте», под
 * папкой базы): карта и исключённые папки. Только при включённом участнике.
 */
export function KnowledgeRows({ draft, set, provider }: {
  draft: Raw; set: SetFn;
  /** Модель по умолчанию, которая ответит сейчас (null — неизвестно). */
  provider: string | null;
}) {
  const assist = draft.assist ?? {};
  if (assist.participant === false) return null;
  const kbRoot = (draft.assistant?.knowledge_dir as string | null | undefined) || null;
  return (
    <>
      <Switch label={KB_MAP_LABEL} value={assist.kb_map !== false}
        onChange={(v) => set("assist", "kb_map", v)}
        hint="Папки и названия документов базы и список прошлых встреч группы, без содержимого. Читает он только по вашей просьбе или с согласия" />
      <KbExcludeEditor value={assist.kb_exclude} kbRoot={kbRoot} provider={provider}
        onChange={(v) => set("assist", "kb_exclude", v)} />
    </>
  );
}
