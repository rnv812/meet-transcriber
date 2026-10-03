/**
 * Настройки «Расшифровка: подсветка и разметка» (`transcript_view`): что из
 * анализа встречи показывать в карточке — значки типов реплик и фильтры,
 * полосу у важных реплик, заголовки глав, «Наблюдения», кривую важности над
 * плеером, подписи глав на полосе плеера; ссылки на задачи Jira (адрес,
 * проекты, проект по умолчанию, шаблон ключа — `integrations.jira_*`,
 * JiraSettings).
 *
 * Это только отображение: что размечать, решает раздел «Анализ встречи».
 */

import { HelpTip, TipLine } from "../../ui/HelpTip";
import { PlayerKeysTip } from "../card/PlayerKeysTip";
import { dropHiddenJiraChanges, JiraSettings, jiraChangesInvalid } from "./JiraSettings";
import { Radio, Switch, type Raw, type SetFn } from "./Section";

/** Правки, которые нельзя сохранить: негодный адрес Jira, проекты или шаблон ключа. */
export function markupChangesInvalid(changes: Raw): boolean {
  return jiraChangesInvalid(changes);
}

/** Ссылки на Jira выключены — негодное в их полях не уходит резиденту (см. JiraSettings). */
export function dropHiddenJira(changes: Raw, draft: Raw): void {
  dropHiddenJiraChanges(changes, draft);
}

export function MarkupTip() {
  return (
    <HelpTip label="Что такое подсветка и разметка" title="Подсветка и разметка">
      <TipLine>
        Разметку делает анализ встречи (раздел «Анализ встречи»): типы реплик, важность, главы и наблюдения.
        Здесь выбирается, что из этого показывать в карточке записи.
      </TipLine>
      <TipLine>Пока анализа нет, расшифровка и плеер выглядят как обычно.</TipLine>
    </HelpTip>
  );
}

export function CurveTip() {
  return (
    <HelpTip label="Что такое кривая важности" title="Кривая важности">
      <TipLine>
        Над полосой плеера рисуется кривая: чем она выше, тем важнее этот момент встречи по оценке анализа.
        Так проще найти главное, как «самые пересматриваемые» моменты на YouTube.
      </TipLine>
      <TipLine>«Только важное» в плеере проигрывает лишь самые важные фрагменты, пропуская остальное.</TipLine>
    </HelpTip>
  );
}

export function MarkupSection({ draft, set }: { draft: Raw; set: SetFn }) {
  const v = (k: string) => draft.transcript_view?.[k];
  const on = (k: string) => v(k) !== false;
  const curve = (v("curve") as "always" | "hover" | "off" | undefined) ?? "hover";
  return (
    <>
      <p className="muted sdesc">
        Что из анализа встречи показывать в карточке записи. Что размечать, выбирается в разделе «Анализ встречи».
      </p>
      <h3 className="shead">Расшифровка</h3>
      <Switch label="Значки типов реплик" help={<MarkupTip />}
        hint="Вопрос, решение, задача, риск, идея — значок в начале реплики и фильтры над лентой"
        value={on("types")} onChange={(x) => set("transcript_view", "types", x)} />
      <Switch label="Полоса у важных реплик" hint="Самые важные реплики (около 15 %) отмечены полосой слева"
        value={on("importance")} onChange={(x) => set("transcript_view", "importance", x)} />
      <Switch label="Заголовки глав" hint="Главы встречи — заголовками в ленте, с кнопкой «Обсудить главу с агентом»"
        value={on("chapters")} onChange={(x) => set("transcript_view", "chapters", x)} />
      <Switch label="Блок «Наблюдения»"
        hint="Противоречия, на что обратить внимание, что сделать после встречи — над лентой реплик"
        value={on("insights")} onChange={(x) => set("transcript_view", "insights", x)} />
      <h3 className="shead">Плеер</h3>
      <Radio label="Кривая важности над плеером" help={<CurveTip />} value={curve}
        options={[
          { value: "always", label: "Всегда" },
          { value: "hover", label: "При наведении" },
          { value: "off", label: "Не показывать" },
        ]}
        onChange={(x) => set("transcript_view", "curve", x)} />
      <Switch label="Подписи глав на полосе плеера" help={<PlayerKeysTip />}
        hint="Номер и короткое название главы под полосой; в узком плеере — только номера"
        value={on("bar_labels")} onChange={(x) => set("transcript_view", "bar_labels", x)} />
      <JiraSettings draft={draft} set={set} />
    </>
  );
}
