/**
 * Карточка настроек одной модели в «Моделях и анализе» (0.6.2): под таблицей
 * моделей — настройки только выбранной (модель по умолчанию или та, что
 * выбрана в таблице), а не строки всех включённых вперемешку.
 *
 * - Claude Code — модель (sonnet, opus, haiku или своё имя) и уровень
 *   рассуждений (постоянный список);
 * - Codex и Antigravity CLI — модель и уровень из самого CLI (`GET
 *   /assistant/models`: Codex — `model/list` app-server, agy — ответ
 *   `session/new` по ACP); у каждой модели свои уровни, уровень вне списка
 *   выбранной модели в черновике сбрасывается на «По умолчанию». Список не
 *   получен — текстом, имя модели вписывается в поле; нет входа в agy —
 *   строка с командой входа;
 * - OpenCode — поле «провайдер/модель» и список `opencode models`;
 * - локальная модель — `LocalModelRows`.
 *
 * Список из CLI — подсказка: поле имени — всегда (своё имя, которого CLI не
 * назвал); «Обновить список» — мимо кэша резидента.
 */

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, type Endpoint, getCliModels } from "../../lib/api";
import { AGY_EFFORTS, CLAUDE_EFFORTS, CODEX_EFFORTS, EFFORT_DEFAULT, effortName, effortOptions } from "../../lib/effort";
import { errorText } from "../../lib/format";
import type { CliEffort, CliModel, CliModels } from "../../lib/types";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Select } from "../../ui/Select";
import { fieldClass } from "./fields";
import { LocalModelRows } from "./LocalModelRows";
import { Row, SettingsCard, type SetFn } from "./Section";

/** Подпись `llm.model`: на неё ссылаются подсказки других разделов. */
export const MODEL_LABEL = "Модель Claude Code";
/** Уровень рассуждений (0.5): у Claude Code — в строке модели, у Codex и agy — своей строкой. */
export const EFFORT_LABEL = "Уровень рассуждений Claude Code";
/** Подписи `llm.codex_model`, `llm.codex_effort` (0.6.2). */
export const CODEX_MODEL_LABEL = "Модель Codex";
export const CODEX_EFFORT_LABEL = "Уровень рассуждений Codex";
/** Подпись `llm.opencode_model`. */
export const OPENCODE_MODEL_LABEL = "Модель OpenCode";
/** Подписи `llm.agy_model`, `llm.agy_effort` (0.6.2). */
export const AGY_MODEL_LABEL = "Модель Antigravity CLI";
export const AGY_EFFORT_LABEL = "Уровень рассуждений Antigravity CLI";
/** Условия Google: на потребительских тарифах встраивание agy в чужие программы не поддерживается. */
export const AGY_PLAN_NOTICE = "На личных (потребительских) тарифах Google встраивание Antigravity CLI в другие программы "
  + "не поддерживается. Для Meet рекомендуем ключ API Gemini или корпоративный вход Gemini Enterprise.";
/** Модели локального сервера (`LocalModelRows`): подписи строк карточки. */
export const LOCAL_LABELS = ["Адрес сервера", "Имя модели"];

/** Подписи строк карточки каждой модели — поиск по настройкам открывает её карточку. */
export const CARD_LABELS: Record<string, string[]> = {
  "claude-code": [MODEL_LABEL, EFFORT_LABEL],
  codex: [CODEX_MODEL_LABEL, CODEX_EFFORT_LABEL],
  opencode: [OPENCODE_MODEL_LABEL],
  agy: [AGY_MODEL_LABEL, AGY_EFFORT_LABEL],
  "openai-compatible": LOCAL_LABELS,
};

/** Модель, чья карточка показывает строку с этой подписью; нет — null. */
export const cardOfLabel = (label: string): string | null =>
  Object.entries(CARD_LABELS).find(([, labels]) => labels.includes(label))?.[0] ?? null;

const OPENCODE_MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9._:@+-][A-Za-z0-9._:@+/-]*$/;
/** Имя модели Codex и agy: как у резидента (`settings.codex_model_error`, `agy_model_error`). */
const CLI_MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,127}$/;

