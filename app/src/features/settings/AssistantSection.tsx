/**
 * Настройки «Ассистент»: кто отвечает (провайдер модели), откуда знания, окно
 * живой расшифровки. Куда выгружаются встречи — раздел «Экспорт встреч».
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
import { KnowledgeTip, LiveWindowTip, ProviderTip } from "./tips";

type Provider = { value: string; label: string; link?: string };

const PROVIDERS: Provider[] = [
  { value: "auto", label: "Авто" },
  { value: "claude-code", label: "Claude Code", link: "claude.ai/code" },
  { value: "codex", label: "Codex", link: "github.com/openai/codex" },
  { value: "openai-compatible", label: "Локальная (LM Studio / Ollama)" },
];
const LOCAL = "openai-compatible";
/** Ключи `llm`, по которым идёт проверка и выбор «Авто». */
const LLM_KEYS = ["provider", "base_url", "local_model", "proxy"];

const PROXY_LABEL = "Прокси для подключения к моделям";
const PROXY_HELP = "Claude Code и Codex сами не используют системный прокси Windows — приложение передаёт его им. "
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
  return (win !== undefined && windowInvalid(win))
    || (typeof proxy === "string" && proxyError(proxy) !== null);
}

/** Подпись «Авто», пока выбран конкретный провайдер: порядок выбора (llm.resolve). */
export const AUTO_ORDER = "первый готовый: Claude Code → Codex → локальная";

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
  const llmDirty = LLM_KEYS.some((k) => JSON.stringify(draft.llm?.[k] ?? null) !== JSON.stringify(saved.llm?.[k] ?? null));
  const win = draft.assist?.window_seconds as number | null | undefined;
  const proxy = String(llm("proxy") ?? "system");
  const proxyMode = proxy === "system" || proxy === "none" ? proxy : "custom";
  const savedProxy = String(saved.llm?.proxy ?? "system");
  // Вернуться к своему адресу после «Без прокси» — с прежним текстом в поле.
  const [customProxy, setCustomProxy] = useState<string | null>(null);
  const customStart = customProxy ?? (savedProxy === "system" || savedProxy === "none" ? "" : savedProxy);
  const proxyProblem = proxyMode === "custom" ? proxyError(proxy) : null;

  return (
    <>
      <Row label="Провайдер модели" hint="Готовит итоги, отвечает на вопросы и ведёт живой обзор встречи"
        help={<ProviderTip />} stack>
        <div role="radiogroup" aria-label="Провайдер модели" className="providers">
          {PROVIDERS.map((p) => {
            const line = status(p, info);
            const missing = p.link && info?.available[p.value]?.found === false;
            const c = checks[p.value];
            return (
              <div key={p.value} role="group" aria-label={p.label} className="provider">
                <label className="radios__item">
                  <input type="radio" name="llm-provider" checked={chosen === p.value}
                    onChange={() => set("llm", "provider", p.value)} />
                  {p.label}
                </label>
                <div className="provider__status">
                  {missing ? (
                    <span className="muted">не найден — установите{" "}
                      <button type="button" className="provider__link"
                        onClick={() => void openUrl(`https://${p.link}`)}>
                        {p.link}
                      </button>
                    </span>
                  ) : line ? <span className="muted">{line}</span> : null}
                </div>
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
      {chosen === LOCAL && (
        <>
          <p className="muted sdesc">Локальная модель не использует базу знаний.</p>
          <Row label="Адрес сервера" htmlFor="llm-base-url" hint="OpenAI-совместимый адрес LM Studio или Ollama">
            <input id="llm-base-url" type="text" value={String(llm("base_url") ?? "")}
              onChange={(e) => set("llm", "base_url", e.target.value)} />
          </Row>
          <Row label="Имя модели" htmlFor="llm-local-model" hint="Как модель называется в LM Studio или Ollama">
            <input id="llm-local-model" type="text" value={String(llm("local_model") ?? "")}
              onChange={(e) => set("llm", "local_model", e.target.value.trim() ? e.target.value : null)} />
          </Row>
        </>
      )}
      <Row label={PROXY_LABEL} hint="Через него Claude Code и Codex подключаются к своим сервисам"
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
    </>
  );
}
