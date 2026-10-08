import { useEffect, useState } from "react";
import { type Endpoint, backupBefore } from "../../lib/api";
import {
  UPDATE_CANCELLED, cancelUpdate, installUpdate, listReleases, onUpdateProgress, openUrl,
  type InstallResult, type ReleaseRow, type UpdateProgress,
} from "../../lib/shell";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { ProgressBar } from "../../ui/ProgressBar";
import { Row } from "./Section";

/**
 * «О программе» → «Другие версии» (0.5): выпуски на GitHub по кнопке, любой
 * можно поставить отсюда. Перед установкой более старой — предупреждение и
 * копия настроек, базы голосов и служебных файлов (`POST /backup` резидента,
 * папка `backups` в папке данных); записи не трогаются.
 */

/** Тот же вопрос оболочки, что у «Скачать и установить» (`updater::WORK_IN_PROGRESS`). */
const CONFIRM_WORK =
  "Идёт расшифровка — она будет прервана и продолжится после обновления. Обновить сейчас?";

/** Версия до 0.4 — прежнее оформление и формат части настроек: особое предупреждение. */
export function beforeAurora(version: string): boolean {
  const [major = 0, minor = 0] = version.replace(/^v/i, "").split(".").map(Number);
  return major === 0 && minor < 4;
}

/** Текст подтверждения отката на `version` с работающей `current`. */
export function downgradeText(version: string, current: string): string[] {
  const lines = [
    `Версия ${version} старее установленной ${current}. Перед установкой Meet сохранит копию настроек, базы голосов и служебных файлов в папку backups в папке данных. Записи не трогаются.`,
    "Старая версия может не понять настройки, сделанные в новой: часть из них вернётся к значениям по умолчанию. Вернуться можно, установив новую версию здесь же.",
  ];
  if (beforeAurora(version)) {
    lines.push("Версии до 0.4 устроены заметно иначе: другое оформление, другой ассистент и формат части настроек. Новые возможности пропадут, а часть настроек придётся задать заново.");
  }
  return lines;
}

