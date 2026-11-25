/**
 * «Папка моделей» (блок «Модели распознавания»): где лежат модели Meet и
 * перенос одних моделей (свой кэш Hugging Face, GigaAM, Xet) в любую папку на
 * любом диске — отдельно от движка — и обратно, в папку по умолчанию рядом с
 * движком.
 *
 * Переносит оболочка (`models_storage_move`, `storage/models_move.rs`):
 * копирует и сверяет модели, перезапускает службу записи с новой папкой и
 * только потом удаляет прежние. Ход и итог — в общем состоянии переносов
 * (`storageMove.ts`, `what: "models"`), «Отменить» — та же команда, что у
 * переноса движка. Вне приложения (окно в браузере) — только путь.
 */

import { useCallback, useEffect, useState } from "react";
import { errorText } from "../../lib/format";
import {
  type StorageCheck, type StorageStatus, inTauri, modelsStorageAbandon, modelsStorageCheck, modelsStorageReset,
  pickFolder, storageStatus,
} from "../../lib/shell";
import { Button } from "../../ui/Button";
import { gb } from "../wizard/gate";
import { PathText, Row } from "./Section";
import { MoveProgress } from "./StoragePane";
import { attachMove, bumpStorage, clearMove, moveState, startModelsMove, useMove, useStorageTick } from "./storageMove";

const norm = (path: string) => path.replace(/[\\/]+/g, "/").replace(/\/$/, "").toLowerCase();

/** Проверенный перенос: куда (null — обратно к движку) и что сказала оболочка. */
type Plan = { to: string | null; check: StorageCheck };

function PlanBox({ plan, onStart, onCancel }: { plan: Plan; onStart: () => void; onCancel: () => void }) {
  const { check } = plan;
  const blocked = Boolean(check.busy || check.error);
  return (
    <div className="storage__plan" role="group" aria-label="Перенос моделей">
      <span>
        {plan.to === null ? "Вернуть модели в папку по умолчанию" : "Перенести модели в"}{" "}
        <PathText path={check.target} />
      </span>
      <span>
        Нужно около {gb(check.needs_gb)} ГБ{check.free_gb !== null && <>, свободно {gb(check.free_gb)} ГБ</>}.
      </span>
      {check.busy && <p className="error">Перенести сейчас нельзя: {check.busy}. Повторите, когда закончится.</p>}
      {check.error && <p className="error">{check.error}</p>}
      <p className="muted">
        {check.resume ? "Продолжение прерванного переноса: скопированное уже на месте. " : ""}
        Модели Meet (Whisper, разделение на спикеров, GigaAM) скопируются и проверятся, служба записи
        перезапустится с новой папкой, и только потом прежние копии удалятся; при сбое модели останутся на
        прежнем месте, а скопированное — для «Продолжить». Движок не переносится. Модели других программ из
        общего кэша Hugging Face Meet не трогает.
      </p>
      <span className="storage__actions">
        <Button variant="primary" disabled={blocked} onClick={onStart}>
          {check.resume ? "Продолжить перенос" : "Перенести"}
        </Button>
        <Button onClick={onCancel}>Отмена</Button>
      </span>
    </div>
  );
}

