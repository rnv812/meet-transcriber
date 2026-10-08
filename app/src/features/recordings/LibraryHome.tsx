/**
 * Ничего не выбрано, а записи есть (App: вместо прежней «Выберите запись»):
 * сверху слева «Записи» и счётчик, карточка «Новая встреча» на тихом сиянии
 * (`card aurora-wash`) с «Начать запись» и «Импортировать файл», под ней —
 * «Последняя»: последняя встреча (название, дата и длительность, состояние),
 * нажатие открывает её.
 *
 * Полного сияния (`.aurora`) здесь нет: оно — только у «Записей пока нет»
 * (LibraryEmpty). Брошенный в окно файл принимает зона над списком, страница
 * его не ловит (иначе импорт ушёл бы дважды).
 */

import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";

import { type Endpoint, recordingCommand } from "../../lib/api";
import { dayLabel, errorText, plural } from "../../lib/format";
import { statusOf } from "../../lib/status";
import type { Job, LibraryItem, Snapshot } from "../../lib/types";
import { BADGE_CLASS } from "../../ui/badge";
import { Button } from "../../ui/Button";
import { Icon } from "../../ui/Icon";
import { ImportErrors, useImportFiles } from "./ImportZone";
import { autoRecordText } from "./LibraryEmpty";
import { badgeOf, listDuration } from "./RecordingItem";
import "./library-home.css";

/** Последняя встреча: самая поздняя по началу; без дат — первая в списке; пусто — null. */
export function latestOf(items: readonly LibraryItem[]): LibraryItem | null {
  let best: LibraryItem | null = null;
  for (const it of items) {
    if (!best) { best = it; continue; }
    if (it.started_at && (!best.started_at || it.started_at > best.started_at)) best = it;
  }
  return best;
}

export function LibraryHome({ endpoint, snapshot, items, jobs, onOpen, onSnapshot, onImported }: {
  endpoint: Endpoint | null;
  snapshot: Snapshot | null;
  /** Записи списка (в его области и с его поиском): счётчик и «Последняя». */
  items: LibraryItem[];
  jobs: Job[];
  onOpen: (id: string) => void;
  /** Ответ команды записи — новый снимок резидента (не ждать опроса). */
  onSnapshot?: (s: Snapshot) => void;
  onImported?: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const files = useImportFiles(endpoint, onImported, { dragDrop: false });
  const startId = useId();
  const lastId = useId();
  const recording = snapshot?.status === "recording" || !!snapshot?.live?.active || !!snapshot?.live?.starting;
  const auto = snapshot?.auto_record;
  const latest = latestOf(items);

  const start = async () => {
    if (!endpoint) return;
    setError(null);
    try {
      onSnapshot?.(await recordingCommand(endpoint, "start"));
    } catch (e) {
      setError(errorText(e));
    }
  };

  let last = null;
  if (latest) {
    const when = latest.started_at ? dayLabel(latest.started_at) : "";
    const meta = [when, listDuration(latest.duration_s)].filter(Boolean).join(" · ");
    const status = statusOf(latest, jobs, snapshot);
    const badge = badgeOf(status);
    last = (
      <section className="lib-home__last" aria-labelledby={lastId}>
        <h3 className="lib-home__label" id={lastId}>Последняя</h3>
        <button type="button" className="card lib-home__rec" onClick={() => onOpen(latest.id)}>
          <span className="lib-home__rec-text">
            <span className="lib-home__rec-title">{latest.title ?? (when || latest.id)}</span>
            {meta && <span className="lib-home__rec-meta num">{meta}</span>}
          </span>
          {badge ? <span className={BADGE_CLASS[badge.tone || "plain"]}>{badge.text}</span>
            : <span className={BADGE_CLASS.ok}>Готово</span>}
          <Icon as={ChevronRight} className="lib-home__rec-go" />
        </button>
      </section>
    );
  }

  return (
    <div className="lib-home">
      <div className="lib-home__head">
        <h2 className="lib-home__title">Записи</h2>
        <span className="lib-home__count num">
          {items.length} {plural(items.length, "запись", "записи", "записей")}
        </span>
      </div>
      <section className="card aurora-wash lib-home__start" aria-labelledby={startId}>
        <div className="lib-home__start-text">
          <h3 className="lib-home__start-title" id={startId}>Новая встреча</h3>
          <p className="lib-home__start-lead">
            {auto?.enabled
              ? "Meet начнёт запись сам, когда начнётся звонок. Можно начать и вручную или импортировать готовый файл."
              : "Начните запись вручную или импортируйте готовый файл — аудио или видео."}
          </p>
        </div>
        <div className="lib-home__actions">
          <Button variant="primary" size="md" disabled={!endpoint || recording} onClick={start}>
            <i className="lib-home__dot" aria-hidden="true" />Начать запись
          </Button>
          <Button size="md" disabled={!endpoint} onClick={files.choose}>Импортировать файл</Button>
        </div>
        {auto && (
          <span className="lib-home__auto">
            <i className={`lib-home__auto-dot${auto.enabled ? " lib-home__auto-dot--on" : ""}`} aria-hidden="true" />
            {autoRecordText(auto)}
          </span>
        )}
        {error && <p className="lib-home__error" role="alert">{error}</p>}
        <ImportErrors errors={files.errors} onClose={files.clearErrors} />
      </section>
      {last}
    </div>
  );
}