/** «8 октября 2026 г.» из `2026-10-08`. */
export function releaseDate(date: string | null): string | null {
  if (!date) return null;
  const parsed = new Date(`${date}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? null
    : parsed.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

const errorText = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

type List =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "failed"; error: string }
  | { kind: "loaded"; rows: ReleaseRow[] };

type Install =
  | { kind: "none" }
  | { kind: "asking"; row: ReleaseRow }
  | { kind: "backing-up"; row: ReleaseRow }
  | { kind: "installing"; row: ReleaseRow; progress: UpdateProgress | null }
  | { kind: "confirm-work"; row: ReleaseRow }
  | { kind: "failed"; row: ReleaseRow; error: string }
  | { kind: "launched"; row: ReleaseRow; result: InstallResult };

export function VersionsRow({ endpoint, current }: { endpoint: Endpoint; current: string }) {
  const [list, setList] = useState<List>({ kind: "idle" });
  const [install, setInstall] = useState<Install>({ kind: "none" });

  useEffect(() => {
    let stop: (() => void) | undefined;
    let gone = false;
    void onUpdateProgress((progress) =>
      setInstall((s) => (s.kind === "installing" ? { ...s, progress } : s)),
    ).then((unlisten) => {
      if (gone) unlisten();
      else stop = unlisten;
    });
    return () => {
      gone = true;
      stop?.();
    };
  }, []);

  const load = async () => {
    setList({ kind: "loading" });
    try {
      setList({ kind: "loaded", rows: await listReleases() });
    } catch (cause) {
      setList({ kind: "failed", error: errorText(cause) });
    }
  };

  const run = async (row: ReleaseRow, confirmed: boolean) => {
    setInstall({ kind: "installing", row, progress: null });
    try {
      const result = await installUpdate(confirmed, row.version);
      setInstall({ kind: "launched", row, result });
    } catch (cause) {
      const error = errorText(cause);
      setInstall(error === CONFIRM_WORK ? { kind: "confirm-work", row }
        : error === UPDATE_CANCELLED ? { kind: "none" }
          : { kind: "failed", row, error });
    }
  };

  const start = async (row: ReleaseRow) => {
    if (row.relation !== "older") {
      await run(row, false);
      return;
    }
    setInstall({ kind: "backing-up", row });
    try {
      await backupBefore(endpoint, row.version);
    } catch (cause) {
      setInstall({ kind: "failed", row, error: `Копия настроек не сохранилась (${errorText(cause)}) — версия не установлена` });
      return;
    }
    await run(row, false);
  };

  const busy = install.kind === "backing-up" || install.kind === "installing" || install.kind === "launched";
  const asking = install.kind === "asking" ? install.row : null;

  return (
    <Row label="Другие версии" hint="Выпуски на GitHub — по кнопке; любую можно установить" stack={list.kind === "loaded"}>
      {list.kind !== "loaded" && (
        <>
          <Button onClick={() => void load()} busy={list.kind === "loading"}>Показать выпуски</Button>
          {list.kind === "failed" && <span className="update" role="status"><span className="update__error">{list.error}</span></span>}
        </>
      )}
      {list.kind === "loaded" && list.rows.length === 0 && <span className="muted">Выпусков пока нет</span>}
      {list.kind === "loaded" && list.rows.length > 0 && (
        <ul className="releases" aria-label="Выпуски">
          {list.rows.map((row) => {
            const mine = install.kind !== "none" && install.kind !== "asking" && install.row.version === row.version
              ? install : null;
            return (
              <li key={row.version} className="releases__item">
                <div className="releases__head">
                  <span className="releases__version">{row.version}</span>
                  {releaseDate(row.date) && <span className="muted">{releaseDate(row.date)}</span>}
                  {row.relation === "current" && <span className="releases__mark">установлена</span>}
                  <span className="releases__actions">
                    <Button variant="link" onClick={() => void openUrl(row.notes_url)}>Что нового</Button>
                    {row.installable && (
                      <Button disabled={busy} onClick={() =>
                        row.relation === "older" ? setInstall({ kind: "asking", row }) : void start(row)}>
                        Установить эту версию
                      </Button>
                    )}
                  </span>
                </div>
                {row.summary && <p className="releases__summary">{row.summary}</p>}
                {mine && (
                  <div className="update" role="status">
                    {mine.kind === "backing-up" && <span className="muted">Сохраняю копию настроек…</span>}
                    {mine.kind === "installing" && (
                      <>
                        <ProgressBar className="update__progress" value={mine.progress && mine.progress.total > 0
                          ? Math.min(1, mine.progress.done / mine.progress.total) : null}
                          ariaLabel={`Загрузка версии ${row.version}`} label={`Скачиваю ${row.version}…`} />
                        <Button onClick={() => void cancelUpdate()}>Отменить загрузку</Button>
                      </>
                    )}
                    {mine.kind === "confirm-work" && (
                      <>
                        <span>{CONFIRM_WORK}</span>
                        <Button onClick={() => setInstall({ kind: "none" })}>Отмена</Button>
                        <Button variant="primary" onClick={() => void run(row, true)}>Установить сейчас</Button>
                      </>
                    )}
                    {mine.kind === "failed" && <span className="update__error">{mine.error}</span>}
                    {mine.kind === "launched" && <span>Установка версии {row.version} запущена, приложение закрывается…</span>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {asking && (
        <ConfirmDialog
          title={`Установить версию ${asking.version}?`}
          message={<>{downgradeText(asking.version, current).map((line) => <p key={line}>{line}</p>)}</>}
          confirmLabel={`Сохранить копию и установить ${asking.version}`}
          danger={false}
          onConfirm={() => void start(asking)}
          onCancel={() => setInstall({ kind: "none" })}
        />
      )}
    </Row>
  );
}
