/**
 * Настройки «Расшифровка: подсветка и разметка» (`transcript_view`): что из
 * анализа встречи показывать в карточке — значки типов реплик и фильтры,
 * полосу у важных реплик, заголовки глав, «Наблюдения», кривую важности над
 * плеером, подписи глав на полосе плеера; ссылки на задачи Jira (адрес и
 * шаблон ключей — `integrations.jira_base_url`, `integrations.jira_keys`).
 *
 * Это только отображение: что размечать, решает раздел «Анализ встречи».
 */

import { DEFAULT_JIRA_KEYS, jiraBaseError, jiraKeysError } from "../../lib/jira";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { PlayerKeysTip } from "../card/PlayerKeysTip";
import { Radio, Row, Switch, type Raw, type SetFn } from "./Section";

/** Правки, которые нельзя сохранить: негодный адрес Jira или шаблон ключей. */
export function markupChangesInvalid(changes: Raw): boolean {
  const i = changes.integrations ?? {};
  return (typeof i.jira_base_url === "string" && jiraBaseError(i.jira_base_url) !== null)
    || (typeof i.jira_keys === "string" && jiraKeysError(i.jira_keys) !== null);
}

export function JiraTip() {
  return (
    <HelpTip label="Как работают ссылки на Jira" title="Ссылки на задачи Jira">
      <TipLine>
        Ключи задач вида <code>SPR-131</code> в расшифровке, итогах и наблюдениях становятся ссылками на задачу в
        Jira. Агент для этого не нужен.
      </TipLine>
      <TipLine>
        Адрес — начало ссылки на вашу Jira, например <code>https://jira.example.com</code>. Ссылка на задачу:
        адрес + <code>/browse/SPR-131</code>. Приложение открывает только этот адрес и только по https.
      </TipLine>
      <TipLine>
        Ключи задач — список проектов через запятую (<code>SPR, OPS</code>) или регулярное выражение. По умолчанию
        подходит любой ключ: <code>{DEFAULT_JIRA_KEYS}</code>.
      </TipLine>
    </HelpTip>
  );
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
  const base = String(draft.integrations?.jira_base_url ?? "");
  const keys = String(draft.integrations?.jira_keys ?? "");
  const baseError = jiraBaseError(base);
  const keysError = jiraKeysError(keys);
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
      <h3 className="shead">Ссылки на Jira</h3>
      <Switch label="Ссылки на задачи Jira" help={<JiraTip />}
        hint="Ключи задач в расшифровке, итогах и наблюдениях открываются в Jira"
        value={on("jira")} onChange={(x) => set("transcript_view", "jira", x)} />
      <Row label="Адрес Jira" htmlFor="jira-base" hint={baseError
        ? <span className="error">{baseError}</span> : "Пусто — ссылок нет. Например, https://jira.example.com"}>
        <input id="jira-base" type="text" inputMode="url" placeholder="https://jira.example.com" value={base} spellCheck={false}
          aria-invalid={baseError ? true : undefined} onChange={(e) => set("integrations", "jira_base_url", e.target.value)} />
      </Row>
      <Row label="Ключи задач" htmlFor="jira-keys" hint={keysError
        ? <span className="error">{keysError}</span> : "Проекты через запятую (SPR, OPS) или регулярное выражение; пусто — любой ключ"}>
        <input id="jira-keys" type="text" placeholder={DEFAULT_JIRA_KEYS} value={keys} spellCheck={false}
          aria-invalid={keysError ? true : undefined} onChange={(e) => set("integrations", "jira_keys", e.target.value)} />
      </Row>
    </>
  );
}
