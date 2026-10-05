/**
 * Выбранной папки движка и моделей нет (внешний диск отключён, папку
 * переименовали): служба записи не запускается, ничего не ставится и не
 * качается заново молча. Человек решает: подключить диск и «Повторить» — или
 * «Вернуть на системный диск» (движок установится заново через мастер).
 */

import { useEffect, useState } from "react";
import { errorText } from "../../lib/format";
import { type StorageStatus, storageReset, storageRetry, storageStatus } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";

export const STORAGE_MISSING_TITLE = "Папка движка и моделей недоступна";

export function StorageMissing() {
  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retried, setRetried] = useState(false);

  useEffect(() => {
    storageStatus().then(setStatus).catch(() => {});
  }, []);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  const where = status?.missing ?? status?.root ?? "Выбранная папка";
  return (
    <div className="empty storage-missing">
      <div className="empty__title">{STORAGE_MISSING_TITLE}</div>
      <div>
        {where} не найдена — внешний диск отключён или папку переименовали. Без неё служба записи не
        запускается. Подключите диск и нажмите «Повторить».
      </div>
      {retried && !error && <div className="muted">Проверяю папку…</div>}
      {error && <p className="error">{error}</p>}
      <span className="storage__actions">
        <Button variant="primary" busy={busy} onClick={() => run(async () => { await storageRetry(); setRetried(true); })}>
          Повторить
        </Button>
        <Button disabled={busy} onClick={() => setConfirming(true)}>Вернуть на системный диск</Button>
      </span>
      {confirming && (
        <ConfirmDialog
          title="Вернуть на системный диск?"
          message={"Движок и модели снова будут храниться на системном диске. Движок придётся установить заново "
            + "(несколько гигабайт загрузки), модели, которых там нет, скачаются, когда понадобятся. "
            + "Папка на внешнем диске останется как есть — её можно удалить вручную."}
          confirmLabel="Вернуть"
          danger={false}
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={() => run(async () => { await storageReset(); setConfirming(false); })}
        />
      )}
    </div>
  );
}