/**
 * Значение `llm.opencode_model` нельзя сохранить: текст для человека, иначе
 * null. Та же проверка, что у резидента (`meet.settings.opencode_model_error`).
 */
export function opencodeModelError(value: string): string | null {
  const text = value.trim();
  if (!text) return null;
  if (!/^[A-Za-z0-9._:@+/-]*$/.test(text)) {
    return "В имени модели OpenCode недопустимые символы: только латиница, цифры и . _ - : @ + /";
  }
  if (!OPENCODE_MODEL_RE.test(text)) return "Модель OpenCode — в виде провайдер/модель, например anthropic/claude-sonnet-4-5";
  return null;
}

/**
 * Значение `llm.agy_model` нельзя сохранить: текст для человека, иначе null.
 * Та же проверка, что у резидента (`meet.settings.agy_model_error`).
 */
export function agyModelError(value: string): string | null {
  const text = value.trim();
  if (!text || CLI_MODEL_RE.test(text)) return null;
  return "В имени модели Antigravity CLI недопустимые символы: только латиница, цифры и . _ - : @ + /";
}

/** Значение `llm.codex_model` нельзя сохранить (как `meet.settings.codex_model_error`), иначе null. */
export function codexModelError(value: string): string | null {
  const text = value.trim();
  if (!text || CLI_MODEL_RE.test(text)) return null;
  return "В имени модели Codex недопустимые символы: только латиница, цифры и . _ - : @ + /";
}

function ModelTip() {
  return (
    <HelpTip label="Какая модель отвечает" title={MODEL_LABEL}>
      <TipLine>
        Модель, на которой Claude Code готовит итоги, анализ и названия, отвечает на вопросы, ведёт
        живого ассистента и работает во вкладке «Агент» (и в «Продолжить прошлую»): sonnet, opus, haiku или
        полное имя модели. Пусто — sonnet. «Проверить» у Claude Code проверяет и эту модель.
      </TipLine>
      <TipLine>
        Живые подсказки в режиме «Быстрее» идут на Haiku. Во вкладке «Агент» модель можно сменить своим{" "}
        <code>--model</code> в параметрах запуска.
      </TipLine>
    </HelpTip>
  );
}

function CodexModelTip() {
  return (
    <HelpTip label="Какая модель Codex отвечает" title={CODEX_MODEL_LABEL}>
      <TipLine>
        Модель, на которой Codex готовит итоги, анализ и названия, отвечает на вопросы и ведёт живого
        ассистента. Список и уровни рассуждений каждой модели — из самого Codex (<code>codex app-server</code>);
        пусто — модель из настроек Codex.
      </TipLine>
      <TipLine>
        У каждой модели свои уровни: при смене модели уровень, которого у неё нет, становится «По умолчанию».
        Во время встречи модель меняет и команда <code>/model</code> в панели.
      </TipLine>
    </HelpTip>
  );
}

function OpencodeModelTip() {
  return (
    <HelpTip label="Какая модель OpenCode отвечает" title={OPENCODE_MODEL_LABEL}>
      <TipLine>
        Модель, на которой OpenCode готовит итоги, анализ и названия, отвечает на вопросы и ведёт живого
        ассистента, — в виде провайдер/модель, как её показывает <code>opencode models</code>: например
        anthropic/claude-sonnet-4-5 или ollama/qwen3:8b. Пусто — модель из настроек самого OpenCode.
      </TipLine>
      <TipLine>
        «Проверить» не тратит запросы к модели: смотрит, что OpenCode установлен, что вход выполнен
        (<code>opencode auth login</code>) и что эта модель у него есть. Годен ли сам ключ, покажет первый ответ.
      </TipLine>
      <TipLine>
        Фоновые задачи OpenCode только читают: файлы не меняет, команды не выполняет, база знаний — для чтения.
      </TipLine>
    </HelpTip>
  );
}

