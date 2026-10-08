/**
 * «Ссылки на Jira» — раздел «Jira» (0.4; в 0.3.1 — «Подсветка расшифровки»): адрес Jira, проекты —
 * чипы ключей со своими вариантами произношения, проект по умолчанию и, в
 * «Дополнительно», шаблон ключа для текста.
 *
 * Что узнаётся в речи, решает резидент (meet.jira_refs): ключ проекта
 * латиницей, его русская запись и чтение по буквам — сами, варианты здесь —
 * сверх них. Номер — цифрами или словами. Регулярное выражение для обычной
 * работы не нужно: шаблон в «Дополнительно» — только для ключей, написанных
 * текстом, если ключи проектов не подходят.
 */

import { useState, type KeyboardEvent } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import {
  aliasError, DEFAULT_JIRA_KEYS, jiraBaseError, jiraKeysError, jiraProjects, literalPattern, projectKeyError,
  projectsError, ALIASES_MAX, PROJECTS_MAX, type JiraProject,
} from "../../lib/jira";
import { plural } from "../../lib/format";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Icon } from "../../ui/Icon";
import { Row, Switch, type Raw, type SetFn } from "./Section";
import "./jira-settings.css";

/** Проект по умолчанию — из списка (или не выбран). */
function defaultError(raw: unknown, projects: JiraProject[]): string | null {
  if (typeof raw !== "string" || !raw) return null;
  return projects.some((p) => p.key === raw) ? null : `Проекта ${raw} нет в списке`;
}

/** Правки, которые нельзя сохранить: негодный адрес Jira, проекты или шаблон ключа. */
export function jiraChangesInvalid(changes: Raw): boolean {
  const i = changes.integrations ?? {};
  return (typeof i.jira_base_url === "string" && jiraBaseError(i.jira_base_url) !== null)
    || (typeof i.jira_pattern === "string" && jiraKeysError(i.jira_pattern) !== null)
    || (Array.isArray(i.jira_projects) && projectsError(i.jira_projects as JiraProject[]) !== null);
}

/**
 * Ссылки на Jira выключены: поля недоступны для правки, и негодное значение в
 * них не должно запирать «Сохранить» — оно просто не уходит резиденту
 * (останется прежнее). Правит `changes` на месте.
 */
export function dropHiddenJiraChanges(changes: Raw, draft: Raw): void {
  const i = changes.integrations;
  if (!i || draft.transcript_view?.jira !== false) return;
  if (typeof i.jira_base_url === "string" && jiraBaseError(i.jira_base_url) !== null) delete i.jira_base_url;
  if (typeof i.jira_pattern === "string" && jiraKeysError(i.jira_pattern) !== null) delete i.jira_pattern;
  if (Array.isArray(i.jira_projects) && projectsError(i.jira_projects as JiraProject[]) !== null) {
    delete i.jira_projects;
  }
  if (Object.keys(i).length === 0) delete changes.integrations;
}

export function JiraTip() {
  return (
    <HelpTip label="Как работают ссылки на Jira" title="Ссылки на задачи Jira">
      <TipLine>
        Задачи, названные во встрече, становятся ссылками в расшифровке, итогах и наблюдениях. Узнаётся то, как
        говорят, а не только как пишут:
      </TipLine>
      <TipLine>«ORION 2122» → <code>ORION-2122</code></TipLine>
      <TipLine>«орион двадцать один двадцать два» → <code>ORION-2122</code></TipLine>
      <TipLine>«в баге 4452» → <code>ORION-4452</code>, если ORION — проект по умолчанию</TipLine>
      <TipLine>
        Ключ проекта, его русская запись и чтение по буквам («эс пи ар» для SPR) узнаются сами. Если проект называют
        иначе, добавьте вариант у его ключа. Номер — цифрами или словами, до шести цифр; «в 2122 году» и
        «2 122 рубля» ссылками не становятся.
      </TipLine>
      <TipLine>
        Неочевидные упоминания («тот баг про экспорт, сорок четыре пятьдесят два») находит анализ встречи, если в
        разделе «Анализ встречи» включено «Ссылки на задачи».
      </TipLine>
      <TipLine>
        Адрес — начало ссылки на вашу Jira, например <code>https://jira.example.com</code>; ссылка на задачу —
        адрес + <code>/browse/ORION-2122</code>. Приложение открывает только этот адрес и только по https.
      </TipLine>
    </HelpTip>
  );
}

