/**
 * «Где хранить движок и модели» (раздел «Движок и модели»). Переносит оболочка
 * (`storage_move`): ставит движок в новую папку заново, копирует и сверяет
 * модели, перезапускает службу записи из новой папки и только потом удаляет
 * прежнее. Записи не переносятся — у них своя папка в «Записи».
 *
 * После переезда из общего кэша Hugging Face — вопрос, удалить ли оттуда
 * модели Meet (по умолчанию — да; кэш могут делить другие программы).
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type StorageInfo, answerLeftovers, getStorage } from "../../lib/api";
import { errorText } from "../../lib/format";
import {
  type StorageCheck, type StorageStatus, inTauri, pickFolder, storageAbandon, storageCancel, storageCheck,
  storageStatus,
} from "../../lib/shell";
import { Button } from "../../ui/Button";
import { ProgressBar } from "../../ui/ProgressBar";
import { gb } from "../wizard/gate";
import { PathText, Row } from "./Section";
import { attachMove, bumpStorage, clearMove, moveState, startMove, useMove, useStorageTick } from "./storageMove";

const GB = 1024 ** 3;

const PHASES: Record<string, string> = {
  engine: "Установка движка в новую папку",
  models: "Копирование моделей",
  switching: "Перезапуск службы записи из новой папки",
  cleanup: "Удаление движка и моделей из прежней папки",
  rollback: "Возвращаю всё на прежнее место",
};

function MoveProgress({ cancellable }: { cancellable: boolean }) {
  const move = useMove();
  if (move.kind !== "running") return null;
  const { progress, engine } = move;
  const phase = progress?.phase ?? "engine";
  const label = phase === "waiting" ? progress?.text ?? "" : PHASES[phase] ?? progress?.text ?? "Перенос";
  let value: number | null = null;
  let detail: string | null = null;
  if (phase === "models" && progress && progress.total > 0) {
    value = progress.done / progress.total;
    detail = `${gb(progress.done / GB)} из ${gb(progress.total / GB)} ГБ`;
  } else if (phase === "engine" && engine) {
    value = (engine.step - 1) / engine.of;
    detail = `шаг ${engine.step} из ${engine.of}`;
  }
  return (
    <div className="storage__progress">
      <ProgressBar value={value} stageKey={phase} label={label} detail={detail} working={phase === "engine"} />
      {phase === "engine" && engine?.line && <span className="muted storage__line">{engine.line}</span>}
      {cancellable && (phase === "engine" || phase === "models" || phase === "waiting") && (
        <Button onClick={() => void storageCancel().catch(() => {})}>Отменить</Button>
      )}
    </div>
  );
}

function PlanBox({ plan, onStart, onCancel }: { plan: StorageCheck; onStart: () => void; onCancel: () => void }) {
  const blocked = Boolean(plan.busy || plan.error);
  return (
    <div className="storage__plan" role="group" aria-label="Перенос">
      <span>Перенести в <PathText path={plan.target} /></span>
      <span>
        Нужно около {gb(plan.needs_gb)} ГБ (движок — {gb(plan.engine_gb)} ГБ, модели — {gb(plan.models_gb)} ГБ)
        {plan.free_gb !== null && <>, свободно {gb(plan.free_gb)} ГБ</>}.
      </span>
      {plan.busy && <p className="error">Перенести сейчас нельзя: {plan.busy}. Повторите, когда закончится.</p>}
      {plan.error && <p className="error">{plan.error}</p>}
      <p className="muted">
        {plan.resume ? "Продолжение прерванного переноса: скопированное уже на месте. " : ""}
        Движок установится в новую папку заново: его пакеты (около 3 ГБ для видеокарты) скачаются один раз в
        эту папку — нужен интернет. Модели скопируются и проверятся. Прежние удалятся, только когда служба
        записи заработает из новой папки; при сбое всё останется на прежнем месте, а скопированное — для
        «Продолжить». Записи не переносятся — их папка задаётся в «Записи». Модели не из каталога (заданные
        вручную) скачаются заново при первой расшифровке.
      </p>
      <span className="storage__actions">
        <Button variant="primary" disabled={blocked} onClick={onStart}>
          {plan.resume ? "Продолжить перенос" : "Перенести"}
        </Button>
        <Button onClick={onCancel}>Отмена</Button>
      </span>
    </div>
  );
}

function Leftovers({ endpoint, info, onAnswered }: {
  endpoint: Endpoint; info: StorageInfo; onAnswered: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const left = info.leftovers;
  if (!left) return null;
  const answer = async (remove: boolean) => {
    setError(null);
    try {
      const result = await answerLeftovers(endpoint, remove);
      if (!result.ok) setError(result.error ?? "Не удалось удалить");
      onAnswered();
      bumpStorage();
    } catch (cause) {
      setError(errorText(cause));
    }
  };
  return (
    <div className="storage__leftovers" role="group" aria-label="Модели в общем кэше">
      <p>
        Модели Meet остались и в общем кэше Hugging Face <PathText path={left.cache} />:{" "}
        {left.repos.length} шт., {gb(left.bytes / GB)} ГБ. Их копии уже в новой папке — удалить их из общего кэша?
      </p>
      <p className="muted">
        Этим кэшем могут пользоваться другие программы: если им нужны те же модели, они скачают их заново.
        Модели других программ Meet не трогает.
      </p>
      {error && <p className="error">{error}</p>}
      <span className="storage__actions">
        <Button variant="primary" onClick={() => answer(true)}>Удалить из общего кэша</Button>
        <Button onClick={() => answer(false)}>Оставить</Button>
      </span>
    </div>
  );
}

/** Папка — системный диск по умолчанию (в том числе выбранная явно папка данных). */
function onSystemDisk(info: StorageInfo, status: StorageStatus | null): boolean {
  if (!info.custom || !info.root) return true;
  const norm = (path: string) => path.replace(/[\\/]+$/, "").toLowerCase();
  return status !== null && norm(info.root) === norm(status.default_home);
}

