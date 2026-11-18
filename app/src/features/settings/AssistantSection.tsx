/**
 * «Ассистент» (0.4): агент-участник встречи (профиль, как часто писать,
 * расширенные возможности) и база знаний (папка, карта, исключения); в «Тонкой
 * настройке» — живая расшифровка (окно, «Не отвлекать», прежний режим подсказок). Какая модель
 * его ведёт — в «Моделях ИИ»; запуск агента во вкладке «Агент» — в
 * «Дополнительно». Куда выгружаются встречи — раздел «Экспорт».
 *
 * Сверху (0.6.2) — «Как сейчас настроено» по сохранённым настройкам; модель,
 * которая ведёт ассистента, — ссылка в «Модели и анализ».
 */

import type { Endpoint } from "../../lib/api";
import type { AssistantInfo } from "../../lib/types";
import { PROFILE_LABELS, profileOf } from "../../live/profiles";
import { Button } from "../../ui/Button";
import { NumberField } from "./fields";
import { LiveHintsRows } from "./LiveHintsRows";
import { currentProvider, providerTitle, useAssistantInfo } from "./ModelsSection";
import { KnowledgeRows, PARTICIPANT_LABEL, PROFILE_DEFAULT_LABEL, ParticipantRows } from "./ParticipantRows";
import { FineTuning, FolderRow, NowLine, Row, SeeAlso, SettingsCard, type NowPart, type Raw, type SetFn } from "./Section";
import { KnowledgeTip, LiveWindowTip } from "./tips";

export const WINDOW_MIN = 5;
export const WINDOW_MAX = 120;

const windowInvalid = (value: unknown): boolean =>
  typeof value !== "number" || !Number.isFinite(value) || value < WINDOW_MIN || value > WINDOW_MAX;

/**
 * Правки раздела нельзя сохранить: окно вне правил. Смотрим только изменённое
 * (`changes` — то, что уйдёт в PATCH): значение, уже лежащее в файле, не должно
 * запирать «Сохранить» для остальных разделов.
 */
export function assistantChangesInvalid(changes: Raw): boolean {
  const win = changes.assist?.window_seconds;
  return win !== undefined && windowInvalid(win);
}

const FREQUENCY_TEXT: Record<string, string> = { less: "реже", normal: "обычно", more: "чаще" };
const KB_LABEL = "База знаний для ассистента";

/**
 * «Как сейчас» по сохранённым настройкам: участник или прежние подсказки,
 * какая модель ведёт, профиль, как часто пишет, база знаний.
 */
export function assistantNow(saved: Raw, info: AssistantInfo | null): NowPart[] {
  const assist = saved.assist ?? {};
  const on = assist.participant !== false;
  const parts: NowPart[] = [{ text: on ? "участник встречи" : "прежние подсказки", to: PARTICIPANT_LABEL }];
  if (on) {
    const provider = currentProvider(info, saved);
    parts.push(
      { text: provider ? `ведёт ${providerTitle(provider)}` : "ведёт модель по умолчанию", to: "Модели", section: "models" },
      { text: `профиль «${PROFILE_LABELS[profileOf(assist.profile)]}»`, to: PROFILE_DEFAULT_LABEL },
      { text: `пишет ${FREQUENCY_TEXT[String(assist.frequency)] ?? FREQUENCY_TEXT.more}`, to: "Как часто писать" },
    );
  }
  const kb = saved.assistant?.knowledge_dir;
  const folder = typeof kb === "string" ? kb.split(/[\\/]/).filter(Boolean).at(-1) : undefined;
  parts.push({ text: folder ? `база знаний — ${folder}` : "без базы знаний", to: KB_LABEL });
  return parts;
}

export function AssistantSection({ draft, saved, set, endpoint, onOpenModels }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint;
  /** Перейти в «Модели и анализ»; без него ссылки нет. */
  onOpenModels?: () => void;
}) {
  const { info } = useAssistantInfo(endpoint, saved);
  const provider = currentProvider(info, draft);
  const win = draft.assist?.window_seconds as number | null | undefined;
  return (
    <>
      <NowLine parts={assistantNow(saved, info)} />
      {onOpenModels && (
        <SeeAlso head>
          Какая модель ведёт ассистента — в разделе{" "}
          <Button variant="link" onClick={onOpenModels}>«Модели и анализ»</Button>.
        </SeeAlso>
      )}
      <SettingsCard title="Участник встречи">
        <ParticipantRows draft={draft} set={set} provider={provider} />
      </SettingsCard>
      <SettingsCard title="База знаний">
        <FolderRow label={KB_LABEL} help={<KnowledgeTip />}
          hint="Папка с материалами, по которой ассистент сверяет термины и имена"
          value={(draft.assistant?.knowledge_dir as string | null | undefined) ?? null}
          onChange={(v) => set("assistant", "knowledge_dir", v)} />
        <KnowledgeRows draft={draft} set={set} provider={provider} />
      </SettingsCard>
      <FineTuning pinned={win !== undefined && windowInvalid(win)}>
        <Row label="Окно живой расшифровки, с" htmlFor="assist-window" help={<LiveWindowTip min={WINDOW_MIN} max={WINDOW_MAX} />}
          hint="Как часто расшифровывается новый звук. Применяется со следующего запуска ассистента">
          <NumberField id="assist-window" label="Окно живой расшифровки" min={WINDOW_MIN} max={WINDOW_MAX} step={5}
            value={win ?? null} invalid={win !== undefined && windowInvalid(win)}
            onChange={(n) => set("assist", "window_seconds", n)} />
          {win !== undefined && windowInvalid(win) && <span className="error">От {WINDOW_MIN} до {WINDOW_MAX} секунд</span>}
        </Row>
        <LiveHintsRows draft={draft} set={set} provider={provider} hints={draft.assist?.participant === false} />
      </FineTuning>
    </>
  );
}