/** Варианты названия одного проекта: чипы и поле «добавить». */
function Aliases({ project, disabled, onChange }: {
  project: JiraProject; disabled: boolean; onChange: (aliases: string[]) => void;
}) {
  const [text, setText] = useState("");
  const typed = text.trim().split(/\s+/).join(" ");
  const taken = project.aliases.some((a) => a.toLowerCase() === typed.toLowerCase());
  const error = aliasError(typed) ?? (taken ? "Такой вариант уже есть" : null)
    ?? (typed && project.aliases.length >= ALIASES_MAX ? `Не больше ${ALIASES_MAX} вариантов` : null);
  const add = () => {
    if (!typed || error) return;
    onChange([...project.aliases, typed]);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); add(); }
  };
  return (
    <div className="jproj__aliases" id={`jira-aliases-${project.key}`}>
      <span className="srow__hint">
        Как ещё называют {project.key} — сверх ключа, его русской записи и чтения по буквам:
      </span>
      {project.aliases.length > 0 && (
        <ul className="jproj__chips" aria-label={`Варианты названия ${project.key}`}>
          {project.aliases.map((a) => (
            <li key={a} className="jchip jchip--alias">
              <span>{a}</span>
              <button type="button" className="jchip__remove" aria-label={`Убрать вариант «${a}»`} disabled={disabled}
                onClick={() => onChange(project.aliases.filter((x) => x !== a))}><Icon as={X} size="sm" /></button>
            </li>
          ))}
        </ul>
      )}
      <span className="jproj__add">
        <input type="text" aria-label={`Новый вариант названия ${project.key}`} placeholder="как ещё говорят на встречах"
          value={text} disabled={disabled} spellCheck={false} aria-invalid={error ? true : undefined}
          onChange={(e) => setText(e.target.value)} onKeyDown={onKey} />
        <Button onClick={add} disabled={disabled || !typed || !!error}>Добавить вариант</Button>
      </span>
      {error && <span className="error jproj__error">{error}</span>}
    </div>
  );
}

