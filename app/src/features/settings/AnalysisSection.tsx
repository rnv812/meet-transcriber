/**
 * Настройки «Анализ встречи» (0.4): ставить ли анализ сам (`analysis.auto`);
 * одна таблица «Размечать / Показывать» — что анализ размечает (`analysis.*`)
 * и что из этого видно в карточке (`transcript_view.*`; прежде — раздел
 * «Подсветка расшифровки»); плеер (кривая важности, подписи глав);
 * «Улучшать расшифровку автоматически» (`analysis.improve_auto`); название
 * (`analysis.title`, `assistant.auto_title`) и категория (`analysis.category`).
 *
 * Выключенная часть не запрашивается у модели (промпт короче) и не
 * показывается в карточке, поэтому «Показывать» без «Размечать» недоступно
 * (кроме ссылок на задачи: ключи в тексте узнаются и без анализа).
 * «Ссылки на задачи: показывать» — тот же ключ `transcript_view.jira`, что
 * «Ссылки на задачи Jira» в разделе «Jira». Анализ делает тот же агент, что
 * итоги, — провайдер выбирается в разделе «Модели ИИ».
 */

import { useId } from "react";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { PlayerKeysTip } from "../card/PlayerKeysTip";
import { Radio, SeeAlso, SettingsCard, Switch, type Raw, type SetFn } from "./Section";

/** Строка таблицы: подпись, ключ в `analysis` (размечать) и в `transcript_view` (показывать). */
export type MarkShowItem = {
  label: string; mark: string; show: string; hint: string;
  /** «Показывать» не зависит от разметки (ссылки на задачи по тексту работают и без анализа). */
  independent?: boolean;
};

/** Таблица «Размечать / Показывать» (порядок — как в окне). */
export const MARK_SHOW: MarkShowItem[] = [
  {
    label: "Типы реплик", mark: "types", show: "types",
    hint: "Вопрос, решение, задача, риск, идея, согласие, возражение. В карточке — значок в начале реплики и фильтры над лентой",
  },
  {
    label: "Важность", mark: "importance", show: "importance",
    hint: "Какие реплики важны тому, кто не был на встрече. В карточке — полоса у самых важных (около 15 %)",
  },
  {
    label: "Главы", mark: "chapters", show: "chapters",
    hint: "Разделы встречи по темам с названиями. В карточке — заголовки в ленте с кнопкой «Обсудить главу с агентом»",
  },
  {
    label: "Наблюдения", mark: "insights", show: "insights",
    hint: "Противоречия, на что обратить внимание, что сделать после встречи. В карточке — блок над лентой реплик",
  },
  {
    label: "Ссылки на задачи", mark: "issues", show: "jira", independent: true,
    hint: "Разметка — задачи Jira, названные неполно или неразборчиво («тот баг про экспорт, сорок четыре "
      + "пятьдесят два»). Показ — ссылки на задачи Jira, как в разделе «Jira»: ключи вида ABC-123 в тексте "
      + "работают и без разметки, если в разделе «Jira» заданы проекты",
  },
];

/** Подсказка у недоступного «Показывать». */
export const MARK_FIRST = "Сначала включите разметку";

export function AnalysisTip() {
  return (
    <HelpTip label="Что такое анализ встречи" title="Анализ встречи">
      <TipLine>
        После расшифровки агент размечает встречу: находит вопросы, решения и задачи, оценивает важность реплик,
        делит встречу на главы и отмечает то, на что стоит обратить внимание.
      </TipLine>
      <TipLine>
        Анализ делает модель по умолчанию — та же, что составляет итоги (раздел «Модели ИИ»). Встречи короче минимальной
        длительности звонка (раздел «Автозапись») и записи, которые ещё идут, автоматически не анализируются.
      </TipLine>
      <TipLine>Повторить анализ можно в карточке записи: «Ещё действия» → «Переанализировать».</TipLine>
    </HelpTip>
  );
}

export function AutoTitleTip() {
  return (
    <HelpTip label="Как придумывается название встречи" title="Название встречи от ИИ">
      <TipLine>Название берётся из анализа встречи, из итогов или из начала разговора.</TipLine>
      <TipLine>
        Названия, которые вы задали сами, не меняются. Название от ИИ отмечено в списке значком «ИИ» —
        нажмите на него, чтобы переименовать запись.
      </TipLine>
    </HelpTip>
  );
}

export function ImproveTip() {
  return (
    <HelpTip label="Что такое улучшение расшифровки" title="Улучшить расшифровку">
      <TipLine>
        ИИ ищет неверно распознанные термины («апи» → «API», «кафка» → «Kafka») и показывает короткий список
        замен. Фразы он не переписывает.
      </TipLine>
      <TipLine>
        Запустить вручную — ✦ «Улучшить» в строке поиска расшифровки или «Ещё действия» → «Улучшить расшифровку».
        Применённые замены отменяются в истории изменений.
      </TipLine>
    </HelpTip>
  );
}

