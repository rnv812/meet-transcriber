import { useEffect, useState } from "react";
import pkg from "../../../package.json";
import { type Endpoint, getDiagnostics } from "../../lib/api";
import {
  UPDATE_CANCELLED, cancelUpdate, checkUpdate, installUpdate, onUpdateProgress, openUrl, releasesPage,
  type InstallOutcome, type UpdateCheck, type UpdateProgress,
} from "../../lib/shell";
import { Button } from "../../ui/Button";
import { ProgressBar } from "../../ui/ProgressBar";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { PathText, Row } from "./Section";
import { VoiceBaseTip } from "./tips";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      window.setTimeout(() => setDone(false), 1500);
    } catch {
      /* буфер недоступен — адрес виден текстом */
    }
  };
  return <Button onClick={() => void copy()}>{done ? "Скопировано" : "Копировать"}</Button>;
}

const errorText = (cause: unknown) =>
  cause instanceof Error ? cause.message : String(cause);

/**
 * Ответ оболочки, когда идёт или ждёт расшифровка: не отказ, а вопрос
 * (тот же текст — `updater::WORK_IN_PROGRESS`). Прерванную задачу служба
 * записи новой версии поставит снова.
 */
export const UPDATE_CONFIRM_WORK =
  "Идёт расшифровка — она будет прервана и продолжится после обновления. Обновить сейчас?";

/** «12,5 МБ». */
export function megabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
}

function UpdateTip() {
  return (
    <HelpTip label="Как работает обновление" title="Обновление">
      <TipLine>
        Приложение проверяет обновления только по этой кнопке — само в сеть за ними не ходит.
      </TipLine>
      <TipLine>
        «Скачать и установить» скачивает установщик новой версии с GitHub, сверяет его контрольную
        сумму и запускает. Приложение закроется, установщик предложит «Обновить до» новой версии и
        в конце запустит её. Записи, голоса и настройки сохранятся.
      </TipLine>
      <TipLine>
        На macOS новая версия встаёт на место прежней, и Meet запускается заново сам. Если папка
        с Meet недоступна на запись, откроется образ диска — перетащите Meet в «Программы».
      </TipLine>
    </HelpTip>
  );
}

/** Что сказать, когда установка пошла (по ответу оболочки). */
export const LAUNCHED: Record<InstallOutcome, string> = {
  installer: "Установщик запущен, приложение закрывается…",
  "in-place": "Устанавливаю новую версию — Meet закроется и запустится заново…",
  manual:
    "Образ открыт в Finder: перетащите Meet в «Программы» с заменой и запустите заново. Приложение закрывается…",
};

type State =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "checked"; result: UpdateCheck }
  | { kind: "failed"; error: string }
  | { kind: "installing"; result: UpdateCheck; progress: UpdateProgress | null }
  | { kind: "cancelled"; result: UpdateCheck }
  | { kind: "launched"; outcome: InstallOutcome }
  | { kind: "confirm"; result: UpdateCheck }
  | { kind: "install-failed"; result: UpdateCheck; error: string };

