/**
 * «Jira» (0.4, прежде — часть «Подсветки расшифровки»): ссылки на задачи,
 * адрес, проекты с вариантами названия, проект по умолчанию (JiraSettings);
 * в «Тонкой настройке» — шаблон ключа для текста. Ключи прежние:
 * `transcript_view.jira` (его же переключает «Ссылки на задачи: показывать»
 * в «Анализе встречи»), `integrations.jira_*`.
 */

import { JiraPatternRow, JiraSettings } from "./JiraSettings";
import { FineTuning, SettingsCard, type Raw, type SetFn } from "./Section";

export function JiraSection({ draft, set }: { draft: Raw; set: SetFn }) {
  // Заданный шаблон виден сразу, как в 0.3.7 (там блок был раскрыт, если шаблон не пуст).
  const pattern = String(draft.integrations?.jira_pattern ?? "");
  return (
    <>
      <SettingsCard title="Ссылки на задачи">
        <JiraSettings draft={draft} set={set} />
      </SettingsCard>
      <FineTuning defaultOpen={pattern !== ""}>
        <JiraPatternRow draft={draft} set={set} />
      </FineTuning>
    </>
  );
}
