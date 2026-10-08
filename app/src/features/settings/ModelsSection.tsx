/**
 * «Модели ИИ» (0.4, прежде — верх раздела «Ассистент»): какие модели включены и
 * какая по умолчанию, модель Claude Code и OpenCode, локальная модель, прокси; в «Тонкой
 * настройке» — «Локальную модель — через прокси».
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
import { Tip } from "../../ui/Tip";
import { fieldClass } from "./fields";
import { FineTuning, Radio, Row, SettingsCard, type Raw, type SetFn } from "./Section";
import { LocalModelRows, LocalViaProxyRow } from "./LocalModelRows";
import { ProviderTip } from "./tips";
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
const LLM_KEYS = ["provider", "base_url", "local_model", "proxy", "opencode_model", "enabled", "local_via_proxy"];

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
        Модель, на которой Claude Code готовит итоги, анализ и названия, отвечает на вопросы, ведёт
        живого ассистента и работает во вкладке «Агент» (и в «Продолжить прошлую»): sonnet, opus, haiku или
        полное имя модели. Пусто — sonnet. «Проверить» у Claude Code проверяет и эту модель.
      </TipLine>
      <TipLine>
        Живые подсказки в режиме «Быстрее» идут на Haiku. Во вкладке «Агент» модель можно сменить своим{" "}
        <code>--model</code> в параметрах запуска. Codex берёт модель из своего конфига, локальная модель
        задаётся отдельно.
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

/** Как у резидента (`settings.SOCKS_LOCAL_ERROR`): urllib не умеет SOCKS. */
export const SOCKS_LOCAL_ERROR = "SOCKS-прокси для локальной модели не поддерживается — укажите HTTP-прокси "
  + "или выключите «Локальную модель — через прокси»";

/** Локальная модель «через прокси» с SOCKS-адресом — не сохранить (текст), иначе null. */
export function socksLocalError(llm: Record<string, unknown> | undefined): string | null {
  return llm?.local_via_proxy === true && /^socks/i.test(String(llm?.proxy ?? "")) ? SOCKS_LOCAL_ERROR : null;
}

/**
 * Правки раздела нельзя сохранить: прокси, модель OpenCode или SOCKS у
 * локальной модели вне правил. Смотрим только изменённое (`changes` — то, что
 * уйдёт в PATCH): значение, уже лежащее в файле, не должно запирать
 * «Сохранить» для остальных разделов.
 */
export function modelsChangesInvalid(changes: Raw, draft?: Raw): boolean {
  const proxy = changes.llm?.proxy;
  const ocModel = changes.llm?.opencode_model;
  return (typeof proxy === "string" && proxyError(proxy) !== null)
    || (typeof ocModel === "string" && opencodeModelError(ocModel) !== null)
    || ((changes.llm?.local_via_proxy !== undefined || changes.llm?.proxy !== undefined)
      && socksLocalError(draft?.llm ?? changes.llm) !== null);
}

/** Подпись «Авто» в таблице моделей; порядок выбора — подсказкой (AUTO_ORDER). */
export const AUTO_SHORT = "первая готовая из включённых";
/** Порядок выбора «Авто» (llm.resolve). */
export const AUTO_ORDER = "первый готовый из включённых: Claude Code → Codex → локальная (OpenCode — только явным выбором)";

const titleOf = (name: string) => PROVIDERS.find((p) => p.value === name)?.label ?? name;

type Check = { busy: boolean; ok?: boolean; text?: string };

/**
 * Состояние модели бейджем DS: итог «Проверить», если был, иначе что видит
 * резидент — найден ли CLI (путь — в подсказке), отвечает ли локальный сервер,
 * кого сейчас выбирает «Авто». Сведений ещё нет — пусто.
 */
function ProviderState({ provider, info, check }: { provider: string; info: AssistantInfo | null; check?: Check }) {
  if (check && !check.busy) {
    return check.ok ? <span className="badge badge--fresh">работает</span> : <span className="badge badge--error">не работает</span>;
  }
  if (!info) return null;
  if (provider === "auto") {
    // `provider` — кто отвечает при СОХРАНЁННОМ выборе: при явном Codex это
    // Codex, а не то, кого взял бы «Авто» (он предпочёл бы Claude Code).
    if (info.setting !== "auto") return null;
    if (info.provider) return <span className="badge badge--info">сейчас: {titleOf(info.provider)}</span>;
    return info.checking ? <span className="badge">определяю…</span> : <span className="badge badge--error">нет доступного</span>;
  }
  const found = info.available[provider];
  if (!found) return null;
  if (provider === LOCAL) {
    return (
      <Tip content={`адрес: ${found.base_url ?? ""}`}>
        <span className={`badge ${found.found ? "badge--fresh" : "badge--error"}`}>{found.found ? "доступен" : "недоступен"}</span>
      </Tip>
    );
  }
  return found.found
    ? <Tip content={found.path ?? ""}><span className="badge badge--fresh">найден</span></Tip>
    : <span className="badge badge--error">не найден</span>;
}

