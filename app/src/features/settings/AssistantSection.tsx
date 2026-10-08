/**
 * «Ассистент» (0.4): агент-участник встречи (профиль, как часто писать,
 * расширенные возможности) и база знаний (папка, карта, исключения); в «Тонкой
 * настройке» — живая расшифровка (окно, «Не отвлекать», прежний режим подсказок). Какая модель
 * его ведёт — в «Моделях ИИ»; запуск агента во вкладке «Агент» — в
 * «Дополнительно». Куда выгружаются встречи — раздел «Экспорт».
 */

import type { Endpoint } from "../../lib/api";
import { Button } from "../../ui/Button";
import { LiveHintsRows } from "./LiveHintsRows";
import { currentProvider, useAssistantInfo } from "./ModelsSection";
import { KnowledgeRows, ParticipantRows } from "./ParticipantRows";
import { FineTuning, FolderRow, Row, SeeAlso, SettingsCard, type Raw, type SetFn } from "./Section";
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

export function AssistantSection({ draft, saved, set, endpoint, onOpenModels }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint;
  /** Перейти в «Модели ИИ»; без него ссылки нет. */
  onOpenModels?: () => void;
}) {
  const { info } = useAssistantInfo(endpoint, saved);
  const provider = currentProvider(info, draft);
  const win = draft.assist?.window_seconds as number | null | undefined;
  return (
    <>
      {onOpenModels && (
        <SeeAlso>
          Какая модель ведёт ассистента — в разделе{" "}
          <Button variant="link" onClick={onOpenModels}>«Модели ИИ»</Button>.
        </SeeAlso>
      )}
      <SettingsCard title="Участник встречи">
        <ParticipantRows draft={draft} set={set} provider={provider} />
      </SettingsCard>
      <SettingsCard title="База знаний">
        <FolderRow label="База знаний для ассистента" help={<KnowledgeTip />}
          hint="Папка с материалами, по которой ассистент сверяет термины и имена"
          value={(draft.assistant?.knowledge_dir as string | null | undefined) ?? null}
          onChange={(v) => set("assistant", "knowledge_dir", v)} />
        <KnowledgeRows draft={draft} set={set} provider={provider} />
      </SettingsCard>
      <FineTuning pinned={win !== undefined && windowInvalid(win)}>
        <Row label="Окно живой расшифровки, с" htmlFor="assist-window" help={<LiveWindowTip min={WINDOW_MIN} max={WINDOW_MAX} />}
          hint="Как часто расшифровывается новый звук. Применяется со следующего запуска ассистента">
          <input id="assist-window" type="number" className="num" min={WINDOW_MIN} max={WINDOW_MAX} step={5}
            value={win ?? ""}
            onChange={(e) => {
              const n = e.target.value === "" ? null : Number(e.target.value);
              set("assist", "window_seconds", n !== null && Number.isFinite(n) ? n : null);
            }} />
          {win !== undefined && windowInvalid(win) && <span className="error">От {WINDOW_MIN} до {WINDOW_MAX} секунд</span>}
        </Row>
        <LiveHintsRows draft={draft} set={set} provider={provider} hints={draft.assist?.participant === false} />
      </FineTuning>
    </>
  );
}
