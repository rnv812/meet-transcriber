/**
 * «Jira» (0.4, прежде — часть «Подсветки расшифровки»): ссылки на задачи,
 * адрес, проекты с вариантами названия, проект по умолчанию и шаблон ключа
 * для текста (JiraSettings). Ключи прежние: `transcript_view.jira`,
 * `integrations.jira_*`.
 */

import { JiraSettings } from "./JiraSettings";
import { SettingsCard, type Raw, type SetFn } from "./Section";

export function JiraSection({ draft, set }: { draft: Raw; set: SetFn }) {
  return (
    <SettingsCard title="Ссылки на задачи">
      <JiraSettings draft={draft} set={set} />
    </SettingsCard>
  );
}
