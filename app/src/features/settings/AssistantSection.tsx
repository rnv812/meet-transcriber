/**
 * Настройки «Ассистент»: модели (какие включены и какая по умолчанию), откуда
 * знания, окно живой расшифровки. Куда выгружаются встречи — раздел «Экспорт встреч».
 *
 * «Модели» (0.3.4): включённые модели (`llm.enabled`) можно выбрать у действий
 * карточки — «Переанализировать», «Итоги», «Улучшить расшифровку», «Предложить
 * название»; модель по умолчанию (`llm.provider`, «Авто» — первая готовая из
 * включённых) делает всю автоматическую работу. У каждой включённой — куда
 * уходит текст встречи.
 *
 * Сведения о провайдерах (`GET /assistant`) — не черновик: что найдено на
 * машине и кого выбрал бы «Авто». Резидент кэширует выбор по сохранённым
 * `llm.provider`/`base_url`, поэтому после сохранения `llm` и после «Проверить»
 * (резидент сбрасывает кэш) сведения перечитываются. «Проверить» зовёт модель
 * по сохранённым настройкам — до полутора минут.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { type Endpoint, checkProvider, getAssistant } from "../../lib/api";
import { errorText } from "../../lib/format";
import { openUrl } from "../../lib/shell";
import type { AssistantInfo, ProxyInfo } from "../../lib/types";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { FolderRow, Row, type Raw, type SetFn } from "./Section";
import { LiveHintsRows } from "./LiveHintsRows";
import { LocalModelRows } from "./LocalModelRows";
import { AgentLaunchSection, agentLaunchChangesInvalid } from "./AgentLaunchSection";
import { KnowledgeTip, LiveWindowTip, ProviderTip } from "./tips";
import { PRIVACY_CLOUD, PRIVACY_LOCAL } from "../../lib/llm";

type Provider = { value: string; label: string; link?: string };

const PROVIDERS: Provider[] = [
  { value: "auto", label: "Авто" },
  { value: "claude-code", label: "Claude Code", link: "claude.ai/code" },
  { value: "codex", label: "Codex", link: "github.com/openai/codex" },
  { value: "opencode", label: "OpenCode", link: "opencode.ai/docs/" },
  { value: "openai-compatible", label: "Локальная (LM Studio / Ollama)" },
];
const LOCAL = "openai-compatible";
const OPENCODE = "opencode";
const CLAUDE = "claude-code";
/** Настоящие модели в порядке окна (как `llm.enabled` у резидента). */
const CONCRETE = PROVIDERS.filter((p) => p.value !== "auto").map((p) => p.value);
/** Кандидаты «Авто» — его выбор у конфига без списка включённых. */
const AUTO_CANDIDATES = ["claude-code", "codex", "openai-compatible"];
/** Ключи `llm`, по которым идёт проверка и выбор «Авто». */
const LLM_KEYS = ["provider", "base_url", "local_model", "proxy", "opencode_model", "enabled"];

/**
 * Включённые модели из черновика `llm`: список резидента, а у конфига без него
 * (старый резидент) — как читает резидент: у «Авто» его кандидаты, иначе одна
 * модель. Модель по умолчанию включена всегда.
 */
export function enabledOf(llm: Record<string, unknown> | undefined): string[] {
  const provider = String(llm?.provider ?? "auto");
  const raw = Array.isArray(llm?.enabled) ? (llm.enabled as unknown[]) : provider === "auto" ? AUTO_CANDIDATES : [provider];
  const wanted = new Set(raw.filter((x): x is string => typeof x === "string"));
  if (provider !== "auto") wanted.add(provider);
  return CONCRETE.filter((p) => wanted.has(p));
}

/** Локальная модель на этом компьютере (адрес — localhost/127.x): как `meet.llm.is_local`. */
export function loopback(baseUrl: string): boolean {
  try {
    const host = new URL(baseUrl).hostname.toLowerCase().replace(/^\[|\]$/g, "");
    return host === "localhost" || host === "::1" || host.startsWith("127.");
  } catch {
    return false;
  }
}