export function JiraSettings({ draft, set }: { draft: Raw; set: SetFn }) {
  const on = draft.transcript_view?.jira !== false;
  const integrations = draft.integrations ?? {};
  const base = String(integrations.jira_base_url ?? "");
  const pattern = String(integrations.jira_pattern ?? "");
  const projects = jiraProjects(integrations);
  const fallback = String(integrations.jira_default_project ?? "");
  const baseError = jiraBaseError(base);
  const patternError = jiraKeysError(pattern);
  const [newKey, setNewKey] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const keys = projects.map((p) => p.key);
  const typed = newKey.trim().toUpperCase();
  const keyError = projectKeyError(typed, keys)
    ?? (typed && projects.length >= PROJECTS_MAX ? `Не больше ${PROJECTS_MAX} проектов` : null);
  const setProjects = (next: JiraProject[]) => set("integrations", "jira_projects", next);
  const addProject = () => {
    if (!typed || keyError) return;
    setProjects([...projects, { key: typed, aliases: [] }]);
    setNewKey("");
  };
  const removeProject = (key: string) => {
    setProjects(projects.filter((p) => p.key !== key));
    if (fallback === key) set("integrations", "jira_default_project", "");
    if (open === key) setOpen(null);
  };
  const opened = projects.find((p) => p.key === open) ?? null;
  // Шаблон, который действует при пустом поле, — подсказка в нём.
  const effective = literalPattern({ jira_projects: projects });

  return (
    <>
      <Switch label="Ссылки на задачи Jira" help={<JiraTip />}
        hint="Задачи, названные во встрече, открываются в Jira — в расшифровке, итогах и наблюдениях"
        value={on} onChange={(x) => set("transcript_view", "jira", x)} />
      <Row label="Адрес Jira" htmlFor="jira-base" stack disabled={!on} hint={baseError
        ? <span className="error">{baseError}</span> : on ? "Пусто — ссылок нет. Например, https://jira.example.com"
          : "Включите «Ссылки на задачи Jira», чтобы задать адрес"}>
        <input id="jira-base" type="text" inputMode="url" placeholder="https://jira.example.com" value={base}
          spellCheck={false} disabled={!on} aria-invalid={baseError ? true : undefined}
          onChange={(e) => set("integrations", "jira_base_url", e.target.value)} />
      </Row>
      <div className={`srow srow--stack jproj${on ? "" : " srow--disabled"}`} role="group" aria-label="Проекты Jira">
        <div className="srow__text">
          <span className="srow__head"><span className="srow__label">Проекты</span></span>
          <span className="srow__hint">
            Ключи проектов, задачи которых называют на встречах. Нажмите на ключ, чтобы добавить, как ещё его
            произносят
          </span>
        </div>
        <div className="srow__control jproj__control">
          {projects.length > 0 ? (
            <ul className="jproj__chips" aria-label="Ключи проектов">
              {projects.map((p) => (
                <li key={p.key} className={`jchip${open === p.key ? " jchip--open" : ""}`}>
                  <button type="button" className="jchip__key" aria-expanded={open === p.key}
                    aria-controls={open === p.key ? `jira-aliases-${p.key}` : undefined}
                    title={`Варианты названия ${p.key}`} onClick={() => setOpen(open === p.key ? null : p.key)}>
                    <Icon as={open === p.key ? ChevronDown : ChevronRight} size="sm" />
                    <span className="jchip__name">{p.key}</span>
                    {p.aliases.length > 0 && (
                      <span className="jchip__n num">
                        +{p.aliases.length} {plural(p.aliases.length, "вариант", "варианта", "вариантов")}
                      </span>
                    )}
                  </button>
                  <button type="button" className="jchip__remove" aria-label={`Убрать проект ${p.key}`}
                    disabled={!on} onClick={() => removeProject(p.key)}><Icon as={X} size="sm" /></button>
                </li>
              ))}
            </ul>
          ) : (
            <span className="muted jproj__empty">
              Проектов нет — ссылками станут только ключи, написанные текстом (SPR-131)
            </span>
          )}
          {opened && (
            <Aliases project={opened} disabled={!on}
              onChange={(aliases) => setProjects(projects.map((p) => (p.key === opened.key ? { ...p, aliases } : p)))} />
          )}
          <span className="jproj__add">
            <input type="text" aria-label="Ключ проекта" placeholder="ORION" value={newKey} disabled={!on}
              spellCheck={false} autoCapitalize="characters" aria-invalid={keyError ? true : undefined}
              onChange={(e) => setNewKey(e.target.value.toUpperCase())}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addProject(); } }} />
            <Button onClick={addProject} disabled={!on || !typed || !!keyError}>Добавить проект</Button>
          </span>
          {keyError && <span className="error jproj__error">{keyError}</span>}
        </div>
      </div>
      <Row label="Проект по умолчанию" htmlFor="jira-default" disabled={!on || !projects.length}
        hint={defaultError(fallback, projects) ?? (projects.length
          ? "Для номеров без проекта после слов «баг», «тикет», «задача»: «в баге 4452»"
          : "Сначала добавьте проекты")}>
        <select id="jira-default" value={projects.some((p) => p.key === fallback) ? fallback : ""}
          disabled={!on || !projects.length}
          onChange={(e) => set("integrations", "jira_default_project", e.target.value)}>
          <option value="">Не выбран</option>
          {projects.map((p) => <option key={p.key} value={p.key}>{p.key}</option>)}
        </select>
      </Row>
      <details className="jadv" open={pattern ? true : undefined}>
        <summary className="jadv__summary">Дополнительно: шаблон ключа для текста</summary>
        <Row label="Шаблон ключа для текста" htmlFor="jira-pattern" stack disabled={!on} hint={patternError
          ? <span className="error">{patternError}</span>
          : <>Ключи, написанные текстом, например <code>SPR-131</code>: регулярное выражение или проекты через
            запятую. Пусто — {projects.length ? "ключи проектов выше" : <>любой ключ (<code>{DEFAULT_JIRA_KEYS}</code>)</>}</>}>
          <input id="jira-pattern" type="text" className="input--wide" placeholder={effective} value={pattern}
            spellCheck={false} disabled={!on} aria-invalid={patternError ? true : undefined}
            onChange={(e) => set("integrations", "jira_pattern", e.target.value)} />
        </Row>
      </details>
    </>
  );
}