export function MarkupTip() {
  return (
    <HelpTip label="Что такое подсветка и разметка" title="Подсветка и разметка">
      <TipLine>
        Разметку делает анализ встречи: типы реплик, важность, главы, наблюдения и ссылки на задачи. «Показывать в
        карточке» выбирает, что из размеченного видно в карточке записи.
      </TipLine>
      <TipLine>
        Выключенное в «Размечать» у модели не запрашивается — показывать нечего. Исключение — ссылки на задачи:
        ключи вида ABC-123 в тексте становятся ссылками и без разметки.
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

/** Переключатель в ячейке таблицы: имя — «<Элемент>: размечать» / «<Элемент>: показывать». */
function CellSwitch({ label, value, disabled, describedBy, onChange }: {
  label: string; value: boolean; disabled?: boolean; describedBy?: string; onChange: (v: boolean) => void;
}) {
  return (
    <button type="button" role="switch" aria-checked={value} aria-label={label} className="switch"
      disabled={disabled} aria-describedby={describedBy} onClick={() => onChange(!value)} />
  );
}

/** Таблица «Размечать / Показывать в карточке». */
function MarkShowTable({ draft, set }: { draft: Raw; set: SetFn }) {
  const id = useId();
  const marked = (k: string) => draft.analysis?.[k] !== false;
  const shown = (k: string) => draft.transcript_view?.[k] !== false;
  return (
    <table className="amark" aria-label="Что размечать и что показывать">
      <thead>
        <tr>
          <th scope="col" className="amark__col">Элемент</th>
          <th scope="col" className="amark__col amark__col--ctl">Размечать</th>
          <th scope="col" className="amark__col amark__col--ctl">Показывать в карточке</th>
        </tr>
      </thead>
      <tbody>
        {MARK_SHOW.map((item) => {
          const mark = marked(item.mark);
          const locked = !mark && !item.independent;
          const note = `${id}-${item.mark}`;
          return (
            <tr key={item.mark} className="amark__row">
              <th scope="row" className="amark__item">
                <span className="srow__label">{item.label}</span>
                <span className="srow__hint">{item.hint}</span>
              </th>
              <td className="amark__cell">
                <CellSwitch label={`${item.label}: размечать`} value={mark}
                  onChange={(x) => set("analysis", item.mark, x)} />
              </td>
              <td className="amark__cell">
                <CellSwitch label={`${item.label}: показывать`} value={shown(item.show)} disabled={locked}
                  describedBy={locked ? note : undefined} onChange={(x) => set("transcript_view", item.show, x)} />
                {locked && <span id={note} className="amark__note">{MARK_FIRST}</span>}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** «Плеер»: кривая важности и подписи глав на полосе. */
export function PlayerRows({ draft, set }: { draft: Raw; set: SetFn }) {
  const v = (k: string) => draft.transcript_view?.[k];
  const curve = (v("curve") as "always" | "hover" | "off" | undefined) ?? "hover";
  return (
    <>
      <Radio label="Кривая важности над плеером" help={<CurveTip />} value={curve}
        options={[
          { value: "always", label: "Всегда" },
          { value: "hover", label: "При наведении" },
          { value: "off", label: "Не показывать" },
        ]}
        onChange={(x) => set("transcript_view", "curve", x)} />
      <Switch label="Подписи глав на полосе плеера" help={<PlayerKeysTip />}
        hint="Номер и короткое название главы под полосой; в узком плеере — только номера"
        value={v("bar_labels") !== false} onChange={(x) => set("transcript_view", "bar_labels", x)} />
    </>
  );
}

export function AnalysisSection({ draft, set, onOpenJira }: {
  draft: Raw; set: SetFn;
  /** Перейти в «Jira» (адрес и проекты); без него ссылки нет. */
  onOpenJira?: () => void;
}) {
  const v = (k: string) => draft.analysis?.[k];
  const on = (k: string) => v(k) !== false;
  return (
    <>
      <SettingsCard title="Анализ после расшифровки">
        <p className="muted sdesc">
          Агент размечает расшифровку: важное, главы, наблюдения. Анализ делает тот же агент, что составляет итоги.
        </p>
        <Switch label="Анализировать встречу после расшифровки" help={<AnalysisTip />}
          hint={v("consent") === "pending"
            ? "После обновления выключено, пока вы не решите. Текст встречи отправляется выбранной модели"
            : "Анализ ставится сам, когда расшифровка готова. Текст встречи отправляется выбранной модели. "
              + "Повторить анализ можно в карточке записи"}
          value={on("auto")} onChange={(x) => set("analysis", "auto", x)} />
      </SettingsCard>
      <SettingsCard title="Разметка">
        <p className="muted sdesc amark__intro">
          Выключенное в «Размечать» не запрашивается у модели и не показывается в карточке. <MarkupTip />
        </p>
        <MarkShowTable draft={draft} set={set} />
        {onOpenJira && (
          <SeeAlso>
            Адрес Jira и проекты — в разделе <Button variant="link" onClick={onOpenJira}>«Jira»</Button>.
          </SeeAlso>
        )}
      </SettingsCard>
      <SettingsCard title="Плеер">
        <PlayerRows draft={draft} set={set} />
      </SettingsCard>
      <SettingsCard title="Улучшение расшифровки">
        <Switch label="Улучшать расшифровку автоматически после распознавания" help={<ImproveTip />}
          hint="ИИ сам готовит список исправлений терминов; текст меняется, только когда вы примените выбранное"
          value={draft.analysis?.improve_auto === true} onChange={(x) => set("analysis", "improve_auto", x)} />
      </SettingsCard>
      <SettingsCard title="Название и категория">
        <Switch label="Название встречи"
          hint={"Модель предлагает название по содержанию встречи. Ставится само, только если включено "
            + "«Придумывать название встречи»; иначе — по кнопке «Предложить название» в карточке"}
          value={on("title")} onChange={(x) => set("analysis", "title", x)} />
        <Switch label="Придумывать название встречи" help={<AutoTitleTip />}
          hint="Название из анализа или итогов ставится само; заданные вами названия не меняются"
          value={Boolean(draft.assistant?.auto_title)} onChange={(x) => set("assistant", "auto_title", x)} />
        <Switch label="Определять категорию автоматически"
          hint="ИИ выбирает категорию из списка в разделе «Категории». Выбранную вами категорию он не меняет"
          value={on("category")} onChange={(x) => set("analysis", "category", x)} />
      </SettingsCard>
    </>
  );
}
