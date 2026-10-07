/**
 * «Запуск агента (вкладка «Агент»)» в разделе «Ассистент»: свои параметры
 * запуска Claude Code, Codex и OpenCode — дополнительные аргументы и переменные
 * окружения (`agent.launch.<агент>`). Только для вкладки «Агент»: фоновые
 * задачи их не получают.
 *
 * Поле переменных держит свой текст как набран (перевод строки, пробелы):
 * черновику уходит разобранный список, а пока в тексте ошибка — сам текст
 * (строка), и «Сохранить» недоступно (`agentLaunchChangesInvalid`). Обратно в
 * поле черновик попадает, только когда его поменяли извне («Сбросить»,
 * перечитанные настройки).
 */

import { useEffect, useState } from "react";
import {
  AGENTS, type AgentId, type LaunchDraft, envText, launchError, parseEnv, previewCommand,
} from "../../lib/agentLaunch";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Row, type Raw, type SetFn } from "./Section";
import { MODEL_LABEL, OPENCODE_MODEL_LABEL } from "./AssistantSection";

const EMPTY: LaunchDraft = { args: "", env: [] };

type Launches = Partial<Record<AgentId, LaunchDraft>>;

/** Правки `agent.launch` нельзя сохранить: параметры или переменные с ошибкой. */
export function agentLaunchChangesInvalid(changes: Raw): boolean {
  const launch = changes.agent?.launch as Launches | undefined;
  if (!launch || typeof launch !== "object") return false;
  return AGENTS.some(({ id }) => {
    const e = launchError(launch[id]);
    return e.args !== null || e.env.length > 0;
  });
}

const QUOTES_LINE = (
  <TipLine>
    Текст с пробелами — в кавычках. Обратная косая черта — обычный символ (пути Windows); путь с апострофом,
    например <code>"D:\Docs\O'Neil"</code>, берите в двойные кавычки — одиночная тоже объединяет текст.
  </TipLine>
);

/**
 * Примеры для полей: параметры и переменные окружения. У Claude Code — не
 * `--model`: серый пример в пустом поле читался как заданная модель, а модель
 * вкладка и так берёт из «Модели Claude Code» (v037 model-pick).
 */
const PLACEHOLDERS: Record<AgentId, { args: string; env: string }> = {
  "claude-code": { args: "--permission-mode acceptEdits", env: "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1" },
  codex: { args: "-m gpt-5", env: "CODEX_HOME=D:\\codex" },
  opencode: { args: "-m anthropic/claude-sonnet-4-5", env: "OPENCODE_CONFIG=D:\\opencode.json" },
};

function OpencodeLaunchTip() {
  return (
    <HelpTip label="Какие параметры можно задать для OpenCode" title="Параметры запуска OpenCode">
      <TipLine>
        Аргументы командной строки opencode, через пробел. Например: <code>-m anthropic/claude-sonnet-4-5</code>,{" "}
        <code>--agent plan</code>.
      </TipLine>
      {QUOTES_LINE}
      <TipLine>
        Приложение само запускает OpenCode в папке встречи, передаёт <code>--continue</code> для «Продолжить
        прошлую», а подсказку о встрече и базу знаний — через переменную <code>OPENCODE_CONFIG_CONTENT</code>:
        база знаний читается без вопроса; что агенту можно менять, решают настройки самого OpenCode. Ваши
        параметры идут после наших.
      </TipLine>
      <TipLine>
        Свои <code>--continue</code> или <code>--session</code> выбирают сеанс вместо приложения. Своя переменная{" "}
        <code>OPENCODE_CONFIG_CONTENT</code> заменит нашу — подсказки о встрече тогда не будет.
      </TipLine>
      <TipLine>Переменные окружения — по одной в строке: ИМЯ=значение. Они применяются последними.</TipLine>
      <TipLine>Действует только во вкладке «Агент». Для фоновых задач используется «{OPENCODE_MODEL_LABEL}».</TipLine>
    </HelpTip>
  );
}

function LaunchTip({ agent }: { agent: AgentId }) {
  if (agent === "opencode") return <OpencodeLaunchTip />;
  return agent === "claude-code" ? (
    <HelpTip label="Какие параметры можно задать для Claude Code" title="Параметры запуска Claude Code">
      <TipLine>
        Аргументы командной строки claude, через пробел. Например: <code>--model opus</code>,{" "}
        <code>--permission-mode acceptEdits</code>, <code>--add-dir D:\Docs</code>.
      </TipLine>
      {QUOTES_LINE}
      <TipLine>
        Приложение само передаёт папку встречи как рабочую, <code>--session-id</code> нового сеанса (или{" "}
        <code>--resume</code> для «Продолжить прошлую»), <code>--add-dir</code> с базой знаний,{" "}
        <code>--append-system-prompt</code> с подсказкой о встрече и <code>--model</code> из настройки «{MODEL_LABEL}».
        Ваши параметры идут после них.
      </TipLine>
      <TipLine>
        Параметр с одним значением действует ваш: свой <code>--append-system-prompt</code> заменит подсказку о
        встрече, свой <code>--model</code> — модель из настроек. <code>--add-dir</code> добавляет папки к нашей. Свои <code>--continue</code>, <code>--resume</code>{" "}
        или <code>--session-id</code> выбирают сеанс вместо приложения.
      </TipLine>
      <TipLine>Переменные окружения — по одной в строке: ИМЯ=значение. Они применяются последними.</TipLine>
      <TipLine>Действует только во вкладке «Агент». Фоновые задачи и живой ассистент всегда идут на модели из настройки «{MODEL_LABEL}».</TipLine>
    </HelpTip>
  ) : (
    <HelpTip label="Какие параметры можно задать для Codex" title="Параметры запуска Codex">
      <TipLine>
        Аргументы командной строки codex, через пробел. Например: <code>-m gpt-5 -c model_reasoning_effort=high</code>.
      </TipLine>
      {QUOTES_LINE}
      <TipLine>
        Приложение само передаёт <code>--cd</code> с папкой встречи, <code>-c developer_instructions=…</code> с
        подсказкой о встрече и <code>resume --last</code> для «Продолжить прошлую». Ваши параметры идут после них.
      </TipLine>
      <TipLine>
        Параметр с одним значением действует ваш: свой <code>--cd</code> заменит папку встречи, свой{" "}
        <code>developer_instructions</code> — подсказку о встрече. <code>--last</code> Codex принимает только
        вместе с «Продолжить прошлую».
      </TipLine>
      <TipLine>Переменные окружения — по одной в строке: ИМЯ=значение. Они применяются последними.</TipLine>
      <TipLine>Действует только во вкладке «Агент». Фоновые задачи Codex идут с его собственными настройками.</TipLine>
    </HelpTip>
  );
}

