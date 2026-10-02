/**
 * «Запуск агента (вкладка «Агент»)» в разделе «Ассистент»: свои параметры
 * запуска Claude Code и Codex — дополнительные аргументы и переменные
 * окружения (`agent.launch.<агент>`). Только для вкладки «Агент»: фоновые
 * задачи их не получают.
 *
 * Черновик `env` — список; пока в поле ошибка, в черновике лежит сам текст
 * (строка): «Сохранить» недоступно (`agentLaunchChangesInvalid`), текст не
 * теряется.
 */

import {
  AGENTS, type AgentId, type LaunchDraft, envText, launchError, parseEnv, previewCommand,
} from "../../lib/agentLaunch";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Row, type Raw, type SetFn } from "./Section";
import { MODEL_LABEL } from "./AssistantSection";

const EMPTY: LaunchDraft = { args: "", env: [] };

type Launches = Partial<Record<AgentId, LaunchDraft>>;

/** Правки `agent.launch` нельзя сохранить: параметры или переменные с ошибкой. */
export function agentLaunchChangesInvalid(changes: Raw): boolean {
  const launch = changes.agent?.launch as Launches | undefined;
  if (!launch || typeof launch !== "object") return false;
  return AGENTS.some(({ id }) => {
    const e = launchError(launch[id]);
    return e.args !== null || e.env !== null;
  });
}

function LaunchTip({ agent }: { agent: AgentId }) {
  return agent === "claude-code" ? (
    <HelpTip label="Какие параметры можно задать для Claude Code" title="Параметры запуска Claude Code">
      <TipLine>
        Аргументы командной строки claude, через пробел; текст с пробелами — в кавычках. Например:{" "}
        <code>--model opus</code>, <code>--permission-mode acceptEdits</code>, <code>--add-dir D:\Docs</code>.
      </TipLine>
      <TipLine>
        Приложение само передаёт папку встречи как рабочую, <code>--append-system-prompt</code> с подсказкой о
        встрече, <code>--add-dir</code> с базой знаний и <code>--continue</code> для «Продолжить прошлую». Ваши
        параметры идут после них: если задать тот же параметр, действует ваш.
      </TipLine>
      <TipLine>Переменные окружения — по одной в строке: ИМЯ=значение. Они применяются последними.</TipLine>
      <TipLine>Действует только во вкладке «Агент». Для фоновых задач используется модель из настройки «{MODEL_LABEL}».</TipLine>
    </HelpTip>
  ) : (
    <HelpTip label="Какие параметры можно задать для Codex" title="Параметры запуска Codex">
      <TipLine>
        Аргументы командной строки codex, через пробел; текст с пробелами — в кавычках. Например:{" "}
        <code>-m gpt-5 -c model_reasoning_effort=high</code>.
      </TipLine>
      <TipLine>
        Приложение само передаёт <code>--cd</code> с папкой встречи, <code>-c developer_instructions=…</code> с
        подсказкой о встрече и <code>resume --last</code> для «Продолжить прошлую». Ваши параметры идут после них:
        если задать тот же параметр, действует ваш.
      </TipLine>
      <TipLine>Переменные окружения — по одной в строке: ИМЯ=значение. Они применяются последними.</TipLine>
      <TipLine>Действует только во вкладке «Агент». Фоновые задачи Codex идут с его собственными настройками.</TipLine>
    </HelpTip>
  );
}

function AgentLaunchRows({ agent, label, launch, knowledge, onChange }: {
  agent: AgentId; label: string; launch: LaunchDraft; knowledge: string | null;
  onChange: (next: LaunchDraft) => void;
}) {
  const errors = launchError(launch);
  const preview = previewCommand(agent, launch, knowledge);
  const dirty = launch.args !== "" || envText(launch.env) !== "";
  const argsId = `agent-args-${agent}`;
  const envId = `agent-env-${agent}`;
  return (
    <div role="group" aria-label={`Запуск ${label}`} className="agent-launch">
      <h4 className="agent-launch__title">
        {label}
        <LaunchTip agent={agent} />
        <Button onClick={() => onChange(EMPTY)} disabled={!dirty} aria-label={`Сбросить параметры ${label}`}>
          Сбросить
        </Button>
      </h4>
      <Row label="Дополнительные параметры" htmlFor={argsId} hint="Аргументы командной строки, через пробел" stack>
        <input id={argsId} type="text" spellCheck={false} value={launch.args}
          placeholder={agent === "claude-code" ? "--model opus" : "-m gpt-5"}
          onChange={(e) => onChange({ ...launch, args: e.target.value })} />
        {errors.args && <span className="error">{errors.args}</span>}
      </Row>
      <Row label="Переменные окружения" htmlFor={envId} hint="По одной в строке: ИМЯ=значение" stack>
        <textarea id={envId} rows={3} spellCheck={false} value={envText(launch.env)}
          placeholder={agent === "claude-code" ? "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1" : "CODEX_HOME=D:\\codex"}
          onChange={(e) => {
            const parsed = parseEnv(e.target.value);
            onChange({ ...launch, env: parsed.error === null ? parsed.env : e.target.value });
          }} />
        {errors.env && <span className="error">{errors.env}</span>}
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
  const update = (agent: AgentId, next: LaunchDraft) => {
    const all: Launches = {};
    for (const { id } of AGENTS) all[id] = launch[id] ?? EMPTY;
    set("agent", "launch", { ...all, [agent]: next });
  };
  return (
    <>
      <h3 className="shead">Запуск агента (вкладка «Агент»)</h3>
      <p className="muted sdesc">
        Свои параметры для Claude Code и Codex во вкладке «Агент». Фоновые задачи (итоги, анализ, живой ассистент,
        профили) их не получают.
      </p>
      {AGENTS.map(({ id, label }) => (
        <AgentLaunchRows key={id} agent={id} label={label} launch={launch[id] ?? EMPTY} knowledge={knowledge}
          onChange={(next) => update(id, next)} />
      ))}
    </>
  );
}
