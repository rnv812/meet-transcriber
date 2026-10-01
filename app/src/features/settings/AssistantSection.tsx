/**
 * Настройки «Ассистент»: кто отвечает (провайдер модели), откуда знания и куда
 * заметки, окно живой расшифровки.
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
import { pickFolder } from "../../lib/shell";
import type { AssistantInfo } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Row, type Raw, type SetFn } from "./Section";

type Provider = { value: string; label: string; link?: string };

const PROVIDERS: Provider[] = [
  { value: "auto", label: "Авто" },
  { value: "claude-code", label: "Claude Code", link: "claude.ai/code" },
  { value: "codex", label: "Codex", link: "github.com/openai/codex" },
  { value: "openai-compatible", label: "Локальная (LM Studio / Ollama)" },
];
const LOCAL = "openai-compatible";
/** Ключи `llm`, по которым идёт проверка и выбор «Авто». */
const LLM_KEYS = ["provider", "base_url", "local_model"];

/** Пока резидент проверяет вход в CLI (`checking`), спрашиваем снова через паузу. */
const RECHECK_MS = 1500;

export const SUBDIR_ERROR = "Только имя подпапки, без .. и полного пути";
export const WINDOW_MIN = 5;
export const WINDOW_MAX = 120;

/** Подпапка заметок — относительная и без `..` (резидент проверяет то же). */
export function subdirInvalid(value: string): boolean {
  const text = value.trim();
  if (!text) return false;
  if (/^[\\/]/.test(text) || /^[A-Za-z]:/.test(text)) return true;
  return text.split(/[\\/]+/).includes("..");
}

const windowInvalid = (value: unknown): boolean =>
  typeof value !== "number" || !Number.isFinite(value) || value < WINDOW_MIN || value > WINDOW_MAX;

/**
 * Правки раздела нельзя сохранить: подпапка или окно вне правил. Смотрим только
 * изменённое (`changes` — то, что уйдёт в PATCH): значение, уже лежащее в файле,
 * не должно запирать «Сохранить» для остальных разделов.
 */
export function assistantChangesInvalid(changes: Raw): boolean {
  const subdir = changes.assistant?.notes_subdir;
  const win = changes.assist?.window_seconds;
  return (typeof subdir === "string" && subdirInvalid(subdir)) || (win !== undefined && windowInvalid(win));
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

function FolderRow({ label, hint, value, onChange }: {
  label: string; hint: string; value: string | null; onChange: (v: string | null) => void;
}) {
  const choose = async () => {
    const path = await pickFolder(value).catch(() => null);
    if (path) onChange(path);
  };
  return (
    <div role="group" aria-label={label}>
      <Row label={label} hint={hint}>
        {value ? <code className="path">{value}</code> : <span className="muted">не задана</span>}
        <Button onClick={() => void choose()}>Выбрать папку…</Button>
        <Button onClick={() => onChange(null)} disabled={!value}>Очистить</Button>
      </Row>
    </div>
  );
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
    const load = () => {
      getAssistant(endpoint).then((data) => {
        if (!live) return;
        setInfo(data);
        setInfoError(null);
        if (data.checking) timer = setTimeout(load, RECHECK_MS);
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
  const subdir = String(draft.assistant?.notes_subdir ?? "");
  const win = draft.assist?.window_seconds as number | null | undefined;

  return (
    <>
      <Row label="Модель" hint="кто пишет итоги, отвечает на вопросы и ведёт живой дайджест">
        <div role="radiogroup" aria-label="Модель" className="providers">
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
                    <span className="muted">не найден — установите <span className="provider__link">{p.link}</span></span>
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
          <p className="muted sdesc">Локальная модель не читает базу знаний</p>
          <Row label="Адрес" htmlFor="llm-base-url" hint="OpenAI-совместимый адрес LM Studio или Ollama">
            <input id="llm-base-url" type="text" value={String(llm("base_url") ?? "")}
              onChange={(e) => set("llm", "base_url", e.target.value)} />
          </Row>
          <Row label="Модель" htmlFor="llm-local-model" hint="как её называет LM Studio или Ollama">
            <input id="llm-local-model" type="text" value={String(llm("local_model") ?? "")}
              onChange={(e) => set("llm", "local_model", e.target.value.trim() ? e.target.value : null)} />
          </Row>
        </>
      )}
      <h3 className="shead">Знания и заметки</h3>
      <FolderRow label="База знаний" hint="папка с материалами: ассистент читает её, отвечая на вопросы"
        value={(draft.assistant?.knowledge_dir as string | null | undefined) ?? null}
        onChange={(v) => set("assistant", "knowledge_dir", v)} />
      <FolderRow label="Папка заметок" hint="сюда «В заметки» кладёт итоги встречи"
        value={(draft.assistant?.notes_dir as string | null | undefined) ?? null}
        onChange={(v) => set("assistant", "notes_dir", v)} />
      <Row label="Подпапка для встреч" htmlFor="notes-subdir" hint="внутри папки заметок; пусто — прямо в ней">
        <input id="notes-subdir" type="text" value={subdir}
          onChange={(e) => set("assistant", "notes_subdir", e.target.value)} />
        {subdirInvalid(subdir) && <span className="error">{SUBDIR_ERROR}</span>}
      </Row>
      <h3 className="shead">Живой ассистент</h3>
      <Row label="Окно живой расшифровки, с" htmlFor="assist-window"
        hint="раз в столько секунд расшифровывается свежий звук: меньше — строки быстрее, больше — точнее. Применится со следующего запуска">
        <input id="assist-window" type="number" className="num" min={WINDOW_MIN} max={WINDOW_MAX} step={5}
          value={win ?? ""}
          onChange={(e) => {
            const n = e.target.value === "" ? null : Number(e.target.value);
            set("assist", "window_seconds", n !== null && Number.isFinite(n) ? n : null);
          }} />
        {win !== undefined && windowInvalid(win) && <span className="error">от {WINDOW_MIN} до {WINDOW_MAX} секунд</span>}
      </Row>
    </>
  );
}