/** Куда уходит текст встречи у модели `provider`. */
export function privacyLine(provider: string, baseUrl: string): string {
  if (provider !== LOCAL) return PRIVACY_CLOUD;
  if (loopback(baseUrl)) return PRIVACY_LOCAL;
  let host = baseUrl;
  try { host = new URL(baseUrl).host; } catch { /* адрес как есть */ }
  return `сервер в сети — текст встречи уходит на ${host}`;
}

/** Подпись `llm.model`: на неё ссылаются подсказки других разделов. */
export const MODEL_LABEL = "Модель Claude Code";

function ModelTip() {
  return (
    <HelpTip label="Какая модель отвечает" title={MODEL_LABEL}>
      <TipLine>
        Модель, на которой Claude Code готовит итоги, анализ и названия, отвечает на вопросы и ведёт
        живого ассистента: sonnet, opus, haiku или полное имя модели. Пусто — sonnet. «Проверить» у Claude Code
        проверяет и эту модель.
      </TipLine>
      <TipLine>
        Живые подсказки в режиме «Быстрее» идут на Haiku. Codex берёт модель из своего конфига, локальная
        модель задаётся отдельно.
      </TipLine>
    </HelpTip>
  );
}

/** Подпись `llm.opencode_model`. */
export const OPENCODE_MODEL_LABEL = "Модель OpenCode";
const OPENCODE_MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9._:@+-][A-Za-z0-9._:@+/-]*$/;

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

const PROXY_LABEL = "Прокси для подключения к моделям";
const PROXY_HELP = "Claude Code, Codex и OpenCode сами не используют системный прокси Windows — приложение передаёт его им. "
  + "Прокси нужен, если доступ к сервисам идёт через VPN или прокси-сервер.";
const PROXY_SCHEMES = ["http", "https", "socks5", "socks5h"];
const PROXY_EXAMPLE = "например http://127.0.0.1:8080";

/**
 * Значение `llm.proxy` нельзя сохранить: текст для человека, иначе null.
 * Та же проверка, что у резидента (`meet.netproxy.check`): схема, узел, порт.
 */
