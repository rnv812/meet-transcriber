/**
 * Поле токена Hugging Face с проверкой: резидент проверяет доступ к модели
 * спикеров и сохраняет токен, только если доступ есть. Токен не показывается
 * и после удачной проверки из поля стирается.
 */

import { useId, useState } from "react";
import { type Endpoint, type HfCheck, setHfToken } from "../../lib/api";
import { errorText } from "../../lib/format";
import { openUrl } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { HF_MODEL_URL } from "./links";
import "./hf.css";

export function checkText(check: HfCheck): string {
  switch (check.reason) {
    case "ok": return "Доступ есть";
    case "invalid_token": return "Неверный токен";
    case "terms_not_accepted": return "Условия модели не приняты — нажмите «Agree and access repository»";
    default: return check.message || "Нет связи с huggingface.co";
  }
}

/** Неудачная проверка; при непринятых условиях — кнопка страницы модели. */
export function CheckFailure({ check }: { check: HfCheck }) {
  return (
    <div role="alert" className="hf-result error">
      <span>{checkText(check)}</span>
      {check.reason === "terms_not_accepted" && (
        <Button onClick={() => void openUrl(HF_MODEL_URL)}>Открыть страницу модели</Button>
      )}
    </div>
  );
}

export function HfTokenForm({ endpoint, label, submitLabel, onSaved }: {
  endpoint: Endpoint;
  label: string;
  submitLabel: string;
  onSaved: (check: HfCheck) => void;
}) {
  const id = useId();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<HfCheck | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const value = token.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const check = await setHfToken(endpoint, value);
      setResult(check);
      if (check.ok) {
        setToken("");
        onSaved(check);
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="hf-form">
      <div className="hf-form__row">
        <label htmlFor={id} className="hf-form__label">{label}</label>
        <input id={id} type="password" autoComplete="off" spellCheck={false} placeholder="hf_…"
          value={token} onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void submit(); }} />
        <Button variant="primary" onClick={() => void submit()} disabled={busy || !token.trim()}>
          {busy ? "Проверяю…" : submitLabel}
        </Button>
      </div>
      {busy && <p className="muted hf-form__note">Проверка занимает до 15 секунд</p>}
      {result?.ok && <p className="notice hf-form__note">{checkText(result)}</p>}
      {result && !result.ok && <CheckFailure check={result} />}
      {error && <p role="alert" className="error hf-form__note">{error}</p>}
    </div>
  );
}
