/**
 * Токен Hugging Face в настройках: где он лежит и пускает ли HF к модели
 * спикеров (`/hf/status`). Значение токена окно не видит и не показывает:
 * сменить — новым токеном с проверкой, удалить — из хранилища.
 */

import { useCallback, useEffect, useState } from "react";
import {
  type Endpoint, type HfStatus, deleteHfToken, getHfStatus, recheckHf,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { OS_TEXT } from "../../lib/platform";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { CheckFailure, HfTokenForm } from "../hf/HfTokenForm";
import { DiarizationTip, HfTokenTip } from "./tips";

const SOURCE: Record<string, string> = {
  keyring: `сохранён в ${OS_TEXT.keyring}`,
  config: "сохранён в файле настроек",
  env: "из переменной среды HF_TOKEN",
};

/** Где лежит токен — строкой подсказки. */
function SourceLine({ status }: { status: HfStatus }) {
  if (!status.configured) return <span className="srow__hint">Не задан — расшифровка без разделения на спикеров</span>;
  return <span className="srow__hint">Токен {SOURCE[status.source ?? ""] ?? "задан"}</span>;
}

/** Пускает ли HF к модели спикеров — бейджем рядом с кнопками. */
function CheckBadge({ status }: { status: HfStatus }) {
  if (!status.configured) return <span className="badge badge--stale">не задан</span>;
  if (status.check === null) return <span className="badge badge--stale">не проверен</span>;
  return status.check.ok ? <span className="badge badge--fresh">доступ есть</span>
    : <span className="badge badge--error">нет доступа</span>;
}

export function HfTokenRow({ endpoint, onChanged }: {
  endpoint: Endpoint;
  /** Токен сменился или удалён (каталог моделей перечитывается при открытии «Движка и моделей»). */
  onChanged?: () => void;
}) {
  const [status, setStatus] = useState<HfStatus | null>(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await getHfStatus(endpoint));
      setError(null);
    } catch (cause) {
      setError(`Состояние токена неизвестно: ${errorText(cause)}`);
    }
  }, [endpoint]);

  useEffect(() => { void load(); }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  const [askRemove, setAskRemove] = useState(false);
  const remove = () => act(async () => {
    setStatus(await deleteHfToken(endpoint));
    setEditing(false);
    onChanged?.();
  });
  const recheck = () => act(async () => {
    await recheckHf(endpoint);
    await load();
  });

  const configured = status?.configured === true;
  const failed = status?.check && !status.check.ok ? status.check : null;
  return (
    <div role="group" aria-label="Токен Hugging Face" className="hf-row">
      {/* Подпись и пояснение сверху, состояние и действия — одной строкой под ними. */}
      <div className="srow srow--stack">
        <div className="srow__text">
          <span className="srow__head"><span className="srow__label">Токен Hugging Face</span><HfTokenTip /></span>
          <span className="srow__hint">
            Нужен для разделения на спикеров <DiarizationTip /> — модель доступна после принятия условий на huggingface.co
          </span>
          {status && <SourceLine status={status} />}
        </div>
        <div className="srow__control">
          {status && <CheckBadge status={status} />}
          <Button onClick={() => setEditing((v) => !v)} aria-expanded={editing} disabled={!status}>
            {configured ? "Изменить токен" : "Задать токен"}
          </Button>
          {configured && <Button onClick={() => void recheck()} disabled={busy}>Проверить снова</Button>}
          {configured && status?.source !== "env" && (
            <Button variant="danger" onClick={() => setAskRemove(true)} disabled={busy}>Удалить токен…</Button>
          )}
        </div>
      </div>
      {askRemove && (
        <ConfirmDialog title="Удалить токен Hugging Face?" confirmLabel="Удалить"
          message="Без токена новые встречи будут расшифровываться без разделения на спикеров. Токен можно будет задать снова."
          onCancel={() => setAskRemove(false)} onConfirm={() => { setAskRemove(false); void remove(); }} />
      )}
      {failed && !editing && <CheckFailure check={failed} />}
      {error && <p className="error">{error}</p>}
      {editing && (
        <HfTokenForm endpoint={endpoint} label="Новый токен" submitLabel="Проверить и сохранить"
          onSaved={() => { setEditing(false); void load(); onChanged?.(); }} />
      )}
    </div>
  );
}