export function StoragePane({ endpoint }: { endpoint: Endpoint }) {
  const [info, setInfo] = useState<StorageInfo | null>(null);
  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [plan, setPlan] = useState<StorageCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const move = useMove();
  const tick = useStorageTick();

  const load = useCallback(async () => {
    const [nextInfo, nextStatus] = await Promise.all([
      getStorage(endpoint).catch((cause) => {
        setError(`Не удалось узнать, где движок и модели: ${errorText(cause)}`);
        return null;
      }),
      storageStatus().catch(() => null),
    ]);
    if (nextInfo) setInfo(nextInfo);
    setStatus(nextStatus);
    // Перенос начат в закрытом с тех пор окне — подключиться к его ходу.
    if (nextStatus?.moving && moveState().kind === "idle") void attachMove().then(() => void load());
  }, [endpoint]);

  // И когда сведения поменялись в другом месте (ответ об остатках в окне).
  useEffect(() => { void load(); }, [load, tick]);
  // Итог переноса — перечитать, где теперь всё (и вопрос об остатках).
  useEffect(() => { if (move.kind === "done" || move.kind === "failed") void load(); }, [move.kind, load]);
  // «Отменить» доступно до переключения — оболочка знает, когда именно.
  useEffect(() => {
    if (move.kind !== "running") return;
    const timer = setInterval(() => { storageStatus().then(setStatus).catch(() => {}); }, 1000);
    return () => clearInterval(timer);
  }, [move.kind]);

  const check = async (target: string) => {
    clearMove();
    setChecking(true);
    setError(null);
    setPlan(null);
    try {
      setPlan(await storageCheck(target));
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setChecking(false);
    }
  };

  const choose = async () => {
    const picked = await pickFolder(info?.home ?? null).catch(() => null);
    if (picked) await check(picked);
  };

  const start = () => {
    if (!plan) return;
    const target = plan.target;
    setPlan(null);
    void startMove(target);
  };

  const abandon = async () => {
    setError(null);
    try {
      await storageAbandon();
    } catch (cause) {
      setError(errorText(cause));
    }
    await load();
  };

  if (!info) return error ? <p className="error">{error}</p> : null;
  const app = inTauri() && status !== null;
  const running = move.kind === "running";
  const system = onSystemDisk(info, status);
  const stopped = !running ? status?.interrupted ?? null : null;
  return (
    <>
      <Row label="Где хранить движок и модели" stack
        hint="Движок — несколько гигабайт, модели — ещё столько же: их можно держать на другом диске">
        <div className="storage">
          <span className="folder"><PathText path={info.home} /></span>
          {info.custom && !system ? (
            <span className="muted">Движок, модели Whisper, разделения на спикеров и GigaAM — в этой папке.</span>
          ) : info.custom ? (
            <span className="muted">Системный диск: движок и все модели Meet — в папке Meet.</span>
          ) : (
            <span className="muted">
              По умолчанию: движок и GigaAM — в папке Meet на системном диске, модели Whisper и разделения на
              спикеров — в общем кэше Hugging Face <PathText path={info.hf_cache} />.
            </span>
          )}
          {stopped && (
            <div className="storage__plan" role="group" aria-label="Прерванный перенос">
              <span>Перенос в <PathText path={stopped} /> прерван — всё работает из прежней папки, скопированное
                сохранено.</span>
              <span className="storage__actions">
                <Button variant="primary" onClick={() => { clearMove(); void startMove(stopped); }}>Продолжить</Button>
                <Button onClick={abandon}>Отменить перенос</Button>
              </span>
            </div>
          )}
          {status?.discarding && !running && (
            <span className="muted">Убираю недоделанное прошлой попытки — файлы были заняты, повторю позже.</span>
          )}
          {app && !running && !plan && !stopped && (
            <span className="storage__actions">
              <Button busy={checking} onClick={choose}>Выбрать папку…</Button>
              {!system && status && (
                <Button busy={checking} onClick={() => check(status.default_home)}>Вернуть на системный диск</Button>
              )}
            </span>
          )}
          {plan && <PlanBox plan={plan} onStart={start} onCancel={() => setPlan(null)} />}
          <MoveProgress cancellable={Boolean(status?.cancellable)} />
          {move.kind === "done" && <p className="tag tag--live">Готово: движок и модели — в {move.path}</p>}
          {move.kind === "failed" && <p className="error">{move.error}</p>}
          {error && <p className="error">{error}</p>}
        </div>
      </Row>
      <Leftovers endpoint={endpoint} info={info} onAnswered={() => void load()} />
    </>
  );
}
