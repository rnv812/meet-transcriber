/**
 * Настройки агента-участника (раздел «Ассистент» → «Живой ассистент», 0.3.6):
 *
 * - «Ассистент — участник встречи» (`assist.participant`) — во время встречи ассистент
 *   пишет в чат как участник; выключен — прежние подсказки и «Спросить»
 *   (запасной режим в 0.3.6), и тогда видны их настройки (`LiveHintsRows`);
 * - «Как часто писать» (`assist.frequency`: less / normal / more);
 * - «Показывать ассистенту карту: базу знаний и прошлые встречи группы» (`assist.kb_map`);
 * - «Не показывать ассистенту» — исключённые папки базы (`assist.kb_exclude`,
 *   по умолчанию «Личное/», «.trash/»): список с выбором папки внутри базы.
 *   Claude Code запрещает их чтение правилами; Codex и OpenCode такого не
 *   умеют — для них исключения только просьба в инструкции (пометка);
 * - какие модели видят картинки (пометка).
 *
 * «Только сводка» (`assist.activity: summary`) выключает и агента — если она
 * осталась от прежнего режима, видно предупреждение с «Вернуть чат».
 */

import { X } from "lucide-react";
import { useState } from "react";
import { kbExcludeEntry, kbRelative } from "../../lib/kb";
import { pickFolder } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { IconButton } from "../../ui/IconButton";
import { Radio, Row, Switch, type Raw, type SetFn } from "./Section";

type Frequency = "less" | "normal" | "more";
const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "less", label: "реже" },
  { value: "normal", label: "обычно" },
  { value: "more", label: "чаще" },
];
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
                <IconButton icon={X} size="sm" label={`Убрать исключение ${p}`}
                  onClick={() => onChange(list.filter((x) => x !== p))} />
              </li>
            ))}
          </ul>
        ) : <span className="muted kb-exclude__empty">Исключений нет — ассистент видит всю базу знаний</span>}
        <div className="kb-exclude__add">
          <Button onClick={() => void pick()} disabled={!kbRoot}>Выбрать папку…</Button>
          <input type="text" aria-label="Папка внутри базы знаний" placeholder="Папка/" value={text} spellCheck={false}
            disabled={!kbRoot}
            onChange={(e) => { setText(e.target.value); setError(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); typed(); } }} />
          <Button onClick={typed} disabled={!kbRoot || !text.trim()}>Добавить</Button>
          {!same && <Button variant="link" onClick={() => { setError(null); onChange([...KB_EXCLUDE_DEFAULT]); }}>По умолчанию</Button>}
        </div>
        {error && <span className="error" role="alert">{error}</span>}
        {deny && (
          <span className="kb-exclude__note" title={`${deny} не умеет запрещать чтение папок: исключённые папки указаны ему только просьбой в инструкции`}>
            {deny}: {DENY_NOTE}
          </span>
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
  const kbRoot = (draft.assistant?.knowledge_dir as string | null | undefined) || null;
  const sees = provider ? provider in VISION : null;
  return (
    <>
      <Switch label={PARTICIPANT_LABEL} help={<ParticipantTip />} value={on} onChange={(v) => set("assist", "participant", v)}
        hint={on ? "Во время встречи ассистент пишет в чат как участник; после — вкладка «Ассистент» в карточке"
          : "Выключен: прежние подсказки и «Спросить» во время встречи"} />
      {on && (
        <>
          {assist.activity === "summary" && (
            <div className="participant__warn" role="alert">
              <span>Выбрано «Только сводка» прежнего ассистента — во время встречи он молчит.</span>
              <Button variant="link" onClick={() => set("assist", "activity", "calm")}>Вернуть чат</Button>
            </div>
          )}
          <Radio label="Как часто писать" value={frequency} options={FREQUENCIES}
            hint="Просьба к ассистенту в инструкции; меняется и в панели встречи"
            onChange={(v) => set("assist", "frequency", v)} />
          <Switch label={KB_MAP_LABEL} value={assist.kb_map !== false}
            onChange={(v) => set("assist", "kb_map", v)}
            hint="Папки и названия документов базы и список прошлых встреч группы, без содержимого. Читает он только по вашей просьбе или с согласия" />
          <KbExcludeEditor value={assist.kb_exclude} kbRoot={kbRoot} provider={provider}
            onChange={(v) => set("assist", "kb_exclude", v)} />
          <p className="muted sdesc participant__vision">
            {VISION_NOTE}
            {sees === false && provider ? `. ${LABELS[provider] ?? provider} картинки не видит` : ""}
          </p>
        </>
      )}
    </>
  );
}
