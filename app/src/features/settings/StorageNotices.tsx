/**
 * Вопросы переноса движка и моделей на уровне всего окна, а не только в
 * разделе настроек: посреди переноса резидент перезапускается, и раздел
 * настроек пересобирается — человек оказывается в другом месте.
 *
 * - Итог переноса: «перенесены в …».
 * - Остатки в общем кэше Hugging Face: удалить ли модели Meet оттуда
 *   (главная кнопка — «Удалить»; фокус и Esc — на «Позже»).
 * - Прерванный перенос (сбой, выключение): «Продолжить» / «Отменить перенос».
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type StorageInfo, answerLeftovers, getStorage } from "../../lib/api";
import { errorText } from "../../lib/format";
import { type StorageStatus, storageAbandon, storageStatus } from "../../lib/shell";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { gb } from "../wizard/gate";
import { startMove, useMove } from "./storageMove";

const GB = 1024 ** 3;

export function StorageNotices({ endpoint, onOpenEngine }: {
  endpoint: Endpoint;
  /** Открыть «Движок и модели» (там ход переноса). */
  onOpenEngine: () => void;
}) {
  const move = useMove();
  const [info, setInfo] = useState<StorageInfo | null>(null);
  const [status, setStatus] = useState<StorageStatus | null>(null);
  /** «Позже»: в этом окне больше не спрашивать. */
  const [later, setLater] = useState<{ leftovers?: boolean; interrupted?: string; done?: string }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [nextInfo, nextStatus] = await Promise.all([
      getStorage(endpoint).catch(() => null),
      storageStatus().catch(() => null),
    ]);
    setInfo(nextInfo);
    setStatus(nextStatus);
  }, [endpoint]);

  useEffect(() => { void load(); }, [load, move.kind]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  };

  if (move.kind === "running") return null;
  const donePath = move.kind === "done" ? move.path : null;
  const left = info?.leftovers;

  if (left && !later.leftovers) {
    return (
      <ConfirmDialog
        title={donePath ? "Движок и модели перенесены" : "Модели Meet в общем кэше"}
        message={<>
          {donePath && <p>Теперь они в {donePath}.</p>}
          <p>
            Модели Meet остались и в общем кэше Hugging Face {left.cache}: {left.repos.length} шт.,{" "}
            {gb(left.bytes / GB)} ГБ. Их копии уже в новой папке — удалить их из общего кэша?
          </p>
          <p className="muted">
            Этим кэшем могут пользоваться другие программы: если им нужны те же модели, они скачают их
            заново. Модели других программ Meet не трогает.
          </p>
          {error && <p className="error">{error}</p>}
        </>}
        confirmLabel="Удалить из общего кэша"
        danger={false}
        cancelLabel="Позже"
        alt={{ label: "Оставить", onClick: () => {
          setLater((cur) => ({ ...cur, done: donePath ?? cur.done }));
          return act(() => answerLeftovers(endpoint, false));
        } }}
        busy={busy}
        onConfirm={() => act(async () => {
          setLater((cur) => ({ ...cur, done: donePath ?? cur.done }));
          const result = await answerLeftovers(endpoint, true);
          if (!result.ok) throw new Error(result.error ?? "Не удалось удалить");
        })}
        onCancel={() => setLater((cur) => ({ ...cur, leftovers: true, done: donePath ?? cur.done }))}
      />
    );
  }

  if (donePath && later.done !== donePath) {
    return (
      <ConfirmDialog
        title="Движок и модели перенесены"
        message={<p>Теперь они в {donePath}. Прежние удалены.</p>}
        confirmLabel="Показать в настройках"
        danger={false}
        cancelLabel="Закрыть"
        onConfirm={() => { setLater((cur) => ({ ...cur, done: donePath })); onOpenEngine(); }}
        onCancel={() => setLater((cur) => ({ ...cur, done: donePath }))}
      />
    );
  }

  const stopped = status?.interrupted;
  if (stopped && later.interrupted !== stopped) {
    return (
      <ConfirmDialog
        title="Перенос движка и моделей прерван"
        message={<>
          {move.kind === "failed" && <p className="error">{move.error}</p>}
          <p>
            Перенос в {stopped} не закончен — движок и модели работают из прежней папки. Скопированные
            модели сохранены: «Продолжить» докопирует недостающее и доустановит движок.
          </p>
          {error && <p className="error">{error}</p>}
        </>}
        confirmLabel="Продолжить"
        danger={false}
        cancelLabel="Позже"
        alt={{ label: "Отменить перенос", onClick: () => act(() => storageAbandon()) }}
        busy={busy}
        onConfirm={() => {
          setLater((cur) => ({ ...cur, interrupted: stopped }));
          onOpenEngine();
          void startMove(stopped);
        }}
        onCancel={() => setLater((cur) => ({ ...cur, interrupted: stopped }))}
      />
    );
  }
  return null;
}