/**
 * Сведения о провайдерах (`GET /assistant`): что найдено на машине и кого
 * выбрал бы «Авто». Перечитываются после сохранения `llm` (кэш выбора у
 * резидента — по этим ключам) и по новому `tick` (после «Проверить»). Пока
 * резидент проверяет вход — переспрашиваем, но не бесконечно.
 */
export function useAssistantInfo(endpoint: Endpoint, saved: Raw, tick = 0) {
  const [info, setInfo] = useState<AssistantInfo | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
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
  return { info, infoError };
}

/** Модель по умолчанию, которая ответит сейчас (null — неизвестно): для пометок «Ассистента». */
export function currentProvider(info: AssistantInfo | null, draft: Raw): string | null {
  const chosen = String(draft.llm?.provider ?? "auto");
  return info?.provider ?? (chosen === "auto" ? null : chosen);
}

export function ModelsSection({ draft, saved, set, endpoint }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint;
}) {
  const [checks, setChecks] = useState<Record<string, Check>>({});
  const [tick, setTick] = useState(0);
  const { info, infoError } = useAssistantInfo(endpoint, saved, tick);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const check = useCallback(async (provider: string) => {
    setChecks((cur) => ({ ...cur, [provider]: { busy: true } }));
    let result: Check;
    try {
      const r = await checkProvider(endpoint, provider);
      // У локальной модели проверка сообщает и окно контекста.
      result = r.ok ? { busy: false, ok: true, text: r.detail ? `работает; ${r.detail}` : "работает" }
        : { busy: false, ok: false, text: r.error || "не работает" };
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
      <SettingsCard title="Провайдеры">
        <Row label="Модели"
          hint="Включённые можно выбрать у действий карточки. Модель по умолчанию готовит анализ, итоги и названия автоматически и ведёт живого ассистента"
          help={<ProviderTip />} stack>
          {/* Таблица: модель · по умолчанию (радио одной группы по name) · включена · состояние · проверка. */}
          <div className="scroll-x mtable-wrap">
            <table className="tbl mtable" aria-label="Модели">
              <thead>
                <tr>
                  <th scope="col">Модель</th>
                  <th scope="col" className="mtable__c">По умолчанию</th>
                  <th scope="col" className="mtable__c">Включена</th>
                  <th scope="col"><span className="sr-only">Проверка</span></th>
                </tr>
              </thead>
              <tbody>
                {PROVIDERS.map((p) => {
                  const c = checks[p.value];
                  const concrete = p.value !== "auto";
                  const missing = p.link && info?.available[p.value]?.found === false;
                  const lock = concrete ? locked(p.value) : null;
                  const sub = !concrete
                    ? (autoBlocked && chosen !== "auto" ? autoBlocked : AUTO_SHORT)
                    : missing ? null : on(p.value) ? privacyLine(p.value, baseUrl) : null;
                  return (
                    <tr key={p.value} aria-label={p.label}>
                      <td className="mtable__model">
                        <span className="mtable__head">
                          <span className="mtable__name">{p.label}</span>
                          <ProviderState provider={p.value} info={info} check={c} />
                        </span>
                        {missing ? (
                          <span className="mtable__sub">не найден — установите{" "}
                            <button type="button" className="provider__link"
                              onClick={() => void openUrl(`https://${p.link}`)}>
                              {p.link}
                            </button>
                          </span>
                        ) : sub === AUTO_SHORT ? (
                          <Tip content={AUTO_ORDER}><span className="mtable__sub">{sub}</span></Tip>
                        ) : sub && (
                          <span className={`mtable__sub${p.value === LOCAL && loopback(baseUrl) ? " mtable__sub--local" : ""}`}>
                            {sub}
                          </span>
                        )}
                        {c && !c.busy && c.text && (c.text !== "работает" || !c.ok) && (
                          <span className={`mtable__note ${c.ok ? "muted" : "error"}`}>{c.text}</span>
                        )}
                      </td>
                      <td className="mtable__c">
                        <Tip content={(!concrete && autoBlocked) || "Модель по умолчанию: вся автоматическая работа"}>
                          <input type="radio" className="rd" name="llm-provider" aria-label={p.label}
                            checked={chosen === p.value}
                            disabled={!concrete && autoBlocked !== null && chosen !== "auto"}
                            onChange={() => makeDefault(p.value)} />
                        </Tip>
                      </td>
                      <td className="mtable__c">
                        {concrete ? (
                          <Tip content={lock ?? "Можно выбрать у действий карточки"}>
                            <button type="button" role="switch" className="switch" aria-label={`Включить: ${p.label}`}
                              aria-checked={on(p.value)} disabled={lock !== null}
                              onClick={() => toggle(p.value, !on(p.value))} />
                          </Tip>
                        ) : <span className="muted">—</span>}
                      </td>
                      <td className="mtable__act">
                        <Button onClick={() => void check(p.value)} disabled={c?.busy}>
                          {c?.busy ? "Проверяю…" : "Проверить"}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {infoError && <span className="error mtable__foot">Сведения о провайдерах недоступны: {infoError}</span>}
          {llmDirty && <span className="muted mtable__foot">Проверяются сохранённые настройки — сначала сохраните изменения</span>}
        </Row>
        {on(CLAUDE) && (
          <Row label={MODEL_LABEL} htmlFor="llm-model" help={<ModelTip />}
            hint="Готовит итоги и анализ, отвечает на вопросы и ведёт живого ассистента">
            <input id="llm-model" type="text" className={fieldClass()} placeholder="sonnet"
              value={String(llm("model") ?? "")}
              onChange={(e) => set("llm", "model", e.target.value)} />
          </Row>
        )}
        {on(OPENCODE) && (
          <Row label={OPENCODE_MODEL_LABEL} htmlFor="llm-opencode-model" help={<OpencodeModelTip />}
            hint="Провайдер/модель, как в opencode models. Пусто — модель из настроек OpenCode">
            <input id="llm-opencode-model" type="text" className={fieldClass({ mono: true })} spellCheck={false}
              placeholder="anthropic/claude-sonnet-4-5" aria-invalid={ocModelProblem ? true : undefined}
              value={ocModel} onChange={(e) => set("llm", "opencode_model", e.target.value)} />
            {ocModelProblem && <span className="error proxy__error">{ocModelProblem}</span>}
          </Row>
        )}
      </SettingsCard>
      {on(LOCAL) && (
        <SettingsCard title="Локальная модель">
          <p className="muted sdesc">Локальная модель не использует базу знаний.</p>
          <LocalModelRows baseUrl={baseUrl} model={String(llm("local_model") ?? "")} set={set} endpoint={endpoint}
            viaProxy={llm("local_via_proxy") === true} />
          {socksLocalError(draft.llm) && <span className="error" role="alert">{socksLocalError(draft.llm)}</span>}
        </SettingsCard>
      )}
      <SettingsCard title="Подключение">
        <Radio label={PROXY_LABEL} hint="Через него Claude Code, Codex и OpenCode подключаются к своим сервисам"
          help={<HelpTip label="Зачем нужен прокси"><TipLine>{PROXY_HELP}</TipLine></HelpTip>} stack
          value={proxyMode}
          options={[
            { value: "system", label: systemProxyLabel(info?.proxy) },
            { value: "none", label: "Без прокси" },
            { value: "custom", label: "Свой адрес…" },
          ]}
          onChange={(mode) => set("llm", "proxy", mode === "custom" ? customStart : mode)}>
          {proxyMode === "custom" && (
            <span className="proxy">
              <input type="text" className={fieldClass({ wide: true, mono: true })} aria-label="Адрес прокси"
                placeholder="http://127.0.0.1:8080" value={proxy} spellCheck={false}
                aria-invalid={proxyProblem ? true : undefined}
                onChange={(e) => { setCustomProxy(e.target.value); set("llm", "proxy", e.target.value); }} />
              {proxyProblem && <span className="error proxy__error">{proxyProblem}</span>}
            </span>
          )}
        </Radio>
      </SettingsCard>
      <FineTuning>
        <LocalViaProxyRow value={llm("local_via_proxy") === true} set={set} disabled={!on(LOCAL)} />
      </FineTuning>
    </>
  );
}
