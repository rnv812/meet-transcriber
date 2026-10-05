/**
 * Настройки живых подсказок (раздел «Ассистент» → «Живой ассистент»):
 * активность, модель для подсказок, сколько подсказок держать и «Не
 * отвлекать» по умолчанию. Всё — секция `assist`; ассистент читает её при
 * запуске, поэтому применяется со следующей записи с ассистентом.
 */

import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Radio, Row, Switch, type Raw, type SetFn } from "./Section";

type Activity = "calm" | "active" | "summary";
type Tier = "agent" | "fast";

const ACTIVITIES: { value: Activity; label: string }[] = [
  { value: "calm", label: "Сдержанно" },
  { value: "active", label: "Активно" },
  { value: "summary", label: "Только сводка" },
];

const TIERS: { value: Tier; label: string }[] = [
  { value: "agent", label: "Как у агента" },
  { value: "fast", label: "Быстрее" },
];

/** Значения «Сколько подсказок»: 0 — по активности. */
export const MAX_HINTS_CHOICES = [0, 3, 5, 8, 10];

const NEXT_RUN = "Применяется со следующего запуска ассистента";

/** Что значит «Быстрее» у выбранного провайдера. */
export function fastMeaning(provider: string | null | undefined): string {
  if (provider === "claude-code") return "Claude Code: модель Haiku без размышлений";
  if (provider === "codex") return "Codex: низкое усилие рассуждения";
  if (provider === "opencode") return "OpenCode: без изменений, модель из настройки «Модель OpenCode»";
  if (provider === "openai-compatible") return "Локальная модель: без изменений, модель одна";
  return "Claude Code — модель Haiku без размышлений, Codex — низкое усилие рассуждения";
}

export function LiveActivityTip() {
  return (
    <HelpTip label="Что такое активность подсказок" title="Активность подсказок">
      <TipLine>
        «Сдержанно» — подсказки обновляются примерно каждые 20 секунд речи и сразу, когда на встрече задали
        вопрос или обратились к вам; сводка — примерно раз в минуту. Подсказок на экране не больше пяти.
      </TipLine>
      <TipLine>«Активно» — подсказки примерно каждые 10 секунд речи, до восьми на экране.</TipLine>
      <TipLine>«Только сводка» — ассистент ведёт сводку встречи, подсказок не предлагает.</TipLine>
      <TipLine>
        Ассистент помнит весь разговор, а не только последнюю минуту. Если сказать нечего, подсказки не
        меняются. В тишине ассистент модель не вызывает.
      </TipLine>
    </HelpTip>
  );
}

export function HintsModelTip() {
  return (
    <HelpTip label="Какая модель ведёт подсказки" title="Модель для живых подсказок">
      <TipLine>
        Подсказки и сводку во время встречи ведёт тот же провайдер, что выбран выше. «Как у агента» — та же
        модель, что отвечает на вопросы и готовит итоги.
      </TipLine>
      <TipLine>
        «Быстрее» — обновления приходят быстрее и расходуют меньше лимита подписки, но подсказки бывают
        проще. У Claude Code это модель Haiku без размышлений, у Codex — низкое усилие рассуждения;
        OpenCode и локальная модель не меняются.
      </TipLine>
      <TipLine>Ответы на вопросы во время встречи всегда даёт модель агента.</TipLine>
    </HelpTip>
  );
}

export function QuietTip() {
  return (
    <HelpTip label="Что значит «Не отвлекать»" title="Не отвлекать">
      <TipLine>
        Новое в панели не подсвечивается, на вкладках нет счётчиков, а строка свёрнутой панели не меняется,
        пока её подсказка актуальна. Сводка и подсказки при этом продолжают обновляться.
      </TipLine>
      <TipLine>Переключается и кнопкой с колокольчиком в шапке панели.</TipLine>
    </HelpTip>
  );
}

export function LiveHintsRows({ draft, set, provider }: {
  draft: Raw; set: SetFn;
  /** Кто отвечает сейчас (для пояснения «Быстрее»), null — неизвестно. */
  provider: string | null;
}) {
  const assist = draft.assist ?? {};
  const activity = (ACTIVITIES.some((a) => a.value === assist.activity) ? assist.activity : "calm") as Activity;
  const tier = (assist.hints_model === "fast" ? "fast" : "agent") as Tier;
  const maxHints = typeof assist.max_hints === "number" ? assist.max_hints : 0;
  const choices = MAX_HINTS_CHOICES.includes(maxHints) ? MAX_HINTS_CHOICES : [...MAX_HINTS_CHOICES, maxHints].sort((a, b) => a - b);
  return (
    <>
      <Radio label="Активность подсказок" help={<LiveActivityTip />} value={activity} options={ACTIVITIES}
        hint={`Как часто ассистент обновляет сводку и подсказки. ${NEXT_RUN}`}
        onChange={(v) => set("assist", "activity", v)} />
      <Radio label="Модель для живых подсказок" help={<HintsModelTip />} value={tier} options={TIERS}
        hint={tier === "fast" ? `«Быстрее» — ${fastMeaning(provider)}` : "Та же модель, что отвечает на вопросы и готовит итоги"}
        onChange={(v) => set("assist", "hints_model", v)} />
      <Row label="Сколько подсказок держать" htmlFor="assist-max-hints" disabled={activity === "summary"}
        hint={activity === "summary" ? "Подсказок нет: выбрано «Только сводка». Выберите «Сдержанно» или «Активно», чтобы задать"
          : "Сверх этого числа уходят наименее важные; закреплённые остаются"}>
        <select id="assist-max-hints" value={maxHints} disabled={activity === "summary"}
          onChange={(e) => set("assist", "max_hints", Number(e.target.value))}>
          {choices.map((n) => (
            <option key={n} value={n}>{n === 0 ? "По активности (5 или 8)" : String(n)}</option>
          ))}
        </select>
      </Row>
      <Switch label="Не отвлекать по умолчанию" help={<QuietTip />}
        hint="Панель открывается без подсветки и счётчиков"
        value={assist.quiet_default === true} onChange={(v) => set("assist", "quiet_default", v)} />
    </>
  );
}