function AgyModelTip() {
  return (
    <HelpTip label="Как Meet работает с Antigravity CLI" title={AGY_MODEL_LABEL}>
      <TipLine>
        Модель, на которой Antigravity CLI (<code>agy</code>) готовит итоги, анализ и названия, отвечает на вопросы
        и ведёт живого ассистента. Список моделей и уровней рассуждений Meet спрашивает у самого agy; пусто —
        модель из настроек agy.
      </TipLine>
      <TipLine>
        Meet запускает agy со своей папкой настроек: ваши правила и MCP-серверы agy он не меняет, а действия
        ассистента спрашивает карточкой, как у Claude Code. Вход для Meet — один раз в терминале командой
        <code> agy acp --login</code> с папкой, которую назовут «Проверить» и список моделей.
      </TipLine>
      <TipLine>{AGY_PLAN_NOTICE}</TipLine>
    </HelpTip>
  );
}

type ListState = { busy: boolean; data: CliModels | null; error: string | null };
const EMPTY: ListState = { busy: false, data: null, error: null };

/**
 * Модели и уровни провайдера из его CLI (`GET /assistant/models`): при показе
 * карточки и по «Обновить список» (мимо кэша резидента). Резидент прежней
 * версии маршрута не знает (404) — без списка, имя вписывается вручную.
 */
export function useCliModels(endpoint: Endpoint, provider: string | null) {
  const [state, setState] = useState<ListState>(EMPTY);
  const [asked, setAsked] = useState<string | null>(null);
  const ask = useRef(0);
  const load = useCallback(async (refresh: boolean) => {
    if (!provider) return;
    const n = ++ask.current;
    setState((cur) => ({ ...cur, busy: true }));
    try {
      const data = await getCliModels(endpoint, provider, refresh);
      if (ask.current === n) setState({ busy: false, data, error: null });
    } catch (e) {
      if (ask.current !== n) return;
      setState({ busy: false, data: null, error: e instanceof ApiError && e.status === 404 ? null : errorText(e) });
    }
  }, [endpoint, provider]);
  // Другая модель — прежний список не её: сброс до ответа (состояние по ходу рендера).
  if (asked !== provider) {
    setAsked(provider);
    setState(EMPTY);
  }
  useEffect(() => {
    void load(false);
    return () => { ask.current += 1; };
  }, [load]);
  return { ...state, refresh: () => void load(true) };
}

/** Пункт «По умолчанию» в списке моделей (пустое имя — модель из настроек CLI). */
const DEFAULT_MODEL = "__default__";

/** Модель, к которой относятся уровни: названная, а без имени — модель CLI по умолчанию. */
export function modelOf(models: CliModel[], name: string): CliModel | undefined {
  const text = name.trim();
  return text ? models.find((m) => m.id === text) : models.find((m) => m.default);
}

/**
 * Строка модели: список из CLI (с «По умолчанию») и поле имени (своё имя,
 * которого в списке нет, — всегда можно). Под ними — состояние списка:
 * ошибка, команда входа, сколько найдено.
 */