export function proxyError(value: string): string | null {
  const text = value.trim();
  if (text === "system" || text === "none") return null;
  if (!text) return `Укажите адрес прокси, ${PROXY_EXAMPLE}`;
  const [, scheme = "", tail = ""] = /^([a-z0-9+.-]+):\/\/(.*)$/i.exec(text) ?? [];
  if (!PROXY_SCHEMES.includes(scheme.toLowerCase())) {
    return "Адрес прокси должен начинаться с http://, https:// или socks5://";
  }
  const rest = tail.replace(/\/$/, "");
  if (/[/?#]/.test(rest)) return `В адресе прокси нужны только схема, узел и порт, ${PROXY_EXAMPLE}`;
  const [, host, port] = /^(\[[^\]]*\]|[^:]*)(?::(.*))?$/.exec(rest.slice(rest.lastIndexOf("@") + 1)) ?? [];
  if (port !== undefined && port !== "" && (!/^\d+$/.test(port) || Number(port) > 65535)) {
    return "Адрес прокси не распознан: проверьте узел и порт";
  }
  if (!host) return `В адресе прокси нет узла, ${PROXY_EXAMPLE}`;
  if (!port) return `В адресе прокси нет порта, ${PROXY_EXAMPLE}`;
  return null;
}

/** Подпись варианта «Как в системе»: что сейчас даёт система (без «http://»). */
export function systemProxyLabel(proxy: ProxyInfo | undefined): string {
  if (!proxy) return "Как в системе";
  const now = proxy.system ? proxy.system.replace(/^http:\/\//i, "") : "не задан";
  return `Как в системе (сейчас: ${now})`;
}

/**
 * Пока резидент проверяет вход в CLI (`checking`), спрашиваем снова через
 * паузу — но не бесконечно: зависшая проверка не должна опрашивать резидент,
 * пока открыты настройки (дальше — по сохранению или «Проверить»).
 */
export const RECHECK_MS = 1500;
export const RECHECK_TRIES = 20;

export const WINDOW_MIN = 5;
export const WINDOW_MAX = 120;

const windowInvalid = (value: unknown): boolean =>
  typeof value !== "number" || !Number.isFinite(value) || value < WINDOW_MIN || value > WINDOW_MAX;

/**
 * Правки раздела нельзя сохранить: окно вне правил. Смотрим только изменённое
 * (`changes` — то, что уйдёт в PATCH): значение, уже лежащее в файле, не должно
 * запирать «Сохранить» для остальных разделов.
 */
export function assistantChangesInvalid(changes: Raw): boolean {
  const win = changes.assist?.window_seconds;
  const proxy = changes.llm?.proxy;
  const ocModel = changes.llm?.opencode_model;
  return (win !== undefined && windowInvalid(win))
    || (typeof proxy === "string" && proxyError(proxy) !== null)
    || (typeof ocModel === "string" && opencodeModelError(ocModel) !== null)
    || agentLaunchChangesInvalid(changes);
}

/** Подпись «Авто», пока выбран конкретный провайдер: порядок выбора (llm.resolve). */
export const AUTO_ORDER = "первый готовый из включённых: Claude Code → Codex → локальная (OpenCode — только явным выбором)";

const titleOf = (name: string) => PROVIDERS.find((p) => p.value === name)?.label ?? name;

type Check = { busy: boolean; ok?: boolean; text?: string };

function status(p: Provider, info: AssistantInfo | null): string | null {
  if (!info) return null;
  if (p.value === "auto") {
    // `provider` — кто отвечает при СОХРАНЁННОМ выборе: при явном Codex это
    // Codex, а не то, кого взял бы «Авто» (он предпочёл бы Claude Code).
    if (info.setting !== "auto") return AUTO_ORDER;
    if (info.provider) return `сейчас: ${titleOf(info.provider)}`;
    return info.checking ? "определяю…" : "нет доступного провайдера";
  }
  const found = info.available[p.value];
  if (!found) return null;
  if (p.value === LOCAL) return `адрес: ${found.base_url ?? ""} — ${found.found ? "доступен" : "недоступен"}`;
  return found.found ? `найден: ${found.path ?? ""}` : null;
}

export function AssistantSection({ draft, saved, set, endpoint }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint;
}) {
  const [info, setInfo] = useState<AssistantInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, Check>>({});
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  // Перечитать после сохранения `llm`: кэш выбора у резидента — по этим ключам.
  const savedLlm = JSON.stringify(saved.llm ?? null);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let tries = 0;
    const load = () => {
      getAssistant(endpoint).then((data) => {
        if (!live) return;
        setInfo(data);
        setInfoError(null);
        if (data.checking && tries++ < RECHECK_TRIES) timer = setTimeout(load, RECHECK_MS);
      }).catch((e) => { if (live) setInfoError(errorText(e)); });
    };
    load();
    return () => { live = false; clearTimeout(timer); };
  }, [endpoint, savedLlm, tick]);

  const check = useCallback(async (provider: string) => {
    setChecks((cur) => ({ ...cur, [provider]: { busy: true } }));
    let result: Check;
    try {
      const r = await checkProvider(endpoint, provider);
      result = r.ok ? { busy: false, ok: true, text: "работает" } : { busy: false, ok: false, text: r.error || "не работает" };
    } catch (e) {
      result = { busy: false, ok: false, text: errorText(e) };
    }
    if (!alive.current) return;
    setChecks((cur) => ({ ...cur, [provider]: result }));
    setTick((t) => t + 1);
  }, [endpoint]);

  const llm = (k: string) => draft.llm?.[k];
  const chosen = String(llm("provider") ?? "auto");
  const enabled = enabledOf(draft.llm);
  const on = (name: string) => enabled.includes(name);
  /**
   * «Авто» выбирает только из своих включённых (Claude Code, Codex, локальная):
   * ни одной не включено — сначала включить, само оно никого не включает
   * (текст встречи не должен сам уйти облачной модели). null — выбрать можно.
   */
  const autoBlocked = enabled.some((p) => AUTO_CANDIDATES.includes(p)) ? null
    : "Включите Claude Code, Codex или локальную модель — из них выбирает «Авто»";
  /** Модель по умолчанию — она же включена. */
  const makeDefault = (name: string) => {
    set("llm", "provider", name);
    if (name !== "auto" && !on(name)) set("llm", "enabled", CONCRETE.filter((p) => p === name || on(p)));
  };
  /**
   * Почему флажок «включена» нельзя снять (null — можно): модель по умолчанию
   * включена всегда, а у «Авто» должна остаться хоть одна своя модель.
   */
  const locked = (name: string): string | null => {
    if (chosen === name) return "Модель по умолчанию включена всегда";
    if (chosen === "auto" && on(name) && AUTO_CANDIDATES.includes(name)
        && enabled.filter((p) => AUTO_CANDIDATES.includes(p)).length === 1) {
      return "Последняя модель для «Авто»: включите другую, прежде чем выключать эту";
    }
    return null;
  };
  const toggle = (name: string, value: boolean) =>
    set("llm", "enabled", CONCRETE.filter((p) => (p === name ? value : on(p))));
  const baseUrl = String(llm("base_url") ?? "");
  const llmDirty = LLM_KEYS.some((k) => JSON.stringify(draft.llm?.[k] ?? null) !== JSON.stringify(saved.llm?.[k] ?? null));
  const win = draft.assist?.window_seconds as number | null | undefined;
  const proxy = String(llm("proxy") ?? "system");
  const proxyMode = proxy === "system" || proxy === "none" ? proxy : "custom";
  const savedProxy = String(saved.llm?.proxy ?? "system");
  // Вернуться к своему адресу после «Без прокси» — с прежним текстом в поле.
  const [customProxy, setCustomProxy] = useState<string | null>(null);
  const customStart = customProxy ?? (savedProxy === "system" || savedProxy === "none" ? "" : savedProxy);
  const proxyProblem = proxyMode === "custom" ? proxyError(proxy) : null;
  const ocModel = String(llm("opencode_model") ?? "");
  const ocModelProblem = opencodeModelError(ocModel);

  return (
    <>
      <Row label="Модели"
        hint="Включённые можно выбрать у действий карточки. Модель по умолчанию готовит анализ, итоги и названия автоматически и ведёт живого ассистента"
        help={<ProviderTip />} stack>
        {/* Радио «по умолчанию» — одна группа по имени (name), рядом с флажками «включена». */}
        <div role="group" aria-label="Модели" className="providers">
          {PROVIDERS.map((p) => {
            const line = status(p, info);
            const missing = p.link && info?.available[p.value]?.found === false;
            const c = checks[p.value];
            const concrete = p.value !== "auto";
            return (
              <div key={p.value} role="group" aria-label={p.label} className="provider">
                <div className="provider__head">
                  <label className="radios__item"
                    title={(!concrete && autoBlocked) || "Модель по умолчанию: вся автоматическая работа"}>
                    <input type="radio" name="llm-provider" checked={chosen === p.value}
                      disabled={!concrete && autoBlocked !== null && chosen !== "auto"}
                      onChange={() => makeDefault(p.value)} />
                    {p.label}
                  </label>
                  {chosen === p.value && <span className="provider__default">по умолчанию</span>}
                  {concrete && (
                    <label className="provider__enable" title={locked(p.value) ?? "Можно выбрать у действий карточки"}>
                      <input type="checkbox" aria-label={`Включить: ${p.label}`} checked={on(p.value)}
                        disabled={locked(p.value) !== null} onChange={(e) => toggle(p.value, e.target.checked)} />
                      включена
                    </label>
                  )}
                </div>
                <div className="provider__status">
                  {missing ? (
                    <span className="muted">не найден — установите{" "}
                      <button type="button" className="provider__link"
                        onClick={() => void openUrl(`https://${p.link}`)}>
                        {p.link}
                      </button>
                    </span>
                  ) : !concrete && autoBlocked && chosen !== "auto" ? <span className="muted">{autoBlocked}</span>
                    : line ? <span className="muted">{line}</span> : null}
                </div>
                {concrete && on(p.value) && (
                  <div className={`provider__privacy${p.value === LOCAL && loopback(baseUrl) ? " provider__privacy--local" : ""}`}>
                    {privacyLine(p.value, baseUrl)}
                  </div>
                )}
                <div className="provider__check">
                  <Button onClick={() => void check(p.value)} disabled={c?.busy}>
                    {c?.busy ? "Проверяю…" : "Проверить"}
                  </Button>
                  {c && !c.busy && <span className={c.ok ? "notice" : "error"}>{c.text}</span>}
                </div>
              </div>
            );
          })}
          {infoError && <span className="error">Сведения о провайдерах недоступны: {infoError}</span>}
          {llmDirty && <span className="muted">Проверяются сохранённые настройки — сначала сохраните изменения</span>}
        </div>
      </Row>
      {on(CLAUDE) && (
        <Row label={MODEL_LABEL} htmlFor="llm-model" help={<ModelTip />}
          hint="Готовит итоги и анализ, отвечает на вопросы и ведёт живого ассистента">
          <input id="llm-model" type="text" placeholder="sonnet"
            value={String(llm("model") ?? "")}
            onChange={(e) => set("llm", "model", e.target.value)} />
        </Row>
      )}
      {on(OPENCODE) && (
        <Row label={OPENCODE_MODEL_LABEL} htmlFor="llm-opencode-model" help={<OpencodeModelTip />}
          hint="Провайдер/модель, как в opencode models. Пусто — модель из настроек OpenCode">
          <input id="llm-opencode-model" type="text" spellCheck={false} placeholder="anthropic/claude-sonnet-4-5"
            value={ocModel} onChange={(e) => set("llm", "opencode_model", e.target.value)} />
          {ocModelProblem && <span className="error">{ocModelProblem}</span>}
        </Row>
      )}
      {on(LOCAL) && (
        <>
          <p className="muted sdesc">Локальная модель не использует базу знаний.</p>
          <LocalModelRows baseUrl={baseUrl} model={String(llm("local_model") ?? "")} set={set} endpoint={endpoint} />
        </>
      )}
      <Row label={PROXY_LABEL} hint="Через него Claude Code, Codex и OpenCode подключаются к своим сервисам"
        help={<HelpTip label="Зачем нужен прокси"><TipLine>{PROXY_HELP}</TipLine></HelpTip>} stack>
        <div role="radiogroup" aria-label={PROXY_LABEL} className="radios radios--column">
          <label className="radios__item">
            <input type="radio" name="llm-proxy" checked={proxyMode === "system"}
              onChange={() => set("llm", "proxy", "system")} />
            {systemProxyLabel(info?.proxy)}
          </label>
          <label className="radios__item">
            <input type="radio" name="llm-proxy" checked={proxyMode === "none"}
              onChange={() => set("llm", "proxy", "none")} />
            Без прокси
          </label>
          <label className="radios__item">
            <input type="radio" name="llm-proxy" checked={proxyMode === "custom"}
              onChange={() => set("llm", "proxy", customStart)} />
            Свой адрес…
          </label>
        </div>
        {proxyMode === "custom" && (
          <>
            <input type="text" aria-label="Адрес прокси" placeholder="http://127.0.0.1:8080" value={proxy}
              onChange={(e) => { setCustomProxy(e.target.value); set("llm", "proxy", e.target.value); }} />
            {proxyProblem && <span className="error proxy__error">{proxyProblem}</span>}
          </>
        )}
      </Row>
      <h3 className="shead">База знаний</h3>
      <FolderRow label="База знаний для ассистента" help={<KnowledgeTip />}
        hint="Папка с материалами, по которой ассистент сверяет термины и имена"
        value={(draft.assistant?.knowledge_dir as string | null | undefined) ?? null}
        onChange={(v) => set("assistant", "knowledge_dir", v)} />
      <h3 className="shead">Живой ассистент</h3>
      <Row label="Окно живой расшифровки, с" htmlFor="assist-window" help={<LiveWindowTip min={WINDOW_MIN} max={WINDOW_MAX} />}
        hint="Как часто расшифровывается новый звук. Применяется со следующего запуска ассистента">
        <input id="assist-window" type="number" className="num" min={WINDOW_MIN} max={WINDOW_MAX} step={5}
          value={win ?? ""}
          onChange={(e) => {
            const n = e.target.value === "" ? null : Number(e.target.value);
            set("assist", "window_seconds", n !== null && Number.isFinite(n) ? n : null);
          }} />
        {win !== undefined && windowInvalid(win) && <span className="error">От {WINDOW_MIN} до {WINDOW_MAX} секунд</span>}
      </Row>
      <LiveHintsRows draft={draft} set={set} provider={info?.provider ?? (chosen === "auto" ? null : chosen)} />
      <AgentLaunchSection draft={draft} set={set} />
    </>
  );
}