export function ModelsFolder({ cache }: {
  /** Кэш Hugging Face, которым пользуется Meet (из `GET /models`). */
  cache: string;
}) {
  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const move = useMove();
  const tick = useStorageTick();

  const load = useCallback(async () => {
    const next = await storageStatus().catch(() => null);
    setStatus(next);
    // Перенос моделей начат в закрытом с тех пор окне — подключиться к его ходу.
    if (next?.moving && next.moving_kind === "models" && moveState().kind === "idle") {
      void attachMove("models").then(() => void load());
    }
  }, []);

  useEffect(() => { void load(); }, [load, tick]);
  useEffect(() => { if (move.kind === "done" || move.kind === "failed") void load(); }, [move.kind, load]);
  // «Отменить» доступно до переключения — оболочка знает, когда именно.
  useEffect(() => {
    if (move.kind !== "running") return;
    const timer = setInterval(() => { storageStatus().then(setStatus).catch(() => {}); }, 1000);
    return () => clearInterval(timer);
  }, [move.kind]);

  const check = async (to: string | null) => {
    clearMove();
    setChecking(true);
    setError(null);
    setPlan(null);
    try {
      setPlan({ to, check: await modelsStorageCheck(to) });
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setChecking(false);
    }
  };

  const choose = async () => {
    const picked = await pickFolder(status?.models_dir ?? null).catch(() => null);
    if (picked) await check(picked);
  };

  const start = () => {
    if (!plan) return;
    // Оболочка уже разрешила подпапку («Meet Models»): переносим туда же.
    const to = plan.to === null ? null : plan.check.target;
    setPlan(null);
    void startModelsMove(to);
  };

  const act = async (action: () => Promise<void>) => {
    setError(null);
    try {
      await action();
      bumpStorage();
    } catch (cause) {
      setError(errorText(cause));
    }
    await load();
  };

  const app = inTauri() && status !== null;
  const running = move.kind === "running";
  const mine = move.kind !== "idle" && move.what === "models";
  const separate = status?.models_root ?? null;
  const unavailable = status?.models_missing ?? (status?.models_unreadable ? "" : null);
  const stopped = !running ? status?.models_interrupted ?? null : null;
  // Кэш HF вне папки моделей — общий кэш Hugging Face (модели по умолчанию без папки движка).
  const shared = !separate && status !== null && !norm(cache).startsWith(norm(status.models_dir ?? ""));
  return (
    <Row label="Папка моделей" stack
      hint={separate
        ? "Отдельная папка: модели Meet лежат здесь, движок — в своей папке"
        : "Скачанные модели не загружаются повторно; модели можно держать на другом диске"}>
      <div className="storage">
        <span className="folder"><PathText path={status?.models_dir ?? cache} /></span>
        {separate ? (
          <span className="srow__hint">Whisper, разделение на спикеров и GigaAM — в этой папке</span>
        ) : shared ? (
          <span className="srow__hint">
            По умолчанию — рядом с движком; модели Whisper и разделения на спикеров — в общем кэше Hugging Face{" "}
            <code className="path">{cache}</code>
          </span>
        ) : status ? (
          <span className="srow__hint">По умолчанию — рядом с движком</span>
        ) : null}
        {unavailable !== null && (
          <div className="storage__plan" role="group" aria-label="Папка моделей недоступна">
            <p className="error">
              {status?.models_unreadable
                ? "Файл выбора папки моделей повреждён — где модели, неизвестно."
                : <>Папка моделей недоступна: <PathText path={unavailable} /> — подключите диск.</>}{" "}
              Пока её нет, модели не скачиваются.
            </p>
            {app && (
              <span className="storage__actions">
                <Button onClick={() => void act(modelsStorageReset)}>Вернуть в папку по умолчанию</Button>
              </span>
            )}
            <p className="muted">«Вернуть в папку по умолчанию» не копирует модели: они скачаются заново рядом с движком.</p>
          </div>
        )}
        {stopped && (
          <div className="storage__plan" role="group" aria-label="Прерванный перенос моделей">
            <span>Перенос моделей в <PathText path={stopped.to_dir} /> прерван — модели работают из прежней папки,
              скопированное сохранено.</span>
            <span className="storage__actions">
              <Button variant="primary" onClick={() => { clearMove(); void startModelsMove(stopped.to); }}>
                Продолжить
              </Button>
              <Button onClick={() => void act(modelsStorageAbandon)}>Отменить перенос</Button>
            </span>
          </div>
        )}
        {app && !running && !plan && !stopped && unavailable === null && (
          <span className="storage__actions">
            <Button busy={checking} onClick={choose}>Перенести…</Button>
            {separate && (
              <Button busy={checking} onClick={() => void check(null)}>Вернуть в папку по умолчанию</Button>
            )}
          </span>
        )}
        {plan && <PlanBox plan={plan} onStart={start} onCancel={() => setPlan(null)} />}
        <MoveProgress cancellable={Boolean(status?.cancellable)} what="models" />
        {mine && move.kind === "done" && <p className="notice">Готово: модели — в {move.path}</p>}
        {mine && move.kind === "failed" && <p className="error">{move.error}</p>}
        {error && <p className="error">{error}</p>}
      </div>
    </Row>
  );
}