function ModelPickRow({ id, label, help, hint, value, onChange, placeholder, problem, list, fallbackHint, children }: {
  id: string; label: string; help: ReactNode; hint: string; value: string; onChange: (v: string) => void;
  placeholder: string; problem: string | null;
  list: ReturnType<typeof useCliModels> | null;
  /** Что значит «По умолчанию», когда CLI модель по умолчанию не назвал. */
  fallbackHint: string;
  children?: ReactNode;
}) {
  const models = list?.data?.models ?? [];
  const name = value.trim();
  const current = models.find((m) => m.id === name);
  const byDefault = models.find((m) => m.default);
  const options = [
    { value: DEFAULT_MODEL, label: "По умолчанию", detail: byDefault ? `сейчас: ${byDefault.label}` : fallbackHint },
    ...models.map((m) => ({ value: m.id, label: m.label || m.id, detail: m.label && m.label !== m.id ? m.id : undefined })),
  ];
  const listError = list?.error ?? list?.data?.error ?? null;
  return (
    <Row label={label} htmlFor={id} help={help} hint={hint} stack>
      <span className="local-model">
        {models.length > 0 && (
          <Select aria-label={`Список: ${label}`} size="sm" className="local-model__pick" placeholder="Своё имя — в поле"
            value={!name ? DEFAULT_MODEL : current ? current.id : ""} options={options}
            onChange={(v) => onChange(v === DEFAULT_MODEL ? "" : v)} />
        )}
        <input id={id} type="text" className={fieldClass({ mono: true })} spellCheck={false}
          placeholder={placeholder} aria-invalid={problem ? true : undefined}
          value={value} onChange={(e) => onChange(e.target.value)} />
        {list && (
          <Button icon={RefreshCw} onClick={list.refresh} busy={list.busy}>Обновить список</Button>
        )}
      </span>
      {problem && <span className="error proxy__error">{problem}</span>}
      {list && (
        <span className="local-find__status" role="status">
          {list.busy && !list.data ? <span className="muted">Спрашиваю список моделей…</span>
            : listError ? <span className="error">Список моделей не получен: {listError}. Имя модели можно вписать в поле</span>
              : list.data ? <span className="muted">Моделей в списке: {models.length}</span> : null}
        </span>
      )}
      {list?.data?.login && (
        <span className="proxy__error">Войдите один раз в терминале: <code className="path">{list.data.login}</code></span>
      )}
      {children}
    </Row>
  );
}

/**
 * Строка уровня рассуждений: уровни выбранной модели из CLI, без списка — запасные.
 * Уровень, которого у выбранной модели нет, в черновике становится «По умолчанию».
 */
function EffortRow({ label, hint, value, onChange, model, fallback, known }: {
  label: string; hint: string; value: string; onChange: (v: string) => void;
  /** Модель из списка CLI, к которой относятся уровни (не найдена — запасной список). */
  model: CliModel | undefined;
  fallback: readonly string[];
  /** Список CLI получен (иначе уровни — запасные и ничего не сбрасывается). */
  known: boolean;
}) {
  const levels: (string | CliEffort)[] = model && model.efforts.length > 0 ? model.efforts : [...fallback];
  const ids = levels.map((l) => (typeof l === "string" ? l : l.id));
  const strict = known && !!model && model.efforts.length > 0;
  const stale = strict && !!value && !ids.includes(value);
  useEffect(() => {
    if (stale) onChange("");
  }, [stale, onChange]);
  const now = model?.default_effort;
  const options = effortOptions(levels, now ? `сейчас: ${effortName(now)}` : "как настроено в самом CLI");
  // Своё значение из файла, которого нет в запасном списке, — показать как есть.
  if (value && !ids.includes(value) && !strict) options.push({ value, label: effortName(value) });
  return (
    <Row label={label} hint={hint}>
      <Select aria-label={label} options={options} value={stale ? EFFORT_DEFAULT : value || EFFORT_DEFAULT}
        onChange={(v) => onChange(v === EFFORT_DEFAULT ? "" : v)} />
    </Row>
  );
}

const CLAUDE_LIST: CliModel[] = [
  { id: "sonnet", label: "Sonnet", default: true, efforts: [] },
  { id: "opus", label: "Opus", efforts: [] },
  { id: "haiku", label: "Haiku", efforts: [] },
];