/** Поле переменных: свой текст, черновику — разобранный список или текст с ошибкой. */
function EnvField({ id, agent, env, onChange }: {
  id: string; agent: AgentId; env: LaunchDraft["env"]; onChange: (env: LaunchDraft["env"]) => void;
}) {
  const [text, setText] = useState(() => envText(env));
  // Черновик сменился не из поля («Сбросить», перечитанные настройки) — показать его.
  const fromDraft = typeof env === "string" ? null : JSON.stringify(env);
  useEffect(() => {
    if (fromDraft === null) return;
    setText((cur) => {
      const parsed = parseEnv(cur);
      return parsed.error === null && JSON.stringify(parsed.env) === fromDraft ? cur : envText(JSON.parse(fromDraft));
    });
  }, [fromDraft]);
  const errors = typeof env === "string" ? parseEnv(env).errors : [];
  return (
    <>
      <textarea id={id} rows={3} spellCheck={false} value={text}
        placeholder={PLACEHOLDERS[agent].env}
        onChange={(e) => {
          const value = e.target.value;
          setText(value);
          const parsed = parseEnv(value);
          onChange(parsed.error === null ? parsed.env : value);
        }} />
      {errors.map((error) => <span key={error} className="error">{error}</span>)}
    </>
  );
}

function AgentLaunchRows({ agent, label, launch, knowledge, model, onChange }: {
  agent: AgentId; label: string; launch: LaunchDraft; knowledge: string | null; model: string | null;
  onChange: (next: LaunchDraft) => void;
}) {
  const errors = launchError(launch);
  const preview = previewCommand(agent, launch, knowledge, model);
  const dirty = launch.args !== "" || envText(launch.env) !== "";
  const argsId = `agent-args-${agent}`;
  const envId = `agent-env-${agent}`;
  return (
    <div role="group" aria-label={`Запуск ${label}`} className="agent-launch">
      <h4 className="agent-launch__title">
        {label}
        <LaunchTip agent={agent} />
        <Button size="sm" onClick={() => onChange(EMPTY)} disabled={!dirty} aria-label={`Параметры ${label} по умолчанию`}
          title="Вернуть параметры по умолчанию (вступит в силу после «Сохранить»)">
          По умолчанию
        </Button>
      </h4>
      <Row label="Дополнительные параметры" htmlFor={argsId} hint="Аргументы командной строки, через пробел" stack>
        <input id={argsId} type="text" spellCheck={false} value={launch.args}
          placeholder={PLACEHOLDERS[agent].args}
          onChange={(e) => onChange({ ...launch, args: e.target.value })} />
        {errors.args && <span className="error">{errors.args}</span>}
      </Row>
      <Row label="Переменные окружения" htmlFor={envId} hint="По одной в строке: ИМЯ=значение" stack>
        <EnvField id={envId} agent={agent} env={launch.env} onChange={(env) => onChange({ ...launch, env })} />
      </Row>
      {preview !== null && (
        <p className="muted agent-launch__preview">
          Команда запуска: <code>{preview}</code>
        </p>
      )}
    </div>
  );
}

export function AgentLaunchSection({ draft, set }: { draft: Raw; set: SetFn }) {
  const launch = (draft.agent?.launch ?? {}) as Launches;
  const knowledge = (draft.assistant?.knowledge_dir as string | null | undefined) ?? null;
  const model = (draft.llm?.model as string | null | undefined) ?? null;
  const update = (agent: AgentId, next: LaunchDraft) => {
    const all: Launches = {};
    for (const { id } of AGENTS) all[id] = launch[id] ?? EMPTY;
    set("agent", "launch", { ...all, [agent]: next });
  };
  return (
    <>
      <h3 className="shead">Запуск агента (вкладка «Агент»)</h3>
      <p className="muted sdesc">
        Свои параметры для Claude Code, Codex и OpenCode во вкладке «Агент». Фоновые задачи (итоги, анализ, живой ассистент)
        их не получают.
      </p>
      {AGENTS.map(({ id, label }) => (
        <AgentLaunchRows key={id} agent={id} label={label} launch={launch[id] ?? EMPTY} knowledge={knowledge}
          model={model} onChange={(next) => update(id, next)} />
      ))}
    </>
  );
}