function UpdateRow() {
  const [state, setState] = useState<State>({ kind: "idle" });

  useEffect(() => {
    let stop: (() => void) | undefined;
    let gone = false;
    void onUpdateProgress((progress) =>
      setState((s) => (s.kind === "installing" ? { ...s, progress } : s)),
    ).then((unlisten) => {
      if (gone) unlisten();
      else stop = unlisten;
    });
    return () => {
      gone = true;
      stop?.();
    };
  }, []);

  const check = async () => {
    setState({ kind: "checking" });
    try {
      setState({ kind: "checked", result: await checkUpdate() });
    } catch (cause) {
      setState({ kind: "failed", error: errorText(cause) });
    }
  };

  const install = async (result: UpdateCheck, confirmed = false) => {
    setState({ kind: "installing", result, progress: null });
    try {
      const outcome = await installUpdate(confirmed);
      setState({ kind: "launched", outcome: outcome ?? "installer" });
    } catch (cause) {
      const error = errorText(cause);
      setState(error === UPDATE_CONFIRM_WORK ? { kind: "confirm", result }
        : error === UPDATE_CANCELLED ? { kind: "cancelled", result }
          : { kind: "install-failed", result, error });
    }
  };

  const result = state.kind === "checked" || state.kind === "install-failed" || state.kind === "cancelled"
    ? state.result : null;
  const asking = state.kind === "confirm" ? state.result : null;
  const notes = result?.notes_url ?? null;

  return (
    <Row label="Обновления" hint="Проверка на GitHub — только по кнопке" help={<UpdateTip />}>
      <Button onClick={() => void check()} busy={state.kind === "checking"}
        disabled={state.kind === "installing" || state.kind === "launched"}>
        Проверить обновления
      </Button>
      {/* Итог проверки и ход загрузки — в строке постоянной высоты: страница ниже не прыгает. */}
      <div className="update" role="status">
        {state.kind === "cancelled" && <span className="muted">Загрузка отменена</span>}
        {state.kind === "failed" && <span className="update__error">{state.error}</span>}
        {result && result.latest === null && (
          <span className="muted">Обновления пока не опубликованы</span>
        )}
        {result && result.latest !== null && !result.newer && (
          <span>У вас последняя версия ({result.current})</span>
        )}
        {result && result.latest !== null && result.newer && (
          <>
            <span>
              Доступна версия {result.latest}
              {result.size ? <span className="muted"> · {megabytes(result.size)}</span> : null}
            </span>
            {notes && <Button onClick={() => void openUrl(notes)}>Что нового</Button>}
            {result.asset_name ? (
              <Button variant="primary" onClick={() => void install(result)}>
                Скачать и установить
              </Button>
            ) : (
              <span className="muted">
                В выпуске нет установщика — скачайте его со страницы выпусков
              </span>
            )}
          </>
        )}
        {state.kind === "install-failed" && <span className="update__error">{state.error}</span>}
        {asking && (
          <>
            <span>{UPDATE_CONFIRM_WORK}</span>
            <Button variant="primary" onClick={() => void install(asking, true)}>Обновить сейчас</Button>
            <Button onClick={() => setState({ kind: "checked", result: asking })}>Отмена</Button>
          </>
        )}
        {state.kind === "installing" && (
          <>
            <InstallProgress progress={state.progress} />
            <Button size="sm" onClick={() => void cancelUpdate()}>Отменить загрузку</Button>
          </>
        )}
        {state.kind === "launched" && <span>{LAUNCHED[state.outcome] ?? LAUNCHED.installer}</span>}
      </div>
    </Row>
  );
}

function InstallProgress({ progress }: { progress: UpdateProgress | null }) {
  const total = progress?.total ?? 0;
  const done = progress?.done ?? 0;
  // Размер неизвестен (оболочка прислала 0) — бегущий блик, а не пустая или полная полоска.
  const value = total > 0 ? Math.min(1, done / total) : null;
  return (
    <ProgressBar className="update__progress" value={value} ariaLabel="Загрузка обновления" extrapolate cap={0.99}
      label={progress === null
        ? "Начинаю загрузку…"
        : total > 0
          ? `Скачиваю: ${megabytes(done)} из ${megabytes(total)}`
          : `Скачиваю: ${megabytes(done)}`}
      detail={value !== null ? `${Math.floor(value * 100)} %` : null} />
  );
}

/** Авторы и их роли — те же, что в NOTICE. */
export const AUTHORS: { name: string; role: string }[] = [
  { name: "Андрей Алейников", role: "автор проекта" },
  { name: "Андрей Сивуха", role: "архитектура десктопного приложения" },
  { name: "Никита Резников", role: "десктопное приложение и интерфейс" },
];

export function About({ endpoint }: { endpoint: Endpoint }) {
  const [dataDir, setDataDir] = useState<string | null>(null);
  // Страница выпусков — от оболочки (репозиторий обновлений назван только там).
  const [releases, setReleases] = useState<string | null>(null);
  useEffect(() => {
    void releasesPage().then(setReleases);
  }, []);
  useEffect(() => {
    getDiagnostics(endpoint, 1)
      .then((d) => setDataDir((d.paths as Record<string, string> | undefined)?.data_dir ?? null))
      .catch(() => setDataDir(null));
  }, [endpoint]);
  return (
    <>
      <Row label="Версия"><span>{pkg.version}</span></Row>
      <UpdateRow />
      <Row
        label="Обновить вручную"
        hint="Скачайте новый установщик и запустите его — данные сохранятся"
      >
        {releases && (
          <Button variant="link" title={releases} onClick={() => void openUrl(releases)}>Скачать новую версию</Button>
        )}
      </Row>
      <Row label="Папка данных" hint="Настройки, журналы и база голосов" help={<VoiceBaseTip />}>
        {dataDir
          ? <span className="folder"><PathText path={dataDir} /><CopyButton text={dataDir} /></span>
          : <span className="muted">Неизвестно</span>}
      </Row>
      <Row label="Авторы" hint="Лицензия Apache-2.0" stack>
        <ul className="authors" aria-label="Авторы">
          {AUTHORS.map((a) => <li key={a.name}>{a.name} — {a.role}</li>)}
        </ul>
      </Row>
    </>
  );
}