/** Настройки одной модели (`provider`) — содержимое карточки под таблицей. */
export function ModelCard({ provider, title, note, llm, set, endpoint }: {
  provider: string;
  title: string;
  /** Строка под заголовком: модель по умолчанию или выбрана в таблице. */
  note: string;
  llm: Record<string, unknown> | undefined;
  set: SetFn;
  endpoint: Endpoint;
}) {
  const cli = provider === "codex" || provider === "agy" || provider === "opencode" ? provider : null;
  const list = useCliModels(endpoint, cli);
  const str = (k: string) => String(llm?.[k] ?? "");
  const setLlm = useCallback((k: string) => (v: string) => set("llm", k, v), [set]);
  const setCodexEffort = useCallback((v: string) => set("llm", "codex_effort", v), [set]);
  const setAgyEffort = useCallback((v: string) => set("llm", "agy_effort", v), [set]);
  const models = list.data?.models ?? [];
  const known = models.length > 0;
  let body: ReactNode = null;
  if (provider === "claude-code") {
    const model = str("model");
    body = (
      <Row label={MODEL_LABEL} htmlFor="llm-model" help={<ModelTip />}
        hint="Готовит итоги и анализ, отвечает на вопросы и ведёт живого ассистента">
        <span className="model-effort">
          <Select aria-label={`Список: ${MODEL_LABEL}`} size="sm" placeholder="Своё имя — в поле"
            value={CLAUDE_LIST.find((m) => m.id === (model.trim() || "sonnet"))?.id ?? ""}
            options={CLAUDE_LIST.map((m) => ({ value: m.id, label: m.label, detail: m.default ? "по умолчанию" : undefined }))}
            onChange={(v) => set("llm", "model", v)} />
          <input id="llm-model" type="text" className={fieldClass()} placeholder="sonnet" spellCheck={false}
            value={model} onChange={(e) => set("llm", "model", e.target.value)} />
          {/* Уровень рассуждений — рядом с моделью (0.5). */}
          <Select aria-label={EFFORT_LABEL} options={effortOptions(CLAUDE_EFFORTS)}
            value={str("effort") || EFFORT_DEFAULT}
            onChange={(v) => set("llm", "effort", v === EFFORT_DEFAULT ? "" : v)} />
        </span>
      </Row>
    );
  } else if (provider === "codex") {
    const model = str("codex_model");
    body = (
      <>
        <ModelPickRow id="llm-codex-model" label={CODEX_MODEL_LABEL} help={<CodexModelTip />}
          hint="Список — из самого Codex. Пусто — модель из настроек Codex" placeholder="как в настройках Codex"
          fallbackHint="как в настройках Codex" value={model} onChange={setLlm("codex_model")}
          problem={codexModelError(model)} list={list} />
        <EffortRow label={CODEX_EFFORT_LABEL} hint="Сколько Codex думает над ответом: дольше — точнее"
          value={str("codex_effort")} onChange={setCodexEffort} model={modelOf(models, model)}
          fallback={CODEX_EFFORTS} known={known} />
      </>
    );
  } else if (provider === "agy") {
    const model = str("agy_model");
    body = (
      <>
        <ModelPickRow id="llm-agy-model" label={AGY_MODEL_LABEL} help={<AgyModelTip />}
          hint="Список — из самого Antigravity CLI. Пусто — модель из настроек agy" placeholder="как в настройках agy"
          fallbackHint="как в настройках agy" value={model} onChange={setLlm("agy_model")}
          problem={agyModelError(model)} list={list}>
          <span className="muted proxy__error">{AGY_PLAN_NOTICE}</span>
        </ModelPickRow>
        <EffortRow label={AGY_EFFORT_LABEL}
          hint="Сколько agy думает над ответом; применяется, если у модели есть такая настройка"
          value={str("agy_effort")} onChange={setAgyEffort} model={modelOf(models, model)}
          fallback={AGY_EFFORTS} known={known} />
      </>
    );
  } else if (provider === "opencode") {
    const model = str("opencode_model");
    body = (
      <ModelPickRow id="llm-opencode-model" label={OPENCODE_MODEL_LABEL} help={<OpencodeModelTip />}
        hint="Провайдер/модель, как в opencode models. Пусто — модель из настроек OpenCode"
        placeholder="anthropic/claude-sonnet-4-5" fallbackHint="как в настройках OpenCode" value={model}
        onChange={setLlm("opencode_model")} problem={opencodeModelError(model)} list={list} />
    );
  } else {
    body = (
      <>
        <p className="muted sdesc">Локальная модель не использует базу знаний.</p>
        <LocalModelRows baseUrl={str("base_url")} model={str("local_model")} set={set} endpoint={endpoint}
          viaProxy={llm?.local_via_proxy === true} />
      </>
    );
  }
  return (
    <SettingsCard title={title}>
      <p className="muted sdesc">{note}</p>
      {body}
    </SettingsCard>
  );
}
