/**
 * Локальная модель в «Ассистенте»: адрес OpenAI-совместимого сервера и имя
 * модели. Модели сервера ищет резидент (`POST /assistant/local-models`): при
 * открытии, после правки адреса (с паузой на ввод) и по «Найти модели» — по
 * адресу из черновика, ещё не сохранённому. Найденные — список, из него
 * заполняется поле имени; вписать имя руками можно всегда. Одна модель на
 * сервере и имя не задано — она и выбирается (сохранить — как любую правку).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, type Endpoint, listLocalModels } from "../../lib/api";
import { errorText } from "../../lib/format";
import type { LocalModel, LocalModels } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Row, type SetFn } from "./Section";

/** Пауза после правки адреса перед поиском: не спрашивать сервер на каждую букву. */
export const FIND_DEBOUNCE_MS = 600;

/** Имя для сравнения: у Ollama `qwen3` — это `qwen3:latest` (как `meet.llm.local_models.model_key`). */
export function modelKey(name: string): string {
  const text = name.trim();
  if (!text) return "";
  const tail = text.slice(text.lastIndexOf("/") + 1);
  return tail.includes(":") ? text : `${text}:latest`;
}

export const sameModel = (a: string, b: string) => !!modelKey(a) && modelKey(a) === modelKey(b);

/** «4,9 ГБ», «700 МБ». */
export function sizeText(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1).replace(".", ",")} ГБ`;
  return `${Math.round(bytes / 1024 ** 2)} МБ`;
}

/** Строка списка: имя и что известно о модели — размер, параметры, контекст. */
export function modelLabel(m: LocalModel): string {
  const parts: string[] = [];
  if (m.size) parts.push(sizeText(m.size));
  if (m.params) parts.push(m.params);
  if (m.context) parts.push(`контекст ${Math.round(m.context / 1024)}K`);
  return parts.length ? `${m.id} — ${parts.join(" · ")}` : m.id;
}

type Found = { busy: boolean; result?: LocalModels; error?: string };

export function LocalModelRows({ baseUrl, model, set, endpoint, viaProxy = false }: {
  baseUrl: string; model: string; set: SetFn; endpoint: Endpoint;
  /** «Локальную модель — через прокси» (`llm.local_via_proxy`), из черновика. */
  viaProxy?: boolean;
}) {
  const [found, setFound] = useState<Found>({ busy: false });
  const ask = useRef(0);
  // Имя модели на момент ответа: выбрать единственную, только если поле пустое.
  const modelNow = useRef(model);
  modelNow.current = model;
  // `set` окна настроек — новая функция на каждый рендер: поиск от неё не зависит.
  const setNow = useRef(set);
  setNow.current = set;
  const viaNow = useRef(viaProxy);
  viaNow.current = viaProxy;

  const find = useCallback(async (url: string) => {
    const n = ++ask.current;
    setFound((cur) => ({ ...cur, busy: true }));
    try {
      const result = await listLocalModels(endpoint, url, modelNow.current.trim() || null, viaNow.current);
      if (ask.current !== n) return;
      setFound({ busy: false, result });
      if (result.ok && result.models.length === 1 && !modelNow.current.trim()) {
        setNow.current("llm", "local_model", result.models[0]!.id);
      }
    } catch (e) {
      if (ask.current !== n) return;
      // Резидент прежней версии поиска не умеет — просто без списка.
      setFound(e instanceof ApiError && e.status === 404 ? { busy: false } : { busy: false, error: errorText(e) });
    }
  }, [endpoint]);

  // При открытии — сразу, после правки адреса — с паузой.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      void find(baseUrl);
      return;
    }
    const timer = setTimeout(() => void find(baseUrl), FIND_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [baseUrl, viaProxy, find]);
  useEffect(() => () => { ask.current += 1; }, []);

  const result = found.result;
  const models = result?.ok ? result.models : [];
  const name = model.trim();
  // У Ollama «qwen3» и «qwen3:latest» — одна модель: в списке выбрана она.
  const picked = models.find((m) => sameModel(m.id, name));
  const listed = !!picked;
  const missing = !!name && models.length > 0 && !listed;

  return (
    <>
      <Row label="Адрес сервера" htmlFor="llm-base-url" hint="OpenAI-совместимый адрес LM Studio, Ollama или vLLM">
        <div className="local-find">
          <input id="llm-base-url" type="text" spellCheck={false} value={baseUrl}
            onChange={(e) => set("llm", "base_url", e.target.value)} />
          <Button onClick={() => void find(baseUrl)} disabled={found.busy}>Найти модели</Button>
        </div>
        <span className="local-find__status" role="status">
          {found.busy ? <span className="muted">Ищу модели…</span>
            : found.error ? <span className="error">Не удалось спросить сервер: {found.error}</span>
              : result && !result.ok ? <span className="error">{result.error}</span>
                : result ? <span className="muted">Найдено моделей: {models.length}</span> : null}
        </span>
      </Row>
      <Row label="Имя модели" htmlFor="llm-local-model" hint="Выберите из найденных на сервере или впишите имя">
        {models.length > 0 && (
          <select aria-label="Модели на сервере" value={picked?.id ?? ""}
            onChange={(e) => { if (e.target.value) set("llm", "local_model", e.target.value); }}>
            <option value="" disabled>Выберите модель…</option>
            {models.map((m) => <option key={m.id} value={m.id}>{modelLabel(m)}</option>)}
          </select>
        )}
        <input id="llm-local-model" type="text" spellCheck={false} placeholder="например qwen3:8b" value={model}
          onChange={(e) => set("llm", "local_model", e.target.value.trim() ? e.target.value : null)} />
        {missing && <span className="error">Модели «{name}» на сервере нет — выберите другую из списка</span>}
      </Row>
    </>
  );
}

/**
 * «Локальную модель — через прокси» (`llm.local_via_proxy`) — «Тонкая настройка»
 * раздела «Модели ИИ». Без включённой локальной модели — недоступно, с причиной.
 */
export function LocalViaProxyRow({ value, set, disabled = false }: { value: boolean; set: SetFn; disabled?: boolean }) {
  return (
    <Row label="Локальную модель — через прокси" htmlFor="llm-local-via-proxy" disabled={disabled}
      hint={disabled ? "Включите локальную модель в «Провайдерах», чтобы выбрать"
        : "Обычно сервер модели в своей сети и прокси не нужен. Включите, если он доступен только через прокси"}>
      <input id="llm-local-via-proxy" type="checkbox" checked={value} disabled={disabled}
        onChange={(e) => set("llm", "local_via_proxy", e.target.checked)} />
    </Row>
  );
}
